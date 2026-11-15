"""跨场景正迁移硬基准（P1-E 验证）—— Cross-Physics Uplift 的诚实版。

真值：每个「场景」是同一类算子（高斯核卷积 u=∫K_w a）的不同参数配置，
核宽 w 决定场景差异。源场景 w∈相关区间；目标场景分两种：
  - 相关目标：w 仍在相关区间（迁移应有正收益）
  - 分布外(OOD)目标：w 远离相关区间（迁移应无收益 → 诚实负对照）

检验：预训练全局先验（跨源场景共享 latent + GP）后，目标场景仅用 k 个少样本
自适应，是否显著优于「从零」GP（只用这 k 个样本，无迁移）。并测「源场景越多→
迁移越强」，以及 OOD 下迁移增益消失（不夸大为万能迁移）。
"""
from __future__ import annotations
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from engine.meta_learner import CrossSceneMetaLearner


def smooth_a(rng, n_in=16):
    ks = np.arange(1, 4)
    cs = rng.standard_normal(3) * (1.0 / ks)
    ph = rng.uniform(0, 2 * np.pi, 3)
    def a(t):
        t = np.asarray(t, float)
        return sum(c * np.sin(k * np.pi * t + p) for c, k, p in zip(cs, ks, ph))
    return a


def scene_op(a_fn, x_grid, w, n_quad=48):
    """u(x)=∫ exp(-(x-s)^2/w) a(s) ds（高斯核卷积，核宽 w 为场景参数）。"""
    s = np.linspace(0, 1, n_quad)
    a_vals = np.array([a_fn(ss) for ss in s])
    ds = s[1] - s[0]
    U = []
    for x in x_grid:
        K = np.exp(-((x - s) ** 2) / w)
        U.append(float(K @ a_vals * ds))
    return np.array(U)


def make_scene(rng, w, n, n_in=16, n_out=32):
    g_in = np.linspace(0, 1, n_in)
    g_out = np.linspace(0, 1, n_out)
    A, U = [], []
    for _ in range(n):
        a_fn = smooth_a(rng, n_in)
        A.append([a_fn(t) for t in g_in])
        U.append(scene_op(a_fn, g_out, w))
    return np.array(A), np.array(U)


def rmse(pred, truth):
    return float(np.sqrt(np.mean((pred - truth) ** 2)))


def eval_target(learner, A_tgt, U_tgt, k, reps=5, seed=0):
    """目标场景 k 样本自适应 vs 从零 GP，平均 RMSE。"""
    rng = np.random.default_rng(seed)
    n = len(A_tgt)
    meta_errs, scratch_errs = [], []
    for r in range(reps):
        idx = rng.choice(n, size=k, replace=False)
        tr_A, tr_U = A_tgt[idx], U_tgt[idx]
        te_A, te_U = np.delete(A_tgt, idx, axis=0), np.delete(U_tgt, idx, axis=0)
        # 从零（无迁移）
        s_m, _ = learner.scratch_predict(tr_A, tr_U, te_A)
        scratch_errs.append(rmse(s_m, te_U))
        # 迁移（全局先验 + k 样本自适应）
        l2 = CrossSceneMetaLearner(n_latent=learner.n_latent, ls=learner.ls, noise=0.02)
        l2._amean = learner._amean
        l2._comps = learner._comps
        l2._k = learner._k
        l2.N_out = learner.N_out
        l2._global_gps = learner._global_gps
        l2._n_source = learner._n_source
        l2.adapt(tr_A, tr_U)
        m_m, _ = l2.predict(te_A)
        meta_errs.append(rmse(m_m, te_U))
    return float(np.mean(scratch_errs)), float(np.mean(meta_errs))


def main():
    N_IN, N_OUT = 16, 32
    N_SRC = 40
    K_SOURCE = 6
    N_TGT = 40
    related_ws = [0.18, 0.26, 0.34, 0.42]      # 相关区间（源场景也取自这附近）
    ood_w = 2.0                                  # 分布外

    rng = np.random.default_rng(7)
    # 源场景
    src_ws = [0.20, 0.24, 0.30, 0.36, 0.40, 0.44][:K_SOURCE]
    scenes = [make_scene(rng, w, N_SRC, N_IN, N_OUT) for w in src_ws]

    # 相关目标场景（3 个，取平均）
    rel_targets = [make_scene(rng, w, N_TGT, N_IN, N_OUT) for w in [0.22, 0.32, 0.38]]
    # OOD 目标场景（2 个）
    ood_targets = [make_scene(rng, ood_w, N_TGT, N_IN, N_OUT) for _ in range(2)]

    # 全局先验只拟合一次，所有目标场景复用（避免重复昂贵 auto-ls 搜索）
    global_learner = learner_fit(scenes)

    ks = [2, 4, 8, 16]
    rel_res, ood_res = {}, {}
    for k in ks:
        er_s, er_m = [], []
        for A_t, U_t in rel_targets:
            s, m = eval_target(global_learner, A_t, U_t, k)
            er_s.append(s); er_m.append(m)
        rel_res[k] = {"scratch_rmse": round(float(np.mean(er_s)), 4),
                      "meta_rmse": round(float(np.mean(er_m)), 4),
                      "transfer_gain": round(float(np.mean(er_s)) / float(np.mean(er_m)), 2)}
        eo_s, eo_m = [], []
        for A_t, U_t in ood_targets:
            s, m = eval_target(global_learner, A_t, U_t, k)
            eo_s.append(s); eo_m.append(m)
        ood_res[k] = {"scratch_rmse": round(float(np.mean(eo_s)), 4),
                      "meta_rmse": round(float(np.mean(eo_m)), 4),
                      "transfer_gain": round(float(np.mean(eo_s)) / float(np.mean(eo_m)), 2)}

    # 源场景越多 → 迁移越强（k=4 固定）
    ablation = {}
    for K in [2, 4, 6]:
        lrn = CrossSceneMetaLearner(n_latent=6, ls=0.6, noise=0.02, auto_ls=False)
        lrn.fit_sources(scenes[:K])
        s, m = eval_target(lrn, rel_targets[0][0], rel_targets[0][1], 4)
        ablation[K] = {"scratch_rmse": round(s, 4), "meta_rmse": round(m, 4),
                       "transfer_gain": round(s / m, 2)}

    print("=" * 64)
    print("跨场景正迁移 · Cross-Physics Uplift 诚实版硬基准")
    print("=" * 64)
    print(f"源场景={K_SOURCE} (w∈相关区间)  每场景 {N_SRC} 样本")
    print("-- 相关目标（迁移应有正收益） --")
    for k in ks:
        r = rel_res[k]
        print(f"  k={k:2d}  从零={r['scratch_rmse']:.4f}  迁移={r['meta_rmse']:.4f}  "
              f"增益={r['transfer_gain']}×")
    print("-- 分布外(OOD)目标（迁移应无收益 = 诚实负对照） --")
    for k in ks:
        r = ood_res[k]
        print(f"  k={k:2d}  从零={r['scratch_rmse']:.4f}  迁移={r['meta_rmse']:.4f}  "
              f"增益={r['transfer_gain']}×")
    print("-- 源场景越多 → 迁移越强（k=4 固定） --")
    for K in [2, 4, 6]:
        a = ablation[K]
        print(f"  K={K}  从零={a['scratch_rmse']:.4f}  迁移={a['meta_rmse']:.4f}  "
              f"增益={a['transfer_gain']}×")
    print("=" * 64)

    out = {
        "method": "跨场景共享 latent + 全局先验 GP + 目标残差自适应 (MAML-lite)",
        "n_source": K_SOURCE, "n_src_per_scene": N_SRC, "n_target": N_TGT,
        "related_ws": related_ws, "ood_w": ood_w,
        "related_target": rel_res, "ood_target": ood_res,
        "source_ablation_k4": ablation,
    }
    with open("data/meta_transfer_benchmark.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("已写出 data/meta_transfer_benchmark.json")


def learner_fit(scenes):
    lrn = CrossSceneMetaLearner(n_latent=6, ls=0.6, noise=0.02, auto_ls=False)
    lrn.fit_sources(scenes)
    return lrn


if __name__ == "__main__":
    main()
