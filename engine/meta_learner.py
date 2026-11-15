"""跨场景正迁移 meta-learning（P1-E）—— Cross-Physics Uplift 的诚实版本。

为什么要它（硬实力缺口）：
  AU 声称「Cross-Physics Uplift / 跨场景正迁移」却零证据。本项目可真做：
  在多个**相关但不同**的场景（同一类算子的不同参数配置）上预训练一个
  **共享 latent + 全局先验**，新场景只需极少样本即可通过残差自适应对齐。

设计（诚实、可复现、CPU 可跑）：
  - 输入函数 a(·) 经 PCA 投影到低维 latent z(a)（跨场景共享投影）。
  - 全局先验 G_global：在所有源场景数据上拟合  z → u 场（逐输出坐标 GP），
    捕获场景间的共享结构。
  - 目标场景自适应：u ≈ G_global(z) + δ_scene(z)，δ 仅用目标场景的 k 个少样本拟合
    （残差 GP）。因为 G_global 已编码跨场景结构，δ 很小 → 极少样本即可对齐。
  - 诚实负对照：若目标场景与源**分布外**（结构迥异），残差 δ 不会变小，
    迁移增益趋近于 0——我们不声称「万能迁移」。

接口：
  ml = CrossSceneMetaLearner(n_latent=6)
  ml.fit_sources([(A0,U0),(A1,U1),...])   # 源场景列表，每项是 (n,N_in)/(n,N_out) 字段矩阵
  ml.adapt(A_tgt_k, U_tgt_k)               # 目标场景少样本自适应
  u_mean, u_std = ml.predict(A_q)           # 预测（全局先验 + 目标残差）
"""

from __future__ import annotations
import numpy as np

from .surrogate import GPSurrogate


class CrossSceneMetaLearner:
    def __init__(self, n_latent: int = 6, ls: float = 0.6, noise: float = 0.02, seed: int = 0,
                 auto_ls: bool = True):
        self.n_latent = int(n_latent)
        self.ls = float(ls)
        self.nv = float(noise) ** 2
        self.seed = int(seed)
        self.auto_ls = bool(auto_ls)
        self._adapted = False

    # ---- 共享 latent 投影（跨场景 PCA） ----------------------------------
    def _build_latent(self, A_all):
        A_all = np.atleast_2d(np.asarray(A_all, float))
        self._amean = A_all.mean(axis=0)
        Ac = A_all - self._amean
        U, S, Vt = np.linalg.svd(Ac, full_matrices=False)
        k = min(self.n_latent, Vt.shape[0])
        self._comps = Vt[:k].T
        self._k = k
        return Ac @ self._comps

    def _project(self, A):
        A = np.atleast_2d(np.asarray(A, float))
        return (A - self._amean) @ self._comps

    # ---- 预训练全局先验（所有源场景） ------------------------------------
    def fit_sources(self, scenes):
        """scenes: list of (A_i, U_i)，每项形状 (n_i, N_in)/(n_i, N_out)。"""
        if not scenes:
            raise ValueError("scenes 不能为空")
        N_out = scenes[0][1].shape[1]
        self.N_out = N_out
        A_all = np.vstack([np.atleast_2d(A) for A, _ in scenes])
        U_all = np.vstack([np.atleast_2d(U) for _, U in scenes])
        Z_all = self._build_latent(A_all)              # (N_total, k)

        self._global_gps = []
        for j in range(N_out):
            gp = GPSurrogate(lengthscale=self.ls, noise_var=self.nv, auto_ls=self.auto_ls)
            gp.fit(Z_all, U_all[:, j])
            self._global_gps.append(gp)
        self._n_source = len(scenes)
        return self

    # ---- 全局先验预测（无目标自适应时） ---------------------------------
    def global_predict(self, A_q):
        Z = self._project(A_q)
        mean = np.stack([gp.predict(Z)[0].ravel() for gp in self._global_gps], axis=1)
        std = np.stack([gp.predict(Z)[1].ravel() for gp in self._global_gps], axis=1)
        return mean, std

    # ---- 目标场景少样本自适应 -------------------------------------------
    def adapt(self, A_tgt, U_tgt):
        A_tgt = np.atleast_2d(np.asarray(A_tgt, float))
        U_tgt = np.atleast_2d(np.asarray(U_tgt, float))
        Z = self._project(A_tgt)
        g_mean, _ = self.global_predict(A_tgt)        # 全局先验在目标输入处
        R = U_tgt - g_mean                             # 残差场
        self._delta_gps = []
        for j in range(self.N_out):
            gp = GPSurrogate(lengthscale=self.ls, noise_var=self.nv, auto_ls=True)
            gp.fit(Z, R[:, j])
            self._delta_gps.append(gp)
        self._adapted = True
        return self

    # ---- 预测（全局先验 + 目标残差） ------------------------------------
    def predict(self, A_q):
        Z = self._project(A_q)
        g_mean, g_std = self.global_predict(A_q)
        if not self._adapted:
            return g_mean, g_std
        d_mean = np.stack([gp.predict(Z)[0].ravel() for gp in self._delta_gps], axis=1)
        d_std = np.stack([gp.predict(Z)[1].ravel() for gp in self._delta_gps], axis=1)
        return g_mean + d_mean, np.sqrt(g_std ** 2 + d_std ** 2)

    # ---- 仅用目标场景 k 样本「从零」GP（对照，不迁移） ------------------
    @staticmethod
    def scratch_predict(A_tgt_train, U_tgt_train, A_q, ls=0.6, noise=0.02):
        A_tgt_train = np.atleast_2d(np.asarray(A_tgt_train, float))
        U_tgt_train = np.atleast_2d(np.asarray(U_tgt_train, float))
        N_out = U_tgt_train.shape[1]
        mean = np.zeros((np.atleast_2d(np.asarray(A_q, float)).shape[0], N_out))
        std = np.zeros_like(mean)
        for j in range(N_out):
            gp = GPSurrogate(lengthscale=ls, noise_var=noise ** 2, auto_ls=True)
            gp.fit(A_tgt_train, U_tgt_train[:, j])
            m, s = gp.predict(A_q)
            mean[:, j] = m.ravel()
            std[:, j] = s.ravel()
        return mean, std
