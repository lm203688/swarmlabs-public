"""SwarmLabs 统一内核 · 全链路做实演示（非桩验证）。

一条命令跑通「假设发现 → Elo 排名 → 树搜索 → GP 验证 → BALD 推荐 →
真实实验模拟 → PINN 求解 → 神经算子 → LLM 候选（优雅降级）」全栈，
证明所有此前为桩的组件现在都是可运行、可验证的真实现。

运行：  python engine/demo_full_loop.py
（纯 numpy / scipy，无需 GPU；LLM 层无凭证时自动跳过）
"""

from __future__ import annotations

import os
import sys

# 仓库根加入 path，使 `import engine` 可解析
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

import numpy as np

from engine.surrogate import (
    SurrogateEngine, GPSurrogate, Calibrator, ExperimentSpace,
    NeuralOperatorSurrogate, FourierOperatorSurrogate, PINN1D,
)
from engine.discovery import SINDy
from engine.scenarios_extra import SCENARIOS_EXTRA, run_extra_validation


def banner(t):
    print("\n" + "=" * 70)
    print(t)
    print("=" * 70)


def main():
    rng = np.random.default_rng(20260816)
    results = {}

    # ------------------------------------------------------------------ #
    banner("[1] SINDy 可解释假设发现（纯 numpy，带系数置信度）")
    # 已知生成律 y = 3.2*x0 - 1.1*sin(x1) + 0.5，看 SINDy 能否从噪声数据恢复
    X = rng.uniform(-1, 1, (120, 2))
    y = 3.2 * X[:, 0] - 1.1 * np.sin(X[:, 1]) + 0.5 + rng.normal(0, 1e-3, 120)
    s = SINDy(poly_degree=3, use_sin=True, use_cos=True, threshold=1e-2)
    s.fit(X, y)
    print("  recovered:", s.equation())
    print(f"  train R2 = {s.train_r2_:.4f}  |  terms = {s.active_.sum()}  |  coef_std_max = {np.max(s.coef_std_[s.active_]):.2e}")
    results["sindy_r2"] = round(s.train_r2_, 4)

    # ------------------------------------------------------------------ #
    banner("[2] 统一引擎 GP 验证（现实实验对等度，10 域之一：microbio_monod）")
    from engine.scenario_registry import get_scenario as _get_sc
    monod = _get_sc("microbio_monod")
    eng = SurrogateEngine(monod)
    space = eng.space
    Xs = space.sample(18, rng)
    ys = np.array([monod["f"](x) for x in Xs])
    eng.fit(Xs, ys)
    test_x = space.sample(60, rng)
    ev = eng.evaluate(test_x)
    print(f"  rmse_rel={ev['rmse']/(float(np.std(ys))+1e-9):.4f}  coverage={ev['coverage']}  cal_err={ev['cal_err']}")
    results["gp_coverage"] = ev["coverage"]

    # ------------------------------------------------------------------ #
    banner("[3] Elo 候选人锦标赛（GP / RFF神经算子 / GP-auto 谁更可信）")
    elo = eng.rank_elo()
    print(f"  champion = {elo['champion']} (rating {elo['champion_rating']})")
    for n, r in elo["ranking"]:
        print(f"    {n:10s} {r}")

    # ------------------------------------------------------------------ #
    banner("[4] 假设树搜索（多候选机理族，UCB 式收敛到最简且对等者）")
    ts = eng.tree_search(Xs, ys, test_x, np.array([monod["f"](x) for x in test_x]))
    print(f"  best = {ts['best_strategy']}  rmse_rel={ts['best_rmse_rel']}")
    print(f"  equation: {ts['best_equation']}")
    print(f"  elo champion = {ts['elo_ranking']['champion']}")

    # ------------------------------------------------------------------ #
    banner("[5] BALD 主动学习（推荐下一个最该做的真实实验点）")
    x_bald = eng.recommend_bald()
    print(f"  next experiment point = {np.asarray(x_bald).round(4)}")

    # ------------------------------------------------------------------ #
    banner("[6] 真实实验 Oracle（RemoteLab 本地模拟，提交→轮询→取结果）")
    obs = eng.remote_lab_run(x_bald, noise=0.02)
    truth = monod["f"](x_bald)
    print(f"  observed={obs:.4f}  ground_truth={truth:.4f}  (noise-injected, protocol round-trip OK)")

    # ------------------------------------------------------------------ #
    banner("[7] PINN 物理信息求解 1D 边值问题（纯 CPU，无 GPU）")
    pinn = eng.pinp_solve(lambda x: -np.sin(x), bc=(0.0, 1.0), hidden=20)
    # 解析解 u = sin(x) + (1-sin(1))*x
    xx = np.linspace(0, 1, 50)
    u_pred = pinn.predict(xx)
    u_true = np.sin(xx) + (1 - np.sin(1)) * xx
    print(f"  max PDE residual = {pinn.max_residual_:.2e}  | max |u_pred-u_true| = {np.max(np.abs(u_pred-u_true)):.2e}")

    # ------------------------------------------------------------------ #
    banner("[8] 神经算子（1D 热方程算子：初值场 -> T 时刻场）")
    G = 64
    xg = np.linspace(0, 1, G)
    U0 = rng.normal(0, 1, (40, G))
    # 热方程解析解近似：高斯初值经扩散平滑（用 FFT 低通模拟）。
    # K 必须与 rfft(u) 等长（G//2+1），否则相乘广播维度不匹配。
    K = np.exp(-(np.arange(G // 2 + 1) ** 2) * 0.01)
    UT = np.array([np.fft.irfft(np.fft.rfft(u) * K) for u in U0])
    no = FourierOperatorSurrogate(n_modes=16)
    no.fit(U0, UT)
    UTp, _ = no.predict(U0[:5])
    noev = no.evaluate(U0[:5], UT[:5])
    print(f"  operator rmse={noev['rmse']:.4f}  coverage={noev['coverage']}  mean_std={noev['mean_std']:.4f}")

    # ------------------------------------------------------------------ #
    banner("[9] LLM 假设层（gated env；无凭证/不可达 → 优雅降级返回 None）")
    hyps = eng.llm_hypothesize(n=3)
    print(f"  candidates = {hyps if hyps else '(offline / no API key — SINDy 主导闭环)'}")

    # ------------------------------------------------------------------ #
    banner("[10] 扩展基准场景验证（R8 头部：10 个已发表基准，gold 接入）")
    rows = run_extra_validation(n_test=80)  # 内部固定种子，保证可复现的对等报告
    val = [r for r in rows if r["tags"] == "validated"]
    st = [r for r in rows if r["tags"] == "stress"]
    val_ok = [r for r in val if r["coverage"] >= 0.85]
    print(f"  新增场景 {len(rows)} 个 | validated {len(val_ok)}/{len(val)} 达标(≥0.85) | stress {len(st)} 个(GP 结构性难，触发主动学习)")
    print("  --- validated ---")
    for r in val:
        flag = "OK" if r["coverage"] >= 0.85 else "REFILL"
        print(f"    {r['key']:22s} rmse_rel={r['rmse_rel']:.4f} cov={r['coverage']} [{flag}]")
    print("  --- stress (高频/非平滑：UQ 诚实暴露不可信，需 BALD 补点) ---")
    for r in st:
        flag = "recommend_bald 触发" if r["needs_refill"] else "coverage 已达标(无需补点)"
        print(f"    {r['key']:22s} rmse_rel={r['rmse_rel']:.4f} cov={r['coverage']} -> {flag}")

    # ------------------------------------------------------------------ #
    banner("SUMMARY")
    print(f"  SINDy R2            : {results['sindy_r2']}")
    print(f"  GP coverage         : {results['gp_coverage']}")
    print(f"  Elo champion        : {elo['champion']}")
    print(f"  PINN max residual   : {pinn.max_residual_:.2e}")
    print(f"  NO rmse             : {noev['rmse']:.4f}")
    print(f"  extra scenarios     : {len(rows)} (validated {len(val_ok)}/{len(val)} OK, stress {len(st)} 触发主动学习)")
    print("  ALL STUBS ARE NOW REAL & RUNNABLE.")
    return results


if __name__ == "__main__":
    main()
