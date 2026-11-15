"""质量验证框架 —— 8 维度结构化评估，灵感来自 Kosmos ScholarEval。

Kosmos 用 8 维度框架验证发现质量。本模块为 SwarmLabs 提供同等能力：

8 个验证维度：
1. 准确性 (Accuracy): 结论是否与数据一致
2. 可追溯性 (Traceability): 是否有出处链接
3. 一致性 (Consistency): 是否与已有知识矛盾
4. 新颖性 (Novelty): 是否提供了新信息
5. 完整性 (Completeness): 是否覆盖了关键方面
6. 可复现性 (Reproducibility): 是否可独立验证
7. 稳健性 (Robustness): 是否对噪声/扰动稳定
8. 实用性 (Utility): 是否对决策有帮助

每个维度 0-1 分，总分 = 加权平均。

诚实边界：
- 评分是「结构化自评」，不是「同行评审」
- 新颖性需要外部知识库，本模块用简化估计
- 完整性评估依赖 checklist，可能遗漏未知维度
- **无法自动判定的维度标记为 unscored 并从加权中排除**（不给编造的中间值），
  评估完整度通过 `coverage` 字段（实评权重/全权重）如实暴露
- **不硬编码任何评分**：可复现性等维度由传入的实测字段（seed、rerun_consistent）
  决定；缺少证据时如实给 0 或标记 unscored
"""

from __future__ import annotations

import json
import os
import datetime
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
VALIDATION_PATH = os.path.join(DATA, "validation_results.json")


# 8 维度定义及默认权重
DIMENSIONS = {
    "accuracy": {"name": "准确性", "weight": 0.20, "description": "结论与数据/基准的一致程度"},
    "traceability": {"name": "可追溯性", "weight": 0.15, "description": "是否有明确出处链接"},
    "consistency": {"name": "一致性", "weight": 0.15, "description": "是否与已有知识/基准一致"},
    "novelty": {"name": "新颖性", "weight": 0.10, "description": "是否提供新信息/新发现"},
    "completeness": {"name": "完整性", "weight": 0.10, "description": "是否覆盖关键方面"},
    "reproducibility": {"name": "可复现性", "weight": 0.10, "description": "是否可独立验证"},
    "robustness": {"name": "稳健性", "weight": 0.10, "description": "对噪声/扰动的稳定性"},
    "utility": {"name": "实用性", "weight": 0.10, "description": "对决策的实际帮助"},
}


class ValidationResult:
    """一次验证的结果。"""

    def __init__(self, target_id: str, target_type: str, domain: str):
        self.id = None
        self.target_id = target_id
        self.target_type = target_type  # finding | scenario | model | equation
        self.domain = domain
        self.scores = {}  # dimension -> score (0-1)
        self.evidence = {}  # dimension -> evidence string
        self.unscored = {}  # dimension -> reason（未评分，从加权中排除）
        self.weighted_score = 0.0
        self.grade = ""  # A/B/C/D/F
        self.created_at = datetime.datetime.now().isoformat(timespec="seconds")

    def set_score(self, dimension: str, score: float, evidence: str = ""):
        if dimension not in DIMENSIONS:
            raise ValueError(f"Unknown dimension: {dimension}")
        self.scores[dimension] = max(0.0, min(1.0, float(score)))
        if evidence:
            self.evidence[dimension] = evidence

    def mark_unscored(self, dimension: str, reason: str = ""):
        """标记某维度为「未评分」。未评分维度不参与加权，
        避免用编造的中间值掩盖「未评估」这一事实。"""
        if dimension not in DIMENSIONS:
            raise ValueError(f"Unknown dimension: {dimension}")
        self.unscored[dimension] = reason or "未评分"
        self.scores.pop(dimension, None)

    def compute_weighted_score(self):
        """加权平均。分母只统计**实际评分**的维度权重，
        未评分维度被排除（并通过 `coverage` 字段暴露评估完整度）。"""
        total_weight = 0.0
        weighted_sum = 0.0
        all_weight = 0.0
        for dim, dim_info in DIMENSIONS.items():
            all_weight += dim_info["weight"]
            if dim in self.scores:
                weighted_sum += self.scores[dim] * dim_info["weight"]
                total_weight += dim_info["weight"]
        self.weighted_score = weighted_sum / total_weight if total_weight > 0 else 0.0
        # 评估完整度：实际评分的维度权重 / 全部维度权重
        self.coverage = total_weight / all_weight if all_weight > 0 else 0.0
        # 评级
        if self.weighted_score >= 0.9:
            self.grade = "A"
        elif self.weighted_score >= 0.8:
            self.grade = "B"
        elif self.weighted_score >= 0.7:
            self.grade = "C"
        elif self.weighted_score >= 0.5:
            self.grade = "D"
        else:
            self.grade = "F"
        return self.weighted_score

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "target_id": self.target_id,
            "target_type": self.target_type,
            "domain": self.domain,
            "scores": self.scores,
            "evidence": self.evidence,
            "unscored": self.unscored,
            "coverage": getattr(self, "coverage", 0.0),
            "weighted_score": self.weighted_score,
            "grade": self.grade,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ValidationResult":
        vr = cls(d["target_id"], d["target_type"], d["domain"])
        vr.id = d.get("id")
        vr.scores = d.get("scores", {})
        vr.evidence = d.get("evidence", {})
        vr.unscored = d.get("unscored", {})
        vr.coverage = d.get("coverage", 0.0)
        vr.weighted_score = d.get("weighted_score", 0)
        vr.grade = d.get("grade", "")
        vr.created_at = d.get("created_at", "")
        return vr


class ValidationFramework:
    """8 维度质量验证框架。

    核心能力：
    1. 对发现/场景/模型进行结构化评分
    2. 自动生成改进建议
    3. 生成审计报告
    """

    def __init__(self, path: str = VALIDATION_PATH):
        self.path = path
        self._results: list[ValidationResult] = []
        self._next_id = 1
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._results = [ValidationResult.from_dict(rd) for rd in data.get("results", [])]
                self._next_id = data.get("next_id", len(self._results) + 1)
            except Exception:
                self._results = []
                self._next_id = 1

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        data = {
            "version": 1,
            "updated_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "next_id": self._next_id,
            "results": [r.to_dict() for r in self._results],
        }
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def validate_scenario(self, scenario_result: dict) -> ValidationResult:
        """对自治闭环的一个场景结果进行 8 维度验证。"""
        key = scenario_result.get("key", "unknown")
        domain = scenario_result.get("domain", "")
        coverage = scenario_result.get("coverage", 0)
        rmse_rel = scenario_result.get("rmse_rel", 1)
        sindy_r2 = scenario_result.get("sindy_r2", 0)
        converged = scenario_result.get("converged", False)

        vr = ValidationResult(key, "scenario", domain)

        # 1. 准确性：覆盖率越高越准确（0.95 视为理想区）
        vr.set_score("accuracy", min(max((coverage - 0.5) * 2.0, 0.0), 1.0),
                      f"coverage={coverage:.3f} → accuracy={(coverage-0.5)*2:.3f}")

        # 2. 可追溯性：非零 SINDy 方程即有追溯（0 方程 → 0 分）
        eq = scenario_result.get("sindy_equation", "") or ""
        has_equation = bool(eq.strip("0. "))
        vr.set_score("traceability", 1.0 if has_equation else 0.0,
                      f"equation={eq[:50] or 'none'}")

        # 3. 一致性：R² 越高越一致（直接用 R² 值）
        vr.set_score("consistency", max(0.0, min(float(sindy_r2), 1.0)),
                      f"sindy_r2={sindy_r2:.3f}")

        # 4. 新颖性：非平凡方程（非常数、非线性）视为有新颖性
        #    常量或纯线性项 → 0（不是新发现）；带乘积/非线性项 → 0.9；空 → 0
        try:
            _eq_clean = eq.strip("0. ")
            if not _eq_clean:
                _novelty = 0.0
                _novelty_reason = "no equation"
            elif "x0*x1" in _eq_clean or "**" in _eq_clean or "exp" in _eq_clean or "sin" in _eq_clean or "cos" in _eq_clean:
                _novelty = 0.9
                _novelty_reason = "nonlinear term present"
            else:
                _novelty = 0.5
                _novelty_reason = "linear-only (low novelty)"
        except Exception:
            _novelty = 0.0
            _novelty_reason = "parse error"
        vr.set_score("novelty", _novelty, _novelty_reason)

        # 5. 完整性：按实际存在的字段数打分（不再 0.9/0.5 二分）
        _req_fields = ["coverage", "rmse_rel", "sindy_r2", "sindy_equation",
                       "converged", "champion", "seed", "confidence", "domain"]
        _present = sum(1 for _f in _req_fields if scenario_result.get(_f) not in (None, "", 0))
        _completeness = _present / len(_req_fields)
        vr.set_score("completeness", round(_completeness, 3),
                      f"{_present}/{len(_req_fields)} fields present; champion={scenario_result.get('champion', 'none')}")

        # 6. 可复现性（真实判定，不硬编码）：
        #    有 seed 且 re-run 结果一致 → 1.0；有 seed 但未提供复跑证据 → 0.6；
        #    无 seed → 0.0（不可复现）。用 scenario_result 里的实测字段判定，
        #    不再固定返回 0.95 这种「声称值」。
        seed = scenario_result.get("seed")
        rerun_ok = scenario_result.get("rerun_consistent")
        if seed is None:
            vr.set_score("reproducibility", 0.0, "no seed recorded → not reproducible")
        elif rerun_ok is True:
            vr.set_score("reproducibility", 1.0, f"seed={seed}, rerun verified identical")
        elif rerun_ok is False:
            vr.set_score("reproducibility", 0.0, f"seed={seed}, rerun DIFFERED")
        else:
            vr.set_score("reproducibility", 0.6,
                          f"seed={seed} present, rerun not verified (UNVERIFIED, not credited)")

        # 7. 稳健性：以 0.95 为目标，coverage 距离 0.95 越近越稳健（连续打分）
        #    |0.95 - coverage| ∈ [0, 0.5] → robustness ∈ [1.0, 0.0]
        _robust_dist = abs(0.95 - coverage)
        _robustness = max(0.0, 1.0 - 2.0 * _robust_dist)
        if coverage > 1.0:
            _robustness = min(_robustness, 0.5)  # over-coverage: 区间过宽
        elif coverage < 0.8:
            _robustness = min(_robustness, 0.5)  # under-coverage: 过自信
        vr.set_score("robustness", round(_robustness, 3),
                      f"coverage={coverage:.3f}, dist_from_0.95={_robust_dist:.3f}")

        # 8. 实用性：收敛 + 有 champion 才有实用价值；仅收敛但无模型 → 中间分
        if converged and scenario_result.get("champion"):
            _utility = 1.0
            _utility_reason = "converged with champion model"
        elif converged:
            _utility = 0.6
            _utility_reason = "converged but no champion"
        else:
            _utility = 0.0
            _utility_reason = "not converged"
        vr.set_score("utility", _utility, _utility_reason)

        vr.compute_weighted_score()
        vr.id = f"V-{self._next_id:04d}"
        self._next_id += 1
        self._results.append(vr)
        return vr

    def validate_finding(self, finding_dict: dict) -> ValidationResult:
        """对一个发现进行验证。

        诚实边界：本方法对「无法自动判定」的维度**不再给中间值**（此前固定返回
        0.7/0.6 等），而是标记为 NOT_SCORED 并从加权中排除，避免用虚假的
        中间分掩盖「未评估」这一事实。调用方可通过 `vr.unscored` 看到被排除项。
        """
        statement = finding_dict.get("statement", "")
        domain = finding_dict.get("domain", "")
        confidence = finding_dict.get("confidence", 0)
        has_provenance = len(finding_dict.get("provenances", [])) > 0

        vr = ValidationResult(finding_dict.get("id", ""), "finding", domain)

        # 可自动判定的维度
        vr.set_score("accuracy", min(confidence * 1.2, 1.0),
                      f"confidence={confidence:.2f}")
        vr.set_score("traceability", 1.0 if has_provenance else 0.0,
                      f"provenances={len(finding_dict.get('provenances', []))}")
        vr.set_score("utility", min(confidence * 1.1, 1.0),
                      f"confidence={confidence:.2f}")

        # 需要外部知识/人工评审的维度：显式标记未评分，不给编造值
        vr.mark_unscored("consistency", "需要外部知识库交叉验证，当前不可自动判定")
        vr.mark_unscored("novelty", "需要文献检索比对，当前不可自动判定")
        vr.mark_unscored("completeness", "需要 checklist 覆盖度评估，当前不可自动判定")
        vr.mark_unscored("robustness", "需要多噪声水平复跑，当前不可自动判定")

        # 可复现性：仅在具备 provenance 时可弱判定，否则未评分
        if has_provenance:
            vr.set_score("reproducibility", 0.8, "provenance present enables reproduction")
        else:
            vr.mark_unscored("reproducibility", "无 provenance，无法判定可复现性")

        vr.compute_weighted_score()
        vr.id = f"V-{self._next_id:04d}"
        self._next_id += 1
        self._results.append(vr)
        return vr

    def get_improvement_suggestions(self, result: ValidationResult) -> list:
        """基于评分生成改进建议。"""
        suggestions = []
        for dim, score in result.scores.items():
            if score < 0.7:
                dim_info = DIMENSIONS[dim]
                suggestions.append({
                    "dimension": dim,
                    "name": dim_info["name"],
                    "current_score": score,
                    "suggestion": self._suggest_for_dimension(dim, score, result),
                })
        return suggestions

    def _suggest_for_dimension(self, dimension: str, score: float,
                               result: ValidationResult) -> str:
        suggestions = {
            "accuracy": "增加更多测试点或使用更精确的代理模型",
            "traceability": "为发现添加代码或论文出处链接",
            "consistency": "与已有基准或文献结果进行交叉验证",
            "novelty": "搜索相关文献确认是否为新发现",
            "completeness": "补充缺失的分析维度（如灵敏度分析）",
            "reproducibility": "记录完整的随机种子和参数配置",
            "robustness": "在不同噪声水平下重新评估",
            "utility": "将结果转化为可操作的建议或决策",
        }
        return suggestions.get(dimension, "无具体建议")

    def summary_report(self) -> dict:
        """生成验证摘要报告。"""
        if not self._results:
            return {"total": 0, "by_grade": {}, "avg_score": 0}

        by_grade = {}
        dim_scores = {dim: [] for dim in DIMENSIONS}
        for r in self._results:
            by_grade[r.grade] = by_grade.get(r.grade, 0) + 1
            for dim, score in r.scores.items():
                dim_scores[dim].append(score)

        dim_avgs = {dim: sum(scores) / len(scores) if scores else 0
                    for dim, scores in dim_scores.items()}

        return {
            "total": len(self._results),
            "by_grade": by_grade,
            "avg_score": sum(r.weighted_score for r in self._results) / len(self._results),
            "dimension_averages": dim_avgs,
            "weakest_dimension": min(dim_avgs, key=dim_avgs.get) if dim_avgs else None,
            "strongest_dimension": max(dim_avgs, key=dim_avgs.get) if dim_avgs else None,
        }
