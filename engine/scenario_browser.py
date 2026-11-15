"""场景浏览器查询接口（B1，板块 01 P1-4）。

背景
----
`engine/scenario_registry.py` 是场景的**装配单一真源**（95 条），但只提供
「有序全量清单」，没有查询 API。Agent 想要「给我所有微生物场景」「给我所有
gold 级溯源的场景」「给我所有维数 ≤ 2 的简单场景」时，只能自己遍历全表
+ 自己定义字段口径——这必然漂移。

本模块
------
1. 三个正交查询接口：
   - `query_by_discipline`：按学科（`domain_norm`，14 类）筛
   - `query_by_trust`：按溯源强度（gold/silver/bronze/none）筛
   - `query_by_difficulty`：按维数 + ls 派生 difficulty（easy/medium/hard）筛
2. 三个派生 tier 函数（`trust_tier` / `difficulty_tier` / `scenario_meta`）
   是**公开契约**——任何下游要复用 tier 定义必须走这里，不许私自在别处
   再算一遍（防漂移）。
3. 全部基于 `load_registry()`，不复制场景数据、不做二次装配。

对齐 12 板块报告的诉求
----------------------
- 板块 01 场景库：需要「按维度筛选」而不是硬编码 key 列表
- 板块 11 vlab_contract：`verified_by` 字段常引用「本学科所有场景的覆盖率」
- Agent 端（tool_ocean.scenario_browser 已预接入）：需要可发现的查询 API

诚实边界
--------
- `difficulty_tier` 的阈值（dim≤2 easy / dim≤4 medium / else hard；
  ls<0.05 hard 加权）是启发式，不是学术共识。修改前先在报告中说明。
- `trust_tier` 依赖 `published / references / ref_papers` 字段的填充完整度，
  任何一条来源的填充口径不一致会漏判（不是本模块的责任，是数据源的责任）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .scenario_registry import ScenarioRegistry, load_registry

#: 溯源强度 tier 定义（按强度从高到低）
TRUST_TIERS: tuple = ("gold", "silver", "bronze", "none")

#: 难度 tier 定义（按难度从低到高）
DIFFICULTY_TIERS: tuple = ("easy", "medium", "hard")

#: 学科白名单（**实际归并口径 10 类**——与 registry.disciplines() 一致）
#: 注：这不是硬约束——`domain_norm` 从装配处动态算出，任何新值都会被自动接纳。
#: 这里仅列出当前 registry **实际观察到**的 canonical 值，供文档和类型提示使用。
#:
#: 2026-10-28 口径修正：此前白名单写 14 类，但 `canon_domain()` 会把
#: biology/microorganisms 归并到 microbiology、cheminformatics 归并到
#: chemistry，thermal/heat 当前为 0 场景——因此对外「14 学科」是口径膨胀，
#: 真实归并后仅 10 个规范学科（全部 ≥2 场景）。对外一律用
#: `scenario_registry.disciplines()`（= 本列表），不再用声明值计数。
KNOWN_DISCIPLINES: tuple = (
    "surrogate_benchmark",
    "optimization",
    "physics",
    "microbiology",
    "llm",
    "chemistry",
    "finance",
    "neuroscience",
    "ecology",
    "energy",
)


# ---------------------------------------------------------------------------
# Tier 派生（公开契约）
# ---------------------------------------------------------------------------

def trust_tier(scenario: Dict[str, Any]) -> str:
    """派生溯源强度 tier。

    判据（从严到松）：
    - gold：`published` 非空 + `ref_papers` ≥ 1 + `references` ≥ 2
    - silver：`published` 非空 OR `ref_papers` ≥ 1
    - bronze：`references` ≥ 1（但没论文）
    - none：三者都无

    设计要点：`published` 字段是文本引用（例："Forrester et al. 2008: min ≈ ..."），
    是「有明确出处」的最强信号；`ref_papers` 是结构化论文引用；`references`
    是最宽松的补充列表。
    """
    published = scenario.get("published") or ""
    ref_papers = scenario.get("ref_papers") or []
    references = scenario.get("references") or []
    has_published = bool(str(published).strip())
    has_papers = len(ref_papers) >= 1
    has_refs = len(references) >= 1
    if has_published and has_papers and len(references) >= 2:
        return "gold"
    if has_published or has_papers:
        return "silver"
    if has_refs:
        return "bronze"
    return "none"


def difficulty_tier(scenario: Dict[str, Any]) -> str:
    """派生难度 tier（启发式，非学术共识）。

    判据：
    - easy：dim ≤ 2 AND ls ≥ 0.1
    - hard：dim > 4 OR ls < 0.05
    - medium：其余

    `ls`（length_scale）越小意味着函数越「尖锐」（相对波动越密），代理
    拟合难度越大。`dim` 大则样本量按指数增长。这是两个正交的复杂度轴。

    缺字段处理：
    - dim 缺失 → 视为 medium（无法判难度，不硬猜）
    - ls 缺失 → 只用 dim 判，忽略 ls 轴
    """
    dim = scenario.get("dim")
    if dim is None:
        return "medium"
    try:
        dim = int(dim)
    except (TypeError, ValueError):
        return "medium"
    ls = scenario.get("ls")
    try:
        ls_f = float(ls) if ls is not None else None
    except (TypeError, ValueError):
        ls_f = None

    if ls_f is None:
        return "easy" if dim <= 2 else ("medium" if dim <= 4 else "hard")

    if ls_f < 0.05 or dim > 4:
        return "hard"
    if dim <= 2 and ls_f >= 0.1:
        return "easy"
    return "medium"


@dataclass(frozen=True)
class ScenarioMeta:
    """查询结果的丰富化视图——原始场景 + 派生 tier 一起返回。"""

    scenario: Dict[str, Any]
    key: str
    discipline: str
    trust: str
    difficulty: str
    dim: Optional[int] = None
    ls: Optional[float] = None
    noise: Optional[float] = None

    def to_dict(self, include_scenario: bool = False) -> Dict[str, Any]:
        out = {
            "key": self.key,
            "discipline": self.discipline,
            "trust": self.trust,
            "difficulty": self.difficulty,
            "dim": self.dim,
            "ls": self.ls,
            "noise": self.noise,
        }
        if include_scenario:
            out["scenario"] = dict(self.scenario)
        return out


def _to_meta(s: Dict[str, Any]) -> ScenarioMeta:
    return ScenarioMeta(
        scenario=s,
        key=str(s.get("key", "")),
        discipline=str(s.get("domain_norm") or s.get("category") or "unknown"),
        trust=trust_tier(s),
        difficulty=difficulty_tier(s),
        dim=int(s["dim"]) if s.get("dim") is not None else None,
        ls=float(s["ls"]) if s.get("ls") is not None else None,
        noise=float(s["noise"]) if s.get("noise") is not None else None,
    )


# ---------------------------------------------------------------------------
# 查询接口
# ---------------------------------------------------------------------------

def _coerce_items(
    registry: Optional[ScenarioRegistry],
) -> List[Dict[str, Any]]:
    if registry is None:
        registry = load_registry()
    if isinstance(registry, ScenarioRegistry):
        return list(registry.items)
    # 容错：允许直接传 list[dict]（测试或脚本可能这样用）
    return list(registry)  # type: ignore[arg-type]


def all_scenarios(
    registry: Optional[ScenarioRegistry] = None,
) -> List[ScenarioMeta]:
    """全部场景的丰富化视图（按 registry 权威顺序）。"""
    return [_to_meta(s) for s in _coerce_items(registry)]


def query_by_discipline(
    discipline: str,
    *,
    min_trust: Optional[str] = None,
    max_difficulty: Optional[str] = None,
    registry: Optional[ScenarioRegistry] = None,
) -> List[ScenarioMeta]:
    """按学科筛场景。

    Parameters
    ----------
    discipline : str
        `domain_norm` 的值。区分大小写；空字符串匹配无学科归属的场景。
    min_trust : str, optional
        溯源强度下限。`gold` 最严，`none` 最松。
        传入时会额外过滤 trust ≥ min_trust 的场景。
    max_difficulty : str, optional
        难度上限。`easy` 最松，`hard` 最严。
        传入时会额外过滤 difficulty ≤ max_difficulty 的场景。
    registry : ScenarioRegistry, optional
        复用已加载的 registry，避免重复装配。
    """
    items = _coerce_items(registry)
    trust_rank = {t: i for i, t in enumerate(TRUST_TIERS)}  # gold=0 最严
    diff_rank = {d: i for i, d in enumerate(DIFFICULTY_TIERS)}  # easy=0 最松
    min_t_rank = trust_rank.get(min_trust) if min_trust else None
    max_d_rank = diff_rank.get(max_difficulty) if max_difficulty else None

    out: List[ScenarioMeta] = []
    for s in items:
        m = _to_meta(s)
        if m.discipline != discipline:
            continue
        if min_t_rank is not None and trust_rank.get(m.trust, 99) > min_t_rank:
            continue
        if max_d_rank is not None and diff_rank.get(m.difficulty, 99) > max_d_rank:
            continue
        out.append(m)
    return out


def query_by_trust(
    tier: str = "gold",
    *,
    discipline: Optional[str] = None,
    registry: Optional[ScenarioRegistry] = None,
) -> List[ScenarioMeta]:
    """按溯源强度筛场景。

    tier : str
        允许的 tier 之一：`gold` / `silver` / `bronze` / `none`。
    discipline : str, optional
        如果同时限定学科，取交集。
    """
    if tier not in TRUST_TIERS:
        raise ValueError(f"tier must be one of {TRUST_TIERS}, got {tier!r}")
    items = _coerce_items(registry)
    out: List[ScenarioMeta] = []
    for s in items:
        m = _to_meta(s)
        if m.trust != tier:
            continue
        if discipline is not None and m.discipline != discipline:
            continue
        out.append(m)
    return out


def query_by_difficulty(
    tier: str = "easy",
    *,
    discipline: Optional[str] = None,
    min_trust: Optional[str] = None,
    registry: Optional[ScenarioRegistry] = None,
) -> List[ScenarioMeta]:
    """按难度筛场景。

    tier : str
        允许的 tier 之一：`easy` / `medium` / `hard`。
    discipline : str, optional
    min_trust : str, optional
        额外的溯源强度下限（默认不启用）。
    """
    if tier not in DIFFICULTY_TIERS:
        raise ValueError(f"tier must be one of {DIFFICULTY_TIERS}, got {tier!r}")
    items = _coerce_items(registry)
    trust_rank = {t: i for i, t in enumerate(TRUST_TIERS)}
    min_t_rank = trust_rank.get(min_trust) if min_trust else None
    out: List[ScenarioMeta] = []
    for s in items:
        m = _to_meta(s)
        if m.difficulty != tier:
            continue
        if discipline is not None and m.discipline != discipline:
            continue
        if min_t_rank is not None and trust_rank.get(m.trust, 99) > min_t_rank:
            continue
        out.append(m)
    return out


# ---------------------------------------------------------------------------
# 统计视图
# ---------------------------------------------------------------------------

def discipline_index(
    registry: Optional[ScenarioRegistry] = None,
) -> Dict[str, Dict[str, Any]]:
    """学科 → {total, trust 分布, difficulty 分布, keys 前 5} 的索引表。

    服务于 Agent 端的「我有哪些学科可查、每个学科的强度分布如何」的问题。
    """
    items = _coerce_items(registry)
    out: Dict[str, Dict[str, Any]] = {}
    for s in items:
        m = _to_meta(s)
        entry = out.setdefault(
            m.discipline,
            {
                "total": 0,
                "trust": {t: 0 for t in TRUST_TIERS},
                "difficulty": {d: 0 for d in DIFFICULTY_TIERS},
                "keys": [],
            },
        )
        entry["total"] += 1
        entry["trust"][m.trust] += 1
        entry["difficulty"][m.difficulty] += 1
        if len(entry["keys"]) < 5:
            entry["keys"].append(m.key)
    return out


def scenario_summary(
    registry: Optional[ScenarioRegistry] = None,
) -> Dict[str, Any]:
    """整个 registry 的顶层汇总——供 llms.txt / 报告 header 复用。"""
    items = _coerce_items(registry)
    trust_dist = {t: 0 for t in TRUST_TIERS}
    diff_dist = {d: 0 for d in DIFFICULTY_TIERS}
    dims: List[int] = []
    lss: List[float] = []
    for s in items:
        m = _to_meta(s)
        trust_dist[m.trust] += 1
        diff_dist[m.difficulty] += 1
        if m.dim is not None:
            dims.append(m.dim)
        if m.ls is not None:
            lss.append(m.ls)
    return {
        "total": len(items),
        "disciplines": len({s.get("domain_norm") or s.get("category") or "unknown" for s in items}),
        "trust": trust_dist,
        "difficulty": diff_dist,
        "dim": {
            "min": min(dims) if dims else None,
            "max": max(dims) if dims else None,
            "mean": round(sum(dims) / len(dims), 2) if dims else None,
        },
        "ls": {
            "min": min(lss) if lss else None,
            "max": max(lss) if lss else None,
            "mean": round(sum(lss) / len(lss), 4) if lss else None,
        },
    }


__all__ = [
    # tier 定义
    "TRUST_TIERS",
    "DIFFICULTY_TIERS",
    "KNOWN_DISCIPLINES",
    # tier 派生
    "trust_tier",
    "difficulty_tier",
    # 元数据视图
    "ScenarioMeta",
    # 查询接口
    "all_scenarios",
    "query_by_discipline",
    "query_by_trust",
    "query_by_difficulty",
    # 统计
    "discipline_index",
    "scenario_summary",
]
