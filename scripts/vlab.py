#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VLab CLI —— 「发来一个实验」的统一入口。

    python scripts/vlab.py schema                    # 契约说明（机器可读）
    python scripts/vlab.py disciplines [--json]      # 专业接入状态
    python scripts/vlab.py checklist <Category>      # 接入清单
    python scripts/vlab.py intake <work-order.json>  # 只校验，不跑
    python scripts/vlab.py run <work-order.json> [--out DIR] [--package]
    python scripts/vlab.py verify <delivery.json>    # 离线复验交付物
    python scripts/vlab.py selftest                  # 否定性自检（fail-closed 纪律）
    python scripts/vlab.py serve [--port 8790]       # 起 HTTP 服务（零依赖）
    python scripts/vlab.py selftest-server           # 服务层否定性自检

退出码约定（供 CI / 客户端脚本消费）：
    0  成功 / 交付完好
    1  工作单或请求非法
    2  交付物被改动（TAMPERED）—— 与 vv_package 的篡改检测同一纪律
    3  流水线给出「不得自动推进」的裁决（BLOCK / FAIL_CLOSED）——**不是错误**，
       是诚实的业务结果，但脚本需要能区分它
    4  环境错误（文件读不到等）

零依赖：本脚本与它加载的 engine/vlab_* 模块都只用标准库，可在无 numpy 的
干净解释器（如 CI）里跑。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE = os.path.join(ROOT, "engine")


def _load(name: str):
    path = os.path.join(ENGINE, f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"_cli_{name}", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _read(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"ERROR: cannot read {path}: {e}", file=sys.stderr)
        raise SystemExit(4)


def cmd_schema(args) -> int:
    C = _load("vlab_contract")
    print(json.dumps(C.schema_doc(), ensure_ascii=False, indent=2))
    return 0


def cmd_disciplines(args) -> int:
    DR = _load("discipline_registry")
    s = DR.registry_summary()
    if args.json:
        print(json.dumps(s, ensure_ascii=False, indent=2))
        return 0
    t = s["totals"]
    print(f"专业接入：{s['n']} 项  "
          f"VALIDATED={t.get('VALIDATED',0)}  "
          f"AWAITING_GOLD={t.get('AWAITING_GOLD',0)}  "
          f"AWAITING_KERNEL={t.get('AWAITING_KERNEL',0)}")
    print(f"{'专业':<18}{'状态':<18}{'gold':<8}{'内核场景':<24}错误")
    for r in s["rows"]:
        print(f"{r['name']:<18}{r['status']:<18}"
              f"{str(r['n_gold_resolved'])+'/'+str(r['n_gold']):<8}"
              f"{(r['kernel_scenario_key'] or '-'):<24}"
              f"{','.join(r['errors'])}")
    print()
    print(s["note"])
    return 0


def cmd_checklist(args) -> int:
    DR = _load("discipline_registry")
    steps = DR.onboarding_checklist(args.category or "")
    print(f"接入清单（category={args.category or '<Category>'}）：")
    for s in steps:
        print(f"\n{s['step']}. {s['action']}")
        print(f"   产出: {s['artifact']}")
        print(f"   验收: {s['gate']}")
    return 0


def cmd_intake(args) -> int:
    C = _load("vlab_contract")
    wo = _read(args.work_order)
    r = C.validate_work_order(wo)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0 if r["ok"] else 1


def cmd_run(args) -> int:
    O = _load("vlab_orchestrator")
    C = _load("vlab_contract")
    wo = _read(args.work_order)
    job = O.run_job(wo, out_dir=args.out, build_package=args.package)
    if args.json:
        print(json.dumps({k: v for k, v in job.items()
                          if not k.startswith("_")},
                         ensure_ascii=False, indent=2))
    else:
        print(f"work order : {job['work_order_id']}")
        print(f"outcome    : {job['outcome']}")
        for s in job["stages"]:
            extra = {k: v for k, v in s.items()
                     if k in ("mode", "kind", "hard_verdict", "gate",
                              "cite_ok", "counts", "reason")}
            print(f"  [{s['stage']:<11}] ok={str(s['ok']):<5} {extra or ''}")
        v = job.get("verdict") or {}
        if v:
            print(f"verdict    : {v.get('hard_verdict')} / {v.get('gate')} "
                  f"(cite_ok={v.get('cite_ok')})")
            print(f"confidence : {v.get('confidence')}  rec={v.get('recommendation')}")
        if job.get("delivery"):
            print(f"delivery   : {job['delivery']['json']}")
            print(f"digest     : {job['delivery']['digest']}")
    oc = job["outcome"]
    if oc in ("BLOCK_AUTONOMOUS_ACTION", "NO_LEDGER_FAIL_CLOSED",
              "STALE_LEDGER_FAIL_CLOSED"):
        return 3
    if oc == "REJECTED_AT_INTAKE":
        return 1
    return 0


def cmd_verify(args) -> int:
    VD = _load("vlab_delivery")
    r = VD.verify_delivery(args.delivery)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    if r["status"] == "DELIVERY_INTACT":
        return 0
    if r["status"] == "DELIVERY_TAMPERED":
        return 2
    return 4


def cmd_selftest(args) -> int:
    path = os.path.join(ROOT, "scripts", "check_vlab_gates.py")
    spec = importlib.util.spec_from_file_location("_vlab_gates", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.main(["--quiet"] if args.quiet else [])


def cmd_serve(args) -> int:
    """起服务。实现体在 engine/vlab_server.py —— CLI 只转发，不复制逻辑。"""
    path = os.path.join(ENGINE, "vlab_server.py")
    spec = importlib.util.spec_from_file_location("_cli_vlab_server", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    argv = ["--host", args.host, "--port", str(args.port)]
    if args.jobs_dir:
        argv += ["--jobs-dir", args.jobs_dir]
    if args.log_file:
        argv += ["--log-file", args.log_file]
    if args.token:
        argv += ["--token", args.token]
    if args.print_token:
        argv += ["--print-token"]
    return mod.main(argv)


def cmd_selftest_server(args) -> int:
    path = os.path.join(ROOT, "scripts", "check_vlab_server.py")
    spec = importlib.util.spec_from_file_location("_vlab_server_gate", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.main(["--quiet"] if args.quiet else [])


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="vlab", description="SwarmLabs comprehensive virtual lab (VLab) CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("schema", help="print the work-order contract").set_defaults(
        func=cmd_schema)

    d = sub.add_parser("disciplines", help="list discipline onboarding status")
    d.add_argument("--json", action="store_true")
    d.set_defaults(func=cmd_disciplines)

    c = sub.add_parser("checklist", help="onboarding checklist for a category")
    c.add_argument("category", nargs="?", default="")
    c.set_defaults(func=cmd_checklist)

    i = sub.add_parser("intake", help="validate a work order only")
    i.add_argument("work_order")
    i.set_defaults(func=cmd_intake)

    r = sub.add_parser("run", help="run the full pipeline on a work order")
    r.add_argument("work_order")
    r.add_argument("--out", default=None)
    r.add_argument("--package", action="store_true",
                   help="also build a V&V archival package")
    r.add_argument("--json", action="store_true")
    r.set_defaults(func=cmd_run)

    v = sub.add_parser("verify", help="offline re-verify a delivery.json")
    v.add_argument("delivery")
    v.set_defaults(func=cmd_verify)

    st = sub.add_parser("selftest", help="negative self-tests (fail-closed)")
    st.add_argument("--quiet", action="store_true")
    st.set_defaults(func=cmd_selftest)

    sv = sub.add_parser("serve", help="run the zero-dependency HTTP service")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8790)
    sv.add_argument("--jobs-dir", default=None)
    sv.add_argument("--log-file", default=None)
    sv.add_argument("--token", default=None)
    sv.add_argument("--print-token", action="store_true")
    sv.set_defaults(func=cmd_serve)

    ss = sub.add_parser("selftest-server",
                        help="negative self-tests for the service layer")
    ss.add_argument("--quiet", action="store_true")
    ss.set_defaults(func=cmd_selftest_server)
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
