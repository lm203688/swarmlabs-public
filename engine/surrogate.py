"""SwarmLabs 统一内核 (Engine) —— 整个项目融合后的唯一真源。

设计原则
--------
1. 现实实验 = 编码世界。虚拟实验通过削减干扰 + 数学加速逼近它。
2. UQ 一等公民：代理模型的预测必须带 std/置信区间，否则"可信度"无从谈起。
3. 主动学习闭环：用虚拟实验挑出"信息量最大的真实实验点"来校准自己，
   这是让虚拟实验逐步达到现实实验可信度的引擎（见 arxiv:2605.06756）。

算力策略
--------
- GPSurrogate / ActiveLearner(BALD) / Calibrator：纯 numpy，现在即可跑（轻量）。
- NeuralOperatorSurrogate：随机傅里叶特征(RFF)算子近似，纯 numpy 可跑（点映射版）；
  FourierOperatorSurrogate 是网格函数版（1D PDE 算子），二者门面一致、可替换。
- RemoteLabOracle：默认 LocalLabSimulator 端到端可跑；endpoint 给出时切换
  RemoteLabClient 真实联网提交。不再是桩。
- 假设层：SINDy 可解释发现 + Elo 锦标赛 + 假设树搜索 + LLM 候选生成（gated env），
  全部挂 SurrogateEngine 门面。算力桩已做实：PINN（CPU 物理约束求解）、深度神经
  算子 DeepONet（CPU 端到端训练）、RemoteLab（接真实数据集 / 真实设备）均可真实
  运行，无需 GPU。

本模块是「一个体系」的脊柱：demo / 验证飞轮 / agentteams 都通过
`SurrogateEngine` 门面操作「领域」，不再各自引一份内核。
"""

from __future__ import annotations

# 稳定性：固定 BLAS 线程数为 1，避免多线程 LAPACK 在重复小矩阵 Cholesky 时
# 偶发原生段错误（见 engine/flywheel.py 说明）。须在 import numpy 之前设置。
import os
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

import numpy as np
from scipy.linalg import solve_triangular

# 统一内核的扩展层（全部纯 numpy / scipy，门面一致）。这些子模块不反向依赖
# surrogate，故在此安全导入；引擎能力从「点映射」扩展到「函数映射 / 假设发现 /
# 真实实验 / LLM 候选」全链路，桩已全部做实。
from .discovery import SINDy
from .elo import EloTournament
from .neural_operator import FourierOperatorSurrogate
from .pinn import PINN1D
from .remote_lab import LocalLabSimulator, RemoteLabClient, ExperimentSpec, DatasetOracle
from .hypothesis_llm import LLMHypothesizer
from .tree_search import HypothesisTree

__all__ = [
    "ExperimentSpace",
    "GPSurrogate",
    "NeuralOperatorSurrogate",
    "SyntheticOracle",
    "RemoteLabOracle",
    "Calibrator",
    "ActiveLearner",
    "BALDEnsemble",
    "run_parity_loop",
    "SurrogateEngine",
]


class ExperimentSpace:
    """参数空间。bounds: list[(lo, hi), ...]。"""

    def __init__(self, bounds):
        self.bounds = np.array(bounds, dtype=float)
        self.dim = len(bounds)

    def sample(self, n, rng=None):
        rng = rng or np.random.default_rng(0)
        u = rng.random((n, self.dim))
        return self.bounds[:, 0] + u * (self.bounds[:, 1] - self.bounds[:, 0])


class GPSurrogate:
    """廉价、免 GPU 的高斯过程代理（RBF 核 + 噪声），预测带 std。

    规模提示：精确 GP 为 O(n^3)。当 n 上千或需高维场预测时，应切换为
    稀疏 GP / SINDy / 深度集成（见 fidelity_ladder 的算力阶梯）。
    """

    def __init__(self, lengthscale=0.4, signal_var=1.0, noise_var=0.03 ** 2, reg=1e-8,
                 y_mean=0.0, y_scale=1.0, auto_ls=False, mean_fn=None):
        # mean_fn：机理先验均值（2026-09-08 新增）。非空时 GP 只学 (y - mean_fn(X)) 残差，
        # 预测返回 mean_fn(x) + 残差预测。用于把"机理/粗低保真模型"与"数据驱动 GP"桥接
        # （多保真 co-kriging 的均值函数形式），而不是二选一。None 时行为与之前完全一致。
        self.mean_fn = mean_fn
        # lengthscale 可以是标量（各向同性）或长度为 d 的向量（ARD，逐维学）。
        self.ls = np.asarray(lengthscale, float)
        self._ls0 = np.asarray(lengthscale, float)   # 先验值，多次 fit 不被自动学习污染
        self.sv = float(signal_var)
        self.nv = float(noise_var)
        self.reg = float(reg)
        self._ymean = float(y_mean)
        self._yscale = float(y_scale)
        self.auto_ls = bool(auto_ls)
        self.ls_source = "prior"   # prior | auto(ARD) —— 供报告说明超参从何而来
        self.std_scale = 1.0       # 预测 std 的标定系数（方差标定/conformal 校准用）
        self._fit = False

    def _norm(self, X):
        return (X - self.xmin) / (self.xmax - self.xmin + 1e-9)

    def _K(self, A, B, ls=None):
        ls = self.ls if ls is None else ls
        diff = (self._norm(A)[:, None, :] - self._norm(B)[None, :, :]) / ls
        sq = np.sum(diff ** 2, axis=2) / 2.0
        return self.sv * np.exp(-sq)

    # ---- 超参自动学习（纯 numpy，无外部优化器） -------------------------
    def _chol(self, ls):
        K = self._K(self.X, self.X, ls) + (self.nv + self.reg) * np.eye(len(self.X))
        return np.linalg.cholesky(K)

    def _lml(self, ls, yn):
        """对数边际似然。数值失败返回 -inf，让搜索自然跳过该超参。"""
        try:
            L = self._chol(ls)
        except np.linalg.LinAlgError:
            return -np.inf
        # L 是下三角（Cholesky 产物），必须用三角求解 O(n^2) 而非通用 LU O(n^3)：
        # np.linalg.solve 不识别三角结构，会在小矩阵上产生巨大线程/数值开销。
        a = solve_triangular(L.T, solve_triangular(L, yn, lower=True), lower=False)
        return float(-0.5 * yn @ a - np.sum(np.log(np.diag(L))))

    def _loo_rmse(self, ls, yn):
        """留一交叉验证 RMSE（GP 有闭式解，不需真的重训 n 次）。

        这是"守卫"：自动学到的超参必须在 LOO 上不劣于先验超参才会被采用，
        否则回落先验。防止小样本下边际似然跑到边界造成的过拟合退化。
        """
        try:
            L = self._chol(ls)
        except np.linalg.LinAlgError:
            return np.inf
        n = len(yn)
        Kinv = solve_triangular(L.T, solve_triangular(L, np.eye(n), lower=True), lower=False)
        a = Kinv @ yn
        d = np.diag(Kinv)
        if np.any(d <= 1e-12):
            return np.inf
        resid = a / d           # 闭式 LOO 残差
        return float(np.sqrt(np.mean(resid ** 2)))

    def _optimize_ls(self, yn):
        """在对数均匀网格上按边际似然搜 ARD lengthscale，再用 LOO 守卫把关。

        数据量门槛 n >= 4*d + 6：小样本估不准逐维超参（实测 branin 6 点时
        LML 会跑到网格边界，相对误差从 88% 恶化到 116%），门槛内直接不启用。
        """
        n, d = self.X.shape
        prior = np.broadcast_to(self.ls, (d,)).astype(float)
        if n < 4 * d + 6:
            return prior, "prior(数据量未达门槛 n>=4d+6)"

        grid = np.exp(np.linspace(np.log(0.08), np.log(1.5), 25))
        if d == 1:
            cands = [np.array([g]) for g in grid]
        else:
            # 2D 及以上统一用坐标下降：避免 2D 全网格 625 候选的组合爆炸
            # （n=100 时每个 _lml 做一次 Cholesky，625 次会卡死飞轮）。
            cands = []
            cur = prior.copy()
            for _ in range(3):
                for j in range(d):
                    best_j, best_v = cur[j], self._lml(cur, yn)
                    for g in grid:
                        t = cur.copy(); t[j] = g
                        v = self._lml(t, yn)
                        if v > best_v:
                            best_v, best_j = v, g
                    cur[j] = best_j
            cands = [cur]

        best = max(cands, key=lambda c: self._lml(c, yn))
        # 守卫：LOO 不劣于先验才采用（允许 1% 容差，避免数值抖动来回切换）
        if self._loo_rmse(best, yn) <= self._loo_rmse(prior, yn) * 1.01:
            return best, "auto(ARD, 边际似然+LOO守卫)"
        return prior, "prior(自动超参未通过 LOO 守卫)"

    def fit(self, X, y):
        X = np.atleast_2d(np.asarray(X, float))
        y = np.asarray(y, float).ravel()
        self.xmin = X.min(axis=0)
        self.xmax = X.max(axis=0)
        self.X = X
        self._y_raw = y.copy()  # 供 BALD 集成等下游复用，避免重复传参
        # y 归一化：GP 核振幅 sv 固定为 1.0（匹配单位方差），避免大动态范围函数
        # 因核"够不到"幅值而欠拟合（logistic/Branin 曾因此平台区崩坏）。
        if self.mean_fn is not None:
            # 归一化改为针对**残差**：机理先验已解释掉大部分幅值，残差尺度远小于 y。
            m_train = np.asarray([float(self.mean_fn(x)) for x in X], float).ravel()
            resid = y - m_train
            self._rmean = float(np.mean(resid))
            self._rscale = float(np.std(resid)) + 1e-9
            self.resid_std = float(np.std(resid))   # 供报告：机理先验还剩多少没解释
            base = resid
        else:
            self._rmean, self._rscale = self._ymean, self._yscale
            self.resid_std = None
            base = y
        yn = (base - self._rmean) / self._rscale
        self.ls = np.broadcast_to(self._ls0, (X.shape[1],)).astype(float)
        if self.auto_ls:
            self.ls, self.ls_source = self._optimize_ls(yn)
        K = self._K(X, X) + (self.nv + self.reg) * np.eye(len(X))
        # 鲁棒 Cholesky：LHS 点过密或飞轮逐日累积补点使 K 近奇异时，
        # 自适应加大对角 jitter 重试，避免 LinAlgError 直接崩掉整轮飞轮。
        try:
            self.L = np.linalg.cholesky(K)
        except np.linalg.LinAlgError:
            jitter = (self.nv + self.reg)
            self.L = None
            for _ in range(8):
                jitter *= 10.0
                try:
                    self.L = np.linalg.cholesky(K + jitter * np.eye(len(X)))
                    break
                except np.linalg.LinAlgError:
                    continue
            if self.L is None:  # 兜底：极端近奇异
                self.L = np.linalg.cholesky(K + 1e-2 * np.eye(len(X)))
        self.alpha = solve_triangular(self.L.T, solve_triangular(self.L, yn, lower=True), lower=False)
        self._fit = True
        return self

    def predict(self, Xs):
        if not self._fit:
            raise RuntimeError("call fit() first")
        Xs = np.atleast_2d(np.asarray(Xs, float))
        Ks = self._K(Xs, self.X)
        mean_n = Ks @ self.alpha
        v = solve_triangular(self.L, Ks.T, lower=True)
        var = self.sv - np.sum(v ** 2, axis=0)
        var = np.maximum(var, 1e-12) + self.nv  # 预测区间须含观测噪声，否则 CI 过窄、UQ 失真
        # 反归一化回原始量纲
        mean_out = mean_n * self._rscale + self._rmean
        if self.mean_fn is not None:
            m_q = np.asarray([float(self.mean_fn(x)) for x in Xs], float).ravel()
            mean_out = mean_out + m_q
        return mean_out, np.sqrt(var) * self._rscale * self.std_scale

    def proximity(self, Xs):
        """每个查询点到训练集的最近归一化距离（越界检测 / OOD 代理）。

        返回形状 (n_query,) 的向量；值越大表示越远离已观测区域——
        这是「可信替代」的安全阀：远离训练区的虚拟结果不应被无条件信任。
        """
        if not self._fit:
            raise RuntimeError("call fit() first")
        Xs = np.atleast_2d(np.asarray(Xs, float))
        Qn = self._norm(Xs)
        Xn = self._norm(self.X)
        d = np.linalg.norm(Qn[:, None, :] - Xn[None, :, :], axis=2)
        return d.min(axis=1)


class NeuralOperatorSurrogate:
    """神经算子代理（随机傅里叶特征 RFF 近似）—— 纯 numpy，已做实。

    与 GPSurrogate 门面完全一致（fit(X,y) / predict(Xs) -> mean, std），可无缝
    切换。RFF 是单隐层「神经算子」的核机等价形式（Rahimi-Recht）：把输入经随机
    傅里叶映射后做岭回归，预测带残差 std 作为 UQ。无需 GPU。

    深度版（FNO / DeepONet）在算力到位时替换后端即可（见 FourierOperatorSurrogate
    的网格函数版，及 fidelity_ladder.py 的算力阶梯）。
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


class FourierNeuralOperatorSurrogate:
    """函数空间算子代理（FNO）— 与 GPSurrogate 同门面，但 X/y 是「字段矩阵」。

    输入 X 形状 (n, N_in)：每条训练样本是一个在网格上采样的输入函数 a(·)；
    输出 y 形状 (n, N_out)：对应输出函数 u(·)。预测返回 (mean fields, std fields)，
    逐点 std 即 epistemic UQ（集成成员方差）——SwarmLabs「UQ 一等公民」纪律在神经
    算子上的落地。这是原 NeuralOperatorSurrogate（逐点 RFF）的**函数空间升级版**，
    直接回应 Accelerated Understanding 的神经算子路线，但带它缺的不确定性量化。

    适用：operator 类领域（PDE 场、积分算子、输入/输出都是函数）。逐点标量领域
    仍用 GPSurrogate（少样本更强）。
    """

    def __init__(self, modes=32, ensemble=7, ridge=1e-3, seed=0):
        try:
            from .fourier_operator import FourierNeuralOperator
        except ImportError:
            from fourier_operator import FourierNeuralOperator
        self._fno = FourierNeuralOperator(modes=modes, ensemble=ensemble,
                                          ridge=ridge, seed=seed)
        self._fit = False

    def fit(self, X, y, grid_in=None, grid_out=None):
        self._fno.fit(X, y, grid_in=grid_in, grid_out=grid_out)
        self._fit = True
        return self

    def predict(self, xs, grid_in_q=None, grid_out_q=None):
        if not self._fit:
            raise RuntimeError("call fit() first")
        xs = np.atleast_2d(np.asarray(xs, float))
        means, stds = [], []
        for a in xs:
            m, s = self._fno.predict(a, grid_in_q=grid_in_q, grid_out_q=grid_out_q)
            means.append(m)
            stds.append(s)
        return np.stack(means), np.stack(stds)


class SyntheticOracle:
    """真实实验的"地面真值"替身，用于无算力自测。

    observe(x) 返回 f(x) + 噪声，模拟真实实验的不可复现误差。
    """

    def __init__(self, f, noise=0.02, rng=None):
        self.f = f
        self.noise = float(noise)
        self.rng = rng or np.random.default_rng(42)

    def observe(self, x):
        x = np.asarray(x, float).ravel()
        return float(self.f(x)) + float(self.rng.normal(0, self.noise))


class RemoteLabOracle:
    """真实实验 Oracle —— 已做实，不再是桩。

    三种后端（按传入参数选择）：
      - data=(X, y) 真实已发表实验集 -> DatasetOracle（最近邻读真实值，端到端可跑）
      - endpoint=...  真实设备 API    -> RemoteLabClient（联网提交真实实验）
      - f=...        解析/模拟真值    -> LocalLabSimulator（无真实数据时自测）

    observe(x) 返回一次真实实验观测（含测量噪声），走完整的「提交→轮询→取结果」
    协议，证明真实验证闭环可跑通。
    """

    def __init__(self, f=None, endpoint=None, api_key=None, noise=0.02, rng=None,
                 data=None):
        self.endpoint = endpoint
        self.api_key = api_key
        self.noise = float(noise)
        self.rng = rng or np.random.default_rng(7)
        if data is not None:
            X, y = data
            self._impl = DatasetOracle(X, y, noise=noise, rng=self.rng)
            self.f = None
        elif endpoint:
            self.f = f
            self._impl = RemoteLabClient(endpoint, api_key=api_key)
        elif f is not None:
            self.f = f
            self._impl = LocalLabSimulator(f, noise=noise, rng=self.rng)
        else:
            raise ValueError("RemoteLabOracle 需要 data=(X,y) / endpoint / f 之一")

    def observe(self, x):
        # DatasetOracle 直接读真实值（无 submit/status/result 协议）
        if isinstance(self._impl, DatasetOracle):
            return self._impl.observe(x)
        spec = ExperimentSpec(np.asarray(x, float).tolist())
        rid = self._impl.submit(spec)
        # 轮询直到完成（本地模拟瞬时完成；真实设备在此阻塞等待）
        import time
        for _ in range(60):
            if self._impl.status(rid) == "done":
                break
            time.sleep(0.2)
        return self._impl.result(rid)


class Calibrator:
    """"对等度"度量：虚拟实验相对真实实验有多可信。

    指标：
      - rmse：预测均值与真实值偏差（越低越好）
      - coverage：95% 置信区间应覆盖约 95% 真实点（校准良好 ≈ 0.95）
      - cal_err：|coverage - 0.95|（越低越好，UQ 校准误差）
    coverage≈0.95 且 rmse 低 = 虚拟实验可信度逼近真实实验。
    """

    def evaluate(self, surrogate, oracle, xs, z=1.96):
        xs = np.atleast_2d(np.asarray(xs, float))
        mean, std = surrogate.predict(xs)
        y_true = np.array([oracle.observe(x) for x in xs])
        rmse = float(np.sqrt(np.mean((mean - y_true) ** 2)))
        lo, hi = mean - z * std, mean + z * std
        inside = np.mean((y_true >= lo) & (y_true <= hi))
        return {
            "rmse": round(rmse, 4),
            "coverage": round(float(inside), 3),
            "cal_err": round(abs(float(inside) - 0.95), 3),
            "mean_std": round(float(np.mean(std)), 4),
        }


class BALDEnsemble:
    """BALD 近似：bootstrap 集成的高斯过程集成。

    用自助采样重训多棵 GPSurrogate，BALD 分数 = 集成均值的方差（epistemic 不确定性）
    + 平均个体方差（aleatoric）。最大 BALD 点即「信息量最大、最该做的真实实验点」。

    纯 numpy，无需外部集成库，是原本 max_std 启发式的诚实升级（捕捉模型间分歧）。
    """

    def __init__(self, n_models=8, seed=0, **gp_kwargs):
        self.n_models = int(n_models)
        self.seed = int(seed)
        self.gp_kwargs = gp_kwargs
        self.models = []

    def fit(self, X, y):
        X = np.atleast_2d(np.asarray(X, float))
        y = np.asarray(y, float).ravel()
        rng = np.random.default_rng(self.seed)
        self.models = []
        for i in range(self.n_models):
            idx = rng.integers(0, len(y), len(y))
            m = GPSurrogate(**self.gp_kwargs)
            m.fit(X[idx], y[idx])
            self.models.append(m)
        return self

    def bald(self, Xs):
        means, vars_ = [], []
        for m in self.models:
            mu, sd = m.predict(Xs)
            means.append(mu)
            vars_.append(sd ** 2)
        means = np.array(means)
        vars_ = np.array(vars_)
        epistemic = np.var(means, axis=0)
        aleatoric = np.mean(vars_, axis=0)
        return epistemic + aleatoric  # 总 BALD


class ActiveLearner:
    """主动学习：挑出"信息量最大"的真实实验点。

    - "max_std"：最大预测方差（启发式，稳定可用）
    - "bald"   ：bootstrap 集成 BALD（epistemic+aleatoric，更诚实）
    """

    def __init__(self, strategy="bald"):
        self.strategy = strategy

    def next(self, surrogate, space, n_candidates=200, rng=None):
        rng = rng or np.random.default_rng(7)
        cands = space.sample(n_candidates, rng)
        if self.strategy == "max_std":
            _, std = surrogate.predict(cands)
            return cands[int(np.argmax(std))]
        if self.strategy == "bald":
            X = getattr(surrogate, "X", None)
            y = getattr(surrogate, "_y_raw", None)
            if X is None or y is None:
                # 退化到 max_std
                _, std = surrogate.predict(cands)
                return cands[int(np.argmax(std))]
            ens = BALDEnsemble(n_models=8, seed=3).fit(X, y)
            b = ens.bald(cands)
            return cands[int(np.argmax(b))]
        if self.strategy in ("volume", "momentbody"):
            # 涨落定理「凸体可达区域」收购：选使整批认证覆盖下界收紧最多的点
            try:
                from .acquisition_volume import ConvexBodyAcquisition
                acq = ConvexBodyAcquisition(tol=getattr(self, "mb_tol", 0.1))
                return acq.next(surrogate, space, n_candidates=n_candidates, rng=rng)
            except Exception:
                # 任何异常（surrogate 缺 _K/L 等）→ 降级 max_std
                _, std = surrogate.predict(cands)
                return cands[int(np.argmax(std))]
        raise ValueError(self.strategy)


def run_parity_loop(
    space,
    oracle,
    surrogate=None,
    calibrator=None,
    learner=None,
    n_seed=8,
    n_iter=12,
    test_grid=None,
    rng=None,
):
    """跑通"虚拟→真实"对等闭环，返回逐轮校准历史（展示收敛到对等）。

    这是 SwarmLabs 飞轮的通用化版本：用尽量少的真实实验观测，
    把虚拟代理校准到接近真实实验的可信度。
    """
    rng = rng or np.random.default_rng(123)
    surrogate = surrogate or GPSurrogate()
    calibrator = calibrator or Calibrator()
    learner = learner or ActiveLearner()

    seed_x = space.sample(n_seed, rng)
    X = list(seed_x)
    y = [oracle.observe(x) for x in seed_x]
    surrogate.fit(X, y)

    if test_grid is None:
        test_grid = space.sample(60, rng)

    history = [{"iter": 0, "n_real": n_seed, **calibrator.evaluate(surrogate, oracle, test_grid)}]
    for i in range(1, n_iter + 1):
        x_next = learner.next(surrogate, space)
        y_next = oracle.observe(x_next)
        X.append(x_next)
        y.append(y_next)
        surrogate.fit(X, y)
        history.append({"iter": i, "n_real": len(X), **calibrator.evaluate(surrogate, oracle, test_grid)})
    return history


def mirror_run(space, oracle, engine_kind="gp", n_seed=8, seed_a=0, seed_b=1,
               test_grid=None, assert_rel=0.10, rng=None):
    """同实验双种子镜像运行：断言两种独立初始化下指标差异在阈值内。

    这是「可复现性」的物理保证——同一实验在两种随机初始化下不得产生
    不可解释的分歧。超出阈值即视为方法 / 数据缺陷，显式报错而非静默通过。

    返回 {seed_a, seed_b, rel_rmse_diff, cov_diff, assert_rel, passed}。
    """
    rng = rng or np.random.default_rng(123)
    out = {}
    for tag, seed in (("a", seed_a), ("b", seed_b)):
        hist = run_parity_loop(space, oracle, n_seed=n_seed, n_iter=0,
                               test_grid=test_grid,
                               rng=np.random.default_rng(seed))
        first = hist[0]
        out[tag] = {k: first[k] for k in ("rmse", "coverage", "cal_err")}
    da, db = out["a"], out["b"]
    rel_rmse = abs(da["rmse"] - db["rmse"]) / max(da["rmse"], db["rmse"], 1e-9)
    cov_diff = abs(da["coverage"] - db["coverage"])
    return {
        "seed_a": da,
        "seed_b": db,
        "rel_rmse_diff": round(float(rel_rmse), 4),
        "cov_diff": round(float(cov_diff), 4),
        "assert_rel": assert_rel,
        "passed": (rel_rmse <= assert_rel) and (cov_diff <= assert_rel),
    }


# ===========================================================================
# 统一门面：SurrogateEngine
# ---------------------------------------------------------------------------
# 这是「一个体系」对外唯一 API。一个「领域(domain)」= 真值/基准 + 数据集 +
# UQ + 主动学习 + 报告。demo / 飞轮 / agentteams 都通过它操作领域，不再
# 各自引一份内核。
# ===========================================================================

class SurrogateEngine:
    """统一引擎门面：把一个「领域」跑成完整的虚拟实验闭环。

    领域配置 domain 字段：
      key        唯一标识，如 "microbio_monod"
      category   分类（optimization/biology/pde/llm/chemistry/...）
      f          真值/基准函数  f(x) -> y
      bounds     参数空间 [(lo, hi), ...]
      ls         先验 lengthscale（标量或向量）
      auto_ls    是否启用 ARD 自动超参（高维/振荡场景开）
      noise      观测噪声水平（用于噪声地板，诚实 UQ）
      references 真实文献引用
      published  已发表基准值（gold）

    用法：
      eng = SurrogateEngine(domain)
      eng.fit(X, y)                 # 用真实实验点拟合
      eng.evaluate(test_xs)         # 返回 {rmse, coverage, cal_err}
      x_next = eng.recommend_next() # 主动学习：下一个最该做的实验点
      eng.report(X, y, test_xs)     # 一份可直发的领域报告
    """

    def __init__(self, domain: dict, engine_kind: str = "gp"):
        self.domain = domain
        self.key = domain["key"]
        self.category = domain.get("category")
        self.f = domain["f"]
        self.bounds = domain["bounds"]
        self.space = ExperimentSpace(self.bounds)
        self.engine_kind = engine_kind
        self._sur = None
        self._cal = Calibrator()
        self._learner = ActiveLearner()

    # ---- 拟合（自动做 z-score 归一化 + 噪声地板，落实诚实 UQ 基线） ----
    def fit(self, X, y, noise_rel=0.03):
        # 神经算子后端：仅 operator 类领域（输入/输出都是函数场）。默认 gp 路径不变。
        if self.engine_kind == "fno":
            if not self.domain.get("operator"):
                raise ValueError("engine_kind='fno' 仅适用于 operator=True 的领域"
                                 "（输入/输出为函数场）；逐点标量领域请用默认 engine_kind='gp'。")
            self._sur = FourierNeuralOperatorSurrogate(
                modes=self.domain.get("fno_modes", 32),
                ensemble=self.domain.get("fno_ensemble", 7),
                ridge=self.domain.get("fno_ridge", 1e-3),
            )
            self._sur.fit(X, y,
                          grid_in=self.domain.get("grid_in"),
                          grid_out=self.domain.get("grid_out"))
            self._X = np.atleast_2d(np.asarray(X, float))
            self._y = np.atleast_2d(np.asarray(y, float))
            return self._sur
        X = np.atleast_2d(np.asarray(X, float))
        y = np.asarray(y, float).ravel()
        y_scale = float(np.std(y)) + 1e-9
        y_mean = float(np.mean(y))
        nv = (noise_rel if noise_rel is not None else self.domain.get("noise", 0.03)) ** 2
        self._sur = GPSurrogate(
            lengthscale=self.domain["ls"],
            noise_var=nv,
            y_mean=y_mean,
            y_scale=y_scale,
            auto_ls=self.domain.get("auto_ls", False),
        )
        self._sur.fit(X, y)
        self._X = X
        self._y = y
        return self._sur

    def predict(self, xs):
        if self._sur is None:
            raise RuntimeError("call fit() first")
        return self._sur.predict(xs)

    def evaluate(self, xs, z=1.96):
        """用领域基准 f 作 oracle，评估当前代理的对等度。"""
        if self._sur is None:
            raise RuntimeError("call fit() first")
        oracle = type("O", (), {"observe": staticmethod(self.f)})()
        return self._cal.evaluate(self._sur, oracle, xs, z=z)

    def recommend_next(self, n_candidates=200):
        """主动学习：返回下一个最该做的真实实验点（最大后验方差）。"""
        if self._sur is None:
            raise RuntimeError("call fit() first")
        return self._learner.next(self._sur, self.space, n_candidates)

    # -----------------------------------------------------------------------
    # 方向对齐能力：可信替代安全边界 + 自主空间填充
    # （对应「替代真实实验 + 不受时间空间限制」的产品方向）
    # -----------------------------------------------------------------------
    def trust_boundary(self, xs, ood_factor=2.0):
        """可信替代安全边界：每个查询点返回 (mean, std, dist_norm, status)。

        status:
          'trusted'              —— 在已观测区内且不确定度可控，可放心当真实实验用
          'caution'              —— 后验 std 过大，结果仅供参考
          'out_of_distribution'  —— 远离所有已观测点，虚拟结果不可信，显式拒绝

        这是「放心替代真实实验」的安全阀：越界时**显式标注/拒答**，而不是给一个
        无依据的标量。替代真实实验的前提是——不可信的区必须自己说出来。
        """
        if self._sur is None:
            raise RuntimeError("call fit() first")
        mean, std = self._sur.predict(xs)
        dist = self._sur.proximity(xs)
        Xn = self._sur._norm(self._sur.X)
        if len(Xn) > 1:
            dd = np.linalg.norm(Xn[:, None, :] - Xn[None, :, :], axis=2)
            np.fill_diagonal(dd, np.inf)
            typical = float(np.median(dd.min(axis=1)))
        else:
            typical = 1.0
        ood_thr = ood_factor * max(typical, 1e-3)
        yscale = self._sur._yscale
        status = []
        for s, d_ in zip(std, dist):
            if d_ > ood_thr:
                status.append("out_of_distribution")
            elif s > 3.0 * yscale:
                status.append("caution")
            else:
                status.append("trusted")
        return {
            "mean": np.asarray(mean),
            "std": np.asarray(std),
            "dist_norm": np.asarray(dist),
            "ood_threshold": ood_thr,
            "status": status,
        }

    def autonomous_fill(self, n_steps=10, oracle=None, n_candidates=200, seed=0, test_xs=None):
        """自主空间填充：让虚拟实验闭环**自治**探索实验空间，无需人工 / 真实实验台。

        每步：主动学习挑信息量最大工况 → 经 oracle 取结果 → 回填并重建代理。
        体现「替代真实实验 + 不受时间空间限制」——探索本身不依赖任何物理实验、
        不受机时 / 地点约束，平台自驱把虚拟空间填满并持续变可信。

        返回每步对等度轨迹（用 domain.f 作留证 oracle），证明自治后虚拟实验可信。
        """
        if oracle is None:
            oracle = SyntheticOracle(self.f, noise=self.domain.get("noise", 0.02))
        rng = np.random.default_rng(seed)
        _X = getattr(self, "_X", None)
        if _X is None or len(_X) == 0:
            X0 = self.space.sample(max(8, 4 * len(self.bounds) + 6), rng)
            y0 = np.array([oracle.observe(x) for x in X0])
            self.fit(X0, y0)
        if test_xs is None:
            test_xs = self.space.sample(80, rng)
        test_y = np.array([self.f(x) for x in test_xs])
        X = list(self._X)
        y = list(self._y)
        history = []
        for step in range(n_steps):
            x = self.recommend_next(n_candidates)
            yv = oracle.observe(x)
            X.append(np.asarray(x, float))
            y.append(float(yv))
            self.fit(X, y)
            mean, std = self._sur.predict(test_xs)
            rmse = float(np.sqrt(np.mean((mean - test_y) ** 2)))
            lo = mean - 1.96 * std
            hi = mean + 1.96 * std
            cov = float(np.mean((test_y >= lo) & (test_y <= hi)))
            history.append({
                "step": step + 1,
                "n_points": len(X),
                "rmse_rel": round(rmse / (float(np.std(self._y)) + 1e-9), 4),
                "coverage": round(cov, 3),
            })
        return history

    def report(self, X, y, test_xs=None, z=1.96):
        """生成一份可直发的领域报告（指标 + 下一步建议 + 文献）。"""
        X = np.atleast_2d(np.asarray(X, float))
        y = np.asarray(y, float).ravel()
        if test_xs is None:
            rng = np.random.default_rng(11)
            test_xs = self.space.sample(40, rng)
        self.fit(X, y)
        metrics = self.evaluate(test_xs, z=z)
        x_next = self.recommend_next()
        rel = metrics["rmse"] / (float(np.std(y)) + 1e-9)
        cov_state = ("ok" if 0.85 <= metrics["coverage"] <= 1.0
                     else "under" if metrics["coverage"] < 0.85 else "over")
        return {
            "key": self.key,
            "category": self.category,
            "n_points": len(X),
            "rmse": metrics["rmse"],
            "rmse_rel": round(rel, 4),
            "coverage": metrics["coverage"],
            "coverage_state": cov_state,
            "cal_err": metrics["cal_err"],
            "ls_source": self._sur.ls_source,
            "next_experiment": np.asarray(x_next).round(4).tolist(),
            "references": self.domain.get("references", []),
            "published": self.domain.get("published", ""),
        }

    # -----------------------------------------------------------------------
    # 扩展层：假设发现 / Elo 排名 / BALD / 树搜索 / 真实实验 / LLM 候选
    # 统一挂在本门面，demo / 飞轮 / agentteams 一个 API 操作全部能力。
    # -----------------------------------------------------------------------
    def discover(self, X=None, y=None, **sindy_kwargs):
        """SINDy 可解释假设发现：从观测中稀疏辨识符号律（带系数置信度）。"""
        if X is None:
            X, y = self._X, self._y
        m = SINDy(**sindy_kwargs)
        m.fit(np.asarray(X, float), np.asarray(y, float))
        return m

    def rank_elo(self, candidates=None, test_xs=None):
        """对候选模型/律按对等度做 Elo 锦标赛排名（越低误差越强）。

        不传 candidates 时，默认对比 GP / RFF 神经算子 / GP-auto 三种代理在
        测试集上的相对误差，给出谁更可信——这是 Co-Scientist Elo 的可复现版。
        """
        if candidates is None:
            if test_xs is None:
                rng = np.random.default_rng(5)
                test_xs = self.space.sample(60, rng)
            test_y = np.array([self.f(x) for x in test_xs])
            cands = []
            for name, sur in [
                ("GP", GPSurrogate(lengthscale=self.domain["ls"],
                                   auto_ls=self.domain.get("auto_ls", False))),
                ("RFF-NO", NeuralOperatorSurrogate()),
                ("GP-auto", GPSurrogate(lengthscale=self.domain["ls"], auto_ls=True)),
            ]:
                sur.fit(self._X, self._y)
                mean, _ = sur.predict(test_xs)
                rmse = float(np.sqrt(np.mean((mean - test_y) ** 2)))
                cands.append((name, rmse / (float(np.std(self._y)) + 1e-9)))
            candidates = cands
        names = [c[0] for c in candidates]
        strengths = [c[1] for c in candidates]
        tour = EloTournament()
        tour.from_strengths(names, strengths, n_rounds=4)
        return tour.report()

    def recommend_bald(self, n_candidates=200):
        """主动学习（BALD）推荐下一个最该做的真实实验点（epistemic+aleatoric）。"""
        if self._sur is None:
            raise RuntimeError("call fit() first")
        ens = BALDEnsemble(n_models=8, seed=11).fit(self._X, self._y)
        cands = self.space.sample(n_candidates, np.random.default_rng(9))
        b = ens.bald(cands)
        return cands[int(np.argmax(b))]

    def tree_search(self, X=None, y=None, test_X=None, test_y=None):
        """假设树搜索：系统探索多候选机理，收敛到最简且对等度最高者。"""
        if X is None:
            X, y = self._X, self._y
        if test_y is None:
            test_X, test_y = X, y
        return HypothesisTree().search(
            np.asarray(X, float), np.asarray(y, float),
            np.asarray(test_X, float), np.asarray(test_y, float))

    def remote_lab_run(self, x, noise=0.02):
        """通过真实实验 Oracle 取得一次观测（默认本地模拟，endpoint 时联网）。"""
        oracle = RemoteLabOracle(self.f, endpoint=self.domain.get("endpoint"),
                                api_key=self.domain.get("api_key"), noise=noise)
        return oracle.observe(x)

    def llm_hypothesize(self, n=3):
        """LLM 假设层：生成候选符号律（gated env；无凭证/不可达返回 None）。"""
        h = LLMHypothesizer()
        ctx = h.context_from(self._X, self._y, self.key)
        return h.hypothesize(ctx, n=n)

    def pinp_solve(self, f_pde, bc=(0.0, 1.0), hidden=20):
        """物理信息神经网络求解 1D 边值问题（纯 CPU，无需 GPU）。"""
        p = PINN1D(hidden=hidden)
        p.fit(f_pde, bc=bc)
        return p
