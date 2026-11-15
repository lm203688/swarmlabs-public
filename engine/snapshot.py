#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SwarmLabs 快照回滚 + Candidate/Baseline 严格优于判定。

借自 PenguinHarness（LlamaFactory 作者）的 RSI 闭环：
  「Candidate 只有严格优于 Baseline 才允许接受，否则回滚。」

SwarmLabs 当前引擎只有「产出指标连续 2 次零增长 → 升级条件②」——
但没有回滚，没有严格的候选对照。本模块补齐：

  * take_snapshot(target_files) —— 快照若干关键文件到 data/snapshots/
  * compare_metrics(baseline, candidate, metrics) —— 严格优于判定
      每个 metric 都必须 candidate >= baseline + epsilon
      （epsilon 默认 0，允许「持平」在 metric 声明了 tie_ok 时）
  * accept_candidate / reject_candidate —— 落地或回滚
  * run_guarded_change(change_fn, baseline_metrics, target_files)
      装饰器风格：change_fn 成功且严格优于 baseline → accept；否则 rollback

严格优于 (strictly_better) 语义（关键）：
    所有声明为「higher_better=True」的指标，candidate >= baseline + epsilon；
    所有声明为「higher_better=False」的指标，candidate <= baseline - epsilon；
    若某指标缺失，视为 candidate 无证据证明优于 baseline → 拒绝。

设计哲学："No Anchored Counter, No PASS" 的飞轮版：
    没有可核验的 candidate_metrics 优于 baseline_metrics → 不允许升级。
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import shutil
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAPSHOT_DIR = os.path.join(ROOT, "data", "snapshots")
AUDIT_LOG = os.path.join(ROOT, "data", "snapshots", "audit.jsonl")


def _now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def take_snapshot(target_files: Iterable[str], tag: Optional[str] = None) -> str:
    """把 target_files 快照到 data/snapshots/<ts>_<tag>/，返回快照目录绝对路径。

    target_files 可含仓库根相对路径（推荐）或绝对路径。
    不存在的路径跳过并记录到 manifest 的 missing 字段。
    """
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    tag = tag or "snap"
    snap_dir = os.path.join(SNAPSHOT_DIR, f"{ts}_{tag}")
    os.makedirs(snap_dir, exist_ok=True)
    manifest: Dict[str, Any] = {"tag": tag, "created_at": _now_iso(), "files": {}, "missing": []}
    for rel in target_files:
        src = rel if os.path.isabs(rel) else os.path.join(ROOT, rel)
        if not os.path.exists(src):
            manifest["missing"].append(rel)
            continue
        dst = os.path.join(snap_dir, os.path.basename(src))
        shutil.copy2(src, dst)
        manifest["files"][rel] = {"sha256": _sha(src), "size": os.path.getsize(src)}
    with open(os.path.join(snap_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    return snap_dir


def _load_baseline(path: str) -> Dict[str, float]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def compare_metrics(
    baseline: Dict[str, float],
    candidate: Dict[str, float],
    metrics: Dict[str, Dict[str, Any]],
    epsilon: float = 0.0,
) -> Dict[str, Any]:
    """严格优于判定。

    metrics 结构：
      { "rubric_full_pass": {"higher_better": True, "tie_ok": False, "weight": 1.0},
        "n_refuted":        {"higher_better": False, "tie_ok": False, "weight": 1.0},
        "r2_mean":          {"higher_better": True, "tie_ok": True,  "weight": 2.0} }
    tie_ok=True 允许该指标「持平」算 pass；否则持平视为非严格优 → 拒绝。

    返回：
      { "accepted": bool,
        "per_metric": {"<name>": {"b": baseline, "c": candidate, "ok": bool, "reason": str}},
        "rejected_on": [<name>, ...] }
    """
    per_metric: Dict[str, Any] = {}
    rejected_on: List[str] = []
    for name, cfg in metrics.items():
        if name not in candidate:
            per_metric[name] = {
                "b": baseline.get(name), "c": None, "ok": False,
                "reason": "candidate missing metric",
            }
            rejected_on.append(name)
            continue
        b = baseline.get(name)
        if b is None:
            per_metric[name] = {
                "b": None, "c": candidate[name], "ok": False,
                "reason": "baseline missing metric (cannot prove improvement)",
            }
            rejected_on.append(name)
            continue
        c = candidate[name]
        higher = cfg.get("higher_better", True)
        tie_ok = cfg.get("tie_ok", False)
        if higher:
            if c > b + epsilon:
                ok, reason = True, "strictly_better"
            elif tie_ok and abs(c - b) <= epsilon:
                ok, reason = True, "tie_ok_allowed"
            else:
                ok, reason = False, f"c={c} not > b={b} (+eps={epsilon})"
        else:
            if c < b - epsilon:
                ok, reason = True, "strictly_better_lower"
            elif tie_ok and abs(c - b) <= epsilon:
                ok, reason = True, "tie_ok_allowed"
            else:
                ok, reason = False, f"c={c} not < b={b} (-eps={epsilon})"
        per_metric[name] = {"b": b, "c": c, "ok": ok, "reason": reason}
        if not ok:
            rejected_on.append(name)
    return {
        "accepted": not rejected_on,
        "per_metric": per_metric,
        "rejected_on": rejected_on,
    }


def restore_snapshot(snap_dir: str) -> List[str]:
    """把快照目录里的文件还原回原路径（相对路径按 manifest 记录）。"""
    manifest_path = os.path.join(snap_dir, "manifest.json")
    if not os.path.exists(manifest_path):
        raise RuntimeError(f"no manifest at {manifest_path}")
    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)
    restored: List[str] = []
    for rel in manifest["files"]:
        src = os.path.join(snap_dir, os.path.basename(rel))
        dst = rel if os.path.isabs(rel) else os.path.join(ROOT, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        restored.append(rel)
    return restored


def _append_audit(record: Dict[str, Any]) -> None:
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    with open(AUDIT_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def run_guarded_change(
    change_fn: Callable[[], Any],
    candidate_metrics_fn: Callable[[], Dict[str, float]],
    baseline_metrics: Dict[str, float],
    metrics_cfg: Dict[str, Dict[str, Any]],
    target_files: Iterable[str],
    tag: Optional[str] = None,
    epsilon: float = 0.0,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """执行 change_fn 并用严格优于判定决定接受 / 回滚。

    流程：
      1) take_snapshot(target_files, tag)         ← 保存 baseline 快照
      2) change_fn()                              ← 尝试变更
      3) candidate_metrics = candidate_metrics_fn()
      4) compare_metrics → accepted / rejected
      5) rejected → restore_snapshot(snap_dir)    ← 回滚到 baseline
      6) 写 audit.jsonl 记录本次决策
    """
    snap_dir = take_snapshot(target_files, tag=tag) if not dry_run else ""
    record: Dict[str, Any] = {"ts": _now_iso(), "tag": tag or "change", "snap_dir": snap_dir}
    try:
        change_fn()
    except Exception as e:
        record.update(status="change_failed", error=str(e))
        if not dry_run and snap_dir:
            record["restored"] = restore_snapshot(snap_dir)
        _append_audit(record)
        return {"status": "change_failed", "detail": record}
    try:
        candidate_metrics = candidate_metrics_fn()
    except Exception as e:
        record.update(status="metric_failed", error=str(e))
        if not dry_run and snap_dir:
            record["restored"] = restore_snapshot(snap_dir)
        _append_audit(record)
        return {"status": "metric_failed", "detail": record}
    cmp = compare_metrics(baseline_metrics, candidate_metrics, metrics_cfg, epsilon=epsilon)
    record.update(
        status="accepted" if cmp["accepted"] else "rejected",
        comparison=cmp,
        candidate_metrics=candidate_metrics,
    )
    if not cmp["accepted"] and not dry_run and snap_dir:
        record["restored"] = restore_snapshot(snap_dir)
    _append_audit(record)
    return {"status": "accepted" if cmp["accepted"] else "rejected", "detail": record}


# ---------- 内置：SwarmLabs 当前核心 metrics 配置 ----------

DEFAULT_METRICS_CFG: Dict[str, Dict[str, Any]] = {
    # 更高越好
    "rubric_full_pass": {"higher_better": True, "tie_ok": False, "weight": 2.0},
    "total_store_points": {"higher_bember": False, "higher_better": True, "tie_ok": False, "weight": 1.0},
    "r2_mean": {"higher_better": True, "tie_ok": False, "weight": 2.0},
    "coverage_mean": {"higher_better": True, "tie_ok": False, "weight": 2.0},
    "n_anchored": {"higher_better": True, "tie_ok": False, "weight": 1.5},
    # 更低越好
    "n_refuted": {"higher_better": False, "tie_ok": False, "weight": 1.0},
    "n_error": {"higher_better": False, "tie_ok": False, "weight": 1.0},
}


if __name__ == "__main__":
    # 自测：一个严格优于 / 一个平局 / 一个退步
    baseline = {"r2_mean": 0.95, "n_refuted": 5, "coverage_mean": 0.94}
    for name, cand in [
        ("strict better", {"r2_mean": 0.97, "n_refuted": 4, "coverage_mean": 0.96}),
        ("tie on r2",     {"r2_mean": 0.95, "n_refuted": 4, "coverage_mean": 0.96}),
        ("regression",    {"r2_mean": 0.93, "n_refuted": 5, "coverage_mean": 0.95}),
        ("missing metric", {"r2_mean": 0.97, "n_refuted": 4}),
    ]:
        r = compare_metrics(
            baseline, cand,
            {"r2_mean": {"higher_better": True, "tie_ok": False},
             "n_refuted": {"higher_better": False, "tie_ok": False},
             "coverage_mean": {"higher_better": True, "tie_ok": False}},
        )
        print(f"{name:16s} accepted={r['accepted']}  rejected_on={r['rejected_on']}")
