"""
实验场景模块
包含 Lotka-Volterra, Hodgkin-Huxley, Black-Scholes 模型

**★ 信任标签单一真源**（v2026-10-14 起）：
    所有对外暴露的场景条目必须调用 `scenario_trust_labels.labels_for(...)`
    生成 ``validation_type`` / ``applicable_domain`` / ``forbidden_use``
    三个字段。禁止各报告生成器各自判定。

    - ``classify_validation_type`` —— 四类：analytic / published / wet_lab / synthetic
    - ``applicable_and_forbidden`` —— 域级适用/禁止清单
    - ``labels_for``              —— 三合一入口，供前端与 index.json 共用

    失败哲学：拿不准一律归为 ``synthetic_surrogate``，宁低报不谎报。
    详细判定规则见 `engine/scenario_trust_labels.py`。
"""

from scenarios.lotka_volterra import LotkaVolterraModel, LVParameters
from scenarios.hodgkin_huxley import HodgkinHuxleyModel, HHParameters
from scenarios.black_scholes import BlackScholesModel, BSParameters
from scenarios.scenario_manager import ScenarioManager, ScenarioConfig

# ★ 信任标签单一真源（从 engine 层复用，避免语义分叉）
from engine.scenario_trust_labels import (
    classify_validation_type,
    applicable_and_forbidden,
    labels_for,
    wet_lab_grade_for_key,
    VALIDATION_TYPES,
    VT_LABELS,
    VT_ANALYTIC,
    VT_PUBLISHED,
    VT_WET_LAB,
    VT_SYNTHETIC,
)

__all__ = [
    "LotkaVolterraModel",
    "LVParameters",
    "HodgkinHuxleyModel",
    "HHParameters",
    "BlackScholesModel",
    "BSParameters",
    "ScenarioManager",
    "ScenarioConfig",
    # --- 信任标签 ---
    "classify_validation_type",
    "applicable_and_forbidden",
    "labels_for",
    "wet_lab_grade_for_key",
    "VALIDATION_TYPES",
    "VT_LABELS",
    "VT_ANALYTIC",
    "VT_PUBLISHED",
    "VT_WET_LAB",
    "VT_SYNTHETIC",
]
