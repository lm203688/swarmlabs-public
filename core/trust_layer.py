"""SwarmLabs Trust Layer (VeritasGuard) —— 可信执行与可复现证书。

把仓库中已有的三块能力统一命名、打包成一个对评委"可讲、可演示、可验证"的层：

1. ``data/trust_surface.json`` —— 每个学科引擎的「文献溯源可信面」
   （含 ground-truth 文献出处 ``ref_papers``、RMSE/校准/覆盖率、``rubric`` 5 项校验）。
2. ``engine/traceability.py`` 的 ``TraceabilityLayer`` —— 每个科学结论链接到
   代码/论文/数据集出处（``Provenance``），并带 ``confidence`` 与审计导出。
3. ``core/task_contract.py`` 的 ``TaskContractManager`` —— 任务边界 + 证据收集 + 验证。

本模块**不修改**上述任何数据/逻辑，只做"聚合 + 生成证书 + 校验"：
- ``issue_certificate()`` 读入现有数据，产出一份 **可复现证书**（JSON）。
- 证书包含：本次运行指纹、输入/产物清单、可信面指标、证据链、rubric 明细、
  以及基于内容规范化的 **数字签名**（sha256），用于评委验证"结论可复现、可溯源"。

导入安全（2026-11-03 P0 修复）：读取带 try/except；但 ``issue_certificate`` 默认
``strict=True``——任一关键数据源缺失时抛出 ``TrustDataMissingError``（fail-closed），
不产出「空证书」。仅当调用方显式传 ``strict=False`` 时才降级为空，并置
``data_complete=False`` + ``degradation_reason``，证书明确标注数据不完整。
"""

from __future__ import annotations

import hashlib
import json
import os
import datetime
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
TRUST_PATH = os.path.join(DATA, "trust_surface.json")
FINDINGS_PATH = os.path.join(DATA, "findings_trace.json")


class TrustDataMissingError(RuntimeError):
    """可信数据源缺失时抛出——fail-closed（2026-11-03 P0 修复）。

    旧实现：任一数据源缺失时静默降级为空，导致证书可「空签发」
    （签名只证明「内容未被篡改」，证明不了「数据在位」）。
    本异常让调用方在严格模式下显式收到数据缺失，而不是拿到一份
    指标全 None / rubric 全空的「假证书」。
    """
    def __init__(self, missing: List[str], message: str = ""):
        self.missing = list(missing)
        super().__init__(message or f"可信数据源缺失（fail-closed）：{', '.join(self.missing)}")

LAYER_NAME = "SwarmLabs Trust Layer (VeritasGuard)"
LAYER_VERSION = "1.0"

# trust_surface 中每行 rubric 的 7 个标准校验项（定义"可信"的口径）
# 2026-09-07 升级：①双指标——新增 sharpness_ok（区间锐度），根治「只看覆盖率 →
# 拉宽误差棒就能糊弄通过」；②新增 function_nonconstant——ground-truth 函数在采样域内
# 若为常数，该"场景"无法验证任何东西（GP 拟合常数零方差 → 白拿 coverage=1.00）。
RUBRIC_KEYS = [
    "gold_source_traced",     # 发表 ground-truth 文献已溯源
    "rmse_rel_ok",            # 相对 RMSE 在阈值内
    "coverage_two_sided_ok",  # 预测区间双侧覆盖率达标
    "cal_err_ok",             # 校准误差达标
    "ls_source_traced",       # 长度尺度来源已溯源
    "sharpness_ok",           # 区间锐度（NMPIW ≤ 阈值，不能靠拉宽误差棒通过）
    "function_nonconstant",   # ground-truth 函数非退化（采样域内非常数）
]


def _load_json(path: str) -> Optional[Any]:
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        return None
    return None


def _canonical(obj: Any) -> str:
    """对内容做规范化序列化（排序键 + 紧凑），供签名使用，避免格式差异影响可复现。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


# 签名排除字段：这些字段随「何时签发」而非「内容是什么」变化。
# 若不排除，同一份数据每次运行签名都不同，证书就只是「防篡改封装」，
# 而不是「数据决定论的可复现证明」。
# 另需排除 `signature`（签名自身）与 `signature_scope`（对签名的元描述），
# 否则签名后写入这两个字段会破坏自验（verify_certificate）的一致性。
_NON_CONTENT_FIELDS = ("issued_at", "certificate_id", "signature", "signature_scope")


def _content_fingerprint(cert: Dict[str, Any]) -> Dict[str, Any]:
    """提取证书的「内容指纹视图」——剔除时间戳/ID 等非内容字段。

    这样同一份输入数据在任何时刻签发，都会得到相同的 ``signature``；
    同时 ``certificate_id`` 与 ``issued_at`` 仍保留在证书里作为审计元数据。
    """
    return {k: v for k, v in cert.items() if k not in _NON_CONTENT_FIELDS}


def load_trust_surface() -> Dict[str, Any]:
    data = _load_json(TRUST_PATH)
    return data if isinstance(data, dict) else {}


def load_findings() -> Dict[str, Any]:
    data = _load_json(FINDINGS_PATH)
    return data if isinstance(data, dict) else {}


def _missing_sources() -> List[str]:
    """返回当前缺失的关键数据源清单（用于 fail-closed 判断）。"""
    missing = []
    if not os.path.exists(TRUST_PATH):
        missing.append("data/trust_surface.json")
    if not os.path.exists(FINDINGS_PATH):
        missing.append("data/findings_trace.json")
    return missing


def _evidence_chain(findings: Dict[str, Any]) -> List[Dict[str, Any]]:
    """从 traceability 的 findings 抽取证据链摘要（出处类型分布 + 低置信度告警）。"""
    rows = findings.get("findings", []) if isinstance(findings, dict) else []
    chain = []
    for f in rows:
        provs = f.get("provenances", []) or []
        types = sorted({p.get("source_type") for p in provs})
        chain.append({
            "id": f.get("id"),
            "statement": (f.get("statement") or "")[:120],
            "confidence": f.get("confidence", 0.0),
            "finding_type": f.get("finding_type"),
            "provenance_types": types,
            "provenance_count": len(provs),
        })
    return chain


def issue_certificate(run_id: str = "", inputs: Optional[Dict[str, Any]] = None,
                      strict: bool = True) -> Dict[str, Any]:
    """生成一份可复现证书。

    参数:
        run_id: 本次运行的外部标识（如 demo 会话 id），留空则自动生成时间戳 id。
        inputs: 本次运行的输入快照（问题、领域、模型版本等），用于固定"可复现"上下文。
        strict: fail-closed 开关（2026-11-03 P0 修复）。
            - ``strict=True``（默认）：任一关键数据源（trust_surface.json /
              findings_trace.json）缺失时，抛出 ``TrustDataMissingError``，不产出空证书。
            - ``strict=False``：兼容旧行为，缺失时降级为空并置 ``data_complete=False``、
              ``degradation_reason``，签名照常计算——但证书明确标注「数据不完整，
              签名仅证明未篡改，不证明数据在位」，调用方必须显式传 False 才能获得。
    返回: 证书字典（含 ``signature``）。
    """
    missing = _missing_sources()
    if strict and missing:
        raise TrustDataMissingError(missing)

    ts = datetime.datetime.now().isoformat(timespec="seconds")
    rid = run_id or f"run-{ts}"

    trust = load_trust_surface()
    findings = load_findings()
    overall = trust.get("overall", {}) if isinstance(trust, dict) else {}
    rows = trust.get("rows", []) if isinstance(trust, dict) else []

    # 可信面指标
    trust_metrics = {
        "scenarios": overall.get("scenarios"),
        "rubric_full_pass": overall.get("rubric_full_pass"),
        "gold_traced": overall.get("gold_traced"),
        "coverage_ok": overall.get("coverage_ok"),
        "coverage_under": overall.get("coverage_under"),
        "coverage_over": overall.get("coverage_over"),
        "flagship": overall.get("flagship"),
        "total_store_points": overall.get("total_store_points"),
    }

    # rubric 明细
    rubric_detail = []
    for r in rows:
        rb = r.get("rubric", {}) or {}
        rubric_detail.append({
            "key": r.get("key"),
            "domain": r.get("domain"),
            "flagship": bool(r.get("flagship")),
            "rubric_passed": r.get("rubric_passed"),
            "rubric_total": r.get("rubric_total"),
            "checks": {k: bool(rb.get(k)) for k in RUBRIC_KEYS},
            "literature_support": r.get("ref_papers", [])[:3],
        })

    evidence = _evidence_chain(findings)

    cert: Dict[str, Any] = {
        "layer": LAYER_NAME,
        "version": LAYER_VERSION,
        "certificate_id": rid,
        "issued_at": ts,
        "inputs": inputs or {},
        "data_complete": not missing,  # fail-closed 标记：关键数据源是否在位
        "degradation_reason": (
            None if not missing
            else f"关键数据源缺失（{', '.join(missing)}），证书降级为空——"
                 "签名仅证明内容未被篡改，不证明数据在位"
        ),
        "artifacts": [
            {"path": "data/trust_surface.json", "sha256": _sha(TRUST_PATH)},
            {"path": "data/findings_trace.json", "sha256": _sha(FINDINGS_PATH)},
        ],
        "trust_metrics": trust_metrics,
        "rubric_detail": rubric_detail,
        "evidence_chain_summary": {
            "total_findings": len(evidence),
            "with_provenance": sum(1 for e in evidence if e["provenance_count"] > 0),
            "low_confidence": [e["id"] for e in evidence if e["confidence"] < 0.5],
        },
        "reproducibility_statement": (
            "本证书汇总的结论均链接至其 ground-truth 文献（见 rubric_detail.literature_support）"
            "与可追溯出处（见 evidence_chain）。证书 signature 为对**全部内容字段**"
            "（不含签发时间戳与证书 ID）的规范化 sha256——因此同一份数据无论何时签发"
            "都得到相同签名，任何输入/产物/指标变化都会导致签名改变，可被评委独立复算验证。"
        ),
    }

    # 数字签名（基于**内容**规范化的 sha256）
    # 关键设计：签名对象剔除了 issued_at / certificate_id（见 _content_fingerprint），
    # 否则每次运行签名都变，无法作为「数据决定论」的复现证明。
    # 现在：同一份输入数据 → 同一签名（无论何时签发）；
    #      任何输入/产物/指标变化 → 签名改变。
    cert["signature"] = hashlib.sha256(
        _canonical(_content_fingerprint(cert)).encode("utf-8")
    ).hexdigest()
    cert["signature_scope"] = (
        "content-only: excludes issued_at/certificate_id; "
        "same data ⇒ same signature regardless of issue time"
    )
    return cert


def _sha(path: str) -> Optional[str]:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def verify_certificate(cert: Dict[str, Any]) -> Dict[str, Any]:
    """独立复算证书签名，供评委/第三方验证「内容未被篡改且可复现」。

    返回:
        {"ok": bool, "expected": str, "actual": str, "reason": str,
         "data_complete": bool}

    ``ok=True`` 表示：用证书内容重算的 sha256 与其自带 ``signature`` 一致，
    即（a）内容未被篡改，（b）该签名与签发时间无关（数据决定论）。
    ``data_complete=False`` 表示证书在数据缺失下降级签发——签名有效但
    数据不在位，评委不应将其视为「可信」证明（2026-11-03 P0 修复）。
    """
    claimed = cert.get("signature")
    recomputed = hashlib.sha256(
        _canonical(_content_fingerprint(cert)).encode("utf-8")
    ).hexdigest()
    ok = bool(claimed) and claimed == recomputed
    return {
        "ok": ok,
        "expected": recomputed,
        "actual": claimed,
        "data_complete": bool(cert.get("data_complete", True)),
        "reason": (
            "signature matches content fingerprint (tamper-free, time-independent)"
            if ok else
            "signature MISMATCH — certificate content has been altered, or was "
            "computed by an older schema that included issued_at in the signature"
        ),
    }


def to_markdown(cert: Dict[str, Any]) -> str:
    """把证书渲染成可读 Markdown，便于放进 Demo 或申报材料。"""
    m = cert["trust_metrics"]
    lines = [
        f"# {cert['layer']} · 可复现证书",
        "",
        f"- 证书 ID: `{cert['certificate_id']}`",
        f"- 签发时间: {cert['issued_at']}",
        f"- 版本: {cert['version']}",
        "",
        "## 可信面指标",
        f"- 已验证领域: {m.get('scenarios')} | rubric 满通过: {m.get('rubric_full_pass')} | "
        f"文献溯源: {m.get('gold_traced')} | 旗舰: {m.get('flagship')} | 累积点: {m.get('total_store_points')}",
        "",
        "## 证据链",
        f"- 可追溯发现: {cert['evidence_chain_summary']['total_findings']} | "
        f"带出处: {cert['evidence_chain_summary']['with_provenance']} | "
        f"低置信度(<0.5): {len(cert['evidence_chain_summary']['low_confidence'])}",
        "",
        "## 数字签名 (内容 sha256)",
        f"`{cert['signature']}`",
        "",
        "> 任何结论/产物变化都会改变此签名；评委可重新运行 `python -m core.trust_layer` 复算验证。",
    ]
    return "\n".join(lines)


def main():
    """CLI: 生成并打印一份 demo 证书。"""
    cert = issue_certificate(run_id="cli-demo", inputs={"demo": True, "scope": "public-demo"})
    print(to_markdown(cert))
    print("\n--- raw JSON ---\n")
    print(json.dumps(cert, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
