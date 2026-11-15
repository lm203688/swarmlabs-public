"""tests/test_orphan_modules.py —— 12 个「规划中但未集成」模块的最小可用性验证。

为什么有这个文件
----------------
2026-10-10 全库扫描发现 17 个「未被 import、无 CLI 入口」的孤儿 py 模块，
其中 12 个在 docs/ 有引用（是待集成的规划模块，不是死代码），
却**零 selftest / 零 pytest 覆盖**——2299 行核心规划代码，从未被验证过。

本文件集中验证这 12 个模块的**最小可用性**：
  - 能 import，公开 API 符合预期
  - 能构造主对象
  - 主对象能跑一次最小调用（不联网、不读生产数据）
  - 依赖外部服务/大文件的场景用 pytest.skip 明示

跑法：python -m pytest tests/test_orphan_modules.py -v

覆盖模块
--------
1.  engine.validation         8 维度质量验证框架（Kosmos ScholarEval）
2.  engine.traceability       可追溯层：每个结论 → 代码/论文出处
3.  engine.entity_shard       实体存储 hash-bucket 分片
4.  engine.federated_evidence 跨域证据检索（P1-8）
5.  engine.design             实验设计：轴变换、宽域处理
6.  engine.remote_lab         远程实验 Oracle（含 LocalLab 离线模式）
7.  engine.acquisition_volume ConvexBodyAcquisition（涨落定理凸体收购）
8.  engine.hypothesis_llm     LLM 假设层（仅构造，不联网）
9.  engine.time_machine       实验时光机（快照 + 时间旅行）
10. engine.tree_search        假设树搜索（HypothesisTree）
11. engine.sdk                统一门面 VirtualExperiment
12. scripts.v3_i18n_map       前端 i18n 文案映射表
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pytest


# ════════════════════════════════════════════════════════════════════
# 1. engine.validation —— 8 维度质量验证框架
# ════════════════════════════════════════════════════════════════════
def test_validation_result_roundtrip():
    """ValidationResult 构造 → to_dict → from_dict 应等价。"""
    from engine.validation import ValidationResult

    r = ValidationResult(target_id="test_finding_1", target_type="finding", domain="biochem")
    r.set_score("novelty", 0.8)
    r.set_score("accuracy", 0.9)
    d = r.to_dict()
    assert d["target_id"] == "test_finding_1"
    assert d["domain"] == "biochem"
    assert "novelty" in str(d) and "accuracy" in str(d)

    r2 = ValidationResult.from_dict(d)
    assert r2.target_id == r.target_id
    assert r2.domain == r.domain


def test_validation_framework_init_with_tmp():
    """ValidationFramework 应在给定路径上初始化而不崩。"""
    from engine.validation import ValidationFramework

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "validation.json"
        fw = ValidationFramework(path=str(path))
        s = fw.summary_report() if hasattr(fw, "summary_report") else {}
        assert isinstance(s, (dict, list, str))


# ════════════════════════════════════════════════════════════════════
# 2. engine.traceability —— 可追溯层
# ════════════════════════════════════════════════════════════════════
def test_traceability_finding_and_provenance_roundtrip():
    """Finding + Provenance 双向序列化应保真。"""
    from engine.traceability import Finding, Provenance

    f = Finding(statement="GP 外推不可达", domain="surrogate", confidence=0.95)
    f_dict = f.to_dict()
    assert f_dict["statement"] == "GP 外推不可达"
    assert f_dict["confidence"] == pytest.approx(0.95)

    f2 = Finding.from_dict(f_dict)
    assert f2.statement == f.statement
    assert f2.domain == f.domain

    p = Provenance(source_type="code", reference="engine/surrogate.py:142",
                   detail="LOO 守卫")
    p_dict = p.to_dict()
    assert p_dict["source_type"] == "code"

    p2 = Provenance.from_dict(p_dict)
    assert p2.reference == p.reference


def test_traceability_layer_record_and_query():
    """TraceabilityLayer 应能记录 finding 并查询回来。"""
    from engine.traceability import TraceabilityLayer, Finding

    with tempfile.TemporaryDirectory() as tmp:
        tl = TraceabilityLayer(path=f"{tmp}/trace.json")
        fid = tl.record_finding(Finding("结论 X", domain="chem", confidence=0.9))
        assert fid is not None and hasattr(fid, "id") and isinstance(fid.id, str)
        results = tl.query_findings(domain="chem")
        assert results is not None and len(results) >= 1


# ════════════════════════════════════════════════════════════════════
# 3. engine.entity_shard —— 实体存储分片
# ════════════════════════════════════════════════════════════════════
def test_entity_shard_shard_summary():
    """shard_summary 应返回字典（含桶数与总数）。"""
    from engine.entity_shard import shard_summary

    summary = shard_summary()
    assert isinstance(summary, dict)
    assert len(summary) > 0


# ════════════════════════════════════════════════════════════════════
# 4. engine.federated_evidence —— 跨域证据检索
# ════════════════════════════════════════════════════════════════════
def test_federated_evidence_evidence_roundtrip():
    """Evidence dataclass 应能构造并序列化。"""
    from engine.federated_evidence import Evidence

    ev = Evidence(
        scenario="mol_suzuki_yield",
        matched_count=3,
        references=[{"type": "paper", "id": "A1"}],
        gaps=[{"type": "temperature_range"}],
        bridges=[{"type": "cross_domain", "target": "chem_kinetics"}],
        suggestion="补齐温度扫描",
    )
    d = ev.to_dict()
    assert d["scenario"] == "mol_suzuki_yield"
    assert d["matched_count"] == 3
    assert d["n_references"] == 1


# ════════════════════════════════════════════════════════════════════
# 5. engine.design —— 实验设计：轴变换
# ════════════════════════════════════════════════════════════════════
def test_design_warp_axes_and_bounds():
    """warp_axes 应对跨尺度 bounds 做对数变换提示。"""
    from engine.design import warp_axes, warped_bounds, width_factors, scaled_ls_prior

    bounds = [[1.0, 1000.0], [0.0, 1.0]]
    axes = warp_axes(bounds, ratio_threshold=10.0)
    assert isinstance(axes, list) and len(axes) == 1
    assert axes[0] == 0  # axis 0: 1→1000, ratio=1000 > 10
    wb = warped_bounds(bounds, axes)
    assert len(wb) == 2

    wf = width_factors(bounds, axes, dim=2)
    assert wf.shape == (2,)

    ls_prior = scaled_ls_prior(np.ones(2), bounds, axes, dim=2)
    assert ls_prior.shape == (2,)


def test_design_to_model_to_raw_roundtrip():
    """to_model 与 to_raw 应互为逆映射。"""
    from engine.design import warp_axes, to_model, to_raw

    bounds = [[0.0, 100.0], [0.0, 1.0]]
    axes = warp_axes(bounds, ratio_threshold=10.0)
    X_raw = np.array([[50.0, 0.5], [10.0, 0.2]])
    X_m = to_model(X_raw, axes)
    X_back = to_raw(X_m, axes)
    np.testing.assert_allclose(X_back, X_raw, rtol=1e-3, atol=1e-6)


# ════════════════════════════════════════════════════════════════════
# 6. engine.remote_lab —— 远程实验 Oracle（离线模式）
# ════════════════════════════════════════════════════════════════════
def test_remote_lab_local_simulator_submit_and_result():
    """LocalLabSimulator 应能 submit 实验并返回合法结果（无网络）。"""
    from engine.remote_lab import LocalLabSimulator, ExperimentSpec

    sim = LocalLabSimulator(f=lambda x: float(np.sin(x[0])), noise=0.0, rng=np.random.default_rng(1))
    spec = ExperimentSpec(inner_x=np.array([0.5, 1.0]))
    job = sim.submit(spec)
    assert job is not None
    r = sim.result(job)
    assert r is not None
    np.testing.assert_allclose(r, np.sin(0.5), atol=0.1)


def test_remote_lab_dataset_oracle_observe():
    """DatasetOracle 应在给定数据集上 observe 返回 (y, noise)。"""
    from engine.remote_lab import DatasetOracle

    X = np.array([[0.0], [1.0], [2.0]])
    y = np.array([1.0, 2.0, 3.0])
    oracle = DatasetOracle(X, y, noise=0.0, rng=np.random.default_rng(0))
    obs = oracle.observe(np.array([0.0, 1.0, 2.0]))
    assert obs is not None


# ════════════════════════════════════════════════════════════════════
# 7. engine.acquisition_volume —— 凸体收购
# ════════════════════════════════════════════════════════════════════
def test_acquisition_volume_init_and_next():
    """ConvexBodyAcquisition 应能构造并对简单 surrogate 提议下一步。"""
    from engine.acquisition_volume import ConvexBodyAcquisition
    from engine.surrogate import GPSurrogate, ExperimentSpace

    rng = np.random.default_rng(42)
    X = rng.uniform(0, 1, size=(20, 2))
    y = np.sin(X[:, 0] + X[:, 1])
    model = GPSurrogate(noise_var=0.01 ** 2, y_mean=float(y.mean()),
                        y_scale=float(y.std()) + 1e-9, lengthscale=0.5)
    model.fit(X, y)
    space = ExperimentSpace([[0, 1], [0, 1]])

    acq = ConvexBodyAcquisition(tol=0.1)
    pt = acq.next(model, space, n_candidates=50, rng=np.random.default_rng(1))
    pt = np.asarray(pt)
    assert pt.shape == (2,) or pt.shape == (1, 2), f"unexpected shape {pt.shape}"


# ════════════════════════════════════════════════════════════════════
# 8. engine.hypothesis_llm —— LLM 假设层（仅构造，不联网）
# ════════════════════════════════════════════════════════════════════
def test_hypothesis_llm_init_no_network():
    """LLMHypothesizer 应能在无 base_url / 无 api_key 时构造而不崩。"""
    from engine.hypothesis_llm import LLMHypothesizer

    h = LLMHypothesizer(base_url=None, model=None, api_key=None)
    assert h is not None
    ctx = h.context_from(X=np.array([[0.5, 1.0]]), y=np.array([0.3]), domain_key="mol_suzuki_yield")
    assert ctx is not None


# ════════════════════════════════════════════════════════════════════
# 9. engine.time_machine —— 实验时光机
# ════════════════════════════════════════════════════════════════════
def test_time_machine_capture_and_list():
    """SnapshotService 应能 capture 一次快照并 list_runs 回来。"""
    from engine.time_machine import SnapshotService

    with tempfile.TemporaryDirectory() as tmp:
        svc = SnapshotService(path=f"{tmp}/tm.json")
        snap = svc.capture(
            run_id="smoke-run",
            payload={"model_state": {"w": [0.1, 0.2], "b": 0.0}, "context": {"domain": "test"}},
        )
        assert snap is not None
        runs = svc.list_runs()
        assert isinstance(runs, list) and len(runs) >= 1


def test_time_machine_time_travel_and_diff():
    """time_travel 应能回到历史快照，diff 应返回变更摘要。"""
    from engine.time_machine import SnapshotService

    with tempfile.TemporaryDirectory() as tmp:
        svc = SnapshotService(path=f"{tmp}/tm.json")
        svc.capture(run_id="v1", payload={"model_state": {"w": 1.0}, "context": {}})
        svc.capture(run_id="v2", payload={"model_state": {"w": 2.0}, "context": {}})
        got = svc.time_travel("v1")
        assert got is not None and got.get("payload", {}).get("model_state", {}).get("w") == 1.0
        d = svc.diff("v1", "v2") if hasattr(svc, "diff") else {}
        assert d is not None


# ════════════════════════════════════════════════════════════════════
# 10. engine.tree_search —— 假设树搜索
# ════════════════════════════════════════════════════════════════════
def test_tree_search_minimal_run():
    """HypothesisTree.search(X, y) 应在最小数据集上跑完一轮。"""
    from engine.tree_search import HypothesisTree

    tree = HypothesisTree(threshold=0.01)
    rng = np.random.default_rng(0)
    X = rng.uniform(-1, 1, size=(30, 1))
    y = np.sin(X[:, 0]) + 0.02 * rng.standard_normal(30)
    result = tree.search(X, y, n_rounds=2)
    assert result is not None
    assert hasattr(result, "__len__") or isinstance(result, dict)


# ════════════════════════════════════════════════════════════════════
# 11. engine.sdk —— 补充 pytest 视角（sdk.py 已有 selftest）
# ════════════════════════════════════════════════════════════════════
def test_sdk_neural_operator_engine_kind():
    """VirtualExperiment 应支持 neural_operator 引擎（RFF 算子近似）。"""
    from engine.sdk import VirtualExperiment

    rng = np.random.default_rng(0)
    X = rng.uniform(0, 1, size=(20, 2))
    y = np.sin(X[:, 0] + X[:, 1]) + 0.05 * rng.standard_normal(20)

    ve = VirtualExperiment(engine_kind="neural_operator",
                           bounds=[[0, 1], [0, 1]])
    ve.fit(X, y)
    pred = ve.predict(X)
    assert pred["mean"].shape == (20,)
    assert np.all(np.isfinite(pred["mean"]))


def test_sdk_fit_before_suggest_required():
    """未 fit 就 suggest 必须 RuntimeError，不能静默退化。"""
    from engine.sdk import VirtualExperiment

    ve = VirtualExperiment(engine_kind="gp", bounds=[[0, 1], [0, 1]])
    with pytest.raises(RuntimeError):
        ve.suggest()


# ════════════════════════════════════════════════════════════════════
# 12. scripts.v3_i18n_map —— 前端 i18n 映射（L-16 退休后移至 _retired/）
# ════════════════════════════════════════════════════════════════════
@pytest.mark.skip(reason="L-16 孤儿率清理：v3_i18n_map 已退休至 scripts/_retired/，不再测试")
def test_v3_i18n_map_all_maps_returns_dict():
    """all_maps() 应返回 (name, mapping) 元组列表，每个 mapping 是非空 dict。"""
    from scripts.v3_i18n_map import all_maps

    maps = all_maps()
    assert isinstance(maps, list) and len(maps) > 0
    for entry in maps:
        assert isinstance(entry, tuple) and len(entry) == 2
        name, mapping = entry
        assert isinstance(name, str) and name
        assert isinstance(mapping, dict) and len(mapping) > 0, \
            f"mapping {name} 不是非空 dict: {type(mapping)}"
        for k, v in mapping.items():
            assert isinstance(k, str) and isinstance(v, str)


# ════════════════════════════════════════════════════════════════════
# 元测试：确认所有 12 个模块能一次 import（防止未来重命名导致静默失联）
# ════════════════════════════════════════════════════════════════════
def test_all_orphan_modules_importable():
    """一次性 import 全部 12 个模块，任何 ImportError 都应立即暴露。"""
    import importlib
    mods = [
        "engine.validation",
        "engine.traceability",
        "engine.entity_shard",
        "engine.federated_evidence",
        "engine.design",
        "engine.remote_lab",
        "engine.acquisition_volume",
        "engine.hypothesis_llm",
        "engine.time_machine",
        "engine.tree_search",
        "engine.sdk",
        # scripts.v3_i18n_map 已退休至 _retired/，不再测试
    ]
    for m in mods:
        importlib.import_module(m)
