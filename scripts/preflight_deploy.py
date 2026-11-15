#!/usr/bin/env python3
"""preflight_deploy.py — 部署前置闸门：产物不合格就不许推向线上。

为什么需要这个，而不是只靠 CI
-----------------------------
本仓库是**私有仓库**，GitHub 的 branch protection 属于付费功能：
    PUT /repos/.../branches/main/protection
    -> HTTP 403 "Upgrade to GitHub Pro or make this repository public
                  to enable this feature."
（2026-09-24 实测。）所以 `.github/workflows/honesty-gate.yml` 即使判红，
也**挡不住**任何人往 main 推送——那个灯泡没接进电路。

因此闸门的落点不能是 CI，得是**部署动作本身**。deploy_pages.py 里的
`gate_assets` 是**事后**闸门：它核对的是已经上线的资产和本地的差异，
一个被 rdkit 缺失截断的产物推上去之后它才会发现，而那时线上已经是一个
「零失败」的假账本了。

本脚本是**事前**闸门：在任何字节上传之前把产物验掉。

用法
----
    python scripts/preflight_deploy.py                 # 验，不部署
    python scripts/preflight_deploy.py --and-deploy    # 全绿才接着跑 deploy_pages.py
    python scripts/deploy_pages.py                     # 已内置本闸门，无需单独跑

退出码
------
    0  全部通过
    2  某项检查不通过（绝不继续部署）

设计约束：五个检查全部只用标准库。CI 和本地走同一条判据，不因为
Actions 环境缺 numpy/scipy/rdkit 而出现「本地绿、线上红」的分裂。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (展示名, 调用参数)。顺序即执行顺序。
# interp 支持 python（默认）或 node。
GATES = [
    ("诚实口径不变量", "python", ["check_honesty_invariants.py"]),
    ("公开数字核验",   "python", ["check_public_numbers.py"]),
    ("前端 i18n 基线", "python", ["lint_frontend_i18n.py"]),
    ("V&V 归档包自检", "python", ["build_vv_package.py", "selfcheck"]),
    ("VLab 编排层门禁", "python", ["check_vlab_gates.py"]),
    # 埋点方案在 KV 免费层配额下的回归测试（防止未来某次改动把写配额重新烧光）
    ("KV 配额回归",    "node",   ["verify_kv_quota.js"]),
]

# 本机 node 路径（Windows）：版本目录由 env 覆盖或从 PATH 找，避免版本升级后硬编码失效。
# 2026-10-09：22.22.2-3 → 22.22.2-6（本机实际位置）。
def _resolve_node() -> str:
    import os as _os
    import shutil as _sh
    env_node = _os.environ.get("SWARMLABS_NODE")
    if env_node and _os.path.exists(env_node):
        return env_node
    ver_dir = r"C:\Users\xing\.workbuddy\binaries\node\versions"
    if _os.path.isdir(ver_dir):
        for v in sorted(_os.listdir(ver_dir), reverse=True):
            cand = _os.path.join(ver_dir, v, "node.exe")
            if _os.path.exists(cand):
                return cand
    fallback = _sh.which("node")
    return fallback or "node"

NODE = _resolve_node()


def run_all() -> int:
    print("=" * 66)
    print("部署前置闸门  preflight_deploy")
    print("=" * 66)
    t0 = time.time()
    failed: list[str] = []
    for name, interp, args in GATES:
        # args[0] 是 scripts/ 下的脚本名，其余是传给它的参数
        script_path = os.path.join(ROOT, "scripts", args[0])
        if interp == "python":
            cmd = [sys.executable, script_path] + list(args[1:])
        elif interp == "node":
            cmd = [NODE, script_path] + list(args[1:])
        else:
            raise SystemExit(f"未知解释器: {interp}")
        label = " ".join([os.path.join("scripts", args[0])] + list(args[1:]))
        print(f"\n--- {name}  ({label}) ---")
        p = subprocess.run(cmd, cwd=ROOT,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True)
        out = (p.stdout or "").strip().splitlines()
        # 只显示头尾几行，完整输出在需要时单独跑那个脚本
        for line in out[:2]:
            print(f"    {line}")
        for line in out[-3:]:
            print(f"    {line}")
        print(f"    -> exit {p.returncode}")
        if p.returncode != 0:
            failed.append(name)

    print("\n" + "=" * 66)
    print(f"用时 {time.time() - t0:.1f}s")
    if failed:
        print(f"[BLOCK] {len(failed)}/{len(GATES)} 项检查不通过，禁止部署：")
        for f in failed:
            print(f"    - {f}")
        print("\n修法不是改这里，也不是加 --skip：")
        print("  产物被截断 -> 用带 rdkit 的解释器重跑 scripts/build_reports.py 再重建账本")
        print("  确实要发布缩减包 -> 改 scripts/check_honesty_invariants.py 的期望值常量，")
        print("                     让那次缩减变成一条可读的 commit")
        print("  紧急情况必须强推 -> 加 --skip-preflight 并立刻在 Actions 标签页确认 honesty-gate 为何红")
        return 2
    print(f"[PASS] {len(GATES)}/{len(GATES)} 项全绿，可以部署")
    return 0


def main() -> int:
    if "--skip-preflight" in sys.argv:
        print("[skip] preflight 被显式跳过")
        return 0
    rc = run_all()
    if rc != 0:
        return rc
    if "--and-deploy" in sys.argv:
        print("\n== 闸门通过，转入 deploy_pages.py ==")
        p = subprocess.run([sys.executable, os.path.join("scripts", "deploy_pages.py")],
                           cwd=ROOT)
        return p.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
