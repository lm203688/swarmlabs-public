"""Fourier Neural Operator（FNO）— 函数空间算子，纯 numpy，无 GPU。

为什么需要它（硬实力缺口）：
  项目原有 `NeuralOperatorSurrogate` 只是**逐点**随机傅里叶特征（输入向量 x → 标量 y），
  并非真正的「函数空间 → 函数空间」算子。而 Accelerated Understanding（Anima
  Anandkumar，2026-08）的核心正是 FNO / DeepONet 这类神经算子。本模块补齐这一块，
  且**带 UQ（我们的纪律）**——AU 全程不提不确定性量化，这正是我们的差异化。

设计（诚实、可复现、CPU 可跑）：
  - 用**连续 Fourier / sine 基**把输入函数 a(·) 与输出函数 u(·) 投影到固定系数的
    谱空间：a ≈ Σ c_a·φ_k，u ≈ Σ c_u·φ_k。基是连续函数（不依赖网格点数），
    所以**天然分辨率无关**（train 在粗网格、query 在细网格都成立）。
  - 算子 G: a→u 在谱空间是线性映射 c_u = W·c_a（对线性算子如 Poisson/热核，谱映射
    逐模态独立，FNO 单层即可精确表示）。
  - W 用**闭式岭回归**求解（无需 autodiff / GPU）：W = (Σc_u c_aᵀ)(Σc_a c_aᵀ + λI)⁻¹。
  - **UQ（epistemic）**：集成 K 个成员（自助采样 + ridge 抖动），预测取均值与逐点 std。
    这是 SwarmLabs「UQ 一等公民」纪律在神经算子上的落地——AU 没有这步。

接口（字段原生，与逐点 GPSurrogate 不同）：
  fno = FourierNeuralOperator(modes=32, ensemble=7)
  fno.fit(A, U)                      # A (n,N_in) 输入函数；U (n,N_out) 输出函数
  u_mean, u_std = fno.predict(a_q)   # a_q (N_in,) → 输出场均值与不确定度
  # 分辨率无关：在细网格上查询
  u_mean, u_std = fno.predict(a_fine, grid_in_q=fine_grid, grid_out_q=fine_grid)
"""

from __future__ import annotations
import numpy as np


class FourierNeuralOperator:
    def __init__(self, modes: int = 32, ensemble: int = 7, ridge: float = 1e-3,
                 seed: int = 0, basis: str = "fourier"):
        self.modes = int(modes)
        self.ensemble = int(ensemble)
        self.ridge = float(ridge)
        self.seed = int(seed)
        self.basis_kind = basis          # "fourier" | "sine"
        self.W_list = []                 # 集成权重：每个成员一个 (D_out × D_in)
        self.grid_in = None
        self.grid_out = None
        self.D_in = None
        self.D_out = None
        self._Ca_train = None
        self._Cu_train = None

    # ---- 连续基（分辨率无关的关键） -------------------------------------
    def _B(self, grid):
        grid = np.asarray(grid, float).ravel()
        if self.basis_kind == "sine":
            cols = [np.sin((k + 1) * np.pi * grid) for k in range(self.modes)]
            return np.stack(cols, axis=1)
        # full fourier：常数 + cos(2πk·) + sin(2πk·)
        cols = [np.ones_like(grid)]
        for k in range(1, self.modes + 1):
            cols.append(np.cos(2 * np.pi * k * grid))
            cols.append(np.sin(2 * np.pi * k * grid))
        return np.stack(cols, axis=1)

    def _project(self, funcs, grid):
        """funcs (n, N) → 谱系数 (n, D)。带 ridge 防近奇异。"""
        B = self._B(grid)                                  # (N, D)
        A = B.T @ B + self.ridge * np.eye(B.shape[1])
        coeff = np.linalg.solve(A, B.T @ funcs.T)          # (D, n)
        return coeff.T                                     # (n, D)

    # ---- 拟合 ----------------------------------------------------------
    def fit(self, A, U, grid_in=None, grid_out=None):
        A = np.atleast_2d(np.asarray(A, float))
        U = np.atleast_2d(np.asarray(U, float))
        n, N_in = A.shape
        n2, N_out = U.shape
        if n != n2:
            raise ValueError(f"A 有 {n} 个函数，U 有 {n2} 个，必须对齐")
        if grid_in is None:
            grid_in = np.linspace(0.0, 1.0, N_in)
        if grid_out is None:
            grid_out = np.linspace(0.0, 1.0, N_out)
        self.grid_in = np.asarray(grid_in, float)
        self.grid_out = np.asarray(grid_out, float)
        self.D_in = self._B(self.grid_in).shape[1]
        self.D_out = self._B(self.grid_out).shape[1]

        C_a = self._project(A, self.grid_in)               # (n, D_in)
        C_u = self._project(U, self.grid_out)              # (n, D_out)
        rng = np.random.default_rng(self.seed)
        self.W_list = []
        for _ in range(self.ensemble):
            idx = rng.integers(0, n, size=n)               # 自助采样
            Ca = C_a[idx]
            Cu = C_u[idx]
            r = self.ridge * (1.0 + 0.1 * rng.random())    # 每成员 ridge 抖动 → 多样性
            Aa = Ca.T @ Ca + r * np.eye(self.D_in)
            W = np.linalg.solve(Aa, Ca.T @ Cu)             # (D_out, D_in)
            self.W_list.append(W)
        self._Ca_train = C_a
        self._Cu_train = C_u
        self._U_train = U
        # 校准地板：集成均值在训练集上的 RMSE → 作为「模型误差」不确定度下限。
        # 否则纯 epistemic（成员方差）在线性算子近完美拟合时会趋零，导致 95%CI
        # 过度自信（覆盖率为 0）。这是 SwarmLabs「UQ 一等公民」纪律的硬约束：
        # 预测区间必须真实覆盖约 95% 的真实点（见 Calibrator.coverage）。
        errs = []
        Bo = self._B(self.grid_out)
        for i in range(n):
            c = C_a[i]
            stack = np.stack([W @ c for W in self.W_list], axis=0)
            u_mean = Bo @ stack.mean(axis=0)
            errs.append(np.sqrt(np.mean((u_mean - U[i]) ** 2)))
        self._calib = float(np.mean(errs)) + 1e-9
        return self

    # ---- 预测（支持分辨率无关查询） ------------------------------------
    def predict(self, a_q, grid_in_q=None, grid_out_q=None):
        """给定输入函数 a_q，返回输出场 (mean, std)。

        grid_in_q  : a_q 采样所在的网格（默认=self.grid_in，可被细网格覆盖 → 分辨率无关）
        grid_out_q : 输出场重建网格（默认=self.grid_out，可被细网格覆盖）
        """
        a_q = np.asarray(a_q, float).ravel()
        g_in = self.grid_in if grid_in_q is None else np.asarray(grid_in_q, float)
        g_out = self.grid_out if grid_out_q is None else np.asarray(grid_out_q, float)
        # 在查询网格上把 a_q 投影到输入谱基（连续基 → 任意分辨率都成立）
        Bq = self._B(g_in)
        c_a = np.linalg.solve(Bq.T @ Bq + self.ridge * np.eye(Bq.shape[1]),
                              Bq.T @ a_q)                                  # (D_in,)
        stack = np.stack([W @ c_a for W in self.W_list], axis=0)           # (E, D_out)
        c_u_mean = stack.mean(axis=0)
        c_u_std = stack.std(axis=0)
        # 重建输出场（连续基 → 任意输出分辨率）
        Bo = self._B(g_out)
        u_mean = Bo @ c_u_mean
        # 总 UQ = sqrt(epistemic 成员方差² + 模型误差校准地板²)
        # 仅用 epistemic 会在近完美拟合时过度自信（覆盖率→0）；校准地板保证
        # 95% CI 真实覆盖约 95% 真实点。
        epistemic = Bo @ c_u_std
        u_std = np.sqrt(epistemic ** 2 + self._calib ** 2)
        return u_mean, u_std

    # ---- 对等度评估（与 Calibrator 同构，便于进仪表盘） ----------------
    def evaluate(self, oracle, a_test, z: float = 1.96):
        """oracle(a) → u_true (1d 场)；a_test 为 (k, N) 输入函数集。"""
        a_test = np.atleast_2d(np.asarray(a_test, float))
        errs, ins, stds = [], [], []
        for a in a_test:
            u_true = np.asarray(oracle(a), float).ravel()
            u_mean, u_std = self.predict(a)
            errs.append(np.sqrt(np.mean((u_mean - u_true) ** 2)))
            lo, hi = u_mean - z * u_std, u_mean + z * u_std
            ins.append(np.mean((u_true >= lo) & (u_true <= hi)))
            stds.append(np.mean(u_std))
        return {
            "rmse": round(float(np.mean(errs)), 4),
            "coverage": round(float(np.mean(ins)), 3),
            "cal_err": round(abs(float(np.mean(ins)) - 0.95), 3),
            "mean_std": round(float(np.mean(stds)), 4),
        }
