"""神经算子代理（Neural Operator）—— 纯 numpy、CPU 可跑、已做实。

提供两个层次：
  1. FourierOperatorSurrogate：线性 Fourier 特征算子（RFF 等价），点映射 / 网格
     函数映射均可，门面一致 fit/predict -> (mean, std)。无需训练，即开即用。
  2. DeepONetSurrogate：真正的**深度神经算子**（branch + trunk 双网络，端到端
     训练），学习「输入函数空间 → 输出函数空间」的非线性算子。无需 GPU，手写
     Adam 在 numpy 上训练，预测带残差 UQ。这是 FNO/DeepONet 深度版的落地实现。

适用场景：输入是网格上的函数（如 PDE 初值场 u0(x)），输出是另一网格函数
（如 t=T 时的场 u(x)）。这正是「仿真」的核心形态——一个算子把一类初始条件
映射到解。把它做实数值，等于把 surrogate 的能力从「点映射」扩展到「函数映射」。
"""

from __future__ import annotations

import numpy as np


class FourierOperatorSurrogate:
    """神经算子代理（随机傅里叶特征 RFF 近似）—— 纯 numpy，已做实。

    与 GPSurrogate 门面完全一致（fit(X,y) / predict(Xs) -> mean, std），可无缝
    切换。RFF 是单隐层「神经算子」的核机等价形式（Rahimi-Recht）：把输入经随机
    傅里叶映射后做岭回归，预测带残差 std 作为 UQ。无需 GPU。
    """

    def __init__(self, n_features=128, gamma=1.0, ridge=1e-6):
        self.n_features = int(n_features)
        self.gamma = float(gamma)
        self.ridge = float(ridge)
        self._W = None
        self._b = None
        self._beta = None
        self._std = None
        self._xmin = None
        self._xmax = None

    def _features(self, X):
        Xn = (X - self._xmin) / (self._xmax - self._xmin + 1e-9)
        Z = np.sqrt(2.0 / self.n_features) * np.cos(Xn @ self._W + self._b)
        return Z

    def fit(self, X, y):
        X = np.atleast_2d(np.asarray(X, float))
        y = np.asarray(y, float).ravel()
        self._xmin = X.min(axis=0)
        self._xmax = X.max(axis=0)
        rng = np.random.default_rng(0)
        d = X.shape[1]
        self._W = rng.normal(0, self.gamma, (d, self.n_features))
        self._b = rng.uniform(0, 2 * np.pi, self.n_features)
        Z = self._features(X)
        A = Z.T @ Z + self.ridge * np.eye(self.n_features)
        self._beta = np.linalg.solve(A, Z.T @ y)
        pred = Z @ self._beta
        self._std = float(np.std(y - pred)) + 1e-9
        return self

    def predict(self, Xs):
        if self._beta is None:
            raise RuntimeError("call fit() first")
        Z = self._features(np.atleast_2d(np.asarray(Xs, float)))
        mean = Z @ self._beta
        std = np.full(mean.shape, self._std)
        return mean, std


# ---------------------------------------------------------------------------
# Burgers 方程真实数值解数据集（数值解 = ground truth，纯物理，非合成种子）
# ---------------------------------------------------------------------------
def make_burgers_dataset(n_grid=64, n_train=256, n_test=64, seed=0,
                         nu=0.01, T=0.5, dt=0.0005):
    """生成 (u0_grid, uT_grid) 算子对：用谱方法求解黏性 Burgers 方程。

    u_t + u u_x = nu u_xx,  周期边界。
    初值 u0 为单/双随机正弦模态（平滑、周期、幅度受限以保证谱方法稳定），
    用于学习「初值场 → T 时刻场」的非线性算子。返回
    (u0_train, uT_train, u0_test, uT_test, x_grid)。数值解即 ground truth。
    """
    rng = np.random.default_rng(seed)
    x = np.linspace(0.0, 1.0, n_grid, endpoint=False)
    dx = x[1] - x[0]
    k = 2.0 * np.pi * np.fft.fftfreq(n_grid, d=dx)
    ik = 1j * k
    ik2 = -k ** 2

    def rhs(u):
        uh = np.fft.fft(u)
        ux = np.real(np.fft.ifft(ik * uh))
        uxx = np.real(np.fft.ifft(ik2 * uh))
        return -u * ux + nu * uxx

    def solve(u0):
        u = u0.copy()
        nsteps = int(round(T / dt))
        for _ in range(nsteps):
            k1 = rhs(u)
            k2 = rhs(u + 0.5 * dt * k1)
            k3 = rhs(u + 0.5 * dt * k2)
            k4 = rhs(u + dt * k3)
            u = u + dt / 6.0 * (k1 + 2 * k2 + 2 * k3 + k4)
        return u

    def sample_ic():
        # 单/双模态，幅度受限（≤0.9），避免强激波导致谱方法发散
        m = int(rng.integers(1, 3))
        u0 = np.zeros(n_grid)
        for _ in range(m):
            A = rng.uniform(0.4, 0.9)
            km = int(rng.integers(1, 3))
            phi = rng.uniform(0, 2 * np.pi)
            u0 = u0 + A * np.sin(2 * np.pi * km * x + phi)
        return u0

    def gen(n):
        U0, UT = [], []
        tries = 0
        while len(U0) < n and tries < n * 20:
            tries += 1
            u0 = sample_ic()
            uT = solve(u0)
            if np.all(np.isfinite(uT)) and np.max(np.abs(uT)) < 5.0:
                U0.append(u0)
                UT.append(uT)
        if len(U0) < n:
            raise RuntimeError("Burgers 生成失败：过多非有限解")
        return np.array(U0), np.array(UT)

    u0_tr, uT_tr = gen(n_train)
    u0_te, uT_te = gen(n_test)
    return u0_tr, uT_tr, u0_te, uT_te, x


# ---------------------------------------------------------------------------
# DeepONetSurrogate：真正的深度神经算子（branch + trunk，端到端训练）
# ---------------------------------------------------------------------------
class DeepONetSurrogate:
    """深度神经算子（DeepONet）—— 纯 numpy 手写 Adam 训练，无需 GPU。

    结构：
      branch 网：输入初值场 u0 (n_grid 点) -> 隐向量 b (p 维)
      trunk  网：输入网格坐标 x (标量)     -> 隐向量 t (p 维)
      输出：uT(x) = b · t(x) + b0   （b 与 t 的点积 + 偏置）

    训练：在 (样本, 网格点) 上最小化 MSE，Adam 更新（门面一致：fit/predict
    返回 (mean, std)，std 取训练残差作为同方差 UQ）。

    这是把 surrogate 从「点映射」升级到「函数映射」的深度实现：给定一类初值场，
    学到把它映射到解场的非线性算子。
    """

    def __init__(self, p=64, branch_h=64, trunk_h=64, lr=1e-3, n_iter=3000,
                 seed=0, verbose=False):
        self.p, self.bh, self.th = int(p), int(branch_h), int(trunk_h)
        self.lr, self.n_iter = float(lr), int(n_iter)
        self.seed = int(seed)
        self.verbose = bool(verbose)
        self._std = None
        self._x = None

    # ---- 前向（向量化：X0 (N,n_grid), x (G,1) -> (N,G)）-------------------
    def _forward(self, P, X0, x):
        W1, b1, W2, b2, W3, b3, W4, b4, b0 = P
        # branch
        z1 = X0 @ W1 + b1          # (N, bh)
        a1 = np.tanh(z1)
        bout = a1 @ W2 + b2        # (N, p)
        # trunk
        z3 = x @ W3 + b3           # (G, th)
        a3 = np.tanh(z3)
        tout = a3 @ W4 + b4        # (G, p)
        return bout @ tout.T + b0  # (N, G)

    def _loss_and_grads(self, P, X0, x, Y):
        W1, b1, W2, b2, W3, b3, W4, b4, b0 = P
        N, G = X0.shape[0], x.shape[0]
        z1 = X0 @ W1 + b1
        a1 = np.tanh(z1)
        bout = a1 @ W2 + b2
        z3 = x @ W3 + b3
        a3 = np.tanh(z3)
        tout = a3 @ W4 + b4
        Yh = bout @ tout.T + b0
        err = Yh - Y
        scale = 2.0 / (N * G)
        dYh = scale * err                       # (N,G)
        # branch grads
        g_b0 = np.sum(dYh)
        g_bout = dYh @ tout                     # (N,p)
        g_b2 = np.sum(g_bout, axis=0)           # (p,)
        g_W2 = a1.T @ g_bout                    # (bh,p)
        g_a1 = g_bout @ W2.T                    # (N,bh)
        g_z1 = g_a1 * (1.0 - a1 ** 2)
        g_b1 = np.sum(g_z1, axis=0)             # (bh,)
        g_W1 = X0.T @ g_z1                      # (n_grid,bh)
        # trunk grads
        g_tout = dYh.T @ bout                   # (G,p)  (sum over N)
        g_b4 = np.sum(g_tout, axis=0)           # (p,)
        g_W4 = a3.T @ g_tout                    # (th,p)
        g_a3 = g_tout @ W4.T                    # (G,th)
        g_z3 = g_a3 * (1.0 - a3 ** 2)
        g_b3 = np.sum(g_z3, axis=0)            # (th,)
        g_W3 = x.T @ g_z3                       # (1,th)
        loss = float(np.mean(err ** 2))
        grads = [g_W1, g_b1, g_W2, g_b2, g_W3, g_b3, g_W4, g_b4, g_b0]
        return loss, grads

    def fit(self, u0_grid, uT_grid, x_grid=None):
        u0_grid = np.atleast_2d(np.asarray(u0_grid, float))
        uT_grid = np.atleast_2d(np.asarray(uT_grid, float))
        N, n_grid = u0_grid.shape
        G = uT_grid.shape[1]
        if x_grid is None:
            x_grid = np.linspace(0.0, 1.0, G, endpoint=False).reshape(-1, 1)
        self._x = np.asarray(x_grid, float).reshape(-1, 1)
        rng = np.random.default_rng(self.seed)
        # 初始化（Xavier 风格）
        s = np.sqrt(2.0 / max(n_grid, self.p))
        P = [
            rng.normal(0, s, (n_grid, self.bh)),
            np.zeros(self.bh),
            rng.normal(0, np.sqrt(2.0 / self.bh), (self.bh, self.p)),
            np.zeros(self.p),
            rng.normal(0, np.sqrt(2.0 / max(1, self.th)), (1, self.th)),
            np.zeros(self.th),
            rng.normal(0, np.sqrt(2.0 / self.th), (self.th, self.p)),
            np.zeros(self.p),
            np.zeros(1),
        ]
        m = [np.zeros_like(p) for p in P]
        v = [np.zeros_like(p) for p in P]
        b1, b2 = 0.9, 0.999
        for t in range(1, self.n_iter + 1):
            loss, g = self._loss_and_grads(P, u0_grid, self._x, uT_grid)
            for i in range(len(P)):
                m[i] = b1 * m[i] + (1 - b1) * g[i]
                v[i] = b2 * v[i] + (1 - b2) * (g[i] ** 2)
                mh = m[i] / (1 - b1 ** t)
                vh = v[i] / (1 - b2 ** t)
                P[i] = P[i] - self.lr * mh / (np.sqrt(vh) + 1e-8)
            if self.verbose and t % 500 == 0:
                print(f"  iter {t:5d} loss {loss:.3e}")
        self._P = P
        resid = uT_grid - self._forward(P, u0_grid, self._x)
        self._std = float(np.std(resid)) + 1e-9
        self._fit = True
        return self

    def predict(self, u0_grid):
        if not getattr(self, "_fit", False):
            raise RuntimeError("call fit() first")
        u0_grid = np.atleast_2d(np.asarray(u0_grid, float))
        mean = self._forward(self._P, u0_grid, self._x)
        std = np.full(mean.shape, self._std)
        return mean, std

    def evaluate(self, u0_grid, uT_grid, z=1.96):
        mean, std = self.predict(u0_grid)
        uT_grid = np.atleast_2d(np.asarray(uT_grid, float))
        rmse = float(np.sqrt(np.mean((mean - uT_grid) ** 2)))
        lo, hi = mean - z * std, mean + z * std
        cover = float(np.mean((uT_grid >= lo) & (uT_grid <= hi)))
        return {"rmse": round(rmse, 5), "coverage": round(cover, 3), "mean_std": round(self._std, 5)}
