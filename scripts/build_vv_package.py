#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V&V 归档包 CLI —— 构建 / 复验 / 打包。

    python scripts/build_vv_package.py build --out dist/vvpkg
    python scripts/build_vv_package.py build --scenarios microbio_monod,bio_logistic
    python scripts/build_vv_package.py build --all --with-reports --zip
    python scripts/build_vv_package.py verify dist/vvpkg          # 退出码 0/2
    python scripts/build_vv_package.py list

退出码（复验）：
    0  PACKAGE_INTACT    文件与 manifest 逐字节一致
    2  PACKAGE_TAMPERED  有文件被改动 / 缺失 / 多出未登记文件
    1  本地调用或参数错误
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name: str, *rel: str):
    """按**文件路径**加载模块，不经过 `engine/__init__.py`。

    原因：`engine/__init__.py` 会 import surrogate → numpy/scipy。归档包的
    构建器与**复验器**都必须能在零依赖环境运行（第三方手里不该需要我们的
    环境才能核对我们的证据），所以这里刻意绕开包初始化。
    """
    path = os.path.join(ROOT, *rel)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:      # pragma: no cover
        raise RuntimeError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


vp = _load("_swarmlabs_vv_package", "engine", "vv_package.py")


def _parse_keys(spec: str) -> list:
    return [k.strip() for k in spec.split(",") if k.strip()]


def cmd_build(args) -> int:
    keys = None
    if args.scenarios:
        keys = _parse_keys(args.scenarios)
    try:
        man = vp.build_package(
            args.out,
            keys=keys,
            include_ground_truth=args.include_ground_truth,
            include_reports=args.with_reports,
        )
    except (FileNotFoundError, KeyError) as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1

    s = man["summary"]
    print(f"BUILT   {args.out}")
    print(f"  package_id   : {man['package_id']}")
    print(f"  scenarios    : {man['scope']['n_scenarios']}")
    print(f"  verdicts     : {json.dumps(s['counts'], ensure_ascii=False)}")
    print(f"  blocked      : {s['n_blocked']}")
    print(f"  files        : {man['integrity']['n_files']} (sha256)")
    print(f"  ground truth : {'INCLUDED' if man['contains_ground_truth'] else 'committed only (y_sha256)'}")
    print(f"  engine commit: {(man['provenance']['engine'] or {}).get('git_commit') or '(unknown)'}")
    if args.zip:
        z = vp.write_zip(args.out, args.zip_path)
        print(f"  zip          : {z} ({os.path.getsize(z)} bytes)")
    print("  verify with  : python scripts/build_vv_package.py verify "
          f"{args.out}")
    return 0


def cmd_verify(args) -> int:
    r = vp.verify_package(args.dir)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    if r.get("intact"):
        print("\nPACKAGE_INTACT")
        return 0
    print(f"\nPACKAGE_TAMPERED — {r.get('reason') or 'checksum/scope mismatch'}",
          file=sys.stderr)
    return 2


def cmd_list(args) -> int:
    vg = vp._load_gate_module()
    led = vg.load_ledger()
    if not led:
        print("[ERROR] no gate ledger found", file=sys.stderr)
        return 1
    sc = led.get("scenarios") or {}
    print(f"{'scenario':34s} {'verdict':10s} {'gate':26s} domain")
    for k in sorted(sc):
        e = sc[k]
        print(f"{k:34s} {str(e.get('verdict')):10s} "
              f"{str(e.get('gate')):26s} {e.get('domain')}")
    print(f"\ntotal={len(sc)} counts={led.get('counts')}")
    return 0


#: 本项目引擎依赖的科学栈——归档工具**不得**引入其中任何一个。
FORBIDDEN_DEPS = ("numpy", "scipy", "pandas", "torch", "cloudpickle")


def cmd_selfcheck(args) -> int:
    """证明构建器/复验器是零依赖的。

    不是形式主义：如果复验器偷偷依赖 numpy，"第三方可独立复验"这句话就只是
    把信任对象从"我们的结论"换成了"我们的环境"。
    """
    bad = [m for m in FORBIDDEN_DEPS if m in sys.modules]
    th = vp._policy_bundle()["thresholds"]
    print(f"python           : {sys.version.split()[0]}")
    print(f"third-party deps : {bad or 'none'}")
    print(f"policy source    : engine/verification_gate.py::thresholds()")
    print(f"thresholds loaded: pass_r2={th['pass_r2']} "
          f"coverage_pass={th['coverage_pass']} noise_floor={th['noise_floor']}")
    if bad:
        print(f"\nSTDLIB_ONLY_VIOLATED — imported: {bad}", file=sys.stderr)
        return 2
    print("\nSTDLIB_ONLY_OK")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="SwarmLabs V&V package builder")
    sub = ap.add_subparsers(dest="cmd")

    b = sub.add_parser("build", help="build an archival package")
    b.add_argument("--out", default=os.path.join("dist", "vvpkg"),
                   help="output directory (recreated)")
    b.add_argument("--scenarios", default="",
                   help="comma-separated keys; default = all in ledger")
    b.add_argument("--all", action="store_true",
                   help="explicit 'all' (same as omitting --scenarios)")
    b.add_argument("--include-ground-truth", action="store_true",
                   help="include held-out y (leaks the answer key — internal use)")
    b.add_argument("--with-reports", action="store_true",
                   help="copy HTML/JSON reports into the package")
    b.add_argument("--zip", action="store_true", help="also write a .zip")
    b.add_argument("--zip-path", default=None)
    b.set_defaults(func=cmd_build)

    v = sub.add_parser("verify", help="offline integrity re-verification")
    v.add_argument("dir")
    v.set_defaults(func=cmd_verify)

    l = sub.add_parser("list", help="list archivable scenarios")
    l.set_defaults(func=cmd_list)

    sc = sub.add_parser("selfcheck",
                        help="prove the builder/verifier import no third-party deps")
    sc.set_defaults(func=cmd_selfcheck)

    args = ap.parse_args(argv)
    if not getattr(args, "func", None):
        ap.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
