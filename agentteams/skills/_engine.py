"""与 SwarmLabs 真实科学计算引擎的唯一桥接点。

这里刻意只做一件事：把仓库里**已经在生产运行**的验证引擎
（scripts/paper_validation_loop.py + scripts/virtual_experiment/parity_framework.py）
以模块形式引入，供 Skill 层调用。

为什么不重写一份：多 Agent 基础设施的价值在于"编排真实能力"，
如果 Skill 底下是玩具实现，整套闭环证据就是空的。本包跑出的每一个
rmse / 覆盖率 / 校准误差，都与线上公开仪表盘
（https://swarmlabs.tools/blog/validation-dashboard.html）同源同算法。
"""

from __future__ import annotations

import os
import sys

_PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # agentteams/
REPO_ROOT = os.path.dirname(_PKG_DIR)
SCRIPTS_DIR = os.path.join(REPO_ROOT, "scripts")

_engine = None
_offline = True


def set_offline(flag: bool):
    """默认离线：不去联网拉 arXiv 最新论文。

    理由是可复现性——评审在任何网络环境下重跑都应得到同样的数值结论。
    联动 arXiv 只影响"参考文献"展示，不影响任何指标。用 --online 打开。
    """
    global _offline
    _offline = bool(flag)
    if _engine is not None:
        _apply_offline(_engine)


def _apply_offline(mod):
    if _offline:
        mod.recent_arxiv = lambda *a, **k: []


def engine():
    global _engine
    if _engine is None:
        if SCRIPTS_DIR not in sys.path:
            sys.path.insert(0, SCRIPTS_DIR)
        import paper_validation_loop as pvl        # noqa: E402
        _apply_offline(pvl)
        _engine = pvl
    return _engine


def _all_scenarios(pvl=None):
    """全部可调度场景 = 跨域 + 52 微生物。

    2026-09-08 修复：此前只遍历 SCENARIOS（10 个跨域），导致 52 个微生物场景
    "在可信面里、却不在可调度目录里"——义务图因此覆盖不到微生物侧。
    """
    pvl = pvl or engine()
    return list(getattr(pvl, "ALL_SCENARIOS", None) or pvl.SCENARIOS)


def scenario_by_key(key: str):
    pvl = engine()
    for s in _all_scenarios(pvl):
        if s["key"] == key:
            return s
    raise KeyError(f"未知场景 «{key}»，共 {len(_all_scenarios(pvl))} 个可选")


def all_keys():
    return [s["key"] for s in _all_scenarios()]


def store_path(key: str) -> str:
    return engine()._store_path(key)
