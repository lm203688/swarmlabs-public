"""全链路 Trace 与审计日志。

对应 AgentLoop 飞轮的第一步 **Collect**：任何一次 Agent/Skill 调用都会落一条
span；任何一次权限判定、审批、写操作、回滚都会落一条 audit 事件。

两条流分开写，原因很实际：
- trace.jsonl 面向"性能与行为分析"（谁调了什么、耗时多少、成没成）；
- audit.jsonl 面向"合规与追责"（谁在什么权限下改了什么、有没有人批准、能否回滚）。
混在一起会让审计噪声淹没在调试日志里。
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Optional

from .contracts import new_id


def _digest(obj: Any, limit: int = 400) -> str:
    """把任意对象压成短摘要写进 trace——避免把大数组灌进日志。"""
    try:
        s = json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        s = str(obj)
    return s if len(s) <= limit else s[:limit] + f"...<truncated {len(s)} chars>"


class Span:
    def __init__(self, tracer: "Tracer", name: str, kind: str, parent: Optional[str],
                 attrs: Dict[str, Any]):
        self.tracer = tracer
        self.span_id = new_id("span")
        self.name = name
        self.kind = kind
        self.parent = parent
        self.attrs = attrs
        self.t0 = time.time()
        self.status = "ok"
        self.error = ""
        self.output = None

    def set_output(self, output: Any):
        self.output = output

    def __enter__(self):
        self.tracer._stack.append(self.span_id)
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc is not None:
            # 已被权限闸门标记为 blocked 的，保留 blocked 语义——
            # "被策略拒绝"和"代码出错"在审计上是两回事，不能混为一谈。
            if self.status != "blocked":
                self.status = "error"
            self.error = f"{exc_type.__name__}: {exc}"
        self.tracer._stack.pop()
        self.tracer._write_span(self)
        return False


class Tracer:
    def __init__(self, run_dir: str, run_id: str):
        self.run_dir = run_dir
        self.run_id = run_id
        os.makedirs(run_dir, exist_ok=True)
        self.trace_path = os.path.join(run_dir, "trace.jsonl")
        self.audit_path = os.path.join(run_dir, "audit.jsonl")
        self._stack = []
        self.span_count = 0
        self.audit_count = 0

    # -- span -------------------------------------------------------------
    def span(self, name: str, kind: str = "internal", **attrs) -> Span:
        parent = self._stack[-1] if self._stack else None
        return Span(self, name, kind, parent, attrs)

    def _write_span(self, sp: Span):
        rec = {
            "ts": round(time.time(), 3),
            "run_id": self.run_id,
            "span_id": sp.span_id,
            "parent_id": sp.parent,
            "name": sp.name,
            "kind": sp.kind,             # agent / skill / gate / loop
            "status": sp.status,
            "latency_ms": int((time.time() - sp.t0) * 1000),
            "attrs": {k: _digest(v) for k, v in sp.attrs.items()},
            "output": _digest(sp.output) if sp.output is not None else None,
            "error": sp.error,
        }
        self.span_count += 1
        with open(self.trace_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # -- audit ------------------------------------------------------------
    def audit(self, event: str, actor: str, action: str, decision: str,
              detail: Dict[str, Any] = None, rollback_ref: str = ""):
        rec = {
            "ts": round(time.time(), 3),
            "run_id": self.run_id,
            "event": event,             # policy_check / approval / write / rollback
            "actor": actor,             # 角色或 skill 名
            "action": action,
            "decision": decision,       # allow / deny / pending / executed / restored
            "detail": detail or {},
            "rollback_ref": rollback_ref,
        }
        self.audit_count += 1
        with open(self.audit_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    # -- collect（AgentLoop 第一步）----------------------------------------
    def collect(self):
        spans, audits = [], []
        if os.path.exists(self.trace_path):
            with open(self.trace_path, encoding="utf-8") as f:
                spans = [json.loads(l) for l in f if l.strip()]
        if os.path.exists(self.audit_path):
            with open(self.audit_path, encoding="utf-8") as f:
                audits = [json.loads(l) for l in f if l.strip()]
        return spans, audits
