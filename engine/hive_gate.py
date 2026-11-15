"""Hive Gate —— 多源发现的广播闸门（借鉴 mu/μ 的 Hive 通信闸门设计）。

mu 在多 Agent 协作里把 Judge 放到通信链路中间: 子 Agent 的新发现先判
"值不值得共享 -> 发给谁 -> 新证据是支持还是推翻旧结论", 只把真正有价值的
信息传给需要它的 Agent, 而不是全员广播。

SwarmLabs 对应场景: 飞轮的多源采集(autonomous_loop 收敛结果 / 模板库 PASS /
反重复快照)此前**无条件**灌进共享世界模型(board)。本模块在灌入前加一个
Jev noul 判定点:

    P(该发现值得进入共享知识库) < 0.4  =>  只记台账, 不进 world_model
    Jev 不可达 / 关闭                  =>  fail-open(照常广播) + 一次性熔断

设计取舍(与安全护栏相反):
  - 数据采集链路用 fail-open —— 漏一条发现比灌一条平庸发现的代价高,
    这与 aishield 安全门禁的 fail-closed ASK 是刻意对立的两种策略。
  - 每次判定落盘 JSONL 台账(data/hive_gate_ledger.jsonl), 与 aishield
    影子台账同风格: 先积累配对数据, 后续可离线重校阈值。

在产品能力面上的位置: 本闸门是"多 Agent 协作 + 知识累积"的判定内核实例,
判定证据留痕可审计 —— 信任面公开的前提是每一步判定都可回放。

用法(engine 内部):
    from engine.hive_gate import gate_finding
    allowed, meta = gate_finding({"key": ..., "status": "converged", ...})

环境变量:
    HIVE_GATE=off        关闭闸门(全部放行, 不调 Jev)
    HIVE_GATE_THRESHOLD  阈值, 默认 0.4
    TYPESAFE_API_KEY     覆盖 ~/.config/typesafe/credentials.json
"""
from __future__ import annotations

import datetime
import json
import os
import time
import urllib.request

API = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
CRED_PATH = os.path.join(os.path.expanduser("~"), ".config", "typesafe", "credentials.json")
LEDGER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data",
                      "hive_gate_ledger.jsonl")

THRESHOLD = float(os.environ.get("HIVE_GATE_THRESHOLD", "0.4"))
ENABLED = os.environ.get("HIVE_GATE", "on").lower() != "off"

# 一次性熔断: Jev 不可达后本进程不再重试(同 tech_radar._laya_triage 风格)
_broken = False
_instruction = (
    "You are the communication gate of a multi-agent research system. A sub-agent "
    "produced a finding that will be broadcast into the shared world-model knowledge "
    "base used by all agents. Decide whether this finding is worth sharing: it should "
    "carry non-trivial, non-duplicative scientific or engineering information "
    "(e.g. a converged validated scenario, a reusable template, a new capability). "
    "Generic logs, empty metadata, pure noise or trivial repetitions are NOT worth "
    "broadcasting."
)


def _load_key() -> str:
    k = os.environ.get("TYPESAFE_API_KEY")
    if k:
        return k.strip()
    if os.path.exists(CRED_PATH):
        return (json.load(open(CRED_PATH, encoding="utf-8")).get("api_key") or "").strip()
    return ""


def _append_ledger(entry: dict) -> None:
    """台账 append-only; 失败静默(台账不能反过来阻塞主流程)。"""
    try:
        with open(LEDGER, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass  # noqa: silent-ok — Exception 可安全忽略（已审计 2026-10-08）


def gate_finding(finding: dict) -> tuple[bool, dict]:
    """判定一条发现是否值得广播进共享世界模型。

    Returns:
        (allow, meta): allow=是否放行; meta 含判定模式/概率/延迟/台账路径。
        fail-open: Jev 不可达或无 key 时 allow=True, meta.mode 说明原因。
    """
    global _broken
    meta = {"mode": "active", "p": None, "ms": None, "threshold": THRESHOLD}

    if not ENABLED:
        meta["mode"] = "off"
        return True, meta

    text = json.dumps(finding, ensure_ascii=False, default=str)[:1200]
    key = _load_key()
    if not key:
        meta["mode"] = "no_key_fail_open"
        _append_ledger({"ts": datetime.datetime.now().isoformat(timespec="seconds"),
                        "allow": True, "excerpt": text[:120], **meta})
        return True, meta
    if _broken:
        meta["mode"] = "circuit_broken_fail_open"
        return True, meta

    body = json.dumps({
        "model": MODEL,
        "state": text,
        "questions": {"share": {"type": "noul", "instructions": _instruction}},
    }).encode("utf-8")
    req = urllib.request.Request(API, data=body, method="POST", headers={
        "Authorization": "Bearer " + key,
        "Content-Type": "application/json",
    })
    try:
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=45) as r:
            data = json.loads(r.read().decode("utf-8"))
        meta["ms"] = round((time.time() - t0) * 1000)
        ans = data.get("answers", {}).get("share")
        if not isinstance(ans, dict) or "noul" not in ans:
            raise ValueError("missing answers.share.noul: %s" % json.dumps(data)[:160])
        meta["p"] = float(ans["noul"])
        allow = meta["p"] >= THRESHOLD
        meta["mode"] = "broadcast" if allow else "gated_out"
        _append_ledger({"ts": datetime.datetime.now().isoformat(timespec="seconds"),
                        "allow": allow, "excerpt": text[:120], **meta})
        return allow, meta
    except Exception as e:
        # error_kind:DEGRADABLE — Jev API 失败/超时是**有意**的 fail-open：数据采集链路宁可多广播
        # 一条平庸发现也不能丢真实收敛结果（与 aishield ASK 门禁 fail-closed 刻意对立）；
        # 通过 ledger 记录 + 一次性熔断 _broken 双重留痕，可离线复核。
        _broken = True  # fail-open + 一次性熔断
        meta["mode"] = "error_fail_open"
        meta["err"] = str(e)[:140]
        _append_ledger({"ts": datetime.datetime.now().isoformat(timespec="seconds"),
                        "allow": True, "excerpt": text[:120], **meta})
        return True, meta


def gate_findings(findings: list[dict]) -> tuple[list[dict], dict]:
    """批量闸门: 返回 (放行列表, 统计)。单条异常不影响其余(fail-open)。"""
    broadcast, stats = [], {"considered": 0, "broadcast": 0, "gated_out": 0,
                            "fail_open": 0}
    for f in findings or []:
        stats["considered"] += 1
        allow, meta = gate_finding(f)
        if meta["mode"].endswith("fail_open"):
            stats["fail_open"] += 1
        if allow:
            stats["broadcast"] += 1
            if meta["mode"] != "off":
                f2 = dict(f)
                f2["hive_gate"] = {"p": meta["p"], "ms": meta["ms"]}
                broadcast.append(f2)
        else:
            stats["gated_out"] += 1
    return broadcast, stats


if __name__ == "__main__":
    # 自检: 一条有信息量的发现 + 一条噪音
    demo = [
        {"key": "microbiology/gp-growth", "status": "converged", "rubric_passed": 4,
         "domain": "microbiology", "note": "Monod GP surrogate validated, 4/4 rubric pass"},
        {"key": "noise-001", "status": "converged", "note": "", "logs": "ok ok ok"},
    ]
    kept, st = gate_findings(demo)
    print(json.dumps({"kept": [k.get("key") for k in kept], "stats": st,
                      "ledger": LEDGER}, ensure_ascii=False, indent=1))
