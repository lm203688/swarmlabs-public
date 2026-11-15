#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""合成锚点预验证器 —— 把场景自带的 `published` 参数抽成「预验证锚点」。

为什么需要这一层
----------------
`ablation_plan()` 报告 79 个 PASS 场景**全部**缺 anchor，覆盖率被拉到 0.62。
但查下来发现这个诊断是粗的：

  * 35 个 PASS 场景自带 `published` 字段（文献参数区间，33 个含具体数值）
    —— 它们**不是没有锚点素材**，只是没走过 `wet_lab_anchors` 流水线；
  * 44 个没有 `published` 的（几乎全是 `micro_*` 微生物场景）才是真缺口。

本模块做的事：从账本读 `published`，抽成结构化的「预验证锚点」，用来回答
「该不该投入人工去查文献」这个问题——它是**分诊工具**，不是锚点。

三条不可妥协的纪律（写进代码，不靠约定）
----------------------------------------
1. `anchor_class` 恒为 `"synthetic"`，绝不写 `"literature"` / `"wet_lab"`。
   `scripts/check_public_numbers.py` 只认 `wet_lab_anchors.json` 的 19 个锚点，
   本模块产物**不进入**那份文件，也没有入口可以进入。
2. 每条都带 `not_a_validation` 字段，说清「预验证通过 ≠ 验证通过」。
   预验证只检查量纲与数值自洽（自己和自己比），不接触任何外部真值。
3. 数值区间**只允许**从 `published` 原样抽取，禁止「合理推测」补齐。
   `published` 里没写数值就标 `unquantified: true`，不编一个范围出来。

依赖：仅标准库。账本由 `scripts/build_gate_ledger.py` 生成，本模块只读。
"""

from __future__ import annotations

import importlib.util
import json
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

SCHEMA = "swarmlabs-synthetic-anchor/1.0"

# `name=value unit` 形态：K=100 h⁻¹、μmax=0.81、r=0.6、Ea=30 kJ/mol
_KV = re.compile(
    r"\b([A-Za-z][A-Za-z0-9_]{0,12})"   # 参数名
    r"\s*[=：:]\s*"                       # 赋值符号
    r"(\d+\.?\d*|\.?\d+)"                # 数值
    r"\s*([^\s,;)]{0,12})?",             # 单位（可选，容错）
    re.IGNORECASE
)
_YEAR = re.compile(r"\b(19|20)\d{2}\b")
_UNIT_OK = re.compile(
    r"^[\d.]*(\s?h⁻¹|\s?h-1|\s?min⁻¹|\s?min-1|\s?kJ/mol|\s?kcal/mol"
    r"|\s?g/L|\s?g/g|\s?mg/L|\s?mol/L|\s?M|\s?mM|\s?degC|\s?°C"
    r"|\s?s⁻¹|\s?s-1|\s?kPa|\s?mmol/g|percent|%)?$"
)

#: 明确标注——防止这份产物被误当验证通过
_NOT_A_VALIDATION = (
    "SYNTHETIC anchor: extracted verbatim from the scenario's own "
    "`published` field. It self-checks unit/number consistency only. "
    "It is NOT evidence, NOT literature-anchored, and it does NOT enter "
    "frontend_fixed/reports/wet_lab_anchors.json. Pre-validation passing "
    "means the scenario carries quotable parameters — nothing more."
)


def _load_ledger() -> Dict[str, Any]:
    p = os.path.join(S.ROOT, "frontend_fixed", "reports", "gate_ledger.json")
    return S.read_json(p) or {}


def parse_published(text: str) -> Tuple[bool, Dict[str, Any]]:
    """从 `published` 文本抽出结构化参数。不推测、不补齐。

    返回 (是否含数值, 抽取结果)。抽取结果里 `quantified` 只放真的抽到数值的，
    `unquantified` 记没抽到的，`citations` 记年份。
    """
    text = (text or "").strip()
    out: Dict[str, Any] = {
        "raw": text,
        "quantified": [],
        "unquantified": 0,
        "citations": sorted(set(_YEAR.findall(text))),
        "has_year": bool(_YEAR.search(text)),
    }
    if not text:
        out["unquantified"] = 1
        return False, out

    hits = list(_KV.finditer(text))
    for m in hits:
        name, val, unit = m.group(1), m.group(2), (m.group(3) or "").strip()
        try:
            num = float(val)
        except ValueError:
            out["unquantified"] += 1
            continue
        if not _UNIT_OK.match(unit):
            unit = ""
        out["quantified"].append({
            "name": name,
            "value": num,
            "unit": unit,
            "unit_declared": bool(unit),
        })
    if not hits:
        out["unquantified"] = 1
    return bool(out["quantified"]), out


def prevalidate(quantified: List[dict]) -> Dict[str, Any]:
    """预验证：只做机械自洽检查，不接触外部真值。

    检查三项：
      - `unit_declared`  —— 数值有没有量纲（无量纲参数是常见错误来源）
      - `finite_positive` —— 是否有限且非负（负速率/负浓度必然是错的）
      - `no_unit_outlier` —— 单位拼写是否在白名单内（已在解析阶段收口）

    结果分三档：`CLEAN` / `NEEDS_REVIEW` / `REJECTED`。
    """
    if not quantified:
        return {"outcome": "REJECTED", "reason": "no quantified parameter found",
                "flags": []}

    flags: List[str] = []
    for q in quantified:
        n = q["name"]
        v = q["value"]
        if not q["unit_declared"]:
            flags.append(f"{n}: no unit declared")
        if not (v == v) or v in (float("inf"), float("-inf")):
            flags.append(f"{n}: non-finite value")
        if v < 0:
            flags.append(f"{n}: negative value (physically implausible)")

    if any(f.endswith("non-finite value") or "negative value" in f
           for f in flags):
        outcome = "REJECTED"
    elif flags:
        outcome = "NEEDS_REVIEW"
    else:
        outcome = "CLEAN"

    return {
        "outcome": outcome,
        "reason": "; ".join(flags) if flags else (
            "all parameters carry a declared unit and are finite and non-negative"
        ),
        "flags": flags,
    }


def collect(scenarios: Optional[List[str]] = None,
            only_pass: bool = True) -> Dict[str, Any]:
    """扫描账本，为每个带 `published` 的场景生成一条合成锚点。"""
    ledger = _load_ledger()
    rows = ledger.get("scenarios") or {}
    total = 0
    entries: List[dict] = []
    skipped = {"no_published": 0, "non_pass": 0}

    for key, meta in rows.items():
        if scenarios is not None and key not in scenarios:
            continue
        total += 1
        verdict = meta.get("verdict")
        if only_pass and verdict != "PASS":
            skipped["non_pass"] += 1
            continue
        has_num, parsed = parse_published(meta.get("published") or "")
        if not has_num:
            skipped["no_published"] += 1
            continue

        pv = prevalidate(parsed["quantified"])
        entries.append({
            "schema": SCHEMA,
            "scenario": key,
            "anchor_class": "synthetic",
            "domain": meta.get("domain"),
            "verdict": verdict,
            "r2": ((meta.get("uncertainty") or {}).get("r2")),
            "parameters": parsed["quantified"],
            "citations": parsed["citations"],
            "has_year": parsed["has_year"],
            "unquantified": parsed["unquantified"],
            "prevalidation": pv,
            "not_a_validation": _NOT_A_VALIDATION,
        })

    entries.sort(key=lambda e: (e["prevalidation"]["outcome"], e["scenario"]))

    counts = {
        "scanned": total,
        "with_synthetic_anchor": len(entries),
        "skipped_no_published": skipped["no_published"],
        "skipped_non_pass": skipped["non_pass"],
        "prevalidation": {
            "CLEAN": sum(1 for e in entries
                         if e["prevalidation"]["outcome"] == "CLEAN"),
            "NEEDS_REVIEW": sum(1 for e in entries
                                if e["prevalidation"]["outcome"] == "NEEDS_REVIEW"),
            "REJECTED": sum(1 for e in entries
                            if e["prevalidation"]["outcome"] == "REJECTED"),
        },
        "with_citation_year": sum(1 for e in entries if e["has_year"]),
    }
    return {
        "schema": "swarmlabs-synthetic-anchors/1.0",
        "generated_at_utc": S.utc_now(),
        "anchor_class": "synthetic",
        "note": (
            "Pre-validation triage only. `anchor_class=synthetic` never "
            "promotes to literature anchoring; promoting requires the "
            "`anchor_intake` three-layer screening (verifiability → "
            "dimension → named confirmer). This file is deliberately kept "
            "separate from wet_lab_anchors.json."
        ),
        "counts": counts,
        "anchors": entries,
    }


def main() -> int:
    report = collect()
    out = os.path.join(S.ROOT, "data", "synthetic_anchors.json")
    S.write_json(out, report)
    c = report["counts"]
    print(f"scanned                     : {c['scanned']}")
    print(f"with synthetic anchor       : {c['with_synthetic_anchor']}")
    print(f"  CLEAN                     : {c['prevalidation']['CLEAN']}")
    print(f"  NEEDS_REVIEW              : {c['prevalidation']['NEEDS_REVIEW']}")
    print(f"  REJECTED                  : {c['prevalidation']['REJECTED']}")
    print(f"skipped (no published)      : {c['skipped_no_published']}")
    print(f"skipped (non-PASS)          : {c['skipped_non_pass']}")
    print(f"with citation year          : {c['with_citation_year']}")
    print(f"written                     : {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
