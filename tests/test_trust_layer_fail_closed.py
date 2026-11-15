"""P0 修复回归测试：trust_layer 数据缺失时必须 fail-closed，不得「空签发」。

背景（2026-11-03 专项体检 · 技术专家 + SwarmLabs 独立核实）：
- 旧实现 _load_json 用 try/except 吞掉全部异常返回 None；issue_certificate() 在
  trust_surface.json / findings_trace.json 缺失时静默降级为空（load_trust_surface() or {}）。
- 后果：可信证书可以在关键数据缺失时照常签发，只是指标全 None / rubric 全空。
  签名只证明「内容未被篡改」，证明不了「数据在位」。
- 修复：默认 strict=True → 数据源缺失时抛 TrustDataMissingError（fail-closed）；
  strict=False 显式兼容通道，证书置 data_complete=False + degradation_reason。
"""
import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import core.trust_layer as tl

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def workdir():
    """在【项目根】直接写临时文件（%TEMP% 与新建子目录均被 DSH 沙箱拒写）。"""
    d = ROOT
    yield d
    for fn in ("trust_surface.json", "findings_trace.json",
               "no_trust_surface.json", "no_findings.json"):
        p = os.path.join(d, ".tmp_trust_" + fn)
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass


def _p(workdir, fn):
    return os.path.join(workdir, ".tmp_trust_" + fn)


def test_issue_certificate_strict_raises_on_missing(monkeypatch, workdir):
    """strict=True（默认）：trust_surface 缺失 → TrustDataMissingError（fail-closed）"""
    monkeypatch.setattr(tl, "TRUST_PATH", _p(workdir, "no_trust_surface.json"))
    monkeypatch.setattr(tl, "FINDINGS_PATH", _p(workdir, "no_findings.json"))
    with pytest.raises(tl.TrustDataMissingError) as exc:
        tl.issue_certificate(run_id="test", inputs={"t": 1})
    assert "trust_surface.json" in str(exc.value)


def test_issue_certificate_strict_findings_missing(monkeypatch, workdir):
    """findings_trace 缺失同样 fail-closed"""
    monkeypatch.setattr(tl, "TRUST_PATH", tl.TRUST_PATH)
    monkeypatch.setattr(tl, "FINDINGS_PATH", _p(workdir, "no_findings.json"))
    with pytest.raises(tl.TrustDataMissingError):
        tl.issue_certificate(run_id="test")


def test_issue_certificate_nonstrict_degrades_with_flag(monkeypatch, workdir):
    """strict=False（显式兼容）：降级为空，但证书标注 data_complete=False"""
    monkeypatch.setattr(tl, "TRUST_PATH", _p(workdir, "no_trust_surface.json"))
    monkeypatch.setattr(tl, "FINDINGS_PATH", _p(workdir, "no_findings.json"))
    cert = tl.issue_certificate(run_id="test", inputs={"t": 1}, strict=False)
    assert cert["data_complete"] is False
    assert "缺失" in cert["degradation_reason"]
    v = tl.verify_certificate(cert)
    assert v["ok"] is True
    assert v["data_complete"] is False


def test_issue_certificate_happy_path_complete(monkeypatch, workdir):
    """数据在位（模拟最小合法 trust_surface + findings）→ data_complete=True"""
    trust_path = _p(workdir, "trust_surface.json")
    findings_path = _p(workdir, "findings_trace.json")
    monkeypatch.setattr(tl, "TRUST_PATH", trust_path)
    monkeypatch.setattr(tl, "FINDINGS_PATH", findings_path)
    with open(trust_path, "w", encoding="utf-8") as f:
        f.write('{"overall": {"scenarios": 95, "rubric_full_pass": 91}, "rows": []}')
    with open(findings_path, "w", encoding="utf-8") as f:
        f.write('{"findings": []}')
    cert = tl.issue_certificate(run_id="test", inputs={"t": 1})
    assert cert["data_complete"] is True
    assert cert["degradation_reason"] is None
    assert cert["trust_metrics"]["scenarios"] == 95
    v = tl.verify_certificate(cert)
    assert v["ok"] is True
    assert v["data_complete"] is True
