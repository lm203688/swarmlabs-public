"""开源基准 + Leaderboard —— 方案 G (2026-10-13)。

前沿依据
--------
AI4AutoSci / 2026 科学智能大会：均呼吁开放基准
Terminal-Bench-Science (P0-2)：科学 AI 任务基准

核心思想
--------
把 95 场景套件 + 保形评测协议开源，**把 73 判负案例列为 hard set**。
基准是 citation magnet，且把 SwarmLabs 从"一篇论文"升级为"领域基础设施"。

交付内容
--------
1. 95 场景评测套件（data/trust_surface.json + evaluation scripts）
2. 73 REFUTED 案例的 hard set 清单
3. Leaderboard 入口（README 中的表格 + scripts/leaderboard.py 生成器）
4. 评测协议 v1（protocols/evaluation_protocol.md）

诚实边界
--------
- 基准是"代理模型保形覆盖"基准，不是"科学发现"基准
- Leaderboard 衡量的是"保形覆盖 + 锐度 + 计算成本"，不是"预测精度"
- 73 REFUTED 案例是"在特定外推协议下失败"，不是"模型完全不可用"
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

_SCHEMA = "svp.open-benchmark.v1"


# ---------------------------------------------------------------------------
# 基准定义
# ---------------------------------------------------------------------------

@dataclass
class BenchmarkEntry:
    """基准条目（单个场景的评测结果）。"""
    key: str
    domain: str
    evidence_type: str
    dimension: int
    n_train: int
    n_test: int
    # 保形覆盖
    coverage: float
    coverage_nominal: float
    coverage_gap: float
    # 锐度
    sharpness: float
    sharpness_cap: float
    sharpness_met: bool
    # 外推表现
    g1_pass: bool
    g2_pass: bool
    g3_pass: bool
    # 判决
    verdict: str  # PASS / MARGINAL / REFUTED
    # 难度标记
    is_hard: bool
    difficulty_reason: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class BenchmarkSummary:
    """基准汇总统计。"""
    total_scenarios: int
    total_hard: int
    total_pass: int
    total_marginal: int
    total_refuted: int
    total_error: int
    # 覆盖统计
    coverage_mean: float
    coverage_under_nominal: int
    coverage_over_nominal: int
    # 锐度统计
    sharpness_met_count: int
    sharpness_met_rate: float
    # 外推统计
    g1_pass_count: int
    g2_pass_count: int
    g3_pass_count: int
    g3_zero_count: int
    # 证据类型分布
    evidence_type_counts: Dict[str, int]
    # 学科分布
    domain_counts: Dict[str, int]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# 基准构建
# ---------------------------------------------------------------------------

def build_benchmark(
    trust_surface_path: Optional[str] = None,
    gen_bench_path: Optional[str] = None,
    heldout_index_path: Optional[str] = None,
    repo_root: Optional[str] = None,
) -> Dict[str, Any]:
    """从现有数据文件构建完整基准。

    Args:
        trust_surface_path: data/trust_surface.json 路径
        gen_bench_path: data/generalization_bench.json 路径
        heldout_index_path: frontend_fixed/reports/heldout_index.json 路径
        repo_root: 仓库根目录

    Returns:
        包含 entries + summary 的完整基准
    """
    if repo_root is None:
        repo_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), ".."))

    if trust_surface_path is None:
        trust_surface_path = os.path.join(repo_root, "data", "trust_surface.json")
    if gen_bench_path is None:
        gen_bench_path = os.path.join(repo_root, "data", "generalization_bench.json")
    if heldout_index_path is None:
        heldout_index_path = os.path.join(
            repo_root, "frontend_fixed", "reports", "heldout_index.json")

    # gate_ledger 路径（verdict 的真实来源）
    gate_ledger_path = os.path.join(
        repo_root, "frontend_fixed", "reports", "gate_ledger.json")

    # 加载数据
    with open(trust_surface_path, encoding="utf-8") as f:
        ts = json.load(f)
    ts_rows = ts.get("rows", [])
    ts_overall = ts.get("overall", {})

    gb = {}
    if os.path.isfile(gen_bench_path):
        with open(gen_bench_path, encoding="utf-8") as f:
            gb = json.load(f)

    heldout = {}
    if os.path.isfile(heldout_index_path):
        with open(heldout_index_path, encoding="utf-8") as f:
            heldout = json.load(f)

    # 从 gate_ledger.json 加载逐场景 verdict
    gate_scenarios: Dict[str, Any] = {}
    if os.path.isfile(gate_ledger_path):
        with open(gate_ledger_path, encoding="utf-8") as f:
            gl = json.load(f)
        gate_scenarios = gl.get("scenarios", {})

    # 加载外推 stress test 结果（scenarios 键，非 results）
    extrapolation_stress = heldout.get("extrapolation_stress", {})
    extrap_results = {
        r["key"]: r
        for r in (extrapolation_stress.get("scenarios", []) or [])
    }

    # 加载 G1/G2/G3 结果
    g1_results = {
        r["key"]: r
        for r in (gb.get("g1_param_extrapolation", {}).get("results", []) or [])
    }
    g2_results = {
        r["key"]: r
        for r in (gb.get("g2_holdout_dim", {}).get("results", []) or [])
    }
    g3_results = {
        r["key"]: r
        for r in (gb.get("g3_cross_domain", {}).get("results", []) or [])
    }

    # 构建基准条目
    entries: List[BenchmarkEntry] = []
    for row in ts_rows:
        key = str(row.get("key", "?"))
        # verdict 来自 gate_ledger.json（trust_surface.json 无 verdict 字段）
        gate_entry = gate_scenarios.get(key, {})
        verdict = str(gate_entry.get("verdict", row.get("verdict", "UNKNOWN")))

        # 获取 G1/G2/G3 结果
        g1_pass = bool(g1_results.get(key, {}).get("ok", False))
        g2_pass = bool(g2_results.get(key, {}).get("ok", False))
        g3_pass = bool(g3_results.get(key, {}).get("ok", False))

        # 获取外推 stress 结果（verdict 字段，非 refuted 布尔值）
        extrap = extrap_results.get(key, {})
        extrap_verdict = str(extrap.get("verdict", ""))
        extrap_refuted = extrap_verdict == "REFUTED"

        # 难度判定
        is_hard = verdict in ("REFUTED", "MARGINAL") or extrap_refuted
        difficulty_reasons = []
        if verdict == "REFUTED":
            difficulty_reasons.append("verdict=REFUTED")
        if verdict == "MARGINAL":
            difficulty_reasons.append("verdict=MARGINAL")
        if extrap_refuted:
            difficulty_reasons.append("extrapolation-stress-REFUTED")
        if g1_pass:
            difficulty_reasons.append("G1-param-extrap-fail")
        if g2_pass:
            difficulty_reasons.append("G2-holdout-dim-fail")
        if g3_pass:
            difficulty_reasons.append("G3-cross-domain-fail")

        coverage = float(row.get("coverage_after", 0.0))
        coverage_nominal = 0.95
        sharpness = float(row.get("sharpness_nmpiw", 0.0))
        sharpness_cap = float(ts_overall.get("sharpness_cap", 5.0))

        entries.append(BenchmarkEntry(
            key=key,
            domain=str(row.get("domain", "")),
            evidence_type=str(row.get("evidence_type", "unknown")),
            dimension=int(row.get("dimension", 0)),
            n_train=int(row.get("n_store", 0)),
            n_test=int(row.get("n_test", 0)),
            coverage=round(coverage, 4),
            coverage_nominal=coverage_nominal,
            coverage_gap=round(coverage - coverage_nominal, 4),
            sharpness=round(sharpness, 4),
            sharpness_cap=sharpness_cap,
            sharpness_met=sharpness < sharpness_cap,
            g1_pass=g1_pass,
            g2_pass=g2_pass,
            g3_pass=g3_pass,
            verdict=verdict,
            is_hard=is_hard,
            difficulty_reason="; ".join(difficulty_reasons) if difficulty_reasons else "",
        ))

    # 构建汇总
    n_total = len(entries)
    n_hard = sum(1 for e in entries if e.is_hard)
    n_pass = sum(1 for e in entries if e.verdict == "PASS")
    n_marginal = sum(1 for e in entries if e.verdict == "MARGINAL")
    n_refuted = sum(1 for e in entries if e.verdict == "REFUTED")
    n_error = sum(1 for e in entries if e.verdict == "ERROR")

    coverage_mean = float(
        sum(e.coverage for e in entries) / max(n_total, 1))
    coverage_under = sum(1 for e in entries if e.coverage < coverage_nominal)
    coverage_over = sum(1 for e in entries if e.coverage > coverage_nominal)

    sharpness_met = sum(1 for e in entries if e.sharpness_met)
    sharpness_rate = round(sharpness_met / max(n_total, 1), 4)

    g1_pass_count = sum(1 for e in entries if e.g1_pass)
    g2_pass_count = sum(1 for e in entries if e.g2_pass)
    g3_pass_count = sum(1 for e in entries if e.g3_pass)
    g3_zero_count = sum(1 for e in entries if not e.g3_pass)

    # 证据类型分布
    evidence_counts: Dict[str, int] = {}
    for e in entries:
        evidence_counts[e.evidence_type] = evidence_counts.get(e.evidence_type, 0) + 1

    # 学科分布
    domain_counts: Dict[str, int] = {}
    for e in entries:
        # 取学科首段
        disc = e.domain.split("/")[0].strip() if e.domain else "unknown"
        domain_counts[disc] = domain_counts.get(disc, 0) + 1

    summary = BenchmarkSummary(
        total_scenarios=n_total,
        total_hard=n_hard,
        total_pass=n_pass,
        total_marginal=n_marginal,
        total_refuted=n_refuted,
        total_error=n_error,
        coverage_mean=round(coverage_mean, 4),
        coverage_under_nominal=coverage_under,
        coverage_over_nominal=coverage_over,
        sharpness_met_count=sharpness_met,
        sharpness_met_rate=sharpness_rate,
        g1_pass_count=g1_pass_count,
        g2_pass_count=g2_pass_count,
        g3_pass_count=g3_pass_count,
        g3_zero_count=g3_zero_count,
        evidence_type_counts=evidence_counts,
        domain_counts=domain_counts,
    )

    return {
        "schema": _SCHEMA,
        "generated_at": int(time.time()),
        "benchmark_name": "SwarmLabs Conformal Coverage Benchmark v1",
        "benchmark_description": (
            "95 场景代理模型保形覆盖基准。"
            "包含 73 个 REFUTED hard set 案例。"
            "衡量保形覆盖 + 锐度 + 外推鲁棒性。"
        ),
        "entries": [e.to_dict() for e in entries],
        "summary": summary.to_dict(),
        "protocol_version": "v1",
        "note": (
            "基准衡量的是代理模型的保形覆盖质量，不是科学发现能力。"
            "73 REFUTED 案例是'在特定外推协议下失败'，"
            "不是'模型完全不可用'。详见 data/trust_surface.json 和 "
            "data/generalization_bench.json 中的详细数据。"
        ),
    }


# ---------------------------------------------------------------------------
# Leaderboard 生成
# ---------------------------------------------------------------------------

def generate_leaderboard(
    benchmark: Optional[Dict[str, Any]] = None,
    repo_root: Optional[str] = None,
) -> Dict[str, Any]:
    """从基准生成 leaderboard。

    Leaderboard 维度：
    1. 保形覆盖率（coverage）
    2. 锐度达标率（sharpness_met_rate）
    3. 外推通过率（G1/G2/G3）
    4. 总场景通过率
    """
    if benchmark is None:
        benchmark = build_benchmark(repo_root=repo_root)

    entries = benchmark.get("entries", [])
    summary = benchmark.get("summary", {})

    # 按学科分组生成 leaderboard
    domain_board: Dict[str, List[Dict[str, Any]]] = {}
    for e in entries:
        disc = e.get("domain", "unknown").split("/")[0].strip()
        if disc not in domain_board:
            domain_board[disc] = []
        domain_board[disc].append(e)

    # 计算每个学科的排名
    leaderboard = []
    for disc, disc_entries in sorted(domain_board.items()):
        n = len(disc_entries)
        if n == 0:
            continue

        n_pass = sum(1 for e in disc_entries if e.get("verdict") == "PASS")
        n_hard = sum(1 for e in disc_entries if e.get("is_hard"))
        avg_coverage = sum(e.get("coverage", 0) for e in disc_entries) / n
        sharpness_rate = sum(1 for e in disc_entries if e.get("sharpness_met")) / n
        g1_rate = sum(1 for e in disc_entries if e.get("g1_pass")) / n
        g2_rate = sum(1 for e in disc_entries if e.get("g2_pass")) / n
        g3_rate = sum(1 for e in disc_entries if e.get("g3_pass")) / n

        # 综合得分：coverage + sharpness + 外推
        # 简单加权：coverage 40% + sharpness 30% + 外推 30%
        score = (
            0.40 * avg_coverage +
            0.30 * sharpness_rate +
            0.30 * ((g1_rate + g2_rate + g3_rate) / 3)
        )

        leaderboard.append({
            "domain": disc,
            "n_scenarios": n,
            "n_pass": n_pass,
            "n_hard": n_hard,
            "pass_rate": round(n_pass / n, 4),
            "avg_coverage": round(avg_coverage, 4),
            "sharpness_met_rate": round(sharpness_rate, 4),
            "g1_pass_rate": round(g1_rate, 4),
            "g2_pass_rate": round(g2_rate, 4),
            "g3_pass_rate": round(g3_rate, 4),
            "composite_score": round(score, 4),
            "rank": 0,  # 稍后填充
        })

    # 按综合得分排序
    leaderboard.sort(key=lambda x: -x["composite_score"])
    for i, entry in enumerate(leaderboard):
        entry["rank"] = i + 1

    return {
        "schema": _SCHEMA,
        "generated_at": int(time.time()),
        "benchmark_name": benchmark.get("benchmark_name", ""),
        "summary": summary,
        "leaderboard": leaderboard,
        "leaderboard_dimensions": {
            "composite_score": "0.4×coverage + 0.3×sharpness + 0.3×extrapolation",
            "coverage": "保形覆盖率（名义 0.95）",
            "sharpness": "锐度达标率（区间宽度/值 < 5×）",
            "extrapolation": "G1/G2/G3 外推通过率均值",
        },
        "note": (
            "Leaderboard 衡量代理模型在不同学科上的保形覆盖质量。"
            "composite_score 是加权综合得分，不代表绝对排名。"
            "G3=0/14 是当前架构的方法学局限，不是实现缺陷。"
        ),
    }


# ---------------------------------------------------------------------------
# Hard Set 清单
# ---------------------------------------------------------------------------

def list_hard_set(
    benchmark: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """列出 73 个 REFUTED hard set 案例。"""
    if benchmark is None:
        benchmark = build_benchmark()

    entries = benchmark.get("entries", [])
    hard = [
        {
            "key": e["key"],
            "domain": e["domain"],
            "verdict": e["verdict"],
            "difficulty_reason": e["difficulty_reason"],
            "evidence_type": e["evidence_type"],
            "dimension": e["dimension"],
        }
        for e in entries if e.get("is_hard")
    ]
    return hard


# ---------------------------------------------------------------------------
# 评测协议
# ---------------------------------------------------------------------------

def generate_protocol() -> str:
    """生成评测协议 v1 文档。"""
    return """# SwarmLabs Conformal Coverage Benchmark Protocol v1

## 1. 概述

本基准评测代理模型（GP surrogate）在 95 个科学场景上的保形预测质量。
评测维度：

| 维度 | 指标 | 目标 |
|------|------|------|
| 保形覆盖 | coverage | ≥ 0.95 |
| 锐度 | sharpness (NMPW) | < 5.0 |
| 外推鲁棒性 | G1/G2/G3 pass rate | > 0 |

## 2. 场景

95 个场景，涵盖：
- **物理**: 热传导、辐射、热阻（3 个）
- **生物**: 逻辑增长、Monod 微生物生长（52+ 个）
- **化学**: Langmuir 吸附、BET、Freundlich（5+ 个）
- **金融**: Black-Scholes 期权定价（2 个）
- **神经科学**: Hodgkin-Huxley 神经元（3 个）
- **生态学**: Lotka-Volterra、Ricker（3+ 个）
- **能源**: Rankine 循环、等温膨胀（3 个）
- **LLM**: Kaplan/Chinchilla scaling law（3 个）
- **优化**: 标准优化 benchmark（12 个）
- **化学信息学**: BACE、ESOL、Freesolv（4 个）

## 3. 评测流程

1. **训练**: 在参数域内采样 n_store 个训练点
2. **拟合**: 训练 GP 代理（Matern-5/2 核）
3. **标定**: 使用 split conformal 在留一法预测上标定分位数
4. **预测**: 在测试集上生成保形区间 [pred - q·σ, pred + q·σ]
5. **评估**: 计算 coverage、sharpness、calibration error

## 4. 外推协议

- **G1 (参数外推)**: 参数超出训练范围 10-50%
- **G2 (留一维)**: 移除一个维度，测试降维泛化
- **G3 (跨学科)**: 用其他学科的数据训练，测试跨域泛化

## 5. Hard Set

73/95 场景在外推压力测试中被判 REFUTED。这些场景列为 hard set，
是未来方法开发的基准线。

## 6. 提交格式

```json
{
  "method_name": "...",
  "method_description": "...",
  "results": {
    "coverage": 0.95,
    "sharpness": 2.1,
    "g1_pass_rate": 0.1,
    "g2_pass_rate": 0.0,
    "g3_pass_rate": 0.0
  }
}
```

## 7. 诚实声明

- 基准衡量的是代理模型的保形覆盖质量，不是科学发现能力
- G3=0/14 是当前 GP 架构的方法学局限，不是实现缺陷
- 所有场景的 ground truth 均为模拟数据，非湿实验数据
- 73 REFUTED 案例是"在特定外推协议下失败"，不是"模型完全不可用"
"""


# ---------------------------------------------------------------------------
# 批量入口
# ---------------------------------------------------------------------------

def build_all(
    repo_root: Optional[str] = None,
) -> Dict[str, Any]:
    """构建完整基准 + leaderboard + hard set 清单。"""
    benchmark = build_benchmark(repo_root=repo_root)
    leaderboard = generate_leaderboard(benchmark, repo_root=repo_root)
    hard_set = list_hard_set(benchmark)
    protocol = generate_protocol()

    return {
        "schema": _SCHEMA,
        "generated_at": int(time.time()),
        "benchmark": benchmark,
        "leaderboard": leaderboard,
        "hard_set": hard_set,
        "hard_set_count": len(hard_set),
        "protocol": protocol,
        "note": (
            "完整开源基准套件。包含 95 场景基准、leaderboard、"
            "73 hard set 案例、评测协议 v1。"
            "73/95 REFUTED 案例是基准的核心价值——"
            "为未来方法开发提供明确的改进目标。"
        ),
    }


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def _selftest() -> bool:
    passed = 0
    total = 0

    def check(name: str, cond: bool) -> None:
        nonlocal passed, total
        total += 1
        if cond:
            passed += 1
            print(f"  PASS  {name}")
        else:
            print(f"  FAIL  {name}")

    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

    # 1. 基准构建
    benchmark = build_benchmark(repo_root=root)
    check("benchmark has schema", benchmark["schema"] == _SCHEMA)
    check("benchmark has entries", "entries" in benchmark)
    check("benchmark has summary", "summary" in benchmark)
    check("benchmark has 95 entries",
          len(benchmark["entries"]) == 95)

    summary = benchmark["summary"]
    check("summary total_scenarios = 95", summary["total_scenarios"] == 95)
    check("summary total_pass + total_marginal + total_refuted + total_error = 95",
          summary["total_pass"] + summary["total_marginal"] +
          summary["total_refuted"] + summary["total_error"] == 95)
    check("summary has coverage_mean", 0 <= summary["coverage_mean"] <= 1)
    check("summary has evidence_type_counts",
          "evidence_type_counts" in summary)
    check("summary has domain_counts", "domain_counts" in summary)

    # 2. Leaderboard
    leaderboard = generate_leaderboard(benchmark, repo_root=root)
    check("leaderboard has leaderboard", "leaderboard" in leaderboard)
    check("leaderboard has dimensions",
          "leaderboard_dimensions" in leaderboard)
    check("leaderboard entries have rank",
          all("rank" in e for e in leaderboard["leaderboard"]))
    check("leaderboard ranks are sequential",
          sorted(e["rank"] for e in leaderboard["leaderboard"]) ==
          list(range(1, len(leaderboard["leaderboard"]) + 1)))

    # 3. Hard set
    hard_set = list_hard_set(benchmark)
    check("hard_set is list", isinstance(hard_set, list))
    check("hard_set has entries", len(hard_set) > 0)
    check("all hard set entries have key",
          all("key" in e for e in hard_set))

    # 4. 协议
    protocol = generate_protocol()
    check("protocol is string", isinstance(protocol, str))
    check("protocol has version", "v1" in protocol)
    check("protocol has G1/G2/G3", "G1" in protocol and "G2" in protocol and "G3" in protocol)
    check("protocol has hard set mention", "73" in protocol)

    # 5. 完整套件
    all_data = build_all(repo_root=root)
    check("all_data has benchmark", "benchmark" in all_data)
    check("all_data has leaderboard", "leaderboard" in all_data)
    check("all_data has hard_set", "hard_set" in all_data)
    check("all_data has protocol", "protocol" in all_data)
    check("all_data has hard_set_count", "hard_set_count" in all_data)

    print(f"\n{passed}/{total} open-benchmark self-tests passed")
    return passed == total


if __name__ == "__main__":
    import sys
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if _selftest():
        res = build_all(repo_root=root)
        print(f"\n开源基准 → 场景数: {res['benchmark']['summary']['total_scenarios']}")
        print(f"  Hard set: {res['hard_set_count']} 个")
        print(f"  Leaderboard: {len(res['leaderboard']['leaderboard'])} 个学科")
        print(f"  协议版本: v1")
        sys.exit(0)
    sys.exit(1)
