"""Skill 注册表：发现、鉴权前置、清单导出。

Skill 是本项目对外复用的最小单位。注册表提供三件事：
1. 名称 → 实例 的解析（Worker 只认名字，不认实现，便于替换）；
2. `describe()` 导出结构化清单（可直接生成提交用的 Skill 清单文档）；
3. 启动时校验：配置里给角色授权的 Skill 必须真实存在，避免"纸面授权"。
"""

from __future__ import annotations

from typing import Dict, List

from ..core.contracts import Skill
from .science import ScenarioCatalog, VirtualExperimentValidate, ApplyCorrection
from .uq import CalibrationReport
from .active_learning import RankCandidates
from .reporting import RenderReport
from .labguard import LabGuard
from .local_model import LocalModelAdvisory


def default_registry() -> "SkillRegistry":
    reg = SkillRegistry()
    for s in (ScenarioCatalog(), VirtualExperimentValidate(), RankCandidates(),
              CalibrationReport(), ApplyCorrection(), RenderReport(), LabGuard(),
              LocalModelAdvisory()):
        reg.register(s)
    return reg


class SkillRegistry:
    def __init__(self):
        self._skills: Dict[str, Skill] = {}

    def register(self, skill: Skill):
        name = skill.spec.name
        if name in self._skills:
            raise ValueError(f"Skill 重名：{name}")
        self._skills[name] = skill

    def get(self, name: str) -> Skill:
        if name not in self._skills:
            raise KeyError(f"未注册的 Skill «{name}»，已注册：{sorted(self._skills)}")
        return self._skills[name]

    def names(self) -> List[str]:
        return sorted(self._skills)

    def describe(self) -> List[dict]:
        return [self._skills[n].spec.to_dict() for n in self.names()]

    def validate_roles(self, roles_cfg: dict) -> List[str]:
        """返回配置错误列表（授权了不存在的 Skill）。"""
        problems = []
        for role, conf in (roles_cfg or {}).items():
            for s in conf.get("allow_skills") or []:
                if s not in self._skills:
                    problems.append(f"角色 {role} 授权了不存在的 Skill «{s}»")
        return problems

    def to_markdown(self) -> str:
        lines = ["# SwarmLabs Skill 清单（由 registry.describe() 自动生成）", ""]
        for spec in self.describe():
            lines += [
                f"## {spec['name']}  `v{spec['version']}`",
                f"- **用途**：{spec['purpose']}",
                f"- **输入**：{spec['inputs']}",
                f"- **输出**：{spec['outputs']}",
                f"- **调用条件**：{spec['preconditions']}",
                f"- **依赖工具**：{', '.join(spec['depends_on_tools']) or '无（纯本地计算）'}",
                f"- **副作用/范围**：{spec['side_effect']} / {spec['scope']}",
                f"- **风险等级**：{spec['risk']}",
                f"- **失败处理**：{spec['failure_modes']}",
                f"- **复用价值**：{spec['reuse_note']}",
                "",
            ]
        return "\n".join(lines)
