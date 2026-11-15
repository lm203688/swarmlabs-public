"""Skill 层：可复用、自描述、带安全边界声明的能力单元。"""

from .labguard import LabGuard  # noqa: F401  (labguard.gate 三态门禁)

__all__ = ["LabGuard"]
