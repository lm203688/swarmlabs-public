#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Scenario trust labels —— 结果页「验证类型 / 适用域 / 禁止场景」的单一真源。

为什么单独成模块
----------------
每个 V&V 报告的卡片在报告库前端展示。用户点开前的第一眼决定「这份报告
值不值得下钻」：能不能拿去做真实决策？拿去做的时候，什么情况下不该用？
这两个问题的答案不能靠读者从"R²=0.998、coverage=0.95"的抽象数字里自己
推导——那是把信任成本转嫁给读者。所以必须**在生成报告时就写死标签**：

- ``validation_type`` —— 这份报告的判定是**对什么**算出来的：
    * ``analytic_closed_form``    —— 场景自带闭式解/解析解（可直接对值）
    * ``published_benchmark``     —— 场景对照已发表基准（有 DOI/年份）
    * ``wet_lab_anchored``        —— 场景与真实湿实验文献锚点一致
    * ``synthetic_surrogate``     —— 纯合成代理（Branin/Hartmann 等），
                                     只做优化算法压测
- ``applicable_domain`` —— 这份结论**在什么场景**下可迁移
- ``forbidden_use``     —— **哪些场景下不要用**（这一条比前两条更值钱）

判定顺序（首个命中即定）：
    1) key 以 ``surrogate_`` 或 ``pde_`` 开头 → synthetic surrogate
       （这些是给优化算法做基准的，天生就不是生物/化学模型）
    2) 有湿实验锚点（wet_lab_anchors）→ wet_lab_anchored
       （第二证据链在位，最贵也最硬）
    3) ``published`` 字段含公式符号（=、f(、y(、x∈、exp、log、sin、cos 等）
       → analytic_closed_form
    4) ``references`` 里含年份/DOI → published_benchmark
    5) 兜底 → synthetic_surrogate（保守判，宁可低报不谎报）

失败哲学（fail-closed 精神）
---------------------------
拿不准的场景一律归为 ``synthetic_surrogate``，绝不硬说"published benchmark"。
这不是给读者看的花架子：这个字段直接进 index.json、进 gallery 卡片、
也供 HTTP 前端过滤，属于**产品承诺的一部分**——写错了就是花架子。
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

# ---- 分类常量（前端也 import 同一份，避免两套语义）----
VT_ANALYTIC = "analytic_closed_form"
VT_PUBLISHED = "published_benchmark"
VT_WET_LAB = "wet_lab_anchored"
VT_SYNTHETIC = "synthetic_surrogate"

VALIDATION_TYPES = (VT_ANALYTIC, VT_PUBLISHED, VT_WET_LAB, VT_SYNTHETIC)

#: 每个 validation_type 的显示标签（中英对照，前端可 i18n）
VT_LABELS: Dict[str, Dict[str, str]] = {
    VT_ANALYTIC: {
        "zh": "闭式解析解",
        "en": "analytic closed-form",
        "desc_zh": "场景自带闭式解，预测可直接对值",
        "desc_en": "Closed-form solution is available; predictions can be compared directly",
    },
    VT_PUBLISHED: {
        "zh": "发表基准",
        "en": "published benchmark",
        "desc_zh": "对照已发表文献/基准数据集（含 DOI 或年份）",
        "desc_en": "Compared against a published benchmark dataset",
    },
    VT_WET_LAB: {
        "zh": "湿实验锚定",
        "en": "wet-lab anchored",
        "desc_zh": "参数落在真实湿实验文献区间内（第二证据链在位）",
        "desc_en": "Parameters fall inside a real wet-lab literature range",
    },
    VT_SYNTHETIC: {
        "zh": "合成代理基准",
        "en": "synthetic surrogate",
        "desc_zh": "无真实生物/化学含义，仅用于优化算法压测",
        "desc_en": "No real biological/chemical meaning; for optimization benchmarks only",
    },
}

# ---- 场景分类映射（按 key 前缀 + 语义判定）----

#: key 前缀 → 天然属于 synthetic surrogate 的场景（无真实含义）
SYNTHETIC_KEY_PREFIXES = ("surrogate_", "pde_")

#: published 字段里出现以下符号 → 场景自带闭式解
ANALYTIC_SYMBOL_RE = re.compile(
    r"(?:=|f\(|y\(|u\(|\bx\s*=|K\s*=|θ\s*=|μ\s*=|"
    r"\b(?:log|exp|sin|cos|tanh|sigmoid)\(|\d+\s*(?:e|×)\s*[0-9]+)",
    re.I,
)

#: references 里出现年份（1900–2099 四位） → 有发表基准
REF_YEAR_RE = re.compile(r"(?:\b|[\s\(/,])(?:19|20)\d{2}(?:\b|[\s\)/,:])")

#: references 里出现 DOI
REF_DOI_RE = re.compile(r"10\.\d{4,}/[^\s\)]+", re.I)


# ---------------------------------------------------------------------------
# 域 → 适用场景 & 禁止场景映射
# ---------------------------------------------------------------------------

#: 每个「域类」的适用域与禁止域。按 key 前缀匹配（子串）；首个命中即定。
_DOMAIN_RULES: List[Dict] = [
    # --- synthetic surrogates ---
    {
        "match": ("surrogate_",),
        "applicable_zh": "贝叶斯优化 / 高斯过程代理的算法压测",
        "applicable_en": "Bayesian optimization / GP surrogate algorithm benchmarks",
        "forbidden_zh": [
            "不能用于任何真实工程或科研决策",
            "不能与实验测量值对比",
            "不能用于模型对现实世界的可信度论证",
        ],
        "forbidden_en": [
            "Not for any real engineering or scientific decision",
            "Not for comparison against experimental measurements",
            "Not for credence claims about the real world",
        ],
    },
    {
        "match": ("pde_",),
        "applicable_zh": "PDE 数值解算法压测、物理信息神经网络训练基线",
        "applicable_en": "PDE numerical-solver benchmarks, PINN training baselines",
        "forbidden_zh": [
            "不能直接迁移到真实物理实验",
            "不能用它论证 PINN 在工业问题上的表现",
        ],
        "forbidden_en": [
            "Not directly transferable to real physical experiments",
            "Not evidence for PINN performance on industrial problems",
        ],
    },
    # --- LLM reasoning ---
    {
        "match": ("llm_",),
        "applicable_zh": "LLM 科研推理链的评分 / 一致性检测",
        "applicable_en": "LLM reasoning-chain scoring and consistency checks",
        "forbidden_zh": [
            "不能用它证明 LLM 的科学推理正确",
            "不能用于医疗或法律结论",
        ],
        "forbidden_en": [
            "Not evidence that LLM scientific reasoning is correct",
            "Not for medical or legal conclusions",
        ],
    },
    # --- micro* (52 微生物场景) ---
    {
        "match": ("micro_", "microbio_"),
        "applicable_zh": "微生物生长动力学参数校准、发酵工艺参数扫描的定性排序",
        "applicable_en": "Microbial growth kinetics parameter calibration; fermentation "
                         "parameter ranking (qualitative)",
        "forbidden_zh": [
            "不能直接指导真实发酵生产（菌株/菌龄/培养基批次差异远大于模型精度）",
            "不能替代湿实验确定灭菌/接种参数",
            "不能用于受监管的制剂或药物工艺决策",
            "外推至训练区间之外需按 mirror-run 重验",
        ],
        "forbidden_en": [
            "Not for direct real-fermentation production guidance (strain/age/media "
            "batch variability >> model precision)",
            "Not a substitute for wet-lab sterilization / inoculation protocols",
            "Not for regulated pharmaceutical process decisions",
            "Extrapolation beyond training bounds requires mirror-run revalidation",
        ],
    },
    # --- chem ---
    {
        "match": ("chem_",),
        "applicable_zh": "吸附等温线/反应转化的参数校准、化学工程初步设计",
        "applicable_en": "Adsorption isotherm / reactor conversion parameter "
                         "calibration, preliminary process design",
        "forbidden_zh": [
            "不能用于高危化学工艺的自主决策",
            "不能替代反应器热力学安全评估",
        ],
        "forbidden_en": [
            "Not for autonomous decisions on hazardous chemical processes",
            "Not a substitute for reactor thermodynamic safety assessment",
        ],
    },
    # --- bio_logistic ---
    {
        "match": ("bio_logistic",),
        "applicable_zh": "种群动态 / 生长动力学的定性分析与参数拟合",
        "applicable_en": "Population dynamics qualitative analysis and parameter fitting",
        "forbidden_zh": [
            "不能用于流行病学预测（缺少代际延迟与接触结构）",
            "不能用于真实种群管理决策",
        ],
        "forbidden_en": [
            "Not for epidemiological prediction (no lag or contact structure)",
            "Not for real population management decisions",
        ],
    },
]


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def _match_prefix(key: str, prefixes) -> bool:
    for p in prefixes:
        if key.startswith(p) or ("_" + p.lstrip("_")) in ("_" + key) or \
           key.replace("_", "").startswith(p.replace("_", "")):
            return True
    return False


def _match_any_of_key(key: str, needles) -> bool:
    for n in needles:
        if n in key:
            return True
    return False


def classify_validation_type(
    key: str,
    published: str = "",
    references: Optional[List[str]] = None,
    wet_lab_grade: Optional[str] = None,
) -> str:
    """给场景打验证类型标签。首个命中规则即返回。

    判定优先级（严格顺序，首个命中即定）：
        1) key 前缀 → synthetic surrogate（纯优化基准，天生无真实含义）
        2) 湿实验锚定（第二证据链在位）—— 最贵最硬
        3) 发表基准（references 含 DOI 或 4 位年份）
        4) 闭式解析解（published 含公式符号）
        5) 兜底 → synthetic surrogate（保守判，绝不谎报）
    """
    # 1) synthetic surrogates —— key 前缀判定
    if any(key.startswith(p) for p in SYNTHETIC_KEY_PREFIXES):
        return VT_SYNTHETIC

    # 2) wet-lab anchored —— 第二证据链在位
    if wet_lab_grade in ("AGREE", "NEAR"):
        return VT_WET_LAB

    # 3) 发表基准 —— references 含年份或 DOI
    if references:
        for ref in references:
            if REF_DOI_RE.search(ref) or REF_YEAR_RE.search(ref):
                return VT_PUBLISHED

    # 4) 闭式解析解
    if published and ANALYTIC_SYMBOL_RE.search(published):
        return VT_ANALYTIC

    # 5) 兜底：保守归为 synthetic
    return VT_SYNTHETIC


def applicable_and_forbidden(key: str) -> Dict[str, List[str]]:
    """给场景打适用域与禁止场景标签。"""
    for rule in _DOMAIN_RULES:
        if _match_any_of_key(key, rule["match"]):
            return {
                "applicable_domain": {
                    "zh": rule["applicable_zh"],
                    "en": rule["applicable_en"],
                },
                "forbidden_use": {
                    "zh": list(rule["forbidden_zh"]),
                    "en": list(rule["forbidden_en"]),
                },
            }
    # 兜底
    return {
        "applicable_domain": {
            "zh": "定性探索 / 参数敏感性分析",
            "en": "Qualitative exploration / parameter sensitivity",
        },
        "forbidden_use": {
            "zh": [
                "不能用于受监管决策",
                "外推至训练区间之外需按 mirror-run 重验",
            ],
            "en": [
                "Not for regulated decisions",
                "Extrapolation beyond training bounds requires mirror-run revalidation",
            ],
        },
    }


def labels_for(
    key: str,
    published: str = "",
    references: Optional[List[str]] = None,
    wet_lab_grade: Optional[str] = None,
) -> dict:
    """场景标签的**单一入口**：给一份报告生成器/账本生成器/前端过滤共用。

    返回一个字典，可直接并入 index.json 的 scenario entry。
    """
    vt = classify_validation_type(key, published, references, wet_lab_grade)
    af = applicable_and_forbidden(key)
    return {
        "validation_type": vt,
        "validation_type_label": dict(VT_LABELS[vt]),
        "applicable_domain": af["applicable_domain"],
        "forbidden_use": af["forbidden_use"],
    }


def wet_lab_grade_for_key(wet_lab_doc: Optional[dict], key: str) -> Optional[str]:
    """从 wet_lab_anchors.json 里取某场景的锚定等级。不存在返回 None。"""
    if not wet_lab_doc:
        return None
    for s in wet_lab_doc.get("scenarios") or []:
        if s.get("scenario_key") == key:
            return s.get("grade")
    return None


if __name__ == "__main__":  # pragma: no cover
    # 快速自检：跑通全部 62 场景并统计分布
    import json
    import os
    import sys

    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    idx_path = os.path.join(ROOT, "frontend_fixed", "reports", "index.json")
    wet_path = os.path.join(ROOT, "frontend_fixed", "reports", "wet_lab_anchors.json")
    idx = json.load(open(idx_path, encoding="utf-8"))
    try:
        wet = json.load(open(wet_path, encoding="utf-8"))
    except Exception:
        wet = None

    from collections import Counter
    c = Counter()
    for s in idx["scenarios"]:
        g = wet_lab_grade_for_key(wet, s["key"])
        vt = classify_validation_type(s["key"], s.get("published", ""),
                                       s.get("references"), g)
        c[vt] += 1
    print(f"total scenarios: {len(idx['scenarios'])}")
    for k, v in c.most_common():
        print(f"  {k:24s} {v}")
    sys.exit(0)
