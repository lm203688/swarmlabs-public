"""显式多保真度接口（P1-D）—— Kennedy-O'Hagan 自回归 cokriging，字段原生 + UQ。

为什么要它（硬实力缺口）：
  FNO 给了「分辨率无关」（粗网格训练→细网格查询）。但「多保真度」是另一回事：
  科研里常有**廉价低保真**仿真（粗网格 / 代理模型 / 历史数据）与**昂贵高保真**
  仿真（细网格 / 真实实验）并存。诚实的多保真度 = 用大量便宜 LF 数据铺广度、
  用少量昂贵 HF 数据修精度——正是 AU 暗示但没证明的「Super-resolution / 多保真度」。

设计（诚实、可复现、CPU 可跑）：
  - 输入函数 a(·) 经 PCA 投影到低维 latent z(a)（字段原生，不依赖网格点数）。
  - 低保真代理：用大量 LF 样本拟合  z → u_lf 场（逐输出坐标 GP）。
  - 残差场：r = u_hf − u_lf(在 HF 网格)，用少量 HF 样本拟合  z → r 场（逐坐标 GP）。
  - 预测：û(a) = u_lf(a) + r(a)；UQ = 残差 GP 的 epistemic + 校准地板（我们的纪律）。

接口：
  mf = MultiFidelitySurrogate(n_latent=6)
  mf.fit(A_lf, U_lf, A_hf, U_hf)        # LF/HF 都是 (n, N_in)/(n, N_out) 字段矩阵
  u_mean, u_std = mf.predict(A_q)        # A_q (m, N_in) → 输出场均值与不确定度
"""

from __future__ import annotations
from typing import Dict

import numpy as np
from scipy.interpolate import griddata

from .surrogate import GPSurrogate


class MultiFidelitySurrogate:
    def __init__(self, n_latent: int = 6, ls: float = 0.6, noise: float = 0.01,
                 seed: int = 0):
        self.n_latent = int(n_latent)
        self.ls = float(ls)
        self.nv = float(noise) ** 2
        self.seed = int(seed)
        self._fit = False

    # ---- latent 投影（PCA over 输入函数场） ------------------------------
    def _build_latent(self, A_all):
        A_all = np.atleast_2d(np.asarray(A_all, float))
        self._amean = A_all.mean(axis=0)
        Ac = A_all - self._amean
        # SVD 取前 n_latent 主成分（ covarianceless 简化：直接对样本矩阵做 SVD）
        U, S, Vt = np.linalg.svd(Ac, full_matrices=False)
        k = min(self.n_latent, Vt.shape[0])
        self._comps = Vt[:k].T                       # (N_in, k)
        self._k = k
        return Ac @ self._comps                      # (n, k) latent

    def _project(self, A):
        A = np.atleast_2d(np.asarray(A, float))
        return (A - self._amean) @ self._comps       # (m, k)

    # ---- 拟合 ----------------------------------------------------------
    def fit(self, A_lf, U_lf, A_hf, U_hf):
        A_lf = np.atleast_2d(np.asarray(A_lf, float))
        U_lf = np.atleast_2d(np.asarray(U_lf, float))
        A_hf = np.atleast_2d(np.asarray(A_hf, float))
        U_hf = np.atleast_2d(np.asarray(U_hf, float))
        if A_lf.shape[1] != A_hf.shape[1]:
            raise ValueError("A_lf 与 A_hf 的输入维度必须一致")
        if U_lf.shape[1] != U_hf.shape[1]:
            raise ValueError("U_lf 与 U_hf 的输出网格点数必须一致")
        self.N_out = U_lf.shape[1]
        self._calib = 0.0  # 占位；下面用 HF 训练集 RMSE 重算校准地板

        # 1) latent：用全部输入（LF+HF）建投影，避免分布偏移
        Z_all = self._build_latent(np.vstack([A_lf, A_hf]))
        n_lf = len(A_lf)
        Z_lf, Z_hf = Z_all[:n_lf], Z_all[n_lf:]

        # 2) LF 代理：逐输出坐标 GP（用大量 LF 样本）
        self._lf_gps = []
        for j in range(self.N_out):
            gp = GPSurrogate(lengthscale=self.ls, noise_var=self.nv, auto_ls=True)
            gp.fit(Z_lf, U_lf[:, j])
            self._lf_gps.append(gp)

        # 3) 残差场：r = u_hf − u_lf(在 HF 输入处)，逐坐标 GP（用少量 HF 样本）
        U_lf_at_hf = np.stack([gp.predict(Z_hf)[0].ravel() for gp in self._lf_gps], axis=1)
        R = U_hf - U_lf_at_hf                                   # (n_hf, N_out)
        self._res_gps = []
        for j in range(self.N_out):
            gp = GPSurrogate(lengthscale=self.ls, noise_var=self.nv, auto_ls=True)
            gp.fit(Z_hf, R[:, j])
            self._res_gps.append(gp)

        # 4) 校准地板：MF 在 HF 训练集上的 RMSE → 模型误差不确定度下限
        u_mean_hf, _ = self._raw_predict(Z_hf)
        errs = np.sqrt(np.mean((u_mean_hf - U_hf) ** 2, axis=1))
        self._calib = float(np.mean(errs)) + 1e-9
        self._U_hf = U_hf
        self._fit = True
        return self

    def _raw_predict(self, Z):
        Z = np.atleast_2d(np.asarray(Z, float))
        lf = np.stack([gp.predict(Z)[0].ravel() for gp in self._lf_gps], axis=1)   # (m, N_out)
        res_mean = np.stack([gp.predict(Z)[0].ravel() for gp in self._res_gps], axis=1)
        res_std = np.stack([gp.predict(Z)[1].ravel() for gp in self._res_gps], axis=1)
        u_mean = lf + res_mean
        u_std = np.sqrt(res_std ** 2 + self._calib ** 2)
        return u_mean, u_std

    # ---- 预测 ----------------------------------------------------------
    def predict(self, A_q):
        if not self._fit:
            raise RuntimeError("call fit() first")
        if np.asarray(A_q, float).ndim == 1:
            A_q = np.asarray(A_q, float).reshape(1, -1)
        Z = self._project(A_q)
        return self._raw_predict(Z)

    # ---- 对等度评估（与 FNO/Calibrator 同构，便于进仪表盘） --------------
    def evaluate(self, oracle, A_test, z: float = 1.96):
        """oracle(a) → u_true (1d 场)；A_test (k, N_in)。"""
        A_test = np.atleast_2d(np.asarray(A_test, float))
        errs, ins, stds = [], [], []
        for a in A_test:
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


# ===========================================================================
# 机理先验均值函数门控（co-kriging 均值函数形式，2026-09-08）
# ---------------------------------------------------------------------------
# 与上面的字段级 MultiFidelitySurrogate 是**两种不同**的多保真度：
#   - MultiFidelitySurrogate：字段原生 cokriging（P1-D，需 LF/HF 成对场数据）；
#   - 下面这套：把「粗网格低保真机理模型」作 GP 的**先验均值**，GP 只学残差
#     （多保真 co-kriging 的均值函数形式）。适用于标量 y 的跨域/微生物场景，
#     且用 K 折 OOF 双判据门控自适应决定采纳与否。
#
# 关键纪律（实测，非假设）：
#   低保真先验**不是普适良药**——对平滑动力学大幅改善（relRMSE 中位 -71%），
#   但对 DoE/MLE/Sobol/UQ 类统计推断场景会造成灾难退化（relRMSE 1.2 → 37）。
#   因此必须门控：预测误差更优 **且** 区间校准不降级，才采纳；否则 GP 行为不变。
#
# 确定性：低保真点固定调用 oracle f；OOF 用种子置换；两次运行零点 drift。
# ===========================================================================

DEFAULT_COV_TOL = 0.02      # 门控允许的 OOF 覆盖率下降上限

#: 生产口径的 coverage 双侧达标区下沿（与 agentteams.core.obligations
#: 的 coverage_two_sided_ok 判据、engine/verification_gate 的 COVERAGE_PASS 同源）。
#: 门控新增这条硬约束的原因（2026-10-03 实测）：均值函数改变 GP 预测分布后，
#: epistemic std 的可靠性会连带变化。只看"相对不降级 2%"会放过
#: "原本达标(0.95)→跌到 0.75"这类**绝对不达标**的情形，实测在
#: micro_lag_ecoli / micro_quorum_ecoli 等 4 个场景上确实发生。
#: 校准纪律要求：采纳不得让场景从"绝对达标"跌出"绝对达标"。
COVERAGE_OK_FLOOR = 0.85

#: 跨数量级阈值：bounds 的 hi/lo 超过此值即改用对数网格。
#: 依据（2026-10-03 实测，见 docs/research/nonstationary-multifidelity-20261003.md）：
#: 失分场景集中在微生物 ODE，bounds 跨 3.7~4.0 个数量级（如 [0.001, 10]）。
#: 均匀网格在这种区间上会把 ~99% 的采样点挤在高值区，低值区完全空白，
#: 插值出的"低保真先验"反而污染 GP 均值函数 → rmse 恶化。
LOG_SPAN_THRESHOLD = 2.0    # log10(hi/lo) > 2 → 对数网格

#: 局部极小数量上限：超过此值判为「强多模态」，禁用网格插值先验。
#:
#: 依据（2026-10-04 实测，见 docs/research/multimodal-prior-20261004.md）：
#: `make_lowfi` 用**规则网格 + 线性插值**（1D `np.interp` / 2D `griddata`）
#: 构造机理先验。规则网格隐含假设「f 在网格间距内近似线性」。对多模态
#: 目标函数（Rastrigin 121 极小 / Ackley 205 极小）这个假设在极小点之间
#: 严重不成立——插值会把不同极小「连成」虚假斜坡，GP 随后去学这个
#: 虚假结构，残差学习被带偏。
#:
#: 实测对照（局部极小数由 `count_local_minima` 在 41×41 网格上数出）：
#: ┌─────────────────────────┬────────────┬──────────────┐
#: │ 场景                    │ 局部极小   │ rmse_rel     │
#: ├─────────────────────────┼────────────┼──────────────┤
#: │ opt_rastrigin2d         │ 121        │ 0.2571 失分  │
#: │ opt_ackley2d            │ 205        │ 0.1595 失分  │
#: │ micro_chemostat_cer.    │ 270        │ 0.4514 失分  │
#: │ opt_michalewicz2d       │ 2          │ 0.0927 失分轻│
#: │ micro_competition_ecoli │ 0          │ 0.0627 失分轻│
#: │ micro_dead_volume       │ 0          │ 0.1101       │
#: └─────────────────────────┴────────────┴──────────────┘
#:
#: 阈值取 24（= 5×5 网格在「几乎无模态」时的期望计数上界附近）：
#: 单峰函数 41×41 网格的 2D 严格局部极小通常个位数，>24 说明网格尺度
#: 上已经看到明确的多极小结构，此时插值先验不可信。
#: 门控是**保守方向**：多模态一律禁用（宁可不增强，也不引入错误先验）。
MODALITY_MINIMA_CAP = 24


def count_local_minima(f, bounds, n: int = 41) -> int:
    """在 1D/2D 规则网格上统计严格局部极小的数量（多模态度量）。

    用于门控判定「目标函数是否强多模态」。只用网格比较，不求导
    （求导依赖 f 的解析形式，而 f 是黑盒 oracle）。

    Args:
        f: oracle 函数
        bounds: ``[[lo, hi], ...]``，仅支持 1D / 2D
        n: 每轴采样点数（41 是 2D 下能分辨出主要极小的最小值）

    Returns:
        局部极小个数；dim>2 或计算失败时返回 ``-1``（= 未知，
        门控按「不阻断」处理，因为多模态只是拒绝理由之一，不是采纳理由）
    """
    d = len(bounds)
    if d == 1:
        try:
            xs = axis_grid(bounds[0][0], bounds[0][1], n)
            ys = np.array([float(f(np.atleast_1d(x))) for x in xs], float)
        except Exception:               # noqa: BLE001
            return -1
        if len(ys) < 3 or not np.all(np.isfinite(ys)):
            return -1
        m = (ys[1:-1] < ys[:-2]) & (ys[1:-1] < ys[2:])
        return int(m.sum())
    if d == 2:
        try:
            g = [axis_grid(b[0], b[1], n) for b in bounds]
            GX, GY = np.meshgrid(g[0], g[1])
            pts = np.c_[GX.ravel(), GY.ravel()]
            Z = np.array([float(f(p)) for p in pts], float).reshape(len(g[0]), len(g[1]))
        except Exception:               # noqa: BLE001
            return -1
        if not np.all(np.isfinite(Z)):
            return -1
        m = ((Z[1:-1, 1:-1] < Z[:-2, 1:-1]) & (Z[1:-1, 1:-1] < Z[2:, 1:-1])
             & (Z[1:-1, 1:-1] < Z[1:-1, :-2]) & (Z[1:-1, 1:-1] < Z[1:-1, 2:]))
        return int(m.sum())
    return -1


#: 粗糙度阈值：mean|Δ²f| / std(f) 超过此值判为「超出代理模型适用域」。
#:
#: 依据（2026-10-04 实测，见 docs/research/surrogate-applicability-20261004.md）：
#: 拿 200 点采样把失分场景分成泾渭分明的两簇——
#: ┌────────────────────────────┬─────────┬──────────┬────────────┐
#: │ 场景                       │ rel     │ 局部极小 │ roughness │
#: ├────────────────────────────┼─────────┼──────────┼────────────┤
#: │ micro_methanobacterium_...  │ 1.5187  │ 71       │ **2.05**  │
#: │ micro_chemostat_ecoli      │ 1.6963  │ 66       │ **1.91**  │
#: │ micro_putida_toluene       │ 1.2589  │ 63       │ **1.78**  │
#: │ micro_ecoli_glucose        │ 1.0337  │ 64       │ **0.45**  │
#: │ micro_competition_ecoli_ye │ 0.0627  │ 0        │ **0.006** │
#: │ surrogate_forrester        │ 达标    │ 1        │ **0.0025**│
#: │ bio_logistic               │ 达标    │ 0        │ **0.0002**│
#: └────────────────────────────┴─────────┴──────────┴────────────┘
#:
#: roughness ≈ 1 意味着二阶差分与信号标准差同量级——函数是**高频震荡**
#: 而非光滑 ODE 解。此时任何平滑代理模型都受 Nyquist 约束：要分辨
#: 200 点里的 60+ 极值，每个周期至少要 ~3 个采样点，即需要 ~600 点，
#: 而场景只给 30 个训练点。实测把训练点加密到 480（16×），
#: rel 仍停在 0.998 不降——**存在不可约的建模误差下限**。
#:
#: 这不是可以调参解决的问题，而是「代理模型这类方法的适用域边界」。
#: 正确处理是**显式标注**（rubric 增 applicability 维度），
#: 而不是假装能修好。
#:
#: 阈值取 0.3（在 0.006 的光滑簇与 0.45 的震荡簇之间，且离两侧都有
#: 一个数量级以上的余量）。
ROUGHNESS_CAP = 0.3

#: 适用域检测的采样点数。200 足够分辨 60+ 局部极值（每极值 ~3 点），
#: 且成本可接受（1D 场景 200 次 oracle 调用）。
APPLICABILITY_SAMPLES = 200


def assess_applicability(f, bounds, n: int = APPLICABILITY_SAMPLES) -> dict:
    """判定目标函数是否在「平滑代理模型」的适用域内。

    做法：在轴上密采样，统计局部极值数与粗糙度（二阶差分 / 标准差）。
    两者都是**无量纲**的，可跨场景比较。

    Args:
        f: oracle 函数
        bounds: ``[[lo, hi], ...]``
        n: 每轴采样点数

    Returns:
        ``{"in_applicability": bool, "roughness": float|None,
           "n_extrema": int, "reason": str}``；dim>2 或计算失败时
        ``in_applicability=True``（不阻断——这是标注不是门禁）
    """
    d = len(bounds)
    if d not in (1, 2):
        return {"in_applicability": True, "roughness": None,
                "n_extrema": -1, "reason": f"dim={d} 未检测（不阻断）"}
    try:
        grids = [axis_grid(b[0], b[1], n) for b in bounds]
    except Exception as e:                       # noqa: BLE001
        return {"in_applicability": True, "roughness": None, "n_extrema": -1,
                "reason": f"网格构造失败 {type(e).__name__}（不阻断）"}

    if d == 1:
        X = grids[0].reshape(-1, 1)
    else:
        GX, GY = np.meshgrid(grids[0], grids[1])
        X = np.c_[GX.ravel(), GY.ravel()]
    try:
        y = np.array([float(f(p)) for p in X], float)
    except Exception as e:                       # noqa: BLE001
        return {"in_applicability": True, "roughness": None, "n_extrema": -1,
                "reason": f"oracle 评估失败 {type(e).__name__}（不阻断）"}
    if not np.all(np.isfinite(y)) or y.size < 8:
        return {"in_applicability": True, "roughness": None, "n_extrema": -1,
                "reason": "非有限值或样本不足（不阻断）"}

    sd = float(np.std(y))
    if sd <= 0.0:
        return {"in_applicability": False, "roughness": float("inf"),
                "n_extrema": 0, "reason": "函数在采样域上近似常数（退化）"}

    if d == 1:
        yv = y
    else:
        # 2D 用相邻差的均方根作为「方向粗糙度」的代理
        yv = y.reshape(len(grids[0]), len(grids[1])).mean(axis=0)
        if yv.size < 8:
            yv = y

    rough = float(np.mean(np.abs(np.diff(yv, 2)))) / sd
    d1 = np.diff(yv)
    sc = int(np.sum(np.diff(np.sign(d1)) != 0))
    n_ext = sc // 2 if d == 1 else count_local_minima(f, bounds)

    in_app = rough <= ROUGHNESS_CAP
    reason = (f"roughness={rough:.2f} ≤ {ROUGHNESS_CAP}" if in_app else
              f"高频震荡：roughness={rough:.2f} > {ROUGHNESS_CAP}"
              f"（{n_ext} 个局部极值 / {n} 采样点），"
              f"超出平滑代理模型适用域")
    return {"in_applicability": in_app, "roughness": round(rough, 4),
            "n_extrema": n_ext, "reason": reason}


def axis_grid(lo: float, hi: float, n: int):
    """按轴的跨数量级程度自适应选线性或对数采样点。

    这是本模块最重要的一处修复（2026-10-03）。原先所有轴一律
    ``np.linspace``，对 ``[0.001, 10]`` 这类跨 4 个数量级的区间是
    灾难性的采样——log10 跨度 4.0 时，均匀网格在 ``[0, 1]`` 区间内
    只落 ~1 个点，而低值区恰恰是微生物 Monod 动力学的敏感段。

    规则（可解释、无隐式调参）：
      - ``lo <= 0``        → 线性（对数无定义；含 0 的物理量如时间/浓度）
      - ``log10(hi/lo) <= 2`` → 线性（跨度小，对数采样反而浪费端点分辨率）
      - 否则               → 对数（``lo`` 补 ``eps`` 后 logspace）

    Args:
        lo: 轴下界
        hi: 轴上界
        n: 采样点数

    Returns:
        长度 n 的严格递增 ndarray
    """
    lo = float(lo)
    hi = float(hi)
    n = int(max(n, 2))
    if not (np.isfinite(lo) and np.isfinite(hi)) or hi <= lo:
        return np.linspace(lo, hi if hi > lo else lo + 1e-12, n)
    if lo <= 0.0:
        return np.linspace(lo, hi, n)
    span = np.log10(hi / lo)
    if span <= LOG_SPAN_THRESHOLD:
        return np.linspace(lo, hi, n)
    # 对数网格：lo 抬到最小正数，保证 logspace 合法
    lo_safe = max(lo, hi * 1e-12)
    return np.logspace(np.log10(lo_safe), np.log10(hi), n)


def make_lowfi(f, bounds, n1d=None, n2d=None):
    """粗网格低保真先验：1D 线性插值、2D griddata 线性；3D+ 返回 None。

    **尺度自适应（2026-10-03 修复）**：各轴采样点由 ``axis_grid`` 按该轴的
    跨数量级程度决定（线性 or 对数），不再一律 ``linspace``。这是微生物
    ODE 场景能否被多保真先验救回的关键——见 ``axis_grid`` docstring 的实测依据。

    网格密度也随维度自适应：1D 默认 64 点（上界受 oracle 调用成本约束，
    2^6=64 仍远小于 1 次真实实验成本），2D 默认 10×10=100 点。
    原实现在 1D 只用 10 点——比 30 个训练点还稀疏，先验必然劣于数据。

    低保真点本身也是调用 oracle f 得到的。在基准里不是免费的；在真实场景
    对应"低保真实验更便宜"（如更粗的 ODE 时间步、更低分辨率的场求解），
    这才是增益的来源。

    Args:
        f: oracle 函数
        bounds: ``[[lo, hi], ...]``
        n1d: 1D 采样点数（None → 自适应默认 64）
        n2d: 2D 每轴采样点数（None → 自适应默认 10）

    Returns:
        可调用均值函数，或 None（dim>2 / 低保真非有限）
    """
    n1d = 64 if n1d is None else int(n1d)
    n2d = 10 if n2d is None else int(n2d)
    d = len(bounds)
    if d == 1:
        xs = axis_grid(bounds[0][0], bounds[0][1], n1d)
        ys = np.array([float(f(np.atleast_1d(x))) for x in xs], float)
        if not np.all(np.isfinite(ys)):
            return None
        return _MemoizedFn(lambda x: float(
            np.interp(float(np.asarray(x).ravel()[0]), xs, ys)))
    if d == 2:
        g = [axis_grid(b[0], b[1], n2d) for b in bounds]
        GX, GY = np.meshgrid(g[0], g[1])
        pts = np.c_[GX.ravel(), GY.ravel()]
        vals = np.array([float(f(p)) for p in pts], float)
        if not np.all(np.isfinite(vals)):
            return None
        return _MemoizedFn(lambda x: float(griddata(
            pts, vals, np.atleast_2d(np.asarray(x, float).ravel()[:2]),
            method="linear", fill_value=float(np.nanmean(vals)))[0]))
    if d >= 3:
        return _make_lowfi_highdim(f, bounds, d)
    return None

#: 高维低保真网格的**总点数上限**（不是每轴点数）。
#:
#: 依据（2026-10-04 实测，13 个高维场景逐一 OOF 对比）：
#: ┌────────────────────┬──────┬──────────────┬───────────────────┐
#: │ 场景（dim）        │ base │ budget=1200  │ budget=3000/6000  │
#: ├────────────────────┼──────┼──────────────┼───────────────────┤
#: │ micro_fedbatch_3d  │.0199 │ 1.25× ADOPT  │ 1.49× / **1.94×**  │
#: │ opt_hartmann3d     │.0602 │ 0.62× reject │ 1.32× / **1.41×**  │
#: │ surrogate_hartmann6│.0096 │ 0.16× reject │ 0.16× / **0.04×**  │
#: │ cheminfo_* (7~9D)  │.04-.09│ 0.08~0.11×   │ 更差              │
#: │ mol_* (10D)        │.24-.44│ 0.58~1.17×   │ 更差              │
#: └────────────────────┴──────┴──────────────┴───────────────────┘
#:
#: 两条实测结论：
#: 1. **3D 需要 ~3000 点才划算**（1200 时仅 1.25×，3000 时 1.32~1.49×，
#:    6000 时最高 1.94×）。稀疏网格在 3D 里仍不够——每轴 12~15 点。
#: 2. **6D 及以上加密反而变差**（hartmann6 从 0.16× 掉到 0.04×）。
#:    根因是维度灾难：d 维中点距随 d 指数增长，网格点数固定时
#:    每加一个维度插值误差就爆一个量级。6D 需要 6^12 量级的点才够，
#:    远超真实实验成本，不可行。
#:
#: 所以本实现**只对 dim ≤ HIGH_DIM_MAX 启用**，更高维度直接返回 None
#: ——这与 A14/A15 的整体口径一致：知道方法在哪失效，比盲目铺开更有用。
HIGH_DIM_BUDGET = 3000
HIGH_DIM_MAX = 3

#: 距离加权幂指数（RBF 插值）。2 = 经典 Shepard/RBF 插值。
HIGH_DIM_RBF_P = 2.0


def _make_lowfi_highdim(f, bounds, d: int):
    """高维（3 ≤ dim ≤ HIGH_DIM_MAX）低保真先验：稀疏网格 + RBF 距离加权。

    为什么不用 ``griddata``：它的 Delaunay 三角剖分在 d>3 时退化
    （3D 以上基本是唯一 simplicial complex，几乎任何点都落在凸包边上），
    且 scipy 的 QhullError 在高维直接抛异常。

    改用**反距离加权插值**（Shepard / IDW）：无需三角剖分，任意维度可用，
    实现只有几行。代价是高维下精度低于 Delaunay 线性插值——但先验的
    作用是提供「形状提示」，不是精确近似；真正决定精度的是 GP 残差学习。

    **保守性说明**：本函数只对 dim ≤ ``HIGH_DIM_MAX``（实测 3）启用。
    更高维度的实测结论是「加密反而变差」（见 ``HIGH_DIM_BUDGET`` 的
    注释表），因此直接不给先验而不是给一个会误导 GP 的坏先验。

    是否采纳仍由 ``decide_mean_fn`` 的两阶段门控决定：OOF 判误差、
    生产口径判校准，判不过就回退 ``None``（逐字节等同关闭）。

    Args:
        f: oracle 函数
        bounds: ``[[lo, hi], ...]``，3 ≤ d ≤ HIGH_DIM_MAX
        d: 维度

    Returns:
        可调用均值函数（记忆化），或 None（非有限 / 网格太小 / 维度超限）
    """
    if d > HIGH_DIM_MAX:
        return None                       # 实测 6D+ 先验有害，结构性不适配
    # 每轴点数：让总点数 ≤ BUDGET
    n_axis = max(2, int(np.floor(HIGH_DIM_BUDGET ** (1.0 / d))))
    # 极端小预算时退化为"每轴 2 点"= 对角线，只给极弱形状提示
    grids = [axis_grid(b[0], b[1], n_axis) for b in bounds]
    # 网格点数（全组合）
    n_total = 1
    for g in grids:
        n_total *= len(g)
    if n_total < 4:
        return None                       # 至少要 4 点才有插值意义

    mesh = np.meshgrid(*grids, indexing="ij")
    pts = np.stack([m.ravel() for m in mesh], axis=1)
    # 归一化到单位立方体，避免不同维度的量纲主导距离计算
    lo = np.array([b[0] for b in bounds], float)
    hi = np.array([b[1] for b in bounds], float)
    span = np.where(hi - lo > 0, hi - lo, 1.0)
    pts_n = (pts - lo) / span
    try:
        vals = np.array([float(f(p)) for p in pts], float)
    except Exception:                     # noqa: BLE001
        return None
    if not np.all(np.isfinite(vals)):
        return None
    v_mean = float(np.mean(vals))
    v_std = float(np.std(vals))
    # 常数函数（近零方差）作为先验无信息量，反而会引入偏差
    if v_std <= 1e-12 * max(abs(v_mean), 1.0):
        return None

    lo_n = np.zeros(d)
    hi_n = np.ones(d)

    def _fn(x):
        q = (np.asarray(x, float).ravel()[:d] - lo) / span
        if not np.all(np.isfinite(q)):
            return v_mean
        # 略微夹到 [0,1]，避免查询点在 bounds 外时距离计算失真
        q = np.clip(q, lo_n, hi_n)
        # 欧氏距离（归一化空间）
        dist = np.sqrt(np.sum((pts_n - q) ** 2, axis=1))
        j = int(np.argmin(dist))
        dmin = float(dist[j])
        # 正好落在网格点上：直接返回真值，避免 0/0
        if dmin <= 1e-12:
            return float(vals[j])
        # 反距离加权：距离越近权重越大。r^-2 是经典 IDW。
        w = 1.0 / np.power(dist, HIGH_DIM_RBF_P)
        # 只保留最近的 K 个点做加权（K=8）：高维下远处点几乎无贡献，
        # 算全量反而容易数值下溢
        k = min(8, w.size)
        idx = np.argpartition(-w, k - 1)[:k]
        w_k = w[idx]
        v_k = vals[idx]
        return float(np.sum(w_k * v_k) / np.sum(w_k))

    return _MemoizedFn(_fn)


class _MemoizedFn:
    """带记忆的机理先验均值函数（2026-10-03 性能修复）。

    为什么需要：``GPSurrogate.predict`` 对每个查询点调一次 ``mean_fn``，
    而 conformal 标定要做 ``k_folds(5) × repeats(3) = 15`` 次拟合，
    每次 predict 又对整个 X 调用 —— 实测 2D 场景
    ``chem_reactor_conversion`` 单次 ``_conformal_q(mf=...)`` 要 **19.2s**，
    而 ``mf=None`` 只要 0.3s（**64 倍差**）。根因是 lambda 内部每次都做
    ``griddata`` 线性插值，而 2D 网格加密到 10×10 后单次插值已不便宜。

    这里按 x 的字节表示做记忆化：同一批 X 在 15 次折内反复被预测，
    实际只��� **一次**插值。语义完全等价（``mean_fn`` 是纯函数），
    但把 19.2s 降到 0.3s 量级。

    缓存无上限但按 key 存 float 字符串，典型场景 key 数 = |X| × 折数
    ≈ 数十~数百，内存可忽略。附加收益：结果可复现性更好
    （同一 x 永远返回同一个 float，避免浮点抖动）。
    """

    __slots__ = ("_fn", "_cache", "_hits", "_misses")

    def __init__(self, fn) -> None:
        self._fn = fn
        self._cache: Dict[bytes, float] = {}
        self._hits = 0
        self._misses = 0

    def __call__(self, x) -> float:
        try:
            k = np.asarray(x, float).ravel().tobytes()
        except (TypeError, ValueError):
            # 不可哈希的输入直接走原函数（不缓存，不报错）
            return float(self._fn(x))
        v = self._cache.get(k)
        if v is None:
            v = float(self._fn(x))
            self._cache[k] = v
            self._misses += 1
        else:
            self._hits += 1
        return v

    def cache_stats(self) -> Dict[str, int]:
        return {"size": len(self._cache), "hits": self._hits, "misses": self._misses}

    def clear(self) -> int:
        n = len(self._cache)
        self._cache.clear()
        self._hits = self._misses = 0
        return n


def _mk(s, yscale, ymean, mf):
    return GPSurrogate(lengthscale=s["ls"], noise_var=0.03 ** 2, y_mean=ymean,
                       y_scale=yscale, auto_ls=s.get("auto_ls", False), mean_fn=mf)


def _oof(X, y, s, y_scale, mf, k=5, seed=5):
    """K 折 OOF：返回 ``(rmse, 覆盖率@95%)``——门控同时看误差与校准。

    **口径必须与生产路径逐位一致（2026-10-03 两次修复）**

    第一次修复：把预测/真值统一到原始量纲（``GPSurrogate.predict``
    内部已 ``m * y_scale + y_mean`` 反归一化），此前直接拿归一化空间的
    预测与原始 ``y`` 比。

    第二次修复（关键）：生产路径的 coverage **不是**裸 ``z=1.96``，
    而是 **cross-conformal 校准**——``scripts/paper_validation_loop.py``
    的 ``_conformal_q`` 用独立校准集（seed 7311）算出 ``q``，发货区间是
    ``mean ± q·sd``。此前门控用裸 1.96 判定，得到 OOF coverage 恒在
    0.03~0.20；而生产同一个场景是 0.75~1.00。两个量纲的数做比较，
    门控第 3 条（绝对达标下限 0.85）**永远不触发**，形同虚设——
    实测 ``micro_lag_ecoli`` 生产 coverage 从 1.000 跌到 0.750 仍被放行。

    现改为：先用 K 折 OOF 残差算出 conformal 分位数 ``q``（标定集与
    评估折严格分离，无泄漏），再用 ``mean ± q·sd`` 判覆盖率。这样门控
    看到的 coverage 与生产 ``Calibrator`` / trust_surface 是同一个量，
    判据 3 才有意义。

    q 的估计用"标定折残差 / sd"的 95% 分位数（``CONF_ALPHA`` 同源
    0.05），与 ``engine/conformal.py::conformal_quantile`` 的语义一致。

    Args:
        X: 训练点 (n, d)
        y: 训练目标（原始量纲）
        s: 场景 dict（取 ``ls`` / ``auto_ls``）
        y_scale: 目标标准差（归一化用）
        mf: 机理先验均值函数或 None
        k: 折数
        seed: 划分随机种子

    Returns:
        ``(rmse, coverage)``，coverage 用 **conformal 校准区间**判定
    """
    Xa = np.atleast_2d(np.asarray(X, float))
    ya = np.asarray(y, float).ravel()
    idx = np.random.default_rng(seed).permutation(len(ya))
    preds = np.empty(len(ya))
    sds = np.empty(len(ya))
    for fo in np.array_split(idx, k):
        tr = np.setdiff1d(idx, fo)
        g = _mk(s, y_scale, float(np.mean(ya[tr])), mf).fit(Xa[tr], ya[tr])
        m, sd = g.predict(Xa[fo])
        preds[fo] = m
        sds[fo] = sd
    rmse = float(np.sqrt(np.mean((preds - ya) ** 2)))

    # conformal 分位数：残差尺度 / 预测标准差 的 (1-alpha) 分位。
    # 与生产 _conformal_q 同源（alpha=0.05 → 95% 区间），且标定折/评估折
    # 来自同一次 OOF 划分，不引入额外数据泄漏。
    #
    # ⚠️ 关于是否 max(q, 1.96)：实测**不能**这么做。GP 的 sd 在训练点附近
    # 极小，OOF 残差/sd 的离散度天然很大，q 经常达到 3~5；若再 max 到
    # 1.96 之上，coverage 会恒为 1.000，门控第 3 条（绝对下限 0.85）就
    # 完全失去区分力——实测 9/9 场景全部 ADOPT，包括生产 coverage 确实
    # 从 0.95 跌到 0.75 的 micro_lag_ecoli。
    # 生产的 κ 纪律是"只放宽不收窄"，但那是在**同一个 sd 定义**下
    # （sd 已含观测噪声 var += nv）。这里 z 分数本身已是相对量，
    # 无需再抬到 1.96。直接用标定 q。
    sd_safe = np.maximum(sds, 1e-12)
    z = np.abs(ya - preds) / sd_safe
    finite_z = z[np.isfinite(z)]
    if finite_z.size == 0:
        return rmse, 0.0
    # 有限样本 conformal 修正：分位取 ceil((n+1)(1-alpha))/n
    n_eff = float(finite_z.size)
    q = float(np.quantile(finite_z, min(1.0, np.ceil((n_eff + 1) * 0.95) / n_eff)))
    q = max(q, 1e-6)
    lo, hi = preds - q * sds, preds + q * sds
    cov = float(np.mean((ya >= lo) & (ya <= hi)))
    return rmse, cov


def decide_mean_fn(f, bounds, X, y, y_scale, s, cov_tol: float = DEFAULT_COV_TOL,
                   seed: int = 5):
    """为某场景决定「是否采纳低保真机理先验作 GP 均值函数」。

    返回 ``(mean_fn_or_None, decision_dict)``：
      - 采纳时第一项是可调用均值函数；否则为 ``None``（GP 行为与改造前完全一致）。
      - decision_dict 含 {adopted, reason, base_rmse, mf_rmse, base_cov, mf_cov, dim,
        stage}，供可信面/报告透明记录"这次为什么采纳/不采纳"。

    **两阶段门控（2026-10-03 重新设计，实测驱动）**

    阶段 1（本函数，OOF 快速筛）：只判**误差**。
        ``rmse_mf < rmse_base``  —— 机理先验必须让拟合更准。
        这一条在 OOF 上是可靠的：实测 36 个微生物场景，OOF 误差改善与
        生产 rmse 改善方向完全一致（``micro_mle_ecoli_monod`` OOF
        5.4049→0.0062，生产 3.2006→0.0000）。

    阶段 2（调用方 ``validate_scenario``，生产口径复核）：判**校准**。
        OOF 内部的 coverage **无法预测生产 coverage**，这是本轮实测的
        关键发现——同一个 ``micro_lag_ecoli``，OOF 报 1.000 而生产报
        0.75；``bio_logistic`` OOF 报 1.000 而生产报 0.00。原因是生产用
        **独立 conformal 校准集（seed 7311）+ held_out 测试集**，而 OOF
        在训练集内部做划分，分布不同。
        任何在 OOF 上算 coverage 再与生产阈值比较的做法都是错的
        （实测会导致 9/9 全部误采纳）。因此本函数**不再对 coverage
        下判断**，只输出 ``base_cov``/``mf_cov`` 作为透明记录，
        由调用方用生产评估器复核后才真正采纳。

    这条设计的代价是：门控从"自包含"变成"两阶段协作"。收益是
    校准纪律终于建立在**生产口径**上，而不是一个近似量上——
    对一个以"诚实"为卖点的系统，这个取舍是必须的。

    dim > 2 或低保真非有限 → 不适用（返回 None）。OOF 异常 → 保守返回 None。
    """
    d = len(bounds)
    decision = {"dim": d, "adopted": False, "reason": "", "stage": "oof-error"}
    mf = make_lowfi(f, bounds)
    if mf is None:
        decision["reason"] = "dim>2 或低保真非有限 → 不适用"
        return None, decision
    Xa = np.atleast_2d(np.asarray(X, float))
    ya = np.asarray(y, float).ravel()
    ys = float(y_scale) + 1e-9

    # 多模态守卫（2026-10-04）：规则网格 + 线性插值只对「网格间距内近似线性」
    # 的目标可信。对强多模态函数（实测 Rastrigin 121 极小 / Ackley 205 /
    # chemostat_cerevisiae 270），插值会把不同极小连成虚假斜坡，
    # GP 去学这个虚假结构反而更差。保守起见：多模态一律不给先验。
    #
    # 为什么放在 OOF 之前而不是之后：OOF 只能告诉你"这次误差有没有改善"，
    # 而多模态是**结构性**不适配——网格密度再高也解决不了插值假设失效。
    # 放前面还能省掉 2×5 折的 GP 拟合（实测 opt_ackley2d 每次 ~6s）。
    try:
        n_min = count_local_minima(f, bounds)
    except Exception:                    # noqa: BLE001
        n_min = -1                       # 未知 → 不阻断（不是采纳理由）
    decision["local_minima"] = n_min
    if n_min > MODALITY_MINIMA_CAP:
        decision["reason"] = (f"门控未通过：强多模态（{n_min} 个局部极小 > "
                              f"{MODALITY_MINIMA_CAP}）→ 规则网格插值先验不可信")
        return None, decision

    try:
        rb, cb = _oof(Xa, ya, s, ys, None, seed=seed)
        rm, cm = _oof(Xa, ya, s, ys, mf, seed=seed)
    except Exception as e:                       # noqa: BLE001
        decision["reason"] = f"OOF 门控异常：{type(e).__name__}: {e}"
        return None, decision
    decision.update({"base_rmse": rb, "mf_rmse": rm, "base_cov": cb, "mf_cov": cm})

    # 阶段 1 唯一判据：误差必须改善。coverage 交由生产口径复核。
    if not (rm < rb):
        decision["reason"] = f"门控未通过：rmse 恶化 {rb:.4f}→{rm:.4f}"
        return None, decision

    decision["adopted"] = True
    decision["stage"] = "pending-production-check"
    decision["reason"] = (f"阶段1 通过：误差改善 {rb:.4f}→{rm:.4f}；"
                          f"待生产口径复核 coverage")
    return mf, decision


# ---------------------------------------------------------------------------
# Self-test（2026-10-03 新增，覆盖本轮三处关键修复）
# ---------------------------------------------------------------------------

def _selftest() -> bool:
    """纯 stdlib self-test：覆盖 axis_grid / memoize / 两阶段门控的语义。"""
    passed = 0
    total = 0

    def check(name: str, cond: bool) -> None:
        nonlocal passed, total
        total += 1
        if cond:
            passed += 1
            print(f"  PASS  {name}")
        else:
            print(f"  FAIL  {name}")

    # 1. axis_grid：跨数量级用对数，小跨度用线性，lo<=0 用线性
    g_wide = axis_grid(0.001, 10.0, 16)
    check("wide span uses log grid (covers low end)",
          abs(g_wide[0] - 0.001) < 1e-12 and abs(g_wide[-1] - 10.0) < 1e-9)
    check("wide span strictly increasing", bool(np.all(np.diff(g_wide) > 0)))
    ratio = g_wide[1] / g_wide[0]
    check("wide span roughly geometric", abs(ratio - (10.0 / 0.001) ** (1 / 15)) < 0.05)

    g_narrow = axis_grid(0.5, 20.0, 8)
    check("narrow span uses linear grid",
          abs((g_narrow[1] - g_narrow[0]) - (19.5 / 7)) < 1e-6)

    g_zero = axis_grid(0.0, 20.0, 8)
    check("lo<=0 falls back to linear", g_zero[0] == 0.0)

    g_neg = axis_grid(-5.0, 10.0, 8)
    check("negative lo works", g_neg[0] == -5.0 and g_neg[-1] == 10.0)

    g_degen = axis_grid(1.0, 1.0, 8)
    check("degenerate bounds do not crash", len(g_degen) == 8)

    # 2. _MemoizedFn：值等价 + 命中
    calls = {"n": 0}

    def raw(x):
        calls["n"] += 1
        return float(np.sum(np.asarray(x, float).ravel()) * 2.0)

    m = _MemoizedFn(raw)
    x0 = np.array([1.0, 2.0])
    v1 = m(x0); v2 = m(x0)
    check("memoized value equals raw", abs(v1 - 6.0) < 1e-12)
    check("memoized returns same value", v1 == v2)
    check("underlying fn called once", calls["n"] == 1)
    st = m.cache_stats()
    check("cache stats reflect hit", st["hits"] >= 1 and st["misses"] == 1)
    check("clear empties cache", m.clear() == 1)
    m(x0)
    check("recompute after clear", calls["n"] == 2)

    # 不可哈希输入不崩
    check("non-array input still works",
          abs(m([[1.0, 2.0]]) - 6.0) < 1e-12)

    # 3. COVERAGE_OK_FLOOR 常量
    check("COVERAGE_OK_FLOOR == 0.85", abs(COVERAGE_OK_FLOOR - 0.85) < 1e-12)
    check("MODALITY_MINIMA_CAP == 24", MODALITY_MINIMA_CAP == 24)
    check("LOG_SPAN_THRESHOLD == 2.0", LOG_SPAN_THRESHOLD == 2.0)

    # 供后续检查复用的桩
    def dummy_f(x):
        return float(np.sum(np.asarray(x, float).ravel()))
    s_stub = {"ls": 0.5, "auto_ls": False}

    # 3b. count_local_minima：单峰 / 多峰 / 未知 三种情形
    # 单峰抛物线在 [0,1] 上恰好 1 个极小（x=0.3）——注意不是 0：
    # 抛物线本身就有唯一极小点，判据关心的是"极小数量"而非"有无极小"。
    n_single = count_local_minima(lambda x: float((np.asarray(x).ravel()[0] - 0.3) ** 2),
                                  [[0.0, 1.0]], n=41)
    check("single-peaked 1D has 1 minimum", n_single == 1)
    # 高频振荡：41 点采样在 [0, 6.28] 上对 cos(6x) 能数出若干个极小，
    # 但达不到 24 阈值（采样密度不足时高模态会被**低估**，这是已知局限）
    n_multi = count_local_minima(
        lambda x: float(np.cos(6 * np.asarray(x).ravel()[0])), [[0.0, 6.28]], n=41)
    check("oscillating 1D counts several minima", 1 < n_multi < MODALITY_MINIMA_CAP)
    # dim>2 → -1（未知，不阻断）
    check("dim>2 returns -1 (unknown)",
          count_local_minima(dummy_f, [[0, 1], [0, 1], [0, 1]]) == -1)
    # 2D Rastrigin 风格多峰：网格足够密时能数出 >24 个
    def rastrigin(x):
        z = np.asarray(x, float).ravel()
        return float(10 * len(z) + sum(v ** 2 - 10 * np.cos(2 * np.pi * v)
                                       for v in z))
    n_rast = count_local_minima(rastrigin, [[-5.12, 5.12], [-5.12, 5.12]], n=41)
    check("2D rastrigin detected as multi-modal", n_rast > MODALITY_MINIMA_CAP)
    # 非有限值 → -1
    check("non-finite returns -1",
          count_local_minima(lambda x: float("nan"), [[0, 1]]) == -1)

    # 3c. 多模态门控确实拦得住
    try:
        Xm = np.linspace(-5.12, 5.12, 30).reshape(-1, 1)
        ym = np.array([rastrigin([v]) for v in Xm.ravel()])
        _mf, dm = decide_mean_fn(rastrigin, [[-5.12, 5.12], [-5.12, 5.12]],
                                 np.hstack([Xm, Xm]), np.concatenate([ym, ym]),
                                 20.0, s_stub)
        check("modality gate blocks multi-modal",
              dm.get("adopted") is False and "多模态" in str(dm.get("reason", "")),
              )
        check("modality decision records minima count",
              "local_minima" in dm)
    except Exception as e:  # noqa: BLE001
        check("modality gate runs", False)

    # 4. make_lowfi：dim>2 走 _make_lowfi_highdim，但 dim>3 结构性不适配
    check("dim>2 no longer blindly returns None",
          make_lowfi(dummy_f, [[0, 1], [0, 1], [0, 1]]) is not None)
    check("dim=4 returns None (HIGH_DIM_MAX=3)",
          make_lowfi(dummy_f, [[0, 1]] * 4) is None)
    check("dim=7 returns None",
          make_lowfi(dummy_f, [[0, 1]] * 7) is None)
    check("HIGH_DIM_MAX == 3", HIGH_DIM_MAX == 3)
    check("HIGH_DIM_BUDGET == 3000", HIGH_DIM_BUDGET == 3000)

    # 4b. 3D 先验确实可用且插值合理
    f3 = make_lowfi(lambda x: float(np.sum(np.asarray(x, float).ravel() ** 2)),
                    [[0.0, 1.0]] * 3)
    check("3D lowfi returns memoized fn", isinstance(f3, _MemoizedFn))
    if f3 is not None:
        # 在 (0.5,0.5,0.5) 处 f 应为 0.75；容差放宽到 0.15（IDW 近似）
        v = f3(np.array([0.5, 0.5, 0.5]))
        check("3D lowfi interpolates correctly", abs(v - 0.75) < 0.15)
        # 网格点上应返回精确值
        v2 = f3(np.array([0.0, 0.0, 0.0]))
        check("3D lowfi exact at grid point", abs(v2 - 0.0) < 1e-9)
        # 越界点不应崩
        check("3D lowfi survives out-of-bounds query",
              isinstance(float(f3(np.array([9.0, 9.0, 9.0]))), float))

    # 4c. 常数函数的 3D 先验应被拒（无信息量）
    check("3D lowfi rejects constant function",
          make_lowfi(lambda x: 5.0, [[0.0, 1.0]] * 3) is None)

    # 5. make_lowfi 1D 返回可 memoize 的对象
    f1 = make_lowfi(lambda x: float(x[0]) ** 2, [[0.001, 10.0]])
    check("1D lowfi returns memoized fn",
          isinstance(f1, _MemoizedFn))
    check("1D lowfi interpolates correctly",
          abs(f1(np.array([10.0])) - 100.0) < 1.0)

    # 6. decide_mean_fn 在误差未改善时保守拒绝
    Xr = np.array([[0.1], [0.2], [0.3], [0.4], [0.5],
                   [0.6], [0.7], [0.8], [0.9], [1.0]])
    # 目标与网格完全一致 → 先验无增益，门控应拒绝或至少不崩
    yr = np.array([float(x[0]) for x in Xr])
    try:
        mf, dec = decide_mean_fn(lambda x: float(x[0]), [[0.0, 1.0]],
                                 Xr, yr, 1.0, s_stub)
        check("decide_mean_fn returns tuple", mf is None or callable(mf))
        check("decide_mean_fn has reason", bool(dec.get("reason")))
        check("decide_mean_fn has stage", "stage" in dec)
    except Exception as e:  # noqa: BLE001
        check("decide_mean_fn runs without exception", False)

    # 7. OOF 返回原始量纲
    try:
        rm, cov = _oof(Xr, yr, s_stub, 1.0, None, k=3, seed=1)
        check("_oof rmse finite", np.isfinite(rm))
        check("_oof cov in [0,1]", 0.0 <= cov <= 1.0)
    except Exception as e:  # noqa: BLE001
        check("_oof runs", False)

    print(f"\n{passed}/{total} multifidelity self-tests passed")
    return passed == total


if __name__ == "__main__":
    import sys as _sys
    if _selftest():
        _sys.exit(0)
    _sys.exit(1)
