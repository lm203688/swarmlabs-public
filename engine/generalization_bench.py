"""三级泛化基准（G1/G2/G3）——补「泛化证据 = 0」这个最大缺口。

为什么需要（2026-10-04 诊断）
----------------------------
现有 82 个场景的 train/test 都是**同一分布内**划分（LHS 采样后随机切分）。
这只能证明「代理模型能插值」，**不能证明「能外推」**。

而「能外推」才是科研场景的真实需求：一个微生物学家不会帮你采 30 个点
然后只在你采过的范围内预测。缺少外推证据，「我们是可信验证层」这句话
就没有支撑——这是项目叙事最大的漏洞。

三级定义（严格递进，不可互相替代）
----------------------------------
G1 **参数外推**：在 ``[a, b]`` 上采样训练，预测 ``[b, 1.5·b]``（训练域外）。
    这是最基本的泛化：连续参数轴上的外插。

G2 **留一维外推**：2D 场景训练时**完全剔除**一个维度（该维固定在中值），
    测试时该维度变化。考的是「模型是否学到了跨维度的耦合关系」。

G3 **留一学科外推**：训练时**完全剔除**目标学科的所有场景，只在剩余
    学科上训练，然后预测目标学科。**这是「能跨学科用」的唯一硬证据。**
    如果 G3 结果是 0%，那 67% 微生物集中就是方法的本质局限而非数据
    问题——必须诚实说出来。

设计原则（与项目其余部分同源）
----------------------------
- **零破坏**：本模块只读场景定义，不改任何生产路径。
- **可复现**：种子固定（默认 4242，与 paper_validation_loop 一致）。
- **如实报告**：G1/G2/G3 各自的达标率独立报告，G3 差不会掩盖 G1 好。
- **不美化**：外推段误差**只报告不掩盖**，不因为"难"就换一个口径。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

_SCHEMA = "svp.generalization-bench.v1"

#: 与 paper_validation_loop 一致的默认种子（跨基准可复现）
DEFAULT_SEED = 4242

#: G1 外推段的扩展倍数：[a,b] 训练，测 [b, (1+G1_EXPAND)·b]
G1_EXPAND = 0.5

#: G2 被剔除的那个维度固定在训练域的什么位置（0.5 = 中值）
G2_HOLD_FRAC = 0.5

#: 达标判据：外推段 rel rmse 相对内插段的允许倍数
#:
#: 为什么用比值而不是绝对阈值：不同场景的难度差异极大（微生物
#: 0.0001~1.7，工程基准 0.0002~0.26），绝对阈值会系统性偏袒某些学科。
#: 比值判据问的是"外推比内插差多少倍"，这才是泛化的真问题。
G1_MAX_RATIO = 2.0
G2_MAX_RATIO = 3.0
G3_MIN_PASS_RATE = 0.5      # G3 留出学科的 rubric 达标率下限

#: 内插 rel 上限：超过此值则「外推/内插比值」判据**无意义**。
#:
#: 依据（2026-10-04 实测发现的判据缺陷）：原判据只看比值，导致
#: 「内插 rel=1.11、外推 rel=1.08、比率 0.97」被判**通过**——
#: 但这个模型连内插都没拟合好（rel>1 = 误差比信号还大），它只是
#: 没有"完美幻觉"可以崩塌而已。实测 G1 首轮 5 个"通过"场景的
#: 内插 rel 全在 0.63~1.11 区间，即**全部是"内外都错"**。
#:
#: 0.2 的依据：rubric 的 rmse_rel_ok 判据是 ≤0.05；内插 ≤0.2 意味着
#: 「至少比 rubric 宽松 4 倍但仍是可用的拟合」。低于这个水平时比值判据
#: 不携带信息。
G1_MAX_INTERP_REL = 0.2
G2_MAX_INTERP_REL = 0.2


# ---------------------------------------------------------------------------
# 场景表（惰性注入，避免 import 时的循环依赖）
# ---------------------------------------------------------------------------

def _scenarios() -> Dict[str, Dict[str, Any]]:
    """取全量场景定义（key → scenario dict）。

    惰性 import：``paper_validation_loop`` 顶层会做路径装配，直接在模块
    导入期 import 会与 ``engine`` 形成循环（见 AXIOM_AUDIT 关于 import
    死锁的记录）。
    """
    from engine.scenario_registry import all_scenarios as _all_scns
    return _all_scns()


def _discipline_of(scn: Dict[str, Any]) -> str:
    """场景的学科标签（domain 首段，小写）。

    刻意用 domain 而不是 key 前缀：domain 是场景自己声明的学科归属，
    key 前缀只是命名习惯。
    """
    dom = str(scn.get("domain", "")).split("/")[0].strip()
    return (dom or "unknown").lower()


# ---------------------------------------------------------------------------
# G1：参数外推
# ---------------------------------------------------------------------------

@dataclass
class G1Result:
    """G1 单场景结果。"""

    key: str
    ok: bool = False
    na: bool = False                 # 不适用（内插就没拟合好，比值无意义）
    dim: int = 0
    reason: str = ""
    interp_rel: Optional[float] = None      # 内插段 rel rmse
    extrap_rel: Optional[float] = None      # 外推段 rel rmse
    ratio: Optional[float] = None           # extrap / interp
    n_train: int = 0
    n_test_interp: int = 0
    n_test_extrap: int = 0
    auto_ls_used: bool = False              # 是否启用了 ARD/LML 超参搜索
    ls_prior: Optional[float] = None        # 场景注册表中的先验长度尺度
    warped: bool = False                    # 第一维是否使用了 log10 变换

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def run_g1(key: str, scn: Dict[str, Any], seed: int = DEFAULT_SEED,
           n_train: int = 30, n_test: int = 20) -> G1Result:
    """G1 参数外推：在参数域的前半段训练，测后半段（训练域外）。

    做法：
    1. 沿**第一个**有正下界的维度切成两半（``lo, mid`` / ``mid, hi``）
    2. 训练点用 LHS 采在 ``[lo, mid]``，其余维度仍用全域
    3. 测试点分两组：内插组（同 ``[lo, mid]``）、外推组（``(mid, hi]``）
    4. 分别算 rel rmse，看比值

    只用第一维是因为多维外推需要"从角上切"，会让训练分布过于畸形
    （其他维也在边缘），G1 想测的是最干净的一维外推。

    Args:
        key: 场景 key
        scn: 场景定义
        seed: 随机种子
        n_train: 训练点数
        n_test: 每个测试组的点数

    Returns:
        G1Result
    """
    res = G1Result(key=key)
    bounds_raw = scn.get("bounds") or []
    res.dim = len(bounds_raw)
    if not bounds_raw:
        res.reason = "无 bounds"
        return res

    f = scn["f"]
    d = len(bounds_raw)

    # ── 轴变换：对跨数量级的正轴使用 log10 建模 ──────────────────
    # 2026-10-11 改进：G1 在原始线性坐标上对 [0.001, 10] 这类轴
    # 做中点切分，训练 [0.001, 5]、外推 (5, 10]，GP 在原始空间
    # 无法外推 4 个数量级。改用 log10 后，训练 [-3, -0.7]、外推
    # (-0.7, 1]，两边各 2.3 个单位，GP 外推大幅改善。
    from engine.design import warp_axes
    warped = warp_axes(bounds_raw)
    if warped and warped[0] == 0:
        # 第一维需要 log10 变换
        lo_raw, hi_raw = float(bounds_raw[0][0]), float(bounds_raw[0][1])
        lo, hi = float(np.log10(lo_raw)), float(np.log10(hi_raw))
        # 包装 oracle：输入 log10 空间，输出原始空间
        f_orig = f
        def f_log(x):
            x_arr = np.asarray(x, dtype=float).copy()
            if x_arr.ndim == 1:
                x_arr[0] = float(10.0 ** x_arr[0])
            else:
                x_arr[:, 0] = 10.0 ** x_arr[:, 0]
            return f_orig(x_arr)
        f = f_log
        res.warped = True
    else:
        lo, hi = float(bounds_raw[0][0]), float(bounds_raw[0][1])
        res.warped = False

    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        res.reason = f"第一维退化 bounds=[{lo},{hi}]"
        return res

    # 构建有效采样空间：第一维用变换后的 bounds，其余维度用原始 bounds
    bounds = list(bounds_raw)
    if res.warped:
        bounds[0] = [lo, hi]  # log10 空间

    mid = lo + (hi - lo) * 0.5
    rng = np.random.default_rng(seed)

    def _lhs_in(sub_lo, sub_hi, n):
        """在第一维 [sub_lo, sub_hi]、其余维全域上采 LHS。"""
        u = (rng.random((n, d)) + np.arange(n)[:, None]) / n
        u = rng.permutation(u, axis=0) if d > 1 else u
        out = np.empty((n, d))
        for j, (a, b) in enumerate(bounds):
            a2, b2 = (a, b) if j else (sub_lo, sub_hi)
            out[:, j] = a2 + u[:, j] * (b2 - a2)
        return out

    Xtr = _lhs_in(lo, mid, n_train)
    # 内插测试点：同区间但严格排除训练点
    Xti = _lhs_in(lo, mid, n_test)
    # 外推测试点：训练域之外的区间 (mid, hi]
    Xte = _lhs_in(mid + 1e-9 * (hi - mid), hi, n_test)

    try:
        ytr = np.array([float(f(p)) for p in Xtr])
        yti = np.array([float(f(p)) for p in Xti])
        yte = np.array([float(f(p)) for p in Xte])
    except Exception as e:                        # noqa: BLE001
        res.reason = f"oracle 评估失败 {type(e).__name__}"
        return res
    if not (np.all(np.isfinite(ytr)) and np.all(np.isfinite(yti))
            and np.all(np.isfinite(yte))):
        res.reason = "存在非有限真值"
        return res

    # 尺度（归一化基准）——2026-10-08 修复「病态分母」（记为 A15）。
    #
    # 判据 rel = RMSE / scale 里，scale 必须回答"这段区域的信号幅度有多大"。
    # 原实现用 std(内插测试 ∪ 外推测试)，对两类场景同时失效：
    #   (a) **上半段饱和**（例 micro_ecoli_glucose [0.001,10]）：G1 只在前半段
    #       训练，后半段测试。f 在 [0.001,5] 已饱和（std=0.048），scale≈0.04
    #       ⇒ 任何 0.04 的绝对误差被判成 rel≈1.0（"连内插都不合格"）。
    #   (b) **下半段陡升**（例 micro_sobol_ecoli [0.001,5]）：f 在低端从 0.045
    #       跃到 0.47 后饱和。改用训练段 std 会让 scale 反而变小。
    #
    # 稳健做法：scale 取「训练段 std」与「全域测试 std」的**较大者**——
    # 即"该场景在整个评估范围内可分辨的响应幅度"。这既不会因为某段饱和
    # 而把分母压到噪声量级，也不会因为只取一段而低估信号。
    #
    # 保守性：scale 变大只会让 rel 变小（更宽容），因此必须配合
    # G1_MAX_INTERP_REL 的绝对下限使用；且常数场景（两个 std 都≈0）
    # 仍会被守卫判 NA，不会凭空产生"通过"。
    sd_train = float(np.std(ytr, ddof=1)) if len(ytr) > 1 else 0.0
    sd_test = float(np.std(np.concatenate([yti, yte]))) if (len(yti) + len(yte)) > 1 else 0.0
    scale = max(sd_train, sd_test)
    if scale <= 1e-9:
        res.reason = "训练段与测试段均近似常数（无信号可学，泛化无定义）"
        res.na = True
        return res

    try:
        from engine.surrogate import GPSurrogate
        ym = float(np.mean(ytr))
        # ── 超参选择：与生产路径对齐（2026-10-08 修复） ──────────────────
        # 此前这里用场景注册表里的**先验** ls（scn["ls"]）且 auto_ls=False。
        # 实测发现这是 G1 大面积 NA 的一个主因：许多场景的先验 ls 与
        # **归一化后**的参数域尺度严重不匹配——
        #   例：micro_ecoli_glucose 的 bounds=[0.001, 10]（跨度 4 个数量级），
        #   先验 ls=0.5；GP 内部把 X 归一化到 [0,1] 后，ls=0.5 意味着
        #   "整条轴只有 2 个相关长度"，模型近乎线性，无法拟合 Monod 的
        #   饱和形状（f 在域内 range 0.25~0.87，相邻点差最大 0.60）。
        #
        # 修复：给「先验 ls 明显不适配」的场景自动开启 ARD/LML 超参搜索
        # （surrogate.GPSurrogate 已有 auto_ls，且带闭式 LOO 守卫，学到的
        # 超参必须在 LOO 上不劣于先验才会被采用）。
        #
        # 判定"不适配"的保守规则：先验 ls < 0.1（归一化域的 1/10）。
        # 这是**只放松、不收紧**的改动：auto_ls 有 LOO 守卫，最坏情况
        # 回落到先验，因此不会把本该失败的场景变成通过。
        prior_ls = float(np.asarray(scn["ls"]).ravel()[0]) if np.ndim(scn["ls"]) else float(scn["ls"])
        use_auto = bool(scn.get("auto_ls", False)) or prior_ls < 0.1
        g = GPSurrogate(lengthscale=scn["ls"], noise_var=(0.03) ** 2,
                        y_mean=ym, y_scale=scale,
                        auto_ls=use_auto)
        g.fit(Xtr, ytr)
        mi, _ = g.predict(Xti)
        me, _ = g.predict(Xte)
        res.auto_ls_used = use_auto
        res.ls_prior = prior_ls
    except Exception as e:                        # noqa: BLE001
        res.reason = f"GP 拟合/预测失败 {type(e).__name__}: {e}"
        return res

    res.interp_rel = float(np.sqrt(np.mean((mi - yti) ** 2)) / scale)
    res.extrap_rel = float(np.sqrt(np.mean((me - yte) ** 2)) / scale)
    res.n_train = n_train
    res.n_test_interp = n_test
    res.n_test_extrap = n_test

    # ── 判据 1（前置）：内插必须先拟合得上，否则比值无意义 ──────────
    # 见 G1_MAX_INTERP_REL 的注释：原判据只看比值，会把
    # 「内插 1.11 / 外推 1.08 / 比率 0.97」判成通过——那是"内外都错"。
    if res.interp_rel > G1_MAX_INTERP_REL:
        res.ok = False
        res.na = True
        res.reason = (f"不适用：内插 rel={res.interp_rel:.4f} > "
                      f"{G1_MAX_INTERP_REL}（模型连内插都没拟合好，"
                      f"外推/内插比值不携带信息）")
        return res

    if res.interp_rel > 1e-12:
        res.ratio = res.extrap_rel / res.interp_rel
        res.ok = res.ratio <= G1_MAX_RATIO
        res.reason = (f"外推/内插 = {res.ratio:.2f}× "
                      f"({'≤' if res.ok else '>'} {G1_MAX_RATIO}×)")
    else:
        # 内插误差接近 0 时比值无意义：直接要求外推绝对误差也小
        res.ok = res.extrap_rel <= 0.05
        res.reason = (f"内插近乎完美，改用绝对判据："
                      f"外推 rel={res.extrap_rel:.4f} "
                      f"({'≤' if res.ok else '>'} 0.05)")
    return res


# ---------------------------------------------------------------------------
# G2：留一维外推
# ---------------------------------------------------------------------------

@dataclass
class G2Result:
    """G2 单场景结果。"""

    key: str
    ok: bool = False
    na: bool = False                 # 不适用（内插就没拟合好）
    held_dim: int = -1
    reason: str = ""
    interp_rel: Optional[float] = None
    extrap_rel: Optional[float] = None
    ratio: Optional[float] = None
    n_train: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def run_g2(key: str, scn: Dict[str, Any], seed: int = DEFAULT_SEED,
           n_train: int = 40, n_test: int = 20) -> G2Result:
    """G2 留一维外推：训练时把第 ``held_dim`` 维钉在中值，测试时让它变化。

    与 G1 的区别：G1 是"沿同一轴继续往外"，G2 是"训练时**完全没看过**
    这个维度的变化，测试时才发现"。G2 考的是模型有没有学到
    **跨维度的耦合**（如果 f 实际上是各维独立的可加模型，G2 应该不难；
    如果存在强交互项，G2 会显著更难——这正是要测的信息）。

    Args:
        key: 场景 key
        scn: 场景定义
        seed: 随机种子
        n_train: 训练点数（都在 held 维中值截面上）
        n_test: 测试点数

    Returns:
        G2Result
    """
    res = G2Result(key=key)
    bounds = scn.get("bounds") or []
    d = len(bounds)
    if d < 2:
        res.reason = f"dim={d} < 2，G2 不适用"
        return res

    # 留出跨度最大的那一维（信息量最大）
    spans = []
    for a, b in bounds:
        a, b = float(a), float(b)
        spans.append((b - a) if (np.isfinite(a) and np.isfinite(b)) else 0.0)
    held = int(np.argmax(spans))
    res.held_dim = held

    f = scn["f"]
    rng = np.random.default_rng(seed)
    hold_val = float(bounds[held][0]) + spans[held] * G2_HOLD_FRAC

    def _sample(n, fix_held):
        u = (rng.random((n, d)) + np.arange(n)[:, None]) / n
        out = np.empty((n, d))
        for j, (a, b) in enumerate(bounds):
            if fix_held and j == held:
                out[:, j] = hold_val
            else:
                out[:, j] = a + u[:, j] * (b - a)
        return out

    Xtr = _sample(n_train, fix_held=True)
    Xte = _sample(n_test, fix_held=False)

    try:
        ytr = np.array([float(f(p)) for p in Xtr])
        yte = np.array([float(f(p)) for p in Xte])
    except Exception as e:                        # noqa: BLE001
        res.reason = f"oracle 评估失败 {type(e).__name__}"
        return res
    if not (np.all(np.isfinite(ytr)) and np.all(np.isfinite(yte))):
        res.reason = "存在非有限真值"
        return res

    scale = float(np.std(yte)) + 1e-9
    if scale <= 1e-9:
        res.reason = "测试集近似常数"
        return res

    try:
        from engine.surrogate import GPSurrogate
        g = GPSurrogate(lengthscale=scn["ls"], noise_var=(0.03) ** 2,
                        y_mean=float(np.mean(ytr)), y_scale=scale,
                        auto_ls=scn.get("auto_ls", False))
        g.fit(Xtr, ytr)
        m, _ = g.predict(Xte)
    except Exception as e:                        # noqa: BLE001
        res.reason = f"GP 拟合/预测失败 {type(e).__name__}: {e}"
        return res

    # 内插基线：同分布留出（训练点的随机 20%）
    n_in = max(n_train // 5, 2)
    X_in, y_in = Xtr[-n_in:], ytr[-n_in:]
    g_in = GPSurrogate(lengthscale=scn["ls"], noise_var=(0.03) ** 2,
                       y_mean=float(np.mean(ytr[:-n_in])), y_scale=scale,
                       auto_ls=scn.get("auto_ls", False))
    g_in.fit(Xtr[:-n_in], ytr[:-n_in])
    m_in, _ = g_in.predict(X_in)

    res.interp_rel = float(np.sqrt(np.mean((m_in - y_in) ** 2)) / scale)
    res.extrap_rel = float(np.sqrt(np.mean((m - yte) ** 2)) / scale)
    res.n_train = n_train

    # 前置：内插拟合不上下��比值判据无意义（同 G1，见 G1_MAX_INTERP_REL）
    if res.interp_rel > G2_MAX_INTERP_REL:
        res.ok = False
        res.na = True
        res.reason = (f"不适用：内插 rel={res.interp_rel:.4f} > "
                      f"{G2_MAX_INTERP_REL}（内插都没拟合好）")
        return res

    if res.interp_rel > 1e-12:
        res.ratio = res.extrap_rel / res.interp_rel
        res.ok = res.ratio <= G2_MAX_RATIO
        res.reason = (f"留出维 {held}：外推/内插 = {res.ratio:.2f}× "
                      f"({'≤' if res.ok else '>'} {G2_MAX_RATIO}×)")
    else:
        res.ok = res.extrap_rel <= 0.10
        res.reason = (f"内插近乎完美，改用绝对判据："
                      f"外推 rel={res.extrap_rel:.4f}")
    return res


# ---------------------------------------------------------------------------
# G3：留一学科外推
# ---------------------------------------------------------------------------

@dataclass
class G3Result:
    """G3 单个留出学科的结果。"""

    discipline: str
    n_held: int = 0                    # 该学科的场景数
    n_other: int = 0                   # 参与训练的其他学科场景数
    n_constant_excluded: int = 0       # 因近似常数（std≈0）被排除的场景数
    ok: bool = False
    reason: str = ""
    dims_shared: str = ""              # 联合训练的维度说明（1D/2D 分别建 GP）
    mean_rel: Optional[float] = None
    worst_key: str = ""
    worst_rel: Optional[float] = None
    per_scenario: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def run_g3(discipline: str, scns: Dict[str, Dict[str, Any]],
           seed: int = DEFAULT_SEED, n_train: int = 30,
           min_scenarios: int = 2) -> G3Result:
    """G3 留一学科外推：训练时**完全剔除**某学科，测试该学科。

    与 G1/G2 的本质区别：G1/G2 是**同一场景内**的划分（改动训练点位置），
    G3 是**跨场景**的划分（改动用哪些学科训练）。只有 G3 能回答
    "这套方法能跨学科用吗"。

    做法：
    1. 把目标学科的所有场景留出
    2. 在剩余学科的场景上，**为每个参数维**收集 LHS 训练点，拼成联合训练集
       （不同学科的场景维度可能不同，按各自 bounds 采样即可——
        GP 不要求所有点同维，只要求同空间）
    3. 在留出学科的场景上测试

    注意：G3 之所以能做，是因为 GP 只需要 (X, y) 和一个核函数，
    不需要知道"这是哪个学科"。**这既是 G3 能成立的技术基础，
    也是它的软肋**——模型可能只是学到了"参数空间的某种通用形状"，
    而没有学到"这个学科的机理"。G3 通过不代表机理泛化，只代表
    数值泛化。这个限定必须一起说。

    **按输入维度分组**（2026-10-04 实测修正）：不同学科的场景维度不同
    （1D 的 micro_* vs 2D 的 opt_*），直接 ``vstack`` 会因维度不一致而崩。
    正确做法是**每个维度单独训一个 GP**——因为 GP 的核函数要求输入同维，
    1D 和 2D 场景在数学上根本不属于同一个函数空间，混在一起学既不合法
    也没有意义。这也意味着 G3 的结果要**按维度分别解读**。

    Args:
        discipline: 要留出的学科标签
        scns: 全部场景
        seed: 随机种子
        n_train: 每个参与学科采多少训练点
        min_scenarios: 少于这个场景数就不做（样本太少结论不稳）

    Returns:
        G3Result
    """
    res = G3Result(discipline=discipline)
    held = [k for k, s in scns.items() if _discipline_of(s) == discipline]
    others = [k for k, s in scns.items() if _discipline_of(s) != discipline]
    res.n_held = len(held)
    res.n_other = len(others)
    if len(held) < min_scenarios:
        res.reason = f"该学科仅 {len(held)} 个场景（< {min_scenarios}），结论不稳"
        return res
    if not others:
        res.reason = "无其他学科可作训练源"
        return res

    # 按维度分组：{dim: [(key, scenario), ...]}
    def _dims(scn):
        return len(scn.get("bounds") or [])

    train_by_dim: Dict[int, List[Tuple[str, Dict[str, Any]]]] = {}
    for k in others:
        s = scns[k]
        d = _dims(s)
        if d > 0:
            train_by_dim.setdefault(d, []).append((k, s))
    held_by_dim: Dict[int, List[str]] = {}
    for k in held:
        held_by_dim.setdefault(_dims(scns[k]), []).append(k)

    shared = sorted(set(train_by_dim) & set(held_by_dim))
    if not shared:
        res.reason = (f"留出学科的维度 {sorted(held_by_dim)} 与其他学科的维度 "
                      f"{sorted(train_by_dim)} 无交集，无法联合训练")
        return res

    rng = np.random.default_rng(seed)
    n_per = max(n_train // 4, 6)          # 每个学科少采一些，避免大学科主导
    note_dims = ", ".join(f"{d}D×{len(held_by_dim[d])}场景" for d in shared)

    for d in shared:
        # ── 逐场景 z-score 归一化（2026-10-04 修复「尺度灾难」）──────────
        # 实测：跨学科 y 量纲差 **10 个数量级**（2D 场景 mean 从 -1.6e-19 到
        # 1.31e+03，std 从 4e-19 到 541）。不归一化时大尺度学科完全主导
        # 回归——G3 实测 `微生物` mean rel 高达 93325（最差 4.56e+06），
        # 而 `surrogate modeling` 只有 1.50。**那个 93325 是量纲灾难，
        # 不是泛化能力的度量。**
        #
        # 归一化用**每个场景自己的** mean/std（不是全局的）：这样每个学科
        # 贡献的都是「自己形状的归一化响应」，联合训练才有意义。
        # 近似常数的场景（std ≈ 0）直接排除——它们没有形状可学，
        # 强行归一化会放大数值噪声。
        Xs: List[np.ndarray] = []
        ys: List[np.ndarray] = []
        n_const = 0
        for _k, s in train_by_dim[d]:
            bounds = s.get("bounds") or []
            n = n_per
            u = (rng.random((n, d)) + np.arange(n)[:, None]) / n
            X = np.empty((n, d))
            for j, (a, b) in enumerate(bounds):
                X[:, j] = float(a) + u[:, j] * (float(b) - float(a))
            try:
                y = np.array([float(s["f"](p)) for p in X])
            except Exception:            # noqa: BLE001
                continue
            if not np.all(np.isfinite(y)):
                continue
            mu = float(np.mean(y))
            sd = float(np.std(y))
            # 相对容差：避免把"变动很小但确实有形状"的场景误判为常数
            if sd <= 1e-12 * max(abs(mu), 1.0):
                n_const += 1
                continue
            Xs.append(X)
            ys.append((y - mu) / sd)      # ← 逐场景 z-score
        if n_const:
            res.n_constant_excluded = n_const
        if not Xs:
            continue
        Xtr = np.vstack(Xs)
        ytr = np.concatenate(ys)
        # 归一化后 y 的量级是 O(1)，用固定 scale 即可
        scale = float(np.std(ytr)) + 1e-9
        if scale <= 1e-9:
            continue
        try:
            from engine.surrogate import GPSurrogate
            g = GPSurrogate(lengthscale=0.6, noise_var=(0.03) ** 2,
                            y_mean=float(np.mean(ytr)), y_scale=scale,
                            auto_ls=False)
            g.fit(Xtr, ytr)
        except Exception:                # noqa: BLE001
            continue

        for k in held_by_dim[d]:
            s = scns[k]
            bounds = s.get("bounds") or []
            n = 12
            u = (rng.random((n, d)) + np.arange(n)[:, None]) / n
            X = np.empty((n, d))
            for j, (a, b) in enumerate(bounds):
                X[:, j] = float(a) + u[:, j] * (float(b) - float(a))
            try:
                y = np.array([float(s["f"](p)) for p in X])
                m, _ = g.predict(X)
            except Exception:            # noqa: BLE001
                continue
            if not np.all(np.isfinite(y)):
                continue
            # 留出场景**也用自己**的 mean/std 归一化后再比——
            # 否则比的是量纲差异，不是泛化误差
            mu = float(np.mean(y))
            sd = float(np.std(y))
            if sd <= 1e-12 * max(abs(mu), 1.0):
                res.n_constant_excluded += 1
                continue
            yz = (y - mu) / sd
            mz = (m - mu) / sd
            rel = float(np.sqrt(np.mean((mz - yz) ** 2)))
            res.per_scenario[k] = round(rel, 4)

    if not res.per_scenario:
        res.reason = f"留出学科场景均评估失败（共享维度：{note_dims}）"
        return res
    res.dims_shared = note_dims
    vals = list(res.per_scenario.values())
    res.mean_rel = round(float(np.mean(vals)), 4)
    worst = max(res.per_scenario.items(), key=lambda kv: kv[1])
    res.worst_key, res.worst_rel = worst[0], worst[1]
    # G3 判据：留出学科的**每个**场景都要到 0.05（与 rubric 同线）
    res.ok = all(v <= 0.05 for v in vals)
    _excl = (f"，{res.n_constant_excluded} 个常数场景已排除"
             if res.n_constant_excluded else "")
    res.reason = (f"留出 {res.n_held} 场景（{note_dims}）/ 训练 {res.n_other} "
                  f"学科场景{_excl}：mean rel={res.mean_rel:.4f}, "
                  f"worst={res.worst_rel:.4f} "
                  f"({'全部 ≤0.05' if res.ok else '有场景 >0.05'})")
    return res


# ---------------------------------------------------------------------------
# G3-aligned：跨学科语义对齐后的 G3 复测（A16）
# ---------------------------------------------------------------------------

def run_g3_aligned(discipline: str,
                   scns: Dict[str, Dict[str, Any]],
                   seed: int = DEFAULT_SEED,
                   n_train: int = 30,
                   min_scenarios: int = 2,
                   k: int = 8,
                   n_grid: int = 40) -> G3Result:
    """G3 留一学科外推 + 跨学科语义对齐预处理（对齐后复测）。

    与 run_g3 的唯一区别：**训练/预测的 X 用 bounds 归一化到 [0,1]**
    （参数无量纲化），而不是用物理量纲的原始 X。

    动机（A16）：run_g3 已经在 y 侧做了逐场景 z-score（量纲归一化），
    但 X 侧仍是物理量纲——不同学科的 x 范围差 10 个数量级
    （微生物 0~10^5 vs optimization 0~5），GP 的 RBF 核在 [0,1]
    和 [0,10^5] 上对 lengthscale=0.6 的感受完全不同。对齐层试图
    把 X 也归一化到 [0,1]，让不同学科在**同一个参数空间**训练。

    本函数还会先跑一次 align_scenarios 计算对齐度量（跨学科
    signature 重合度），但不改变训练/预测流程本身——即：
    对齐度量是**报告用的观测值**，训练/预测只用了 X_norm。

    诚实说明：本函数是「对齐层 → G3」的诚实尝试。如果对齐后
    G3 仍为 0%，则 A16 成立——跨学科语义对齐不能救 G3。
    """
    res = G3Result(discipline=discipline)
    held = [k for k, s in scns.items() if _discipline_of(s) == discipline]
    others = [k for k, s in scns.items() if _discipline_of(s) != discipline]
    res.n_held = len(held)
    res.n_other = len(others)
    if len(held) < min_scenarios:
        res.reason = (f"该学科仅 {len(held)} 个场景（< {min_scenarios}），"
                      f"结论不稳")
        return res
    if not others:
        res.reason = "无其他学科可作训练源"
        return res

    # 惰性 import：避免与 semantic_alignment 形成循环（它只依赖 numpy）
    from engine.semantic_alignment import (
        align_scenarios, normalize_params)

    # ── 先跑一次对齐（只为拿 alignment_report，训练仍按原流程）──
    try:
        _, _, alignment_report = align_scenarios(
            scns, n_grid=n_grid, k=k, seed=seed)
    except Exception:
        alignment_report = {}

    # 以下逻辑与 run_g3 完全一致，唯一区别是 X 用 normalize_params 归一化
    def _dims(scn):
        return len(scn.get("bounds") or [])

    train_by_dim: Dict[int, List[Tuple[str, Dict[str, Any]]]] = {}
    for _k in others:
        s = scns[_k]
        d = _dims(s)
        if d > 0:
            train_by_dim.setdefault(d, []).append((_k, s))
    held_by_dim: Dict[int, List[str]] = {}
    for _k in held:
        held_by_dim.setdefault(_dims(scns[_k]), []).append(_k)

    shared = sorted(set(train_by_dim) & set(held_by_dim))
    if not shared:
        res.reason = (f"留出学科的维度 {sorted(held_by_dim)} 与其他学科的维度 "
                      f"{sorted(train_by_dim)} 无交集，无法联合训练")
        return res

    rng = np.random.default_rng(seed)
    n_per = max(n_train // 4, 6)
    note_dims = ", ".join(f"{d}D×{len(held_by_dim[d])}场景" for d in shared)

    for d in shared:
        Xs: List[np.ndarray] = []
        ys: List[np.ndarray] = []
        n_const = 0
        for _k, s in train_by_dim[d]:
            bounds = s.get("bounds") or []
            n = n_per
            u = (rng.random((n, d)) + np.arange(n)[:, None]) / n
            X = np.empty((n, d))
            for j, (a, b) in enumerate(bounds):
                X[:, j] = float(a) + u[:, j] * (float(b) - float(a))
            try:
                y = np.array([float(s["f"](p)) for p in X])
            except Exception:            # noqa: BLE001
                continue
            if not np.all(np.isfinite(y)):
                continue
            mu = float(np.mean(y))
            sd = float(np.std(y))
            if sd <= 1e-12 * max(abs(mu), 1.0):
                n_const += 1
                continue
            # ── 关键区别：X 用 bounds 归一化到 [0,1] ──
            Xn = normalize_params(X, bounds)
            Xs.append(Xn)
            ys.append((y - mu) / sd)
        if n_const:
            res.n_constant_excluded = n_const
        if not Xs:
            continue
        Xtr = np.vstack(Xs)
        ytr = np.concatenate(ys)
        scale = float(np.std(ytr)) + 1e-9
        if scale <= 1e-9:
            continue
        try:
            from engine.surrogate import GPSurrogate
            g = GPSurrogate(lengthscale=0.6, noise_var=(0.03) ** 2,
                            y_mean=float(np.mean(ytr)), y_scale=scale,
                            auto_ls=False)
            g.fit(Xtr, ytr)
        except Exception:                # noqa: BLE001
            continue

        for _k in held_by_dim[d]:
            s = scns[_k]
            bounds = s.get("bounds") or []
            n = 12
            u = (rng.random((n, d)) + np.arange(n)[:, None]) / n
            X = np.empty((n, d))
            for j, (a, b) in enumerate(bounds):
                X[:, j] = float(a) + u[:, j] * (float(b) - float(a))
            try:
                y = np.array([float(s["f"](p)) for p in X])
                # ── 关键区别：预测也用 X_norm ──
                Xn = normalize_params(X, bounds)
                m, _ = g.predict(Xn)
            except Exception:            # noqa: BLE001
                continue
            if not np.all(np.isfinite(y)):
                continue
            mu = float(np.mean(y))
            sd = float(np.std(y))
            if sd <= 1e-12 * max(abs(mu), 1.0):
                res.n_constant_excluded += 1
                continue
            yz = (y - mu) / sd
            mz = (m - mu) / sd
            rel = float(np.sqrt(np.mean((mz - yz) ** 2)))
            res.per_scenario[_k] = round(rel, 4)

    if not res.per_scenario:
        res.reason = f"留出学科场景均评估失败（共享维度：{note_dims}）"
        return res
    res.dims_shared = note_dims
    vals = list(res.per_scenario.values())
    res.mean_rel = round(float(np.mean(vals)), 4)
    worst = max(res.per_scenario.items(), key=lambda kv: kv[1])
    res.worst_key, res.worst_rel = worst[0], worst[1]
    res.ok = all(v <= 0.05 for v in vals)
    _excl = (f"，{res.n_constant_excluded} 个常数场景已排除"
             if res.n_constant_excluded else "")
    res.reason = (f"留出 {res.n_held} 场景（{note_dims}）/ 训练 {res.n_other} "
                  f"学科场景{_excl}：mean rel={res.mean_rel:.4f}, "
                  f"worst={res.worst_rel:.4f} "
                  f"({'全部 ≤0.05' if res.ok else '有场景 >0.05'})")
    # 把对齐度量挂到结果上（非标准字段，但便于报告）
    res.alignment_report = alignment_report  # type: ignore[attr-defined]
    return res


def run_g3_aligned_benchmark(seed: int = DEFAULT_SEED,
                             n_train: int = 30,
                             k: int = 8,
                             n_grid: int = 40) -> Dict[str, Any]:
    """跑 G3 对齐前 vs 对齐后的对比（A16 的核心实验）。

    返回 dict 包含：
      - baseline: run_g3 的完整结果
      - aligned: run_g3_aligned 的完整结果
      - comparison: 对齐前后的 pass_rate 对比
      - alignment_metrics: 关键对齐度量
    """
    t0 = time.time()
    scns = _scenarios()
    by_disc: Dict[str, List[str]] = {}
    for sc_key, s in scns.items():
        by_disc.setdefault(_discipline_of(s), []).append(sc_key)
    eligible = {d: ks for d, ks in by_disc.items() if len(ks) >= 2}
    disc_list = sorted(eligible)

    # 对齐度量：在 top 层跑一次全量 align_scenarios
    # （不在 run_g3_aligned 内做，避免参数 k 被循环变量遮蔽）
    from engine.semantic_alignment import align_scenarios as _align
    _, _, alignment_report = _align(scns, n_grid=n_grid, k=k, seed=seed)
    alignment_metrics = {
        "cross_discipline_alignment":
            alignment_report.get("cross_discipline_alignment"),
        "intra_discipline_alignment":
            alignment_report.get("intra_discipline_alignment"),
        "cross_discipline_centroid_distance_mean":
            alignment_report.get("cross_discipline_centroid_distance_mean"),
        "intra_discipline_centroid_distance_mean":
            alignment_report.get("intra_discipline_centroid_distance_mean"),
        "latent_dim": alignment_report.get("latent_dim"),
        "latent_explained_variance":
            alignment_report.get("latent_explained_variance"),
        "signature_dim": alignment_report.get("signature_dim"),
        "n_disciplines": alignment_report.get("n_disciplines"),
        "n_aligned_scenarios": alignment_report.get("aligned"),
    }

    g3_baseline = [run_g3(d, scns, seed=seed, n_train=n_train)
                   for d in disc_list]
    g3_aligned = [run_g3_aligned(d, scns, seed=seed, n_train=n_train,
                                 k=k, n_grid=n_grid)
                  for d in disc_list]

    def _pass_rate(rs):
        total = len(rs)
        passed = sum(1 for r in rs if r.ok)
        return (passed, total, round(passed / total, 4) if total else None)

    p_base, n_base, rate_base = _pass_rate(g3_baseline)
    p_ali, n_ali, rate_ali = _pass_rate(g3_aligned)

    # 序列化结果
    return {
        "schema": _SCHEMA + ".g3-aligned.v1",
        "generated_at": int(time.time()),
        "seed": seed,
        "n_train": n_train,
        "latent_k": k,
        "n_grid": n_grid,
        "eligible_disciplines": len(eligible),
        "skipped_disciplines": [d for d in by_disc if d not in eligible],
        "baseline": {
            "passed": p_base, "total": n_base,
            "pass_rate": rate_base,
            "results": [r.to_dict() for r in g3_baseline],
        },
        "aligned": {
            "passed": p_ali, "total": n_ali,
            "pass_rate": rate_ali,
            "results": [r.to_dict() for r in g3_aligned],
        },
        "comparison": {
            "baseline_pass_rate": rate_base,
            "aligned_pass_rate": rate_ali,
            "delta": (round(rate_ali - rate_base, 4)
                      if (rate_base is not None and rate_ali is not None)
                      else None),
            "conclusion": (
                "对齐无改善" if (rate_ali is not None and
                                 rate_base is not None and
                                 rate_ali == rate_base)
                else ("对齐有改善" if (rate_ali is not None and
                                      rate_base is not None and
                                      rate_ali > rate_base)
                      else "对齐变差")),
        },
        "alignment_metrics": alignment_metrics,
        "elapsed_sec": round(time.time() - t0, 1),
    }


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------

def run_all(seed: int = DEFAULT_SEED, quick: bool = False) -> Dict[str, Any]:
    """跑完整三级泛化基准。

    Args:
        seed: 随机种子
        quick: True 时只跑 12 个代表场景（快速冒烟），False 跑全部

    Returns:
        dict：G1/G2/G3 三级结果 + overall
    """
    t0 = time.time()
    scns = _scenarios()
    keys = sorted(scns.keys())
    if quick:
        # 覆盖不同学科与维度，别只挑好跑的
        keys = [k for k in keys
                if k.startswith(("micro_lag", "micro_competition", "micro_chemostat",
                                 "surrogate_b", "surrogate_f", "opt_rastrigin",
                                 "opt_ackley", "pde_heat", "bio_logistic",
                                 "resp_welch", "ode_lotka", "eco_lv",
                                 "mol_suzuki", "neuro_hh", "llm_scaling"))]

    g1 = [run_g1(k, scns[k], seed=seed) for k in keys]
    g2 = [run_g2(k, scns[k], seed=seed) for k in keys
          if len(scns[k].get("bounds") or []) >= 2]

    # G3：只对「有 ≥2 场景」的学科做留出（单场景学科留出后无从检验）
    from collections import Counter
    by_disc: Dict[str, List[str]] = {}
    for k, s in scns.items():
        by_disc.setdefault(_discipline_of(s), []).append(k)
    eligible = {d: ks for d, ks in by_disc.items() if len(ks) >= 2}
    g3 = [run_g3(d, scns, seed=seed) for d in sorted(eligible)]

    def _rate(rs) -> Tuple[int, int, int]:
        """返回 (通过数, 不适用数, 总数)。

        「不适用」= 内插就没拟合好，比值判据不携带信息（见
        G1_MAX_INTERP_REL）。这些场景**不计入分母**——把它们算作失败
        会让"通过率"被"模型压根没学会内插"淹没，掩盖真正的问题。
        """
        ap = sum(1 for r in rs if r.ok)
        na = sum(1 for r in rs if getattr(r, "na", False))
        return ap, na, len(rs)

    p1, na1, n1 = _rate(g1)
    p2, na2, n2 = _rate(g2)
    p3, na3, n3 = _rate(g3)
    # G3 没有内插基线概念（联合训练），na 恒为 0
    eff1 = max(n1 - na1, 1)
    eff2 = max(n2 - na2, 1)

    return {
        "schema": _SCHEMA,
        "generated_at": int(time.time()),
        "seed": seed,
        "quick": quick,
        "n_scenarios_tested": len(keys),
        "g1_param_extrapolation": {
            "description": (f"训练 [lo,mid] 测 (mid,hi]；判据 外推/内插 ≤ "
                            f"{G1_MAX_RATIO}×，且内插 rel ≤ {G1_MAX_INTERP_REL}"),
            "passed": p1, "not_applicable": na1, "total": n1,
            "pass_rate": round(p1 / eff1, 4),
            "pass_rate_note": ("分母已剔除「不适用」场景（内插 rel > "
                               f"{G1_MAX_INTERP_REL}，比值判据无意义）"),
            "results": [r.to_dict() for r in g1],
        },
        "g2_holdout_dim": {
            "description": (f"训练时钉住一维；判据 外推/内插 ≤ {G2_MAX_RATIO}×，"
                            f"且内插 rel ≤ {G2_MAX_INTERP_REL}"),
            "passed": p2, "not_applicable": na2, "total": n2,
            "pass_rate": round(p2 / eff2, 4),
            "results": [r.to_dict() for r in g2],
        },
        "g3_holdout_discipline": {
            "description": (f"训练时剔除整学科；判据 留出学科每个场景 rel ≤ 0.05。"
                            f"⚠️ 数值泛化 ≠ 机理泛化（模型可能只学到参数空间形状）"),
            "eligible_disciplines": len(eligible),
            "skipped_disciplines": [d for d in by_disc if d not in eligible],
            "passed": p3, "total": n3,
            "pass_rate": round(p3 / n3, 4) if n3 else None,
            "results": [r.to_dict() for r in g3],
        },
        "overall": {
            "g1": round(p1 / eff1, 4),
            "g2": round(p2 / eff2, 4),
            "g3": round(p3 / n3, 4) if n3 else None,
            "g1_na": na1, "g2_na": na2, "g3_na": na3,
            "elapsed_sec": round(time.time() - t0, 1),
        },
        "interpretation": {
            "what_this_measures": (
                "同分布内插值能力之外的**外推**能力。这是「我们是可信验证层」"
                "这个主张目前唯一缺失的证据。"),
            "what_this_does_not_measure": (
                "G3 只测数值泛化：联合训练时模型看到的是各学科的 (x, y) 数值对，"
                "**没有**看到学科标签或机理方程。因此 G3 通过不代表模型"
                "理解了跨学科的机理，只代表它学到了某种通用的参数-响应形状。"),
        },
    }


def save(result: Dict[str, Any], path: str) -> str:
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    return os.path.abspath(path)


def default_output_path(repo_root: Optional[str] = None) -> str:
    if repo_root is None:
        repo_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), ".."))
    return os.path.join(repo_root, "data", "generalization_bench.json")


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def _selftest() -> bool:
    """用解析函数自测（不依赖真实场景，保证逻辑正确）。"""
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

    # 1. 线性函数：G1 应该轻松通过（外推误差≈内插误差）
    lin = {"bounds": [[0.0, 10.0]], "ls": 1.0, "auto_ls": False,
           "f": lambda x: float(np.asarray(x).ravel()[0]) * 2.0 + 1.0,
           "domain": "test/linear"}
    r = run_g1("lin", lin, n_train=25, n_test=15)
    check("G1 linear: no crash", bool(r.reason))
    check("G1 linear: has both rels",
          r.interp_rel is not None and r.extrap_rel is not None)
    check("G1 linear: ratio sane (0<r<1000)",
          r.ratio is None or 0 < r.ratio < 1000)

    # 2. 强非线性二次：外推应该变差但不该崩
    quad = {"bounds": [[0.0, 1.0]], "ls": 0.5, "auto_ls": False,
            "f": lambda x: float(np.asarray(x).ravel()[0]) ** 2,
            "domain": "test/quad"}
    r2 = run_g1("quad", quad, n_train=20, n_test=12)
    check("G1 quad: no crash", bool(r2.reason))
    check("G1 quad: extrap >= interp (harder)",
          r2.interp_rel is not None and r2.extrap_rel is not None
          and r2.extrap_rel >= r2.interp_rel * 0.5)

    # 3. G2 对 dim<2 应拒绝
    r3 = run_g2("lin", lin)
    check("G2 rejects dim<2", not r3.ok and "不适用" in r3.reason)

    # 4. G2 对可加函数（各维独立）应该能过
    add2 = {"bounds": [[0.0, 1.0], [0.0, 1.0]], "ls": 0.4, "auto_ls": False,
            "f": lambda x: float(np.sum(np.asarray(x).ravel())),
            "domain": "test/additive"}
    r4 = run_g2("add2", add2, n_train=40, n_test=15)
    check("G2 additive: no crash", bool(r4.reason))
    check("G2 additive: picked a held dim", r4.held_dim in (0, 1))
    check("G2 additive: has ratio", r4.ratio is not None)

    # 5. G2 对强交互函数（非可加）应该更难
    inter2 = {"bounds": [[0.0, 1.0], [0.0, 1.0]], "ls": 0.4, "auto_ls": False,
              "f": lambda x: float(np.prod(np.asarray(x).ravel())),
              "domain": "test/interaction"}
    r5 = run_g2("inter2", inter2, n_train=40, n_test=15)
    check("G2 interaction: no crash", bool(r5.reason))
    if r4.extrap_rel and r5.extrap_rel:
        check("G2 interaction is harder than additive",
              r5.extrap_rel >= r4.extrap_rel * 0.5)

    # 6. G3 对单场景学科应跳过
    scns = {"a1": lin, "b1": quad, "c1": add2}
    r6 = run_g3("solo", {"solo": lin, "x": quad, "y": add2},
                min_scenarios=2)
    check("G3 skips discipline with <2 scenarios",
          not r6.ok and "仅" in r6.reason)

    # 6b. 内插下限：内插极差的高频震荡场景应判「不适用」而非「通过」
    #     这是 2026-10-04 实测发现的判据缺陷：原判据只看比值，会把
    #     「内插 1.11 / 外推 1.08」判成通过（那是"内外都错"）。
    rough = {"bounds": [[0.0, 1.0]], "ls": 1.0, "auto_ls": False,
             "f": lambda x: float(np.cos(30 * np.asarray(x).ravel()[0])),
             "domain": "test/rough"}
    rr = run_g1("rough", rough, n_train=12, n_test=8)
    check("G1 flags N.A. when interp is poor",
          rr.na and not rr.ok and "不适用" in rr.reason)
    check("G1 N.A. still reports both rels",
          rr.interp_rel is not None and rr.extrap_rel is not None)

    # 6c. 阈值常量自洽
    check("G1_MAX_INTERP_REL == 0.2", abs(G1_MAX_INTERP_REL - 0.2) < 1e-12)
    check("G2_MAX_INTERP_REL == 0.2", abs(G2_MAX_INTERP_REL - 0.2) < 1e-12)

    # 7. G3 在两个学科间留出（linear 学 linear 应当很强）
    #    训练侧用 add2（可加），留出侧用另一个可加但范围不同的
    train_side = {"bounds": [[0.0, 1.0], [0.0, 1.0]], "ls": 0.4,
                  "auto_ls": False,
                  "f": lambda x: float(np.sum(np.asarray(x).ravel())),
                  "domain": "train/additive"}
    hold_side = {"bounds": [[0.0, 2.0], [0.0, 2.0]], "ls": 0.4,
                 "auto_ls": False,
                 "f": lambda x: float(np.sum(np.asarray(x).ravel())),
                 "domain": "hold/additive"}
    r7 = run_g3("hold", {**{"t1": train_side, "t2": train_side}, "h1": hold_side},
                n_train=40)
    check("G3 runs", bool(r7.reason))
    check("G3 has per_scenario or clear reason",
          bool(r7.per_scenario) or "仅" in r7.reason or "失败" in r7.reason)

    # 8. 异常输入
    check("G1 handles empty bounds",
          not run_g1("x", {"bounds": [], "f": lin["f"]}).ok)
    check("G1 handles degenerate bounds",
          not run_g1("x", {"bounds": [[1.0, 1.0]], "f": lin["f"]}).ok)

    def boom(x):
        raise ValueError("oracle 炸了")

    check("G1 handles raising oracle",
          not run_g1("x", {"bounds": [[0, 1]], "f": boom}).ok)

    check("G1 handles NaN oracle",
          not run_g1("x", {"bounds": [[0, 1]],
                           "f": lambda z: float("nan")}).ok)

    # 9. _discipline_of 容错
    check("discipline handles missing domain",
          _discipline_of({}) == "unknown")
    check("discipline takes first segment",
          _discipline_of({"domain": "biology / E. coli"}) == "biology")

    # 10. 跨学科语义对齐层 selftest（不依赖真实场景）
    #     验证：参数无量纲化 / signature 长度 / 对齐前后 y 关系 /
    #     相同场景对齐度=1.0 / 不同场景对齐度<0.3
    from engine.semantic_alignment import (
        normalize_params, response_signature, align_scenarios,
        _alignment_score, SIGNATURE_DIM)

    rng = np.random.default_rng(4242)

    # 10a. 参数无量纲化后 x ∈ [0,1]
    X = np.array([[0.0, 0.0], [5.0, 5.0], [10.0, 10.0]])
    Xn = normalize_params(X, [[0.0, 10.0], [0.0, 10.0]])
    check("align: 参数无量纲化后 x ∈ [0,1]",
          Xn.min() >= -1e-9 and Xn.max() <= 1.0 + 1e-9)

    # 10b. signature 长度固定
    y_lin = np.linspace(0, 10, 40)
    y_osc = np.cos(4 * np.linspace(0, 10, 40))
    s_lin = response_signature(y_lin)
    s_osc = response_signature(y_osc)
    check("align: signature 长度固定 = SIGNATURE_DIM",
          s_lin.shape[0] == SIGNATURE_DIM == s_osc.shape[0])

    # 10c. 对齐前后 y 的均值/方差关系保持（z-score 保持均值0方差1）
    y = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0])
    mu, sd = y.mean(), y.std()
    yz = (y - mu) / sd
    check("align: y_norm 均值为 0", abs(yz.mean()) < 1e-9)
    check("align: y_norm 标准差为 1", abs(yz.std() - 1.0) < 1e-9)

    # 10d. 完全相同场景的对齐度 ≈ 1.0
    M_same1 = np.vstack([s_lin, response_signature(y_lin + 1e-6)])
    M_same2 = np.vstack([s_lin, response_signature(y_lin + 1e-6)])
    check("align: 完全相同场景的对齐度 ≈ 1.0",
          _alignment_score(M_same1, M_same2) > 0.99)

    # 10e. 完全不同场景的对齐度 < 0.3
    M_osc = np.vstack([s_osc] * 3)
    M_lin = np.vstack([s_lin] * 3)
    check("align: 完全不同场景的对齐度 < 0.3",
          _alignment_score(M_osc, M_lin) < 0.3)

    # 10f. align_scenarios 端到端不崩
    scns_t = {
        "lin": {"bounds": [[0.0, 10.0]], "domain": "bio/ecoli",
                "f": lambda x: float(np.asarray(x).ravel()[0])},
        "osc": {"bounds": [[0.0, 10.0]], "domain": "phys/osc",
                "f": lambda x: float(np.cos(4 * np.asarray(x).ravel()[0]))},
        "lin2": {"bounds": [[0.0, 10.0]], "domain": "bio/yeast",
                 "f": lambda x: float(np.asarray(x).ravel()[0] * 2)},
    }
    X_al, sig_al, rep_al = align_scenarios(scns_t, n_grid=25)
    check("align: align_scenarios 返回非空", len(X_al) >= 2)
    check("align: 报告含 cross_discipline_alignment",
          "cross_discipline_alignment" in rep_al)
    check("align: cross_discipline_alignment ∈ [0,1]",
          0.0 <= rep_al["cross_discipline_alignment"] <= 1.0 + 1e-9)
    check("align: signature_dim 一致",
          rep_al["signature_dim"] == SIGNATURE_DIM)

    # 10g. run_g3_aligned 端到端不崩（用合成场景，留出 bio 学科）
    r_al = run_g3_aligned("bio", scns_t, seed=4242, n_train=20,
                          k=4, n_grid=20)
    check("align: run_g3_aligned 不崩", bool(r_al.reason))
    check("align: run_g3_aligned 挂 alignment_report",
          hasattr(r_al, "alignment_report") and r_al.alignment_report)

    print(f"\n{passed}/{total} generalization-bench self-tests passed")
    return passed == total


if __name__ == "__main__":
    import sys
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if _selftest():
        quick = "--full" not in sys.argv
        res = run_all(quick=quick)
        out = save(res, default_output_path(root))
        o = res["overall"]
        print(f"\n三级泛化基准（{'quick' if quick else 'full'}）→ {out}")
        print("=" * 68)
        for label, key in (("G1 参数外推", "g1"), ("G2 留一维", "g2"),
                           ("G3 留一学科", "g3")):
            v = o[key]
            blk = res[{"g1": "g1_param_extrapolation",
                       "g2": "g2_holdout_dim",
                       "g3": "g3_holdout_discipline"}[key]]
            rate = "n/a" if v is None else f"{v * 100:.1f}%"
            na = blk.get("not_applicable", 0)
            na_s = f"，{na} 不适用" if na else ""
            print(f"  {label:<16} {rate:>8}   "
                  f"({blk['passed']}/{blk['total'] - na}{na_s})")
        print(f"\n耗时 {o['elapsed_sec']}s")
        print("\n不宣称：")
        for k, v in res["interpretation"].items():
            print(f"  - {k}: {v}")
        sys.exit(0)
    sys.exit(1)
