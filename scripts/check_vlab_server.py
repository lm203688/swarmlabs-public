#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VLab 服务层否定性门禁 —— 证明这些闸门**真的会拦**。

纪律（与 `check_vlab_gates.py` / `vv-package-gate` 同源）：
一个永远回 200 的服务，和一个永远说 intact 的校验器一样，等于不存在。
所以这里几乎每条断言都是**否定性**的：构造一个应当被拒的请求，
断言它**确实**被拒，且拒的理由对得上。

只用 stdlib。CI 里刻意不装 numpy（`python -S`），用来检出偷依赖。

    python -S scripts/check_vlab_server.py            # 全量
    python -S scripts/check_vlab_server.py --quiet
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import threading
import urllib.error
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
ENGINE = os.path.join(ROOT, "engine")

# 以文件路径加载 engine/vlab_* 时，verification_gate.py:49 顶层绝对导入
# `from engine.ablation_verdict` 要求仓库根在 sys.path（与 scripts/vlab.py、
# scripts/fault_injection_check.py、scripts/check_vlab_gates.py 同一先例）。
# 缺了会 ModuleNotFoundError: No module named 'engine'（ci-cd vlab-gate
# 「服务层」步骤实测红）。python -S 下 sys.path.insert 仍有效。
sys.path.insert(0, ROOT)


def _load(alias: str, path: str):
    spec = importlib.util.spec_from_file_location(alias, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


SV = _load("_vlab_server", os.path.join(ENGINE, "vlab_server.py"))
C = _load("_vlab_contract", os.path.join(ENGINE, "vlab_contract.py"))
O = _load("_vlab_orchestrator", os.path.join(ENGINE, "vlab_orchestrator.py"))

TOKEN = "selftest-token-do-not-use-in-prod"


class R:
    passed = 0
    failed = 0
    quiet = False

    @classmethod
    def check(cls, name: str, cond: bool, detail: str = ""):
        if cond:
            cls.passed += 1
            if not cls.quiet:
                print(f"  PASS  {name}")
        else:
            cls.failed += 1
            print(f"  FAIL  {name}" + (f"\n        -> {detail}" if detail else ""))


def _req(base: str, method: str, path: str, body=None, token: str = None,
         raw: bytes = None, caller: str = "selftest"):
    url = base + path
    data = raw if raw is not None else (
        json.dumps(body).encode("utf-8") if body is not None else None)
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("content-type", "application/json")
    req.add_header("x-vlab-caller", caller)
    if token:
        req.add_header("authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode("utf-8")), dict(r.headers)
    except urllib.error.HTTPError as e:
        payload = e.read().decode("utf-8")
        try:
            payload = json.loads(payload)
        except Exception:
            pass  # noqa: silent-ok — Exception 可安全忽略（已审计 2026-10-08）
        return e.code, payload, dict(e.headers)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    R.quiet = args.quiet

    tmp = tempfile.mkdtemp(prefix="vlab-srv-")
    jobs = os.path.join(tmp, "jobs")
    logf = os.path.join(tmp, "calls.jsonl")

    cfg = SV.Config("127.0.0.1", 0, jobs, logf, TOKEN, "--token")
    httpd = SV.serve(cfg)
    port = httpd.server_address[1]
    base = f"http://127.0.0.1:{port}"
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()

    try:
        good = C.schema_doc()["example"]

        # ---------------------------------------------------------- 公开读面
        st, b, _ = _req(base, "GET", "/vlab/health")
        R.check("P1 /vlab/health 200 且声明 stdlib_only",
                st == 200 and b.get("stdlib_only") is True, f"{st} {b}")
        R.check("P2 health 暴露账本口径（不是硬编码）",
                # 2026-10-18: 原断言硬编码 total==82（旧口径），账本扩到 95 后误报。
                # 改为与服务器同源的 S.load_gate().load_ledger() 动态比对——
                # 这才是「不是硬编码」的本意；额外要求 total>0 防账本缺失时
                # 两侧皆 None 的虚空通过（fail-closed）。
                (lambda exp: (b.get("ledger") or {}).get("total") == exp.get("total")
                     and (b.get("ledger") or {}).get("counts") == exp.get("counts")
                     and (b.get("ledger") or {}).get("generated_at_utc") == exp.get("generated_at_utc")
                     and isinstance(exp.get("total"), int) and exp.get("total") > 0
                )(SV.S.load_gate().load_ledger() or {}),
                json.dumps(b.get("ledger"), ensure_ascii=False))
        R.check("P3 health 明示未知路由语义为 404（不是 200 兜底）",
                "404" in str((b.get("policy") or {}).get("unknown_route")), str(b.get("policy")))

        st, b, _ = _req(base, "GET", "/vlab/schema")
        R.check("P4 /vlab/schema 200 且带 example",
                st == 200 and isinstance(b.get("example"), dict), str(st))

        st, b, _ = _req(base, "GET", "/vlab/disciplines")
        vals = [r for r in b.get("rows", []) if r.get("status") == "VALIDATED"]
        R.check("P5 /vlab/disciplines 200 且 VALIDATED 项都有真 gold",
                st == 200 and all(r.get("n_gold_resolved", 0) > 0 for r in vals),
                f"validated={[r['name'] for r in vals]}")

        st, b, _ = _req(base, "GET", "/vlab/openapi.json")
        R.check("P6 openapi 就地生成且收录写端点",
                st == 200 and "/vlab/run" in (b.get("paths") or {}), str(st))

        # ------------------------------------------------- 否定性：未知路由
        st, b, _ = _req(base, "GET", "/vlab/nope")
        R.check("N1 未知 GET 必须 404（绝不 200 兜底）",
                st == 404, f"got {st} —— 200-fallback 是最贵的假绿")
        st, b, _ = _req(base, "POST", "/vlab/nope", body={}, token=TOKEN)
        R.check("N2 未知 POST 必须 404", st == 404, f"got {st}")
        st, b, _ = _req(base, "GET", "/vlab/jobs/does-not-exist")
        R.check("N3 不存在的 job 必须 404", st == 404, f"got {st}")

        # ------------------------------------------------- 否定性：鉴权
        st, b, _ = _req(base, "POST", "/vlab/intake", body=good)
        R.check("N4 无 token 的写操作必须 401", st == 401, f"got {st}")
        st, b, _ = _req(base, "POST", "/vlab/intake", body=good, token="wrong")
        R.check("N5 错 token 必须 401", st == 401, f"got {st}")
        st, _, h = _req(base, "POST", "/vlab/intake", body=good)
        R.check("N6 401 必须带 WWW-Authenticate",
                "www-authenticate" in {k.lower() for k in h}, str(h.keys()))

        # ------------------------------------------------- 否定性：坏请求体
        st, b, _ = _req(base, "POST", "/vlab/intake", raw=b"{not json", token=TOKEN)
        R.check("N7 坏 JSON 必须 400", st == 400, f"got {st}")
        st, b, _ = _req(base, "POST", "/vlab/intake", raw=b"", token=TOKEN)
        R.check("N8 空 body 必须 400", st == 400, f"got {st}")

        # -------------------------------------------- 否定性：契约层拒收
        bad = json.loads(json.dumps(good))
        bad.pop("discipline")
        st, b, _ = _req(base, "POST", "/vlab/intake", body=bad, token=TOKEN)
        R.check("N9 缺 discipline 必须 422（fail closed）",
                st == 422 and b.get("accepted") is False, f"{st} {b}")

        inv = json.loads(json.dumps(good))
        inv["corroboration"]["as_input"] = True
        st, b, _ = _req(base, "POST", "/vlab/intake", body=inv, token=TOKEN)
        codes = [e.get("code") for e in (b.get("errors") or [])]
        R.check("N10 把真实数据当前置输入的写法必须 422 且码为 ORDER_INVERTED",
                st == 422 and "ORDER_INVERTED" in codes, f"{st} codes={codes}")

        st, b, _ = _req(base, "POST", "/vlab/run", body=inv, token=TOKEN)
        R.check("N11 /vlab/run 同样拒绝倒置写法（不经 intake 直跑也不行）",
                st == 422 and b.get("verdict") is None, f"{st} verdict={b.get('verdict')}")

        unsafe = json.loads(json.dumps(good))
        unsafe["work_order_id"] = "../../etc/passwd"
        st, b, _ = _req(base, "POST", "/vlab/run", body=unsafe, token=TOKEN)
        R.check("N12 job_id 目录穿越必须被挡（422，不落盘）",
                st == 422, f"{st} {b}")

        # ------------------------------------------ 正向：走已有内核出裁决
        st, b, _ = _req(base, "POST", "/vlab/run", body=good, token=TOKEN)
        R.check("P7 内核场景跑通且给出裁决与 gate",
                st == 200 and b.get("outcome") == "PROCEED"
                and b.get("gate") and b.get("hard_verdict"), f"{st} {b.get('outcome')}")
        R.check("P8 交付摘要存在且 job 已落盘",
                bool(b.get("delivery_digest")) and bool(b.get("job_file"))
                and os.path.exists(os.path.normpath(
                    os.path.join(ROOT, b.get("job_file", "")))),
                str(b.get("job_file")))
        jid = b.get("work_order_id")

        st, b2, _ = _req(base, "GET", f"/vlab/jobs/{jid}")
        R.check("P9 GET /vlab/jobs/{id} 回读同一 job",
                st == 200 and b2.get("work_order_id") == jid, f"{st}")

        # --------------------------------- 否定性：未接入专业不得给裁决
        pending = json.loads(json.dumps(good))
        pending["discipline"] = "Chemistry"
        pending["scenario_key"] = ""
        pending["work_order_id"] = "w3-chemistry-onboarding-server"
        st, b, _ = _req(base, "POST", "/vlab/run", body=pending, token=TOKEN)
        R.check("N13 未接入专业必须 NEEDS_ONBOARDING 且 verdict 为 null",
                st == 200 and b.get("outcome") == "NEEDS_ONBOARDING"
                and b.get("verdict") is None and b.get("gate") is None,
                f"{st} outcome={b.get('outcome')} verdict={b.get('verdict')}")

        # ---------------------------------------- 否定性：交付篡改必被捕获
        st, dv, _ = _req(base, "GET", f"/vlab/jobs/{jid}/delivery")
        R.check("P10 GET delivery 200", st == 200 and isinstance(dv, dict), str(st))
        st, b, _ = _req(base, "POST", "/vlab/verify", body={"job_id": jid}, token=TOKEN)
        R.check("P11 真交付复验 = DELIVERY_INTACT",
                st == 200 and b.get("status") == "DELIVERY_INTACT", f"{st} {b.get('status')}")

        tampered = json.loads(json.dumps(dv))
        # 注入的篡改**必须保证真的改变了字节**。上一版这里写死改成 "PASS"，
        # 而该场景真实值本来就是 PASS ⇒ 篡改成了空操作，测试假绿（本轮实测踩到）。
        cur = (tampered.get("verdict") or {}).get("hard_verdict")
        tampered["verdict"]["hard_verdict"] = ("MARGINAL" if cur != "MARGINAL"
                                               else "PASS")
        R.check("P0 篡改注入自检：确实改变了内容（否则下面这条是假绿）",
                tampered["verdict"]["hard_verdict"] != cur,
                f"cur={cur!r} -> {tampered['verdict']['hard_verdict']!r}")
        st, b, _ = _req(base, "POST", "/vlab/verify", body=tampered, token=TOKEN)
        R.check("N14 改一字段的交付必须 TAMPERED 且 HTTP 409",
                st == 409 and b.get("status") == "DELIVERY_TAMPERED",
                f"{st} {b.get('status')} (flipped {cur!r} -> "
                f"{tampered['verdict']['hard_verdict']!r})")

        st, b, _ = _req(base, "POST", "/vlab/verify",
                        body={"job_id": "no-such-job"}, token=TOKEN)
        R.check("N15 不存在的 job 复验必须 404（不是「intact」）",
                st == 404, f"got {st}")

        # -------------------------------------------------- 调用留痕（非空）
        calls = []
        if os.path.exists(logf):
            with open(logf, "r", encoding="utf-8") as f:
                calls = [json.loads(x) for x in f if x.strip()]
        has_401 = any(c.get("status") == 401 for c in calls)
        has_409 = any(c.get("status") == 409 for c in calls)
        R.check("P12 每次调用都留痕（含被拒的 401/409）",
                bool(calls) and has_401 and has_409,
                f"n_calls={len(calls)} has_401={has_401} has_409={has_409}")
        R.check("P13 留痕含调用方标识与 request id",
                all(c.get("caller") and c.get("request_id") for c in calls[-5:]),
                str(calls[-1] if calls else None))

        # --------------------------------------- 非回环 + 无 token 必须拒启
        refused = False
        try:
            SV.build_config(type("A", (), {
                "host": "0.0.0.0", "port": 0, "jobs_dir": jobs,
                "log_file": logf, "token": None, "print_token": False})())
        except SystemExit:
            refused = True
        except Exception:
            refused = True
        R.check("N16 非回环绑定且无 token 必须拒绝启动（不静默裸奔）",
                refused, "server agreed to bind 0.0.0.0 without a token")

    finally:
        httpd.shutdown()
        httpd.server_close()
        shutil.rmtree(tmp, ignore_errors=True)

    total = R.passed + R.failed
    print(f"\nVLab server gate: {R.passed}/{total} passed, {R.failed} failed"
          + ("  (STDLIB_ONLY)" if os.environ.get("VLAB_SERVER_GATE_STDLIB") else ""))
    return 1 if R.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
