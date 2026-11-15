"""义务图（Obligation Graph）端到端验收 —— 用**真实**可信面数据验证。

借鉴 Eureka（arXiv 2608.19047）的核心主张：任务"完成"不是执行者自证，
而必须由**机器可判定的义务 + 独立 evidence 复核**放行。本脚本证明三件事：

  A. 达标场景（rubric 全通过）      → 落 completed（放行）
  B. 不达标场景（欠覆盖，过自信）    → 强制落 blocked（拦下）
  C. 无 evidence（拿不出证据）      → 强制落 blocked（不因"没证据"就放行）

用法：
    python scripts/obligation_verify.py
"""

from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agentteams.core.board import (BLOCKED, COMPLETED,  # noqa: E402
                                   TaskBoard)
from agentteams.core.obligations import (  # noqa: E402
    derive_rubric, evidence_from_trust_row, evidence_from_trust_rows)

TS_PATH = os.path.join(ROOT, "data", "trust_surface.json")


class _Result:
    """duck-type WorkerResult：只需 task_id / role / status / output / error。"""

    def __init__(self, task_id, role, status, output=None, error=""):
        self.task_id = task_id
        self.role = role
        self.status = status
        self.output = output or {}
        self.error = error
        self.skipped = False


def _load_rows():
    with open(TS_PATH, encoding="utf-8") as f:
        return json.load(f)["rows"]


def main() -> int:
    rows = _load_rows()
    full = [r for r in rows if r.get("rubric_passed") == r.get("rubric_total")]
    under = [r for r in rows if r.get("coverage_state") == "under"]
    print(f"真实可信面：{len(rows)} 场景 · rubric 全通过 {len(full)} · 欠覆盖 {len(under)}")

    board = TaskBoard(run_id="obligation-verify")

    # 先登记任务 + 按角色挂默认义务
    specs = [SimpleNamespace(task_id=f"t{i}", role=role)
             for i, role in enumerate(("executor", "executor", "executor", "verifier"))]
    board.register_all(specs)
    board.attach_role_defaults_all(specs)
    print("已挂义务：", {s.task_id: len(board.get(s.task_id).obligations) for s in specs})

    # A. 达标场景（单场景 evidence）
    a_row = full[0] if full else rows[0]
    ev_a = evidence_from_trust_row(a_row)

    # B. 欠覆盖场景
    b_row = under[0] if under else None
    ev_b = evidence_from_trust_row(b_row) if b_row else {}

    # D. verifier：多场景 worst-of 聚合。混入一个**sharpness 不达标**场景
    #    （verifier 的义务含 sharpness，故"有短板即不过"应把它拦下）
    sharp_bad = [r for r in rows if not (r.get("rubric") or {}).get("sharpness_ok")]
    d_bad = sharp_bad[0] if sharp_bad else None
    agg_rows = [full[0]] + ([d_bad] if d_bad else [])
    ev_d = evidence_from_trust_rows(agg_rows)

    board.transition("t0", "active", reason="派发")
    board.absorb_result(_Result("t0", "executor", "ok",
                                {"trust_row": a_row}), evidence=ev_a)

    board.transition("t1", "active", reason="派发")
    board.absorb_result(_Result("t1", "executor", "ok",
                                {"trust_row": b_row}), evidence=ev_b)

    # C. 无 evidence：执行者自称 ok，但拿不出任何证据
    board.transition("t2", "active", reason="派发")
    board.absorb_result(_Result("t2", "executor", "ok", {}))

    board.transition("t3", "active", reason="派发")
    board.absorb_result(_Result("t3", "verifier", "ok",
                                {"trust_rows": agg_rows}), evidence=ev_d)

    print("\n" + board.render())
    print("\n看板义务汇总：", json.dumps(board.obligation_verdicts(),
                                         ensure_ascii=False, indent=2))

    # ── 仲裁层（PACMAN 第四级「输出层」）自检 ────────────────────────
    # 核心命题：执行者自证 ok、独立验收 rejected 时，冲突必须被**显式记录**，
    # 而不是被静默改成 blocked 后无任何痕迹。
    ok = True
    arb = board.arbitration_records()
    print("\n看板仲裁汇总：", json.dumps(arb, ensure_ascii=False, indent=2))

    arb_t1 = board.get("t1").arbitration          # 自证 ok / 验收拦下
    arb_t0 = board.get("t0").arbitration          # 双方通过
    arb_t2 = board.get("t2").arbitration          # 自证 ok / 无证据
    ok &= (arb_t1 is not None and arb_t1.get("blocks") is True
           and bool(arb_t1.get("conflicts")))
    print(f"  仲裁 t1（自证ok/验收拦下）→ blocks={arb_t1.get('blocks') if arb_t1 else None} "
          f"conflicts={len(arb_t1.get('conflicts', [])) if arb_t1 else 0}  "
          f"{'PASS' if (arb_t1 and arb_t1.get('blocks') and arb_t1.get('conflicts')) else 'FAIL'}")
    ok &= (arb_t0 is not None and arb_t0.get("blocks") is False)
    print(f"  仲裁 t0（双方通过）→ blocks={arb_t0.get('blocks') if arb_t0 else None}  "
          f"{'PASS' if (arb_t0 and arb_t0.get('blocks') is False) else 'FAIL'}")
    ok &= (arb_t2 is not None and arb_t2.get("blocks") is True)
    print(f"  仲裁 t2（自证ok/无证据）→ blocks={arb_t2.get('blocks') if arb_t2 else None}  "
          f"{'PASS' if (arb_t2 and arb_t2.get('blocks')) else 'FAIL'}")
    # 裁决规则可追溯：t1 的"自证 ok / 验收拦下"冲突必须落到一条明确的裁决规则
    # （带证据的独立验收拦下，命中「规则1 安全优先」或「规则2 独立验收优先」均正确）。
    if arb_t1:
        ok &= bool(arb_t1.get("rule"))
        print(f"  裁决依据：{arb_t1.get('rule')}  "
              f"{'PASS' if arb_t1.get('rule') else 'FAIL'}")

    got = {t: board.get(t).state for t in ("t0", "t1", "t2", "t3")}
    print("\n实际终态：", got)

    if b_row:
        ok &= (got["t0"] == COMPLETED)
        print(f"  A 达标({a_row['key']}) → {got['t0']}  "
              f"{'PASS' if got['t0'] == COMPLETED else 'FAIL'}")
        ok &= (got["t1"] == BLOCKED)
        print(f"  B 欠覆盖({b_row['key']}, cov={b_row['coverage_after']:.2f}) → {got['t1']}  "
              f"{'PASS' if got['t1'] == BLOCKED else 'FAIL'}")
        ok &= (got["t3"] == BLOCKED)
        print(f"  D verifier 聚合混入 sharpness 短板"
              f"({d_bad['key']}, NMPIW={d_bad.get('sharpness_nmpiw')}) → {got['t3']}  "
              f"{'PASS' if got['t3'] == BLOCKED else 'FAIL'}")
    ok &= (got["t2"] == BLOCKED)
    print(f"  C 无证据 → {got['t2']}  "
          f"{'PASS' if got['t2'] == BLOCKED else 'FAIL'}")

    # 单一真源自检：derive_rubric 必须复现可信面已存档的 rubric
    drift = 0
    for r in rows:
        if derive_rubric(r) != r.get("rubric"):
            drift += 1
    print(f"\n单一真源自检：derive_rubric 与存档 rubric 一致的场景 "
          f"{len(rows) - drift}/{len(rows)}（漂移 {drift}）")
    ok &= (drift == 0)

    print("\n结论：", "ALL PASS —— 义务图按预期放行/拦截" if ok else "存在 FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
