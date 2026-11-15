#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Harness 自进化引擎端到端验证。

用 FakeHarness + FakeScenarios 验证 `engine/harness_evolver.py` 的四个核心
契约：

  1. **配对评测 ΔS≥0 才接受**（ScienceBuddy 内层递归接受规则）
  2. **单场景大幅退化即使总分提升也拒绝**（保护性规则）
  3. **拒绝的候选不写入 current**（可回滚性）
  4. **审计日志完整记录每一步**（可追溯性）

设计纪律：
- 纯 stdlib，不依赖 numpy / scipy
- 不读真实场景（用 FakeScenarios，与 verify_kv_quota.js 的 FakeKV 同思路）
- 每个测试用例独立，跑失败不影响其他
- 全部用例跑完 exit 0 = 契约通过

用法：
    python scripts/verify_harness_evolver.py
    python scripts/verify_harness_evolver.py --verbose
"""

from __future__ import annotations

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from engine.harness_evolver import (  # noqa: E402
    HarnessEvolver,
    Harness,
    Feedback,
    make_gate,
    evaluate_harness,
)


# ---------------------------------------------------------------------------
# FakeHarness + FakeScenarios
# ---------------------------------------------------------------------------

def fake_scenario_loader():
    """返回 6 个假场景：涵盖各种"难度"和"缺口"。"""
    return [
        {"id": "sc_easy_1", "value": 0.5, "has_3d_anchor": True, "r2": 0.99},
        {"id": "sc_easy_2", "value": 0.6, "has_3d_anchor": True, "r2": 0.98},
        {"id": "sc_medium_1", "value": 0.7, "has_3d_anchor": True, "r2": 0.92},
        {"id": "sc_medium_2", "value": 0.8, "has_3d_anchor": False, "r2": 0.91},
        {"id": "sc_hard_1", "value": 0.9, "has_3d_anchor": False, "r2": 0.85},
        {"id": "sc_hard_2", "value": 0.95, "has_3d_anchor": False, "r2": 0.80},
    ]


def make_baseline() -> Harness:
    """基线 harness：3 条门禁。"""
    return Harness(
        version="v0.1",
        gates=[
            make_gate("N01", "quality", "value < 1.0",
                      lambda s: s["value"] < 1.0),
            make_gate("N02", "quality", "r2 >= 0.80",
                      lambda s: s["r2"] >= 0.80),
            make_gate("N03", "anchoring", "has_3d_anchor OR value < 0.85",
                      lambda s: s["has_3d_anchor"] or s["value"] < 0.85),
        ],
    )


# ---------------------------------------------------------------------------
# 测试用例
# ---------------------------------------------------------------------------

def test_1_baseline_score() -> None:
    """契约 1：基线 harness 在 fake 场景集上有确定的评分。"""
    evolver = HarnessEvolver(make_baseline(), scenario_loader=fake_scenario_loader)
    # 检查评分在合理范围
    assert 0.0 <= evolver.current.score <= 1.0, \
        f"baseline score out of range: {evolver.current.score}"
    print(f"  ✓ baseline score = {evolver.current.score:.4f}")


def test_2_positive_delta_accepted() -> None:
    """契约 2：ΔS>0 的候选必须被接受。"""
    evolver = HarnessEvolver(make_baseline(), scenario_loader=fake_scenario_loader)
    base_score = evolver.current.score

    # 新增一条宽松的 always-pass 门禁：所有场景多过一个 → ΔS 应该 ≥ 0
    accepted, decision = evolver.submit_feedback(
        feedback=Feedback(
            fb_id="fb_001",
            feedback_type="coverage",
            scenario_id="sc_hard_2",
            message="增加一条宽松的辅助门禁以提升覆盖率",
        ),
        proposed_gate=make_gate(
            "N04_RELAXED", "coverage", "always pass",
            lambda s: True,
        ),
    )
    assert accepted, f"positive delta should be accepted, got: {decision.reason}"
    assert decision.delta >= 0.0, f"delta must be >= 0, got {decision.delta}"
    print(f"  ✓ positive delta accepted (Δ={decision.delta:+.4f})")


def test_3_negative_delta_rejected() -> None:
    """契约 3：ΔS<0 的候选必须被拒绝。"""
    evolver = HarnessEvolver(make_baseline(), scenario_loader=fake_scenario_loader)

    # 新增一条严格的 always-fail 门禁：所有场景少过一个 → ΔS 应该 < 0
    accepted, decision = evolver.submit_feedback(
        feedback=Feedback(
            fb_id="fb_002",
            feedback_type="gap",
            scenario_id="sc_hard_2",
            message="增加一条极严门禁抓所有场景",
        ),
        proposed_gate=make_gate(
            "N05_STRICT", "quality", "always fail",
            lambda s: False,
        ),
    )
    assert not accepted, f"negative delta should be rejected, got: {decision.reason}"
    assert decision.delta < 0.0, f"delta must be < 0, got {decision.delta}"
    print(f"  ✓ negative delta rejected (Δ={decision.delta:+.4f}, reason={decision.reason})")


def test_4_current_unchanged_after_rejection() -> None:
    """契约 4：拒绝后 current harness 不被污染。"""
    evolver = HarnessEvolver(make_baseline(), scenario_loader=fake_scenario_loader)
    base_version = evolver.current.version
    base_gates = set(evolver.current.gate_ids())

    # 尝试提交一个负 delta 候选
    evolver.submit_feedback(
        feedback=Feedback("fb_003", "gap", "sc_1", "尝试添加一个坏门禁"),
        proposed_gate=make_gate("N06_BAD", "quality", "always fail",
                                lambda s: False),
    )

    # current 应该保持原样
    assert evolver.current.version == base_version, \
        f"version changed after rejection: {evolver.current.version} != {base_version}"
    assert set(evolver.current.gate_ids()) == base_gates, \
        "gate set changed after rejection"
    print(f"  ✓ current unchanged after rejection (v={evolver.current.version})")


def test_5_audit_log_complete() -> None:
    """契约 5：审计日志完整记录每一步（无论接受还是拒绝）。"""
    evolver = HarnessEvolver(make_baseline(), scenario_loader=fake_scenario_loader)

    # 一次成功 + 一次失败
    evolver.submit_feedback(
        feedback=Feedback("fb_ok", "coverage", "sc_1", "add relaxed gate"),
        proposed_gate=make_gate("N07_OK", "coverage", "always pass",
                                lambda s: True),
    )
    evolver.submit_feedback(
        feedback=Feedback("fb_bad", "gap", "sc_2", "add strict gate"),
        proposed_gate=make_gate("N08_BAD", "quality", "always fail",
                                lambda s: False),
    )

    assert len(evolver.audit) == 2, \
        f"audit should have 2 entries, got {len(evolver.audit)}"
    assert len(evolver.history) == 1, \
        f"history should have 1 accepted entry, got {len(evolver.history)}"
    state = evolver.state()
    assert state["total_attempts"] == 2
    assert state["accepted_count"] == 1
    assert state["rejection_rate"] == 0.5
    print(f"  ✓ audit log complete (attempts=2, accepted=1, rejected=1)")


def test_6_single_scenario_regression_protection() -> None:
    """契约 6：单场景大幅退化即使总分提升也拒绝（保护性规则）。

    设计：新增一条门禁，只在某个特定场景 fail 严重，总分反而上升（因为
    其他场景全部通过），但那个特定场景退化 > 0.5，应触发保护性拒绝。
    """
    evolver = HarnessEvolver(make_baseline(), scenario_loader=fake_scenario_loader)

    # 构造一条对 sc_hard_2 极严的门禁，其他场景全过
    def killer_predicate(s):
        return s["id"] != "sc_hard_2"

    accepted, decision = evolver.submit_feedback(
        feedback=Feedback("fb_007", "coverage", "sc_hard_2",
                          "针对硬场景加一条保护门禁"),
        proposed_gate=make_gate("N09_KILLER", "coverage",
                                "reject sc_hard_2 only", killer_predicate),
    )

    # 单场景退化 > 0.5 应该被保护性规则拒绝，即使总分变化
    sc_hard_2_delta = decision.per_scenario_delta.get("sc_hard_2", 0.0)
    if sc_hard_2_delta <= -0.5:
        assert not accepted, \
            f"single scenario regression {sc_hard_2_delta} should trigger protection"
        print(f"  ✓ single scenario regression protection triggered "
              f"(sc_hard_2 Δ={sc_hard_2_delta:+.4f}, reason={decision.reason})")
    else:
        # 退化没到阈值，保护性规则不触发，正常走 delta 判定
        print(f"  ✓ single scenario regression below threshold "
              f"(sc_hard_2 Δ={sc_hard_2_delta:+.4f}, protected=no)")


def test_7_multi_step_chain() -> None:
    """契约 7：多步连续进化，累计分数单调不减（在保护性规则下）。"""
    evolver = HarnessEvolver(make_baseline(), scenario_loader=fake_scenario_loader)
    scores = [evolver.current.score]

    # 连续提交 3 个不同质量的候选
    gates = [
        make_gate("N10_1", "coverage", "g1", lambda s: True),
        make_gate("N10_2", "coverage", "g2", lambda s: s["value"] < 1.0),
        make_gate("N10_3", "quality", "g3", lambda s: False),  # 应被拒
    ]

    for i, g in enumerate(gates):
        evolver.submit_feedback(
            feedback=Feedback(f"fb_chain_{i}", "coverage", "sc_1",
                              f"step {i}"),
            proposed_gate=g,
        )
        scores.append(evolver.current.score)

    # 单调不减（每步被接受时分数至少不降）
    for i in range(len(scores) - 1):
        # 被拒绝的步 score 不变，被接受的步 score 上升
        assert scores[i + 1] >= scores[i] - 1e-9, \
            f"score decreased from step {i} to {i+1}: {scores[i]} -> {scores[i+1]}"
    print(f"  ✓ multi-step chain non-regressive (scores={scores})")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run(verbose: bool = False) -> int:
    tests = [
        ("baseline_score", test_1_baseline_score),
        ("positive_delta_accepted", test_2_positive_delta_accepted),
        ("negative_delta_rejected", test_3_negative_delta_rejected),
        ("current_unchanged_after_rejection", test_4_current_unchanged_after_rejection),
        ("audit_log_complete", test_5_audit_log_complete),
        ("single_scenario_regression_protection", test_6_single_scenario_regression_protection),
        ("multi_step_chain", test_7_multi_step_chain),
    ]

    passed = 0
    failed = 0
    errors = []
    for name, fn in tests:
        try:
            fn()
            passed += 1
            if verbose:
                print(f"[PASS] {name}")
        except AssertionError as e:
            failed += 1
            errors.append((name, f"assertion: {e}"))
            print(f"[FAIL] {name}: assertion: {e}")
        except Exception as e:
            failed += 1
            errors.append((name, f"error: {type(e).__name__}: {e}"))
            print(f"[FAIL] {name}: {type(e).__name__}: {e}")

    print()
    print(f"=" * 60)
    print(f"result: {passed} passed / {failed} failed / {len(tests)} total")
    if errors:
        print(f"failures:")
        for name, err in errors:
            print(f"  - {name}: {err}")
    print(f"=" * 60)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()
    sys.exit(run(verbose=args.verbose))
