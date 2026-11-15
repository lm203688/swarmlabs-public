"""INV-1~5 hypothesis property-based tests.

为什么写这个：engine/formal_invariants.py 是 SwarmLabs 的核心诚实性
约束，此前只有 6/6 selftest case（手写点样本）。属性测试让每一条
不变量在**整个输入空间**上被验证，而不仅是几个精心挑的样本。

对论文的意义：审稿人问「你怎么知道 5 条不变量没有实现漏洞」时，
selftest 只能证明 6 个 case 通过；属性测试证明 5 条不变量在任意
结构化输入下都满足其数学定义（pass iff invariant holds）。

覆盖策略（每条不变量至少 1 正例 + 1 反例 + 若干边界）：
- INV-1 honesty: 任意 counts 字典，total=sum → pass; total≠sum → fail
- INV-2 REFUTED irreversibility: 空/无 REFUTED → fail; REFUTED+doi → pass
- INV-3 anchor 3D: DR anchor 三维全填 → pass; 缺维度 → fail
- INV-4 error bound: errors ≤ 1% total → pass; 超过 → fail
- INV-5 3D non-regression: cur ≥ base → pass; cur < base → fail
"""

from __future__ import annotations

import sys
import os
from typing import Any, Dict, List

# 让 tests/ 能 import engine/
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from hypothesis import given, strategies as st, assume

from engine.formal_invariants import (
    _inv1_honesty,
    _inv2_refuted_irreversible,
    _inv3_anchor_3d,
    _inv4_error_bound,
    _inv5_3d_non_regression,
    check_all,
    all_pass,
)


# =========================================================================
# 共享 strategies
# =========================================================================

count_strat = st.integers(min_value=0, max_value=100)


def verdict_counts_strat() -> "st.SearchStrategy[Dict[str, int]]":
    """任意非负整数 counts 字典，键是 4 种 verdict。"""
    return st.fixed_dictionaries({
        "PASS": count_strat,
        "MARGINAL": count_strat,
        "REFUTED": count_strat,
        "ERROR": count_strat,
    })


# =========================================================================
# INV-1 诚实性守恒
# =========================================================================

@given(counts=verdict_counts_strat())
def test_inv1_pass_when_sum_equals_total(counts: Dict[str, int]) -> None:
    """counts 加和等于 total 时 INV-1 必须 pass。"""
    total = sum(counts.values())
    ok, _ = _inv1_honesty({"total": total, "counts": counts})
    assert ok, f"INV-1 应通过但失败了 counts={counts} total={total}"


@given(counts=verdict_counts_strat(), offset=st.integers(min_value=1, max_value=50))
def test_inv1_fail_when_sum_mismatches(counts: Dict[str, int], offset: int) -> None:
    """counts 加和 ≠ total 时 INV-1 必须 fail。"""
    real_total = sum(counts.values())
    fake_total = real_total + offset
    ok, msg = _inv1_honesty({"total": fake_total, "counts": counts})
    assert not ok, f"INV-1 应失败但通过了: {msg}"
    assert "≠" in msg or "sum" in msg


def test_inv1_rejects_missing_fields() -> None:
    """缺 total 或 counts 时 INV-1 应 fail。"""
    assert not _inv1_honesty({"counts": {}})[0]
    assert not _inv1_honesty({"total": 10, "counts": None})[0]
    assert not _inv1_honesty({"total": 10})[0]
    assert not _inv1_honesty({})[0]


# =========================================================================
# INV-2 REFUTED 不可撤销
# =========================================================================

@given(
    n_refuted=st.integers(min_value=1, max_value=20),
    with_doi=st.booleans(),
)
def test_inv2_refuted_with_doi_pass(n_refuted: int, with_doi: bool) -> None:
    """有 REFUTED 场景时，全部带 doi → pass；缺 doi → fail。"""
    refuted_scenarios = {
        f"ref{i}": {
            "key": f"ref{i}",
            "verdict": "REFUTED",
            **({"doi": "10.1000/xyz"} if with_doi else {}),
        }
        for i in range(n_refuted)
    }
    # 加一个 PASS 让场景集非空但不干扰
    scenarios = {"pass0": {"key": "pass0", "verdict": "PASS"}, **refuted_scenarios}
    ok, msg = _inv2_refuted_irreversible({"scenarios": scenarios})
    if with_doi:
        assert ok, f"REFUTED 全带 doi 应通过: {msg}"
    else:
        assert not ok, f"REFUTED 缺 doi 应失败: {msg}"


def test_inv2_empty_scenarios_fails() -> None:
    """空 scenarios 或缺 REFUTED → INV-2 fail（SwarmLabs 公开失败原则）。"""
    ok, msg = _inv2_refuted_irreversible({"scenarios": {}})
    assert not ok
    assert "REFUTED" in msg or "无" in msg

    only_pass = _inv2_refuted_irreversible({
        "scenarios": {"a": {"verdict": "PASS"}}
    })
    assert not only_pass[0], "全无 REFUTED 应 fail"


def test_inv2_handles_list_scenarios() -> None:
    """INV-2 应同时支持 dict 和 list 两种 scenarios 结构。"""
    scenarios = [{"key": "k1", "id": "i1", "verdict": "REFUTED", "doi": "10.x/y"}]
    ok, msg = _inv2_refuted_irreversible({"scenarios": scenarios})
    assert ok, f"list 形式的 REFUTED+doi 应通过: {msg}"


# =========================================================================
# INV-3 三维证据完整性
# =========================================================================

@given(
    decision_relevant=st.booleans(),
    has_exp=st.booleans(),
    has_ver=st.booleans(),
    has_cond=st.booleans(),
)
def test_inv3_dr_anchor_must_be_3d(
    decision_relevant: bool,
    has_exp: bool,
    has_ver: bool,
    has_cond: bool,
) -> None:
    """决策相关 (DR) anchor 必须三维全填；非 DR anchor 缺维度也 OK。"""
    anchor = {
        "id": "A_test",
        "decision_relevant": decision_relevant,
        "experiment_system": "HepG2" if has_exp else None,
        "version": "v3" if has_ver else None,
        "operating_condition": "37C" if has_cond else None,
    }
    ok, _ = _inv3_anchor_3d({"anchors": [anchor]})
    if decision_relevant and not (has_exp and has_ver and has_cond):
        assert not ok, "DR anchor 缺维度应 fail"
    else:
        assert ok, "非 DR 或 DR 全 3D 应 pass"


@given(
    n_dr=st.integers(min_value=1, max_value=10),
    missing_dim=st.sampled_from(["experiment_system", "version", "operating_condition"]),
)
def test_inv3_any_missing_dim_fails(n_dr: int, missing_dim: str) -> None:
    """多 DR anchor，其中一个缺维度，整体应 fail。"""
    anchors = []
    for i in range(n_dr):
        a = {
            "id": f"A{i}",
            "decision_relevant": True,
            "experiment_system": "X",
            "version": "v1",
            "operating_condition": "37C",
        }
        if i == 0:
            a[missing_dim] = None
        anchors.append(a)
    ok, msg = _inv3_anchor_3d({"anchors": anchors})
    assert not ok, f"缺 {missing_dim} 应 fail"


def test_inv3_empty_anchors_passes() -> None:
    """无 anchor 时 INV-3 应 pass（无 DR anchor 需检查）。"""
    ok, msg = _inv3_anchor_3d({"anchors": []})
    assert ok, msg

    ok, msg = _inv3_anchor_3d({})
    assert ok, msg


# =========================================================================
# INV-4 ERROR ≤ 1% total
# =========================================================================

@given(
    total=st.integers(min_value=1, max_value=1000),
    error=st.integers(min_value=0, max_value=1),
)
def test_inv4_error_within_1pct_passes(total: int, error: int) -> None:
    """ERROR 率 ≤ 1% 时 INV-4 应 pass。"""
    assume(error <= total * 0.01)
    data = {
        "total": total,
        "counts": {"PASS": max(0, total - error - 1), "MARGINAL": 0,
                   "REFUTED": 1, "ERROR": error},
    }
    ok, _ = _inv4_error_bound(data)
    assert ok, f"ERROR={error}/{total} 应通过"


@given(
    total=st.integers(min_value=2, max_value=200),
)
def test_inv4_error_exceeds_1pct_fails(total: int) -> None:
    """ERROR 率 > 1% 时 INV-4 应 fail。"""
    error = int(total * 0.02)  # 2%
    assume(error >= 1)  # 至少 1 个 ERROR
    assume(error <= total)
    data = {
        "total": total,
        "counts": {"PASS": total - error - 1, "MARGINAL": 0,
                   "REFUTED": 1, "ERROR": error},
    }
    ok, msg = _inv4_error_bound(data)
    assert not ok, f"ERROR={error}/{total}={error/total:.3f} 应失败: {msg}"


def test_inv4_zero_total_fails() -> None:
    """total=0 时 INV-4 应 fail（避免除零陷阱）。"""
    ok, msg = _inv4_error_bound({"total": 0, "counts": {"ERROR": 0}})
    assert not ok
    assert "total=0" in msg


# =========================================================================
# INV-5 三维完整度不劣化
# =========================================================================

@given(
    cur=st.floats(min_value=0.70, max_value=1.0, allow_nan=False),
    base=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
)
def test_inv5_current_above_baseline_passes(cur: float, base: float) -> None:
    """cur ≥ base 时 INV-5 应 pass。"""
    assume(cur >= base - 1e-9)
    ok, _ = _inv5_3d_non_regression({
        "anchors_3d_coverage": cur,
        "anchors_3d_coverage_baseline": base,
    })
    assert ok, f"cur={cur} ≥ base={base} 应通过"


@given(
    cur=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    base=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
)
def test_inv5_current_below_baseline_fails(cur: float, base: float) -> None:
    """cur < base - 1e-9 时 INV-5 应 fail。"""
    assume(cur < base - 1e-9)
    ok, _ = _inv5_3d_non_regression({
        "anchors_3d_coverage": cur,
        "anchors_3d_coverage_baseline": base,
    })
    assert not ok, f"cur={cur} < base={base} 应失败"


def test_inv5_absolute_threshold_without_baseline() -> None:
    """无基线时，cur < 0.70 应 fail，cur ≥ 0.70 应 pass。"""
    ok_below, _ = _inv5_3d_non_regression({"anchors_3d_coverage": 0.50})
    assert not ok_below

    ok_above, _ = _inv5_3d_non_regression({"anchors_3d_coverage": 0.75})
    assert ok_above


def test_inv5_missing_field_fails() -> None:
    """缺 anchors_3d_coverage 字段时 INV-5 应 fail。"""
    ok, msg = _inv5_3d_non_regression({})
    assert not ok
    assert "anchors_3d_coverage" in msg


# =========================================================================
# check_all 集成属性
# =========================================================================

@given(counts=verdict_counts_strat())
def test_check_all_returns_all_invariants(counts: Dict[str, int]) -> None:
    """check_all 对任意输入必须返回全部已注册不变量的结果。

    注：不变量数会随版本增长（INV-1~5 是初始，INV-6 内容指纹来自 B4
    provenance_guard，未来还会继续加），因此不再硬编码为 5。用
    `KNOWN_INVARIANTS` 集合做真源，确保每个已知 invariant_id 都出现。
    """
    from engine.formal_invariants import INVARIANTS
    total = sum(counts.values())
    results = check_all({"total": total, "counts": counts})
    # 每个已注册 invariant 都出现一次
    expected_ids = [inv.id for inv in INVARIANTS]
    ids = [r.invariant_id for r in results]
    assert len(ids) == len(expected_ids), f"expected {len(expected_ids)}, got {len(ids)}"
    for inv_id in expected_ids:
        assert inv_id in ids, f"missing invariant {inv_id}"
    # 无重复
    assert len(ids) == len(set(ids)), f"duplicate invariants: {ids}"
    # 每个 CheckResult 有 invariant_id/name/holds/note 字段
    for r in results:
        assert hasattr(r, "holds")
        assert hasattr(r, "invariant_id")


def test_all_pass_semantics() -> None:
    """all_pass 逻辑：全 True → True；任一 False → False。"""
    from engine.formal_invariants import CheckResult
    cr = lambda inv, p: CheckResult(invariant_id=inv, name=inv, holds=p, note="")
    assert all_pass([cr("INV-1", True), cr("INV-2", True)])
    assert not all_pass([cr("INV-1", True), cr("INV-2", False)])
    assert not all_pass([cr("INV-1", False), cr("INV-2", True)])


if __name__ == "__main__":
    # 允许直接跑（不依赖 pytest）
    import traceback
    tests = [
        test_inv1_pass_when_sum_equals_total,
        test_inv1_fail_when_sum_mismatches,
        test_inv1_rejects_missing_fields,
        test_inv2_refuted_with_doi_pass,
        test_inv2_empty_scenarios_fails,
        test_inv2_handles_list_scenarios,
        test_inv3_dr_anchor_must_be_3d,
        test_inv3_any_missing_dim_fails,
        test_inv3_empty_anchors_passes,
        test_inv4_error_within_1pct_passes,
        test_inv4_error_exceeds_1pct_fails,
        test_inv4_zero_total_fails,
        test_inv5_current_above_baseline_passes,
        test_inv5_current_below_baseline_fails,
        test_inv5_absolute_threshold_without_baseline,
        test_inv5_missing_field_fails,
        test_check_all_returns_5_results,
        test_all_pass_semantics,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {t.__name__}: {type(e).__name__}: {e}")
            traceback.print_exc()
    print(f"\n{passed}/{len(tests)} hypothesis property tests passed")
    sys.exit(0 if passed == len(tests) else 1)
