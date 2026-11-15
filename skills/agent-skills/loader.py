#!/usr/bin/env python3
"""Progressive-disclosure loader for SwarmLabs agent skills.

The load-bearing claim
---------------------
An agent can keep the entire SwarmLabs skill set resident in a small, *measured*
fraction of a context window, and only pull a full SKILL.md into context when a
short description actually matches the task at hand. This module makes that
claim checkable instead of assertive: :func:`budget_check` returns the measured
ratio, and :func:`rank` fails closed when nothing matches.

Three tiers
-----------
Following the open Agent Skills standard used by the larger public skill
collections (e.g. K-Dense-AI/scientific-agent-skills):

    tier 0  resident index    name + description        always in context
    tier 1  SKILL.md body     procedural steps, rules   pulled by :func:`load`
    tier 2  references/       deep background, tables   pulled by :func:`references`

Rules
-----
- stdlib only; no network, no credentials, no subprocess. Runs offline.
- Fail closed: :func:`rank` returns nothing below MATCH_FLOOR. Loading a wrong
  skill into context is worse than loading none — a misapplied procedure is a
  confident error, and this project's whole selling point is refusing.
- Deterministic: no randomness, no wall clock, no path-ordering dependencies.
  Same tree + same query => same decision, every run.
- Auditable: every match reports the terms that earned it, so an agent can show
  *why* it loaded a skill rather than just that it did.
- Descriptions are served as written, not paraphrased. Truncation is honest
  about what is resident; a summary is not, and it would also cost recall.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent

# ----------------------------------------------------------------- tunables
#: Terms too common to carry any signal in a keyword match.
STOPWORDS = frozenset(
    """
    a an the and or but if then else of in on at to for from by with without
    is are was were be been being do does did this that these those it its as
    than into over under out up down off per via not no yes can could will
    would should may might must has have had using use used uses returns return
    use uses when while about above below between each other such only also
    more most less than very just like so here there what which who whom how
    any all some every both our your my his her their own
    """.split()
)

#: Agent Skills spec cap on `description`; also the resident budget per skill.
MAX_DESCRIPTION_CHARS = 1024

#: Reference context window and the fraction of it we are willing to occupy.
DEFAULT_WINDOW_TOKENS = 200_000
DEFAULT_BUDGET_RATIO = 0.07

#: Cosine-similarity floor. Below it we refuse to recommend a skill.
MATCH_FLOOR = 0.18

#: Rough chars per token for mixed English prose; used only for reporting.
CHARS_PER_TOKEN = 4.0

#: Bonus weight on an exact hit of the skill's own id/name tokens.
_NAME_WEIGHT = 2.5

TIER_NAMES = ("tier0_resident", "tier1_skill_md", "tier2_references")


# ----------------------------------------------------------- frontmatter
def _split_frontmatter(text: str) -> tuple[list[str] | None, str]:
    """Return (frontmatter lines, body); (None, text) when absent/unclosed."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return None, text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return lines[1:i], "\n".join(lines[i + 1 :])
    return None, text


def _parse_frontmatter(text: str) -> dict[str, str]:
    """Minimal YAML-frontmatter reader: top-level scalar fields only.

    Handles the two block-scalar forms the skills actually use (`>-` and `|`),
    which is what makes `mirror-run`'s folded description parseable. Anything
    else degrades to an empty string rather than raising.
    """
    fm, _body = _split_frontmatter(text)
    if fm is None:
        return {}

    out: dict[str, str] = {}
    i = 0
    while i < len(fm):
        line = fm[i]
        if not line.strip() or line.lstrip().startswith("#") or line.startswith((" ", "\t")):
            i += 1
            continue
        m = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if not m:
            i += 1
            continue
        key, val = m.group(1), m.group(2).strip()
        i += 1
        if val in (">-", ">", "|", "|-"):
            # Collect the indented continuation block.
            chunk: list[str] = []
            while i < len(fm) and (not fm[i].strip() or fm[i].startswith((" ", "\t"))):
                chunk.append(fm[i].strip())
                i += 1
            out[key] = " ".join(c for c in chunk if c).strip()
            continue
        out[key] = val
    return out


def _read_skill_dir(skill_dir: Path) -> dict:
    """Read one skill directory into a tier-0 record."""
    skill_md = skill_dir / "SKILL.md"
    text = skill_md.read_text(encoding="utf-8")
    fm = _parse_frontmatter(text)
    desc = fm.get("description", "").strip()

    def _rel_list(subdir: str) -> list[str]:
        d = skill_dir / subdir
        if not d.is_dir():
            return []
        # Paths are relative to the collection root, which is also `index["root"]`.
        return sorted(str(p.relative_to(skill_dir.parent)) for p in d.rglob("*") if p.is_file())

    return {
        "id": skill_dir.name,
        "name": fm.get("name", skill_dir.name).strip(),
        "description": desc,
        "description_chars": len(desc),
        "line_count": len(text.split("\n")),
        "skill_md": str(skill_md.relative_to(skill_dir.parent)),
        "references": _rel_list("references"),
        "scripts": _rel_list("scripts"),
        "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }


# ------------------------------------------------------------------- index
def build_index(root: Path | None = None) -> dict:
    """Scan a skill collection and return the tier-0 resident index.

    Only directories containing SKILL.md are treated as skills, so collection
    metadata (README, MANIFEST, tests/) is never mistaken for a skill.
    """
    root = Path(root) if root else HERE
    records = sorted(
        (_read_skill_dir(d) for d in root.iterdir() if d.is_dir() and (d / "SKILL.md").exists()),
        key=lambda r: r["id"],
    )
    return {
        "root": str(root),
        "skill_count": len(records),
        "match_floor": MATCH_FLOOR,
        "skills": records,
    }


def resident_bytes(index: dict) -> int:
    """Byte cost of keeping the whole tier-0 set in context."""
    total = 0
    for s in index["skills"]:
        # id + name + description, plus the minimal JSON scaffolding an
        # agent framework would add per entry.
        total += len(s["id"]) + len(s["name"]) + s["description_chars"] + 24
    return total


def resident_tokens(index: dict) -> int:
    """Rounded token cost of the tier-0 set."""
    return math.ceil(resident_bytes(index) / CHARS_PER_TOKEN)


def budget_check(
    index: dict,
    window_tokens: int = DEFAULT_WINDOW_TOKENS,
    budget_ratio: float = DEFAULT_BUDGET_RATIO,
) -> dict:
    """Is the resident set small enough to keep permanently?

    Returns the measured ratio and a hard PASS/FAIL against the budget. This is
    the number behind the progressive-disclosure claim, so it is computed, not
    quoted from memory.
    """
    tokens = resident_tokens(index)
    limit = int(window_tokens * budget_ratio)
    ratio = tokens / window_tokens if window_tokens else math.inf
    return {
        "skill_count": index["skill_count"],
        "resident_tokens": tokens,
        "window_tokens": window_tokens,
        "budget_ratio": budget_ratio,
        "budget_tokens": limit,
        "measured_ratio": round(ratio, 5),
        "pass": tokens <= limit,
    }


# --------------------------------------------------------------- ranking
_WORD_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return [w for w in _WORD_RE.findall(text.lower()) if w not in STOPWORDS and len(w) > 2]


def _idf(index: dict) -> dict[str, float]:
    """Inverse document frequency over skill descriptions."""
    n = max(1, index["skill_count"])
    df: dict[str, int] = {}
    for s in index["skills"]:
        for w in set(_tokens(f"{s['id']} {s['name']} {s['description']}")):
            df[w] = df.get(w, 0) + 1
    return {w: math.log(1.0 + n / c) for w, c in df.items()}


def _vector(index: dict, text: str, weights: dict[str, float]) -> dict[str, float]:
    return {w: weights.get(w, 1.0) for w in set(_tokens(text))}


def _cosine(a: dict[str, float], b: dict[str, float]) -> tuple[float, list[str]]:
    if not a or not b:
        return 0.0, []
    shared = set(a) & set(b)
    dot = sum(a[w] * b[w] for w in shared)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    if na == 0.0 or nb == 0.0:
        return 0.0, []
    return dot / (na * nb), sorted(shared)


def rank(
    index: dict,
    query: str,
    top_k: int = 3,
    floor: float | None = None,
) -> list[dict]:
    """Rank skills against a query. Returns [] when nothing clears the floor.

    Scoring is IDF-weighted cosine similarity over description tokens, with an
    extra weight on tokens that are part of the skill's own name. Each result
    carries the terms that earned the score, so a match is auditable.
    """
    floor = MATCH_FLOOR if floor is None else floor
    qv = _vector(index, query, _idf(index))
    if not qv:
        return []

    scored: list[dict] = []
    for s in index["skills"]:
        name_w = {tok: _NAME_WEIGHT for tok in _tokens(s["id"]) if tok in qv}
        sv = _vector(index, f"{s['name']} {s['description']}", name_w)
        sim, shared = _cosine(qv, sv)
        if sim < floor:
            continue
        scored.append(
            {
                "id": s["id"],
                "score": round(sim, 4),
                "matched_terms": shared,
                "n_matched_terms": len(shared),
            }
        )

    scored.sort(key=lambda r: (-r["score"], r["id"]))
    return scored[:top_k]


# --------------------------------------------------------------- loading
def load(index: dict, skill_id: str) -> dict:
    """Tier 1: return the full SKILL.md body for one skill.

    Refuses unknown ids rather than silently falling back to another skill.
    """
    for s in index["skills"]:
        if s["id"] == skill_id:
            return {
                "id": s["id"],
                "tier": "tier1_skill_md",
                "path": s["skill_md"],
                "body": Path(index["root"], s["skill_md"]).read_text(encoding="utf-8"),
                "references": s["references"],
            }
    return {"id": skill_id, "error": f"unknown skill id '{skill_id}'"}


def references(index: dict, skill_id: str) -> list[str]:
    """Tier 2: list the deep-reference files a skill can pull on demand."""
    for s in index["skills"]:
        if s["id"] == skill_id:
            return s["references"]
    return []


def decide(index: dict, query: str, top_k: int = 3) -> dict:
    """One-shot serving decision: rank, then load at most the best skill.

    The output states explicitly whether anything was loaded and why, which is
    what an auditable agent needs to show a human.
    """
    hits = rank(index, query, top_k=top_k)
    if not hits:
        return {
            "query": query,
            "loaded": None,
            "reason": f"no skill scored at or above {MATCH_FLOOR}; refusing to guess",
            "candidates": [],
        }
    best = hits[0]
    tier1 = load(index, best["id"])
    return {
        "query": query,
        "loaded": best["id"],
        "reason": f"score {best['score']} >= {MATCH_FLOOR} on terms {best['matched_terms']}",
        "candidates": hits,
        "tier1_bytes": len(tier1.get("body", "")),
    }


# --------------------------------------------------------------- hygiene
def find_duplicates(root: Path | None = None) -> list[dict]:
    """Find identical SKILL.md copies served from more than one path.

    Two identical skills under different paths is a serving hazard: an agent
    matches on the name and may load the stale copy. Reported, never auto-fixed.

    Copies are split by whether they sit in the *same* collection root. A
    same-collection collision is the real hazard — one loader can serve both, and
    which copy wins depends on filesystem order. A cross-collection mirror is a
    normal packaging pattern (this repo ships `skills/agent-skills/` and also
    publishes a subset as `skills/swarmlabs-plugins/`); it is still reported so
    the mirroring is visible, but it is not what the gate should fail on.
    """
    root = Path(root) if root else REPO_ROOT
    groups: dict[str, list[str]] = {}
    for p in sorted(root.rglob("SKILL.md")):
        if ".workbuddy" in p.parts:
            continue
        groups.setdefault(hashlib.sha256(p.read_bytes()).hexdigest(), []).append(p.relative_to(root).as_posix())
    out = []
    for digest, paths in sorted(groups.items()):
        if len(paths) < 2:
            continue
        # Collection root = first two path components (skills/<collection>).
        roots = {tuple(p.split("/")[:2]) for p in paths}
        out.append({
            "sha256": digest,
            "copies": paths,
            "same_collection": len(roots) == 1,
        })
    return out


# ------------------------------------------------------------------- cli
def main() -> int:
    index = build_index()
    budget = budget_check(index)
    payload = {
        "skill_count": index["skill_count"],
        "skills": [
            {
                "id": s["id"],
                "description_chars": s["description_chars"],
                "refs": len(s["references"]),
            }
            for s in index["skills"]
        ],
        "budget": budget,
        "duplicates": find_duplicates(),
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if budget["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
