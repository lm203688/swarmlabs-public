#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/rebuild_trust_chain.py — 给 honesty 块配编制（P0-A）。

为什么这个脚本存在
-------------------
2026-10-17 晨评估报告 §2 实锤：夜间 nightly-ops 重建（03:17）会把
``data/trust_surface.json`` 整体重写，从而**静默丢弃** ``overall.honesty``
块。全仓唯一写 honesty 的脚本 ``patch_trust_surface_honesty.py`` 却**零
调用**——grep 全仓只匹配到注释。这是七轮评估里第三次复发（10-12 首现 →
10-13~15 手动补丁临时修 → 10-16晚绿 → 10-17 03:17 再红）。

根因不是"忘了跑补丁"，而是"补丁没有编制"。项目把「fail-closed + 诚实
披露」当作核心主张，但最重要的诚实声明没有自动化守卫——每次重建都要
靠人记得。

解决方案
--------
把补丁脚本**纳入正式流水线**，用三条路径共同保证 honesty 块不再丢失：

1. **主线**（本脚本）：作为 nightly-ops 的末尾步骤，每次重建后必跑。
2. **兜底断言**：``scripts/check_data_contracts.py`` 已把
   ``overall.honesty`` 列为必填字段（fail-closed）。若本脚本未运行，
   CI 会在下游硬失败，报错信息直接指向本脚本。
3. **文档**：`reports/assessment/..._2026-10-17.md` §六 P0-A 明确把
   "honesty 编制" 列为 P0，任何后续改动都不得移除本脚本调用。

执行顺序（严格）
---------------
    1. ``scripts/patch_trust_surface_honesty.py``  → 打补丁写 honesty 块
    2. ``scripts/build_public_facts.py``            → 重建 public_facts.json
    3. ``scripts/build_gate_ledger.py``             → 刷新账本

补丁先跑的原因：``build_public_facts.py`` 从 ``trust_surface.json`` 读
counts，而 ``honesty.experimental_ground_truth_count`` 是 ``counts``
之外的独立字段——先打补丁，再让事实表基于正确的 honesty 快照重建。

退出码
------
    0  三步全绿
    1  任一步骤非零退出（fail-closed）

用法
----
    python scripts/rebuild_trust_chain.py
    python scripts/rebuild_trust_chain.py --print  # 打印执行计划后跳过
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# L-10 可复现性（2026-10-17 查缺补漏）
# 主流程固定随机种子。虽然本脚本自身不做采样，但它调用 build_public_facts.py
# 与 build_gate_ledger.py，两者都可能引入 numpy 随机性。通过环境变量
# SWARMLABS_BUILD_SEED 显式传递给下游，确保整条链路共享同一 seed。
BUILD_SEED = 42


def _fix_seeds() -> int:
    random.seed(BUILD_SEED)
    try:
        import numpy as np  # noqa: F401
        np.random.seed(BUILD_SEED)
    except Exception:
        pass
    os.environ["SWARMLABS_BUILD_SEED"] = str(BUILD_SEED)
    return BUILD_SEED

# 三步流水线：每步 (label, script_path, expected_output_checks)
CHAIN = [
    (
        "patch_trust_surface_honesty",
        ROOT / "scripts" / "patch_trust_surface_honesty.py",
        "trust_surface.json:overall.honesty",
    ),
    (
        "build_public_facts",
        ROOT / "scripts" / "build_public_facts.py",
        "public_facts.json regenerated",
    ),
    (
        "build_gate_ledger",
        ROOT / "scripts" / "build_gate_ledger.py",
        "gate_ledger.json regenerated",
    ),
]


def _run_step(label: str, script: Path) -> tuple[bool, str, float]:
    """Run one step. Return (ok, stdout+stderr, elapsed_s)."""
    if not script.exists():
        return (False, f"[MISSING SCRIPT] {script}", 0.0)

    t0 = time.time()
    try:
        r = subprocess.run(
            [sys.executable, str(script)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )
        elapsed = time.time() - t0
        out = (r.stdout or "") + ("\n---STDERR---\n" + r.stderr if r.stderr else "")
        return (r.returncode == 0, out, elapsed)
    except subprocess.TimeoutExpired:
        return (False, f"[TIMEOUT] {script} after 120s", time.time() - t0)
    except Exception as e:  # error_kind:FATAL — 子进程拉起失败即整链失败
        return (False, f"[EXCEPTION] {e}", time.time() - t0)


def _verify_honesty_present() -> tuple[bool, str]:
    """Post-chain assertion: trust_surface.json:overall.honesty must exist."""
    ts = ROOT / "data" / "trust_surface.json"
    if not ts.exists():
        return (False, "trust_surface.json missing")
    try:
        d = json.loads(ts.read_text(encoding="utf-8"))
        overall = d.get("overall") or {}
        h = overall.get("honesty")
        if not isinstance(h, dict):
            return (False, f"overall.honesty missing or not a dict (got {type(h).__name__})")
        # Sanity: honesty should carry the invariants that CI honesty-gate depends on.
        # `simulated_count` and `experimental_ground_truth_count` are the two
        # fields the对外口径 (README §1.1 / public_facts.json) actually reads.
        required = ("experimental_ground_truth_count", "simulated_count", "all_evidence_simulated")
        missing = [k for k in required if k not in h]
        if missing:
            return (False, f"overall.honesty missing keys: {missing}")
        return (True, (
            f"honesty intact "
            f"(simulated_count={h.get('simulated_count')}, "
            f"experimental_ground_truth_count={h.get('experimental_ground_truth_count')}, "
            f"all_evidence_simulated={h.get('all_evidence_simulated')})"
        ))
    except Exception as e:
        return (False, f"parse error: {e}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--print", action="store_true", help="print plan only, do not execute")
    ap.add_argument("--json", action="store_true", help="emit JSON summary to stdout")
    args = ap.parse_args()

    seed = _fix_seeds()

    if args.print:
        print("Rebuild chain (dry-run):")
        for i, (label, script, _) in enumerate(CHAIN, 1):
            print(f"  {i}. {script.name}  —  {label}")
        print(f"  seed={seed}")
        return 0

    summary = {"chain": [], "honesty_verified": False, "honesty_detail": "", "all_ok": False, "seed": seed}

    print("=" * 72)
    print("rebuild_trust_chain — 给 honesty 块配编制（P0-A）")
    print("=" * 72)

    for label, script, _ in CHAIN:
        print(f"\n[{label}] {script.relative_to(ROOT)}")
        ok, out, elapsed = _run_step(label, script)
        # Trim output for CI logs
        tail = out[-400:] if len(out) > 400 else out
        if tail:
            for line in tail.rstrip().splitlines()[-4:]:
                print(f"    {line}")
        status = "OK" if ok else "FAIL"
        print(f"    -> {status} ({elapsed:.2f}s)")
        summary["chain"].append({
            "step": label,
            "ok": ok,
            "elapsed_s": round(elapsed, 3),
        })
        if not ok:
            print(f"\n[FAIL] {label} non-zero — aborting chain")
            if args.json:
                print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 1

    ok, detail = _verify_honesty_present()
    summary["honesty_verified"] = ok
    summary["honesty_detail"] = detail
    print(f"\n[HONESTY VERIFY] {ok}: {detail}")
    summary["all_ok"] = ok

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))

    if not ok:
        print("\n[FAIL] honesty 块未恢复 —— CI 硬失败，检查 patch_trust_surface_honesty.py")
        return 1

    print("\n[OK] 三步流水线全绿，honesty 块已恢复。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
