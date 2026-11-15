#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/check_data_contracts.py — 三份核心数据产物的字段契约检查（stdlib only）。

为什么这个脚本存在
-------------------
2026-10-16 晚评估报告 §7 · P0-3 指出：`data/trust_surface.json`、
`data/real_anchors.json`、`data/gate_ledger.json` 是产品的**对外数据面**，
但仓库里**没有任何机器可执行的契约**约束它们的字段结构。任何一次构建
脚本改动让字段悄悄消失或类型改变（比如把 counts.PASS 变成 str），
对外 API / README / 前端都会跟着静默漂移，而 CI 仍然全绿。

这正是"生成链自我引用"的又一形态：build 脚本写死字段名，消费方也写死
字段名，两者之间的契约靠人的记忆维持。这个脚本把契约变成**可执行**
的一等公民：任何一份产物少了必需字段、类型不对、或架构反转回归
（`gate_ledger.json:build.source` 又指回展示层），本门禁 exit 1。

检查内容（fail-closed）
----------------------
1. **trust_surface.json**：必需顶层字段 (`generated_at`, `overall`, `rows`)，
   `overall.honesty` 子树必须存在，`rows[].{key, domain}` 契约。
2. **real_anchors.json**：必需顶层字段 (`total_anchors`, `anchors`, `last_updated`)，
   `total_anchors` 必须等于 `len(anchors)`，每条 anchor 必须有 `id`/`scenario`/
   `parameter`；每条 anchor 的 provenance 必须归入 `zenodo` / `paper_chart`
   两档之一，任何一档未知都是 fail-closed 漂移。
3. **gate_ledger.json**：必需顶层字段 (`build`, `counts`, `total`, `provenance`,
   `scenarios`)，`build.source` **必须**是 `scripts/build_gate_ledger.py`（架构
   反转回归检查），`total` 必须等于 `len(scenarios)`，`counts` 之和必须等于 `total`。

用法
----
    python scripts/check_data_contracts.py
    python scripts/check_data_contracts.py --json

退出码
------
    0  全部契约成立
    1  任一文件缺失字段 / 类型错 / 计数不一致 / 架构反转回归

仅标准库。可在 GitHub Actions 直接运行。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple


ROOT = Path(__file__).resolve().parent.parent

TRUST_SURFACE = ROOT / "data" / "trust_surface.json"
REAL_ANCHORS = ROOT / "data" / "real_anchors.json"
GATE_LEDGER = ROOT / "data" / "gate_ledger.json"


# ===========================================================================
# Helpers
# ===========================================================================
def _load(path: Path) -> Dict[str, Any] | None:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _check_type(value: Any, expected: type, path_label: str, issues: List[dict]) -> bool:
    """Fail-closed type check. Returns True if OK, else records an issue."""
    if not isinstance(value, expected):
        issues.append({
            "field": path_label,
            "kind": "TYPE-MISMATCH",
            "expected": expected.__name__,
            "actual": type(value).__name__,
            "value": repr(value)[:200],
        })
        return False
    return True


# ===========================================================================
# 1. trust_surface.json
# ===========================================================================
REQUIRED_TRUST_SURFACE_TOP = {
    "generated_at": str,
    "overall": dict,
    "rows": list,
}


def check_trust_surface() -> List[dict]:
    issues: List[dict] = []
    file_label = "data/trust_surface.json"
    d = _load(TRUST_SURFACE)
    if d is None:
        return [{"file": file_label, "kind": "FILE-UNREADABLE"}]

    for key, expected_type in REQUIRED_TRUST_SURFACE_TOP.items():
        if key not in d:
            issues.append({
                "file": file_label, "kind": "MISSING-FIELD", "field": f"#{key}",
            })
        else:
            _check_type(d[key], expected_type, f"#{key}", issues)

    overall = d.get("overall") or {}
    if isinstance(overall, dict):
        if "honesty" not in overall:
            issues.append({
                "file": file_label, "kind": "MISSING-FIELD",
                "field": "#overall.honesty",
                "note": "honesty subtree is the ONLY source of truth for "
                        "overall.*.honesty — CI honesty-gate depends on it",
            })
        elif not isinstance(overall["honesty"], dict):
            _check_type(overall["honesty"], dict, "#overall.honesty", issues)

    # rows[*] required keys — use fields that actually exist. "key" is the
    # stable identifier; "domain" is the discipline grouping.
    rows = d.get("rows") or []
    required_row_keys = ("key", "domain")
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            issues.append({
                "file": file_label, "kind": "TYPE-MISMATCH",
                "field": f"#rows[{i}]", "expected": "dict",
                "actual": type(row).__name__,
            })
            continue
        for k in required_row_keys:
            if k not in row:
                issues.append({
                    "file": file_label, "kind": "MISSING-FIELD",
                    "field": f"#rows[{i}].{k}",
                })
    return issues


# ===========================================================================
# 2. real_anchors.json
# ===========================================================================
REQUIRED_REAL_ANCHORS_TOP = {
    "total_anchors": int,
    "anchors": list,
    "last_updated": str,
}

REQUIRED_ANCHOR_KEYS = ("id", "scenario", "parameter")

#: Provenance tiers for real_anchors (measured 2026-10-16).
ANCHOR_PROVENANCE_TIERS = ("zenodo", "paper_chart")


def _classify_anchor_provenance(anchor: Dict[str, Any]) -> str:
    """Return 'zenodo' | 'paper_chart' | 'unknown'."""
    prov = anchor.get("provenance") or {}
    if not isinstance(prov, dict):
        return "unknown"
    # Tier 1: Zenodo DOI at the anchor level, plus a resolvable source_url.
    if str(anchor.get("doi", "")).startswith("10.5281/zenodo.") and anchor.get("source_url"):
        return "zenodo"
    # Tier 2: paper_doi or doi inside provenance (paper figure-derived).
    if prov.get("paper_doi") or prov.get("doi"):
        return "paper_chart"
    return "unknown"


def check_real_anchors() -> Tuple[List[dict], Dict[str, int]]:
    """Return (issues, provenance_tiers)."""
    issues: List[dict] = []
    file_label = "data/real_anchors.json"
    d = _load(REAL_ANCHORS)
    if d is None:
        return [{"file": file_label, "kind": "FILE-UNREADABLE"}], {
            "zenodo": 0, "paper_chart": 0, "unknown": 0,
        }

    for key, expected_type in REQUIRED_REAL_ANCHORS_TOP.items():
        if key not in d:
            issues.append({
                "file": file_label, "kind": "MISSING-FIELD", "field": f"#{key}",
            })
        else:
            _check_type(d[key], expected_type, f"#{key}", issues)

    total = d.get("total_anchors")
    anchors = d.get("anchors") or []
    if isinstance(total, int) and total != len(anchors):
        issues.append({
            "file": file_label, "kind": "COUNT-MISMATCH",
            "field": "#total_anchors",
            "expected": len(anchors),
            "actual": total,
            "note": "total_anchors must equal len(anchors); drift means "
                    "either an anchor was silently dropped or added without "
                    "updating the counter",
        })

    provenance_tiers: Dict[str, int] = {t: 0 for t in ANCHOR_PROVENANCE_TIERS}
    provenance_tiers["unknown"] = 0

    for i, a in enumerate(anchors):
        if not isinstance(a, dict):
            issues.append({
                "file": file_label, "kind": "TYPE-MISMATCH",
                "field": f"#anchors[{i}]", "expected": "dict",
                "actual": type(a).__name__,
            })
            continue
        for k in REQUIRED_ANCHOR_KEYS:
            if k not in a:
                issues.append({
                    "file": file_label, "kind": "MISSING-FIELD",
                    "field": f"#anchors[{i}].{k}",
                })
        tier = _classify_anchor_provenance(a)
        provenance_tiers[tier] = provenance_tiers.get(tier, 0) + 1
        if tier == "unknown":
            issues.append({
                "file": file_label, "kind": "PROVENANCE-UNKNOWN",
                "field": f"#anchors[{i}].provenance",
                "note": (
                    "Anchor lacks both a Zenodo DOI at the anchor level "
                    "and a paper_doi / doi inside provenance — cannot "
                    "classify its provenance tier."
                ),
            })

    return issues, provenance_tiers


# ===========================================================================
# 3. gate_ledger.json — including architecture-inversion regression guard
# ===========================================================================
REQUIRED_LEDGER_TOP = {
    "build": dict,
    "counts": dict,
    "total": int,
    "provenance": dict,
    "scenarios": dict,
}

EXPECTED_LEDGER_BUILD_SOURCE = "scripts/build_gate_ledger.py"


def check_gate_ledger() -> List[dict]:
    issues: List[dict] = []
    file_label = "data/gate_ledger.json"
    d = _load(GATE_LEDGER)
    if d is None:
        return [{"file": file_label, "kind": "FILE-UNREADABLE"}]

    for key, expected_type in REQUIRED_LEDGER_TOP.items():
        if key not in d:
            issues.append({
                "file": file_label, "kind": "MISSING-FIELD", "field": f"#{key}",
            })
        else:
            _check_type(d[key], expected_type, f"#{key}", issues)

    total = d.get("total")
    scenarios = d.get("scenarios") or {}
    if isinstance(total, int) and isinstance(scenarios, dict):
        if total != len(scenarios):
            issues.append({
                "file": file_label, "kind": "COUNT-MISMATCH",
                "field": "#total",
                "expected": len(scenarios),
                "actual": total,
            })

    counts = d.get("counts") or {}
    if isinstance(counts, dict) and isinstance(total, int):
        verdict_keys = ("PASS", "MARGINAL", "REFUTED", "ERROR", "UNVALIDATED")
        s = sum(
            counts.get(k, 0) for k in verdict_keys
            if isinstance(counts.get(k, 0), int)
        )
        if s != total:
            issues.append({
                "file": file_label, "kind": "COUNT-SUM-MISMATCH",
                "field": "#counts",
                "expected_sum": total,
                "actual_sum": s,
                "counts": dict(counts),
            })

    # Architecture-inversion regression guard (P0-3 acceptance criterion).
    build = d.get("build") or {}
    if isinstance(build, dict):
        src = build.get("source")
        if src != EXPECTED_LEDGER_BUILD_SOURCE:
            issues.append({
                "file": file_label, "kind": "ARCHITECTURE-INVERSION",
                "field": "#build.source",
                "expected": EXPECTED_LEDGER_BUILD_SOURCE,
                "actual": src,
                "note": (
                    "The data layer (gate_ledger.json) must not declare "
                    "itself as derived from the display layer "
                    "(frontend_fixed/reports/index.json). The single source "
                    "of truth is the build script itself; index.json is a "
                    "sibling artifact of the same build process."
                ),
            })

    return issues


# ===========================================================================
# Main
# ===========================================================================
def run_all() -> Tuple[List[dict], Dict[str, int]]:
    issues: List[dict] = []
    issues.extend(check_trust_surface())
    real_anchors_issues, provenance_tiers = check_real_anchors()
    issues.extend(real_anchors_issues)
    issues.extend(check_gate_ledger())
    return issues, provenance_tiers


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    issues, provenance_tiers = run_all()
    ok = len(issues) == 0

    if args.json:
        print(json.dumps({
            "ok": ok,
            "n_issues": len(issues),
            "real_anchors_provenance_tiers": provenance_tiers,
            "issues": issues,
        }, indent=2, ensure_ascii=False))
    else:
        if ok:
            print("[OK] data contract check PASSED — all 3 files conform")
            print(f"     trust_surface: {TRUST_SURFACE.relative_to(ROOT)}")
            print(f"     real_anchors:  {REAL_ANCHORS.relative_to(ROOT)}")
            print(f"     gate_ledger:   {GATE_LEDGER.relative_to(ROOT)}")
            print(f"     real_anchors provenance tiers: {provenance_tiers}")
        else:
            print(f"[FAIL] data contract check FAILED — {len(issues)} issue(s)")
            for i, issue in enumerate(issues, 1):
                print(f"  {i}. [{issue.get('kind','?')}] "
                      f"{issue.get('file','?')}:  {issue.get('field','?')}")
                for k in ("expected", "actual", "note"):
                    if k in issue:
                        v = issue[k]
                        s = (json.dumps(v, ensure_ascii=False)
                             if isinstance(v, (dict, list)) else str(v))
                        print(f"     {k}: {s[:240]}")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
