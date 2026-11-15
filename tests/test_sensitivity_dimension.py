#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_sensitivity_dimension.py — A-4 sensitivity 维度消费扰动审计的测试。

覆盖：
  1. 无审计数据 → 0.0（fail-closed：缺文件不等于放行）；
  2. flip_rate=0 → 1.0（零翻转 = 完全稳健）；
  3. flip_rate=0.375 → 0.625（线性：扰动下 verdict 存活比例）；
  4. 有 sobol/doe/uq 变体的场景走旧口径，优先于审计；
  5. 审计无 applicable 配置 → 0.0 且注明原因。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import verification_gate as vg


def _patch_audit(monkeypatch, scenarios: dict) -> None:
    monkeypatch.setattr(vg, "_SENSITIVITY_AUDIT_CACHE", scenarios)


class TestSensitivityDimension:
    def test_no_audit_data_is_zero(self, monkeypatch):
        _patch_audit(monkeypatch, {})
        score, note = vg._score_dimension({}, "sensitivity", key="bio_logistic")
        assert score == 0.0
        assert "无扰动审计" in note

    def test_zero_flip_rate_full_score(self, monkeypatch):
        _patch_audit(monkeypatch, {
            "bio_logistic": {"flip_rate": 0.0, "n_applicable": 8, "n_flipped": 0},
        })
        score, note = vg._score_dimension({}, "sensitivity", key="bio_logistic")
        assert score == 1.0
        assert "flip_rate=0.00" in note

    def test_partial_flip_rate_linear(self, monkeypatch):
        _patch_audit(monkeypatch, {
            "micro_diauxic_ecoli": {"flip_rate": 0.375, "n_applicable": 8,
                                    "n_flipped": 3},
        })
        score, note = vg._score_dimension({}, "sensitivity",
                                          key="micro_diauxic_ecoli")
        assert score == 0.625
        assert "3 个 verdict 翻转" in note

    def test_no_applicable_configs_is_zero(self, monkeypatch):
        _patch_audit(monkeypatch, {
            "broken_case": {"flip_rate": None, "n_applicable": 0, "n_flipped": 0},
        })
        score, note = vg._score_dimension({}, "sensitivity", key="broken_case")
        assert score == 0.0
        assert "无 applicable 配置" in note

    def test_variant_prefix_wins_over_audit(self, monkeypatch):
        """已有 sobol/doe 变体场景走旧口径（1.0），不依赖审计文件。"""
        _patch_audit(monkeypatch, {})
        score, note = vg._score_dimension({}, "sensitivity",
                                          key="micro_sobol_ecoli_glucose")
        assert score == 1.0
        assert "灵敏度变体" in note

    def test_unknown_dimension_is_zero(self):
        score, note = vg._score_dimension({}, "no_such_dim", key="x")
        assert score == 0.0
