"""多保真度融合硬基准（P1-D 验证）。

真值算子：非局部积分 u(x)=∫K(x,s)a(s)ds（u 依赖整个 a，逐点模型学不了）。
两种保真度 = 同一算子在两个积分离散化分辨率下的结果：
  - 低保真（LF）：粗积分离散（Nc=12 求积点）→ 便宜、有偏（丢高频）
  - 高保真（HF）：细积分离散（Nf=64 求积点）→ 昂贵、精确

检验：用大量 LF（200）+ 少量 HF（12）的 MF 融合，是否同时优于
  (a) 纯 LF（只有大量便宜数据，但有偏）
  (b) 纯 HF（只有 12 个昂贵点，方差大）
并估算「纯 HF 需要多少个昂贵点才能追平 MF」= 省下的 HF 评估数。
"""
from __future__ import annotations
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from engine.multifidelity import MultiFidelitySurrogate
from engine.surrogate import GPSurrogate


# ---- 真值算子（非局部，两种分辨率） -------------------------------------
def smooth_a(rng, n_in=16):
    """生成平滑随机输入函数 a(t)，连续可求任意点值。"""
    ks = np.arange(1, 4)
    cs = rng.standard_normal(3) * (1.0 / ks)
    ph = rng.uniform(0, 2 * np.pi, 3)
    def a(t):
        t = np.asarray(t, float)
        return sum(c * np.sin(k * np.pi * t + p) for c, k, p in zip(cs, ks, ph))
    return a


def operator(a_fn, x_grid, n_quad):
    """u(x)=∫_0^1 K(x,s)a(s)ds，K=exp(-|x-s|/0.3)，n_quad 求积点。"""
    s = np.linspace(0, 1, n_quad)
    a_vals = np.array([a_fn(ss) for ss in s])
    ds = s[1] - s[0]
    U = []
    for x in x_grid:
        K = np.exp(-np.abs(x - s) / 0.3)
        U.append(float(K @ a_vals * ds))
    return np.array(U)


def gen(n, rng, n_in=16, n_out=32, n_quad_lf=12, n_quad_hf=64):
    g_in = np.linspace(0, 1, n_in)
    g_out = np.linspace(0, 1, n_out)
    A, U_lf, U_hf = [], [], []
    for _ in range(n):
        a_fn = smooth_a(rng, n_in)
        A.append([a_fn(t) for t in g_in])
        U_lf.append(operator(a_fn, g_out, n_quad_lf))
        U_hf.append(operator(a_fn, g_out, n_quad_hf))
    return np.array(A), np.array(U_lf), np.array(U_hf)


def rmse(pred, truth):
    return float(np.sqrt(np.mean((pred - truth) ** 2)))


def main():
    rng = np.random.default_rng(42)
    N_IN, N_OUT = 16, 32
    N_LF, N_HF = 200, 12
    N_HF_POOL = 500          # 更大的 HF 池，用于测纯 HF 的盈亏平衡
    N_TEST = 30

    # LF 与测试集
    A_lf, U_lf, _ = gen(N_LF, rng, N_IN, N_OUT)
    A_test, _, U_hf_test = gen(N_TEST, rng, N_IN, N_OUT)
    # HF 池：MF 只用前 N_HF=12 个，纯 HF 基线可用到整池查盈亏平衡
    A_hf_pool, _, U_hf_pool = gen(N_HF_POOL, rng, N_IN, N_OUT)
    A_hf, U_hf = A_hf_pool[:N_HF], U_hf_pool[:N_HF]

    # ---- MF 融合 ----
    mf = MultiFidelitySurrogate(n_latent=6, ls=0.6, noise=0.02)
    mf.fit(A_lf, U_lf, A_hf, U_hf)
    mf_pred, _ = mf.predict(A_test)
    mf_rmse = rmse(mf_pred, U_hf_test)

    # ---- 基线 (a) 纯 LF ----
    lf_pred = np.zeros_like(U_hf_test)
    for j in range(N_OUT):
        gp = GPSurrogate(lengthscale=0.6, noise_var=0.02 ** 2, auto_ls=False)
        gp.fit(A_lf, U_lf[:, j])
        lf_pred[:, j] = gp.predict(A_test)[0].ravel()
    lf_rmse = rmse(lf_pred, U_hf_test)

    # ---- 基线 (b) 纯 HF（仅 12 个昂贵点） ----
    hf_pred = np.zeros_like(U_hf_test)
    for j in range(N_OUT):
        gp = GPSurrogate(lengthscale=0.6, noise_var=0.02 ** 2, auto_ls=False)
        gp.fit(A_hf, U_hf[:, j])
        hf_pred[:, j] = gp.predict(A_test)[0].ravel()
    hf_rmse = rmse(hf_pred, U_hf_test)

    # ---- 盈亏平衡：纯 HF 需多少个昂贵点才追平 MF ----
    # 公平设定：MF 用 12 HF + 200 LF；纯 HF 用 k 个 HF（不用 LF），k 逐步增大。
    breakeven = None
    n_hf_sweep = [12, 25, 50, 100, 150, 200, 300, 400, 500]
    sweep = {}
    for nh in n_hf_sweep:
        p = np.zeros_like(U_hf_test)
        for j in range(N_OUT):
            gp = GPSurrogate(lengthscale=0.6, noise_var=0.02 ** 2, auto_ls=False)
            gp.fit(A_hf_pool[:nh], U_hf_pool[:nh, j])
            p[:, j] = gp.predict(A_test)[0].ravel()
        v = rmse(p, U_hf_test)
        sweep[nh] = round(v, 4)
        if breakeven is None and v <= mf_rmse:
            breakeven = nh

    imp_vs_lf = round(lf_rmse / mf_rmse, 2)
    imp_vs_hf = round(hf_rmse / mf_rmse, 2)
    max_sweep = max(n_hf_sweep)
    if breakeven is None:
        breakeven = f">{max_sweep}"
        hf_saved = f">{max_sweep - N_HF}"
    else:
        hf_saved = breakeven - N_HF

    print("=" * 64)
    print("多保真度融合 · Kennedy-O'Hagan 自回归 cokriging 硬基准")
    print("=" * 64)
    print(f"LF 样本={N_LF}(便宜)  HF 样本={N_HF}(昂贵)  测试={N_TEST}")
    print(f"  MF  (LF+HF 融合)      RMSE = {mf_rmse:.4f}")
    print(f"  纯 LF (仅便宜数据)    RMSE = {lf_rmse:.4f}  → MF 提升 {imp_vs_lf}×")
    print(f"  纯 HF (仅 {N_HF} 昂贵点)  RMSE = {hf_rmse:.4f}  → MF 提升 {imp_vs_hf}×")
    print(f"  纯 HF 盈亏平衡扫描: {sweep}")
    print(f"  → 纯 HF 需约 {breakeven} 个昂贵点才追平 MF（省下 ~{hf_saved} 次昂贵评估）")
    print("=" * 64)

    out = {
        "method": "Kennedy-O'Hagan autoregressive cokriging (latent PCA + 残差GP)",
        "n_lf": N_LF, "n_hf": N_HF, "n_test": N_TEST,
        "mf_rmse": round(mf_rmse, 4),
        "pure_lf_rmse": round(lf_rmse, 4),
        "pure_hf_rmse": round(hf_rmse, 4),
        "improvement_vs_lf": imp_vs_lf,
        "improvement_vs_hf": imp_vs_hf,
        "pure_hf_breakeven_sweep": {str(k): v for k, v in sweep.items()},
        "hf_points_to_match_mf": breakeven,
        "hf_evals_saved": hf_saved,
    }
    with open("data/multifidelity_benchmark.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("已写出 data/multifidelity_benchmark.json")


if __name__ == "__main__":
    main()
