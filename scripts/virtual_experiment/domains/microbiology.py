#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SwarmLabs · 微生物领域虚拟实验对等脚手架 (domains/microbiology.py)
===============================================================
落点：复用 parity_framework 的 run_parity_loop，把"虚拟实验对等现实"战略
接到用户真实微生物实验室。

本文件提供：
- monod_growth / inhibition_curve：微生物机理（确定性，L1 保真度）
- MicrobioLabOracle：真实实验室 oracle 的**接口桩**（你填数据入口）
- demo()：用 SyntheticOracle（机理+噪声）跑通闭环，证明 rmse/coverage 收敛
  （非真实实验室数据，仅验证框架；真实数据需接 MicrobioLabOracle）

运行：python scripts/virtual_experiment/domains/microbiology.py
"""
import os
import sys
import numpy as np

# 允许从 scripts/ 直接运行
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from virtual_experiment.parity_framework import (
    ExperimentSpace, GPSurrogate, SyntheticOracle, Calibrator, run_parity_loop,
)


# ---------- L1 机理（确定性） ----------
def monod_growth(cond):
    """Monod 增长：cond=[接种量 x0, 底物浓度 s0, 最大比速 mu_max, 半饱和 Ks]
    返回稳态生物量（简化稳态近似）。"""
    x0, s0, mu_max, Ks = [float(v) for v in cond]
    # 简化：平衡时生物量 ≈ 受底物限制的 log 增长幅度
    return float(x0 + mu_max / (mu_max + Ks) * s0 * 0.5)


def inhibition_curve(cond, abx_conc, ic50=0.5, hill=1.0):
    """抗生素抑制曲线：基础增长 × (1 - 抑制率)。
    cond=[x0, s0, mu_max, Ks]；abx_conc 抗生素浓度。
    返回在 abx_conc 下的相对生长量（0~1 区间映射）。"""
    base = monod_growth(cond)
    inhib = 1.0 / (1.0 + (abx_conc / ic50) ** hill)
    return float(base * inhib)


# ---------- 真实实验室 oracle（接口桩） ----------
class MicrobioLabOracle:
    """真实微生物实验室的数据入口。

    [WARN] 当前为桩：observe() 未实现。真实接入方式（三选一，由用户拍板）：
      A. 手动录入页：实验员在 SwarmLabs UI 填实测值 + 重复次数
      B. 仪器 CSV 导入：平板读数仪/酶标仪导出 → 解析 → 回填
      C. LIMS / 设备 API：自动推送 (cond, measurement, repeats)

    关键：必须返回 aleatoric_std（重复实验估计的偶然性标准差），
    否则 UQ 会过自信、把生物偶然性藏进误差棒。
    """

    def __init__(self, source="<未接入>"):
        self.source = source

    def observe(self, cond, abx_conc=None):
        raise NotImplementedError(
            "MicrobioLabOracle 未接入真实实验室数据入口。\n"
            "请实现 observe(cond, abx_conc) -> (measurement, aleatoric_std)：\n"
            "  - measurement : 真实实验测量值（生物量 OD / CFU / 荧光等）\n"
            "  - aleatoric_std: 重复实验估计的偶然性标准差（必填，UQ 分离用）\n"
            "数据入口三选一：手动录入页 / 仪器 CSV / LIMS API（见 docs/microbiology-parity-analysis.md §4）。"
        )


# ---------- 自测：用合成 oracle 跑通闭环 ----------
def demo():
    print("=== 微生物虚拟实验对等 · 自测（合成 oracle，非真实实验室）===")

    # 一维场景：固定其他参数，扫抗生素浓度下的抑制曲线
    def f(x):
        x = np.asarray(x, float).ravel()
        cond = [0.1, 2.0, 0.8, 0.3]  # x0, s0, mu_max, Ks
        return float(inhibition_curve(cond, abx_conc=float(x[0])))

    space = ExperimentSpace([(0.0, 2.0)])  # 抗生素浓度 0~2
    oracle = SyntheticOracle(f, noise=0.02)
    surrogate = GPSurrogate(lengthscale=0.35, signal_var=0.5, noise_var=0.02 ** 2)
    hist = run_parity_loop(space, oracle, surrogate=surrogate, n_seed=6, n_iter=14)
    print("iter | n_real |   rmse | coverage | cal_err")
    print("-" * 44)
    for h in hist:
        print(f"{h['iter']:>4} | {h['n_real']:>6} | {h['rmse']:>6} | {h['coverage']:>8} | {h['cal_err']:>6}")
    last = hist[-1]
    print(f"\n结论：合成 oracle 下，rmse 收敛至 {last['rmse']}，"
          f"覆盖率 {last['coverage']}（目标 0.95），校准误差 {last['cal_err']}。")
    print("→ 框架闭环成立。接 MicrobioLabOracle 即切换为真实实验室数据驱动。")


if __name__ == "__main__":
    demo()
