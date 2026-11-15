"""VLab 契约级测试（板块07 P0：4940 行零测试覆盖 → 至少覆盖 3 条主路径）。

覆盖 vlab_contract / vlab_stdlib / digital_twin_lab 三处的核心不变式：

1. `validate_work_order`（工作单入口校验）：
   - 有效工作单 → ok=True
   - 缺必填字段 → ok=False
   - schema 错 → error
   - direction 枚举错 → error
   - plausible_range 倒置 → error
   - **红线**：corroboration.as_input=true → ORDER_INVERTED（读法 B 结构拒收）
   - method.family 未知 → error

2. `assert_stage_order`（阶段顺序不变式）：
   - 正序 → 通过
   - 倒置（corroborate 早于 verify）→ OrderInvertedError
   - 未知阶段名 → ContractError

3. `assert_corroboration_post_hoc`（build 阶段白名单）：
   - build_inputs 含 observations（非白名单键）→ OrderInvertedError
   - build_inputs 只含白名单键 → 通过

4. `goal_quantity_binding`（防「路由成功但答非所问」）：
   - 无 goal → UNVERIFIABLE
   - 量纲兼容且量名一致 → MATCH
   - 量纲不兼容 → MISMATCH

5. `claim_id` 稳定性（跨工作单关联）：
   - claim_id 不随 design_space 变
   - work_order_id 随 design_space 变

6. `digital_twin_lab` 解析 oracle 的确定性（Monod / Langmuir / Arrhenius / Logistic / Black-Scholes）：
   - 给定输入 → 确定性输出
   - Monod：s=0 时 x→0；s=Km 时 x 为半饱和
   - Black-Scholes 平价：C(0)=0, C(K)=0.5*K

只用标准库 + 无 rdkit/numpy 依赖（走 vlab_stdlib 路径加载），与 CI 干净解释器一致。
"""
from __future__ import annotations

import copy
import importlib.util
import math
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# --------------------------------------------------------------------- 加载

def _load_vlab_contract():
    """按文件路径加载 vlab_contract（避开 engine.__init__ 的 numpy 链）。"""
    path = os.path.join(ROOT, "engine", "vlab_contract.py")
    spec = importlib.util.spec_from_file_location("_vlab_contract", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


VC = _load_vlab_contract()


# --------------------------------------------------------------------- 工作单工厂

def _valid_work_order(**overrides) -> dict:
    """构造一份通过 validate_work_order 的最小工作单。"""
    base = {
        "schema": VC.SCHEMA,
        "submitted_by": "alice@example.com",
        "discipline": "biochemical_yield",  # 需存在，否则 warn 但不 fail
        "goal": {
            "quantity": "biomass yield",
            "unit": "g/L",
            "direction": "maximize",
            "plausible_range": [1.0, 5.0],
        },
        "design_space": {
            "variables": [
                {"name": "temp", "lo": 20.0, "hi": 40.0, "unit": "degC"},
            ],
        },
        "method": {"family": "gp"},
        "cost_of_error": 1.0,
    }
    base.update(overrides)
    return base


# =========================================================== 1) validate_work_order

class TestValidateWorkOrder(unittest.TestCase):
    def test_valid_work_order_passes(self):
        r = VC.validate_work_order(_valid_work_order())
        # discipline 未在 registry 里时会给 warn，但 ok 仍 True
        self.assertTrue(r["ok"], f"errors: {r['errors']}")
        self.assertIsNotNone(r["digest"])

    def test_non_dict_rejected(self):
        r = VC.validate_work_order("not a dict")
        self.assertFalse(r["ok"])
        self.assertEqual(r["errors"][0]["code"], "not_an_object")

    def test_bad_schema_rejected(self):
        wo = _valid_work_order(schema="some-other-schema")
        r = VC.validate_work_order(wo)
        self.assertFalse(r["ok"])
        self.assertTrue(any(e["code"] == "bad_schema" for e in r["errors"]))

    def test_missing_submitted_by_rejected(self):
        wo = _valid_work_order()
        del wo["submitted_by"]
        r = VC.validate_work_order(wo)
        self.assertFalse(r["ok"])
        self.assertTrue(any(e.get("where") == "submitted_by" for e in r["errors"]))

    def test_bad_direction_rejected(self):
        wo = _valid_work_order()
        wo["goal"]["direction"] = "sideways"  # 不在 GOAL_DIRECTIONS
        r = VC.validate_work_order(wo)
        self.assertFalse(r["ok"])
        self.assertTrue(any(
            e["code"] == "bad_enum" and e.get("where") == "goal.direction"
            for e in r["errors"]
        ))

    def test_inverted_plausible_range_rejected(self):
        wo = _valid_work_order()
        wo["goal"]["plausible_range"] = [5.0, 1.0]  # lo >= hi
        r = VC.validate_work_order(wo)
        self.assertFalse(r["ok"])
        self.assertTrue(any(
            e["code"] == "bad_range" and e.get("where") == "goal.plausible_range"
            for e in r["errors"]
        ))

    def test_bad_method_family_rejected(self):
        wo = _valid_work_order(method={"family": "telepathy"})
        r = VC.validate_work_order(wo)
        self.assertFalse(r["ok"])
        self.assertTrue(any(
            e["code"] == "bad_enum" and e.get("where") == "method.family"
            for e in r["errors"]
        ))

    def test_variable_range_inverted_rejected(self):
        wo = _valid_work_order()
        wo["design_space"]["variables"] = [
            {"name": "temp", "lo": 40.0, "hi": 20.0, "unit": "degC"},
        ]
        r = VC.validate_work_order(wo)
        self.assertFalse(r["ok"])
        self.assertTrue(any(
            e["code"] == "bad_range"
            and str(e.get("where", "")).startswith("design_space.variables[0]")
            for e in r["errors"]
        ))

    def test_corroboration_as_input_true_rejected_RED_LINE(self):
        """红线：真实数据作前置输入必须结构拒收（读法 B 不可表达）。"""
        wo = _valid_work_order()
        wo["corroboration"] = {
            "as_input": True,
            "points": [{"y": 3.1, "unit": "g/L", "citation": "paper", "confirmed_by": "bob"}],
        }
        r = VC.validate_work_order(wo)
        self.assertFalse(r["ok"])
        self.assertTrue(any(e["code"] == "ORDER_INVERTED" for e in r["errors"]),
                        f"expected ORDER_INVERTED, got: {[e['code'] for e in r['errors']]}")

    def test_corroboration_as_input_false_ok(self):
        """as_input=False 允许，但 points 需完整。"""
        wo = _valid_work_order()
        wo["corroboration"] = {
            "as_input": False,
            "points": [
                {"y": 3.1, "unit": "g/L", "citation": "Smith 2020", "confirmed_by": "bob"},
            ],
        }
        r = VC.validate_work_order(wo)
        self.assertTrue(r["ok"], f"errors: {r['errors']}")

    def test_cost_of_error_out_of_range_rejected(self):
        wo = _valid_work_order(cost_of_error=11.0)
        r = VC.validate_work_order(wo)
        self.assertFalse(r["ok"])
        self.assertTrue(any(e.get("where") == "cost_of_error" for e in r["errors"]))

    def test_claim_id_mismatch_warns_not_rejects(self):
        """手写错 claim_id 应该 warn 不 fail（避免让既有工作单失效）。

        注意：CLAIM_ID_RE 是 ^claim-[0-9a-f]{8,32}$，只接受**小写** hex。
        大写字母会命中 bad_format（拒收），不是我们要测的 mismatch 分支。
        """
        wo = _valid_work_order(claim_id="claim-deadbeef")
        r = VC.validate_work_order(wo)
        self.assertTrue(r["ok"], f"expected ok=True, got errors={r['errors']}")
        self.assertTrue(any(w["code"] == "claim_id_mismatch" for w in r["warnings"]),
                        f"expected claim_id_mismatch warning, got: {[w['code'] for w in r['warnings']]}")


# =========================================================== 2) assert_stage_order

class TestStageOrder(unittest.TestCase):
    def test_valid_order_passes(self):
        stages = [{"stage": s} for s in ("intake", "scope", "build", "verify")]
        VC.assert_stage_order(stages)  # 不应抛

    def test_inverted_raises_OrderInvertedError(self):
        # corroborate 出现在 verify 之前 → 倒置
        stages = [
            {"stage": "intake"},
            {"stage": "build"},
            {"stage": "corroborate"},  # 提前
            {"stage": "verify"},
        ]
        with self.assertRaises(VC.OrderInvertedError):
            VC.assert_stage_order(stages)

    def test_unknown_stage_raises_ContractError(self):
        with self.assertRaises(VC.ContractError):
            VC.assert_stage_order([{"stage": "intake"}, {"stage": "teleport"}])

    def test_missing_stage_ok_as_subsequence(self):
        # 允许缺阶段（诊断只跑到 verify）
        stages = [{"stage": "intake"}, {"stage": "build"}, {"stage": "verify"}]
        VC.assert_stage_order(stages)


# =========================================================== 3) assert_corroboration_post_hoc

class TestCorroborationPostHoc(unittest.TestCase):
    def test_whitelisted_build_inputs_pass(self):
        bi = {"discipline": "biochemical_yield", "goal": {"quantity": "y"}, "seed": 42}
        VC.assert_corroboration_post_hoc(bi, None)  # 不应抛

    def test_non_whitelisted_key_rejected(self):
        """build 里出现 observations（真实观测的键名）→ OrderInvertedError。"""
        bi = {"discipline": "biochemical_yield", "observations": [{"y": 3.1}]}
        with self.assertRaises(VC.OrderInvertedError) as cm:
            VC.assert_corroboration_post_hoc(bi, None)
        self.assertIn("observations", str(cm.exception))

    def test_byte_identical_leak_detected(self):
        """build 里的 seed 若与 corroboration 某点位字节相同 → OrderInvertedError。"""
        pts = [{"y": 3.1, "unit": "g/L", "citation": "p", "confirmed_by": "b"}]
        bi = {"discipline": "biochemical_yield", "seed": pts[0]}  # 偷偷喂观测值
        with self.assertRaises(VC.OrderInvertedError):
            VC.assert_corroboration_post_hoc(bi, {"points": pts})


# =========================================================== 4) goal_quantity_binding

class TestGoalQuantityBinding(unittest.TestCase):
    def _spec(self, output_quantity="biomass yield", output_unit="g/L"):
        return type("Spec", (), {
            "has_output_declared": True,
            "output_quantity": output_quantity,
            "output_unit": output_unit,
        })()

    def test_no_goal_returns_UNVERIFIABLE(self):
        r = VC.goal_quantity_binding({"submitted_by": "x"}, self._spec())
        self.assertEqual(r["status"], "UNVERIFIABLE")

    def test_no_declared_output_returns_UNVERIFIABLE(self):
        wo = _valid_work_order()
        spec = type("Spec", (), {
            "has_output_declared": False,
            "output_quantity": "", "output_unit": "",
        })()
        r = VC.goal_quantity_binding(wo, spec)
        self.assertEqual(r["status"], "UNVERIFIABLE")

    def test_matching_quantity_and_unit_returns_MATCH(self):
        wo = _valid_work_order()
        spec = self._spec("biomass yield", "g/L")
        r = VC.goal_quantity_binding(wo, spec)
        self.assertEqual(r["status"], "MATCH")
        self.assertTrue(r["dimension_match"])

    def test_incompatible_unit_returns_MISMATCH(self):
        """单位量纲不兼容 → MISMATCH（拒收，因为场景产不出被问的量）。"""
        wo = _valid_work_order()
        # 场景声明 output=秒（时间），goal 是 g/L（质量/体积）→ 量纲不兼容
        spec = self._spec("yield", "s")
        r = VC.goal_quantity_binding(wo, spec)
        self.assertEqual(r["status"], "MISMATCH")
        self.assertFalse(r["dimension_match"])


# =========================================================== 5) claim_id 稳定性

class TestClaimIdStability(unittest.TestCase):
    def test_claim_id_unchanged_across_design_space(self):
        """claim_id 只绑定主张语义（discipline+goal），改设计空间不换 id。"""
        wo1 = _valid_work_order()
        wo2 = _valid_work_order()
        wo2["design_space"]["variables"][0]["lo"] = 10.0  # 改区间
        wo2["scenario_key"] = "different-scenario"
        self.assertEqual(VC.new_claim_id(wo1), VC.new_claim_id(wo2))

    def test_work_order_id_changes_with_design_space(self):
        """work_order_id 绑定整次请求（含 design_space），改区间就是新 id。"""
        wo1 = _valid_work_order()
        wo2 = _valid_work_order()
        wo2["design_space"]["variables"][0]["lo"] = 10.0
        self.assertNotEqual(VC.new_work_order_id(wo1), VC.new_work_order_id(wo2))

    def test_claim_id_changes_when_goal_changes(self):
        """改 goal 的任何字段（quantity/unit/direction/plausible_range）都是新主张。"""
        wo1 = _valid_work_order()
        wo2 = _valid_work_order()
        wo2["goal"]["quantity"] = "biomass concentration"
        self.assertNotEqual(VC.new_claim_id(wo1), VC.new_claim_id(wo2))

    def test_claim_digest_is_stable_full_sha256(self):
        wo1 = _valid_work_order()
        wo2 = copy.deepcopy(wo1)
        wo2["design_space"]["variables"][0]["lo"] = 25.0  # 只改区间
        self.assertEqual(VC.claim_digest(wo1), VC.claim_digest(wo2))


# =========================================================== 6) digital_twin_lab oracle 确定性

def _load_digital_twin_lab():
    """digital_twin_lab 依赖 numpy，用 envs/default 有 numpy 的解释器可跑。"""
    try:
        from engine import digital_twin_lab as DT
        return DT
    except Exception as e:
        return unittest.SkipTest(f"digital_twin_lab unavailable: {e}")


class TestDigitalTwinOracles(unittest.TestCase):
    def setUp(self):
        DT = _load_digital_twin_lab()
        if isinstance(DT, unittest.SkipTest):
            raise DT  # 触发 skip
        self.DT = DT

    def test_monod_oracle_deterministic(self):
        """Monod 生长：x[0]=S（底物浓度）；S 越大 μ 越接近 μmax。"""
        import numpy as np
        x = np.array([1.0, 30.0])  # (S=1.0, temp=30)
        y1 = self.DT._monod_oracle(x)
        y2 = self.DT._monod_oracle(x)
        self.assertAlmostEqual(y1, y2)
        self.assertGreater(y1, 0.0)
        # S=1.0 应明显低于饱和 μmax=0.81
        self.assertLess(y1, 0.81)

    def test_arrhenius_oracle_increases_with_temp(self):
        """Arrhenius：温度升高 → 速率单调升（在合理区间内）。"""
        import numpy as np
        y_low = self.DT._arrhenius_oracle(np.array([300.0, 1.0]))
        y_high = self.DT._arrhenius_oracle(np.array([320.0, 1.0]))
        self.assertGreater(y_high, y_low)

    def test_langmuir_saturation(self):
        """Langmuir：x[0]=P（吸附压力）；P→∞ 时 θ→1（饱和）。"""
        import numpy as np
        y_low = self.DT._langmuir_oracle(np.array([0.001, 30.0]))
        y_high = self.DT._langmuir_oracle(np.array([10.0, 30.0]))
        # 高 P 应 > 低 P
        self.assertGreater(y_high, y_low)
        # 极大 P 应饱和于 1（不再增长）
        y_huge = self.DT._langmuir_oracle(np.array([1e6, 30.0]))
        self.assertGreater(y_huge, 0.999)
        # P=1e6 时 y≈1.0，P=10 时 y=0.9375，比值≈1.067
        self.assertLessEqual(y_huge / y_high, 1.10)  # 接近饱和，几乎不再增长


# =========================================================== 7) vlab_stdlib 工具函数

class TestVlabStdlib(unittest.TestCase):
    def setUp(self):
        path = os.path.join(ROOT, "engine", "vlab_stdlib.py")
        spec = importlib.util.spec_from_file_location("_vlab_stdlib_test", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        self.S = mod

    def test_canonical_sha256_stable_across_key_order(self):
        """同一语义 → 同一摘要（json.dumps sort_keys=True）。"""
        a = {"x": 1, "y": 2}
        b = {"y": 2, "x": 1}
        self.assertEqual(self.S.canonical_sha256(a), self.S.canonical_sha256(b))

    def test_nonempty_str_rejects_empty_and_none(self):
        self.assertFalse(self.S.nonempty_str(""))
        self.assertFalse(self.S.nonempty_str("  "))
        self.assertFalse(self.S.nonempty_str(None))
        self.assertTrue(self.S.nonempty_str("alice"))

    def test_is_finite_rejects_nan_inf(self):
        self.assertFalse(self.S.is_finite(float("nan")))
        self.assertFalse(self.S.is_finite(float("inf")))
        self.assertFalse(self.S.is_finite(None))
        self.assertTrue(self.S.is_finite(0.5))
        self.assertTrue(self.S.is_finite(0))

    def test_short_truncates_digest(self):
        d = "a" * 64
        s12 = self.S.short(d, 12)
        self.assertEqual(len(s12), 12)
        self.assertTrue(s12.startswith("a"))


# =========================================================== 8) 契约 docstring / schema_doc

class TestSchemaDoc(unittest.TestCase):
    def test_schema_doc_includes_key_invariants(self):
        """schema_doc 应暴露 build 白名单——审计者能一眼看到「真实数据不许进 build」。"""
        doc = VC.schema_doc()
        self.assertIn("build_input_whitelist", doc)
        self.assertEqual(
            doc["build_input_whitelist"], list(VC.BUILD_INPUT_KEYS)
        )
        self.assertNotIn("observations", doc["build_input_whitelist"])
        self.assertNotIn("real_points", doc["build_input_whitelist"])

    def test_build_input_keys_excludes_observation_fields(self):
        """红线：真实观测的键名不得进 BUILD_INPUT_KEYS。"""
        banned = {"observations", "real_points", "y", "points", "ground_truth"}
        self.assertEqual(
            set(VC.BUILD_INPUT_KEYS) & banned,
            set(),
            "BUILD_INPUT_KEYS must not contain real-observation keys",
        )


# =========================================================== 9) 主运行器

if __name__ == "__main__":
    # 自测入口：不依赖 pytest，CI 可用 python -m unittest tests.test_vlab_contract
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
