#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VLab 交付契约 —— 让「一次验证」能被第三方**离线**核对，而非只能相信。

为什么需要它
------------
`docs/feasibility-virtual-lab-2026-09-21.md` §6 把「交付物可独立复验」列为
商业模式成立的**第三个前提**：

    客户/审方能离线核对——这是「验证服务」的信任地基。

一次验证如果只能以"我们的网页上写着 PASS"的形式交付，那它就还是"请信任
我们"。本模块把一次流水线的全部关键事实打成**自校验**的 delivery.json：

  - 交付物的每个语义块都在一个规范化摘要里（改一字节即失效）；
  - 裁决字段直接搬自 `engine/verification_gate.py`（不重算、不发明阈值）；
  - 诚实边界写进交付物本身，不靠对方来问；
  - 复验步骤写成可执行命令，第三方无需我们的账号 / 网络 / numpy。

红线
----
- **不重算裁决**。本模块搬运引擎已签发的 verdict/gate；`verify_delivery()`
  校验的是**文件没被动过**，不是"判定是对的"。这两件事必须分开说。
- **corroboration 不得出现在裁决里**。交付物里 corroboration 是独立区块，
  且带 `affects_verdict: false`——它只增信，不改判。

依赖：仅标准库（含同目录 `vlab_stdlib.py`，按路径加载）。
"""

from __future__ import annotations

import importlib.util
import os
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))


def _sibling(name: str):
    spec = importlib.util.spec_from_file_location(
        f"_vlab_{name}", os.path.join(_HERE, f"{name}.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


S = _sibling("vlab_stdlib")

SCHEMA = "swarmlabs-vlab-delivery/1.0"
_KEEP = object()  # 占位：无

#: 交付层自有的诚实边界（与 vv_package 的边界互补，不重复抄）
VLAB_BOUNDARIES = [
    "This delivery states whether a claim is VALIDATABLE under our gate — "
    "it does not state whether the underlying scientific claim is true.",
    "The verdict was produced by the virtual experiment BEFORE any client "
    "data was consulted. Corroboration points are post-hoc only and are "
    "structurally prevented from reaching the build stage "
    "(see stage_records[build].inputs_digest and the contract red line).",
    "Corroboration screening is mechanical (citation checkability, unit "
    "dimensionality, plausibility band) plus a named human confirmer. It does "
    "NOT judge whether the cited science is correct. An unconfirmed reference "
    "can never be ACCEPTED.",
    "If a discipline has no verifiable gold source or no ledger-backed kernel "
    "scenario, the honest outcome is NEEDS_ONBOARDING — not a verdict.",
]


def _package_boundaries() -> List[str]:
    """复用 vv_package 的诚实边界（单一来源，不在此另抄一份）。"""
    try:
        vp = S.load_module_by_path(
            "_vlab_vvpkg", os.path.join(S.ENGINE_DIR, "vv_package.py")
        )
        return list(getattr(vp, "HONEST_BOUNDARIES", []) or [])
    except Exception:
        return []


def _gate_bundle() -> Dict:
    """从唯一真源搬阈值 / 策略 / 指纹。搬不到 → 空 dict（不发明默认值）。"""
    try:
        g = S.load_gate()
        return {
            "thresholds": g.thresholds(),
            "policy_source": "engine/verification_gate.py::thresholds()",
            "engine": g.engine_fingerprint(),
        }
    except Exception as e:                       # pragma: no cover
        return {"thresholds": None,
                "policy_source": None,
                "engine": None,
                "error": str(e)}


def _verification_instructions(has_package: bool) -> List[dict]:
    steps = [
        {"step": 1,
         "action": "re-verify this delivery is unmodified",
         "command": "python scripts/vlab.py verify <this-file>.json",
         "expect": "DELIVERY_INTACT"},
        {"step": 2,
         "action": "confirm the verdict comes from the gate single source",
         "command": "python scripts/vlab.py schema",
         "expect": "thresholds.policy_source == engine/verification_gate.py::thresholds()"},
        {"step": 3,
         "action": "confirm the engine revision the verdict was bound to",
         "command": "read delivery.engine.all_digest and compare with the repo "
                    "at the commit in delivery.engine.git_commit",
         "expect": "identical all_digest, or the verdict is stale and must be recomputed"},
    ]
    if has_package:
        steps.append({
            "step": 4,
            "action": "independently verify the archival evidence package",
            "command": "python scripts/build_vv_package.py verify <package-dir>",
            "expect": "PACKAGE_INTACT (tampering any byte yields TAMPERED, exit 2)"})
    return steps


def build_delivery(job: dict) -> dict:
    """把一次 job 的完整事实打成交付物（含规范化摘要）。"""
    gb = _gate_bundle()
    work = job.get("work_order_summary") or {}
    payload: Dict[str, Any] = {
        "delivery_schema": SCHEMA,
        "generated_at_utc": job.get("generated_at_utc"),
        "work_order_id": job.get("work_order_id"),
        "work_order_digest": job.get("work_order_digest"),
        "submitted_by": work.get("submitted_by"),
        "discipline": work.get("discipline"),
        "goal": work.get("goal"),
        "outcome": job.get("outcome"),
        "stage_records": job.get("stages") or [],
        "verdict": job.get("verdict") or {},
        "verdict_unchanged_by_corroboration": True,
        "corroboration": {
            "affects_verdict": False,
            "role": "post-hoc corroboration only (never an input to the build stage)",
            "screening_counts": (job.get("screening") or {}).get("counts"),
            "accepted": (job.get("screening") or {}).get("accepted") or [],
            "policy": (job.get("screening") or {}).get("policy"),
        },
        "honest_boundaries": VLAB_BOUNDARIES + _package_boundaries(),
        "thresholds": gb.get("thresholds"),
        "policy_source": gb.get("policy_source"),
        "engine": gb.get("engine"),
        "verification": {
            "algorithm": "sha256 over canonical JSON (sort_keys, no whitespace)",
            "instructions": _verification_instructions(has_package=job.get("vv_package")),
        },
        "vv_package": job.get("vv_package"),
    }
    # 摘要覆盖 integrity 之外的**全部**内容
    payload["integrity"] = {
        "algorithm": "sha256",
        "canonicalization": "json.dumps(sort_keys=True, ensure_ascii=False, separators=(',',':'))",
        "digest": S.canonical_sha256(payload),
    }
    return payload


def delivery_digest(doc: dict) -> str:
    """对去掉 integrity 的副本重算摘要。"""
    clone = {k: v for k, v in doc.items() if k != "integrity"}
    return S.canonical_sha256(clone)


def verify_delivery(doc_or_path) -> Dict[str, Any]:
    """离线复验：重算摘要并比对。返回 {status, expected, actual, ...}。

    status ∈ DELIVERY_INTACT | DELIVERY_TAMPERED | DELIVERY_UNREADABLE
    """
    if isinstance(doc_or_path, (str, os.PathLike)):
        doc = S.read_json(str(doc_or_path))
        if doc is None:
            return {"status": "DELIVERY_UNREADABLE",
                    "reason": f"cannot read/parse: {doc_or_path}"}
        src = str(doc_or_path)
    else:
        doc = doc_or_path
        src = "<in-memory>"
    if not isinstance(doc, dict) or "integrity" not in doc:
        return {"status": "DELIVERY_TAMPERED",
                "reason": "missing integrity block", "source": src}
    expected = (doc.get("integrity") or {}).get("digest")
    actual = delivery_digest(doc)
    ok = (expected == actual)
    return {
        "status": "DELIVERY_INTACT" if ok else "DELIVERY_TAMPERED",
        "source": src,
        "expected": expected,
        "actual": actual,
    }


def finalize_delivery(job: dict, out_dir: str,
                      basename: str = "delivery") -> Dict[str, Any]:
    """写盘 delivery.json + 人类可读 README + .sha256 侧车。"""
    doc = build_delivery(job)
    json_path = os.path.join(out_dir, f"{basename}.json")
    S.write_json(json_path, doc)
    digest = doc["integrity"]["digest"]
    S.write_text(os.path.join(out_dir, f"{basename}.sha256"),
                 f"{digest}  {basename}.json\n")
    S.write_text(os.path.join(out_dir, f"{basename}.README.md"),
                 render_readme(doc))
    return {"json": json_path, "digest": digest}


def render_readme(doc: dict) -> str:
    """人类可读的交付说明——把「怎么自己核」写在最前面。"""
    v = doc.get("verdict") or {}
    eng = doc.get("engine") or {}
    corr = doc.get("corroboration") or {}
    lines: List[str] = []
    lines.append("# VLab 验证交付说明\n")
    lines.append(f"- work order: `{doc.get('work_order_id')}`")
    lines.append(f"- 提交方: {doc.get('submitted_by')} ｜ 专业: {doc.get('discipline')}")
    g = doc.get("goal") or {}
    lines.append(f"- 目标量: {g.get('quantity')} ({g.get('unit')}, {g.get('direction')})")
    lines.append(f"- 结果(outcome): **{doc.get('outcome')}**")
    lines.append("")
    lines.append("## 裁决\n")
    lines.append(f"- hard_verdict: **{v.get('hard_verdict')}**")
    lines.append(f"- gate: **{v.get('gate')}** — {v.get('gate_label') or ''}")
    if v.get("probabilities"):
        lines.append(f"- probabilities: {v.get('probabilities')}")
    if v.get("confidence") is not None:
        lines.append(f"- confidence: {v.get('confidence')} ｜ recommendation: {v.get('recommendation')}")
    lines.append("")
    lines.append("## 事后作证（不影响裁决）\n")
    lines.append(f"- 作用: {corr.get('role')}")
    lines.append(f"- affects_verdict: **{corr.get('affects_verdict')}**")
    lines.append(f"- 筛选计数: {corr.get('screening_counts')}")
    lines.append("")
    lines.append("## 如何自己核对（无需我们的账号/网络）\n")
    for s in (doc.get("verification") or {}).get("instructions") or []:
        lines.append(f"{s.get('step')}. {s.get('action')}")
        lines.append(f"   - 命令: `{s.get('command')}`")
        lines.append(f"   - 期望: `{s.get('expect')}`")
    lines.append("")
    lines.append("## 交付摘要（改一字节即失效）\n")
    lines.append(f"```\n{(doc.get('integrity') or {}).get('digest')}\n```")
    lines.append("")
    lines.append("## 绑定的引擎版本\n")
    lines.append(f"- git_commit: `{eng.get('git_commit')}`")
    lines.append(f"- n_modules: {eng.get('n_modules')}")
    lines.append(f"- all_digest: `{(eng.get('all_digest') or '')[:24]}...`")
    lines.append(f"- 阈值来源: `{doc.get('policy_source')}`")
    lines.append("")
    lines.append("## 诚实边界\n")
    for b in doc.get("honest_boundaries") or []:
        lines.append(f"- {b}")
    lines.append("")
    return "\n".join(lines)


__all__ = [
    "SCHEMA", "VLAB_BOUNDARIES", "build_delivery", "delivery_digest",
    "verify_delivery", "finalize_delivery", "render_readme",
]
