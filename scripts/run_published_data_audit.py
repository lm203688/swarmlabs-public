#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GC-01 自动提取级审计入口（B-2）——审计已发表论文的原始数据。

与 scripts/ 下其他审计的关系：
  - engine/paper_audit.py（17 个经典基准）：数据手工录入，内部一致性级。
  - 本脚本（engine/published_data_audit.py）：Zenodo 机器可读原始数据 +
    sha256 校验 + 程序化重拟合，独立复现级。当前唯一基准：
    González-Siso et al. 2026 (Sci Rep, DOI 10.1038/s41598-026-54144-5)。

产物：
  data/published_data_audit.json
  data/published_data_audit_report.md

用法：
  python scripts/run_published_data_audit.py
  python scripts/run_published_data_audit.py --data-dir <自定义目录>  # 跳过 sha256
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.published_data_audit import audit_ecoli_paper, run_published_data_audit


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="GC-01 已发表原始数据自动提取审计")
    ap.add_argument("--data-dir", default=None,
                    help="自定义原始数据目录（跳过 manifest sha256 校验，仅供测试）")
    ap.add_argument("--print-summary", action="store_true")
    args = ap.parse_args(argv)

    if args.data_dir:
        from engine import published_data_audit as pda
        cert = pda.audit_ecoli_paper(data_dir=args.data_dir)
        print(f"[published-data-audit] verdict={cert['verdict']} "
              f"claims={cert['n_claims_reproduced']}/{cert['n_claims_total']}")
        return 0

    report = run_published_data_audit()
    if args.print_summary:
        cert = report["certificates"][0]
        print(f"  verdict : {cert['verdict']}")
        print(f"  basis   : {cert['verdict_basis']}")
        for ck in cert["claim_checks"]:
            print(f"  [{'OK ' if ck.get('reproduced') else 'MISS'}] {ck['claim']}"
                  f" ({ck.get('reading', '')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
