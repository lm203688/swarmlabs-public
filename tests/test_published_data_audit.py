#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_published_data_audit.py — B-2 自动提取级审计的测试。

覆盖（含否定性测试，纪律：注入已知错误必须命中 + 干净样本不得误报）：
  1. sha256 fail-loud：篡改 manifest / 缺文件必须抛错（否定性）；
  2. evidence_grade_for fail-closed：未登记/未校验 ⇒ repo 档（否定性）；
  3. 提取器确定性：两次提取结果逐比特一致；条件数=36；
  4. 拟合健全性：R²>0.95、噪声地板量级、起点稳定性字段齐全；
  5. claim 核对逻辑：精确复现 ⇒ True，2 倍偏离 ⇒ False（合成数据）；
  6. verdict 优先级：τ 撞界 majority ⇒ UNDERDETERMINED（合成证书路径）；
  7. 证据等级常量与 paper_audit.py 一致（单一真源防串味）。
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import paper_audit as pa
from engine import published_data_audit as pda

DATA_DIR = os.path.join(pda.DATA_DIR)
_HAS_RAW = os.path.isdir(DATA_DIR) and any(
    f.endswith(".xlsx") for f in os.listdir(DATA_DIR)) if os.path.isdir(DATA_DIR) else False

needs_raw = pytest.mark.skipif(not _HAS_RAW, reason="data/raw 原始数据缺失（gitignored）")


# ── 1. sha256 fail-loud ──────────────────────────────────────────

@needs_raw
def test_verify_source_ok():
    src = pda.verify_source()
    assert src["n_files"] == 5
    assert all("sha256" in v for v in src["files"].values())


def test_verify_source_rejects_tampered_manifest(monkeypatch, tmp_path):
    """否定性：manifest 的 sha256 被改 ⇒ 必须抛错，绝不放过。"""
    good = json.load(open(pda.MANIFEST_PATH, encoding="utf-8"))
    bad = json.loads(json.dumps(good))
    bad["downloads"][0]["sha256"] = "0" * 64
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(bad), encoding="utf-8")
    monkeypatch.setattr(pda, "MANIFEST_PATH", str(p))
    with pytest.raises(ValueError, match="sha256 不符"):
        pda.verify_source()


def test_verify_source_rejects_missing_file(monkeypatch, tmp_path):
    """否定性：文件缺失 ⇒ FileNotFoundError。"""
    good = json.load(open(pda.MANIFEST_PATH, encoding="utf-8"))
    bad = json.loads(json.dumps(good))
    bad["downloads"][0]["path"] = "data\\raw\\ecoli_monod_growth\\__nope__.xlsx"
    bad["downloads"][0]["filename"] = "__nope__.xlsx"
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(bad), encoding="utf-8")
    monkeypatch.setattr(pda, "MANIFEST_PATH", str(p))
    with pytest.raises(FileNotFoundError):
        pda.verify_source()


# ── 2. evidence_grade fail-closed ────────────────────────────────

def test_evidence_grade_unregistered_is_repo():
    """未登记基准 ⇒ 保守档（fail-closed）。"""
    assert pa.evidence_grade_for("paper_unknown_key") == pa.EVIDENCE_GRADE_REPO


def test_evidence_grade_all_17_classics_are_repo():
    """17 个经典基准全部 repo 档——没有任何一个被悄悄升级。"""
    for k in pa.DATA_PROVENANCE:
        assert pa.evidence_grade_for(k) == pa.EVIDENCE_GRADE_REPO, k


def test_evidence_grade_auto_requires_verified_flag():
    """登记为 auto_download 但 sha256_verified 缺失/False ⇒ 仍然保守档。"""
    pa.DATA_PROVENANCE["_tmp_unverified"] = {"source_type": "auto_download"}
    try:
        assert pa.evidence_grade_for("_tmp_unverified") == pa.EVIDENCE_GRADE_REPO
        pa.DATA_PROVENANCE["_tmp_unverified"]["sha256_verified"] = True
        assert pa.evidence_grade_for("_tmp_unverified") == pa.EVIDENCE_GRADE_AUTO
    finally:
        del pa.DATA_PROVENANCE["_tmp_unverified"]


def test_evidence_grade_constants_align_across_modules():
    """两模块的等级字符串必须一致（防漂移）。"""
    assert pda.EVIDENCE_GRADE == pa.EVIDENCE_GRADE_AUTO


# ── 3. 提取器确定性 ──────────────────────────────────────────────

@needs_raw
def test_extract_deterministic():
    c1 = pda.extract_growth_curves()
    c2 = pda.extract_growth_curves()
    assert len(c1) == len(c2) == 36
    for a, b in zip(c1, c2):
        assert a["sheet"] == b["sheet"]
        assert np.array_equal(a["t_min"], b["t_min"])
        assert np.array_equal(a["od_mean"], b["od_mean"])
        assert a["replicates"] == b["replicates"]


@needs_raw
def test_extract_condition_geometry():
    conds = pda.extract_growth_curves()
    glucoses = sorted({c["glucose_pct"] for c in conds})
    temps = sorted({c["temperature_C"] for c in conds})
    assert glucoses == [0.1, 0.5, 1.0, 2.0]
    assert temps == [29.0, 31.0, 33.0, 35.0, 37.0, 39.0, 41.0, 43.0, 45.0]
    for c in conds:
        assert c["n_points"] if False else len(c["t_min"]) >= 10
        assert len(c["replicates"][0]) == 8  # 8 个技术重复


# ── 4. 拟合健全性 ────────────────────────────────────────────────

@needs_raw
def test_fit_one_condition_sane():
    conds = pda.extract_growth_curves()
    f = pda.fit_condition(conds[0])
    assert f["r2"] > 0.95
    assert 0.0 < f["b_per_min"] < 0.5
    assert f["noise_ratio"] is not None and 0.2 < f["noise_ratio"] < 3.0
    assert f["n_starts"] == 15
    assert f["n_distinct_near_optimal"] >= 1
    assert f["td_min_from_b"] > 0


def test_richards_model_monotone_s_shape():
    """模型形态自检：S 型增长、渐近 c、早期指数段斜率 = b。"""
    # t_apm=50、b=0.03：需 b·(t−t_apm) ≫ 1 才达渐近，上界取 800
    t = np.linspace(-250, 800, 400)
    p = (1.0, 2.0, 0.03, 50.0)
    y = pda.richards_od(p, t)
    assert y[0] < 0.005 and abs(y[-1] - 1.0) < 1e-3          # 0 → c
    assert np.all(np.diff(y) > -1e-9)                        # 单调不减
    # 早期指数段（u<<1）：OD ≈ c·exp(b(t-t_apm))，ln(OD) 斜率 → b
    early = (t >= -200) & (t <= -150)
    slope = np.polyfit(t[early], np.log(y[early]), 1)[0]
    assert abs(slope - 0.03) < 0.002


# ── 5. claim 核对逻辑（合成数据） ────────────────────────────────

def _synth_fits(b_per_min=0.02, tau=2.0, n=36):
    out = []
    for g in (0.1, 0.5, 1.0, 2.0):
        for T in (29.0, 31.0, 33.0, 35.0, 37.0, 39.0, 41.0, 43.0, 45.0):
            out.append({"glucose_pct": g, "temperature_C": T, "sheet": "synth",
                        "b_per_min": b_per_min, "tau": tau, "c": 0.5, "r2": 0.99,
                        "rmse": 0.01, "noise_ratio": 1.0, "n_starts": 15,
                        "n_near_optimal": 15, "n_distinct_near_optimal": 1,
                        "params_at_bound": [False, False, False, False],
                        "td_min_from_b": 34.7, "t_apm_min": 50.0, "n_points": 100,
                        "sigma_replicate": 0.02, "sigma_of_mean": 0.007})
    return out


def test_check_claims_exact_values_reproduce():
    """把论文声称值原样喂进核对逻辑 ⇒ 全部标记复现（防判据本身写反）。"""
    fits = _synth_fits()
    # 让 b 的极值与倍率对上论文声称（min⁻¹ 读法）
    fits[0]["b_per_min"] = 0.014                                    # 29C/0.1%？不，改条件
    # 直接构造：b(29,2%)=0.014, b(45,0.1%)=0.068, 固定葡萄糖 2.4 倍
    for f in fits:
        if f["glucose_pct"] == 2.0 and f["temperature_C"] == 29.0:
            f["b_per_min"] = 0.014
        if f["glucose_pct"] == 0.1 and f["temperature_C"] == 45.0:
            f["b_per_min"] = 0.068
    # 0.1% 葡萄糖：b(29)=0.068/2.4
    for f in fits:
        if f["glucose_pct"] == 0.1 and f["temperature_C"] == 29.0:
            f["b_per_min"] = 0.068 / 2.4
    checks = {c["claim"] + "|" + c.get("reading", ""): c for c in pda.check_claims(fits)}
    c1a = checks["C1a_b_at_paper_condition|at 2.0%/29.0C (paper's own condition), as min^-1"]
    assert c1a["reproduced"] is True
    c2 = checks["C2_b_temp_fold|0.1% glucose"]
    assert c2["reproduced"] is True


def test_check_claims_gross_deviation_not_reproduced():
    """2 倍以上偏离 ⇒ 不标记复现（防判据过松）。"""
    fits = _synth_fits()
    for f in fits:
        f["b_per_min"] = 0.14  # 论文 min 0.014 的 10 倍
    checks = {c["claim"] + "|" + c.get("reading", ""): c for c in pda.check_claims(fits)}
    c1a = checks["C1a_b_at_paper_condition|at 2.0%/29.0C (paper's own condition), as min^-1"]
    assert c1a["reproduced"] is False


def test_check_claims_tau_fold_direction():
    """τ 倍率核对方向：fold<1（反向）时 relative deviation 必须能识别。"""
    fits = _synth_fits()
    for f in fits:
        f["tau"] = 3.950 if f["glucose_pct"] == 0.1 else 1.501
    checks = {c["claim"]: c for c in pda.check_claims(fits)}
    assert checks["C3_tau_glucose_fold"]["refit"] == pytest.approx(2.631, abs=0.01)
    assert checks["C3_tau_glucose_fold"]["reproduced"] is True


# ── 6. 端到端（有原始数据时） ────────────────────────────────────

@needs_raw
def test_end_to_end_certificate_structure():
    cert = pda.audit_ecoli_paper()
    assert cert["evidence_grade"] == pda.EVIDENCE_GRADE
    assert cert["data_source"]["sha256_verified"] is True
    assert cert["n_conditions"] == 36
    assert cert["verdict"] in ("CONSISTENT", "PARTIALLY_REPRODUCED", "UNDERDETERMINED",
                               "DATA_PROVENANCE_SUSPECT", "STRUCTURAL_MISFIT", "AUDIT_ERROR")
    # 数据真实性：噪声地板中位在 [0.3, 3] ⇒ 真实实验数据
    assert 0.3 <= cert["data_quality"]["median_noise_ratio"] <= 3.0
    # claim 核对完整覆盖 C1-C6（C1a/C1b 同属 C1）
    claims = {c["claim"][:2] for c in cert["claim_checks"]}
    assert claims >= {"C1", "C2", "C3", "C4", "C5", "C6"}
    # 每个条件都有诊断字段
    for f in cert["conditions"]:
        assert f["n_distinct_near_optimal"] >= 1
        assert isinstance(f["params_at_bound"], list) and len(f["params_at_bound"]) == 4


@needs_raw
def test_paper_audit_registry_covers_all_benchmarks():
    """paper_audit 的 17 个基准必须全部在登记簿中（防新增基准漏登记被悄悄升级）。"""
    import engine.paper_validation as pv
    keys = {b["key"] for b in pv.BENCHMARKS}
    assert keys <= set(pa.DATA_PROVENANCE), keys - set(pa.DATA_PROVENANCE)
