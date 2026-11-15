#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SwarmLabs 综合虚拟实验室 —— 服务入口（零依赖 HTTP）。

为什么需要这一层
----------------
VLab 流水线（`vlab_orchestrator`）此前只有 CLI：能跑，但**不是一个服务**。
商业模式是「把一个需要验证的实验发过来」，发过来需要**网络入口**。
这一层只做三件事：鉴权、路由、落盘 —— 流水线逻辑一行都不复制，
全部委托 `vlab_orchestrator.run_job()`（单一真源）。

刻意不依赖任何东西
------------------
纯 stdlib。CF Pages Function 跑不了 Python，所以这一层**不部署在边缘**，
而是作为受控进程运行（本机 / ECS）。与 `engine-kit/vv_gate_server.py`
同一纪律：**跑不起来的门禁等于没有门禁**。

三条不可让步的失败语义
----------------------
1. **未知路由必须 404**，绝不返回 200 兜底（"200 即成功"是最贵的假绿）。
2. **未接入专业不得给出裁决**：返回 200 但 `verdict: null` + `NEEDS_ONBOARDING`
   + onboarding 清单。给出裁决才是失败。
3. **非回环地址绑定且未配置 token ⇒ 拒绝启动**（fail closed，不静默裸奔）。

用法
----
    python engine/vlab_server.py --host 127.0.0.1 --port 8790
    python engine/vlab_server.py --host 127.0.0.1 --port 8790 --print-token

    curl -s localhost:8790/vlab/health
    curl -s -X POST localhost:8790/vlab/run \\
         -H 'content-type: application/json' \\
         -H 'authorization: Bearer <token>' \\
         -d @data/vlab/work-orders/w3-microbio-ecoli.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import secrets
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

_HERE = os.path.dirname(os.path.abspath(__file__))


def _sibling(name: str):
    spec = importlib.util.spec_from_file_location(
        f"_vlab_{name}", os.path.join(_HERE, f"{name}.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


S = _sibling("vlab_stdlib")
C = _sibling("vlab_contract")
DR = _sibling("discipline_registry")
O = _sibling("vlab_orchestrator")
VD = _sibling("vlab_delivery")

SERVICE = "swarmlabs-vlab-server"
VERSION = "1.0.0"
STDLIB_ONLY = True

ROOT = S.ROOT
DEFAULT_JOBS_DIR = os.path.join(ROOT, "data", "vlab", "jobs")
DEFAULT_LOG_FILE = os.path.join(ROOT, "data", "vlab", "calls.jsonl")
TOKEN_FILE = os.path.join(ROOT, "data", "vlab", "server_token")

MAX_BODY = 4 << 20  # 4 MiB 上限：工作单本来就该很小，超了说明发错东西了

#: 需要鉴权的写操作（读操作公开：schema / disciplines / health 是宣传面）
PROTECTED = {("POST", "/vlab/intake"), ("POST", "/vlab/run"),
             ("POST", "/vlab/verify")}

_LOG_LOCK = threading.Lock()


# --------------------------------------------------------------------------- 配置
class Config:
    def __init__(self, host: str, port: int, jobs_dir: str, log_file: str,
                 token: Optional[str], token_source: str):
        self.host = host
        self.port = port
        self.jobs_dir = jobs_dir
        self.log_file = log_file
        self.token = token
        self.token_source = token_source

    @property
    def auth_enabled(self) -> bool:
        return bool(self.token)

    @property
    def loopback(self) -> bool:
        return self.host in ("127.0.0.1", "::1", "localhost")


def _resolve_token(explicit: Optional[str]) -> Tuple[Optional[str], str]:
    if explicit:
        return explicit, "--token"
    env = os.environ.get("SWARMLABS_VLAB_TOKEN")
    if env:
        return env.strip(), "env:SWARMLABS_VLAB_TOKEN"
    if os.path.exists(TOKEN_FILE):
        try:
            with open(TOKEN_FILE, "r", encoding="utf-8") as f:
                t = f.read().strip()
            if t:
                return t, f"file:{os.path.relpath(TOKEN_FILE, ROOT)}"
        except Exception:
            pass  # noqa: silent-ok — Exception 可安全忽略（已审计 2026-10-08）
    return None, "none"


def _persist_token(tok: str) -> str:
    os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
    with open(TOKEN_FILE, "w", encoding="utf-8", newline="\n") as f:
        f.write(tok + "\n")
    return TOKEN_FILE


# --------------------------------------------------------------------------- 工具
def _job_dir(cfg: Config, job_id: str) -> str:
    return os.path.join(cfg.jobs_dir, job_id)


def _safe_job_id(job_id: str) -> bool:
    """job_id 直接拼进路径 —— 必须挡住 `../` 之类的目录穿越。"""
    if not job_id or len(job_id) > 128:
        return False
    return all(ch.isalnum() or ch in "-_.:" for ch in job_id) \
        and ".." not in job_id


def _list_jobs(cfg: Config, limit: int = 50) -> list:
    if not os.path.isdir(cfg.jobs_dir):
        return []
    out = []
    for name in sorted(os.listdir(cfg.jobs_dir), reverse=True):
        p = os.path.join(cfg.jobs_dir, name, "job.json")
        if not os.path.exists(p):
            continue
        try:
            j = S.read_json(p) or {}
        except Exception:
            continue
        out.append({
            "work_order_id": name,
            "outcome": j.get("outcome"),
            "generated_at_utc": j.get("generated_at_utc"),
            "hard_verdict": ((j.get("verdict") or {}) or {}).get("hard_verdict"),
            "gate": ((j.get("verdict") or {}) or {}).get("gate"),
        })
        if len(out) >= limit:
            break
    return out


def _append_call(cfg: Config, rec: dict) -> None:
    """调用记录 —— 这是「有没有人真的来用」的唯一凭据，必须留痕。"""
    try:
        os.makedirs(os.path.dirname(cfg.log_file), exist_ok=True)
        with _LOG_LOCK:
            with open(cfg.log_file, "a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception:
        pass  # 记日志失败不得影响服务本身


def _openapi() -> dict:
    """就地生成：不落静态文件 ⇒ 不会与实现漂移。"""
    order = C.schema_doc()["example"]
    return {
        "openapi": "3.0.3",
        "info": {"title": "SwarmLabs VLab service", "version": VERSION,
                 "description": ("Submit a validation work order, get a verdict "
                                 "with honest uncertainty. Stdlib-only; "
                                 "corroboration data is post-hoc by construction.")},
        "servers": [{"url": "/"}],
        "paths": {
            "/vlab/health": {"get": {"summary": "liveness + honesty flags",
                                     "responses": {"200": {"description": "ok"}}}},
            "/vlab/schema": {"get": {"summary": "work-order schema + example",
                                     "responses": {"200": {"description": "ok"}}}},
            "/vlab/disciplines": {"get": {"summary": "discipline onboarding status",
                                          "responses": {"200": {"description": "ok"}}}},
            "/vlab/intake": {"post": {
                "summary": "validate a work order WITHOUT running it",
                "security": [{"bearerAuth": []}],
                "requestBody": {"required": True, "content": {
                    "application/json": {"schema": {"type": "object", "example": order}}}},
                "responses": {"200": {"description": "accepted"},
                              "422": {"description": "rejected (fail closed)"},
                              "400": {"description": "malformed body"}}}},
            "/vlab/run": {"post": {
                "summary": "run the full pipeline and persist the delivery",
                "security": [{"bearerAuth": []}],
                "requestBody": {"required": True, "content": {
                    "application/json": {"schema": {"type": "object", "example": order}}}},
                "responses": {"200": {"description": "job finished (see outcome)"},
                              "422": {"description": "rejected at intake"},
                              "400": {"description": "malformed body"}}}},
            "/vlab/verify": {"post": {
                "summary": "verify a delivery offline-style (tamper detection)",
                "security": [{"bearerAuth": []}],
                "requestBody": {"required": True, "content": {
                    "application/json": {"schema": {"type": "object"}}}},
                "responses": {"200": {"description": "DELIVERY_INTACT"},
                              "409": {"description": "DELIVERY_TAMPERED"}}}},
            "/vlab/jobs": {"get": {"summary": "recent jobs",
                                   "responses": {"200": {"description": "ok"}}}},
            "/vlab/jobs/{id}": {"get": {"summary": "one job",
                                        "responses": {"200": {"description": "ok"},
                                                      "404": {"description": "not found"}}}},
        },
        "components": {"securitySchemes": {"bearerAuth": {
            "type": "http", "scheme": "bearer"}}},
    }


# --------------------------------------------------------------------------- 业务
def do_intake(body: Any) -> Tuple[int, dict]:
    v = C.validate_work_order(body if isinstance(body, dict) else {})
    if not v["ok"]:
        return 422, {"ok": False, "stage": "intake", "accepted": False,
                     "errors": v["errors"], "warnings": v["warnings"],
                     "digest": v["digest"]}
    return 200, {"ok": True, "stage": "intake", "accepted": True,
                 "errors": [], "warnings": v["warnings"], "digest": v["digest"],
                 "work_order_id": body.get("work_order_id")
                 or C.new_work_order_id(body)}


def do_run(cfg: Config, body: Any, caller: str, rid: str) -> Tuple[int, dict]:
    if not isinstance(body, dict):
        return 400, {"ok": False, "error": "body must be a JSON object"}
    wo = dict(body)
    wo.setdefault("work_order_id", C.new_work_order_id(wo))
    job_id = wo["work_order_id"]
    if not _safe_job_id(job_id):
        return 422, {"ok": False, "stage": "intake",
                     "error": "work_order_id contains unsafe characters"}

    out_dir = _job_dir(cfg, job_id)
    os.makedirs(out_dir, exist_ok=True)
    job = O.run_job(wo, out_dir=out_dir)

    outcome = job.get("outcome")
    verdict = job.get("verdict") or {}
    status = 200
    if outcome == O.REJECTED_AT_INTAKE:
        status = 422
    payload = {
        "ok": True,
        "work_order_id": job_id,
        "outcome": outcome,
        "verdict": job.get("verdict"),          # 未接入专业时为 null —— 这是设计
        "gate": verdict.get("gate"),
        "hard_verdict": verdict.get("hard_verdict"),
        "stages": job.get("stages"),
        "screening": job.get("screening"),
        "onboarding_checklist": job.get("onboarding_checklist"),
        "delivery": (job.get("delivery") or {}).get("json"),
        "delivery_digest": (job.get("delivery") or {}).get("digest"),
        "job_file": os.path.relpath(os.path.join(out_dir, "job.json"), ROOT),
        "request_id": rid, "caller": caller,
        "honest_boundaries": VD.VLAB_BOUNDARIES,
    }
    # 语义护栏：给出裁决就必须有 gate；没裁决就不许出现 gate
    if payload["verdict"] is None and payload["gate"] is not None:
        payload["ok"] = False
        payload["error"] = "internal invariant broken: verdict is null but gate is set"
        status = 500
    return status, payload


def do_verify(cfg: Config, body: Any) -> Tuple[int, dict]:
    doc = body
    if isinstance(body, dict) and body.get("job_id") and "schema" not in body:
        jid = body["job_id"]
        if not _safe_job_id(jid):
            return 422, {"ok": False, "error": "unsafe job_id"}
        p = os.path.join(_job_dir(cfg, jid), "delivery.json")
        if not os.path.exists(p):
            return 404, {"ok": False, "error": f"no delivery for job '{jid}'"}
        doc = S.read_json(p)
    if not isinstance(doc, dict):
        return 400, {"ok": False, "error": "expected a delivery object or {job_id}"}
    r = VD.verify_delivery(doc)
    status = 200 if r.get("status") == "DELIVERY_INTACT" else 409
    return status, dict(r, ok=(r.get("status") == "DELIVERY_INTACT"))


# --------------------------------------------------------------------------- HTTP
class _Handler(BaseHTTPRequestHandler):
    server_version = f"{SERVICE}/{VERSION}"
    protocol_version = "HTTP/1.1"
    cfg: Config = None  # type: ignore

    # -- 基础 ---------------------------------------------------------------
    def log_message(self, fmt, *args):  # 静音默认 stderr 噪声
        return

    def _send(self, status: int, obj: Any, rid: str, extra: Optional[dict] = None):
        body = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.send_header("x-vlab-request-id", rid)
        self.send_header("x-vlab-service", f"{SERVICE}/{VERSION}")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _caller(self) -> str:
        return (self.headers.get("x-vlab-caller")
                or self.headers.get("user-agent") or "anonymous")

    def _authed(self) -> bool:
        if not self.cfg.auth_enabled:
            return True
        h = self.headers.get("authorization") or ""
        if not h.lower().startswith("bearer "):
            return False
        return secrets.compare_digest(h[7:].strip(), self.cfg.token or "")

    def _body(self) -> Tuple[Optional[Any], Optional[Tuple[int, dict]]]:
        n = int(self.headers.get("content-length") or 0)
        if n > MAX_BODY:
            return None, (413, {"ok": False, "error": "body too large"})
        raw = self.rfile.read(n) if n else b""
        if not raw:
            return None, (400, {"ok": False, "error": "empty body"})
        try:
            return json.loads(raw.decode("utf-8")), None
        except Exception as e:
            return None, (400, {"ok": False, "error": f"malformed JSON: {e}"})

    def _record(self, status: int, payload: Any, rid: str):
        p = payload if isinstance(payload, dict) else {}
        _append_call(self.cfg, {
            "ts_utc": S.utc_now(), "request_id": rid, "service": SERVICE,
            "version": VERSION, "method": self.command,
            "path": urlparse(self.path).path, "caller": self._caller(),
            "status": status, "outcome": p.get("outcome"),
            "hard_verdict": p.get("hard_verdict"),
            "gate": p.get("gate"), "ok": p.get("ok"),
            "work_order_id": p.get("work_order_id"),
            "delivery_digest": p.get("delivery_digest"),
        })

    # -- 路由 ---------------------------------------------------------------
    def do_GET(self):
        rid = uuid.uuid4().hex[:16]
        path = urlparse(self.path).path.rstrip("/") or "/"
        try:
            if path == "/vlab/health":
                led = {}
                try:
                    led = (S.load_gate().load_ledger() or {})
                except Exception:
                    led = {}
                self._send(200, {
                    "ok": True, "service": SERVICE, "version": VERSION,
                    "stdlib_only": STDLIB_ONLY,
                    "auth": ("bearer" if self.cfg.auth_enabled
                             else "disabled (loopback only)"),
                    "auth_source": self.cfg.token_source,
                    "bind": f"{self.cfg.host}:{self.cfg.port}",
                    "ledger": {"generated_at_utc": led.get("generated_at_utc"),
                               "total": led.get("total"),
                               "counts": led.get("counts")},
                    "n_disciplines": DR.registry_summary()["n"],
                    "policy": {"corroboration_as_input": "rejected at contract level",
                               "unknown_route": "404 (never a 200 fallback)",
                               "verdict_without_ledger": "withheld (fail closed)"},
                }, rid)
                self._record(200, {"ok": True}, rid)
                return
            if path == "/vlab/schema":
                self._send(200, C.schema_doc(), rid)
                self._record(200, {}, rid)
                return
            if path == "/vlab/disciplines":
                self._send(200, DR.registry_summary(), rid)
                self._record(200, {}, rid)
                return
            if path == "/vlab/openapi.json":
                self._send(200, _openapi(), rid)
                self._record(200, {}, rid)
                return
            if path == "/vlab/jobs":
                self._send(200, {"ok": True, "jobs": _list_jobs(self.cfg)}, rid)
                self._record(200, {}, rid)
                return
            if path.startswith("/vlab/jobs/"):
                rest = path[len("/vlab/jobs/"):]
                parts = [x for x in rest.split("/") if x]
                if not parts or not _safe_job_id(parts[0]):
                    self._send(404, {"ok": False, "error": "not found"}, rid)
                    self._record(404, {}, rid)
                    return
                jid = parts[0]
                d = _job_dir(self.cfg, jid)
                if len(parts) == 1:
                    p = os.path.join(d, "job.json")
                elif len(parts) == 2 and parts[1] == "delivery":
                    p = os.path.join(d, "delivery.json")
                else:
                    self._send(404, {"ok": False, "error": "not found"}, rid)
                    self._record(404, {}, rid)
                    return
                if not os.path.exists(p):
                    self._send(404, {"ok": False,
                                     "error": f"no such job/artifact: {jid}"}, rid)
                    self._record(404, {}, rid)
                    return
                doc = S.read_json(p)
                self._send(200, doc, rid)
                self._record(200, {}, rid)
                return
            # 未命中：必须 404，绝不 200 兜底
            self._send(404, {"ok": False, "error": "not found",
                             "hint": "see /vlab/openapi.json"}, rid)
            self._record(404, {}, rid)
        except Exception as e:  # 兜底：服务不因单个请求崩
            self._send(500, {"ok": False, "error": f"{type(e).__name__}: {e}"}, rid)
            self._record(500, {}, rid)

    def do_POST(self):
        rid = uuid.uuid4().hex[:16]
        path = urlparse(self.path).path.rstrip("/") or "/"
        try:
            if (self.command, path) in PROTECTED and not self._authed():
                self._send(401, {"ok": False, "error": "unauthorized",
                                 "hint": "Authorization: Bearer <token>"}, rid,
                           extra={"www-authenticate": "Bearer"})
                self._record(401, {}, rid)
                return
            body, err = self._body()
            if err:
                self._send(err[0], err[1], rid)
                self._record(err[0], err[1], rid)
                return
            if path == "/vlab/intake":
                st, pl = do_intake(body)
            elif path == "/vlab/run":
                st, pl = do_run(self.cfg, body, self._caller(), rid)
            elif path == "/vlab/verify":
                st, pl = do_verify(self.cfg, body)
            else:
                self._send(404, {"ok": False, "error": "not found"}, rid)
                self._record(404, {}, rid)
                return
            self._send(st, pl, rid)
            self._record(st, pl, rid)
        except Exception as e:
            self._send(500, {"ok": False, "error": f"{type(e).__name__}: {e}"}, rid)
            self._record(500, {}, rid)

    do_HEAD = do_GET


# --------------------------------------------------------------------------- 启动
def build_config(args) -> Config:
    tok, src = _resolve_token(args.token)
    if args.print_token and not tok:
        tok, src = secrets.token_urlsafe(24), "generated"
        _persist_token(tok)
        src = f"file:{os.path.relpath(TOKEN_FILE, ROOT)}"
    cfg = Config(args.host, args.port,
                 os.path.abspath(args.jobs_dir),
                 os.path.abspath(args.log_file), tok, src)
    if not cfg.loopback and not cfg.auth_enabled:
        raise SystemExit(
            "[REFUSED] binding to a non-loopback address with no token would "
            "expose the pipeline unauthenticated. Set SWARMLABS_VLAB_TOKEN, "
            "or pass --token / --print-token (writes data/vlab/server_token)."
        )
    return cfg


def serve(cfg: Config) -> ThreadingHTTPServer:
    handler = type("_BoundHandler", (_Handler,), {"cfg": cfg})
    httpd = ThreadingHTTPServer((cfg.host, cfg.port), handler)
    httpd.daemon_threads = True
    return httpd


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="SwarmLabs VLab HTTP service")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--jobs-dir", default=DEFAULT_JOBS_DIR)
    ap.add_argument("--log-file", default=DEFAULT_LOG_FILE)
    ap.add_argument("--token", default=None)
    ap.add_argument("--print-token", action="store_true",
                    help="generate + persist a token if none is configured")
    args = ap.parse_args(argv)

    cfg = build_config(args)
    httpd = serve(cfg)
    print(f"[{SERVICE}/{VERSION}] listening on http://{cfg.host}:{cfg.port}")
    print(f"  stdlib_only={STDLIB_ONLY}  auth={cfg.auth_enabled} "
          f"({cfg.token_source})  loopback={cfg.loopback}")
    print(f"  jobs -> {os.path.relpath(cfg.jobs_dir, ROOT)}")
    print(f"  calls-> {os.path.relpath(cfg.log_file, ROOT)}")
    sys.stdout.flush()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass  # noqa: silent-ok — KeyboardInterrupt 可安全忽略（已审计 2026-10-08）
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
