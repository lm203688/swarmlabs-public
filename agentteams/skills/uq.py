"""不确定性量化（UQ）判读 Skill。

这是 SwarmLabs 与"只给点预测"的同类系统的分水岭：没有误差棒的虚拟实验
不能被当作实验证据。本 Skill 把原始指标翻译成可判定的成熟度与人类可读结论。
"""

from __future__ import annotations

from typing import Any, Dict

from ..core.contracts import Skill, SkillSpec
from . import _engine


class CalibrationReport(Skill):
    spec = SkillSpec(
        name="uq.calibration_report",
        version="1.1.0",
        purpose="把 rmse/覆盖率/校准误差判读为成熟度（已收敛/收敛中/需数据校准）与结构化结论。",
        inputs={"validation": "virtual_experiment.validate 的输出", "thresholds": "判定阈值"},
        outputs={"maturity": "str", "rel": "float", "verdict": "str", "needs_correction": "bool"},
        preconditions="必须先有一次 validate 的输出；不接受人工填写的指标。",
        side_effect="read", risk="low", scope="run_local",
        failure_modes="缺字段 → 显式 KeyError；阈值缺省时回落到与线上仪表盘一致的默认值。",
        reuse_note="判定逻辑与公开仪表盘同源，保证'团队内部结论'与'对外展示'不会打架。",
        depends_on_tools=(),
    )

    DEFAULT_TH = {"rel_converged": 0.05, "cov_min": 0.8, "cal_max": 0.08,
                  "cov_bad": 0.7, "rel_bad": 0.2}

    def run(self, params: Dict[str, Any], ctx) -> Dict[str, Any]:
        v = params["validation"]
        th = {**self.DEFAULT_TH, **(params.get("thresholds") or {})}
        scale = max(float(v.get("scale", 1.0)), 1e-9)
        rel = float(v["rmse_after"]) / scale
        cov = float(v["coverage_after"])
        cal = float(v["cal_err_after"])

        if rel <= th["rel_converged"] and th["cov_min"] <= cov <= 1.0 and cal <= th["cal_max"]:
            maturity = "已收敛"
        elif cov < th["cov_bad"] or rel > th["rel_bad"]:
            maturity = "需数据/校准"
        else:
            maturity = "收敛中"

        reasons = []
        if rel > th["rel_converged"]:
            reasons.append(f"相对误差 {rel:.1%} 高于收敛阈值 {th['rel_converged']:.0%}")
        if cov < th["cov_min"]:
            reasons.append(f"95%CI 覆盖率 {cov:.0%} 偏低（UQ 过度自信）")
        if cov > 1.0:
            reasons.append("覆盖率异常 >100%，检查噪声地板是否虚胖")
        if cal > th["cal_max"]:
            reasons.append(f"校准误差 {cal:.3f} 超阈，可能存在系统性偏移")
        verdict = "指标全绿，可升保真度或作跨域锚点。" if not reasons else "；".join(reasons)

        # 新实验方向建议由引擎统一产出，保证与线上仪表盘措辞一致
        try:
            suggestions = _engine.engine().suggest_next_experiments(v)
        except Exception as e:                       # noqa: BLE001
            suggestions = [f"（建议生成失败，不影响判定：{type(e).__name__}）"]

        return {
            "maturity": maturity,
            "suggestions": suggestions,
            "rel": round(rel, 4),
            "coverage": round(cov, 3),
            "cal_err": round(cal, 3),
            "verdict": verdict,
            # 只有"还没收敛"才值得动知识库——已收敛还补点是浪费真实数据预算
            "needs_correction": maturity != "已收敛",
        }
