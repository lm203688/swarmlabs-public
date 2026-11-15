"""SwarmLabs 虚拟实验对等现实框架 (virtual_experiment)。

核心哲学（见 docs/virtual-experiment-parity.md）：
  现实实验本质上也是"编码世界"——物理规律 = 微分方程，随机动力学 = 噪声 + 采样。
  虚拟实验只是主动削减干扰项、用数学/算演加速收敛。
  本框架目标是让虚拟实验逐步达到"与现实实验同等可信"的程度，关键在两点：
    1) 不确定性量化(UQ) 是一等公民——真实实验有误差棒，虚拟实验也必须有；
    2) 主动学习闭环——用虚拟实验挑选"信息量最大的真实实验"来校准自己。

算力策略：轻量组件（GP 代理、主动学习、校准）现在即可跑；
  算力重的组件（PINN/神经算子训练、RemoteLab 对接、GPU 集群）只留接口与桩，
  框架就绪，待融资或算力充足时上规模。
"""

from .parity_framework import (
    ExperimentSpace,
    GPSurrogate,
    NeuralOperatorSurrogate,
    SyntheticOracle,
    RemoteLabOracle,
    Calibrator,
    ActiveLearner,
    run_parity_loop,
)
from .fidelity_ladder import FIDELITY_TIERS, recommend_tier

__all__ = [
    "ExperimentSpace",
    "GPSurrogate",
    "NeuralOperatorSurrogate",
    "SyntheticOracle",
    "RemoteLabOracle",
    "Calibrator",
    "ActiveLearner",
    "run_parity_loop",
    "FIDELITY_TIERS",
    "recommend_tier",
]
