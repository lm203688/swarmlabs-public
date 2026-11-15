"""Anti-repeat memory — stop the flywheel from rediscovering the same thing.

Borrowed from ARIS (Auto-claude-code-research-in-sleep), which writes failed
ideas to memory so future ideation does not propose them again.

SwarmLabs version, adapted to what we actually run every day:

  * **Explored gaps** — research gaps, topics and claims the flywheel has
    already surfaced. Fingerprinted, not string-matched, so "lipid yield in
    Yarrowia" and "Yarrowia lipid yield" collapse to the same entry.
  * **Stalled validations** — scenarios that ran and made no progress. Tracked
    with a consecutive-stall counter, because a scenario that stalls three days
    running is telling you something is structurally wrong, not that it needs
    one more attempt.

The point is not to store history for its own sake. It is so tomorrow's run can
answer "have we already tried this?" in O(1) instead of re-running it.

Storage: `data/antirepeat_memory.json`. Pure standard library.

Usage:
    from engine.antirepeat import AntiRepeat

    ar = AntiRepeat()
    novel, dupes = ar.filter_novel(candidate_gaps)
    ar.record_gaps(novel)
    ar.update_stalls({"micro_monod": "converged", "micro_haldane": "stopped_no_progress"})
    print(ar.should_skip("micro_haldane"))   # True after enough consecutive stalls
    ar.save()
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import date, datetime

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
DATA_DIR = os.path.join(ROOT, "data")
MEMORY_PATH = os.path.join(DATA_DIR, "antirepeat_memory.json")

# A scenario that stalls this many days in a row is deprioritised. It is not
# deleted from the schedule — we simply stop spending the daily budget on it
# until something changes (new data, new engine, manual reset).
STALL_SKIP_THRESHOLD = 3

# Jaccard overlap above which two fingerprints count as the same topic.
NEAR_DUPLICATE_THRESHOLD = 0.72

# Tokens too common to carry identity for fingerprinting.
_STOPWORDS = {
    "the", "a", "an", "of", "in", "on", "for", "to", "and", "or", "with",
    "by", "at", "from", "as", "is", "are", "be", "this", "that", "using",
    "based", "via", "study", "research", "analysis", "novel", "new",
    "的", "了", "和", "与", "在", "对", "基于", "研究", "分析", "方法",
}

_TOKEN_RE = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]")


def fingerprint(text: str) -> str:
    """Order-insensitive, stopword-stripped hash of a topic string.

    Two phrasings of the same idea must collide; different ideas must not.
    Chinese text is handled via character bigrams, since it has no spaces.
    """
    if not text:
        return ""
    s = str(text).lower()
    tokens = [t for t in _TOKEN_RE.findall(s) if t not in _STOPWORDS]

    grams: set[str] = set()
    cjk_run: list[str] = []
    for t in tokens:
        if "\u4e00" <= t <= "\u9fff":
            cjk_run.append(t)
            continue
        if cjk_run:
            grams.update(_bigrams("".join(cjk_run)))
            cjk_run = []
        if len(t) > 1:
            grams.add(t)
    if cjk_run:
        grams.update(_bigrams("".join(cjk_run)))

    if not grams:
        return hashlib.sha1(s.encode("utf-8")).hexdigest()[:16]

    return hashlib.sha1("|".join(sorted(grams)).encode("utf-8")).hexdigest()[:16]


def _bigrams(s: str) -> list[str]:
    if len(s) == 1:
        return [s]
    return [s[i : i + 2] for i in range(len(s) - 1)]


def _grams_of(text: str) -> set[str]:
    """Token set used for near-duplicate comparison (not hashed)."""
    s = str(text).lower()
    tokens = [t for t in _TOKEN_RE.findall(s) if t not in _STOPWORDS]
    grams: set[str] = set()
    cjk_run: list[str] = []
    for t in tokens:
        if "\u4e00" <= t <= "\u9fff":
            cjk_run.append(t)
            continue
        if cjk_run:
            grams.update(_bigrams("".join(cjk_run)))
            cjk_run = []
        if len(t) > 1:
            grams.add(t)
    if cjk_run:
        grams.update(_bigrams("".join(cjk_run)))
    return grams


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


class AntiRepeat:
    """Persistent memory of what has already been explored or has stalled."""

    def __init__(self, path: str = MEMORY_PATH):
        self.path = path
        self.mem = self._load()

    # -- persistence ---------------------------------------------------------

    def _load(self) -> dict:
        if os.path.exists(self.path):
            try:
                with open(self.path, encoding="utf-8") as fh:
                    data = json.load(fh)
                if isinstance(data, dict) and "gaps" in data:
                    return data
            except (json.JSONDecodeError, OSError):
                pass  # corrupt file: start clean rather than crash the flywheel
        return {
            "version": 1,
            "created_at": _today(),
            "updated_at": _today(),
            "gaps": {},          # fingerprint -> entry
            "stalls": {},        # scenario   -> entry
        }

    def save(self) -> str:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.mem["updated_at"] = _today()
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(self.mem, fh, ensure_ascii=False, indent=2)
        return self.path

    # -- explored gaps -------------------------------------------------------

    def is_explored(self, text: str) -> bool:
        """Exact-fingerprint membership test, plus a near-duplicate scan."""
        fp = fingerprint(text)
        if fp in self.mem["gaps"]:
            return True
        return self._near_duplicate(text) is not None

    def _near_duplicate(self, text: str) -> str | None:
        grams = _grams_of(text)
        if not grams:
            return None
        for fp, entry in self.mem["gaps"].items():
            other = entry.get("grams") or []
            if not other:
                continue
            if _jaccard(grams, set(other)) >= NEAR_DUPLICATE_THRESHOLD:
                return fp
        return None

    def filter_novel(self, items, key=None):
        """Split candidates into (novel, duplicates).

        `key` extracts the topic string from each item; defaults to the item
        itself when it is a string.
        """
        key = key or (lambda x: x if isinstance(x, str) else str(x))
        novel, dupes = [], []
        for item in items:
            if self.is_explored(key(item)):
                dupes.append(item)
            else:
                novel.append(item)
        return novel, dupes

    def record_gaps(self, items, key=None, source: str = "") -> int:
        """Record topics as explored. Returns the number newly added."""
        key = key or (lambda x: x if isinstance(x, str) else str(x))
        added = 0
        for item in items:
            text = key(item)
            fp = fingerprint(text)
            if not fp:
                continue
            entry = self.mem["gaps"].get(fp)
            if entry is None:
                self.mem["gaps"][fp] = {
                    "text": _truncate(text),
                    "grams": sorted(_grams_of(text)),
                    "first_seen": _today(),
                    "last_seen": _today(),
                    "seen_count": 1,
                    "source": source or None,
                }
                added += 1
            else:
                entry["last_seen"] = _today()
                entry["seen_count"] = entry.get("seen_count", 0) + 1
        return added

    # -- stalled validations -------------------------------------------------

    def update_stalls(self, status_by_scenario: dict) -> dict:
        """Track per-scenario outcomes; maintain a consecutive-stall counter.

        Returns a summary of what changed, suitable for the flywheel report.
        """
        summary = {"converged": 0, "stalled": 0, "cleared": 0, "newly_stalling": []}
        for scenario, status in (status_by_scenario or {}).items():
            if not scenario:
                continue
            stalled = status in ("stopped_no_progress", "failed", "stalled")
            entry = self.mem["stalls"].get(scenario, {})
            prev_streak = entry.get("consecutive_stalls", 0)

            entry.update(
                {
                    "last_status": status,
                    "last_seen": _today(),
                    "runs": entry.get("runs", 0) + 1,
                    "consecutive_stalls": (prev_streak + 1) if stalled else 0,
                    "total_stalls": entry.get("total_stalls", 0) + (1 if stalled else 0),
                }
            )

            if stalled:
                summary["stalled"] += 1
                if entry["consecutive_stalls"] == STALL_SKIP_THRESHOLD:
                    summary["newly_stalling"].append(scenario)
            else:
                summary["converged"] += 1
                if prev_streak >= STALL_SKIP_THRESHOLD:
                    summary["cleared"] += 1  # recovered after being deprioritised

            self.mem["stalls"][scenario] = entry
        return summary

    def should_skip(self, scenario: str) -> bool:
        """True when a scenario has stalled enough days running to deprioritise."""
        entry = self.mem["stalls"].get(scenario)
        if not entry:
            return False
        # Do not skip forever: if we have not seen it recently the streak is stale.
        if entry.get("last_seen") != _today():
            pass  # streak still counts until it succeeds or is reset
        return entry.get("consecutive_stalls", 0) >= STALL_SKIP_THRESHOLD

    def reset_stall(self, scenario: str) -> None:
        """Manually clear a scenario's stall streak (after an engine fix, etc.)."""
        if scenario in self.mem["stalls"]:
            self.mem["stalls"][scenario]["consecutive_stalls"] = 0
            self.mem["stalls"][scenario]["manually_reset_at"] = _today()

    def priority_scenarios(self, scenarios: list) -> list:
        """Order scenarios: untested first, then converged, long-stalled last."""
        def rank(s):
            e = self.mem["stalls"].get(s)
            if not e:
                return (0, 0)
            return (1, -e.get("consecutive_stalls", 0))
        return sorted(scenarios, key=rank)

    # -- reporting -----------------------------------------------------------

    def stats(self) -> dict:
        gaps = self.mem["gaps"]
        stalls = self.mem["stalls"]
        repeat_gaps = sum(1 for g in gaps.values() if g.get("seen_count", 1) > 1)
        deprioritised = [s for s in stalls if self.should_skip(s)]
        return {
            "explored_gaps": len(gaps),
            "repeat_gaps": repeat_gaps,
            "tracked_scenarios": len(stalls),
            "deprioritised": len(deprioritised),
            "deprioritised_list": sorted(deprioritised)[:10],
            "total_stall_events": sum(s.get("total_stalls", 0) for s in stalls.values()),
            "memory_file": os.path.relpath(self.path, ROOT).replace("\\", "/"),
        }


def _today() -> str:
    return date.today().isoformat()


def _truncate(text: str, limit: int = 180) -> str:
    s = str(text).strip().replace("\n", " ")
    return s if len(s) <= limit else s[: limit - 1] + "…"


def ingest_from_daily_log(log: dict, path: str = MEMORY_PATH) -> dict:
    """Flywheel entry point: fold one day's run into memory and report.

    Pulls stalled scenarios out of the autonomous-loop subsystem and records
    the day's validated territory as explored gaps.
    """
    ar = AntiRepeat(path)

    # 1) scenario outcomes -> stall tracking
    status_map: dict[str, str] = {}
    al = (log.get("subsystems") or {}).get("autonomous_loop") or {}
    for r in (log.get("_al_results") or []):
        if isinstance(r, dict) and r.get("scenario"):
            status_map[r["scenario"]] = r.get("status", "unknown")
    if al and not status_map:
        # Fall back to the aggregate when per-scenario rows are unavailable.
        if al.get("n_scenarios"):
            status_map["_aggregate"] = (
                "converged" if al.get("converged") else "stopped_no_progress"
            )

    stall_summary = ar.update_stalls(status_map)

    # 2) today's territory -> explored gaps
    topics: list[str] = []
    for sc in (log.get("totals") or {}).get("domains", []) or []:
        if isinstance(sc, str):
            topics.append(sc)
    pv = (log.get("subsystems") or {}).get("paper_validation") or {}
    topics.extend(s for s in pv.get("scenarios", []) if isinstance(s, str))

    novel, dupes = ar.filter_novel(topics)
    added = ar.record_gaps(novel, source="flywheel")

    ar.save()

    result = {
        "new_gaps_recorded": added,
        "duplicate_gaps_skipped": len(dupes),
        "stall_summary": stall_summary,
        "stats": ar.stats(),
    }
    return result


if __name__ == "__main__":
    ar = AntiRepeat()
    print(json.dumps(ar.stats(), indent=2, ensure_ascii=False))
    if ar.mem["stalls"]:
        print("\nTracked scenarios:")
        for name, e in sorted(ar.mem["stalls"].items()):
            flag = " [DEPRIORITISED]" if ar.should_skip(name) else ""
            print(
                f"  {name:<32} runs={e.get('runs')} "
                f"streak={e.get('consecutive_stalls')} "
                f"total_stalls={e.get('total_stalls')} "
                f"last={e.get('last_status')}{flag}"
            )
