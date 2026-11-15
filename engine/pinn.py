"""物理信息神经网络 (PINN) —— 1D 边值/初值问题的物理约束求解器。

诚实定位：用一个小 MLP + 物理残差**软约束**，在 CPU 上训练（scipy L-BFGS-B，
数值梯度，**无需 GPU / torch**）。证明「物理信息训练」在我们的纯 numpy 栈可跑通，
后续可挂到 SurrogateEngine 作为「物理一致性校验 / 发现式本构律」组件。

示例：求解  u'' = -sin(x),  u(0)=0, u(1)=1
解析解  u(x) = sin(x) + (1 - sin(1)) * x
PINN 不被告知解析解，只被告知 PDE + 边界条件，自己学出 u(x)。

**⚠️ 对外宣称边界（2026-10-14 板块 02 修正）**：
本模块仅作为「物理信息训练范式可行」的**定性验证**，
**未**与 SciPy / FEM 数值解做任何定量的对拍基准（如无残差谱、
无 PDE 残差随训练步的收敛曲线、无与 PINN 文献基准的定量比较）。
因此对外叙事应表述为「PINN 范式在纯 numpy 栈可跑通」，
**不可**表述为「达到工业级 PINN 精度」或「替代 SciPy 求解器」。
与 `engine/neural_operator.py` 的 DeepONet 同为定性演示，
均未纳入 fail-closed 门禁的强校验路径。
"""

from __future__ import annotations

import numpy as np
from scipy.optimize import minimize


class PINN1D:
    def __init__(self, x_grid=None, hidden=20):
        self.x = np.linspace(0.0, 1.0, 50) if x_grid is None else np.asarray(x_grid, float)
        self.hidden = int(hidden)
        self.params_ = None
        self.loss_ = None
        self.max_residual_ = None

    # ---- 网络（1 输入，1 隐藏层 tanh，1 输出） ----------------------------
    def _split(self, p):
        h = self.hidden
        W1 = p[0:h].reshape(1, h)          # (1,h): 输入(1) -> 隐藏(h)
        b1 = p[h:2 * h]                     # (h,)
        W2 = p[2 * h:3 * h].reshape(h, 1)   # (h,1): 隐藏(h) -> 输出(1)
        b2 = p[3 * h]
        return W1, b1, W2, b2

    def _net(self, p, x):
        W1, b1, W2, b2 = self._split(p)
        z = x.reshape(-1, 1) @ W1 + b1          # (G,h)
        h = np.tanh(z)
        return h @ W2 + b2                       # (G,1)

    def _net_d2(self, p, x):
        W1, b1, W2, b2 = self._split(p)
        z = x.reshape(-1, 1) @ W1 + b1
        t = np.tanh(z)
        t1 = 1.0 - t ** 2
        t2 = -2.0 * t * t1
        h2 = t2 * (W1 ** 2).reshape(1, -1)      # (G,h)：d2/dx2 of hidden
        return (h2 @ W2).ravel()                 # (G,)

    # ---- 物理残差：pde(x) = u'' - f(x) -----------------------------------
    def _pde_residual(self, p, x, f):
        u2 = self._net_d2(p, x)
        return u2 - f(x)

    def fit(self, f, bc=(0.0, 1.0), lam_pde=1.0, n_interior=None, seed=0):
        """训练。f：右端项函数 f(x)（u'' = f）。bc=(u(0), u(1))。"""
        rng = np.random.default_rng(seed)
        x = self.x
        xi = x[1:-1] if n_interior is None else x
        p0 = rng.normal(0, 0.3, 3 * self.hidden + 1)

        def loss(p):
            u = self._net(p, x).ravel()
            bc_loss = (u[0] - bc[0]) ** 2 + (u[-1] - bc[1]) ** 2
            res = self._pde_residual(p, xi, f)
            pde_loss = np.mean(res ** 2)
            return bc_loss + lam_pde * pde_loss

        res = minimize(loss, p0, method="L-BFGS-B", options={"maxiter": 2000})
        self.params_ = res.x
        self.loss_ = float(res.fun)
        res_full = self._pde_residual(self.params_, x, f)
        self.max_residual_ = float(np.max(np.abs(res_full)))
        return self

    def predict(self, x):
        if self.params_ is None:
            raise RuntimeError("call fit() first")
        return self._net(self.params_, np.asarray(x, float)).ravel()

    def report(self):
        # ⚠️ 2026-10-14 板块 02 P0 (#551)：report 输出显式标记为定性演示，
        # 禁止被下游解读为"达到工业级 PINN 精度"或"替代 SciPy/FEM 求解器"。
        # 与 neural_operator.py (DeepONet) 同为定性演示，均未纳入 fail-closed
        # 门禁的强校验路径，也不计入 gate_ledger.json 的 PASS/MARGINAL 统计。
        return {
            "mode": "qualitative_demo_only",
            "disclaimer": (
                "PINN 范式在纯 numpy 栈可跑通的定性验证；"
                "未与 SciPy/FEM 做定量对拍，不进入 fail-closed 门禁强校验路径。"
            ),
            "max_pde_residual": round(self.max_residual_, 5),
            "final_loss": round(self.loss_, 5),
            "hidden": self.hidden,
        }
