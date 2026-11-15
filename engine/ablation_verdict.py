#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ablation_verdict.py — A-3：让消融维度参与 verdict（分级判定，2026-10-18）。

背景（第九轮科学性评估报告，A-3）：
    ablation_plan_batch() 的六维评估此前**不参与判定**——91 个 PASS 场景里
    sensitivity 95.6% 零分、anchor 85 缺口、replication 55 缺口，但 verdict
    仍是清一色 PASS。「缺口不参与判定」是审稿人必问的问题：结论的支撑强度
    与支撑证据的完整度脱节。

修法不是把 PASS 改成 FAIL（那会丢掉 fidelity/coverage 的真实信号），而是
引入**分级判定**：核心维度（fidelity/coverage）保持硬门禁，支撑维度
（anchor/replication/sensitivity）决定结论的**置信度等级**。

    core     = ["fidelity", "coverage"]           # 缺一即 FAIL
    support  = ["anchor", "replication", "sensitivity"]  # 决定置信度

    FAIL        任一核心维度 < 0.5
    PASS_FULL   核心全过 + 0 个支撑维度为零
    PASS_PARTIAL 核心全过 + 恰好 1 个支撑维度为零（需在论文中声明缺口）
    PASS_WEAK   核心全过 + ≥2 个支撑维度为零（结论需谨慎）

benchmark 维度照常计分并报告，但**不参与**分级判定：它衡量「报告里有没有
解析解/基准引用」（呈现完整性），不属于证据强度维度。这一点在论文中显式
声明，避免「六维」与「五维判定」的口径混淆。

设计约束：
    - 纯函数、零依赖（不 import verification_gate，避免循环引用）；
    - 只消费 ablation_plan() 已算好的维度分，不重复评估逻辑。
"""
from __future__ import annotations

#: 核心维度：缺一即 FAIL（与 verification_gate 的 PASS 阈值语义一致）
CORE_DIMENSIONS: tuple[str, ...] = ("fidelity", "coverage")

#: 支撑维度：零分即计为「缺失」，决定置信度等级
SUPPORT_DIMENSIONS: tuple[str, ...] = ("anchor", "replication", "sensitivity")

#: 核心维度的硬门禁阈值（与 PASS_R2 / COVERAGE_PASS 的 0.5 语义对齐）
CORE_THRESHOLD: float = 0.5

#: 支撑维度「缺失」的判定阈值。
#: 说明（与报告参考实现的唯一偏差，刻意记录）：报告给的参考实现用
#: `scores[s] == 0` 判缺失，但在本代码库里 anchor 维度对「无锚定/未评估」
#: 的场景打 0.3 分（零信息），0 分只保留给「与文献矛盾」的 fail-closed 场景。
#: 若按 `== 0` 判缺失，83 个无锚定场景会被计为「有支撑」，分级 verdict
#: 会退化成 85/91 PASS_FULL——正是它本来要防止的过度声称。故此处与
#: ablation_plan() 既有的缺口定义对齐：score < 0.5 即缺口（缺失）。
SUPPORT_MISSING_THRESHOLD: float = 0.5

#: 分级 verdict 全集（顺序即置信度递减）
GRADES: tuple[str, ...] = ("FAIL", "PASS_WEAK", "PASS_PARTIAL", "PASS_FULL")

#: 每个等级的人类可读含义（论文/README 直接引用，避免口径漂移）
GRADE_MEANING: dict[str, str] = {
    "FAIL": "核心维度（fidelity 或 coverage）未达标——不得作为可信结论呈现",
    "PASS_WEAK": "核心达标但 ≥2 个支撑维度缺失——结论需谨慎，须显式声明证据缺口",
    "PASS_PARTIAL": "核心达标，1 个支撑维度缺失——结论可用，缺口须声明",
    "PASS_FULL": "核心达标且支撑维度完整——全维度证据支撑的结论",
}


def grade(scores: dict) -> str:
    """六维分数 → 分级 verdict，而非布尔 PASS。

    Args:
        scores: {dimension: score} 映射（缺失维度按 0 处理）。

    Returns:
        "FAIL" | "PASS_WEAK" | "PASS_PARTIAL" | "PASS_FULL"
    """
    for c in CORE_DIMENSIONS:
        if float(scores.get(c, 0.0) or 0.0) < CORE_THRESHOLD:
            return "FAIL"
    n_missing = sum(
        1 for s in SUPPORT_DIMENSIONS
        if float(scores.get(s, 0.0) or 0.0) < SUPPORT_MISSING_THRESHOLD
    )
    if n_missing == 0:
        return "PASS_FULL"
    if n_missing <= 1:
        return "PASS_PARTIAL"
    return "PASS_WEAK"


def grade_from_plan(plan: dict) -> str:
    """从 ablation_plan() 的输出直接分级（便捷入口，不重复评估逻辑）。"""
    dims = (plan or {}).get("dimensions") or {}
    scores = {k: float((v or {}).get("score", 0.0) or 0.0) for k, v in dims.items()}
    return grade(scores)


def grade_distribution(plans: dict) -> dict:
    """批量分级统计：{PASS_FULL: n, PASS_PARTIAL: n, PASS_WEAK: n, FAIL: n}。

    Args:
        plans: ablation_plan_batch() 的 plans 字段（{key: plan}）。
    """
    dist = {g: 0 for g in GRADES}
    for plan in (plans or {}).values():
        dist[grade_from_plan(plan)] += 1
    return dist


def missing_support(scores: dict) -> list[str]:
    """列出得分低于缺失阈值的支撑维度（供论文「缺口声明」节直接引用）。"""
    return [
        s
        for s in SUPPORT_DIMENSIONS
        if float((scores or {}).get(s, 0.0) or 0.0) < SUPPORT_MISSING_THRESHOLD
    ]


def explain(g: str) -> str:
    """分级 verdict 的人类可读含义。"""
    return GRADE_MEANING.get(g, "未知等级")
