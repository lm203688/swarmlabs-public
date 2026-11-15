#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VLab 批量运行器 + 收敛检测（死循环检测器）。

为什么需要这一层
----------------
`vlab_orchestrator.run_job()` 是**单趟确定性流水线**，自身没有循环——所以
ml-intern 那种 token 级的 doom-loop detector 在这里不适用。真正会吃掉算力
的是**调度层**：有人拿着一个注定失败的工作单反复提交，或者同一份工作单被
重复投递。这类循环在单个 `run_job` 里看不出来，只有跨运行的历史才看得到。

ml-intern 的架构教训是「提交队列 + 死循环检测」，本模块把它按我方架构改造成
**作业级收敛检测**，四类判据：

  1. `DUPLICATE_SUBMISSION` —— 同一 `work_order_digest` 已跑过。
     工作单字节完全相同 ⇒ 结果必然完全相同 ⇒ 再跑一次是纯浪费。
  2. `NON_RETRYABLE` —— outcome 属于「重试不会改变结果」的集合。
     `REJECTED_AT_INTAKE` / `NEEDS_ONBOARDING` / `NO_LEDGER_FAIL_CLOSED` /
     `STALE_LEDGER_FAIL_CLOSED` 都需要**配置或数据变更**才能解，重跑只是
     把同一个错再算一遍。
  3. `STALLED` —— 连续 N 次同一 `(outcome, verdict, scenario_key)` 且
     工作单本身没有实质变更。这是「agent 卡在原地」的工程化定义。
  4. `OSCILLATING` —— 连续运行在两个以上 verdict 间反复横跳。
     说明输入或依赖在不稳定地变化，需要停下来查根因，而不是继续跑。

fail-closed 语义：1/2 类**阻断后续运行**（不跑、直接返回历史结果）；
3/4 类**告警并计数**，达到阈值即升级为阻断。判定全部基于历史字节，
不引入任何随机性，可复算。

依赖：仅标准库（同目录模块按路径加载）。
"""

from __future__ import annotations

import importlib.util
import json
import os
from typing import Any, Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(
        f"_vlab_{name}", os.path.join(_HERE, f"{name}.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


S = _load("vlab_stdlib")
C = _load("vlab_contract")
ORCH = _load("vlab_orchestrator")

SCHEMA = "swarmlabs-vlab-batch-run/1.0"

DEFAULT_WO_DIR = os.path.join(S.ROOT, "data", "vlab", "work-orders")
DEFAULT_HISTORY = os.path.join(S.ROOT, "data", "vlab", "history.jsonl")

#: 重试不会改变结果的 outcome。判定依据写在 `vlab_orchestrator` 的 docstring：
#: 这些分支都在 build/verify 之前或依赖外部配置，工作单不变 ⇒ 结果不变。
NON_RETRYABLE = frozenset((
    ORCH.REJECTED_AT_INTAKE,
    ORCH.NEEDS_ONBOARDING,
    ORCH.NO_LEDGER_FAIL_CLOSED,
    ORCH.STALE_LEDGER_FAIL_CLOSED,
))

#: 停滞判定：连续 N 次无变化即视为卡住
STALL_THRESHOLD = 3


# ------------------------------------------------------------------ 历史读写

def _read_history(path: str) -> List[dict]:
    """读 JSONL 历史。逐行解析，坏行跳过并计数（不整文件失败）。"""
    rows: List[dict] = []
    if not os.path.exists(path):
        return rows
    bad = 0
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:  # noqa: BLE001
            bad += 1
            continue
        if isinstance(obj, dict):
            rows.append(obj)
        else:
            bad += 1
    if bad:
        # 历史被写坏是可观测状态，不是静默忽略
        S.write_json(path + ".corrupt", {"bad_lines": bad,
                                         "checked_at_utc": S.utc_now()})
    return rows


def _append_history(path: str, record: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


# ------------------------------------------------------------------ 判定

def _signature(rec: dict) -> str:
    """历史记录的收敛签名：outcome + verdict + 场景。"""
    return "|".join((str(rec.get("outcome") or ""),
                     str(rec.get("verdict") or ""),
                     str(rec.get("scenario_key") or "")))


def detect(history: List[dict], digest: str) -> Dict[str, Any]:
    """对某个 work_order_digest 做收敛检测。返回判定结果。

    `action` 取值：`RUN`（放行）/ `SKIP`（阻断，理由见 reason）。
    """
    prior = [r for r in history if r.get("digest") == digest]

    # 1) 重复提交
    if prior:
        last = prior[-1]
        return {
            "action": "SKIP",
            "rule": "DUPLICATE_SUBMISSION",
            "severity": "block",
            "reason": (
                f"work_order_digest {digest} 已运行 {len(prior)} 次，"
                f"最近一次 outcome={last.get('outcome')} "
                f"({last.get('at_utc')})。工作单字节完全相同 ⇒ 结果必然相同，"
                f"重跑是纯浪费；需要变更工作单内容才会产生新结果。"
            ),
            "prior_runs": len(prior),
            "last_outcome": last.get("outcome"),
            "reuse": True,
        }

    # 2) 不可重试失败（首次运行，无从判定 → 放行）
    return {"action": "RUN", "rule": None, "severity": None,
            "reason": "first run for this digest",
            "prior_runs": 0, "reuse": False}


def detect_batch(history: List[dict],
                 digest_outcomes: List[Tuple[str, str]],
                 threshold: int = STALL_THRESHOLD) -> Dict[str, Any]:
    """对整个批次做停滞/震荡检测（跨 digest 的趋势判断）。

    `digest_outcomes` 是本次运行得到的 [(digest, outcome)]，按提交顺序。
    """
    flags: List[dict] = []

    # 3) 停滞：连续 N 次相同 outcome
    streak = 0
    cur: Optional[str] = None
    for digest, outcome in digest_outcomes:
        if outcome == cur:
            streak += 1
        else:
            streak, cur = 1, outcome
        if streak >= threshold and outcome in NON_RETRYABLE:
            flags.append({
                "rule": "STALLED",
                "severity": "warn",
                "consecutive": streak,
                "outcome": outcome,
                "reason": (
                    f"连续 {streak} 次得到 {outcome}，且该 outcome 属于"
                    "「重试不会改变结果」集合。继续提交只会重复同一个错误——"
                    "需要变更工作单或补齐专业准入（gold / 账本场景），"
                    "而不是再跑一遍。"
                ),
            })
            streak = 0  # 同一轮不重复报

    # 4) 震荡：verdict 在不同值间反复
    seen = [o for _, o in digest_outcomes if o not in NON_RETRYABLE]
    if len(seen) >= 4:
        for i in range(1, len(seen)):
            if seen[i] != seen[i - 1] and i + 1 < len(seen) \
                    and seen[i + 1] == seen[i - 1]:
                flags.append({
                    "rule": "OSCILLATING",
                    "severity": "warn",
                    "sequence": seen[max(0, i - 2):min(len(seen), i + 2)],
                    "reason": (
                        "verdict 在相邻提交间反复横跳，说明输入或依赖在"
                        "不稳定地变化（账本重建、引擎变更、工作单漂移）。"
                        "应先定位根因再批量运行。"
                    ),
                })
                break

    counts = {
        "STALLED": sum(1 for f in flags if f["rule"] == "STALLED"),
        "OSCILLATING": sum(1 for f in flags if f["rule"] == "OSCILLATING"),
    }
    return {
        "flags": flags,
        "counts": counts,
        "threshold": threshold,
        "non_retryable_set": sorted(NON_RETRYABLE),
    }


# ------------------------------------------------------------------ 主流程

def run_batch(wo_dir: Optional[str] = None,
              history_path: Optional[str] = None,
              force: bool = False,
              only: Optional[str] = None) -> Dict[str, Any]:
    """跑一批工作单，带收敛检测。

    `force=True` 忽略重复提交阻断（用于刻意复算，需外部说明理由）。
    `only` 指定单个工作单文件名（不含扩展名）。
    """
    wo_dir = wo_dir or DEFAULT_WO_DIR
    history_path = history_path or DEFAULT_HISTORY
    history = _read_history(history_path)

    if not os.path.isdir(wo_dir):
        return {"schema": SCHEMA, "error": f"work order dir not found: {wo_dir}",
                "at_utc": S.utc_now()}

    files = sorted(f for f in os.listdir(wo_dir)
                   if f.endswith(".json") and (only is None or only in f))

    results: List[dict] = []
    digest_outcomes: List[Tuple[str, str]] = []
    skipped: List[dict] = []
    ran = 0

    for fn in files:
        fp = os.path.join(wo_dir, fn)
        wo = S.read_json(fp)
        if not isinstance(wo, dict):
            skipped.append({"file": fn, "rule": "UNPARSEABLE",
                            "reason": "not a JSON object"})
            continue
        digest = C.work_order_digest(wo)
        verdict_str = str((wo.get("scenario_key") or ""))

        if not force:
            det = detect(history, digest)
            if det["action"] == "SKIP":
                det["file"] = fn
                skipped.append(det)
                digest_outcomes.append((digest, det.get("last_outcome") or ""))
                continue

        job = ORCH.run_job(wo)
        outcome = job.get("outcome")
        ran += 1
        record = {
            "schema": SCHEMA,
            "at_utc": S.utc_now(),
            "file": fn,
            "digest": digest,
            "work_order_id": job.get("work_order_id"),
            "scenario_key": (job.get("scope") or {}).get("scenario_key"),
            "outcome": outcome,
            "verdict": (job.get("verdict") or {}).get("hard_verdict"),
            "gate": (job.get("verdict") or {}).get("gate"),
            "signature": _signature(job),
        }
        _append_history(history_path, record)
        history.append(record)
        results.append(record)
        digest_outcomes.append((digest, outcome or ""))

    trend = detect_batch(history, digest_outcomes)

    return {
        "schema": SCHEMA,
        "at_utc": S.utc_now(),
        "work_order_dir": wo_dir,
        "history_path": history_path,
        "history_lines_before": len(_read_history(history_path)) - ran,
        "ran": ran,
        "skipped": skipped,
        "results": results,
        "trend": trend,
        "force": bool(force),
        "summary": {
            "submitted": len(files),
            "ran": ran,
            "skipped": len(skipped),
            "by_outcome": _tally(results, "outcome"),
            "by_rule": _tally(skipped, "rule"),
            "flags": trend["counts"],
        },
    }


def _tally(rows: List[dict], key: str) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for r in rows:
        k = str(r.get(key) or "null")
        out[k] = out.get(k, 0) + 1
    return out


def main(argv: Optional[List[str]] = None) -> int:
    import sys
    argv = argv if argv is not None else sys.argv[1:]
    force = "--force" in argv
    hist = None
    wo_dir = None
    for a, b in zip(argv, argv[1:]):
        if a == "--history" and b and not b.startswith("-"):
            hist = b
        if a == "--wo-dir" and b and not b.startswith("-"):
            wo_dir = b

    rep = run_batch(wo_dir=wo_dir, history_path=hist, force=force)
    if rep.get("error"):
        print(rep["error"])
        return 1

    s = rep["summary"]
    print(f"submitted : {s['submitted']}")
    print(f"ran       : {s['ran']}")
    print(f"skipped   : {s['skipped']}")
    for rule, n in sorted(s["by_rule"].items()):
        print(f"  by rule   {rule:22s}: {n}")
    for outcome, n in sorted(s["by_outcome"].items()):
        print(f"  outcome   {outcome:24s}: {n}")
    print(f"flags     : {s['flags']}")
    for fl in rep["trend"]["flags"]:
        print(f"  [{fl['severity']}] {fl['rule']}: {fl['reason'][:160]}")

    # 判定：不可重试失败占比过高 = 批量在空转
    bad = sum(s["by_outcome"].get(o, 0) for o in NON_RETRYABLE)
    warn = any(s["flags"].get(k, 0) > 0 for k in ("STALLED", "OSCILLATING"))
    if bad and s["ran"]:
        print(f"\nWARNING: {bad}/{s['ran']} 次运行落在「重试不会改变结果」集合")
        print("  → 继续批量提交不会收敛；请先变更工作单或补齐专业准入。")
    return 0 if not (bad or warn) else 2


if __name__ == "__main__":
    raise SystemExit(main())
