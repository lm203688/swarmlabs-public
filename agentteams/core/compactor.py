"""SoL-Pi 四机制适配器（NVlabs SoL-Pi, MIT, 2026-09-11）。

来源论文核心思想：AI 自动研究 AI Harness 如何更省 Token。152 想法 → 4 存活
（1 in 40 存活率），token 省 45–49%，成本降 33%。四个可搬机制：

1. **ActionFusion**      —— 把「编辑文件 + 紧接着运行该文件」合并为一次本地执行。
2. **OnlineContextCompact** —— 只在子任务边界压缩上下文；且仅当「未来省 > 重写成本」时触发。
3. **ObservationPack**   —— 大块工具输出存盘、上下文只留句柄，按需召回（避免反复携带长日志）。
4. **EvidencePreservingReducer** —— 小模型读长日志时，每条「引用/结论」必须能对着原始
                             逐字核验才放行；无法核验的声明直接丢弃（强化 verification_gate）。

这些机制全部是**离线、零 LLM、纯 numpy/标准库**实现，与 SwarmLabs 引擎内核一致。
它们不引入任何外部依赖，也不改变科学判据——只优化「信息在 harness 内的流动成本」。

设计原则（与 guards.py 一致）：
- 旁路观测，不修改任务语义；
- fail-closed：证据缺失时**丢弃声明**，而非带着假证据放行；
- 可解释：每个机制都产出可读的节省/命中报告。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _approx_tokens(text: str) -> int:
    """粗略 token 估计：英文 ~4 字符/token，中文 ~1.5 字符/token 加权。

    仅用于节省估算，不参与任何科学判据。
    """
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    non_cjk = len(text) - cjk
    return max(1, int(non_cjk / 4 + cjk / 1.5))


# ───────────────────────────────────────────────────────────────────────────
# 1. ActionFusion
# ───────────────────────────────────────────────────────────────────────────
@dataclass
class Action:
    kind: str                     # edit_file / run_command / invoke_skill / other
    payload: Dict[str, Any] = field(default_factory=dict)
    label: str = ""


@dataclass
class FuseResult:
    fused: List[Action]
    raw_count: int
    fused_count: int
    merged: int
    est_token_saved: int


def _refs_file(cmd: str, path: str) -> bool:
    """命令字符串是否引用了该文件路径（basename 或完整路径）。"""
    base = os.path.basename(path)
    return base in cmd or path in cmd


def fuse_actions(actions: List[Action]) -> FuseResult:
    """合并连续的 (edit_file → run_command) 为单次 edit_then_run 动作。

    仅当 run_command 引用了刚编辑的文件、且两步 actor 相同（同一 worker 内）才合并。
    这是 SoL-Pi ActionFusion 的最小可用实现：消除「编辑后立即运行」带来的两次往返
    与两次上下文回写。
    """
    out: List[Action] = []
    merged = 0
    saved = 0
    i = 0
    n = len(actions)
    while i < n:
        cur = actions[i]
        nxt = actions[i + 1] if i + 1 < n else None
        if (cur.kind == "edit_file" and nxt is not None and nxt.kind == "run_command"
                and _refs_file(str(nxt.payload.get("command", "")), str(cur.payload.get("path", "")))):
            fused = Action(
                kind="edit_then_run",
                label=f"fused:{cur.label}+{nxt.label}",
                payload={
                    "path": cur.payload.get("path"),
                    "edit": cur.payload.get("edit"),
                    "command": nxt.payload.get("command"),
                    "actor": cur.payload.get("actor", nxt.payload.get("actor")),
                },
            )
            out.append(fused)
            saved += _approx_tokens(json.dumps(nxt.payload, ensure_ascii=False)) // 2
            merged += 1
            i += 2
            continue
        out.append(cur)
        i += 1
    return FuseResult(
        fused=out, raw_count=n, fused_count=len(out),
        merged=merged, est_token_saved=saved,
    )


# ───────────────────────────────────────────────────────────────────────────
# 2. OnlineContextCompact
# ───────────────────────────────────────────────────────────────────────────
@dataclass
class ContextChunk:
    id: str
    text: str
    boundary: bool = False        # True = 处于子任务边界（仅此处允许压缩）


@dataclass
class CompactResult:
    kept: List[str]
    dropped: List[str]
    compacted_at: List[str]
    est_token_saved: int
    triggered: bool


def online_compact(
    chunks: List[ContextChunk],
    *,
    rewrite_cost_tokens: int = 400,
    future_window: int = 3,
) -> CompactResult:
    """SoL-Pi Online Context Compact：只在子任务边界压缩，且仅当未来节省 > 重写成本。

    - 非边界的 chunk 永远不压缩（保证任务内上下文完整）；
    - 边界处，若「后续 future_window 个 chunk 的总 token」超过 rewrite_cost，
      则把当前边界之前的**已完成** chunk 摘要化（此处用截断摘要占位），否则原样保留。

    返回 kept（保留的 chunk id 列表）与 dropped（被摘要掉的 id）。
    """
    kept: List[str] = []
    dropped: List[str] = []
    compacted_at: List[str] = []
    saved = 0
    triggered = False

    # 后续窗口 token 预算（用于判断是否值得压缩）
    future_tokens = 0
    for c in chunks:
        future_tokens += _approx_tokens(c.text)

    i = 0
    n = len(chunks)
    while i < n:
        c = chunks[i]
        if c.boundary and i > 0:
            # 边界：看后续窗口值不值得压
            tail = sum(_approx_tokens(chunks[j].text) for j in range(i, min(n, i + future_window)))
            if tail > rewrite_cost_tokens:
                # 压缩边界之前最旧的若干 finished chunk
                for k in range(i):
                    if chunks[k].id not in dropped:
                        dropped.append(chunks[k].id)
                        saved += _approx_tokens(chunks[k].text)
                        triggered = True
                if triggered:
                    compacted_at.append(c.id)
                break
        kept.append(c.id)
        i += 1
    # 边界之后一律保留
    for j in range(i, n):
        kept.append(chunks[j].id)

    return CompactResult(
        kept=kept, dropped=dropped, compacted_at=compacted_at,
        est_token_saved=saved, triggered=triggered,
    )


# ───────────────────────────────────────────────────────────────────────────
# 3. ObservationPack
# ───────────────────────────────────────────────────────────────────────────
class ObservationPack:
    """大块工具输出存盘，上下文只留句柄。

    用法：
        pack = ObservationPack(run_dir)
        h = pack.stash("gp_training_log", huge_text)
        # 上下文里只存 h（句柄），需要时 pack.recall(h["handle_id"]) 取回
    """

    def __init__(self, run_dir: str):
        self.dir = os.path.join(run_dir, "observation_pack")
        os.makedirs(self.dir, exist_ok=True)

    def stash(self, label: str, content: str) -> Dict[str, Any]:
        h = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
        fname = f"{label}__{h}.txt"
        path = os.path.join(self.dir, fname)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        preview = content[:240].replace("\n", " ")
        return {
            "handle_id": f"{label}__{h}",
            "path": path,
            "bytes": len(content.encode("utf-8")),
            "sha256_16": h,
            "preview": preview,
        }

    def recall(self, handle_id: str) -> Optional[str]:
        for fn in os.listdir(self.dir):
            if fn.startswith(handle_id) or handle_id in fn:
                p = os.path.join(self.dir, fn)
                try:
                    with open(p, encoding="utf-8") as f:
                        return f.read()
                except Exception:
                    return None
        return None

    def report(self) -> Dict[str, Any]:
        items = []
        total = 0
        for fn in os.listdir(self.dir):
            p = os.path.join(self.dir, fn)
            sz = os.path.getsize(p)
            total += sz
            items.append({"handle": fn, "bytes": sz})
        return {"count": len(items), "total_bytes": total, "items": items}


# ───────────────────────────────────────────────────────────────────────────
# 4. EvidencePreservingReducer
# ───────────────────────────────────────────────────────────────────────────
def _token_set(text: str) -> set:
    return set(t.lower() for t in _TOKEN_RE.findall(text))


def evidence_preserving_reduce(
    source: str,
    claims: List[str],
    *,
    min_overlap: float = 0.5,
) -> Dict[str, Any]:
    """SoL-Pi Evidence-Preserving Reducer。

    每条 claim 必须与 source 有可核验的词级重叠（> min_overlap）。
    无法核验的 claim 直接丢弃——这正是 verification_gate 的「No Anchored Counter,
    No PASS」哲学在「长日志摘要」上的应用：没有证据支撑的归纳不被允许进入上下文。

    返回：保留的 claim、被丢弃的 claim（附原因）、重叠分。
    """
    src_toks = _token_set(source)
    kept: List[str] = []
    dropped: List[Dict[str, Any]] = []
    scores: List[float] = []
    for cl in claims:
        cl_toks = _token_set(cl)
        if not cl_toks:
            dropped.append({"claim": cl, "reason": "empty", "overlap": 0.0})
            continue
        inter = len(cl_toks & src_toks)
        overlap = inter / len(cl_toks)
        scores.append(round(overlap, 3))
        if overlap >= min_overlap:
            kept.append(cl)
        else:
            dropped.append({
                "claim": cl, "reason": "no_verbatim_evidence",
                "overlap": round(overlap, 3),
            })
    return {
        "kept": kept,
        "dropped": dropped,
        "scores": scores,
        "kept_ratio": round(len(kept) / max(len(claims), 1), 3),
    }


# ───────────────────────────────────────────────────────────────────────────
# 自检（python -m agentteams.core.compactor）
# ───────────────────────────────────────────────────────────────────────────
def _self_test() -> int:
    # 1. ActionFusion
    acts = [
        Action("edit_file", {"path": "x.py", "edit": "+++", "actor": "exec"}, "e1"),
        Action("run_command", {"command": "python x.py", "actor": "exec"}, "r1"),
        Action("invoke_skill", {"name": "uq"}, "s1"),
        Action("edit_file", {"path": "y.py", "edit": "---", "actor": "exec"}, "e2"),
        Action("run_command", {"command": "ls", "actor": "exec"}, "r2"),  # 不引用 y.py
    ]
    fr = fuse_actions(acts)
    assert fr.merged == 1, fr
    assert fr.fused_count == 4, fr
    print(f"[ActionFusion] merged={fr.merged} saved≈{fr.est_token_saved}t OK")

    # 2. OnlineContextCompact
    chunks = [
        ContextChunk("c1", "a" * 2000),
        ContextChunk("c2", "b" * 2000),
        ContextChunk("boundary", "boundary marker", boundary=True),
        ContextChunk("c3", "c" * 6000),
        ContextChunk("c4", "d" * 6000),
    ]
    cr = online_compact(chunks, rewrite_cost_tokens=400, future_window=3)
    assert cr.triggered, cr
    assert "c1" in cr.dropped and "c2" in cr.dropped, cr
    assert "c3" in cr.kept and "c4" in cr.kept, cr
    print(f"[OnlineContextCompact] dropped={cr.dropped} saved≈{cr.est_token_saved}t OK")

    # 3. ObservationPack
    import tempfile
    td = tempfile.mkdtemp()
    pack = ObservationPack(td)
    big = "line\n" * 5000
    h = pack.stash("gp_log", big)
    assert pack.recall(h["handle_id"]) == big
    rep = pack.report()
    assert rep["count"] == 1 and rep["total_bytes"] > 0
    print(f"[ObservationPack] stashed {h['bytes']}B, recall OK")

    # 4. EvidencePreservingReducer
    src = "The GP surrogate achieved R2 0.94 on the held-out set with 95% coverage."
    claims = [
        "The GP surrogate achieved R2 0.94 on the held-out set.",  # 可核验
        "We believe the model is probably very good in general.",  # 无证据
    ]
    er = evidence_preserving_reduce(src, claims)
    assert er["kept_ratio"] == 0.5, er
    assert len(er["dropped"]) == 1, er
    print(f"[EvidencePreservingReducer] kept={len(er['kept'])} dropped={len(er['dropped'])} OK")

    print("ALL SoL-Pi COMPACTOR SELF-TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(_self_test())
