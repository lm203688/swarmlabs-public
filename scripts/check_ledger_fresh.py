#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ledger freshness gate —— 指纹变更 → 强制重验的 CI 守门人。

设计：CI 跑完 scripts/build_gate_ledger.py 后立刻调本脚本；
如果 ledger 里的 engine 摘要与**当前**引擎摘要不一致（意味着开发者改了
引擎但没重跑账本），CI 直接红——「沿用旧结论」从默认行为变成显式失败。

用法：
    python scripts/check_ledger_fresh.py           # 默认检查前端公开的账本
    python scripts/check_ledger_fresh.py --print    # 打印诊断字典

退出码：
    0 = 账本新鲜
    1 = 账本过期（引擎摘要不一致）
    2 = 无账本

这条门禁的存在意义
------------------
指纹不是给读者看的，是给**机器**看的：
    · scripts/build_reports.py 跑完 → build_gate_ledger 刷新账本
    · ledger 内 all_digest == engine_fingerprint().all_digest → PASS
    · 开发者改了 engine/surrogate.py 但没重跑账本
      → all_digest 变了 → 本脚本 exit 1 → CI 红
      → 飞轮/MCP/HTTP 侧都不会把旧 PASS 当成有效结论

fail-closed 语义与 engine/verification_gate.assert_action_allowed 一致：
拿不准就不放行，宁红不假绿。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine.verification_gate import (  # noqa: E402
    force_revalidate_reason,
    ledger_is_stale,
    load_ledger,
)


def main() -> int:
    ap = argparse.ArgumentParser(description="Ledger freshness gate (fail-closed)")
    ap.add_argument("--print", action="store_true", help="print full status dict")
    ap.add_argument("--ledger", help="custom ledger path (default: auto-locate)")
    args = ap.parse_args()

    ledger = load_ledger(args.ledger)
    status = ledger_is_stale(ledger)

    if args.do_print if hasattr(args, "do_print") else args.print:
        print(json.dumps(status, ensure_ascii=False, indent=2))

    if not status.get("available"):
        print(
            f"[LEDGER FRESH] exit 2 —— 无账本 (run scripts/build_gate_ledger.py "
            f"before consuming gates). Reason: {status.get('reason','')}",
            file=sys.stderr,
        )
        return 2

    if status.get("stale"):
        reason = force_revalidate_reason(ledger) or "engine all_digest mismatch"
        print(
            f"[LEDGER FRESH] exit 1 —— 账本过期，拒绝沿用旧结论。\n"
            f"  ledger_all_digest   = {str(status.get('ledger_all_digest',''))[:16]}…\n"
            f"  current_all_digest  = {str(status.get('current_all_digest',''))[:16]}…\n"
            f"  ledger_generated_at = {status.get('ledger_generated_at_utc','?')}\n"
            f"  reason              = {reason}\n"
            f"  fix                 = python scripts/build_gate_ledger.py",
            file=sys.stderr,
        )
        return 1

    digest = str(status.get("current_all_digest", ""))[:16]
    commit = str(status.get("current_git_commit", ""))[:12]
    print(
        f"[LEDGER FRESH] exit 0 —— 账本新鲜，与当前引擎指纹一致 "
        f"(all_digest={digest}…  git={commit or '(no git)'})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
