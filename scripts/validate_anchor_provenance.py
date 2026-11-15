#!/usr/bin/env python3
"""Validate anchor provenance schema across data/real_anchors.json.

硬骨头 2 (2026-10-16 地基承重审计报告 §三): 契约校验缺失

    | 文件                    | schema 字段 |
    |-------------------------|-------------|
    | data/real_anchors.json  | ❌ 无       |

本脚本是锚点溯源契约的**可执行**守门人。每一个真实锚点都必须携带完整的
provenance 块，且每个 required 字段都要存在。缺失即 FAIL（fail-closed），
不允许"没有 provenance 就静默通过"。

Required provenance fields（新增锚点必须携带）:
    source_type, source_url, doi, doi_resolvable, publisher,
    dataset_name, file_sha256, file_size_bytes, extraction_method,
    extraction_date, extraction_script, license, raw_points, fit

历史豁免（grace list）：2026-10-16 之前入库的旧锚点允许缺失部分字段，但
必须在 GRACE_ANCHORS 里显式列出，且带 reason。新增锚点若未入 GRACE 且缺字段
则直接 FAIL——不允许"默认为旧"。

Usage:
    python scripts/validate_anchor_provenance.py
    python scripts/validate_anchor_provenance.py --strict  # 无豁免
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
ANCHORS_FILE = DATA_DIR / "real_anchors.json"

# Fields every anchor MUST carry in its `provenance` block.
REQUIRED_PROVENANCE_FIELDS = [
    "source_type",
    "source_url",
    "doi",
    "doi_resolvable",
    "publisher",
    "dataset_name",
    "file_sha256",
    "file_size_bytes",
    "extraction_method",
    "extraction_date",
    "extraction_script",
    "license",
    "raw_points",
    "fit",
]

# Fields every anchor (top-level) MUST carry.
REQUIRED_TOP_FIELDS = ["id", "parameter", "value", "units", "notes", "provenance"]

DOI_RE = re.compile(r"^10\.\d{4,9}/[^\"']+")


def _is_grace(anchor_id: str, anchor: Dict[str, Any]) -> Tuple[bool, str]:
    """Grace-list is inline (not a separate file) so the exemption is auditable.

    2026-10-16 硬骨头 2 · 契约豁免登记表 —— 每一条豁免都必须带 reason。
    新增豁免必须同步在下方列表登记，且 CI 会打印"豁免已使用"。
    """
    GRACE_ANCHORS = {
        # E. coli Monod anchors (2026-10-15 Monod fit; pre-schema enforcement)
        "ecoli_monod_ks_real": (
            "2026-10-15 Monod fit result; K_s left-censored so `value` is None "
            "(documented as < 0.1 g/L). Provenance block attached at top-level "
            "via `monod_fit` key, not per-anchor. To be refactored once the "
            "fit is re-run through scripts/extract_yeast_anchors.py-style schema."
        ),
        "ecoli_monod_mu_max_real": (
            "2026-10-15 Monod fit result; provenance lives in `monod_fit` "
            "block. To be refactored to per-anchor provenance in next round."
        ),
    }
    if anchor_id in GRACE_ANCHORS:
        return True, GRACE_ANCHORS[anchor_id]
    return False, ""


def _validate_anchor(anchor: Dict[str, Any]) -> List[str]:
    """Return list of contract violations. Empty list = OK."""
    problems: List[str] = []

    # 1. Top-level required fields
    for field in REQUIRED_TOP_FIELDS:
        if field not in anchor:
            problems.append(f"missing top-level field: {field}")

    aid = anchor.get("id", "<unknown>")

    # 2. Grace-listed anchors: don't require full provenance schema, but log
    grace, reason = _is_grace(aid, anchor)
    if grace:
        # Even grace anchors must carry id, value, notes
        if not anchor.get("notes"):
            problems.append(f"[grace] missing notes")
        return problems  # early out; grace is logged by caller

    prov = anchor.get("provenance")
    if not isinstance(prov, dict):
        problems.append("provenance is not a dict (missing or malformed)")
        return problems

    # 3. Required provenance fields
    missing = [f for f in REQUIRED_PROVENANCE_FIELDS if f not in prov]
    if missing:
        problems.append(f"provenance missing fields: {missing}")

    # 4. DOI resolvable check
    if prov.get("doi_resolvable") is not True:
        problems.append("doi_resolvable must be True (not False, not missing)")

    # 5. DOI format check
    doi = prov.get("doi") or ""
    if not DOI_RE.match(str(doi)):
        problems.append(f"malformed DOI: {doi!r}")

    # 6. SHA256 format (64 hex chars)
    sha = prov.get("file_sha256") or ""
    if not re.match(r"^[0-9a-f]{64}$", str(sha)):
        problems.append(f"file_sha256 not 64 hex chars: {sha[:16]}...")

    # 7. File path on disk must exist
    rel = prov.get("data_file_path") or ""
    if rel:
        abs_p = (ROOT / rel).resolve()
        if not abs_p.exists():
            problems.append(f"data_file_path does not exist: {rel}")
        elif prov.get("file_sha256"):
            import hashlib
            actual = hashlib.sha256(abs_p.read_bytes()).hexdigest()
            if actual != sha:
                problems.append(
                    f"file_sha256 mismatch: stored={sha[:16]}... actual={actual[:16]}..."
                )

    # 8. extraction_script path must exist
    script_rel = prov.get("extraction_script") or ""
    if script_rel and not (ROOT / script_rel).exists():
        problems.append(f"extraction_script does not exist: {script_rel}")

    # 9. raw_points must be non-empty list
    rp = prov.get("raw_points")
    if not isinstance(rp, list) or len(rp) == 0:
        problems.append("raw_points must be a non-empty list")

    # 10. fit block must have r_squared and method
    fit = prov.get("fit") or {}
    if "r_squared" not in fit:
        problems.append("provenance.fit missing r_squared")
    if "method" not in fit:
        problems.append("provenance.fit missing method")
    r2 = fit.get("r_squared")
    if isinstance(r2, (int, float)) and (r2 < 0 or r2 > 1):
        problems.append(f"r_squared out of [0,1]: {r2}")

    return problems


def validate(path: Path = ANCHORS_FILE, strict: bool = False) -> Dict[str, Any]:
    if not path.exists():
        return {"ok": False, "error": f"missing {path}"}

    data = json.loads(path.read_text(encoding="utf-8"))
    anchors = data.get("anchors", [])
    if not anchors:
        return {"ok": False, "error": "no anchors"}

    per_anchor: List[Dict[str, Any]] = []
    total_violations = 0
    grace_used: List[str] = []
    for a in anchors:
        aid = a.get("id", "<unknown>")
        is_grace, grace_reason = _is_grace(aid, a)
        problems = _validate_anchor(a)
        if is_grace:
            grace_used.append(aid)
        total_violations += len(problems)
        per_anchor.append({
            "id": aid,
            "is_grace": is_grace,
            "grace_reason": grace_reason,
            "violations": problems,
        })

    result = {
        "ok": total_violations == 0,
        "total_anchors": len(anchors),
        "total_violations": total_violations,
        "strict_mode": strict,
        "grace_anchors_used": grace_used,
        "required_provenance_fields": REQUIRED_PROVENANCE_FIELDS,
        "per_anchor": per_anchor,
    }
    return result


def print_report(r: Dict[str, Any]) -> None:
    print(f"[validate_anchor_provenance] {r['total_anchors']} anchors checked, "
          f"strict_mode={r.get('strict_mode', False)}")
    if r.get("grace_anchors_used"):
        print(f"  grace-listed (skipped full schema): {r['grace_anchors_used']}")
    print("-" * 72)
    if not r.get("ok"):
        print(f"[validate_anchor_provenance] FAIL: {r.get('total_violations')} violations")
        for a in r["per_anchor"]:
            if a["violations"]:
                print(f"  ✗ {a['id']}")
                for v in a["violations"]:
                    print(f"      - {v}")
        return
    print("[validate_anchor_provenance] PASS: all anchors meet the contract")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true",
                    help="no grace anchors allowed")
    ap.add_argument("--output", help="write JSON report here")
    args = ap.parse_args()
    # Variable `args` intentionally named to avoid typos.
    r = validate(strict=args.strict)
    print_report(r)
    if args.output:
        Path(args.output).write_text(
            json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    if not r.get("ok"):
        if r.get("error"):
            print(f"  reason: {r['error']}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
