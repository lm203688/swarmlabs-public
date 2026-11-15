"""保真度阶梯 (Fidelity Ladder) —— 虚拟实验如何一步步逼近现实实验。

每一级都标注：算力的"现在能跑" vs "待算力上规模"，便于融资叙事与路线规划。
"""

from __future__ import annotations

# 阶梯：从廉价代理 → 物理约束代理 → 神经算子 → 校准数字孪生（对等现实）
FIDELITY_TIERS = [
    {
        "tier": 1,
        "name": "GP / SINDy 代理",
        "desc": "高斯过程或稀疏辨识代理，预测带 UQ；最快、最廉价。",
        "compute": "CPU / numpy，现在即可跑",
        "status": "已实现 (GPSurrogate)",
        "readiness": "now",
    },
    {
        "tier": 2,
        "name": "PIML / PINN 物理约束代理",
        "desc": "把控制方程残差作为无监督损失，预测满足守恒律；数据稀缺也鲁棒。",
        "compute": "单卡 GPU 训练（数小时级）",
        "status": "框架桩就绪 (NeuralOperatorSurrogate 同接口)",
        "readiness": "scale-when-funded",
    },
    {
        "tier": 3,
        "name": "神经算子 (Fourier / DeepONet)",
        "desc": "直接学习算子映射，预测高维场（如流场）；可参数化、实时推理。",
        "compute": "多卡 GPU + 大规模配对数据",
        "status": "接口桩 (NeuralOperatorSurrogate)",
        "readiness": "scale-when-funded",
    },
    {
        "tier": 4,
        "name": "校准数字孪生 (对等现实)",
        "desc": "虚拟↔真实双向更新 + 持续 UQ；主动学习挑真实实验校准自己。",
        "compute": "常驻算力 + RemoteLab/HPC 对接 (RemoteLabOracle)",
        "status": "闭环逻辑已实现 (run_parity_loop)；真实设备对接为桩",
        "readiness": "scale-when-funded",
    },
]


def recommend_tier(budget_compute="none"):
    """按当前算力预算推荐起点层级（用于框架优先、上规模在后的策略）。"""
    if budget_compute in ("none", "cpu"):
        return FIDELITY_TIERS[0]
    if budget_compute in ("single-gpu",):
        return FIDELITY_TIERS[1]
    if budget_compute in ("multi-gpu", "hpc"):
        return FIDELITY_TIERS[2]
    return FIDELITY_TIERS[-1]


def summary():
    return "\n".join(
        f"T{t['tier']} {t['name']} [{t['readiness']}] {t['status']}" for t in FIDELITY_TIERS
    )


if __name__ == "__main__":
    print(summary())
