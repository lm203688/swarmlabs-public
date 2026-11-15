#!/usr/bin/env python3
"""Tests for the virtual-experiment-design skill.

Standard library only. Run from the repo root:

    python tests/agent-skills/virtual-experiment-design/test_design_next_experiment.py
    python -m unittest discover -s tests/agent-skills -p "test_*.py"

These cover what is specific to this skill. Repo-wide structural checks
(frontmatter conformance, line limits, no eval/exec) belong in a future
tests/_meta suite mirroring the Agent Skills reference repo layout.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

# tests/agent-skills/<skill>/test_x.py -> parents[3] is the repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
SKILL_DIR = REPO_ROOT / "skills" / "agent-skills" / "virtual-experiment-design"
SCRIPTS_DIR = SKILL_DIR / "scripts"

sys.path.insert(0, str(SCRIPTS_DIR))
import design_next_experiment as dnx  # noqa: E402

SPEC_TOP_LEVEL_FIELDS = {
    "name", "description", "license", "compatibility", "allowed-tools", "metadata",
}


class GridTests(unittest.TestCase):
    def test_1d_grid_hits_both_bounds(self):
        pts = dnx.grid([[0.0, 10.0]], 5)
        self.assertEqual(len(pts), 5)
        self.assertEqual(pts[0], [0.0])
        self.assertEqual(pts[-1], [10.0])
        self.assertTrue(all(len(p) == 1 for p in pts))

    def test_1d_grid_monotonic(self):
        pts = dnx.grid([[0.0, 1.0]], 11)
        xs = [p[0] for p in pts]
        self.assertEqual(xs, sorted(xs))

    def test_2d_grid_stays_inside_bounds(self):
        bounds = [[0.0, 5.0], [-1.0, 1.0]]
        pts = dnx.grid(bounds, 49)
        self.assertTrue(all(len(p) == 2 for p in pts))
        for x, y in pts:
            self.assertGreaterEqual(x, 0.0)
            self.assertLessEqual(x, 5.0)
            self.assertGreaterEqual(y, -1.0)
            self.assertLessEqual(y, 1.0)

    def test_3d_grid_capped(self):
        pts = dnx.grid([[0.0, 1.0]] * 3, 50)
        self.assertLessEqual(len(pts), 50)
        self.assertTrue(all(len(p) == 3 for p in pts))


class RankTests(unittest.TestCase):
    """Ranking behaviour, including the guard-availability contract."""

    def _pred(self, means, stds, dists):
        return {"mean": means, "std": stds, "dist": dists}

    def test_exploit_ranks_by_mean_desc(self):
        cands = [[1.0], [2.0], [3.0]]
        pred = self._pred([1.0, 3.0, 2.0], [0.1, 0.1, 0.1], [0.1, 0.1, 0.1])
        guard = {"status": ["pass", "pass", "pass"]}
        rows = dnx.rank(cands, pred, guard, "exploit")
        self.assertEqual([r["x"] for r in rows], [[2.0], [3.0], [1.0]])

    def test_explore_ranks_by_std_desc(self):
        cands = [[1.0], [2.0], [3.0]]
        pred = self._pred([1.0, 1.0, 1.0], [0.1, 0.9, 0.5], [0.1, 0.1, 0.1])
        guard = {"status": ["pass", "pass", "pass"]}
        rows = dnx.rank(cands, pred, guard, "explore")
        self.assertEqual([r["x"] for r in rows], [[2.0], [3.0], [1.0]])

    def test_balanced_mixes_mean_and_std(self):
        cands = [[1.0], [2.0], [3.0], [4.0]]
        pred = self._pred([9.0, 1.0, 1.0, 1.0], [0.1, 0.1, 0.1, 9.0],
                          [0.1, 0.1, 0.1, 0.1])
        guard = {"status": ["pass"] * 4}
        rows = dnx.rank(cands, pred, guard, "balanced")
        tops = [tuple(r["x"]) for r in rows[:2]]
        self.assertIn((1.0,), tops)   # highest mean
        self.assertIn((4.0,), tops)   # highest uncertainty

    def test_credible_interval_uses_1_96_sigma(self):
        cands = [[1.0]]
        pred = self._pred([2.0], [0.5], [0.1])
        rows = dnx.rank(cands, pred, {"status": ["pass"]}, "exploit")
        self.assertAlmostEqual(rows[0]["lo"], 2.0 - 1.96 * 0.5)
        self.assertAlmostEqual(rows[0]["hi"], 2.0 + 1.96 * 0.5)

    def test_rejected_candidates_are_dropped(self):
        cands = [[1.0], [2.0]]
        pred = self._pred([1.0, 99.0], [0.1, 0.1], [0.1, 5.0])
        guard = {"status": ["pass", "reject"]}
        rows = dnx.rank(cands, pred, guard, "exploit")
        self.assertEqual([r["x"] for r in rows], [[1.0]])

    def test_controlled_is_kept(self):
        cands = [[1.0], [2.0]]
        pred = self._pred([1.0, 99.0], [0.1, 0.1], [0.1, 5.0])
        guard = {"status": ["pass", "controlled"]}
        rows = dnx.rank(cands, pred, guard, "exploit")
        self.assertEqual([r["x"] for r in rows], [[2.0], [1.0]])

    # ---- regression: the two bugs found while validating against the live API ----

    def test_verdict_reads_status_field(self):
        """The API returns verdicts under 'status', not 'verdict(s)'."""
        cands = [[1.0]]
        pred = self._pred([1.0], [0.1], [0.1])
        rows = dnx.rank(cands, pred, {"status": ["pass"]}, "exploit")
        self.assertEqual(rows[0]["verdict"], "pass")

    def test_guard_down_is_not_a_rejection(self):
        """Empty guard means 'unknown', NOT 'reject'.

        A missing guard must not make a usable model look out-of-distribution.
        """
        cands = [[1.0], [5.0], [9.0]]
        pred = self._pred([1.0, 2.0, 3.0], [0.1, 0.1, 0.1], [0.1, 4.0, 8.0])
        rows = dnx.rank(cands, pred, {}, "exploit")
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["x"], [9.0])  # highest mean still wins
        self.assertTrue(all(r["verdict"] == "unknown" for r in rows))

    def test_all_rejected_falls_back_to_nearest(self):
        """When everything is OOD, surface the nearest point, never a guessed value."""
        cands = [[1.0], [5.0], [9.0]]
        pred = self._pred([1.0, 2.0, 99.0], [0.1, 0.1, 0.1], [0.1, 4.0, 8.0])
        guard = {"status": ["reject", "reject", "reject"]}
        rows = dnx.rank(cands, pred, guard, "exploit")
        self.assertEqual(rows[0]["x"], [1.0])  # smallest dist, not highest mean


class FrontmatterContractTests(unittest.TestCase):
    """SKILL.md must satisfy the Agent Skills specification."""

    @classmethod
    def setUpClass(cls):
        cls.skill_md = SKILL_DIR / "SKILL.md"
        if not cls.skill_md.is_file():
            raise unittest.SkipTest("SKILL.md not found at %s" % cls.skill_md)
        cls.lines = cls.skill_md.read_text(encoding="utf-8").splitlines()
        cls.fm = cls._frontmatter(cls.lines)

    @staticmethod
    def _frontmatter(lines):
        if not lines or lines[0].strip() != "---":
            return None
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                return lines[1:i]
        return None

    def test_frontmatter_delimited(self):
        self.assertIsNotNone(self.fm, "SKILL.md must open with a --- delimited block")

    def test_name_matches_directory(self):
        self.assertEqual(self._field("name"), SKILL_DIR.name)

    def test_name_charset(self):
        name = self._field("name")
        self.assertIsNotNone(name)
        self.assertRegex(name, r"^[a-z0-9-]+$")
        self.assertFalse(name.startswith("-") or name.endswith("-"))
        self.assertNotIn("--", name)
        self.assertLessEqual(len(name), 64)

    def test_description_present_and_bounded(self):
        desc = self._field("description")
        self.assertIsNotNone(desc)
        self.assertTrue(desc.strip())
        self.assertLessEqual(len(desc), 1024)

    def test_only_spec_top_level_fields(self):
        self.assertIsNotNone(self.fm)
        keys = {
            m.group(1)
            for line in self.fm
            if (m := re.match(r"^([a-zA-Z][a-zA-Z-]*):", line))
        }
        self.assertTrue(keys, "no top-level fields parsed")
        self.assertLessEqual(keys, SPEC_TOP_LEVEL_FIELDS)

    def test_metadata_is_block_mapping_not_json(self):
        self.assertIsNotNone(self.fm)
        for line in self.fm:
            if line.startswith("metadata:"):
                self.assertNotIn(
                    "{", line,
                    "metadata must be a block mapping; JSON flow style breaks parsing",
                )

    def test_version_present_and_quoted(self):
        self.assertIsNotNone(self.fm)
        hits = [l for l in self.fm if re.match(r"^\s+version:\s*(\S.*)$", l)]
        self.assertTrue(hits, "metadata.version is required")
        value = re.match(r"^\s+version:\s*(\S.*)$", hits[0]).group(1).strip()
        self.assertTrue(
            value.startswith('"') and value.endswith('"'),
            "version must be a quoted string, got: %s" % value,
        )

    def test_allowed_tools_is_a_string_not_a_list(self):
        self.assertIsNotNone(self.fm)
        for line in self.fm:
            if line.startswith("allowed-tools:"):
                self.assertNotIn("[", line, "allowed-tools must be space-separated")

    def test_under_500_lines(self):
        self.assertLessEqual(len(self.lines), 500)

    def _field(self, key):
        if not self.fm:
            return None
        for line in self.fm:
            m = re.match(r"^%s:\s*(.*)$" % re.escape(key), line)
            if m:
                return m.group(1).strip()
        return None


if __name__ == "__main__":
    unittest.main(verbosity=2)
