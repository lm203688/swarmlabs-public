"""多 Agent 主张仲裁层（2026-09-08）

借鉴来源
--------
DIII-D 托卡马克 **PACMAN** 框架（PPPL + 普林斯顿，Nuclear Fusion 2026-09-02）的
第四级「输出层」：

    "An output stage resolves conflicts between controllers, enforces hardware
     safety limits, and transmits commands to the tokamak."

即：**多个控制器同时给出指令时，必须有一个独立阶段解冲突 + 强制安全边界**，
而不是让最后一个写入者生效，也不是让执行者自己说了算。

为什么需要
----------
义务图落地后出现了一个真实矛盾：执行者自证 `status=ok`、成熟度面板说
"已收敛 100 分"，而独立验收把任务判为 blocked。改造前这个矛盾被**静默吞掉**
（直接改落 blocked，没有任何地方记录"这里发生过冲突"）。

仲裁层把它变成显式数据：每个主张都被记录，冲突被点名，裁决依据可追溯。

裁决规则（优先级从高到低）
--------------------------
1. **安全优先**：任何「带证据的 block 主张」胜过所有 pass 主张。
   宁可拦下有把握的，也不放行有争议的安全项。
2. **独立验收 > 执行者自证**：`independent_check` 压过 `self_report`。
   这是义务图的核心命题——执行者不能给自己发合格证。
3. **有证据 > 无证据**：拿不出证据的主张不参与放行投票。
4. **同级冲突 → 保守落 block**：无法裁决时按最坏情况处理，并把冲突点名。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# 主张来源（决定权重）
SOURCE_SELF = "self_report"          # 执行者自证（WorkerResult.status）
SOURCE_INDEPENDENT = "independent_check"  # 独立验收（义务图 / verifier）
SOURCE_POLICY = "policy"             # 策略闸门（权限、人工审批）

_SOURCE_RANK = {SOURCE_SELF: 0, SOURCE_INDEPENDENT: 2, SOURCE_POLICY: 3}

VERDICT_PASS = "pass"
VERDICT_BLOCK = "block"
VERDICT_CONFLICT = "conflict"


@dataclass
class Claim:
    """一个 Agent 对同一任务的一次主张。"""
    actor: str                       # 主张者（角色名）
    source: str                      # self_report / independent_check / policy
    verdict: str                     # pass / block
    detail: str = ""
    has_evidence: bool = False
    evidence_keys: List[str] = field(default_factory=list)

    @property
    def rank(self) -> int:
        return _SOURCE_RANK.get(self.source, 0)

    def to_dict(self) -> Dict[str, Any]:
        return {"actor": self.actor, "source": self.source, "verdict": self.verdict,
                "detail": self.detail, "has_evidence": self.has_evidence,
                "evidence_keys": list(self.evidence_keys)}


@dataclass
class ArbitrationVerdict:
    """仲裁裁决——含完整冲突记录，不静默吞掉任何矛盾。"""
    task_id: str = ""
    verdict: str = VERDICT_PASS      # pass / block / conflict
    winner: Optional[Claim] = None
    rule: str = ""                   # 生效的裁决规则（可追溯）
    conflicts: List[Dict[str, Any]] = field(default_factory=list)
    claims: List[Claim] = field(default_factory=list)

    @property
    def blocks(self) -> bool:
        """是否应当拦下（conflict 按保守原则同样拦下）。"""
        return self.verdict in (VERDICT_BLOCK, VERDICT_CONFLICT)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "verdict": self.verdict,
            "blocks": self.blocks,
            "rule": self.rule,
            "winner": self.winner.to_dict() if self.winner else None,
            "n_claims": len(self.claims),
            "conflicts": list(self.conflicts),
            "claims": [c.to_dict() for c in self.claims],
        }

    def summary(self) -> str:
        if not self.claims:
            return "无主张（不仲裁）"
        if self.conflicts:
            names = "; ".join(f"{c['a']} vs {c['b']}" for c in self.conflicts[:2])
            return f"冲突 {len(self.conflicts)} 处（{names}）→ 保守拦下"
        return f"裁决 {self.verdict}（规则：{self.rule}）"


def _pair_conflicts(claims: List[Claim]) -> List[Dict[str, Any]]:
    """列出所有「一个说放行、另一个说拦下」的矛盾对。"""
    out = []
    for i, a in enumerate(claims):
        for b in claims[i + 1:]:
            if a.verdict != b.verdict:
                out.append({
                    "a": f"{a.actor}({a.source}:{a.verdict})",
                    "b": f"{b.actor}({b.source}:{b.verdict})",
                    # 冲突严重度：来源权重差越大，越说明自证与独立验收打架
                    "severity": abs(a.rank - b.rank),
                    "detail": (a.detail or b.detail)[:160],
                })
    return out


def arbitrate(task_id: str, claims: List[Claim]) -> ArbitrationVerdict:
    """对同一任务的所有主张做仲裁。

    返回 verdict；`blocks=True` 表示应当拦下（含 conflict 的保守处理）。
    """
    claims = [c for c in claims if c is not None]
    v = ArbitrationVerdict(task_id=task_id, claims=claims)
    if not claims:
        v.verdict, v.rule = VERDICT_PASS, "无主张 → 不拦截（零破坏）"
        return v

    v.conflicts = _pair_conflicts(claims)
    blocking = [c for c in claims if c.verdict == VERDICT_BLOCK]
    passing = [c for c in claims if c.verdict == VERDICT_PASS]

    # 规则 1：安全优先——带证据的 block 主张压过一切
    evidenced_blocks = [c for c in blocking if c.has_evidence]
    if evidenced_blocks:
        w = max(evidenced_blocks, key=lambda c: (c.rank, c.has_evidence))
        v.verdict, v.winner = VERDICT_BLOCK, w
        v.rule = "规则1 安全优先：带证据的拦截主张压过放行主张"
        return v

    # 规则 2：无证据的 block 主张——只要有更高来源的放行主张也不能翻盘，
    # 但来源相同时按保守处理（不放行）
    if blocking and not passing:
        w = max(blocking, key=lambda c: (c.rank, c.has_evidence))
        v.verdict, v.winner = VERDICT_BLOCK, w
        v.rule = "规则1 安全优先：仅有拦截主张"
        return v

    # 规则 3：存在冲突（有放行也有拦截，但拦截无证据）→ 比较来源权重
    if blocking and passing:
        top_block = max(blocking, key=lambda c: c.rank)
        top_pass = max(passing, key=lambda c: c.rank)
        if top_block.rank > top_pass.rank:
            v.verdict, v.winner = VERDICT_BLOCK, top_block
            v.rule = "规则2 独立验收优先：更高来源的拦截主张胜出"
            return v
        if top_pass.rank > top_block.rank:
            v.verdict, v.winner = VERDICT_PASS, top_pass
            v.rule = "规则2 独立验收优先：更高来源的放行主张胜出（拦截方来源较低）"
            return v
        # 同级且互斥 → 无法裁决
        v.verdict, v.winner = VERDICT_CONFLICT, top_block
        v.rule = "规则4 同级冲突无法裁决 → 保守拦下"
        return v

    # 规则 4：全是放行主张——选来源最高、有证据者
    w = max(passing, key=lambda c: (c.rank, c.has_evidence))
    v.verdict, v.winner = VERDICT_PASS, w
    v.rule = "规则3 证据优先：无拦截主张，取最高来源放行主张"
    return v


def claim_from_worker(result) -> Optional[Claim]:
    """把 WorkerResult 转成「执行者自证」主张。"""
    if result is None:
        return None
    status = getattr(result, "status", "error")
    out = getattr(result, "output", None)
    ev = out.get("evidence") if isinstance(out, dict) else None
    keys = sorted(ev.keys()) if isinstance(ev, dict) else []
    return Claim(
        actor=getattr(result, "role", "") or getattr(result, "agent", "") or "worker",
        source=SOURCE_SELF,
        verdict=VERDICT_PASS if status == "ok" else VERDICT_BLOCK,
        detail=f"status={status}" + (f" / {getattr(result,'error','')}" if getattr(result, "error", "") else ""),
        has_evidence=bool(keys),
        evidence_keys=keys,
    )


def claim_from_obligations(report) -> Optional[Claim]:
    """把 ObligationReport 转成「独立验收」主张。"""
    if report is None:
        return None
    if not getattr(report, "results", None):
        return None      # 无义务 = 不参与仲裁（零破坏）
    ok = bool(getattr(report, "all_satisfied", False))
    return Claim(
        actor="obligation_graph",
        source=SOURCE_INDEPENDENT,
        verdict=VERDICT_PASS if ok else VERDICT_BLOCK,
        detail=getattr(report, "summary", lambda: "")() if hasattr(report, "summary") else "",
        has_evidence=True,     # 义务判定本身即证据（每项都有 detail）
        evidence_keys=[r.obligation_id for r in report.results],
    )
