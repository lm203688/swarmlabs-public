"""Error taxonomy CI gate (硬骨头 3 验收门禁).

对齐《地基承重审计报告》§九 · 硬骨头 3 的验收条款：
- 门禁模块内**不允许**存在无 error_kind 标记的静默吞异常
- 报告全仓库覆盖率（不 fail，只 WARN 趋势）
- 每次新增 except Exception 都必须选 FATAL / DEGRADABLE / IGNORABLE 一档

跑法：
    python scripts/check_error_taxonomy.py              # 常规门禁
    python scripts/check_error_taxonomy.py --json       # JSON 报告
    python scripts/check_error_taxonomy.py --baseline=N # 覆盖率必须 ≥ N（可选）
    python scripts/check_error_taxonomy.py --strict     # 未标注任何 except 也 fail

失败退出码：
    0 — 门禁模块内无静默吞异常，可接受
    1 — 门禁模块内有未标注的静默吞异常（阻塞合并）
    2 — 内部错误
"""
from __future__ import annotations

import argparse
import json
import sys

# 直接借用 error_taxonomy 的扫描器，避免复制
from error_taxonomy import scan_repo, summarize, GATE_MODULES, ROOT


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    ap.add_argument("--baseline", type=float, default=0.0,
                    help="覆盖率下限（百分比），低于此值 fail")
    ap.add_argument("--strict", action="store_true",
                    help="全仓库静默吞异常未标注也 fail（比门禁模块更严）")
    ap.add_argument("--gate-only", action="store_true",
                    help="只报告门禁模块的违规（当前默认；等价于不 --strict）")
    ap = ap
    args = ap.parse_args(argv)

    sites = scan_repo()
    s = summarize(sites)

    # 硬性失败条件
    fail_reasons = []
    if s["gate_violations"] > 0:
        fail_reasons.append(
            f"gate modules contain {s['gate_violations']} untagged silent-swallow sites "
            f"(FATAL/DEGRADABLE/IGNORABLE annotation required)"
        )
    if args.strict and s["silent_untagged"] > 0:
        fail_reasons.append(
            f"--strict: {s['silent_untagged']} untagged silent-swallow sites "
            f"across whole repo"
        )
    if s["coverage_pct"] < args.baseline:
        fail_reasons.append(
            f"coverage {s['coverage_pct']}% < baseline {args.baseline}%"
        )

    if args.json:
        print(json.dumps({
            **s,
            "gate_modules_scanned": sorted(GATE_MODULES),
            "fail_reasons": fail_reasons,
            "ok": not fail_reasons,
        }, ensure_ascii=False, indent=2))
    else:
        print(f"error_taxonomy gate — {s['total']} sites across "
              f"{len(s['per_file'])} files ({len(GATE_MODULES)} gate modules)")
        print(f"  tagged:   {s['tagged']}/{s['total']} ({s['coverage_pct']}%)")
        print(f"    FATAL      {s['by_kind']['FATAL']}")
        print(f"    DEGRADABLE {s['by_kind']['DEGRADABLE']}")
        print(f"    IGNORABLE  {s['by_kind']['IGNORABLE']}")
        print(f"    legacy-only {s['legacy_only']}")
        print(f"    untagged    {s['untagged']}")
        print(f"  silent: {s['silent']}  (untagged: {s['silent_untagged']})")
        print(f"  gate modules: {s['gate_sites']} sites / {s['gate_violations']} violations")
        if s["violations"]:
            print("\nviolations:")
            for v in s["violations"][:20]:
                print(f"  [{v['violation']}] {v['path']}:{v['lineno']}  {v['raw'][:80]}")
        if fail_reasons:
            print("\nFAIL:")
            for r in fail_reasons:
                print(f"  - {r}")
        else:
            print("\nOK — 门禁模块内无静默吞异常未标注")

    return 1 if fail_reasons else 0


if __name__ == "__main__":
    sys.exit(main())
