"""TaskBoard —— 显式任务看板五态机。

借鉴来源
--------
OpenClaw 2.0（team.openclaw.ai）的多任务管理：把「谁在跑、谁被挡、谁被取消」
做成**外部可见的显式状态**，而不是藏在执行器的局部变量里。
FrontierAgent 的 Agent Team 协调器同样维护一份任务看板。

Eureka（arXiv 2608.19047）的「义务图 + 显式验收语义」：任务落 `completed` 前，
其**机器可判定**的 obligation 必须由 evidence 独立满足，否则强制落 `blocked`。
详见 `core/obligations.py`——本模块只负责"按验收结果落正确的状态"。

为什么需要
----------
改造前 `LocalRuntime.dispatch` 用局部 `done: Dict[str, WorkerResult]` 隐式跟踪
进度：跑完才知道结果，跑挂了看不出卡在哪一步，也无法区分「被权限挡住」与
「上游失败被跳过」。看板把这些变成可查询、可落盘、可审计的状态。

更进一步（Eureka 借鉴）：改造前"完成"由 `WorkerResult.status == "ok"` **单方声明**，
执行者自己说自己做好了。挂上义务后，完成需要**独立复核**——这是零误接受的前提。

五态与合法转移
--------------
    pending ──▶ active ──▶ completed
       │          │
       │          └──────▶ blocked   （权限拦截 / 可行性不通过 / 防跑偏命中）
       │
       └────────────────▶ cancelled （上游失败导致跳过 / 人工中止）

    blocked ──▶ active     （批准后重试，对应 `--approve` 重跑）
    blocked ──▶ cancelled  （放弃）

非法转移会抛 `IllegalTransition`——这是刻意的：状态机静默容错等于没有状态机。
终态（completed / cancelled）不可再转移。

零破坏保证
----------
看板是**旁路观测器**，不参与调度决策。`leader.run` 里即使看板整体失效，
派发逻辑与 Worker 行为完全不变（见 leader.py 的 try/except 包裹）。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

try:                                    # 义务图可选依赖：缺失时看板退化为纯状态机
    from .obligations import (Obligation, ObligationReport, ObligationSet,
                              evidence_from_trust_row, evidence_from_trust_rows,
                              obligations_for_role)
except Exception:                       # noqa: BLE001
    Obligation = None                   # type: ignore[assignment]
    ObligationReport = None             # type: ignore[assignment]
    ObligationSet = None                # type: ignore[assignment]
    evidence_from_trust_row = None      # type: ignore[assignment]
    evidence_from_trust_rows = None     # type: ignore[assignment]
    obligations_for_role = None         # type: ignore[assignment]

try:                                    # 多 Agent 仲裁层（PACMAN 借鉴）：可选依赖
    from .arbitration import (arbitrate as _arbitrate,
                              claim_from_worker as _claim_worker,
                              claim_from_obligations as _claim_ob,
                              ArbitrationVerdict)
except Exception:                       # noqa: BLE001
    _arbitrate = None                   # type: ignore[assignment]
    _claim_worker = None                # type: ignore[assignment]
    _claim_ob = None                    # type: ignore[assignment]
    ArbitrationVerdict = None           # type: ignore[assignment]

# ── 五态 ────────────────────────────────────────────────────────────
PENDING = "pending"
ACTIVE = "active"
COMPLETED = "completed"
BLOCKED = "blocked"
CANCELLED = "cancelled"

STATES = (PENDING, ACTIVE, COMPLETED, BLOCKED, CANCELLED)
TERMINAL_STATES = (COMPLETED, CANCELLED)

# 合法转移表：from → 允许的 to 集合
_TRANSITIONS: Dict[str, tuple] = {
    PENDING: (ACTIVE, BLOCKED, CANCELLED),
    ACTIVE: (COMPLETED, BLOCKED, CANCELLED),
    BLOCKED: (ACTIVE, CANCELLED),
    COMPLETED: (),
    CANCELLED: (),
}

# WorkerResult.status → 看板终态映射。
# 注意：error / partial 都落终态，但归因不同（见 _kind_of）：
#  - "blocked"  = 派发前就被权限/可行性拦下，从未真正执行；
#  - "partial"  = 跑了一半，高风险写被 Human-in-the-Loop 闸门挂起（Verifier 的正常分支）；
#  - "error"    = 自身报错（非上游拖累）才算 KIND_FAILED 故障点。
# "partial" 映射到 BLOCKED（等待人工批准 = 被挂起），而不是 CANCELLED，
# 否则会把「正常待批」误判成需要修的故障。
_RESULT_STATUS_MAP = {
    "ok": COMPLETED,
    "blocked": BLOCKED,
    "partial": BLOCKED,
    "error": CANCELLED,
}

# 终态归因（诊断用，与五态正交）
KIND_COMPLETED = "completed"
KIND_BLOCKED = "blocked"
KIND_SKIPPED = "skipped"      # 上游失败连锁
KIND_FAILED = "failed"        # 真正的故障点


class IllegalTransition(RuntimeError):
    """非法状态转移。静默容错会让看板失去意义，因此显式抛错。"""


@dataclass
class TaskCard:
    """看板上的一张任务卡。"""

    task_id: str
    role: str
    intent: str
    state: str = PENDING
    risk: str = "low"
    terminal_kind: str = ""      # completed / blocked / skipped / failed
    depends_on: tuple = ()
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    ended_at: Optional[float] = None
    reason: str = ""
    history: List[Dict[str, Any]] = field(default_factory=list)
    # 义务图（Eureka）：落 completed 前必须被 evidence 独立满足；
    # 默认为空 = 不拦截（零破坏），只有显式 attach 才启用验收。
    obligations: List[Any] = field(default_factory=list)
    obligation_report: Optional[Dict[str, Any]] = None
    # 多 Agent 仲裁层（PACMAN 借鉴）：记录「执行者自证 vs 独立验收」的显式冲突，
    # 不静默吞掉任何矛盾。仅当任务挂义务且执行者自证 ok 时生成。
    arbitration: Optional[Dict[str, Any]] = None

    @property
    def elapsed_ms(self) -> Optional[int]:
        if self.started_at is None:
            return None
        end = self.ended_at if self.ended_at is not None else time.time()
        return int((end - self.started_at) * 1000)

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "role": self.role,
            "intent": self.intent,
            "state": self.state,
            "risk": self.risk,
            "terminal_kind": self.terminal_kind,
            "depends_on": list(self.depends_on),
            "elapsed_ms": self.elapsed_ms,
            "reason": self.reason,
            "history": self.history,
            "obligations": [ob.to_dict() for ob in self.obligations],
            "obligation_report": self.obligation_report,
            "arbitration": self.arbitration,
        }


class TaskBoard:
    """显式任务看板：五态机 + 时间戳 + 转移审计 + 快照落盘。

    用法
    ----
        board = TaskBoard(run_id="run-20260830-220000")
        board.register_all(tasks)                 # TaskSpec 列表 → pending
        board.transition("t1-plan", ACTIVE)
        board.absorb_result(worker_result)        # 按 WorkerResult.status 自动落终态
        board.snapshot(run_dir)                   # 写 board.json
        print(board.render())                     # 控制台可读看板
    """

    def __init__(self, run_id: str = "", tracer=None):
        self.run_id = run_id
        self.tracer = tracer
        self._cards: Dict[str, TaskCard] = {}
        self._order: List[str] = []

    # ── 注册 ────────────────────────────────────────────────────────
    def register(self, task) -> TaskCard:
        """把一个 TaskSpec（或等价 duck-type 对象）登记为 pending 卡片。"""
        tid = getattr(task, "task_id", None) or str(task)
        if tid in self._cards:
            return self._cards[tid]
        card = TaskCard(
            task_id=tid,
            role=getattr(task, "role", "unknown"),
            intent=getattr(task, "intent", ""),
            risk=getattr(task, "risk", "low"),
            depends_on=tuple(getattr(task, "depends_on", ()) or ()),
        )
        self._cards[tid] = card
        self._order.append(tid)
        return card

    def register_all(self, tasks) -> List[TaskCard]:
        return [self.register(t) for t in tasks]

    # ── 转移 ────────────────────────────────────────────────────────
    def transition(self, task_id: str, to_state: str, reason: str = "",
                   evidence: Optional[Dict[str, Any]] = None) -> TaskCard:
        """执行一次状态转移，非法转移抛 IllegalTransition。

        `to_state == COMPLETED` 且该卡挂了义务时，会先用 evidence 做**独立验收**：
        未通过则强制改落 `BLOCKED`（Eureka 显式验收语义——完成不是自证）。
        """
        if to_state not in STATES:
            raise IllegalTransition(f"未知状态 {to_state!r}，可选 {STATES}")
        card = self._cards.get(task_id)
        if card is None:
            raise IllegalTransition(f"任务 {task_id!r} 未在看板注册")

        frm = card.state
        if to_state == frm:
            return card                                    # 幂等：同态重入不报错
        allowed = _TRANSITIONS.get(frm, ())
        if to_state not in allowed:
            raise IllegalTransition(
                f"非法转移 {task_id}: {frm} → {to_state}（合法：{allowed or '终态不可转移'}）"
            )

        # ── 义务图验收（Eureka）：completed 不是自证，需要独立复核 ──
        # 只有挂了义务的卡才会被拦截；空义务集行为与改造前完全一致。
        if to_state == COMPLETED and card.obligations and ObligationSet is not None:
            report = ObligationSet(card.obligations).evaluate(evidence or {}, task_id)
            card.obligation_report = report.to_dict()
            if not report.all_satisfied:
                reason = (reason + " | " if reason else "") + \
                         f"义务验收未通过：{report.summary()}"
                to_state = BLOCKED
                if card.terminal_kind == KIND_COMPLETED:
                    card.terminal_kind = KIND_BLOCKED   # 是被拦下，不是故障点

        now = time.time()
        card.state = to_state
        if to_state == ACTIVE and card.started_at is None:
            card.started_at = now
        if to_state in TERMINAL_STATES or to_state == BLOCKED:
            card.ended_at = now
        if reason:
            card.reason = reason
        card.history.append({
            "from": frm, "to": to_state, "at": round(now, 3), "reason": reason,
        })

        if self.tracer is not None:
            try:
                self.tracer.audit(
                    "board_transition", actor=card.role, action=task_id,
                    decision=to_state,
                    detail={"from": frm, "to": to_state, "reason": reason},
                )
            except Exception:                              # noqa: BLE001
                pass                                       # 审计失败不阻塞主流程
        return card

    def absorb_result(self, result,
                      evidence: Optional[Dict[str, Any]] = None) -> Optional[TaskCard]:
        """按 WorkerResult.status 自动落终态（ok→completed / blocked→blocked /
        error→cancelled）。未注册的任务先自动补登记，保证看板不漏项。

        义务图：evidence 未显式传入时，尝试从 `result.output` 自动提取
        （支持 `output["evidence"]` 或 `output["trust_row"]` 两种约定）。
        若该卡挂了义务而 evidence 不足，任务会被**强制拦在 blocked**。
        """
        tid = getattr(result, "task_id", None)
        if not tid:
            return None
        if tid not in self._cards:
            self.register(result)
        card = self._cards[tid]
        if evidence is None:
            evidence = self._evidence_of(result)
        status = getattr(result, "status", "error")
        to_state = _RESULT_STATUS_MAP.get(status, CANCELLED)
        card.terminal_kind = self._kind_of(result, status)
        # pending → 终态需先过 active，保持转移链完整可审计
        if card.state == PENDING and to_state != CANCELLED:
            self.transition(tid, ACTIVE, reason="runtime 派发")
        if card.state in TERMINAL_STATES:
            return card
        # 原因文案：error 用原始错误信息；partial 是被闸门挂起（而非故障），
        # 给出可读提示，避免控制台只看到 "status=partial" 这种含糊字样。
        if status == "partial":
            reason = "部分完成，高风险写操作待人工批准（--approve 重跑）"
        else:
            reason = getattr(result, "error", "") or f"status={status}"
        card = self.transition(tid, to_state, reason=reason, evidence=evidence)

        # ── 多 Agent 仲裁层（PACMAN 借鉴）─────────────────────────────
        # 真实冲突点：执行者自证 ok（落入 completed），但该卡挂了义务、独立验收判 rejected。
        # 改造前这个矛盾被**静默吞掉**（直接改落 blocked，不记录"这里发生过冲突"）。
        # 现在把「自证主张」与「独立验收主张」交给仲裁层，冲突被显式点名、裁决可追溯。
        # 仅对"执行者自证 ok + 挂了义务"触发；非 ok 路径（error/blocked/partial）不评仲裁。
        if (card is not None and card.obligations and _arbitrate is not None
                and getattr(result, "status", "") == "ok"):
            try:
                ob_report = self.verify_obligations(tid, evidence)
                claims = [_claim_worker(result), _claim_ob(ob_report)]
                v = _arbitrate(tid, [c for c in claims if c is not None])
                card.arbitration = v.to_dict()
            except Exception:                              # noqa: BLE001
                pass                                      # 仲裁失败不阻塞主流程
        return card

    @staticmethod
    def _evidence_of(result) -> Dict[str, Any]:
        """从 WorkerResult.output 提取义务判定所需的 evidence（约定优于配置）。"""
        out = getattr(result, "output", None)
        if not isinstance(out, dict):
            return {}
        if isinstance(out.get("evidence"), dict):
            return out["evidence"]
        # 多场景（一个任务跑 N 个实验）→ worst-of 聚合，有短板即不过
        rows = out.get("trust_rows")
        if isinstance(rows, list) and evidence_from_trust_rows is not None:
            return evidence_from_trust_rows(
                rows, artifacts=out.get("artifacts"), gates=out.get("gates"))
        row = out.get("trust_row")
        if isinstance(row, dict) and evidence_from_trust_row is not None:
            return evidence_from_trust_row(
                row, artifacts=out.get("artifacts"), gates=out.get("gates"))
        return {}

    # ── 义务图（Eureka）────────────────────────────────────────────
    def attach_obligations(self, task_id: str, obligations, replace: bool = True):
        """给任务挂上义务清单。挂上后，落 completed 必须通过独立验收。"""
        card = self._cards.get(task_id)
        if card is None:
            card = self.register(task_id)
        if replace or not card.obligations:
            card.obligations = list(obligations)
        else:
            card.obligations.extend(obligations)
        return card

    def attach_role_defaults(self, task) -> Optional[Any]:
        """按角色挂默认义务（角色未定义义务 → 不挂，行为不变）。"""
        if obligations_for_role is None:
            return None
        role = getattr(task, "role", "") or ""
        obs = obligations_for_role(role)
        if not obs:
            return None
        tid = getattr(task, "task_id", None) or str(task)
        return self.attach_obligations(tid, obs)

    def attach_role_defaults_all(self, tasks):
        if not tasks:
            return []
        return [self.attach_role_defaults(t) for t in tasks]

    def verify_obligations(self, task_id: str,
                           evidence: Optional[Dict[str, Any]] = None):
        """独立验收某张卡的义务（不改变状态，返回报告或 None）。"""
        if ObligationSet is None:
            return None
        card = self._cards.get(task_id)
        if card is None or not card.obligations:
            return None
        return ObligationSet(card.obligations).evaluate(evidence or {}, task_id)

    def obligation_verdicts(self) -> Dict[str, Any]:
        """看板级义务验收汇总：accepted / rejected / no_obligations 计数。"""
        out = {"accepted": 0, "rejected": 0, "no_obligations": 0,
               "rejected_tasks": []}
        for tid in self._order:
            rep = self._cards[tid].obligation_report
            if not rep:
                out["no_obligations"] += 1
                continue
            if rep.get("verdict") == "accepted":
                out["accepted"] += 1
            else:
                out["rejected"] += 1
                out["rejected_tasks"].append(
                    {"task_id": tid, "summary": rep.get("summary", "")})
        return out

    def arbitration_records(self) -> Dict[str, Any]:
        """看板级仲裁汇总：列出「执行者自证放行、独立验收拦下」的显式冲突。

        这是 PACMAN 第四级「输出层」的落点——多主张解冲突 + 安全边界被显式记录，
        而不是让最后一个写入者生效。返回计数与逐条冲突，供报告渲染与审计。
        """
        out = {"n_arbitrated": 0, "n_blocked_by_obligations": 0,
               "n_conflicts": 0, "conflicts": []}
        for tid in self._order:
            rec = self._cards[tid].arbitration
            if not rec:
                continue
            out["n_arbitrated"] += 1
            if rec.get("blocks"):
                out["n_blocked_by_obligations"] += 1
            if rec.get("conflicts"):
                out["n_conflicts"] += len(rec["conflicts"])
                out["conflicts"].append({
                    "task_id": tid,
                    "verdict": rec.get("verdict"),
                    "rule": rec.get("rule"),
                    "summary": rec.get("summary", ""),
                })
        return out

    @staticmethod
    def _kind_of(result, status: str) -> str:
        """判定终态归因。error 且 skipped → 上游连锁；error 非 skipped → 故障点。

        "partial" 视为被 Human-in-the-Loop 闸门挂起（非故障），归入 KIND_BLOCKED；
        这样 `failure_points()` 不会把「等人工批准」的任务误标为要修的故障点。
        """
        if status == "ok":
            return KIND_COMPLETED
        if status in ("blocked", "partial"):
            return KIND_BLOCKED
        if getattr(result, "skipped", False):
            return KIND_SKIPPED
        return KIND_FAILED

    # ── 查询 ────────────────────────────────────────────────────────
    def get(self, task_id: str) -> Optional[TaskCard]:
        return self._cards.get(task_id)

    def by_state(self, state: str) -> List[TaskCard]:
        return [self._cards[t] for t in self._order if self._cards[t].state == state]

    def counts(self) -> Dict[str, int]:
        c = {s: 0 for s in STATES}
        for tid in self._order:
            c[self._cards[tid].state] += 1
        return c

    def kinds(self) -> Dict[str, int]:
        """终态归因计数：failed 是故障点，skipped 只是连锁反应。"""
        k = {KIND_COMPLETED: 0, KIND_BLOCKED: 0, KIND_SKIPPED: 0, KIND_FAILED: 0}
        for tid in self._order:
            kd = self._cards[tid].terminal_kind
            if kd in k:
                k[kd] += 1
        return k

    def failure_points(self) -> List[str]:
        """真正的故障点（自身报错，非上游拖累）——排障时先看这里。"""
        return [tid for tid in self._order
                if self._cards[tid].terminal_kind == KIND_FAILED]

    @property
    def cards(self) -> List[TaskCard]:
        return [self._cards[t] for t in self._order]

    def all_terminal(self) -> bool:
        return all(self._cards[t].is_terminal for t in self._order) if self._order else True

    def health(self) -> str:
        """看板整体健康度：ok（全完成）/ partial（有挡住的）/ degraded（有取消的）。"""
        c = self.counts()
        if c[CANCELLED]:
            return "degraded"
        if c[BLOCKED] or c[PENDING] or c[ACTIVE]:
            return "partial"
        return "ok"

    # ── 导出 ────────────────────────────────────────────────────────
    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "counts": self.counts(),
            "kinds": self.kinds(),
            "failure_points": self.failure_points(),
            "health": self.health(),
            "all_terminal": self.all_terminal(),
            "obligations": self.obligation_verdicts(),
            "arbitration": self.arbitration_records(),
            "cards": [c.to_dict() for c in self.cards],
        }

    def snapshot(self, out_dir: str, filename: str = "board.json") -> str:
        """把看板当前状态落盘（状态 checkpoint，配合 Policy.restore 使用）。"""
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, filename)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
        return path

    def render(self) -> str:
        """控制台可读看板（CLI 摘要用）。"""
        icons = {PENDING: "·", ACTIVE: "▶", COMPLETED: "✓",
                 BLOCKED: "!", CANCELLED: "×"}
        # 同一个 × 可能是"被上游跳过"，也可能是"自己挂了"——用 kind 区分
        kind_tag = {KIND_SKIPPED: "跳过", KIND_FAILED: "故障",
                    KIND_BLOCKED: "拦截", KIND_COMPLETED: ""}
        lines = []
        for c in self.cards:
            ms = f"{c.elapsed_ms}ms" if c.elapsed_ms is not None else "-"
            tag = kind_tag.get(c.terminal_kind, "")
            state_show = f"{c.state}/{tag}" if tag else c.state
            tail = f"  ← {c.reason}" if c.reason and c.state != COMPLETED else ""
            lines.append(f"  {icons.get(c.state, '?')} {c.task_id:<12} "
                         f"{c.role:<9} {state_show:<16} {ms:>8}{tail}")
        return "\n".join(lines)

    def render_mermaid(self) -> str:
        """Render task DAG as Mermaid flowchart (inspired by DataFlow-Agent).

        Outputs a Mermaid diagram showing task dependencies and states.
        Useful for visualizing the execution flow in documentation or UI.

        Returns:
            Mermaid syntax string for the task DAG.
        """
        state_colors = {
            PENDING: "#6b7280",      # gray
            ACTIVE: "#3b82f6",       # blue
            COMPLETED: "#10b981",    # green
            BLOCKED: "#f59e0b",      # amber
            CANCELLED: "#ef4444",    # red
        }
        state_icons = {
            PENDING: "⏳",
            ACTIVE: "▶",
            COMPLETED: "✓",
            BLOCKED: "⚠",
            CANCELLED: "✗",
        }

        lines = ["```mermaid", "flowchart TD"]

        # Add nodes with state styling
        for card in self.cards:
            node_id = f"T{card.task_id}"
            label = f"{state_icons.get(card.state, '?')} {card.task_id}\\n{card.role}"
            color = state_colors.get(card.state, "#6b7280")
            lines.append(f"    {node_id}[\"{label}\"]")
            lines.append(f"    style {node_id} fill:{color},color:white")

        # Add dependency edges
        for card in self.cards:
            for dep in card.depends_on:
                dep_id = f"T{dep}"
                lines.append(f"    {dep_id} --> {node_id}")

        lines.append("```")
        return "\n".join(lines)

    def render_dot(self) -> str:
        """Render task DAG as Graphviz DOT format.

        Returns:
            DOT syntax string for the task DAG.
        """
        state_colors = {
            PENDING: "gray",
            ACTIVE: "lightblue",
            COMPLETED: "lightgreen",
            BLOCKED: "lightyellow",
            CANCELLED: "lightcoral",
        }

        lines = [
            "digraph TaskDAG {",
            "    rankdir=TB;",
            "    node [shape=box, style=filled];",
            "    edge [arrowhead=vee];",
        ]

        for card in self.cards:
            color = state_colors.get(card.state, "gray")
            label = f"{card.task_id}\\n{card.role}\\n[{card.state}]"
            lines.append(f'    "{card.task_id}" [label="{label}", fillcolor="{color}"];')

        for card in self.cards:
            for dep in card.depends_on:
                lines.append(f'    "{dep}" -> "{card.task_id}";')

        lines.append("}")
        return "\n".join(lines)

    def visualization_summary(self) -> Dict[str, Any]:
        """Get visualization statistics for the task DAG.

        Returns:
            Dict with node/edge counts and state distribution.
        """
        state_counts = {s: 0 for s in STATES}
        total_edges = 0

        for card in self.cards:
            state_counts[card.state] += 1
            total_edges += len(card.depends_on)

        return {
            "total_nodes": len(self.cards),
            "total_edges": total_edges,
            "state_distribution": state_counts,
            "terminal_nodes": sum(
                1 for c in self.cards if c.state in (COMPLETED, CANCELLED)
            ),
            "has_cycles": self._detect_cycles(),
        }

    def _detect_cycles(self) -> bool:
        """Detect if the task DAG contains cycles.

        Returns:
            True if cycles detected.
        """
        visited = set()
        temp_visited = set()
        cards_by_id = {c.task_id: c for c in self.cards}

        def dfs(node_id):
            if node_id in temp_visited:
                return True  # cycle detected
            if node_id in visited:
                return False
            visited.add(node_id)
            temp_visited.add(node_id)
            card = cards_by_id.get(node_id)
            if card:
                for dep in card.depends_on:
                    if dfs(dep):
                        return True
            temp_visited.discard(node_id)
            return False

        for card in self.cards:
            if card.task_id not in visited:
                if dfs(card.task_id):
                    return True
        return False
