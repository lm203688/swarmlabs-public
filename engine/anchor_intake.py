#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""锚定数据硬化层 —— 「防垃圾进」的可执行形式。

为什么必须有这一层
------------------
`docs/feasibility-virtual-lab-2026-09-21.md` §6 把**垃圾进风险**列为第一号
诚实风险：

    抓错的参照数据会产出「自信的错误裁决」，这会一击摧毁「可信验证层」定位。

一个"验证服务"如果自己的参照数据没被筛过，它的可信度就建立在自己没验证的
东西上——这是自指坍塌。所以参照数据在进入 corroborate 阶段前必须过一道
**机械可查**的筛子，且**永远不自动采纳**。

三层筛选（顺序即判据，前层不过不看后层）
----------------------------------------
  1. **可核验性**：引用必须含年份或 DOI。一段读起来像文献的散文不是来源。
  2. **量纲一致性**：候选的单位必须与目标量量纲兼容，且能换算到基准单位。
     单位对不上（如把 degC 当成 g/L）是"自信地错"的典型来源。
  3. **人工确认**：机械检查全过也**不等于**可采用——必须有具名确认人
     (`confirmed_by`)，否则最多 `NEEDS_HUMAN`。

判定只有三档：`ACCEPTED` / `NEEDS_HUMAN` / `REJECTED`，且 `ACCEPTED`
的必要条件是"机械全过 **且** 有人具名确认"。**不存在自动 ACCEPT 路径**。

职责边界（不越界）
------------------
本模块**不判断科学对错**，只判断"这条参照数据能不能被当作锚定 chip 使用"。
它不修改场景参数、不重算裁决——那是引擎的事。

依赖：仅标准库（含同目录 `vlab_stdlib.py`，按路径加载）。
"""

from __future__ import annotations

import importlib.util
import os
import re
from typing import Any, Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))


def _stdlib():
    spec = importlib.util.spec_from_file_location(
        "_vlab_stdlib", os.path.join(_HERE, "vlab_stdlib.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


S = _stdlib()

_SCHEMA = "swarmlabs-vlab-anchor-screening/1.0"

_YEAR = re.compile(r"\b(19|20)\d{2}\b")
_DOI = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+")

ACCEPTED = "ACCEPTED"
NEEDS_HUMAN = "NEEDS_HUMAN"
REJECTED = "REJECTED"

# ------------------------------------------------------------------ 单位表
#
# 每个量纲有一个基准单位；同量纲内可线性换算。跨量纲一律拒绝——
# 这是"单位对不上"这类错误的机器化拦截点。
#   (dimension, factor_to_base, offset)
# offset 非 0 的（温度）只允许同量纲内显式换算，且必须写明，因此 K 与 degC
# 归到不同维度，避免静默的仿射换算。

_UNITS: Dict[str, Tuple[str, float, float]] = {
    # 质量浓度 -> 基准 g/L
    "g/l": ("mass_conc", 1.0, 0.0),
    "mg/l": ("mass_conc", 1e-3, 0.0),
    "ug/l": ("mass_conc", 1e-6, 0.0),
    "µg/l": ("mass_conc", 1e-6, 0.0),
    "ng/l": ("mass_conc", 1e-9, 0.0),
    "g/m3": ("mass_conc", 1e-3, 0.0),
    "g/m^3": ("mass_conc", 1e-3, 0.0),
    "kg/m3": ("mass_conc", 1.0, 0.0),
    "kg/m^3": ("mass_conc", 1.0, 0.0),
    "mg/ml": ("mass_conc", 1.0, 0.0),
    "g/ml": ("mass_conc", 1e3, 0.0),
    # 摩尔浓度 -> 基准 mol/L
    "mol/l": ("molar_conc", 1.0, 0.0),
    # mm/nm 故意不注册：歧义（更像毫米/纳米），必须写全 "mmol/l"/"umol/l"
    "mmol/l": ("molar_conc", 1e-3, 0.0),
    "umol/l": ("molar_conc", 1e-6, 0.0),
    "µmol/l": ("molar_conc", 1e-6, 0.0),
    # 时间 -> 基准 s
    "s": ("time", 1.0, 0.0),
    "sec": ("time", 1.0, 0.0),
    "min": ("time", 60.0, 0.0),
    "h": ("time", 3600.0, 0.0),
    "hr": ("time", 3600.0, 0.0),
    "hour": ("time", 3600.0, 0.0),
    "d": ("time", 86400.0, 0.0),
    "day": ("time", 86400.0, 0.0),
    # 速率 -> 基准 1/h
    "1/h": ("rate", 1.0, 0.0),
    "h^-1": ("rate", 1.0, 0.0),
    "h-1": ("rate", 1.0, 0.0),
    "1/s": ("rate", 3600.0, 0.0),
    "s^-1": ("rate", 3600.0, 0.0),
    "s-1": ("rate", 3600.0, 0.0),
    "1/min": ("rate", 60.0, 0.0),
    "1/d": ("rate", 1.0 / 24.0, 0.0),
    # 无量纲
    "-": ("dimensionless", 1.0, 0.0),
    "": ("dimensionless", 1.0, 0.0),
    "1": ("dimensionless", 1.0, 0.0),
    "%": ("dimensionless", 0.01, 0.0),
    "pct": ("dimensionless", 0.01, 0.0),
    # 温度（摄氏度）——K 单列一维，防静默仿射换算
    "degc": ("temperature_C", 1.0, 0.0),
    "c": ("temperature_C", 1.0, 0.0),
    "celsius": ("temperature_C", 1.0, 0.0),
    "°c": ("temperature_C", 1.0, 0.0),
    "k": ("temperature_K", 1.0, 0.0),
    "kelvin": ("temperature_K", 1.0, 0.0),
    # 压强 -> 基准 bar
    "bar": ("pressure", 1.0, 0.0),
    "atm": ("pressure", 1.01325, 0.0),
    "pa": ("pressure", 1e-5, 0.0),
    "kpa": ("pressure", 1e-2, 0.0),
    "mpa": ("pressure", 10.0, 0.0),
    "mbar": ("pressure", 1e-3, 0.0),
    # 长度 -> 基准 m（裸 "m" 按米解；摩尔浓度必须写 "mol/l"，不猜）
    "m": ("length", 1.0, 0.0),
    "cm": ("length", 1e-2, 0.0),
    "mm": ("length", 1e-3, 0.0),
    "um": ("length", 1e-6, 0.0),
    "µm": ("length", 1e-6, 0.0),
    "nm": ("length", 1e-9, 0.0),
    # 质量 -> 基准 g
    "g": ("mass", 1.0, 0.0),
    "mg": ("mass", 1e-3, 0.0),
    "ug": ("mass", 1e-6, 0.0),
    "kg": ("mass", 1e3, 0.0),
    # 体积 -> 基准 L
    "l": ("volume", 1.0, 0.0),
    "ml": ("volume", 1e-3, 0.0),
    "ul": ("volume", 1e-6, 0.0),
}

#: 单位别名（去掉空白/大小写差异后的二次归一）
_ALIASES = {
    "hr^-1": "h^-1", "1/hr": "1/h", "hours": "h", "hours^-1": "h^-1",
    "deg_c": "degc", "°c": "degc",
    "molarity": "mol/l", "h-1": "h^-1",
}


def normalize_unit_token(unit: str) -> str:
    u = (unit or "").strip().lower()
    u = u.replace(" ", "").replace("·", "*")
    u = u.replace("**", "^")
    return _ALIASES.get(u, u) or u


def parse_unit(unit: str) -> Optional[Tuple[str, float, float]]:
    """返回 (dimension, factor_to_base, offset)；无法识别返回 None。"""
    tok = normalize_unit_token(unit)
    # 处理上标减号变体 h⁻¹
    tok = tok.replace("⁻¹", "^-1").replace("⁻", "^-")
    if tok in _UNITS:
        return _UNITS[tok]
    return None


class UnitMismatch(ValueError):
    pass


def normalize_value(value: float, unit: str, target_unit: str) -> float:
    """把 value 从 unit 换算到 target_unit。跨量纲抛 UnitMismatch。"""
    a = parse_unit(unit)
    b = parse_unit(target_unit)
    if a is None:
        raise UnitMismatch(f"unknown unit: {unit!r}")
    if b is None:
        raise UnitMismatch(f"unknown target unit: {target_unit!r}")
    if a[0] != b[0]:
        raise UnitMismatch(
            f"dimension mismatch: {unit!r} is {a[0]}, {target_unit!r} is {b[0]}"
        )
    base = float(value) * a[1] + a[2]
    return (base - b[2]) / b[1]


# ------------------------------------------------------------ 候选筛选

class ReferenceCandidate:
    """一条待筛选的参照数据。

    `confirmed_by` 是**人**的名字/标识：机械检查通过不等于可采用。

    `raw` 保存提交方的原始字典。筛选报告必须能**回显原始观测点**，否则客户
    看到的是"你有一条被拒了"，却不知道是哪一条——报告会变成噪音。
    """

    def __init__(self, label: str = "", value: Optional[float] = None,
                 unit: str = "", citation: str = "", confirmed_by: str = "",
                 conditions: str = "", source_kind: str = "external",
                 raw: Optional[dict] = None):
        self.label = label
        self.value = value
        self.unit = unit
        self.citation = citation
        self.confirmed_by = confirmed_by
        self.conditions = conditions
        self.source_kind = source_kind
        self.raw = dict(raw or {})

    def to_dict(self) -> dict:
        # 先铺原始提交内容（含 x / y 等），再用规范化字段覆盖，
        # 保证筛选报告既不丢原始点、又有一致的字段名。
        out = dict(self.raw)
        out.update({
            "label": self.label, "value": self.value, "unit": self.unit,
            "citation": self.citation, "confirmed_by": self.confirmed_by,
            "conditions": self.conditions, "source_kind": self.source_kind,
        })
        return out

    @staticmethod
    def from_dict(d: dict) -> "ReferenceCandidate":
        """从工作单的 corroboration point 读入。

        契约侧把观测值叫 `y`（与设计变量 `x` 对称），锚定层把它叫 `value`
        ——这里**必须显式桥接**，否则每一条真实提交都会因 `value is None`
        被判 bad_value 而拒收（一种"看起来在工作、实际全拒"的静默失效）。
        """
        d = d or {}
        val = d.get("value", d.get("y"))
        return ReferenceCandidate(
            label=d.get("label", "") or "",
            value=val,
            unit=d.get("unit", "") or "",
            citation=d.get("citation", "") or "",
            confirmed_by=d.get("confirmed_by", "") or "",
            conditions=d.get("conditions", "") or "",
            source_kind=d.get("source_kind", "external") or "external",
            raw=d,
        )


def _citation_checkable(citation: str) -> bool:
    return bool(_YEAR.search(citation or "") or _DOI.search(citation or ""))


def screen_candidate(cand: ReferenceCandidate,
                     spec_unit: str,
                     plausible_range: Optional[Tuple[float, float]] = None
                     ) -> Dict[str, Any]:
    """筛一条候选。返回 {verdict, reasons, normalized_value, checks}。

    判据顺序（前层不过即 REJECTED，不看后层）：
      1. 数值有限  → bad_value
      2. 引用可核验（含年份/DOI）→ unverifiable_citation
      3. 单位可解析 → unknown_unit
      4. 与 spec_unit 同量纲 → dimension_mismatch
    以上全过之后：
      5. 超出声明的合理区间 → NEEDS_HUMAN（out_of_plausible_range）
      6. 无具名确认人 → NEEDS_HUMAN（awaiting_human_confirmation）
      7. 全过 → ACCEPTED
    """
    reasons: List[str] = []
    checks: Dict[str, Any] = {}

    # 1) 数值
    if not S.is_finite(cand.value):
        return {"verdict": REJECTED, "reasons": ["bad_value"],
                "normalized_value": None,
                "checks": {"value_finite": False}}

    # 2) 引用
    ok_cite = _citation_checkable(cand.citation)
    checks["citation_checkable"] = ok_cite
    if not ok_cite:
        return {"verdict": REJECTED, "reasons": ["unverifiable_citation"],
                "normalized_value": None, "checks": checks}

    # 3) 单位可解析
    pu = parse_unit(cand.unit)
    checks["unit_parsed"] = pu[0] if pu else None
    if pu is None:
        return {"verdict": REJECTED, "reasons": ["unknown_unit"],
                "normalized_value": None, "checks": checks}

    # 4) 量纲一致
    ptarget = parse_unit(spec_unit) if spec_unit else None
    if ptarget is None:
        # spec 没给单位 → 无从做量纲检查，只能交人工（fail-closed）
        checks["dimension_ok"] = False
        return {"verdict": NEEDS_HUMAN,
                "reasons": ["spec_unit_unknown_cannot_check_dimension"],
                "normalized_value": None, "checks": checks}
    if pu[0] != ptarget[0]:
        checks["dimension_ok"] = False
        return {"verdict": REJECTED, "reasons": ["dimension_mismatch"],
                "normalized_value": None,
                "checks": {**checks, "expected_dimension": ptarget[0]}}
    checks["dimension_ok"] = True

    normalized = normalize_value(float(cand.value), cand.unit, spec_unit)
    checks["normalized_value"] = normalized

    # 5) 合理区间
    if plausible_range is not None:
        lo, hi = plausible_range
        if not (lo <= normalized <= hi):
            reasons.append("out_of_plausible_range")
            checks["plausible_range"] = [lo, hi]

    # 6) 人工确认（机械全过也不自动采纳）
    if not S.nonempty_str(cand.confirmed_by):
        reasons.append("awaiting_human_confirmation")

    if reasons:
        return {"verdict": NEEDS_HUMAN, "reasons": reasons,
                "normalized_value": normalized, "checks": checks}
    return {"verdict": ACCEPTED, "reasons": [],
            "normalized_value": normalized, "checks": checks}


def screen_batch(candidates: List[ReferenceCandidate], spec_unit: str,
                 plausible_range: Optional[Tuple[float, float]] = None) -> Dict:
    """批量筛选 + 汇总。ACCEPTED 的条目带 `normalized_value`，可直接作锚定 chip。"""
    rows = []
    counts = {ACCEPTED: 0, NEEDS_HUMAN: 0, REJECTED: 0}
    for c in candidates:
        r = screen_candidate(c, spec_unit, plausible_range)
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
        rows.append({**c.to_dict(), "verdict": r["verdict"],
                     "reasons": r["reasons"],
                     "normalized_value": r["normalized_value"],
                     "checks": r["checks"]})
    return {
        "schema": _SCHEMA,
        "spec_unit": spec_unit,
        "plausible_range": list(plausible_range) if plausible_range else None,
        "counts": counts,
        "n": len(rows),
        "accepted": [r for r in rows if r["verdict"] == ACCEPTED],
        "needs_human": [r for r in rows if r["verdict"] == NEEDS_HUMAN],
        "rejected": [r for r in rows if r["verdict"] == REJECTED],
        "rows": rows,
        "policy": ("ACCEPTED requires: finite value + citation with year/DOI + "
                   "unit dimensionally compatible with the spec + within the "
                   "declared plausible range + a named human confirmer. "
                   "There is no automatic-accept path."),
    }


__all__ = [
    "ACCEPTED", "NEEDS_HUMAN", "REJECTED",
    "ReferenceCandidate", "screen_candidate", "screen_batch",
    "parse_unit", "normalize_unit_token", "normalize_value", "UnitMismatch",
]
