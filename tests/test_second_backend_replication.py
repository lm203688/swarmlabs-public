#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_second_backend_replication.py — B-1 replication 维度消费第二后端复算的测试。

覆盖：
  1. 判据分档（agree_exact / agree_direction / contradict）与方向性；
  2. _decide 的全部边界（含 RF 升级 GP 负判 = 矛盾）；
  3. replication 维度接线：账本优先、矛盾披露、无数据 fail-closed。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import verification_gate as vg
from scripts.build_second_backend_replication import _decide


# ── 1. 判据分档 ────────────────────────────────────────────────

def test_decide_exact():
    d, s = _decide("PASS", "PASS")
    assert (d, s) == ("agree_exact", 1.0)


def test_decide_directional_one_level_stricter():
    assert _decide("PASS", "MARGINAL") == ("agree_direction", 0.7)
    assert _decide("MARGINAL", "REFUTED") == ("agree_direction", 0.7)


def test_decide_contradict_two_levels_stricter():
    d, s = _decide("PASS", "REFUTED")
    assert (d, s) == ("contradict", 0.0)


def test_decide_contradict_when_rf_upgrades_negative():
    """RF 把 GP 的负判升级为 PASS —— 同样是复算失败（负判是家族特有的）。"""
    assert _decide("REFUTED", "PASS") == ("contradict", 0.0)
    assert _decide("MARGINAL", "PASS") == ("contradict", 0.0)


def test_decide_unavailable_on_missing_grade():
    assert _decide("PASS", None)[0] == "unavailable"


# ── 2. replication 维度接线 ────────────────────────────────────

def _patch_b1(monkeypatch, scenarios: dict) -> None:
    monkeypatch.setattr(vg, "_SECOND_BACKEND_CACHE", scenarios)


class TestReplicationDimension:
    def test_no_second_backend_data_is_zero(self, monkeypatch):
        _patch_b1(monkeypatch, {})
        score, note = vg._score_dimension({}, "replication", key="bio_logistic")
        assert score == 0.0
        assert "无第二后端复算数据" in note

    def test_ledger_pass_uses_same_backend(self, monkeypatch):
        _patch_b1(monkeypatch, {})
        score, note = vg._score_dimension(
            {"replication_passed": True}, "replication", key="bio_logistic")
        assert score == 1.0
        assert "同后端异种子" in note

    def test_ledger_pass_with_contradiction_is_disclosed(self, monkeypatch):
        """同后端通过 + 第二后端矛盾 → 分数取最佳证据，但反证必须披露。"""
        _patch_b1(monkeypatch, {
            "micro_sobol_ecoli": {
                "decision": "contradict", "score": 0.0,
                "gp": {"grade": "PASS"}, "rf": {"grade": "REFUTED"},
            },
        })
        score, note = vg._score_dimension(
            {"replication_passed": True}, "replication", key="micro_sobol_ecoli")
        assert score == 1.0
        assert "⚠ 第二后端矛盾" in note

    def test_ledger_fail_uses_second_backend(self, monkeypatch):
        _patch_b1(monkeypatch, {
            "surrogate_forrester": {
                "decision": "agree_direction", "score": 0.7,
                "gp": {"grade": "PASS"}, "rf": {"grade": "MARGINAL"},
            },
        })
        score, note = vg._score_dimension(
            {"replication_passed": False}, "replication",
            key="surrogate_forrester")
        assert score == 0.7
        assert "agree_direction" in note
