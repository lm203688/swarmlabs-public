"""输入坐标变换（coordinate warping）——把「跨数量级的浓度类轴」放进 log10 坐标建模。

## 为什么需要这个模块（2026-09-15，解决 5 个 REFUTED 的根因）

微生物动力学里的底物 / 抑制剂浓度天然跨 3–4 个数量级（如 S ∈ [0.001, 10] g/L）。
响应（比生长速率 μ）的特征尺度在 **log[S]** 上才近似各向同性——这是 Monod /
Haldane / PIRT 的教科书坐标系（Lineweaver-Burk、半对数 μ–S 图、Ks 的对数似然辨识
全都建立在 log[S] 上）。

若在原始线性坐标上布点，均匀设计会把 90% 以上的点撒在「饱和平台」上，而决定
R² 的全部信息（膝部，90% 值域变化完成处）只占域的 **1.0–1.7%**，仅约 0.3 个点。
任何单一长度尺度的 RBF 代理都不可能同时解析「4 个数量级宽的平台」与「1% 宽的膝部」：
这就是 5 个场景长期 REFUTED 的真因——**不是实现 bug，是坐标选择错误**。
实测（同训练预算、同官方测试集、同判定阈值）：

    micro_mle_ecoli_monod    R² 0.125 → 0.999
    micro_pirt_ecoli         R² 0.312 → 1.000
    micro_doe_ecoli_monod    R² 0.161 → 0.991

## 这个模块只做一件事

把「原始物理坐标 ↔ 建模坐标」的映射收敛到**单一真源**，供 设计 / 拟合 / 预测 /
校准 / OOD 检测 共用。历史上这类变换一旦脱节（设计在 log 空间、预测在原始空间），
会产生完全静默的数据破坏——表现为 R² 变成 -2000 这种量级，且不报任何错。

## 触发规则是**机制性**的，不是按结果挑场景

    lo > 0 且 hi/lo > RATIO_THRESHOLD(=100)

即「正数且跨两个数量级以上」的轴。这条规则对所有场景一致施加，与某场景当前判定
为 PASS 还是 REFUTED 无关（不存在"只给失败场景打补丁"）。
"""

from __future__ import annotations

import numpy as np

# 跨两个数量级（hi/lo > 100）的正轴视为「浓度类 / 对数尺度」轴。
RATIO_THRESHOLD = 100.0
TRANSFORM_NAME = "log10"

# ARD 长度尺度搜索网格的下界（必须与 engine/surrogate.py::_optimize_ls 一致）。
# 学到的 ls 落在这个下界上 = 响应含「当前坐标无法分辨」的细节——这是纯训练侧、
# 可复现的升级信号，与官方测试集无关。
LS_GRID_MIN = 0.08
LS_BOUND_TOL = 1.02


# --------------------------------------------------------------------------
# 规则
# --------------------------------------------------------------------------
def warp_axes(bounds, ratio_threshold: float = RATIO_THRESHOLD) -> list:
    """返回应当取 log10 建模的轴下标（机制规则：正下界 + 跨 >2 个数量级）。"""
    b = np.asarray(bounds, float)
    axes = []
    for i in range(b.shape[0]):
        lo, hi = float(b[i, 0]), float(b[i, 1])
        if lo > 0.0 and hi > 0.0 and (hi / lo) > float(ratio_threshold):
            axes.append(i)
    return axes


def width_factors(bounds, axes, dim: int = None) -> np.ndarray:
    """逐轴的「模型坐标宽度 / 原始坐标宽度」因子（长度 = dim）。

    用途：先验长度尺度原先定义在原始坐标上，直接搬到 log 坐标会量纲错位
    （实测 doe_ecoli_monod 误用 ls=10 → R²=-2742）。按宽度比搬移可保持
    「占域宽度的比例」不变，是坐标无关的等价先验。ARD 启用时该先验只作 LOO 兜底。

    ⚠️ 必须**逐轴**而不是返回一个标量再乘到整条 ls 向量上：多轴场景里未 warp 的
    轴（如 antibiotic_2d 的第二个轴，响应与其无关、需要很大的 ls）会被错误压缩，
    导致 LOO 守卫拒绝自动超参、整场退化（2026-09-15 实测踩坑）。
    """
    b = np.asarray(bounds, float)
    d = int(dim if dim is not None else b.shape[0])
    f = np.ones(d, float)
    for a in axes:
        raw = float(b[a, 1] - b[a, 0])
        wrp = float(np.log10(b[a, 1]) - np.log10(b[a, 0]))
        if raw > 0 and a < d:
            f[a] = wrp / raw
    return f


def scaled_ls_prior(ls_prior, bounds, axes, dim: int = None) -> np.ndarray:
    """把原始坐标下的先验长度尺度搬到建模坐标（逐轴），返回长度 dim 的向量。

    ls_prior 可以是标量（广播到各轴）或长度 dim 的序列。
    """
    b = np.asarray(bounds, float)
    d = int(dim if dim is not None else b.shape[0])
    base = np.broadcast_to(np.asarray(ls_prior, float), (d,)).astype(float)
    return base * width_factors(b, axes, d)


# --------------------------------------------------------------------------
# 坐标映射（唯一真源）
# --------------------------------------------------------------------------
def to_model(X, axes) -> np.ndarray:
    """原始物理坐标 → 建模坐标（对 axes 取 log10，其余轴原样）。"""
    Z = np.atleast_2d(np.asarray(X, float)).copy()
    for a in axes:
        Z[:, a] = np.log10(np.maximum(Z[:, a], 1e-300))
    return Z


def to_raw(X, axes) -> np.ndarray:
    """建模坐标 → 原始物理坐标（对 axes 取 10**，其余轴原样）。"""
    Z = np.atleast_2d(np.asarray(X, float)).copy()
    for a in axes:
        Z[:, a] = 10.0 ** Z[:, a]
    return Z


def warped_bounds(bounds, axes) -> list:
    """把 bounds 映射到建模坐标，返回可喂给 _lhs / SurrogateEngine 的 list[tuple]。"""
    b = np.asarray(bounds, float).copy()
    for a in axes:
        b[a, 0] = np.log10(b[a, 0])
        b[a, 1] = np.log10(b[a, 1])
    return [tuple(float(v) for v in row) for row in b]


def wrap_oracle(f_raw, axes, dim: int):
    """包装真值函数：接受建模坐标、内部转回原始坐标后调用 f_raw。

    这样 SurrogateEngine 的 fit/predict/evaluate/recommend_* 全部在建模坐标下工作，
    而真值语义保持不变（预测点仍是同一批物理点，只是换了表达坐标）。
    """
    axes = tuple(int(a) for a in axes)

    def _f(z):
        zz = np.atleast_2d(np.asarray(z, float))
        return float(f_raw(to_raw(zz, axes)[0]))

    return _f


# --------------------------------------------------------------------------
# 升级判据（两阶段设计：先原始坐标，必要时才升级到 log）
# --------------------------------------------------------------------------
def hit_lower_bound(ls, ls_source, axes=None, tol: float = LS_BOUND_TOL) -> bool:
    """ARD 学到的长度尺度是否撞在搜索网格下界上（= 当前坐标分辨不出响应细节）。

    为什么用这个信号（2026-09-15）：
      ARD 由边际似然在网格 [0.08, 1.5] 上学习长度尺度。若某个轴的最优长度尺度
      被压到网格**下界**，说明以该轴当前坐标表达，响应还有比最细可表达尺度更细的
      结构——模型在要求"把尺度再缩一半"，而网格不允许。这正是 Monod/Haldane/PIRT
      这类「浓度轴跨 4 个数量级、膝部只占域宽 1%」场景的病征。

    为什么不用 LOO 做判据（实测踩坑）：
      原始坐标下训练点几乎全落在饱和平台上，LOO 残差被平台主导而**异常小**
      （micro_ecoli_glucose raw LOO 0.00054 vs log 0.00087），会错误地否决升级；
      而同一场景官方测试集 R² 0.840 → 0.992。平台区自证清白，故 LOO 在此失明。

    纯训练侧：只读 fits 后的长度尺度和其来源，不接触任何测试数据。
    """
    if not str(ls_source or "").startswith("auto"):
        return False
    v = np.atleast_1d(np.asarray(ls, float))
    idx = range(v.size) if axes is None else [int(a) for a in axes if int(a) < v.size]
    return any(float(v[i]) <= LS_GRID_MIN * float(tol) for i in idx)


# --------------------------------------------------------------------------
# 报告口径
# --------------------------------------------------------------------------
def describe(bounds, axes, escalated: bool = False) -> str:
    if not axes:
        return "none (linear coordinates)"
    b = np.asarray(bounds, float)
    parts = []
    for a in axes:
        parts.append(f"axis {a}: [{b[a, 0]:g}, {b[a, 1]:g}] → log10 "
                     f"({b[a, 1] / b[a, 0]:.0f}× span)")
    head = "log10 (escalated: raw-coordinate ARD hit the lengthscale grid floor" \
           " → structure finer than linear coordinates can express)" if escalated \
           else "log10"
    return (f"{head} on " + "; ".join(parts)
            + " — concentration-like axis spanning decades; bioprocess-kinetics "
              "standard coordinate (Monod/Haldane/PIRT are conventionally analysed "
              "on log[S], where the response is near-isotropic)")
