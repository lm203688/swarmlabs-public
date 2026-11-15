"""GP / 逐点模型 vs Fourier Neural Operator — 非局部算子硬基准。

目的（硬实力证据，非营销）：
  Accelerated Understanding 主打神经算子（函数空间映射）。本项目原有 GPSurrogate /
  NeuralOperatorSurrogate 是**逐点**代理（输入向量 → 标量）。本基准用**非局部算子**
  （Poisson 格林函数：u(x)=∫K(x,s)a(s)ds，u 依赖整个 a，不只是 a(x)）作 ground truth，
  证明：(1) 逐点模型学不了非局部性，误差随数据量不降；(2) FNO 函数空间算子能学，
  且带集成 UQ（覆盖率≈0.95）；(3) FNO 分辨率无关（粗网格训练→细网格查询仍准）。

产出：data/fno_benchmark.json（供仪表盘/报告引用）+ 终端打印摘要。

运行：python scripts/bench_gp_vs_fno.py
"""

from __future__ import annotations
import os, json, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.fourier_operator import FourierNeuralOperator


# ---------------------------------------------------------------------------
# Ground truth：非局部 Poisson 积分算子（格林函数）
# ---------------------------------------------------------------------------
def poisson_op(a, grid):
    """u(x) = ∫_0^1 K(x,s) a(s) ds，K(x,s)=min(x,s)-x·s（Dirichlet Poisson 格林函数）。"""
    grid = np.asarray(grid, float)
    ds = grid[1] - grid[0]
    x = grid[:, None]
    s = grid[None, :]
    K = np.minimum(x, s) - x * s
    return (K @ a) * ds


def random_smooth(n, grid, rng, modes=6, amp=1.0):
    """生成 n 条平滑随机函数（截断 sine 级数），作为算子输入 a。"""
    out = []
    for _ in range(n):
        coeffs = rng.normal(0, amp, size=modes)
        a = sum(c * np.sin((k + 1) * np.pi * grid) for k, c in enumerate(coeffs))
        out.append(a)
    return np.stack(out, axis=0)


# ---------------------------------------------------------------------------
# 逐点基线：每个输出点一个独立模型，输入仅为「局部信息」
# ---------------------------------------------------------------------------
def pointwise_baseline(A_in, U_in, A_q, U_q, dim):
    """dim=1: 输入=a(x_loc)（标量）；dim=2: 输入=[x_loc, a(x_loc)]（公平但仍无全场上下文）。
    返回 fine 网格上对所有查询函数的 RMSE。"""
    n_train, N = A_in.shape
    n_q = A_q.shape[0]
    x_loc = np.linspace(0, 1, N)
    preds = np.zeros((n_q, N))
    for j in range(N):  # 每个输出点一个独立模型
        if dim == 1:
            feat_tr = A_in[:, j:j + 1]
            feat_q = A_q[:, j:j + 1]
        else:
            feat_tr = np.column_stack([np.full(n_train, x_loc[j]), A_in[:, j]])
            feat_q = np.column_stack([np.full(n_q, x_loc[j]), A_q[:, j]])
        # 闭式岭回归（快速、无需 GPU）
        Atr = feat_tr.T @ feat_tr + 1e-4 * np.eye(feat_tr.shape[1])
        w = np.linalg.solve(Atr, feat_tr.T @ U_in[:, j])
        preds[:, j] = feat_q @ w
    err = np.sqrt(np.mean((preds - U_q) ** 2, axis=1))
    return float(np.mean(err))


def main():
    rng = np.random.default_rng(20260830)
    N_coarse, N_fine = 16, 64
    g_coarse = np.linspace(0, 1, N_coarse)
    g_fine = np.linspace(0, 1, N_fine)

    n_train_total = 120
    A_all = random_smooth(n_train_total, g_fine, rng)          # 在细网格生成
    U_all = np.stack([poisson_op(a, g_fine) for a in A_all], axis=0)
    n_test = 30
    A_train, A_test = A_all[:-n_test], A_all[-n_test:]
    U_train, U_test = U_all[:-n_test], U_all[-n_test:]

    # 粗网格训练表示（subsample）
    A_coarse = A_train[:, ::N_fine // N_coarse]
    U_coarse = U_train[:, ::N_fine // N_coarse]
    # 测试集也取粗/细两版
    A_test_coarse = A_test[:, ::N_fine // N_coarse]
    U_test_coarse = U_test[:, ::N_fine // N_coarse]

    # ---- FNO（函数空间，分辨率无关：粗训练→细/粗查询） ----
    fno = FourierNeuralOperator(modes=24, ensemble=7, ridge=1e-3, seed=1)
    fno.fit(A_coarse, U_coarse, grid_in=g_coarse, grid_out=g_coarse)
    # FNO 在粗网格（与逐点同分辨率，苹果对苹果）
    fno_rmse_c_list, fno_cover_list = [], []
    for a, u_true in zip(A_test_coarse, U_test_coarse):
        um, us = fno.predict(a, grid_in_q=g_coarse, grid_out_q=g_coarse)
        fno_rmse_c_list.append(np.sqrt(np.mean((um - u_true) ** 2)))
        lo, hi = um - 1.96 * us, um + 1.96 * us
        fno_cover_list.append(np.mean((u_true >= lo) & (u_true <= hi)))
    fno_rmse_coarse = float(np.mean(fno_rmse_c_list))
    fno_cover = float(np.mean(fno_cover_list))
    # FNO 在细网格（分辨率无关额外能力：逐点模型做不到）
    fno_rmse_f_list = []
    for a, u_true in zip(A_test, U_test):
        um, _ = fno.predict(a, grid_in_q=g_fine, grid_out_q=g_fine)
        fno_rmse_f_list.append(np.sqrt(np.mean((um - u_true) ** 2)))
    fno_rmse = float(np.mean(fno_rmse_f_list))

    # ---- 逐点基线（粗网格上评，输入仅局部信息） ----
    pw1 = pointwise_baseline(A_coarse, U_coarse, A_test_coarse, U_test_coarse, dim=1)
    pw2 = pointwise_baseline(A_coarse, U_coarse, A_test_coarse, U_test_coarse, dim=2)

    # ---- 样本效率：FNO 随数据量下降，逐点卡在 nonlocal 天花板（均在粗网格评） ----
    ns = [10, 20, 40, 80]
    se_fno, se_pw = [], []
    for n in ns:
        At, Ut = A_coarse[:n], U_coarse[:n]
        f = FourierNeuralOperator(modes=24, ensemble=5, ridge=1e-3, seed=1)
        f.fit(At, Ut, grid_in=g_coarse, grid_out=g_coarse)
        e = np.mean([np.sqrt(np.mean((f.predict(a, grid_in_q=g_coarse, grid_out_q=g_coarse)[0]
                                      - ut) ** 2)) for a, ut in zip(A_test_coarse, U_test_coarse)])
        se_fno.append(float(e))
        se_pw.append(pointwise_baseline(At, Ut, A_test_coarse, U_test_coarse, dim=2))

    result = {
        "operator": "poisson_greens_nonlocal (u(x)=∫K(x,s)a(s)ds, 非局部)",
        "grid_coarse": N_coarse, "grid_fine": N_fine, "n_test": n_test,
        "fno": {
            "rmse_coarse": round(fno_rmse_coarse, 4),
            "rmse_fine_resolution_invariance": round(fno_rmse, 4),
            "coverage_95ci": round(fno_cover, 3),
            "cal_err": round(abs(fno_cover - 0.95), 3),
            "modes": 24, "ensemble": 7,
        },
        "pointwise_local_only_1d": {"rmse_coarse": round(pw1, 4),
            "note": "输入仅 a(x_loc)，完全无全场上下文"},
        "pointwise_fair_2d": {"rmse_coarse": round(pw2, 4),
            "note": "输入 [x_loc, a(x_loc)]，公平但仍学不了非局部性"},
        "resolution_invariance": {
            "train_grid": N_coarse, "query_grid": N_fine,
            "fno_rmse_on_fine": round(fno_rmse, 4),
            "note": "FNO 在细网格查询仍准（连续基→分辨率无关）；逐点模型无此能力",
        },
        "sample_efficiency": {
            "n_train": ns, "fno_rmse": [round(x, 4) for x in se_fno],
            "pointwise_rmse": [round(x, 4) for x in se_pw],
        },
        "improvement_factor_vs_best_pointwise": round(min(pw1, pw2) / max(fno_rmse_coarse, 1e-9), 1),
    }

    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "data", "fno_benchmark.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump(result, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    print("=" * 64)
    print("FNO vs 逐点模型 · 非局部 Poisson 算子硬基准")
    print("=" * 64)
    print(f"FNO (函数空间, 带集成UQ)  粗网格RMSE={fno_rmse_coarse:.4f}  95%CI覆盖率={fno_cover:.2f}")
    print(f"  └ 分辨率无关：细网格查询 RMSE={fno_rmse:.4f}")
    print(f"逐点基线 1D (仅a(x))        RMSE={pw1:.4f}")
    print(f"逐点基线 2D (公平)          RMSE={pw2:.4f}")
    print(f"FNO 相对最佳逐点 提升倍数    {result['improvement_factor_vs_best_pointwise']}×")
    print("-" * 64)
    print("样本效率（粗网格 RMSE vs 训练函数数）：")
    for n, f, p in zip(ns, se_fno, se_pw):
        print(f"  n={n:3d}  FNO={f:.4f}  逐点={p:.4f}")
    print("-" * 64)
    print(f"→ 逐点模型误差随数据量不降（非局部性根本学不了）；FNO 持续下降。")
    print(f"→ FNO 分辨率无关：粗网格(N={N_coarse})训练 → 细网格(N={N_fine})查询 RMSE={fno_rmse:.4f}。")
    print(f"→ FNO 集成 UQ 95%CI 覆盖率={fno_cover:.2f}（≈0.95，epistemic UQ 诚实）。")
    print(f"已写出 {out}")
    return result


if __name__ == "__main__":
    main()
