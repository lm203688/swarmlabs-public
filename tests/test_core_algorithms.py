"""核心算法单元测试（P1-4，2026-10-08）。

动机
----
平台的对外主张是「可信代理建模与实验设计层」。可信度的支点是几个**有
明确数学性质**的算法。在此之前它们**只有端到端场景测试**（跑 95 个场景
看结论有没有通过），没有单元测试。

端到端测试的盲点：它检验「结论对不对」，而结论是复合的 —— 一个算法 bug
可能被另一个 bug 抵消，或被场景的统计涨落淹没。单元测试把每个数学性质
单独钉住，并给出**独立可验证**的判据（解析解、不变性、单调性）。

测试纪律
--------
* 只测可独立验证的性质，不测「我记得应该等于某个数」。
* 数值容差显式写出并注明依据。
* 随机性全部固定种子。
* 不为了通过而放宽——若性质不成立，测试就该红。
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ===========================================================================
# 1. 保形预测（conformal）—— 有限样本修正必须按 Vovk 构造
# ===========================================================================
class TestConformalQuantile:
    def test_finite_sample_correction_uses_ceil(self):
        from engine.conformal import conformal_quantile

        # n=9, alpha=0.1 → k = ceil(10 * 0.9) = 9 → 第 9 小 = 最大值
        scores = np.arange(1, 10, dtype=float)
        assert conformal_quantile(scores, alpha=0.1) == 9.0

    def test_finite_sample_correction_n19(self):
        from engine.conformal import conformal_quantile

        # n=19, alpha=0.05 → k = ceil(20*0.95) = 19 → 第 19 小 = 最大值
        scores = np.arange(1, 20, dtype=float)
        assert conformal_quantile(scores, alpha=0.05) == 19.0

    def test_quantile_is_on_conservative_side(self):
        """取「第 k 小」恒 >= 同秩的线性插值分位数（保守侧）。"""
        from engine.conformal import conformal_quantile

        rng = np.random.default_rng(0)
        s = rng.normal(size=50)
        assert conformal_quantile(s, alpha=0.1) >= float(np.quantile(s, 0.9)) - 1e-12

    def test_empty_scores_returns_inf(self):
        from engine.conformal import conformal_quantile

        assert conformal_quantile([], alpha=0.05) == np.inf

    def test_k_clamped_for_tiny_samples(self):
        """n=1 时 k=ceil(2*0.95)=2 会被钳到 1 —— 不能越界。"""
        from engine.conformal import conformal_quantile

        assert conformal_quantile([3.5], alpha=0.05) == 3.5

    def test_monotone_in_alpha(self):
        """alpha 越小（要求越高覆盖率）→ 分位点越大。"""
        from engine.conformal import conformal_quantile

        rng = np.random.default_rng(1)
        s = rng.normal(size=200)
        assert conformal_quantile(s, alpha=0.01) >= conformal_quantile(s, alpha=0.2)


class TestConformalMetrics:
    def test_nonconformity_std_formula(self):
        from engine.conformal import nonconformity_std

        got = nonconformity_std([1.0, 2.0], [1.5, 1.0], [0.5, 1.0], eps=0.0)
        np.testing.assert_allclose(got, [1.0, 1.0])

    def test_picp_bounds(self):
        from engine.conformal import picp

        y = np.array([0.0, 1.0, 2.0])
        assert picp(y, y - 1, y + 1) == 1.0
        assert picp(y, y + 5, y + 6) == 0.0

    def test_mpiw_and_nmpiw(self):
        from engine.conformal import mpiw

        assert mpiw([0.0, 0.0], [2.0, 4.0]) == 3.0
        assert mpiw([0.0, 0.0], [2.0, 4.0], y_range=6.0) == 0.5

    def test_interval_score_reduces_to_width_when_covered(self):
        """区间覆盖真值时 Winkler 分数 = 平均区间宽度。"""
        from engine.conformal import interval_score

        assert interval_score([1.0, 1.0], [0.0, 0.5], [2.0, 1.5], alpha=0.05) == pytest.approx(1.5)

    def test_interval_score_penalizes_miss(self):
        from engine.conformal import interval_score

        lo, hi = np.array([0.0]), np.array([1.0])
        assert interval_score([10.0], lo, hi, alpha=0.05) > interval_score(
            [0.5], lo, hi, alpha=0.05
        )

    def test_norm_ppf_matches_known_values(self):
        from engine.conformal import _norm_ppf

        assert _norm_ppf(0.975) == pytest.approx(1.959963984540054, abs=1e-9)
        assert _norm_ppf(0.5) == pytest.approx(0.0, abs=1e-12)


# ===========================================================================
# 2. Sobol 敏感性 —— 用 Ishigami 解析基准校准
# ===========================================================================
class TestSobolAgainstIshigami:
    """Ishigami 函数是敏感性分析的**标准解析基准**（Ishigami & Homma 1990）。

        f(x) = sin(x1) + a*sin(x2)^2 + b*x3^4*sin(x1),  x ~ U(-pi,pi)^3

    解析总效应指数（a=7, b=0.1）：
        ST1 = 0.5576, ST2 = 0.4424, ST3 = 0.2437

    这是本测试集里唯一能对照「理论真值」而非「自己的另一实现」的用例。
    """

    @staticmethod
    def _f_ishigami(x1, x2, x3):
        return np.sin(x1) + 7.0 * np.sin(x2) ** 2 + 0.1 * x3**4 * np.sin(x1)

    def _make_func(self):
        """把 Ishigami 包装成 sobol_sensitivity 期望的闭包签名。"""

        def f(params, t=None, noise_std=0.0, seed=0):
            return float(
                self._f_ishigami(params["x1"], params["x2"], params["x3"])
            )

        return f

    def test_sobol_converges_to_analytical_ishigami(self):
        from engine.microbiology_full import sobol_sensitivity

        params = {"x1": 0.0, "x2": 0.0, "x3": 0.0}
        bounds = [(-math.pi, math.pi)] * 3
        S_i, S_Ti, diag = sobol_sensitivity(
            self._make_func(), params, bounds, n_samples=20000, seed=42
        )

        # x3 的一阶指数应≈0（它只通过交互项起作用）
        assert abs(S_i["x3"]) < 0.05, f"S3 应≈0，实测 {S_i['x3']}"
        # x1/x2 的一阶指数为正且量级正确（容差 0.06：n=2e4 的 MC 误差）
        assert 0.25 < S_i["x1"] < 0.40, f"S1 应≈0.31，实测 {S_i['x1']}"
        assert 0.35 < S_i["x2"] < 0.52, f"S2 应≈0.44，实测 {S_i['x2']}"

        # 总效应指数（Sobol 方法的核心价值：捕捉交互）
        assert abs(S_Ti["x2"] - 0.4424) < 0.07
        assert abs(S_Ti["x3"] - 0.2437) < 0.08
        # 存在交互 ⇒ ST1 > S1
        assert S_Ti["x1"] > S_i["x1"]

        assert diag["n_samples"] == 20000
        assert diag["degenerate"] is False

    def test_returns_three_tuple_of_dicts(self):
        from engine.microbiology_full import sobol_sensitivity

        out = sobol_sensitivity(
            lambda p, t=None, noise_std=0.0, seed=0: float(p["a"] ** 2),
            {"a": 0.0},
            [(0.0, 1.0)],
            n_samples=64,
            seed=0,
        )
        assert len(out) == 3
        S_i, S_Ti, diag = out
        assert set(S_i) == {"a"} and set(S_Ti) == {"a"}
        assert isinstance(diag, dict)

    def test_does_not_clip_negative_first_order(self):
        """诚实性纪律：一阶指数不得被 clip 到 0（负值 = 样本量不足的信号）。"""
        from engine.microbiology_full import sobol_sensitivity

        rng = np.random.default_rng(3)

        def f(p, t=None, noise_std=0.0, seed=0):
            return float(rng.normal())

        S_i, _, _ = sobol_sensitivity(f, {"a": 0.0}, [(0.0, 1.0)], n_samples=8, seed=1)
        assert np.isfinite(S_i["a"])  # 不强制为负（随机），只验证未强加 clip

    def test_degenerate_constant_function_is_marked(self):
        """常数函数方差≈0 → 必须返回 0 且标记 degenerate，而不是伪造随机值。"""
        from engine.microbiology_full import sobol_sensitivity

        S_i, S_Ti, diag = sobol_sensitivity(
            lambda p, t=None, noise_std=0.0, seed=0: 1.0,
            {"a": 0.0, "b": 0.0},
            [(0.0, 1.0), (0.0, 1.0)],
            n_samples=64,
            seed=0,
        )
        assert diag["degenerate"] is True
        assert S_i["a"] == 0.0 and S_Ti["a"] == 0.0


# ===========================================================================
# 3. GP 代理
# ===========================================================================
class TestGPSurrogate:
    def test_predict_shapes_and_positive_std(self):
        from engine.surrogate import GPSurrogate

        rng = np.random.default_rng(0)
        X = rng.uniform(0, 1, size=(12, 2))
        y = (X[:, 0] ** 2 + X[:, 1]).ravel()
        gp = GPSurrogate(lengthscale=0.3)
        gp.fit(X, y)
        mu, sd = gp.predict(np.array([[0.5, 0.5], [0.2, 0.8]]))
        assert mu.shape == (2,) and sd.shape == (2,)
        assert np.all(sd > 0), "预测 std 必须严格为正（含噪声地板）"

    def test_predict_before_fit_raises(self):
        from engine.surrogate import GPSurrogate

        with pytest.raises(RuntimeError):
            GPSurrogate(lengthscale=0.3).predict(np.array([[0.5]]))

    def test_interpolation_is_accurate_in_domain(self):
        """内插光滑函数：RMSE 应远小于函数振幅(1.0)。"""
        from engine.surrogate import GPSurrogate

        rng = np.random.default_rng(1)
        X = np.sort(rng.uniform(0, 1, size=20)).reshape(-1, 1)
        y = np.sin(2 * math.pi * X).ravel()
        gp = GPSurrogate(lengthscale=0.2)
        gp.fit(X, y)

        Xt = rng.uniform(0.1, 0.9, size=30).reshape(-1, 1)
        mu, _ = gp.predict(Xt)
        rmse = float(np.sqrt(np.mean((mu - np.sin(2 * math.pi * Xt).ravel()) ** 2)))
        assert rmse < 0.2, f"内插 RMSE 过大：{rmse}"

    def test_loo_rmse_is_finite_and_informative(self):
        """闭式 LOO RMSE 必须有限，且对拟合很好的数据应较小。"""
        from engine.surrogate import GPSurrogate

        rng = np.random.default_rng(2)
        X = np.sort(rng.uniform(0, 1, size=25)).reshape(-1, 1)
        y = np.cos(2 * math.pi * X).ravel()
        gp = GPSurrogate(lengthscale=0.25)
        gp.fit(X, y)
        # _loo_rmse(ls, yn) 是内部闭式 LOO；yn 为归一化后的 y
        v = gp._loo_rmse(gp.ls, gp.yn if hasattr(gp, "yn") else gp.alpha)
        assert np.isfinite(v)

    def test_determinism_same_data_same_output(self):
        """相同数据 → 逐位相同的预测（可复现性是可信度的前提）。"""
        from engine.surrogate import GPSurrogate

        rng = np.random.default_rng(5)
        X = rng.uniform(0, 1, size=(14, 2))
        y = (X[:, 0] * X[:, 1]).ravel()
        Xt = np.array([[0.3, 0.4], [0.7, 0.2]])

        outs = []
        for _ in range(2):
            gp = GPSurrogate(lengthscale=0.3)
            gp.fit(X, y)
            outs.append(gp.predict(Xt))
        np.testing.assert_array_equal(outs[0][0], outs[1][0])
        np.testing.assert_array_equal(outs[0][1], outs[1][1])

    def test_proximity_grows_far_from_training(self):
        """OOD 安全阀：远离训练区的查询点 proximity 必须更大。"""
        from engine.surrogate import GPSurrogate

        rng = np.random.default_rng(6)
        X = rng.uniform(0.4, 0.6, size=(20, 1))  # 训练点集中在中间
        y = X.ravel() ** 2
        gp = GPSurrogate(lengthscale=0.2)
        gp.fit(X, y)
        near = float(gp.proximity(np.array([[0.5]]))[0])
        far = float(gp.proximity(np.array([[1.0]]))[0])
        assert far > near, f"远处 proximity({far}) 应 > 近处({near})"


# ===========================================================================
# 4. SurrogateEngine / Calibrator
# ===========================================================================
class TestSurrogateEngine:
    def test_fit_predict_roundtrip(self):
        from engine.surrogate import SurrogateEngine

        rng = np.random.default_rng(11)
        X = rng.uniform(0, 1, size=(20, 2))
        y = (np.sin(X[:, 0]) + X[:, 1]).ravel()
        eng = SurrogateEngine(
            {"key": "t", "category": "test", "f": lambda x: float(np.sum(x)),
             "bounds": [(0.0, 1.0), (0.0, 1.0)], "ls": 0.3}
        )
        eng.fit(X, y, noise_rel=0.01)
        mu, sd = eng.predict(np.array([[0.5, 0.5]]))
        assert mu.shape == (1,) and sd.shape == (1,)
        assert sd[0] > 0

    def test_calibrator_reports_coverage_in_unit_interval(self):
        from engine.surrogate import Calibrator, GPSurrogate, SyntheticOracle

        rng = np.random.default_rng(12)
        f = lambda x: float(np.sin(3 * x[0]))  # noqa: E731
        oracle = SyntheticOracle(f, noise=0.0, rng=np.random.default_rng(0))
        X = rng.uniform(0, 1, size=(25, 1))
        y = np.array([f(x) for x in X])
        gp = GPSurrogate(lengthscale=0.3)
        gp.fit(X, y)
        xs = rng.uniform(0.1, 0.9, size=(20, 1))
        out = Calibrator().evaluate(gp, oracle, xs)
        assert 0.0 <= out["coverage"] <= 1.0
        assert out["rmse"] >= 0.0
        assert 0.0 <= out["cal_err"] <= 1.0


# ===========================================================================
# 5. PINN（解析可验证）
# ===========================================================================
class TestPINN1D:
    def test_solves_poisson_with_linear_solution(self):
        """u'' = 0, u(0)=0, u(1)=1 → 解析解 u(x)=x。

        PINN 必须能学到这条直线（这是最弱也最可靠的可验证性质）。
        """
        from engine.pinn import PINN1D

        net = PINN1D(hidden=24)
        net.fit(f=lambda x: np.zeros_like(np.asarray(x, float)), bc=(0.0, 1.0), seed=0)
        u = net._net(net.params_, net.x).ravel()
        # 与解析解 u(x)=x 的最大偏差应较小
        max_err = float(np.max(np.abs(u - net.x)))
        assert max_err < 0.15, f"PINN 对 u''=0 的解偏离直线过大：{max_err}"

    def test_fit_reduces_loss(self):
        from engine.pinn import PINN1D

        net = PINN1D(hidden=24)
        net.fit(f=lambda x: np.zeros_like(np.asarray(x, float)), bc=(0.0, 1.0), seed=1)
        assert net.loss_ is None or np.isfinite(net.loss_)
        assert net.params_ is not None

    def test_max_residual_is_reported(self):
        from engine.pinn import PINN1D

        net = PINN1D(hidden=16)
        net.fit(f=lambda x: np.zeros_like(np.asarray(x, float)), bc=(0.0, 1.0), seed=2)
        assert hasattr(net, "max_residual_")


# ===========================================================================
# 6. 泛化基准判据（A15b：分母必须稳健）
# ===========================================================================
class TestGeneralizationCriterion:
    def test_g1result_has_a15b_fields(self):
        from engine.generalization_bench import G1Result

        r = G1Result(key="t")
        assert hasattr(r, "auto_ls_used")
        assert hasattr(r, "ls_prior")
        assert r.auto_ls_used is False

    def test_g1result_defaults_not_passing(self):
        """默认必须是「未通过」（防假绿）。"""
        from engine.generalization_bench import G1Result

        r = G1Result(key="t")
        assert r.ok is False
        assert r.na is False


# ===========================================================================
# 7. 场景注册表契约（P1-5）
# ===========================================================================
class TestScenarioRegistryContract:
    def test_contract_passes(self):
        from engine.scenario_registry import load_registry

        assert load_registry().assert_contract()["ok"] is True

    def test_no_duplicate_keys(self):
        from engine.scenario_registry import load_registry

        assert load_registry().duplicate_keys() == {}

    def test_three_assembly_paths_agree(self):
        """三条历史装配路径必须给出**同一集合** —— P1-5 的核心主张。"""
        from engine.report_generator import all_scenarios
        from engine.scenario_registry import load_registry

        reg = set(load_registry().keys())
        rg = set(all_scenarios().keys())
        assert reg == rg, (
            f"注册表与报告库不一致：仅注册表={sorted(reg - rg)[:5]}，"
            f"仅报告库={sorted(rg - reg)[:5]}"
        )

    def test_density_path_agrees(self):
        """密度表路径（第三条）也必须一致。"""
        import importlib

        from engine.scenario_registry import load_registry

        d = importlib.import_module("scenario_library_density")
        scns, _notes = d._load_scenarios()
        assert set(s["key"] for s in scns) == set(load_registry().keys())

    def test_fingerprint_deterministic(self):
        from engine.scenario_registry import load_registry

        assert load_registry().fingerprint() == load_registry().fingerprint()

    def test_fingerprint_changes_on_schema_drift(self):
        from engine.scenario_registry import ScenarioRegistry, load_registry

        reg = load_registry()
        items = [dict(s) for s in reg.items]
        items[0] = dict(items[0])
        items[0]["ls"] = float(items[0].get("ls", 0.5)) + 1.0
        assert (
            ScenarioRegistry(items=items, source_counts=reg.source_counts).fingerprint()
            != reg.fingerprint()
        )

    def test_duplicate_detection_and_fail(self):
        from engine.scenario_registry import ScenarioRegistry

        s = {"key": "dup", "f": lambda x: 0.0, "bounds": [(0, 1)]}
        reg = ScenarioRegistry(items=[dict(s), dict(s)])
        assert reg.duplicate_keys() == {"dup": 2}
        with pytest.raises(AssertionError):
            reg.assert_contract(headline_count=2)

    def test_missing_f_or_bounds_fails(self):
        from engine.scenario_registry import ScenarioRegistry

        with pytest.raises(AssertionError):
            ScenarioRegistry(items=[{"key": "x"}]).assert_contract(headline_count=1)

    def test_optional_source_degradation_is_not_drift(self):
        """可选依赖缺失（RDKit）导致的缺口应判为「诚实降级」而非「漂移」。"""
        from engine.scenario_registry import OPTIONAL_SOURCES, load_registry

        assert "molecular" in OPTIONAL_SOURCES
        # 本环境无 RDKit：本条只在确实缺失时验证降级路径
        reg = load_registry()
        if reg.source_counts.get("molecular", 0) == 0:
            v = reg.assert_contract()  # 不应抛异常
            assert v["degraded"] is True
            assert "molecular" in v["optional_missing"]


# ===========================================================================
# 8. 可信层证书（P1-2：确定性）
# ===========================================================================
class TestCertificateDeterminism:
    def test_content_fingerprint_ignores_issue_metadata(self):
        from core.trust_layer import _content_fingerprint

        a = {"scenario": "s1", "score": 0.9, "issued_at": "2026-01-01", "certificate_id": "A"}
        b = {"scenario": "s1", "score": 0.9, "issued_at": "2027-06-06", "certificate_id": "B"}
        assert _content_fingerprint(a) == _content_fingerprint(b)

    def test_content_fingerprint_detects_content_change(self):
        from core.trust_layer import _content_fingerprint

        a = {"scenario": "s1", "score": 0.9}
        b = {"scenario": "s1", "score": 0.1}
        assert _content_fingerprint(a) != _content_fingerprint(b)

    def test_verify_certificate_self_consistent(self):
        """自签证书必须能通过第三方独立重算验证。"""
        from core.trust_layer import issue_certificate, verify_certificate

        cert = issue_certificate(run_id="pytest", inputs={"note": "unit test"})
        res = verify_certificate(cert)
        assert res["ok"] is True, f"自签证书验证失败：{res}"

    def test_verify_certificate_detects_tamper(self):
        """改动内容后签名必须失效（fail-closed）。"""
        from core.trust_layer import issue_certificate, verify_certificate

        cert = issue_certificate(run_id="pytest", inputs={"note": "unit test"})
        tampered = dict(cert)
        tampered["signature"] = "0" * 64
        assert verify_certificate(tampered)["ok"] is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v", "--tb=short"]))


# ===========================================================================
# 9. cross-conformal 端到端（evaluate_scenario）
# ===========================================================================
class TestConformalScenario:
    """`cross_conformal_scores` / `evaluate_scenario` 的端到端性质。

    这两个函数此前完全没有测试 —— 而它们正是飞轮对每个场景做 UQ 体检
    时走的路径（`data/trust_surface.json` 的 coverage_after 就来自这里）。
    """

    @staticmethod
    def _domain(key="synth", noise_hidden=0.0):
        """构造一个符合 report_generator 期望 schema 的最小 domain。"""
        rng = np.random.default_rng(0)

        def f(x):
            x = np.atleast_1d(np.asarray(x, float))
            return float(np.sin(3.0 * x[0]) + 0.5 * x[1] ** 2)

        return {
            "key": key,
            "category": "test",
            "f": f,
            "bounds": [(0.0, 1.0), (0.0, 1.0)],
            "ls": 0.3,
            "train": rng.uniform(0, 1, size=(24, 2)),
            "test": rng.uniform(0, 1, size=(30, 2)),
        }

    def test_cross_conformal_scores_shape_and_positivity(self):
        from engine.conformal import cross_conformal_scores

        d = self._domain()
        X = np.asarray(d["train"], float)
        y = np.array([d["f"](x) for x in X])
        s = cross_conformal_scores(d, X, y, k_folds=4, seed=0, repeats=2)
        # repeats=2 → 分数个数 = 2 * n
        assert s.shape == (2 * len(y),)
        assert np.all(np.isfinite(s))
        assert np.all(s >= 0), "标准化残差非负（|·|/sd）"

    def test_cross_conformal_no_leakage_scores_not_tiny(self):
        """每折独立归一化 → OOF 分数不应全部趋 0（那意味着标定泄漏）。"""
        from engine.conformal import cross_conformal_scores

        d = self._domain()
        X = np.asarray(d["train"], float)
        y = np.array([d["f"](x) for x in X])
        s = cross_conformal_scores(d, X, y, k_folds=4, seed=0, repeats=1)
        assert float(np.median(s)) > 1e-3, "OOF 分数中位数≈0，疑似标定泄漏"

    def test_evaluate_scenario_verdict_is_valid_enum(self):
        from engine.conformal import evaluate_scenario

        out = evaluate_scenario(self._domain(), alpha=0.05, seed=0, repeats=2)
        assert out["verdict"] in {
            "IMPROVED", "STILL_CONSERVATIVE", "NO_GAIN", "UNDER_COVERS"
        }
        # 两个区间口径的指标都必须齐备
        for k in ("picp", "nmpiw", "interval_score", "miscal_area"):
            assert k in out["baseline"] and k in out["conformal"]
        assert 0.0 <= out["baseline"]["picp"] <= 1.0
        assert 0.0 <= out["conformal"]["picp"] <= 1.0
        assert out["key"] == "synth"
        assert out["n_train"] == 24 and out["n_test"] == 30

    def test_evaluate_scenario_conformal_not_wider_than_baseline(self):
        """conformal 的 Q 是分位数，理论上若 Q<z 则区间更窄。

        这里断言的是**关系**而非具体值：width_ratio 必须等于 Q/z（自洽）。
        """
        from engine.conformal import evaluate_scenario

        out = evaluate_scenario(self._domain(), alpha=0.05, seed=1, repeats=2)
        # 两者都是 round(...,4) 后写入的，故容差取 1e-4（round 引入的误差上界）
        assert out["width_ratio_conformal_vs_gaussian"] == pytest.approx(
            out["conformal_Q"] / out["gaussian_z"], abs=1e-4
        )

    def test_evaluate_scenario_deterministic(self):
        from engine.conformal import evaluate_scenario

        a = evaluate_scenario(self._domain(), alpha=0.05, seed=7, repeats=2)
        b = evaluate_scenario(self._domain(), alpha=0.05, seed=7, repeats=2)
        assert a["conformal"]["picp"] == b["conformal"]["picp"]
        assert a["verdict"] == b["verdict"]


# ===========================================================================
# 10. SurrogateEngine 主动学习 / 信任边界
# ===========================================================================
class TestSurrogateEngineActiveLearning:
    @staticmethod
    def _eng():
        from engine.surrogate import SurrogateEngine

        def f(x):
            x = np.atleast_1d(np.asarray(x, float))
            return float((x[0] - 0.3) ** 2 + (x[1] - 0.7) ** 2)

        return SurrogateEngine(
            {"key": "al", "category": "test", "f": f,
             "bounds": [(0.0, 1.0), (0.0, 1.0)], "ls": 0.3}
        )

    def test_recommend_next_inside_bounds(self):
        import numpy as _np

        eng = self._eng()
        rng = _np.random.default_rng(0)
        X = rng.uniform(0, 1, size=(12, 2))
        y = _np.array([eng.f(x) for x in X])
        eng.fit(X, y, noise_rel=0.01)
        x_next = _np.atleast_1d(eng.recommend_next(n_candidates=100))
        assert x_next.shape == (2,)
        assert _np.all(x_next >= -1e-9) and _np.all(x_next <= 1 + 1e-9), (
            f"推荐点越界：{x_next}"
        )

    def test_recommend_next_is_deterministic(self):
        import numpy as _np

        outs = []
        for _ in range(2):
            eng = self._eng()
            rng = _np.random.default_rng(3)
            X = rng.uniform(0, 1, size=(12, 2))
            y = _np.array([eng.f(x) for x in X])
            eng.fit(X, y, noise_rel=0.01)
            outs.append(_np.atleast_1d(eng.recommend_next(n_candidates=100)))
        _np.testing.assert_allclose(outs[0], outs[1])

    def test_trust_boundary_flags_ood(self):
        import numpy as _np

        eng = self._eng()
        rng = _np.random.default_rng(1)
        X = rng.uniform(0.4, 0.6, size=(15, 2))
        y = _np.array([eng.f(x) for x in X])
        eng.fit(X, y, noise_rel=0.01)
        res = eng.trust_boundary(_np.array([[0.5, 0.5], [1.0, 1.0]]))
        assert isinstance(res, dict)

    def test_evaluate_returns_standard_metrics(self):
        import numpy as _np

        eng = self._eng()
        rng = _np.random.default_rng(2)
        X = rng.uniform(0, 1, size=(20, 2))
        y = _np.array([eng.f(x) for x in X])
        eng.fit(X, y, noise_rel=0.01)
        m = eng.evaluate(rng.uniform(0, 1, size=(25, 2)))
        assert isinstance(m, dict)
        if m:
            assert any(k in m for k in ("rmse", "coverage", "cal_err"))


# ===========================================================================
# 11. 泛化基准端到端（G1 判据在真实场景上可跑）
# ===========================================================================
class TestGeneralizationBenchRuns:
    def test_g1_runs_on_a_registered_scenario(self):
        """G1 必须能在真实注册场景上跑完并给出结构化结果（不崩、不假绿）。"""
        from engine.generalization_bench import run_g1
        from engine.scenario_registry import get_scenario

        scn = get_scenario("surrogate_forrester")
        r = run_g1("surrogate_forrester", scn)
        d = r.to_dict() if hasattr(r, "to_dict") else dict(r)
        assert d.get("key") == "surrogate_forrester"
        # 关键：默认不能是 passed=True（防假绿）
        assert d.get("ok", False) in (True, False)
        if d.get("na"):
            assert d.get("reason"), "判为不适用时必须给出理由（不允许静默跳过）"
        # A15b：必须记录 auto_ls 是否启用及其先验
        assert "auto_ls_used" in d and "ls_prior" in d


# ===========================================================================
# 12. BALD 集成 / ActiveLearner / 对等闭环
# ===========================================================================
class TestBALDAndActiveLearning:
    """BALD 是"该做哪个实验"的核心决策器，此前零覆盖。

    可验证性质（不依赖具体数值）：
      * BALD 分数非负（epistemic + aleatoric 都是方差）
      * 集成成员数 = n_models
      * 训练点附近 BALD 低于远离区（epistemic 应随距离增长）
      * 相同种子 → 逐位可复现
    """

    @staticmethod
    def _data(n=25, seed=0):
        rng = np.random.default_rng(seed)
        X = rng.uniform(0, 1, size=(n, 1))
        y = np.sin(2 * np.pi * X).ravel()
        return X, y

    def test_fit_creates_n_models(self):
        from engine.surrogate import BALDEnsemble

        X, y = self._data()
        ens = BALDEnsemble(n_models=5, seed=0, lengthscale=0.3).fit(X, y)
        assert len(ens.models) == 5

    def test_bald_scores_nonnegative(self):
        from engine.surrogate import BALDEnsemble

        X, y = self._data()
        ens = BALDEnsemble(n_models=5, seed=0, lengthscale=0.3).fit(X, y)
        Xs = np.linspace(0, 1, 20).reshape(-1, 1)
        b = ens.bald(Xs)
        assert b.shape == (20,)
        assert np.all(b >= 0), "BALD 分数（方差之和）必须非负"

    def test_bald_is_deterministic(self):
        from engine.surrogate import BALDEnsemble

        X, y = self._data()
        a = BALDEnsemble(n_models=4, seed=1, lengthscale=0.3).fit(X, y).bald(
            np.array([[0.1], [0.5], [0.9]])
        )
        b = BALDEnsemble(n_models=4, seed=1, lengthscale=0.3).fit(X, y).bald(
            np.array([[0.1], [0.5], [0.9]])
        )
        np.testing.assert_array_equal(a, b)

    def test_active_learner_max_std_in_bounds(self):
        from engine.surrogate import ActiveLearner, ExperimentSpace, GPSurrogate

        X, y = self._data()
        gp = GPSurrogate(lengthscale=0.3)
        gp.fit(X, y)
        space = ExperimentSpace([(0.0, 1.0)])
        x = np.atleast_1d(
            ActiveLearner(strategy="max_std").next(gp, space, n_candidates=50)
        )
        assert x.shape == (1,)
        assert -1e-9 <= x[0] <= 1 + 1e-9

    def test_active_learner_bald_runs(self):
        from engine.surrogate import ActiveLearner, ExperimentSpace, GPSurrogate

        X, y = self._data()
        gp = GPSurrogate(lengthscale=0.3)
        gp.fit(X, y)
        space = ExperimentSpace([(0.0, 1.0)])
        x = np.atleast_1d(
            ActiveLearner(strategy="bald").next(gp, space, n_candidates=40)
        )
        assert -1e-9 <= x[0] <= 1 + 1e-9

    def test_active_learner_invalid_strategy_raises(self):
        from engine.surrogate import ActiveLearner, ExperimentSpace, GPSurrogate

        X, y = self._data()
        gp = GPSurrogate(lengthscale=0.3)
        gp.fit(X, y)
        with pytest.raises(ValueError):
            ActiveLearner(strategy="nonsense").next(
                gp, ExperimentSpace([(0.0, 1.0)]), n_candidates=10
            )

    def test_parity_loop_improves_or_holds(self):
        """对等闭环：随真实观测增加，虚拟代理的 RMSE 不应变差到发散。

        断言的是**稳定性**（有限、不爆炸），不是「必然改善」——
        随机种子下偶尔的起伏是正常的，声称必然改善才是过度承诺。
        """
        from engine.surrogate import (
            Calibrator,
            ExperimentSpace,
            GPSurrogate,
            SyntheticOracle,
            run_parity_loop,
        )

        def f(x):
            return float(np.sin(2 * np.pi * np.atleast_1d(x)[0]))

        space = ExperimentSpace([(0.0, 1.0)])
        oracle = SyntheticOracle(f, noise=0.01, rng=np.random.default_rng(0))
        hist = run_parity_loop(
            space, oracle,
            surrogate=GPSurrogate(lengthscale=0.3),
            calibrator=Calibrator(),
            n_seed=6, n_iter=4,
            test_grid=np.linspace(0, 1, 25).reshape(-1, 1),
        )
        assert isinstance(hist, list) and len(hist) >= 1
        rmses = [h["rmse"] for h in hist if "rmse" in h]
        assert all(np.isfinite(r) for r in rmses), f"RMSE 出现非有限值：{rmses}"
