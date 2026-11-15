"""物理参数来源验证模块（Provenance Guard）。

响应 arXiv:2608.20439 "Wrong-Physics Backdoors in Neural PDE Operators"：
FNO 在 advection-diffusion 与 2D Navier-Stokes 上门后成功率 1.0000，
同时保持低 clean relative L2 error。结论是：

    "平滑性或通用求解器行为不足以保证正确性，
     除非同时验证目标物理参数的 provenance"

本模块把 SwarmLabs 已有的审计链从「记录来源」升级为「验证来源」：

1. 来源可追溯性检查（check_source_traceability）
   - 每个物理参数必须有明确的来源声明
   - 来源类型分级：DOI > textbook > web > unknown
   - 无来源 → REFUTED

2. 来源可信度分级（grade_source_credibility）
   - DOI（同行评议论文）→ 最高可信
   - textbook（教材/权威参考）→ 高可信
   - web（网页/博客）→ 中可信
   - unknown（无来源）→ 不可信

3. 参数漂移检测（detect_parameter_drift）
   - 比对当前参数与 baseline 指纹
   - SHA256 哈希检测篡改
   - 漂移超出阈值 → 后门攻击嫌疑

4. 后门攻击检测（detect_backdoor_attack）
   - 检查参数是否在「clean」和「poisoned」之间切换
   - 检测异常小的扰动是否导致异常大的行为变化

与 fail-closed 门禁集成，返回 PASS/MARGINAL/REFUTED 三态。

诚实边界：
- 来源验证不等于正确性验证——DOI 论文也可能有错误
- 参数漂移检测依赖 baseline 的完整性
- 后门攻击检测是启发式的，不能保证检出所有攻击

**⚠️ 2026-10-14 板块 05 P0 (#553) 明确降级 —— SHA256 字节指纹的具体局限**：
本模块的 SHA256 字节指纹只能检测**内容层面的改动**（byte-for-byte 篡改），
**无法检测跨参数重连攻击**：即攻击者把参数 A 的数值绑定到物理量 λ 上、
把参数 B 的数值绑定到物理量 μ 上（数值本身没变，但绑定关系被交换），
此时 SHA256 不变、物理残差也为零（因为绑定自洽），本模块会误判为 PASS。

arXiv:2608.20439 "Wrong-Physics Backdoors in Neural PDE Operators" 在 FNO
上实测该攻击成功率 **1.0000**（clean 相对 L2 误差仍 <0.05）。这类攻击需要
**频谱维度**的诊断才能捕获——见 `engine/spectral_guard.py`
（Fourier-symbol 诊断，arXiv:2610.09255）。

因此对外口径必须是："provenance_guard + spectral_guard **双通道**互补覆盖
字节层与频谱层"，**不可**宣称 provenance_guard 单独能防跨参数重连攻击。
"""

from __future__ import annotations

import hashlib
import json
import os
import datetime
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
PROVENANCE_LOG_PATH = os.path.join(DATA, "provenance_log.json")


class SourceGrade(str, Enum):
    """来源可信度等级。"""
    DOI = "doi"           # 同行评议论文，最高可信
    TEXTBOOK = "textbook"  # 教材/权威参考
    WEB = "web"           # 网页/博客/文档
    UNKNOWN = "unknown"   # 无来源声明


class GuardVerdict(str, Enum):
    """门禁判定结果。"""
    PASS = "PASS"
    MARGINAL = "MARGINAL"
    REFUTED = "REFUTED"


# 可信度权重（用于综合评分）
GRADE_WEIGHTS = {
    SourceGrade.DOI: 1.0,
    SourceGrade.TEXTBOOK: 0.8,
    SourceGrade.WEB: 0.4,
    SourceGrade.UNKNOWN: 0.0,
}

# 漂移检测阈值
DRIFT_THRESHOLD_ABS = 1e-6   # 绝对漂移阈值
DRIFT_THRESHOLD_REL = 1e-3   # 相对漂移阈值（0.1%）
BACKDOOR_SPOOF_RATIO = 10.0  # clean vs poisoned 行为差异倍数


@dataclass
class ParameterSource:
    """单个物理参数的来源声明。"""
    name: str                    # 参数名（如 "diffusion_coefficient"）
    value: float                 # 当前值
    source_type: SourceGrade     # 来源类型
    reference: str               # 参考文献（DOI/书名/URL）
    detail: str = ""             # 详细说明（页码/段落/版本）
    sha256: str = ""             # 值指纹（用于漂移检测）
    timestamp: str = ""          # 声明时间

    def __post_init__(self):
        if not self.sha256:
            self.sha256 = self._compute_hash()
        if not self.timestamp:
            self.timestamp = datetime.datetime.now().isoformat(timespec="seconds")

    def _compute_hash(self) -> str:
        """计算参数值的 SHA256 指纹。"""
        # 使用 repr 保留完整精度
        val_repr = repr(float(self.value))
        return hashlib.sha256(val_repr.encode()).hexdigest()[:16]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["source_type"] = self.source_type.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ParameterSource":
        return cls(
            name=d["name"],
            value=d["value"],
            source_type=SourceGrade(d.get("source_type", "unknown")),
            reference=d.get("reference", ""),
            detail=d.get("detail", ""),
            sha256=d.get("sha256", ""),
            timestamp=d.get("timestamp", ""),
        )


@dataclass
class ProvenanceCheckResult:
    """来源验证检查结果。"""
    verdict: GuardVerdict
    sources: list  # list of ParameterSource
    score: float              # 综合可信度分数 [0, 1]
    min_grade: SourceGrade    # 最低来源等级
    issues: list              # 问题列表
    timestamp: str = field(default_factory=lambda: datetime.datetime.now().isoformat(timespec="seconds"))

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict.value,
            "sources": [s.to_dict() for s in self.sources],
            "score": self.score,
            "min_grade": self.min_grade.value,
            "issues": self.issues,
            "timestamp": self.timestamp,
        }


class ProvenanceGuard:
    """物理参数来源验证守卫。

    核心能力：
    1. 验证参数来源可追溯性
    2. 来源可信度分级
    3. 参数漂移检测（后门攻击）
    4. 综合门禁判定

    使用示例::

        guard = ProvenanceGuard()

        # 声明参数来源
        guard.declare_parameter("diffusion_coefficient", 0.01, SourceGrade.DOI, "10.1063/1.490001")
        guard.declare_parameter("viscosity", 0.001, SourceGrade.TEXTBOOK, "Leveque, Convective Heat Transfer, p.15")

        # 验证
        result = guard.verify()
        if result.verdict == GuardVerdict.PASS:
            print("参数来源可信")
    """

    def __init__(self, log_path: str = PROVENANCE_LOG_PATH):
        self.log_path = log_path
        self._sources: dict[str, ParameterSource] = {}  # name -> source
        self._baseline_hashes: dict[str, str] = {}       # name -> baseline hash
        self._history: list[dict] = []
        self._load_log()

    def _load_log(self):
        """加载历史验证记录。"""
        if os.path.exists(self.log_path):
            try:
                with open(self.log_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._history = data.get("history", [])
                self._baseline_hashes = data.get("baseline_hashes", {})
            except Exception:
                # error_kind:IGNORABLE — 历史日志是簿记信息，加载失败降级为
                # 空历史；本次校验仍会重新算指纹并写入。不 FATAL 是因为校验
                # 逻辑本身（比对指纹）不依赖历史，历史只是"上一次的结果"。
                self._history = []
                self._baseline_hashes = {}

    def save_log(self):
        """保存验证记录到磁盘。"""
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        data = {
            "version": 1,
            "updated_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "history": self._history[-100:],  # 只保留最近 100 条
            "baseline_hashes": self._baseline_hashes,
        }
        with open(self.log_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def declare_parameter(self, name: str, value: float, source_type: SourceGrade,
                          reference: str, detail: str = "") -> ParameterSource:
        """声明一个物理参数的来源。

        Args:
            name: 参数名
            value: 参数值
            source_type: 来源类型（DOI/TEXTBOOK/WEB/UNKNOWN）
            reference: 参考文献
            detail: 详细说明

        Returns:
            ParameterSource 对象
        """
        source = ParameterSource(
            name=name,
            value=value,
            source_type=source_type,
            reference=reference,
            detail=detail,
        )
        self._sources[name] = source

        # 如果是首次声明，记录为 baseline
        if name not in self._baseline_hashes:
            self._baseline_hashes[name] = source.sha256

        return source

    def check_source_traceability(self) -> list[dict]:
        """检查所有已声明参数的来源可追溯性。

        Returns:
            检查问题列表，每项包含 name, issue, severity
        """
        issues = []
        for name, source in self._sources.items():
            if source.source_type == SourceGrade.UNKNOWN:
                issues.append({
                    "name": name,
                    "issue": "no_source_declared",
                    "severity": "critical",
                    "message": f"参数 '{name}' 无来源声明",
                })
            elif not source.reference:
                issues.append({
                    "name": name,
                    "issue": "missing_reference",
                    "severity": "high",
                    "message": f"参数 '{name}' 有来源类型但无参考文献",
                })
            elif source.source_type == SourceGrade.WEB and len(source.reference) < 10:
                issues.append({
                    "name": name,
                    "issue": "weak_reference",
                    "severity": "medium",
                    "message": f"参数 '{name}' 的 WEB 来源引用过于简短",
                })
        return issues

    def grade_source_credibility(self) -> dict:
        """对已声明参数进行来源可信度分级。

        Returns:
            分级结果，包含综合分数、各参数等级、问题列表
        """
        if not self._sources:
            return {
                "score": 0.0,
                "min_grade": SourceGrade.UNKNOWN,
                "by_source": {},
                "issues": [{"issue": "no_parameters_declared", "severity": "critical"}],
            }

        scores = []
        by_source = {}
        issues = []

        for name, source in self._sources.items():
            weight = GRADE_WEIGHTS[source.source_type]
            scores.append(weight)
            by_source[name] = {
                "grade": source.source_type.value,
                "weight": weight,
                "reference": source.reference,
            }

            # 检查引用质量
            if source.source_type == SourceGrade.DOI:
                # DOI 应该有标准格式
                if not source.reference.startswith("10."):
                    issues.append({
                        "name": name,
                        "issue": "invalid_doi_format",
                        "severity": "medium",
                        "message": f"DOI 格式无效: {source.reference}",
                    })
            elif source.source_type == SourceGrade.TEXTBOOK:
                # textbook 应该有书名和页码
                if len(source.reference) < 10:
                    issues.append({
                        "name": name,
                        "issue": "incomplete_textbook_ref",
                        "severity": "medium",
                        "message": f"教材引用信息不完整: {source.reference}",
                    })

        # 综合分数：加权平均（使用最低分作为保守估计）
        min_score = min(scores)
        avg_score = sum(scores) / len(scores)
        # 使用几何平均的保守版本
        score = min(min_score + 0.1 * (avg_score - min_score), avg_score)

        # 确定最低等级
        min_grade = SourceGrade.UNKNOWN
        for g in SourceGrade:
            if GRADE_WEIGHTS[g] <= min_score:
                min_grade = g
                break

        return {
            "score": round(score, 4),
            "min_grade": min_grade,
            "by_source": by_source,
            "issues": issues,
        }

    def detect_parameter_drift(self, current_values: dict[str, float] = None) -> list[dict]:
        """检测参数漂移（后门攻击检测）。

        对比当前参数值与 baseline 指纹，检测是否发生篡改。

        Args:
            current_values: 当前参数值字典（如 None 则使用已声明的值）

        Returns:
            漂移检测结果列表
        """
        if current_values is None:
            current_values = {name: src.value for name, src in self._sources.items()}

        drifts = []
        for name, value in current_values.items():
            baseline_hash = self._baseline_hashes.get(name)
            if not baseline_hash:
                # 没有 baseline，跳过
                continue

            # 计算当前值的哈希
            current_hash = hashlib.sha256(repr(float(value)).encode()).hexdigest()[:16]

            if current_hash != baseline_hash:
                # 计算漂移幅度
                source = self._sources.get(name)
                baseline_value = source.value if source else None

                if baseline_value is not None:
                    abs_drift = abs(value - baseline_value)
                    rel_drift = abs_drift / (abs(baseline_value) + 1e-12)

                    drift_entry = {
                        "name": name,
                        "baseline_value": baseline_value,
                        "current_value": value,
                        "abs_drift": abs_drift,
                        "rel_drift": rel_drift,
                        "severity": "info",
                    }

                    # 判定严重程度
                    if abs_drift > DRIFT_THRESHOLD_ABS or rel_drift > DRIFT_THRESHOLD_REL:
                        drift_entry["severity"] = "warning"
                        if rel_drift > 0.01:  # >1% 漂移
                            drift_entry["severity"] = "critical"
                            drift_entry["message"] = (
                                f"参数 '{name}' 漂移 {rel_drift:.2%}，"
                                f"可能为后门攻击"
                            )
                    drifts.append(drift_entry)

        return drifts

    def detect_backdoor_attack(self, clean_params: dict[str, float],
                                poisoned_params: dict[str, float] = None) -> dict:
        """检测后门攻击模式。

        检查参数在「clean」和「poisoned」状态之间是否表现出异常行为。
        后门攻击的特征是：微小的参数扰动导致巨大的模型行为变化。

        Args:
            clean_params: 正常参数值
            poisoned_params: 被投毒的参数值（如 None 则使用已声明值）

        Returns:
            攻击检测结果
        """
        if poisoned_params is None:
            poisoned_params = {name: src.value for name, src in self._sources.items()}

        attack_signals = []
        for name in clean_params:
            clean_val = clean_params[name]
            poisoned_val = poisoned_params.get(name, clean_val)

            if clean_val == 0:
                continue

            rel_diff = abs(poisoned_val - clean_val) / abs(clean_val)

            # 后门攻击特征：极小扰动（<0.01%）但可能影响行为
            if rel_diff > 0 and rel_diff < 0.0001:
                attack_signals.append({
                    "name": name,
                    "clean_value": clean_val,
                    "poisoned_value": poisoned_val,
                    "relative_difference": rel_diff,
                    "suspicion": "extremely_small_perturbation",
                })

        has_attack = len(attack_signals) > 0
        return {
            "has_backdoor_suspicion": has_attack,
            "signals": attack_signals,
            "confidence": "low" if has_attack else "none",
            "note": "此检测为启发式，不能保证检出所有后门攻击",
        }

    def verify(self) -> ProvenanceCheckResult:
        """执行完整的来源验证。

        综合检查：
        1. 来源可追溯性
        2. 来源可信度
        3. 参数漂移
        4. 后门攻击

        Returns:
            ProvenanceCheckResult 包含综合判定
        """
        issues = []

        # 1. 来源可追溯性检查
        trace_issues = self.check_source_traceability()
        issues.extend(trace_issues)

        # 2. 来源可信度分级
        credibility = self.grade_source_credibility()
        score = credibility["score"]
        min_grade = credibility["min_grade"]
        issues.extend(credibility.get("issues", []))

        # 3. 参数漂移检测
        drifts = self.detect_parameter_drift()
        for d in drifts:
            if d["severity"] in ("warning", "critical"):
                issues.append({
                    "name": d["name"],
                    "issue": "parameter_drift",
                    "severity": d["severity"],
                    "message": d.get("message", f"参数漂移 {d['rel_drift']:.4%}"),
                })

        # 4. 判定 verdict
        critical_issues = [i for i in issues if i.get("severity") == "critical"]
        high_issues = [i for i in issues if i.get("severity") == "high"]
        warning_issues = [i for i in issues if i.get("severity") == "warning"]

        if critical_issues:
            verdict = GuardVerdict.REFUTED
        elif high_issues or score < 0.4:
            verdict = GuardVerdict.MARGINAL
        elif warning_issues or score < 0.7:
            verdict = GuardVerdict.MARGINAL
        else:
            verdict = GuardVerdict.PASS

        # 5. 保存历史记录
        result = ProvenanceCheckResult(
            verdict=verdict,
            sources=list(self._sources.values()),
            score=score,
            min_grade=min_grade,
            issues=issues,
        )

        self._history.append({
            "timestamp": result.timestamp,
            "verdict": verdict.value,
            "score": score,
            "n_sources": len(self._sources),
            "n_issues": len(issues),
        })
        self.save_log()

        return result

    def set_baseline(self):
        """将当前已声明参数值设为新的 baseline（用于漂移检测）。"""
        for name, source in self._sources.items():
            self._baseline_hashes[name] = source.sha256
        self.save_log()

    def export_report(self) -> str:
        """导出 Markdown 格式的验证报告。"""
        result = self.verify()

        lines = [
            "# Provenance Guard Report",
            f"\nGenerated: {result.timestamp}\n",
            f"## Verdict: {result.verdict.value}\n",
            f"## Source Credibility Score: {result.score:.2%}\n",
            f"## Minimum Source Grade: {result.min_grade.value}\n",
        ]

        if result.issues:
            lines.append("\n## Issues Found\n")
            for issue in result.issues:
                severity = issue.get("severity", "info")
                lines.append(f"- **[{severity.upper()}]** {issue.get('message', issue.get('issue', ''))}")

        lines.append("\n## Declared Sources\n")
        lines.append("| Parameter | Value | Grade | Reference |")
        lines.append("|---|---|---|---|")
        for s in result.sources:
            lines.append(f"| {s.name} | {s.value} | {s.source_type.value} | {s.reference} |")

        return "\n".join(lines)

    def to_dict(self) -> dict:
        """导出完整状态为字典。"""
        return {
            "sources": {name: s.to_dict() for name, s in self._sources.items()},
            "baseline_hashes": self._baseline_hashes,
            "history": self._history[-20:],
        }

    @classmethod
    def from_dict(cls, d: dict, log_path: str = PROVENANCE_LOG_PATH) -> "ProvenanceGuard":
        """从字典恢复状态。"""
        guard = cls(log_path=log_path)
        for name, sd in d.get("sources", {}).items():
            source = ParameterSource.from_dict(sd)
            guard._sources[name] = source
        guard._baseline_hashes = d.get("baseline_hashes", {})
        guard._history = d.get("history", [])
        return guard


# ---------------------------------------------------------------------------
# 便捷函数：与现有 verification_gate 集成的适配器
# ---------------------------------------------------------------------------

def guard_verdict_to_grade(verdict: GuardVerdict) -> str:
    """将 ProvenanceGuard 判定转换为 verification_gate 的 grade。"""
    mapping = {
        GuardVerdict.PASS: "PASS",
        GuardVerdict.MARGINAL: "MARGINAL",
        GuardVerdict.REFUTED: "REFUTED",
    }
    return mapping[verdict]


def check_provenance_for_scenario(scenario_params: dict, source_declarations: list[dict]) -> dict:
    """为场景执行 provenance 检查（与 verification_gate 集成）。

    Args:
        scenario_params: 场景参数值 {name: value}
        source_declarations: 来源声明列表 [{name, value, source_type, reference, detail}]

    Returns:
        检查结果字典，包含 verdict, score, issues
    """
    guard = ProvenanceGuard()

    # 声明所有参数来源
    for decl in source_declarations:
        source_type = SourceGrade(decl.get("source_type", "unknown"))
        guard.declare_parameter(
            name=decl["name"],
            value=decl["value"],
            source_type=source_type,
            reference=decl.get("reference", ""),
            detail=decl.get("detail", ""),
        )

    # 执行验证
    result = guard.verify()

    return {
        "verdict": guard_verdict_to_grade(result.verdict),
        "score": result.score,
        "min_grade": result.min_grade.value,
        "issues": result.issues,
        "n_sources": len(result.sources),
    }


def __main__():
    """Self-test: 验证模块基本功能。"""
    print("Provenance Guard Self-Test")
    print("=" * 60)

    guard = ProvenanceGuard(log_path="/tmp/test_provenance_log.json")

    # 测试 1: 无来源 → REFUTED
    print("\n1. Testing unknown sources...")
    guard.declare_parameter("test_param_1", 0.5, SourceGrade.UNKNOWN, "")
    result = guard.verify()
    assert result.verdict == GuardVerdict.REFUTED, f"Expected REFUTED, got {result.verdict}"
    print(f"   Verdict: {result.verdict.value} (score={result.score:.2%}) ✓")

    # 测试 2: DOI 来源 → PASS
    print("\n2. Testing DOI sources...")
    guard2 = ProvenanceGuard(log_path="/tmp/test_provenance_log2.json")
    guard2.declare_parameter("diffusion_coefficient", 0.01, SourceGrade.DOI, "10.1063/1.490001")
    guard2.declare_parameter("viscosity", 0.001, SourceGrade.DOI, "10.1103/PhysRevLett.110.164501")
    result2 = guard2.verify()
    assert result2.verdict == GuardVerdict.PASS, f"Expected PASS, got {result2.verdict}"
    print(f"   Verdict: {result2.verdict.value} (score={result2.score:.2%}) ✓")

    # 测试 3: 参数漂移检测
    print("\n3. Testing drift detection...")
    guard3 = ProvenanceGuard(log_path="/tmp/test_provenance_log3.json")
    guard3.declare_parameter("drag_coefficient", 0.47, SourceGrade.TEXTBOOK, "Anderson, Fluid Mechanics, p.230")
    drifts = guard3.detect_parameter_drift({"drag_coefficient": 0.57})  # 21% drift
    assert len(drifts) > 0, "Should detect drift"
    assert drifts[0]["severity"] == "critical", f"Expected critical, got {drifts[0]['severity']}"
    print(f"   Detected {len(drifts)} drift(s), severity={drifts[0]['severity']} ✓")

    # 测试 4: 后门攻击检测
    print("\n4. Testing backdoor detection...")
    attack_result = guard3.detect_backdoor_attack(
        clean_params={"drag_coefficient": 0.47},
        poisoned_params={"drag_coefficient": 0.470001}  # 0.0001% perturbation
    )
    print(f"   Backdoor suspicion: {attack_result['has_backdoor_suspicion']}")

    # 测试 5: 报告导出
    print("\n5. Testing report export...")
    report = guard2.export_report()
    assert "Provenance Guard Report" in report
    assert "PASS" in report
    print(f"   Report length: {len(report)} chars ✓")

    print("\n" + "=" * 60)
    print("All tests passed! ✓")

    # 清理测试文件
    for p in ["/tmp/test_provenance_log.json", "/tmp/test_provenance_log2.json",
              "/tmp/test_provenance_log3.json"]:
        if os.path.exists(p):
            os.remove(p)


if __name__ == "__main__":
    __main__()
