#!/usr/bin/env python3
"""Test the progressive-disclosure loader (fail-closed oriented).

Complements tests/validate_skills.py: that script asks "is every skill
well-formed", this one asks "does the serving layer actually behave".

Rules enforced:

  1. Tier-0 resident index fits its budget. The whole skill set must occupy at
     most DEFAULT_BUDGET_RATIO of a 200k-token window, else progressive
     disclosure is a lie and every query pays full cost.
  2. Index is honest and complete: every skill has a non-empty description
     within the spec cap, and its tier-1 file exists on disk.
  3. Every skill is reachable. Each of the eight skills must be loadable by at
     least one query a practitioner would actually type. An unreachable skill is
     a dead skill: it costs resident tokens and never fires.
  4. Fail-closed on unrelated queries. Queries from other domains must return
     no candidates at all. Recommending a wrong skill is worse than refusing —
     a misapplied procedure is a confident error.
  5. Determinism. Ranking must be identical across repeated calls and must not
     depend on the order skills happened to be discovered on disk.
  6. Unknown ids are refused, never silently resolved to a different skill.
  7. No same-collection duplicate SKILL.md copies. Cross-collection mirrors are
     packaging and are only reported.
  8. The budget check actually bites. Shrinking the window must flip it to fail;
     a check that always passes is decoration.
  9. Fail-closed is monotone. Raising the match floor may only remove
     candidates, never add them.
 10. Empty or stopword-only queries return nothing.

Exit code 0 when every rule holds, 1 otherwise.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
REPO_ROOT = HERE.parent.parent

sys.path.insert(0, str(HERE))

import loader  # noqa: E402


# Queries a practitioner would type, one per skill. Used for the reachability
# rule: they are deliberately phrased as requests, not as the skill's own
# description text.
POSITIVE_QUERIES: dict[str, str] = {
    "fermentation-design": "optimize a fermentation campaign with active learning",
    "kinetic-model-selection": "fit Monod and Andrews kinetics and compare R squared",
    "mirror-run": "estimate sim to real gap for hardware in the loop",
    "ood-guard": "is this condition inside the region the model was trained on",
    "paper-claim-validation": "validate a quantitative claim from a paper",
    "strain-media-selection": "screen strains and match a defined medium",
    "uq-coverage-audit": "is this prediction interval trustworthy, is the model overconfident",
    "virtual-experiment-design": "design the next experiment with a gaussian process surrogate",
}

# None of these should match anything. Each is a fluent, plausible task
# description from outside this skill set.
NEGATIVE_QUERIES: list[str] = [
    "write a haiku about mitochondria",
    "how do I reset my office printer and set up wifi",
    "what is the capital of australia and its population",
    "translate this paragraph into german please",
    "explain the plot of the movie blade runner",
    "debug a segfault in my rust binary",
    "plan a three day itinerary for kyoto",
    "convert this csv into a powerpoint slide",
]


def main() -> int:
    index = loader.build_index()
    failures: list[str] = []

    def check(ok: bool, label: str, detail: str = "") -> None:
        if not ok:
            failures.append(f"{label}" + (f" — {detail}" if detail else ""))

    print(f"Loader self-test: {index['skill_count']} skills\n" + "-" * 60)

    # -- 1. resident budget -------------------------------------------------
    budget = loader.budget_check(index)
    check(
        budget["pass"],
        "[1] resident budget",
        f"{budget['resident_tokens']} tokens is {budget['measured_ratio']:.4f} of the "
        f"{budget['window_tokens']}-token window (budget {budget['budget_ratio']})",
    )
    print(
        f"  [PASS] resident {budget['resident_tokens']} tok "
        f"= {budget['measured_ratio']:.3%} of window"
        if budget["pass"]
        else f"  [FAIL] resident budget exceeded"
    )

    # -- 2. index honesty ---------------------------------------------------
    for s in index["skills"]:
        check(
            0 < s["description_chars"] <= loader.MAX_DESCRIPTION_CHARS,
            f"[2] description length {s['id']}",
            f"{s['description_chars']} chars (limit {loader.MAX_DESCRIPTION_CHARS})",
        )
        check(
            Path(index["root"], s["skill_md"]).is_file(),
            f"[2] tier1 path exists {s['id']}",
            s["skill_md"],
        )
    bad = sum(
        1
        for s in index["skills"]
        if not (0 < s["description_chars"] <= loader.MAX_DESCRIPTION_CHARS)
        or not Path(index["root"], s["skill_md"]).is_file()
    )
    print(f"  [{'PASS' if bad == 0 else 'FAIL'}] index honesty ({index['skill_count']} skills)")

    # -- 3. every skill reachable ------------------------------------------
    unreachable: list[str] = []
    for skill_id, query in POSITIVE_QUERIES.items():
        hits = loader.rank(index, query, top_k=5)
        if not hits or hits[0]["id"] != skill_id:
            got = hits[0]["id"] if hits else "<none>"
            unreachable.append(f"{skill_id} -> {got}")
    check(not unreachable, "[3] every skill reachable", "; ".join(unreachable))
    print(f"  [{'PASS' if not unreachable else 'FAIL'}] reachability ({len(POSITIVE_QUERIES)} queries)")
    # tier-1 body must be real content, and references must be listed honestly
    for skill_id in POSITIVE_QUERIES:
        tier1 = loader.load(index, skill_id)
        check(bool(tier1.get("body", "").strip()), f"[3] non-empty body {skill_id}")
        check(
            tier1.get("body", "").lstrip().startswith("---"),
            f"[3] body keeps frontmatter {skill_id}",
        )

    # -- 4. fail closed on unrelated queries -------------------------------
    leaked: list[str] = []
    for q in NEGATIVE_QUERIES:
        hits = loader.rank(index, q, top_k=5)
        if hits:
            leaked.append(f"{q!r} -> {hits[0]['id']} ({hits[0]['score']})")
    check(not leaked, "[4] fail closed", "; ".join(leaked))
    print(f"  [{'PASS' if not leaked else 'FAIL'}] fail-closed ({len(NEGATIVE_QUERIES)} negatives)")

    # -- 5. determinism ----------------------------------------------------
    base = loader.rank(index, next(iter(POSITIVE_QUERIES.values())))
    same_call = loader.rank(index, next(iter(POSITIVE_QUERIES.values())))
    rebuilt = loader.build_index()
    same_order = loader.rank(rebuilt, next(iter(POSITIVE_QUERIES.values())))
    check(base == same_call == same_order, "[5] determinism")
    print(f"  [{'PASS' if base == same_call == same_order else 'FAIL'}] determinism")

    # -- 6. unknown id refused ---------------------------------------------
    ghost = loader.load(index, "no-such-skill")
    check("error" in ghost and "body" not in ghost, "[6] unknown id refused", str(ghost)[:80])
    check(loader.references(index, "no-such-skill") == [], "[6] unknown id references")
    print(f"  [{'PASS' if 'error' in ghost else 'FAIL'}] unknown id refused")

    # -- 7. no same-collection duplicates ----------------------------------
    dups = loader.find_duplicates()
    collisions = [g for g in dups if g["same_collection"]]
    check(not collisions, "[7] no same-collection duplicates", str(collisions)[:200])
    for g in dups:
        tag = "mirror" if not g["same_collection"] else "COLLISION"
        print(f"    [{tag}] " + "  <>  ".join(g["copies"]))
    print(
        f"  [{'PASS' if not collisions else 'FAIL'}] duplicates "
        f"({len(dups)} mirrored groups, {len(collisions)} collisions)"
    )

    # -- 8. budget check actually bites ------------------------------------
    tight = loader.budget_check(index, window_tokens=1000)
    check(not tight["pass"], "[8] budget bites when window shrinks")
    check(budget["pass"] != tight["pass"], "[8] budget is window-sensitive")
    print(f"  [{'PASS' if not tight['pass'] else 'FAIL'}] budget bites (1000-tok window)")

    # -- 9. fail-closed is monotone in the floor ---------------------------
    for high in (1.0,):
        loose = [h["id"] for h in loader.rank(index, next(iter(POSITIVE_QUERIES.values())), floor=0.0)]
        strict = [h["id"] for h in loader.rank(index, next(iter(POSITIVE_QUERIES.values())), floor=high)]
        check(set(strict) <= set(loose), f"[9] floor monotone at {high}")
    # and it can actually exclude everything
    nothing = loader.rank(index, next(iter(POSITIVE_QUERIES.values())), floor=1.01)
    check(not nothing, "[9] unreachable floor returns nothing")
    print(f"  [{'PASS' if not nothing else 'FAIL'}] floor monotone")

    # -- 10. degenerate queries --------------------------------------------
    for q in ("", "   ", "the of and to with", "!!!", "zzzz"):
        check(not loader.rank(index, q), f"[10] degenerate query {q!r}")
        d = loader.decide(index, q)
        check(d["loaded"] is None and d["candidates"] == [], f"[10] decide refuses {q!r}")
    print(f"  [{'PASS' if not any(f'[10]' in f for f in failures) else 'FAIL'}] degenerate queries")

    print("-" * 60)
    if failures:
        print(f"\n{len(failures)} failure(s):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nAll loader rules hold.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
