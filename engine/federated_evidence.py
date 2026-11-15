"""Federated Evidence (P1-8) — 跨域证据检索。

当 UQ 超阈值或主动学习无法收敛时，从 entity_store.json 的 137k+ 结构化科研实体
中检索同领域/跨域桥接证据，降低「黑箱外推」风险。

实体库真实结构（entity_store.json）:
  meta: {source, built_at, note}
  counts: {works, authors, ...}
  works: {openalex_id: {id, title, year}, ...}     # 无 domain 字段
  authors / institutions / concepts / venues / references: 同结构 dict
  insights: {
      trends:               [{id, name, field, n}, ...]
      research_gaps:        [{id, name, field, n}, ...]
      cross_domain_bridges: [{name, field, n, cross}, ...]
      coauthor_insights:    [{author_id, n_collaborators}, ...]
  }

本模块把 insights 当作「证据层」—— trend / gap / bridge 都带 `field`，可按领域
匹配、跨域桥接发现、gap 定位。
"""

from __future__ import annotations
import json, math
import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any

__all__ = ["Evidence", "federated_evidence"]


DEFAULT_STORE = Path(__file__).resolve().parent.parent / "data" / "entity_store.json"

# 场景名 → 科研领域关键词映射（scenario 的 domain 字段可能缺失，用此表兜底）
DOMAIN_MAP = {
    "forrester":       ["optimization", "mathematics", "statistics"],
    "branin":          ["optimization", "engineering", "statistics"],
    "hartmann6":       ["optimization", "mathematics", "engineering"],
    "heat1d":          ["physics", "thermal", "heat", "partial differential equation"],
    "logistic":        ["biology", "growth", "microbiology", "population"],
    "monod":           ["microbiology", "biochemistry", "kinetics", "bioprocess"],
    "inhibition":      ["microbiology", "pharmacology", "biochemistry", "kinetics"],
    "llm_scaling":     ["artificial intelligence", "language model", "machine learning",
                         "deep learning", "computer science"],
    # 扩充微生物场景（engine/microbiology_expanded.py）
    "micro_ecoli_glucose":          ["microbiology", "monod kinetics", "bioprocess", "bacterial growth modeling", "glucose"],
    "micro_cerevisiae_ethanol":     ["microbiology", "fermentation optimization", "ethanol", "yeast", "bioprocess"],
    "micro_putida_toluene":         ["microbiology", "substrate inhibition kinetics", "bioremediation", "pseudomonas", "toluene"],
    "micro_lactococcus_lactose":    ["microbiology", "fermentation optimization", "lactic acid", "lactose", "lactococcus"],
    "micro_acetobacter_acetate":    ["microbiology", "bioreactor optimization", "temperature", "acetate", "acetobacter"],
    "micro_methanobacterium_methane": ["microbiology", "methane", "archaea", "anaerobic", "biogeochemical"],
    "micro_chemostat_ecoli":        ["microbiology", "chemostat", "bioprocess", "e coli", "dilution"],
    "micro_chemostat_cerevisiae":   ["microbiology", "fermentation", "chemostat", "bioreactor optimization"],
    # 别名（用于 DOMAIN_MAP.get(scenario, [scenario]) 兜底匹配）
    "ecoli":       ["microbiology", "bacterial growth modeling", "monod kinetics", "glucose"],
    "cerevisiae":  ["microbiology", "fermentation optimization", "ethanol", "yeast"],
    "putida":      ["microbiology", "substrate inhibition kinetics", "bioremediation"],
    "lactococcus": ["microbiology", "fermentation optimization", "lactic acid"],
    "acetobacter": ["microbiology", "bioreactor optimization"],
    "methanobacterium": ["microbiology", "archaea", "methane"],
    "chemostat":   ["microbiology", "bioprocess", "continuous culture"],
}

INSIGHT_KEYS = ["trends", "research_gaps", "cross_domain_bridges"]


def _text_tokens(s: str) -> set[str]:
    return {t.lower() for t in (s or "").replace("-", " ").replace("_", " ").split()}


def _token_match(row: dict, query_tokens: set[str]) -> float:
    """计算一条 insight 记录与 query tokens 的 Jaccard 相似度。"""
    name = row.get("name", "")
    field_val = row.get("field", "")
    row_tokens = _text_tokens(name) | _text_tokens(field_val)
    if not row_tokens:
        return 0.0
    return len(query_tokens & row_tokens) / len(query_tokens)


@dataclass
class Evidence:
    scenario: str
    matched_count: int
    references: list[dict[str, Any]]
    gaps: list[dict[str, Any]]
    bridges: list[dict[str, Any]]
    suggestion: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario,
            "matched_count": self.matched_count,
            "n_references": len(self.references),
            "n_gaps": len(self.gaps),
            "n_bridges": len(self.bridges),
            "suggestion": self.suggestion,
        }


def federated_evidence(
    scenario: str,
    oracle,
    uq: Any,
    x: Any,
    store_path: Path | None = None,
    top_k: int = 5,
    match_threshold: float = 0.08,
) -> Evidence:
    """UQ 超阈值时触发：从 entity_store 检索同域/跨域证据。

    参数
    ----
    scenario : str
        场景名（如 'forrester', 'llm_scaling'）
    oracle : Callable
        真实 oracle（用于记录建议里"需要真实数据"的提示）
    uq : array-like
        UQ 估计（当前点）——主要用于判断是否真的需要证据
    x : array-like
        当前查询点
    store_path : Path, optional
        entity_store.json 路径，默认从本项目根目录取
    top_k : int
        每类最多返回的证据条数
    match_threshold : float
        Jaccard 匹配阈值，低于此值视为"未找到相关证据"

    返回
    ----
    Evidence 对象，含 matched_count / references / gaps / bridges / suggestion。
    """
    path = store_path or DEFAULT_STORE
    if not path.exists():
        return Evidence(
            scenario=scenario, matched_count=0,
            references=[], gaps=[], bridges=[],
            suggestion=f"[federated_evidence] 实体库不存在: {path}，跳过。",
        )

    with open(path, "r", encoding="utf-8") as f:
        store: dict = json.load(f)

    insights: dict = store.get("insights", {})
    works: dict = store.get("works", {})

    # 从领域映射表取场景的领域关键词；也尝试从 x/uo 推领域
    domain_keywords: list[str] = DOMAIN_MAP.get(scenario, [scenario])

    # 也提取 x 中可能的数值特征作为补充 token（如含字符串标签）
    query_tokens: set[str] = set()
    for kw in domain_keywords:
        query_tokens |= _text_tokens(kw)
    # 若 scenario 名本身含语义
    query_tokens |= _text_tokens(scenario)

    # --- 检索 insights ---
    trend_hits: list[tuple[float, dict]] = []
    gap_hits: list[tuple[float, dict]] = []
    bridge_hits: list[tuple[float, dict]] = []

    for bucket, hits in [
        ("trends", trend_hits),
        ("research_gaps", gap_hits),
        ("cross_domain_bridges", bridge_hits),
    ]:
        for row in insights.get(bucket, []):
            score = _token_match(row, query_tokens)
            if score >= match_threshold:
                hits.append((score, row))

    # 按分数降序
    trend_hits.sort(key=lambda t: -t[0])
    gap_hits.sort(key=lambda t: -t[0])
    bridge_hits.sort(key=lambda t: -t[0])

    refs = [{"source": "trend", **row} for _, row in trend_hits[:top_k]]
    gaps_list = [{"source": "research_gap", **row} for _, row in gap_hits[:top_k]]
    bridges = [{"source": "cross_domain_bridge", **row} for _, row in bridge_hits[:top_k]]

    total = len(refs) + len(gaps_list) + len(bridges)

    # 生成建议
    suggestion_parts = [f"[federated_evidence] 场景={scenario}"]
    if total == 0:
        suggestion_parts.append(
            f"在 {len(insights.get('trends', []))} 个趋势 / "
            f"{len(insights.get('research_gaps', []))} 个研究空白 / "
            f"{len(insights.get('cross_domain_bridges', []))} 个跨域桥中未找到领域 '{'/' .join(domain_keywords)}' 的匹配证据。"
            "建议：①扩充 entity_store 该领域实体；②检查 oracle 真实性；③直接补真实实验数据。"
        )
    else:
        suggestion_parts.append(
            f"命中 {total} 条相关证据"
            f"（趋势 {len(refs)} / 研究空白 {len(gaps_list)} / 跨域桥 {len(bridges)}）。"
            "建议：①优先关注命中分数最高的 research_gap 作为下一步真实实验方向；"
            "②跨域桥证据可用于设计迁移实验；"
            "③若 evidence 不足以降低 UQ，仍应补真实数据。"
        )
    suggestion = " ".join(suggestion_parts)

    return Evidence(
        scenario=scenario,
        matched_count=total,
        references=refs,
        gaps=gaps_list,
        bridges=bridges,
        suggestion=suggestion,
    )
