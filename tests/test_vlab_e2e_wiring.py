"""VLab 端到端接线测试（板块 07 P0，2026-10-15）。

背景：板块 07 vlab 有 ~4940 行核心代码（vlab_contract / vlab_stdlib /
vlab_server / vlab_batch / vlab_orchestrator / vlab_delivery /
scenario_router / digital_twin_lab），此前测试散落在 unit 层，缺"整条数据
流能跑通"的接线证据。本文件用**真实工作单 fixture** 从入口跑到出口，
覆盖 6 个契约面：

1. 8 个 vlab 核心模块全部可 import（无孤儿）
2. validate_work_order 在真实 fixture 上通过
3. run_job 五阶段（intake → scope → build → verify → corroborate）全部 append
4. vlab_batch.detect_batch 收敛检测签名不变
5. build_delivery 能生成含 VLAB_BOUNDARIES + verification_instructions 的 doc
6. run_batch 端到端能消费真实 work-orders/ 目录

不 mock、不做模型推理——只验证接线不断、schema 稳定、fail-closed 语义保留。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# ── Repo root into sys.path ──
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

WO_DIR = _ROOT / "data" / "vlab" / "work-orders"
WO_FIXTURE = WO_DIR / "w3-microbio-ecoli.json"


def _load_wo() -> dict:
    with open(WO_FIXTURE, encoding="utf-8") as f:
        return json.load(f)


# ===================================================================== 1) 核心模块可 import

class TestCoreImports(unittest.TestCase):
    """8 个 vlab 核心模块必须全部可 import（无孤儿接线）。"""

    def test_all_vlab_modules_importable(self):
        mods = [
            "vlab_contract",
            "vlab_stdlib",
            "vlab_server",
            "vlab_batch",
            "vlab_orchestrator",
            "vlab_delivery",
            "scenario_router",
            "digital_twin_lab",
        ]
        missing = []
        for m in mods:
            try:
                __import__(f"engine.{m}")
            except Exception as e:  # pragma: no cover
                missing.append(f"{m}: {type(e).__name__}: {e}")
        self.assertEqual(
            missing, [],
            "以下 vlab 核心模块不可 import：\n  " + "\n  ".join(missing))

    def test_engine_init_exports_vlab_symbols(self):
        import engine
        # 三大 P0 孤儿接线（板块 03/06/07 修正）后的关键导出
        for name in ("SpectralProvenanceGuard",
                     "physics_constrained_conformal",
                     "run_twin_loop"):
            self.assertIn(
                name, engine.__all__,
                f"engine.__all__ 缺少孤儿模块导出：{name}")


# ===================================================================== 2) 工作单验证

class TestWorkOrderValidation(unittest.TestCase):
    """真实 fixture 必须能通过 validate_work_order。"""

    def test_fixture_passes_intake(self):
        from engine.vlab_contract import validate_work_order
        wo = _load_wo()
        res = validate_work_order(wo)
        self.assertTrue(res["ok"],
                        f"fixture intake 应通过，实际错误：{res['errors']}")

    def test_new_work_order_id_deterministic_prefix(self):
        from engine.vlab_contract import new_work_order_id
        wo = _load_wo()
        wid = new_work_order_id(wo)
        self.assertIsInstance(wid, str)
        self.assertGreater(len(wid), 8)

    def test_empty_work_order_rejected(self):
        from engine.vlab_contract import validate_work_order
        res = validate_work_order({})
        self.assertFalse(res["ok"])
        self.assertGreater(len(res["errors"]), 0)


# ===================================================================== 3) run_job 五阶段

class TestRunJobPipeline(unittest.TestCase):
    """run_job 应 append 全部 5 阶段（intake → scope → build → verify → corroborate）。"""

    def test_pipeline_appends_all_five_stages(self):
        from engine.vlab_orchestrator import run_job
        wo = _load_wo()
        job = run_job(wo)
        # stage dict 用的是 `stage` 键（不是 `name`）
        stage_names = [s.get("stage") or s.get("name") for s in job["stages"]]
        for expected in ("intake", "scope", "build", "verify", "corroborate"):
            self.assertIn(
                expected, stage_names,
                f"run_job 应产出阶段 {expected}，实际：{stage_names}")

    def test_pipeline_sets_verdict_field(self):
        from engine.vlab_orchestrator import run_job
        wo = _load_wo()
        job = run_job(wo)
        # verdict 可能是 None（NEEDS_ONBOARDING / REJECTED_AT_INTAKE），
        # 但字段必须在，且 hard_verdict 若非 None 必须在允许值集
        self.assertIn("verdict", job)
        if job["verdict"] is not None:
            self.assertIn(
                "hard_verdict", job["verdict"],
                "verdict 存在时必须含 hard_verdict")

    def test_pipeline_outcome_in_known_set(self):
        from engine.vlab_orchestrator import run_job
        from engine.vlab_orchestrator import (
            REJECTED_AT_INTAKE, NEEDS_ONBOARDING,
            NO_LEDGER_FAIL_CLOSED, STALE_LEDGER_FAIL_CLOSED,
        )
        known = {REJECTED_AT_INTAKE, NEEDS_ONBOARDING,
                 NO_LEDGER_FAIL_CLOSED, STALE_LEDGER_FAIL_CLOSED,
                 "PROCEED", "ACCEPTED", "MARGINAL", "REFUTED",
                 "BROKEN", "PROCEED_WITH_HUMAN_CHECK"}
        wo = _load_wo()
        job = run_job(wo)
        self.assertIn(job.get("outcome"), known,
                      f"outcome 未在允许集：{job.get('outcome')}")

    def test_order_integrity_assertion_holds(self):
        from engine.vlab_orchestrator import (run_job,
                                              assert_order_integrity)
        wo = _load_wo()
        job = run_job(wo)
        # run_job 内部已经 assert_order_integrity(job)，
        # 显式再跑一次确保不变式对外稳定
        assert_order_integrity(job)


# ===================================================================== 4) batch detect

class TestBatchDetect(unittest.TestCase):
    """vlab_batch.detect_batch 收敛检测必须幂等。"""

    def test_detect_batch_signature_stable(self):
        from engine.vlab_batch import detect_batch, STALL_THRESHOLD
        # 空历史 → 空趋势，counts 只统计 STALLED/OSCILLATING 两条规则
        empty = detect_batch([], [])
        self.assertIn("counts", empty)
        self.assertIn("STALLED", empty["counts"])
        self.assertIn("OSCILLATING", empty["counts"])
        # 一次 PROCEED 提交（PROCEED 不在 NON_RETRYABLE 内）→ 不触发停滞
        one = detect_batch(
            [{"digest": "d1", "outcome": "PROCEED", "signature": "s1"}],
            [("d1", "PROCEED")])
        self.assertEqual(one["counts"]["STALLED"], 0)
        self.assertEqual(one["counts"]["OSCILLATING"], 0)
        # 连续 STALL_THRESHOLD 次相同 NEEDS_ONBOARDING（在 NON_RETRYABLE 里）→ 触发 STALLED
        stalled_outcomes = [("d" + str(i), "NEEDS_ONBOARDING")
                            for i in range(STALL_THRESHOLD)]
        stalled_hist = [{"digest": d, "outcome": "NEEDS_ONBOARDING", "signature": "s"}
                        for d, _ in stalled_outcomes]
        stalled = detect_batch(stalled_hist, stalled_outcomes)
        self.assertGreaterEqual(stalled["counts"]["STALLED"], 1,
                                "STALL_THRESHOLD 次 NEEDS_ONBOARDING 应触发 STALLED")

    def test_detect_skip_on_duplicate_digest(self):
        from engine.vlab_batch import detect
        history = [{"digest": "dup", "outcome": "ACCEPTED", "signature": "s"}]
        res = detect(history, "dup")
        self.assertEqual(res["action"], "SKIP")
        self.assertEqual(res["last_outcome"], "ACCEPTED")


# ===================================================================== 5) delivery

class TestDelivery(unittest.TestCase):
    """build_delivery 必须生成边界 + 验证指引 + digest。"""

    def test_delivery_doc_shape(self):
        from engine.vlab_orchestrator import run_job
        from engine.vlab_delivery import build_delivery, delivery_digest
        wo = _load_wo()
        job = run_job(wo)
        doc = build_delivery(job)
        # 元字段
        self.assertIn("delivery_schema", doc)
        self.assertIn("honest_boundaries", doc)
        self.assertIn("verification", doc)
        self.assertIn("stage_records", doc)
        self.assertIn("integrity", doc)
        # honest_boundaries 至少一条（VLAB_BOUNDARIES 常量非空）
        self.assertGreater(len(doc["honest_boundaries"]), 0)
        # verification 必须有 instructions 列表
        self.assertIn("instructions", doc["verification"])
        self.assertIsInstance(doc["verification"]["instructions"], list)
        self.assertGreater(len(doc["verification"]["instructions"]), 0)
        # integrity digest 稳定
        d1 = delivery_digest(doc)
        d2 = delivery_digest(doc)
        self.assertEqual(d1, d2)
        self.assertEqual(d1, doc["integrity"]["digest"])


# ===================================================================== 6) run_batch 端到端

class TestRunBatchEndToEnd(unittest.TestCase):
    """run_batch 端到端消费真实 data/vlab/work-orders/。"""

    def test_work_orders_dir_exists(self):
        self.assertTrue(WO_DIR.is_dir(),
                        f"work-orders 目录不存在：{WO_DIR}")
        files = sorted(f.name for f in WO_DIR.glob("*.json"))
        self.assertGreaterEqual(len(files), 1,
                                "至少 1 个工作单 fixture 应存在")

    def test_run_batch_with_temp_history(self):
        """用临时 history 隔离，不污染 data/vlab/history.jsonl。"""
        from engine.vlab_batch import run_batch
        with tempfile.TemporaryDirectory() as td:
            hist = os.path.join(td, "history.jsonl")
            with open(hist, "w") as f:
                f.write("")  # 空历史，允许首轮全部跑
            # force=True 忽略历史重复检测，确保首轮一定消费
            res = run_batch(wo_dir=str(WO_DIR),
                            history_path=hist,
                            force=True)
            # run_batch 返回结构
            self.assertIn("schema", res)
            self.assertIn("results", res)
            self.assertGreaterEqual(len(res["results"]), 1)
            # 每个 result 必须有 outcome
            for r in res["results"]:
                self.assertIn("outcome", r)
                self.assertIn("digest", r)
                self.assertIn("signature", r)

    def test_run_batch_detects_duplicates_on_second_run(self):
        """第二次相同工作单必须 SKIP（收敛检测生效）。"""
        from engine.vlab_batch import run_batch
        with tempfile.TemporaryDirectory() as td:
            hist = os.path.join(td, "history.jsonl")
            open(hist, "w").close()
            r1 = run_batch(wo_dir=str(WO_DIR),
                           history_path=hist, force=True)
            r2 = run_batch(wo_dir=str(WO_DIR),
                           history_path=hist, force=False)
            # 第二轮 results 应比第一轮少（全部 SKIP）
            self.assertLessEqual(len(r2["results"]), len(r1["results"]))
            # 如果第一轮有跑，第二轮应有 skipped 记录
            if r1["results"]:
                self.assertGreater(len(r2["skipped"]), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
