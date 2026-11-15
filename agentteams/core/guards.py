"""DriftGuard —— 防跑偏 / 防死循环守卫。

借鉴来源
--------
- FrontierAgent（Apodex AI）的四守卫：`DuplicateQueryRollback`、`RepetitionGuard`
  （连续相同字节工具调用，提示 3 次后停）、`TextRepetitionGuard`、
  `logical_call_timeout_s=900`。
- OpenClaw 2.0 `sessions_spawn` 的严格校验：**嵌套深度防递归 + 并发上限**——
  同一思路应用到「同一次运行内的调用次数」上限。

为什么本项目也需要
------------------
SwarmLabs 全链路零 LLM，不会出现「模型鬼打墙」式的语义跑偏。但仍有两类真实
风险：

1. **参数化死循环**：`AgentLoop` 的 optimize() 产出优先队列 → planner 依据队列
   选题。若某场景始终「收敛中」，队列会反复把它排到最前，形成跨轮打转。
2. **数值不收敛**：GP 拟合在病态核矩阵上可能长时间不返回；主动学习候选排名在
   高维 bounds 上可能退化为穷举。

守卫不改变任何科学判据，只在「同一件事重复做」和「单步耗时异常」时熔断，把
任务转为 blocked 并给出可读原因，交人工决策——而不是让它安静地烧算力。

设计原则
--------
- **旁路**：守卫只观测与判定，不修改任务内容、不重写参数。
- **软超时**：不使用线程强杀（科学计算中途被杀会留下半成品状态），而是在
  调用**返回后**检查耗时是否越界，越界则标记并阻止后续同类调用。
- **可解释**：每次命中都产出 `DriftEvent`，带指纹、计数、阈值、建议动作。
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


class DriftDetected(RuntimeError):
    """守卫命中：调用被判定为跑偏 / 打转 / 超时，应转 blocked。"""

    def __init__(self, event: "DriftEvent"):
        super().__init__(event.message)
        self.event = event


@dataclass
class DriftEvent:
    kind: str                   # repeat_call / repeat_intent / max_calls / slow_call
    message: str
    fingerprint: str = ""
    count: int = 0
    threshold: float = 0.0
    observed: float = 0.0
    suggestion: str = ""
    at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "message": self.message,
            "fingerprint": self.fingerprint,
            "count": self.count,
            "threshold": self.threshold,
            "observed": round(self.observed, 3),
            "suggestion": self.suggestion,
            "at": round(self.at, 3),
        }


def _fingerprint(name: str, payload: Any) -> str:
    """调用指纹：名字 + 规范化 payload 的 sha1 前 12 位。

    规范化用 sort_keys 保证 dict 顺序无关；不可序列化对象退化为 repr。
    """
    try:
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:                                   # noqa: BLE001
        body = repr(payload)
    return hashlib.sha1(f"{name}::{body}".encode("utf-8")).hexdigest()[:12]


class DriftGuard:
    """四类守卫合一。

    参数
    ----
    max_repeat        同一指纹（名字+参数完全一致）允许的重复次数上限，超过即熔断。
                      FrontierAgent 的 RepetitionGuard 是「提示 3 次后停」，
                      我方取 3 与之对齐。
    max_repeat_intent 同一语义意图（忽略参数）允许的重复次数上限。
    max_calls         单次运行内总调用数上限（对应 OpenClaw 的并发/深度上限思想）。
    slow_call_s       单次调用软超时（秒）。默认 900s，与 FrontierAgent
                      `logical_call_timeout_s` 对齐。
    strict            True → 命中即 raise DriftDetected；False → 只记录事件，
                      由调用方决定。默认 True（fail-closed）。

    用法
    ----
        guard = DriftGuard()
        with guard.watch("virtual_experiment.validate", {"scenario": "monod"}):
            result = do_work()
        # 命中时抛 DriftDetected，调用方转 blocked
        print(guard.report())
    """

    def __init__(self, max_repeat: int = 3, max_repeat_intent: int = 5,
                 max_calls: int = 200, slow_call_s: float = 900.0,
                 strict: bool = True):
        self.max_repeat = int(max_repeat)
        self.max_repeat_intent = int(max_repeat_intent)
        self.max_calls = int(max_calls)
        self.slow_call_s = float(slow_call_s)
        self.strict = bool(strict)

        self._fp_counts: Dict[str, int] = {}
        self._intent_counts: Dict[str, int] = {}
        self._total_calls = 0
        self._slow_names: Dict[str, float] = {}      # 已判定慢的调用名 → 观测耗时
        self.events: List[DriftEvent] = []

    # ── 判定（调用前）────────────────────────────────────────────────
    def check(self, name: str, payload: Any = None) -> Optional[DriftEvent]:
        """调用前预检。返回命中的 DriftEvent，未命中返回 None。

        strict=True 时命中直接 raise DriftDetected。
        """
        self._total_calls += 1

        # 1) 总调用数上限（防无界扩张）
        if self._total_calls > self.max_calls:
            return self._fire(DriftEvent(
                kind="max_calls",
                message=f"单次运行调用数超上限（{self._total_calls} > {self.max_calls}）",
                count=self._total_calls, threshold=self.max_calls,
                observed=self._total_calls,
                suggestion="检查是否存在无界任务派生；必要时提高 max_calls 或拆分运行。",
            ))

        # 2) 同名调用已被判定为慢 → 不再重复烧算力
        if name in self._slow_names:
            return self._fire(DriftEvent(
                kind="slow_call",
                message=(f"{name} 上次耗时 {self._slow_names[name]:.1f}s 超软超时 "
                         f"{self.slow_call_s:.0f}s，已熔断后续同类调用"),
                fingerprint=_fingerprint(name, payload),
                threshold=self.slow_call_s, observed=self._slow_names[name],
                suggestion="缩小 bounds / 降低候选点数量 / 检查 GP 核矩阵是否病态。",
            ))

        fp = _fingerprint(name, payload)

        # 3) 完全相同的调用（名字 + 参数）重复
        self._fp_counts[fp] = self._fp_counts.get(fp, 0) + 1
        if self._fp_counts[fp] > self.max_repeat:
            return self._fire(DriftEvent(
                kind="repeat_call",
                message=(f"{name} 以完全相同参数重复调用 {self._fp_counts[fp]} 次"
                         f"（上限 {self.max_repeat}）"),
                fingerprint=fp, count=self._fp_counts[fp],
                threshold=self.max_repeat, observed=self._fp_counts[fp],
                suggestion="上游未消费结果或参数未推进，检查 AgentLoop 优先队列是否卡死。",
            ))

        # 4) 同一意图（忽略参数）重复——参数微调但语义打转
        self._intent_counts[name] = self._intent_counts.get(name, 0) + 1
        if self._intent_counts[name] > self.max_repeat_intent:
            return self._fire(DriftEvent(
                kind="repeat_intent",
                message=(f"意图 {name} 重复 {self._intent_counts[name]} 次"
                         f"（上限 {self.max_repeat_intent}），疑似语义打转"),
                fingerprint=fp, count=self._intent_counts[name],
                threshold=self.max_repeat_intent, observed=self._intent_counts[name],
                suggestion="参数在变但意图不变，检查收敛判据是否永不满足。",
            ))
        return None

    def _fire(self, ev: DriftEvent) -> DriftEvent:
        self.events.append(ev)
        if self.strict:
            raise DriftDetected(ev)
        return ev

    # ── 记录（调用后）────────────────────────────────────────────────
    def observe(self, name: str, elapsed_s: float) -> Optional[DriftEvent]:
        """调用返回后登记耗时。越过软超时则记录并熔断后续同名调用。

        刻意**不**在此处 raise：本次调用已经完成，结果是有效的，杀掉它没有意义。
        熔断作用于下一次同名调用（见 check() 的第 2 条）。
        """
        if elapsed_s <= self.slow_call_s:
            return None
        self._slow_names[name] = float(elapsed_s)
        ev = DriftEvent(
            kind="slow_call",
            message=f"{name} 耗时 {elapsed_s:.1f}s 超软超时 {self.slow_call_s:.0f}s",
            threshold=self.slow_call_s, observed=elapsed_s,
            suggestion="本次结果保留；后续同类调用已熔断，请缩小问题规模。",
        )
        self.events.append(ev)
        return ev

    # ── 上下文管理器（推荐用法）──────────────────────────────────────
    def watch(self, name: str, payload: Any = None):
        return _Watch(self, name, payload)

    # ── 导出 ────────────────────────────────────────────────────────
    def report(self) -> Dict[str, Any]:
        return {
            "total_calls": self._total_calls,
            "distinct_fingerprints": len(self._fp_counts),
            "distinct_intents": len(self._intent_counts),
            "slow_calls": {k: round(v, 2) for k, v in self._slow_names.items()},
            "events": [e.to_dict() for e in self.events],
            "clean": not self.events,
            "thresholds": {
                "max_repeat": self.max_repeat,
                "max_repeat_intent": self.max_repeat_intent,
                "max_calls": self.max_calls,
                "slow_call_s": self.slow_call_s,
            },
        }

    def reset(self):
        self._fp_counts.clear()
        self._intent_counts.clear()
        self._slow_names.clear()
        self._total_calls = 0
        self.events.clear()


class _Watch:
    """`with guard.watch(name, payload):` 的实现体。

    进入时预检（可能 raise DriftDetected），退出时登记耗时。
    """

    def __init__(self, guard: DriftGuard, name: str, payload: Any):
        self.guard = guard
        self.name = name
        self.payload = payload
        self.t0 = 0.0

    def __enter__(self):
        self.guard.check(self.name, self.payload)
        self.t0 = time.time()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.guard.observe(self.name, time.time() - self.t0)
        return False                                     # 不吞异常
