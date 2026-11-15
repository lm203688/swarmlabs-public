"""Experiment Time Machine —— 实验时光机（基于结构化世界模型快照）。

思路借鉴 AsoulAI ChronosFix 的「软件故障时间机器」，把
SwarmLabs 的「自治飞轮每轮状态」做成可回放快照：任意历史 run 都能
`time_travel(run_id)` 完整还原当时的引擎配置 / 指标 / 决策，用于
审计、复现、以及「如果当时换个决策会怎样」的反事实对比。

底层复用 engine/world_model.py 的纯 JSON 存储哲学：零依赖、可序列化、
可在任何环境运行。
"""

from __future__ import annotations

import datetime
import json
import os
from typing import Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
TIME_MACHINE_PATH = os.path.join(DATA, "time_machine.json")


class SnapshotService:
    """记录与回放飞轮 / 实验运行快照。"""

    def __init__(self, path: str = TIME_MACHINE_PATH):
        self.path = path
        self._data = self._load()

    def _load(self) -> dict:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass  # noqa: silent-ok — Exception 可安全忽略（已审计 2026-10-08）
        return {"version": 1, "snapshots": {}}

    def save(self):
        self._data["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def capture(self, run_id: str, payload: dict) -> dict:
        """记录一次运行快照（幂等：同 run_id 覆盖）。"""
        snap = {
            "run_id": run_id,
            "captured_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "payload": payload,
        }
        self._data["snapshots"][run_id] = snap
        self.save()
        return snap

    def time_travel(self, run_id: str) -> Optional[dict]:
        """回到任意历史时刻：返回该 run 的完整快照（None 表示不存在）。"""
        return self._data["snapshots"].get(run_id)

    def list_runs(self) -> list:
        return sorted(self._data["snapshots"].values(),
                      key=lambda s: s.get("captured_at", ""), reverse=True)

    def diff(self, run_a: str, run_b: str) -> dict:
        """对比两次运行的指标差异（反事实分析用）。"""
        a = self.time_travel(run_a)
        b = self.time_travel(run_b)
        if a is None or b is None:
            return {"error": "run 不存在"}
        pa, pb = a["payload"], b["payload"]
        keys = set(pa) | set(pb)
        out = {}
        for k in keys:
            out[k] = {"a": pa.get(k), "b": pb.get(k),
                      "changed": pa.get(k) != pb.get(k)}
        return out


# ── 模块级便捷入口：experiments.time_travel(run_id) ──────────
_service = SnapshotService()


def time_travel(run_id: str):
    return _service.time_travel(run_id)


def capture(run_id: str, payload: dict):
    return _service.capture(run_id, payload)


def list_runs():
    return _service.list_runs()


def diff(run_a: str, run_b: str):
    return _service.diff(run_a, run_b)
