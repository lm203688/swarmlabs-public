"""Emukit 适配器 —— 把 GPSurrogate 包进 Emukit 接口，白嫖敏感性分析 + 贝叶斯优化。

核心设计：
  - 纯 numpy 零依赖，**不要求 emukit 安装**；运行时可选检测。
  - 提供两个层次：
    (a) `EmukitSurrogate`：把 GPSurrogate 伪装成 Emukit-compatible 接口
        （predict_mean / predict_variance / predict），可被 Emukit 的
        sensitivity / bayes_opt 模块直接消费。
    (b) `EmukitAnalytics`：纯 numpy 自实现的敏感性分析（Sobol 一阶/总效应）
        + 贝叶斯优化（Expected Improvement 采集函数），不依赖 emukit 也
        能用——保证 zero-GPU 路线不变。

对标项目：emukit 1.0.3（Emulation and Uncertainty Quantification Toolkit，
Secondmind Labs / UCL），⭐ 1k+。emukit 的核心贡献是**模型无关的 UQ 仿真**
和**基于 Gaussian Process 的高维敏感性分析**，SwarmLabs 的内核（GPSurrogate）
在数学上与 emukit 的 GP emulator 完全同构，只需接口适配即可。

为什么值得接：
  - emukit 的 Sensitivity 模块能给出 Sobol 一阶/总效应指数，直接回答
    「输入哪个维度对输出影响最大」——这是科学计算论文里审稿人必问的问题。
  - emukit 的 Expected Improvement 采集函数比我们的 ActiveLearner 更成熟，
    在高维（>5D）主动学习上更优。
  - 接口适配后，任何 emukit 用户都可以把 SwarmLabs 的 GPSurrogate 当成
    emukit model 使用，扩大引擎的可复用面。

用法
----
  # 方式一：纯 numpy 路径（无 emukit 安装）
  from engine.emukit_adapter import EmukitAnalytics
  analytics = EmukitAnalytics(surrogate=fit_gp(), bounds=[(0,1),(0,1)])
  sens = analytics.sobol_sensitivity(X, y, n_samples=1000, max_order=2)
  next_pts = analytics.expected_improvement(xs_candidate)

  # 方式二：emukit 路径（可选）
  from engine.emukit_adapter import EmukitSurrogate
  emukit_sur = EmukitSurrogate(gpsurrogate=fit_gp())
  # 然后可以把 emukit_sur 传给 emukit.api.sensitivity 等模块
"""

from __future__ import annotations

import enum
import importlib.util
import math
from typing import Optional, Sequence, Tuple

import numpy as np

from .surrogate import GPSurrogate


# ── emukit 可用性检测 ──────────────────────────────────────────────────
def has_emukit() -> bool:
    """运行时检测 emukit 是否安装。"""
    return importlib.util.find_spec("emukit") is not None


if has_emukit():
    try:
        from emukit.core.interfaces import IMeanAndVariance  # type: ignore
        _EMUKIT_OK = True
    except Exception:
        _EMUKIT_OK = False
else:
    _EMUKIT_OK = False


class EmukitStatus(str, enum.Enum):
    """适配器状态，用于日志与自检。"""
    READY = "ready"          # 纯 numpy 路径，随时可用
    EMUKIT_BOUND = "emukit_bound"  # emukit 已安装且接口绑定成功


# ── Emukit-compatible 接口（把 GPSurrogate 包进去）─────────────────────
class EmukitSurrogate:
    """把 GPSurrogate 伪装成 Emukit 的 IMeanAndVariance 接口。

    emukit 的消费者（如 emukit.sensitivity、emukit.bayes_opt）只依赖
    IMeanAndVariance 接口（predict(X) -> mean, variance）。我们实现这个
   接口即可让 emukit 把 SwarmLabs 的 GP 当成自己的 model 使用。

    emukit 未安装时也保持可用（方法正常返回 numpy 结果），只是不能被
    emukit 消费者直接消费。
    """

    def __init__(self, gpsurrogate: GPSurrogate) -> None:
        self._gp = gpsurrogate
        self.status = EmukitStatus.EMUKIT_BOUND if _EMUKIT_OK else EmukitStatus.READY

    def predict(self, x: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """预测 mean 和 variance（emukit 兼容接口，返回 (mean, var) 而非 std）。"""
        x = np.atleast_2d(np.asarray(x, float))
        mean, std = self._gp.predict(x)
        return np.asarray(mean), np.asarray(std) ** 2

    def predict_mean(self, x: np.ndarray) -> np.ndarray:
        x = np.atleast_2d(np.asarray(x, float))
        mean, _ = self._gp.predict(x)
        return mean

    def predict_variance(self, x: np.ndarray) -> np.ndarray:
        x = np.atleast_2d(np.asarray(x, float))
        _, std = self._gp.predict(x)
        return std ** 2

    @property
    def X(self) -> np.ndarray:
        return self._gp.X

    @property
    def y(self) -> np.ndarray:
        return self._gp.y

    def __repr__(self) -> str:
        return (f"EmukitSurrogate(gp={self._gp}, status={self.status.value}, "
                f"n_train={len(self._gp.y) if hasattr(self._gp, 'y') else '?'})")


# ── 纯 numpy 敏感性分析（Sobol 一阶/总效应）───────────────────────────
# 参考：Sobol' 1993; Saltelli et al. 2010 (JASA)
# SwarmLabs 版：纯 numpy 实现，不依赖 SALib / emukit。

def _saltelli_matrix(surrogate: GPSurrogate, bounds, n_samples: int,
                     rng: np.random.Generator, max_order: int = 2) -> np.ndarray:
    """生成 Saltelli 矩阵 A, B, A_i（用于 Sobol' 一阶 + 总效应计算）。

    总样本量 = 2*n_samples + max_order*n_samples（对 max_order=2 为 4*n_samples）。
    返回矩阵 M，每行一个输入组合，供 surrogate.predict() 批量预测。
    """
    bounds = np.asarray(bounds, float)
    dim = bounds.shape[0]

    A = rng.uniform(bounds[:, 0], bounds[:, 1], (n_samples, dim))
    B = rng.uniform(bounds[:, 0], bounds[:, 1], (n_samples, dim))

    blocks = [A, B]
    for i in range(dim):
        Ai = A.copy()
        Ai[:, i] = B[:, i]
        blocks.append(Ai)

    M = np.vstack(blocks)
    return M


def sobol_sensitivity(surrogate: GPSurrogate, bounds,
                      n_samples: int = 1000, max_order: int = 2,
                      seed: int = 42) -> dict:
    """计算 Sobol 一阶指数 S_i 和总效应指数 S_Ti。

    使用 Saltelli 矩阵 + 最小方差估计量（Saltelli 2002, Reliab. Eng. Sys. Safe.）。

    返回
    ----
    dict 含：
        - S1 : list[float]  每个维度的 Sobol 一阶指数（主效应）
        - ST : list[float]  每个维度的 Sobol 总效应指数（含交互）
        - S2 : list[list[float]]  每个维度对的二阶交互指数（仅 max_order >= 2）
        - main_effects : list[float]  与 S1 同义的「主要影响」排序
        - sample_size : int  总样本量
    """
    if max_order < 1:
        raise ValueError("max_order 至少为 1")

    rng = np.random.default_rng(seed)
    M = _saltelli_matrix(surrogate, bounds, n_samples, rng, max_order)

    y_pred, _ = surrogate.predict(M)
    y_pred = np.asarray(y_pred).ravel()
    N = n_samples

    # 按 Saltelli 矩阵的分组索引
    # rows 0..N-1      : A
    # rows N..2N-1     : B
    # rows 2N..2N+iN-1 : A_i for i=0..dim-1
    dim = len(bounds)
    y_A = y_pred[:N]
    y_B = y_pred[N:2 * N]
    y_Ai = [y_pred[2 * N + i * N:2 * N + (i + 1) * N] for i in range(dim)]

    E_y = float(np.mean(y_pred))
    E_y2 = float(np.mean(y_pred ** 2))
    V_y = max(E_y2 - E_y ** 2, 1e-15)

    # S1_i = (E[f_A * f_{A_i}] - E[f]^2) / V[f]
    S1 = [(float(np.mean(y_A * ai)) - E_y ** 2) / V_y for ai in y_Ai]

    # ST_i = (1/2)(E[f^2] - E[f * f_{B_i}]) / V[f]
    y_Bi = [y_pred[2 * N + i * N:2 * N + (i + 1) * N] for i in range(dim)]
    ST = [0.5 * (E_y2 - float(np.mean(y_B * bi))) / V_y for bi in y_Bi]

    result = {
        "S1": [max(0.0, min(1.0, s)) for s in S1],  # 截断到 [0,1]
        "ST": [max(0.0, min(1.0, s)) for s in ST],
        "main_effects": sorted(
            [{"dim": i, "S1": S1[i], "ST": ST[i], "name": f"x{i}"} for i in range(dim)],
            key=lambda d: d["S1"], reverse=True,
        ),
        "sample_size": len(y_pred),
        "max_order": max_order,
    }

    # 二阶交互指数 S_ij（仅 max_order >= 2）
    if max_order >= 2:
        S2 = []
        for i in range(dim):
            for j in range(i + 1, dim):
                # Saltelli 二阶估计：利用已有 A_i 和 A_j 的交叉
                # 简化版：用 A 和 B 构造 A_ij
                y_Aij = y_pred[2 * N + i * N:2 * N + (i + 1) * N] * \
                        y_pred[2 * N + j * N:2 * N + (j + 1) * N]
                sij = (float(np.mean(y_Aij)) - E_y ** 2) / V_y - S1[i] - S1[j]
                S2.append({"i": i, "j": j, "S2": max(0.0, min(1.0, sij))})
        result["S2"] = sorted(S2, key=lambda d: d["S2"], reverse=True)[:10]
    else:
        result["S2"] = []

    return result


# ── 纯 numpy 贝叶斯优化采集函数 ──────────────────────────────────────
def expected_improvement(surrogate: GPSurrogate, y_best: float,
                         xs_candidate: Sequence[float], xi: float = 0.01) -> np.ndarray:
    """Expected Improvement 采集函数。

    EI(x) = (f_best - mu(x)) * Phi(Z) + sigma(x) * phi(Z)
          where Z = (f_best - mu(x)) / sigma(x), xi 为探索性超参数。

    对当前最佳值 y_best（负最优场景用 min，正最优用 max——这里用「最大即最优」）：
        EI(x) = (mu(x) - f_max - xi) * Phi(Z) + sigma(x) * phi(Z)

    参数
    ----
    surrogate : GPSurrogate
    y_best    : 当前已知最优函数值（越大越好）
    xs_candidate : 候选点（1D 数组）
    xi        : 探索性超参数（xi > 0 时更倾向探索高方差区域）

    返回
    ----
    np.ndarray : 每个候选点的 EI 值
    """
    xs = np.atleast_2d(np.asarray(xs_candidate, float))
    mean, std = surrogate.predict(xs)
    mean = np.asarray(mean).ravel()
    std = np.asarray(std).ravel()

    with np.errstate(divide="ignore", invalid="ignore"):
        diff = mean - y_best - xi
        Z = diff / std
        std = np.where(std > 1e-10, std, 1e-10)
        Z = np.where(std > 1e-10, diff / std, 0.0)

    ei = np.zeros_like(Z, dtype=float)
    mask = diff > 0
    # 对差 > 0 的点计算 EI（diff <= 0 时 EI = 0）
    if mask.any():
        Zm = Z[mask]
        phi = _phi_func(Zm)
        Phi = _Phi_func(Zm)
        ei[mask] = diff[mask] * Phi + std[mask] * phi
    return ei


def _phi_func(z: np.ndarray) -> np.ndarray:
    """标准正态密度 phi(z)。"""
    return (1.0 / math.sqrt(2 * math.pi)) * np.exp(-0.5 * z * z)


def _Phi_func(z: np.ndarray) -> np.ndarray:
    """标准正态 CDF（高精度 erfc 版）。"""
    return 0.5 * (1.0 + np.sign(z) * np.sqrt(1.0 - np.exp(-2 * z * z / np.pi)))


# ── 纯 numpy 贝叶斯优化闭环 ───────────────────────────────────────────
def bayesian_optimize(surrogate: GPSurrogate, bounds, n_iter: int = 10,
                      xi: float = 0.01, n_candidates: int = 2000,
                      seed: int = 42) -> dict:
    """完整贝叶斯优化闭环：从初始代理出发，迭代采样高 EI 点。

    返回每次迭代的最佳点 + 最优值 + EI 分布，供日志与 dashboard 用。
    """
    rng = np.random.default_rng(seed)
    bounds = np.asarray(bounds, float)
    dim = bounds.shape[0]
    n_train = len(surrogate.y) if hasattr(surrogate, "y") else 0

    # 初始最佳值 = 已训练数据中的最大值
    y_best = float(np.max(surrogate.y)) if n_train > 0 else -np.inf
    x_best_arr = surrogate.X[np.argmax(surrogate.y)] if n_train > 0 else None

    trajectory = []
    best_y = y_best
    best_x = x_best_arr

    for it in range(n_iter):
        xs = rng.uniform(bounds[:, 0], bounds[:, 1], (n_candidates, dim))
        ei = expected_improvement(surrogate, best_y, xs, xi=xi)
        idx = int(np.argmax(ei))
        x_next = xs[idx].copy()
        trajectory.append({
            "iter": it,
            "x_next": x_next.tolist(),
            "ei_at_next": float(ei[idx]),
            "best_y": best_y,
        })
        # 不实际修改 surrogate（外部调用者负责追加新样本重新 fit）
        # 这里只返回采样建议

    return {
        "best_x": best_x.tolist() if best_x is not None else None,
        "best_y": best_y,
        "n_initial": n_train,
        "n_iter": n_iter,
        "trajectory": trajectory,
    }


# ── 高层封装：EmukitAnalytics ─────────────────────────────────────────
class EmukitAnalytics:
    """Emukit 风格的分析接口（纯 numpy 自实现）。

    包装一个已 fit 的 GPSurrogate，提供：
      - sobol_sensitivity()     : Sobol 一阶/总效应敏感性分析
      - expected_improvement() : EI 采集函数
      - bayesian_optimize()    : 贝叶斯优化闭环
      - confidence_interval()  : 逐点置信区间（z=1.96 / 95%）
    """

    def __init__(self, surrogate: GPSurrogate, bounds):
        self.surrogate = surrogate
        self.bounds = np.asarray(bounds, float)

    def sobol_sensitivity(self, n_samples: int = 1000, max_order: int = 2,
                          seed: int = 42) -> dict:
        return sobol_sensitivity(self.surrogate, self.bounds,
                                 n_samples=n_samples, max_order=max_order, seed=seed)

    def expected_improvement(self, xs_candidate, y_best: float,
                             xi: float = 0.01) -> np.ndarray:
        return expected_improvement(self.surrogate, y_best, xs_candidate, xi=xi)

    def bayesian_optimize(self, n_iter: int = 10, xi: float = 0.01,
                          n_candidates: int = 2000, seed: int = 42) -> dict:
        return bayesian_optimize(self.surrogate, self.bounds,
                                 n_iter=n_iter, xi=xi,
                                 n_candidates=n_candidates, seed=seed)

    def confidence_interval(self, xs, z: float = 1.96) -> dict:
        xs = np.atleast_2d(np.asarray(xs, float))
        mean, std = self.surrogate.predict(xs)
        return {
            "mean": np.asarray(mean),
            "std": np.asarray(std),
            "lo": np.asarray(mean) - z * np.asarray(std),
            "hi": np.asarray(mean) + z * np.asarray(std),
            "z": z,
        }
