#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_ablation_verdict.py — A-3 分级判定的单元 + 否定性测试（2026-10-18）。

覆盖：
  1. grade() 的四档行为（含边界：支撑维度 0.5 分不算缺失）；
  2. 缺失维度按 0 处理（fail-closed：缺 key ≠ 放行）；
  3. grade_from_plan / grade_distribution 与 ablation_plan() 的真实输出对接；
  4. 否定性测试：若有人把 core 门禁删掉，FAIL 档必须消失（测试会红）。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.ablation_verdict import (
    CORE_DIMENSIONS,
    SUPPORT_DIMENSIONS,
    grade,
    grade_distribution,
    grade_from_plan,
    missing_support,
)

# ── 1. 四档行为 ────────────────────────────────────────────────

def test_full_when_all_dimensions_pass():
    scores = {d: 1.0 for d in CORE_DIMENSIONS + SUPPORT_DIMENSIONS}
    scores["benchmark"] = 1.0
    assert grade(scores) == "PASS_FULL"


def test_partial_when_one_support_missing():
    scores = {"fidelity": 1.0, "coverage": 1.0,
              "anchor": 1.0, "replication": 1.0, "sensitivity": 0.0}
    assert grade(scores) == "PASS_PARTIAL"


def test_weak_when_two_or_more_support_missing():
    scores = {"fidelity": 1.0, "coverage": 1.0,
              "anchor": 1.0, "replication": 0.0, "sensitivity": 0.0}
    assert grade(scores) == "PASS_WEAK"


def test_fail_when_core_below_threshold():
    scores = {d: 1.0 for d in CORE_DIMENSIONS + SUPPORT_DIMENSIONS}
    scores["coverage"] = 0.49
    assert grade(scores) == "FAIL"
    scores["coverage"] = 1.0
    scores["fidelity"] = 0.0
    assert grade(scores) == "FAIL"


def test_support_half_credit_is_not_missing():
    """支撑维度 0.5 分 = 部分支撑，不计为「缺失」（阈值是严格 <）。"""
    scores = {"fidelity": 1.0, "coverage": 1.0,
              "anchor": 0.5, "replication": 0.5, "sensitivity": 0.5}
    assert grade(scores) == "PASS_FULL"


def test_unanchored_0_3_counts_as_missing():
    """诚实性回归测试（2026-10-18）：anchor=0.3 是「无锚定/零信息」，
    必须计为缺失支撑维度。若这条红，说明分级 verdict 已退化成
    「85/91 PASS_FULL」式的过度声称。"""
    scores = {"fidelity": 1.0, "coverage": 1.0,
              "anchor": 0.3, "replication": 1.0, "sensitivity": 1.0}
    assert grade(scores) == "PASS_PARTIAL"
    assert missing_support(scores) == ["anchor"]


# ── 2. fail-closed：缺 key 按 0 处理 ───────────────────────────

def test_missing_keys_treated_as_zero():
    assert grade({}) == "FAIL"                       # 核心缺失 → FAIL
    assert grade({"fidelity": 1.0}) == "FAIL"        # coverage 缺失 → FAIL
    assert grade({"fidelity": 1.0, "coverage": 1.0}) == "PASS_WEAK"  # 三支撑全缺


# ── 3. 与 ablation_plan() 真实输出对接 ─────────────────────────

def test_grade_from_plan_matches_manual_grade():
    plan = {
        "dimensions": {
            "fidelity": {"score": 1.0, "note": "x"},
            "coverage": {"score": 0.8, "note": "x"},
            "anchor": {"score": 0.3, "note": "x"},       # 无锚定（零信息）
            "replication": {"score": 0.0, "note": "x"},
            "benchmark": {"score": 1.0, "note": "x"},
            "sensitivity": {"score": 0.0, "note": "x"},
        }
    }
    # anchor=0.3 是「无锚定」而非「有弱锚」——按 <0.5 判缺失（2026-10-18 修正），
    # 否则 83 个无锚定场景会被计为有支撑，分级 verdict 退化成过度声称。
    assert grade_from_plan(plan) == "PASS_WEAK"
    assert missing_support(
        {k: v["score"] for k, v in plan["dimensions"].items()}
    ) == ["anchor", "replication", "sensitivity"]


def test_grade_distribution_sums_to_total():
    plans = {
        "a": {"dimensions": {d: {"score": 1.0} for d in
                             CORE_DIMENSIONS + SUPPORT_DIMENSIONS}},
        "b": {"dimensions": {"fidelity": {"score": 1.0}, "coverage": {"score": 1.0},
                             "anchor": {"score": 0.0}, "replication": {"score": 0.0},
                             "sensitivity": {"score": 0.0}}},
        "c": {"dimensions": {"fidelity": {"score": 0.1}, "coverage": {"score": 1.0}}},
    }
    dist = grade_distribution(plans)
    assert dist == {"FAIL": 1, "PASS_WEAK": 1, "PASS_PARTIAL": 0, "PASS_FULL": 1}
    assert sum(dist.values()) == len(plans)


# ── 4. 否定性测试：核心门禁不允许被静默拆除 ────────────────────

def test_negation_core_gate_is_load_bearing():
    """若 core 门禁失效（恒返回 PASS_FULL），本测试必须红。"""
    sabotaged = {d: 1.0 for d in SUPPORT_DIMENSIONS}
    sabotaged["fidelity"] = 0.0     # 核心塌方
    sabotaged["coverage"] = 0.0
    g = grade(sabotaged)
    assert g == "FAIL", (
        f"核心维度全零却得到 {g}——A-3 分级判定的 FAIL 档失效，"
        "等于退回'91 全 PASS'的旧口径"
    )
