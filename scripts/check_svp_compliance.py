#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SVP 协议合规检查（v0.5）。

五个契约：
  1. 7 个 JSON Schema 都能被加载（语法正确）
  2. harness_registry 里注册的门禁符合 SVP gate 语义（id 前缀、category 有效）
  3. 现有 Anchor dataclass 字段与 SVP anchor schema 兼容
  4. Formal Invariant 引擎可执行（v0.5 新增）
  5. Distributional V&V 引擎可执行（v0.5 新增）

用法：
    python scripts/check_svp_compliance.py
    python scripts/check_svp_compliance.py --verbose
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

SVP_SCHEMA_DIR = Path(_ROOT) / "svp" / "schema"
SVP_LEAN_DIR = Path(_ROOT) / "svp" / "lean"
SVP_SMT_DIR = Path(_ROOT) / "svp" / "smt"

#: v0.1 核心 schema
REQUIRED_SCHEMAS_V01 = [
    "scenario.json",
    "gate.json",
    "anchor.json",
    "harness.json",
    "vv_report.json",
]

#: v0.5 新增 schema
REQUIRED_SCHEMAS_V05 = [
    "formal_invariant.json",
    "confidence_profile.json",
]

#: v0.1 schema 用 svp: "1.0"（历史原因），v0.5 schema 用 "0.5"
VALID_SVP_VERSIONS = {"0.5", "1.0"}

#: SVP gate 定义的门禁 id 前缀
VALID_GATE_PREFIXES = ("N", "P", "A", "I", "F")

#: SVP gate 定义的 category 枚举
VALID_GATE_CATEGORIES = {
    "contract", "stage_order", "discipline", "evidence",
    "anchoring", "delivery", "engineering", "positive_control", "system",
}


def check_1_schemas_loadable() -> tuple[bool, list[str]]:
    """契约 1：7 个 JSON Schema 都能被加载（v0.1 五 + v0.5 二）。"""
    errors = []
    all_names = REQUIRED_SCHEMAS_V01 + REQUIRED_SCHEMAS_V05
    for name in all_names:
        path = SVP_SCHEMA_DIR / name
        if not path.exists():
            errors.append(f"missing: {name}")
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if "$schema" not in data:
                errors.append(f"{name}: missing $schema field")
            if "svp" not in data:
                errors.append(f"{name}: missing svp field")
            elif data["svp"] not in VALID_SVP_VERSIONS:
                errors.append(f"{name}: svp version {data['svp']!r} not in {VALID_SVP_VERSIONS}")
            if "title" not in data:
                errors.append(f"{name}: missing title field")
        except json.JSONDecodeError as e:
            errors.append(f"{name}: JSON decode error: {e}")
    return len(errors) == 0, errors


def check_2_registry_compliant(verbose: bool = False) -> tuple[bool, list[str]]:
    """契约 2：harness_registry 里注册的门禁符合 SVP gate 语义。"""
    from engine.harness_registry import all_gates, registered_ids

    errors = []
    gates = all_gates()
    if not gates:
        errors.append("no gates registered")
        return False, errors

    for g in gates:
        if not g.id.startswith(VALID_GATE_PREFIXES):
            errors.append(f"{g.id}: invalid prefix (must be one of {VALID_GATE_PREFIXES})")
        suffix = g.id[1:]
        if not suffix.isdigit():
            errors.append(f"{g.id}: suffix must be digits, got {suffix!r}")
        if g.category not in VALID_GATE_CATEGORIES:
            errors.append(
                f"{g.id}: category {g.category!r} not in SVP enum {sorted(VALID_GATE_CATEGORIES)}"
            )
        if g.category == "positive_control" and g.negation:
            errors.append(f"{g.id}: positive_control gate must have negation=False")
        if g.category != "positive_control" and not g.negation:
            errors.append(f"{g.id}: non-positive-control gate must have negation=True")
        if len(g.description) < 10:
            errors.append(f"{g.id}: description too short ({len(g.description)} chars)")
        if not g.source:
            errors.append(f"{g.id}: missing source field")
        if not callable(g.predicate):
            errors.append(f"{g.id}: predicate must be callable")

    if verbose:
        print(f"  registry gates: {len(gates)}")
        print(f"  gate ids: {registered_ids()}")
    return len(errors) == 0, errors


def check_3_anchor_schema_compatible() -> tuple[bool, list[str]]:
    """契约 3：现有 Anchor dataclass 字段与 SVP anchor schema 兼容。"""
    from engine.wet_lab_anchors import Anchor, DIM_UNANCHORED

    errors = []
    required_fields = {
        "key", "label", "unit", "lo", "hi", "note", "sources",
        "scenarios", "value_key", "decision_relevant",
        "experiment_system", "version", "operating_condition",
    }
    actual_fields = {f.name for f in Anchor.__dataclass_fields__.values()}
    missing = required_fields - actual_fields
    if missing:
        errors.append(f"Anchor dataclass missing fields: {sorted(missing)}")

    if not isinstance(DIM_UNANCHORED, str) or DIM_UNANCHORED != "UNANCHORED":
        errors.append(f"DIM_UNANCHORED should be 'UNANCHORED', got {DIM_UNANCHORED!r}")

    if not hasattr(Anchor, "__dataclass_fields__"):
        errors.append("Anchor is not a dataclass")
    return len(errors) == 0, errors


def check_4_formal_invariants() -> tuple[bool, list[str]]:
    """契约 4（v0.5 新增）：Formal Invariant 引擎可执行。"""
    errors = []
    try:
        from engine import formal_invariants
    except Exception as e:
        return False, [f"import engine.formal_invariants failed: {type(e).__name__}: {e}"]

    # 应有 6 条不变量（INV-1..INV-6）
    # 2026-10-14 新增 INV-6 内容指纹不劣化（防"总数不变但内容缩水"静默降级），
    # Lean 侧同步实现在 svp/lean/Invariants.lean inv6a/inv6b/inv6c 三条 theorem。
    # 之前 selftest 期望 5 导致 L-02 假红灯（2026-10-17 查缺补漏报告）。
    n = len(formal_invariants.INVARIANTS)
    if n != 6:
        errors.append(f"expected 6 invariants, got {n}")

    # id 唯一
    ids = [i.id for i in formal_invariants.INVARIANTS]
    if len(set(ids)) != len(ids):
        errors.append(f"duplicate invariant ids: {ids}")

    # 每条不变量 formal 是 callable
    for inv in formal_invariants.INVARIANTS:
        if not callable(inv.formal):
            errors.append(f"{inv.id}: formal is not callable")
        if len(inv.lean_source) < 50:
            errors.append(f"{inv.id}: lean_source too short ({len(inv.lean_source)} chars)")

    # Lean 4 骨架文件存在且非空
    lean_path = SVP_LEAN_DIR / "Invariants.lean"
    if not lean_path.exists():
        errors.append(f"missing {lean_path.relative_to(_ROOT)}")
    else:
        content = lean_path.read_text(encoding="utf-8")
        if "namespace SwarmLabs" not in content:
            errors.append("Invariants.lean: missing SwarmLabs namespace")
        for expected in ("invariant_honesty_conversation", "invariant_refuted_irreversible",
                         "invariant_anchor_3d", "invariant_error_bound",
                         "invariant_3d_non_regression",
                         "invariant_fingerprint_present",
                         "invariant_fingerprint_matches_baseline",
                         "invariant_fingerprint_set_non_regression"):
            if expected not in content:
                errors.append(f"Invariants.lean: missing definition {expected}")
        # 每个不变量至少要有 1 条具体 theorem（防止"只定义不证明"回归）。
        # 2026-09-27 Phase 4 修 bug 时发现：只有 definition 没有 theorem 的骨架会
        # 让 Lean CI 通过（因为没有错误可以报），但实际什么都没证明。
        for inv, min_theorems in [
            ("inv1_current_production", 1),   # INV-1 具体实例
            ("inv2b_current_refuted_present", 1),  # INV-2b 具体实例
            ("inv3_valid", 1),                # INV-3 具体实例
            ("inv4_zero_errors", 1),          # INV-4 具体实例
            ("inv5_ok", 1),                   # INV-5 具体实例
            ("inv6a_present_valid", 1),       # INV-6a 具体实例
            ("inv6b_match_ok", 1),            # INV-6b 具体实例
            ("inv6c_set_non_regression_present", 1),  # INV-6c 具体实例
            ("svp_current_production", 1),    # 组合定理
            ("svp_rdkit_trap_fails_inv2b", 1),  # 反例
        ]:
            # 至少 1 处 "theorem <name>" 出现
            if content.count(f"theorem {inv}") < min_theorems:
                errors.append(f"Invariants.lean: theorem `{inv}` not found (骨架不能只定义不证明)")

    # SMT-LIB 文件存在
    smt_path = SVP_SMT_DIR / "invariants.smt2"
    if not smt_path.exists():
        errors.append(f"missing {smt_path.relative_to(_ROOT)}")
    else:
        content = smt_path.read_text(encoding="utf-8")
        if "(check-sat)" not in content:
            errors.append("invariants.smt2: missing (check-sat)")

    return len(errors) == 0, errors


def check_5_conformal_vv() -> tuple[bool, list[str]]:
    """契约 5（v0.5 新增）：Distributional V&V 引擎可执行。"""
    errors = []
    try:
        from engine import conformal_vv
    except Exception as e:
        return False, [f"import engine.conformal_vv failed: {type(e).__name__}: {e}"]

    # 应有 5 个默认 alpha level
    if len(conformal_vv.ALPHA_LEVELS) < 3:
        errors.append(f"ALPHA_LEVELS too few ({len(conformal_vv.ALPHA_LEVELS)})")

    # ConfidenceProfile 类存在且字段完整
    required_fields = {"key", "n_train", "n_test", "levels", "coverage_band",
                       "calibration_grade", "profile_score"}
    profile_fields = set(conformal_vv.ConfidenceProfile.__dataclass_fields__.keys())
    missing = required_fields - profile_fields
    if missing:
        errors.append(f"ConfidenceProfile missing fields: {sorted(missing)}")

    # AlphaLevelResult 类存在
    required_lv = {"alpha", "picp", "nmpiw", "verdict"}
    lv_fields = set(conformal_vv.AlphaLevelResult.__dataclass_fields__.keys())
    missing = required_lv - lv_fields
    if missing:
        errors.append(f"AlphaLevelResult missing fields: {sorted(missing)}")

    # Grade 函数存在
    for fn in ("build_confidence_profile", "all_profiles", "rank_profiles", "summarize_grades"):
        if not hasattr(conformal_vv, fn):
            errors.append(f"missing function: {fn}")

    return len(errors) == 0, errors


def run(verbose: bool = False) -> int:
    print("SVP Compliance Check (v0.5)")
    print("=" * 60)
    all_passed = True
    checks = [
        ("1. Schemas loadable (7 files)", check_1_schemas_loadable),
        ("2. Registry compliant with SVP", lambda: check_2_registry_compliant(verbose)),
        ("3. Anchor schema compatible", check_3_anchor_schema_compatible),
        ("4. Formal Invariants engine ready [v0.5]", check_4_formal_invariants),
        ("5. Distributional V&V engine ready [v0.5]", check_5_conformal_vv),
    ]
    for name, fn in checks:
        try:
            ok, errors = fn()
            status = "PASS" if ok else "FAIL"
            print(f"[{status}] {name}")
            if errors and (not ok or verbose):
                for e in errors:
                    print(f"    - {e}")
            if not ok:
                all_passed = False
        except Exception as e:
            print(f"[FAIL] {name}: {type(e).__name__}: {e}")
            all_passed = False

    print()
    print("=" * 60)
    print(f"result: {'ALL PASS' if all_passed else 'FAILED'}")
    print("=" * 60)
    return 0 if all_passed else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()
    sys.exit(run(args.verbose))
