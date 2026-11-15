"""科学实验类 Skill：场景目录、虚拟实验验证、知识库修正（高风险写）。"""

from __future__ import annotations

from typing import Any, Dict

from ..core.contracts import Skill, SkillSpec
from . import _engine


class ScenarioCatalog(Skill):
    spec = SkillSpec(
        name="scenario.catalog",
        version="1.1.0",
        purpose="列出全部可验证的科学场景及其论文级 ground-truth 来源，供 Planner 选题。",
        inputs={"filter": "可选，场景 key 子集"},
        outputs={"scenarios": "[{key, domain, published, n_seed, source}]"},
        preconditions="无。纯读取引擎内置场景定义。",
        side_effect="read", risk="low", scope="run_local",
        failure_modes="场景定义缺失 → 抛 KeyError 并列出可选 key，不静默降级。",
        reuse_note="任何需要'先看有哪些实验可做'的 Agent 都能直接复用；新增场景只需在引擎侧注册。",
        depends_on_tools=("scripts/paper_validation_loop.py",),
    )

    def run(self, params: Dict[str, Any], ctx) -> Dict[str, Any]:
        pvl = _engine.engine()
        want = params.get("filter")
        out = []
        # 2026-09-08：目录改为覆盖 ALL_SCENARIOS（10 跨域 + 52 微生物）。
        # 微生物场景无 seed/grid 字段，n_seed 回退到 train（种子训练集）长度。
        for s in _engine._all_scenarios(pvl):
            if want and s["key"] not in want:
                continue
            seed = s.get("seed")
            tr = s.get("train")
            # 注意：train 是 ndarray，不能用 `or []`（真值歧义），必须显式判 None
            if seed is not None:
                n_seed = len(seed)
            elif tr is not None:
                n_seed = len(tr)
            else:
                n_seed = int(s.get("n_train") or 0)
            out.append({
                "key": s["key"],
                "domain": s["domain"],
                "published": s["published"],
                "n_seed": n_seed,
                "source": "microbiology_full" if s["key"].startswith("micro_") else "cross_domain",
            })
        return {"scenarios": out, "catalog_size": len(out)}


class VirtualExperimentValidate(Skill):
    spec = SkillSpec(
        name="virtual_experiment.validate",
        version="1.2.0",
        purpose="在指定科学场景上跑 GP 代理虚拟实验，输出 rmse / 95%CI 覆盖率 / 校准误差（带误差棒）。",
        inputs={"scenario": "场景 key"},
        outputs={"rmse_after": "float", "coverage_after": "float",
                 "cal_err_after": "float", "scale": "float", "n_points": "int"},
        preconditions="场景已注册；累积数据点 ≥ 种子点数。",
        side_effect="read", risk="low", scope="run_local",
        failure_modes="GP 矩阵近奇异 → 由引擎的 jitter 正则兜底；场景不存在 → KeyError 显式失败。",
        reuse_note="领域无关：换任何带 ground-truth 的场景即可复用，是整个验证闭环的核心度量单元。",
        depends_on_tools=("scripts/virtual_experiment/parity_framework.py", "numpy"),
    )

    def run(self, params: Dict[str, Any], ctx) -> Dict[str, Any]:
        pvl = _engine.engine()
        s = _engine.scenario_by_key(params["scenario"])
        # correct=False：本 Skill 只读不写。写入知识库是另一个高风险 Skill 的职责，
        # 两者分离是为了让"读"永远安全、"写"永远可审计。
        r = pvl.validate_scenario(s, correct=False, train_small=False)
        return r


class ApplyCorrection(Skill):
    spec = SkillSpec(
        name="experiment.apply_correction",
        version="1.1.0",
        purpose="把误差最大处的论文真实值补入跨天累积知识库并重拟合（主动学习修正）。",
        inputs={"scenario": "场景 key"},
        outputs={"correction_added": "int", "rmse_after": "float", "store": "被修改的文件路径"},
        preconditions="必须由 Verifier 在验证结论支持补点时发起，且通过人工审批闸门。",
        side_effect="write", risk="high", scope="shared_state",
        failure_modes="被策略拒绝 → 返回 blocked，任务降级但不中断；写入前自动快照，可一键回滚。",
        reuse_note="任何'主动学习把新观测并入训练集'的场景都可复用；写入范围由 scope 明确声明。",
        depends_on_tools=("scripts/paper_validation_loop.py", "data/validation_store/"),
    )

    def run(self, params: Dict[str, Any], ctx) -> Dict[str, Any]:
        pvl = _engine.engine()
        key = params["scenario"]
        s = _engine.scenario_by_key(key)
        store = _engine.store_path(key)
        # 写之前先快照：这是"可回滚"的物理保证，不是文档承诺。
        ref = ctx.policy.snapshot([store], actor="verifier", action=self.spec.name)
        r = pvl.validate_scenario(s, correct=True, train_small=False)
        ctx.tracer.audit("write", "verifier", self.spec.name, "executed",
                         {"scenario": key, "added": r["correction_added"],
                          "rmse_after": round(r["rmse_after"], 4)}, rollback_ref=ref)
        return {"correction_added": r["correction_added"],
                "rmse_after": r["rmse_after"],
                "coverage_after": r["coverage_after"],
                "store": store, "rollback_ref": ref}
