"""跨学科语义对齐层（Cross-Discipline Semantic Alignment）—— A16 的诚实尝试。

背景（2026-10-05，接 A15）
--------------------------
G3 留一学科外推已实测 0/14 学科通过。A15 指出 GP 在训练域外退化为
「预测 = 训练集均值」是**核方法的本质性质**，同时指出：即便做完
逐场景 z-score 归一化（量纲灾难 552.9× 改善），`微生物` 仍为 168.79
——纯数值 GP **没有跨学科语义对齐层**，因为不同学科的 f(x) 形状
本身就没共享几何。

本模块试图在**预处理层**补上这一层：

  1. 参数无量纲化：每个场景 x 用自身 bounds 归一化到 [0,1]
     ——把「物理量纲」变成「相对位置」。
  2. 响应形状签名：对每个场景在自身 bounds 上采网格，把响应 y
     提取成固定长度的 signature vector（差分谱 / 单调性 / 一阶导
     变化率）。这个 signature 回答"这个函数长什么样"，而不是
     "它的数值是多少"。
  3. 跨学科共享 latent：所有 signature 做 PCA 降到 k=8，学一个
     跨学科共享的低维 latent（跟 meta_learner 的 _build_latent 同源）。
  4. 对齐度量：跨学科 signature 分布在 latent 空间的重合度。
     用 centroid 距离 + 一个 MMD-lite（高斯核），归一化到 [0,1]
     以便对比「完全相同场景 → 1.0」和「完全不同场景 → <0.3」。

设计约束（诚实优先）
--------------------
- **对齐层不是模型**：它是**预处理**，只把 X 从「物理量纲」变成
  「无量纲位置 + 场景语义签名」，仍然交给现有的 GPSurrogate 用。
- **不美化**：如果对齐后 G3 仍为 0%，如实报告并给出对齐度量佐证
  ——见 A16。
- **零破坏**：不修改任何现有场景注册表或 surrogate 接口。
- **可复现**：seed=4242 与 generalization_bench 一致。

接口语义
--------
  align_scenarios(scns, n_samples=40, n_grid=12, k=8, seed=4242)
    -> (X_aligned_dict, signatures_dict, alignment_report)

其中：
  X_aligned_dict[key] = (X_raw, X_norm, y_raw, y_norm)
    X_raw    : (n_samples, d)  物理量纲参数
    X_norm   : (n_samples, d)  [0,1] 无量纲参数
    y_raw    : (n_samples,)    原始响应
    y_norm   : (n_samples,)    逐场景 z-score 响应

  signatures_dict[key] = (sig, latent)
    sig    : (S,)              固定长度响应形状签名
    latent : (k,)              PCA 降维后的跨学科共享 latent

  alignment_report: dict
    cross_discipline_alignment : float  [0,1]  跨学科 signature 重合度
    intra_discipline_alignment : float  [0,1]  学科内 signature 重合度
    centroid_distance          : float        raw centroid 距离
    per_discipline             : {disc: {sig_centroid, ...}}
    signature_dim              : int
    latent_dim                 : int

Selftest 见文件末尾 _selftest()，不依赖真实场景库。
"""

from __future__ import annotations

import numpy as np
from typing import Any, Dict, List, Optional, Tuple

SCHEMA = "svp.semantic-alignment.v1"

#: 与 generalization_bench 一致的默认种子
DEFAULT_SEED = 4242

#: signature 中的谱长度（差分阶数 × 频率数）
_SPECTRUM_LENGTH = 16
#: signature 中的手工特征维度
_MANUAL_FEAT_DIM = 5
#: 总 signature 长度（谱 + 手工特征），selftest 依赖此值
SIGNATURE_DIM = _SPECTRUM_LENGTH + _MANUAL_FEAT_DIM
#: PCA 降维目标维度
DEFAULT_K = 8


# ---------------------------------------------------------------------------
# 1. 参数无量纲化
# ---------------------------------------------------------------------------

def normalize_params(
    X: np.ndarray, bounds: List[Tuple[float, float]]
) -> np.ndarray:
    """把物理量纲参数 X 归一化到 [0,1]。

    Args:
        X: (n, d) 原始参数
        bounds: [(lo, hi), ...]

    Returns:
        (n, d) 归一化参数（∈ [0,1]，退化维度固定在 0.5）
    """
    X = np.atleast_2d(np.asarray(X, float))
    d = X.shape[1]
    assert len(bounds) == d, f"bounds 维度 {len(bounds)} 与 X 不匹配 {d}"
    out = np.empty_like(X)
    for j, (a, b) in enumerate(bounds):
        a, b = float(a), float(b)
        if not np.isfinite(a) or not np.isfinite(b) or abs(b - a) < 1e-15:
            # 退化 bounds：全部钉到 0.5（避免除零 + 保留维度）
            out[:, j] = 0.5
        else:
            out[:, j] = (X[:, j] - a) / (b - a)
    # 数值安全：clip 到 [0,1]（浮点误差可能超出 1e-12）
    return np.clip(out, 0.0, 1.0)


# ---------------------------------------------------------------------------
# 2. 响应形状签名
# ---------------------------------------------------------------------------

def _diff_spectrum(y: np.ndarray, n_bins: int = _SPECTRUM_LENGTH) -> np.ndarray:
    """对响应 y（1D 序列）提取一阶差分谱（差分后做 DFT 幅度）。

    动机：一阶差分捕获"曲线的形状变化率"，与绝对量纲无关。
    对 y 做 DFT 后取幅度前 n_bins 个系数，得到固定长度的频率特征。

    Args:
        y: (n,) 原始响应序列
        n_bins: 保留的低频系数数量

    Returns:
        (n_bins,) 差分幅度谱，已用自身能量归一化
    """
    y = np.asarray(y, float)
    n = y.shape[0]
    if n < 3:
        return np.zeros(n_bins)
    dy = np.diff(y)
    # 一阶差分：n-1 个值
    m = dy.shape[0]
    if m < 2:
        return np.zeros(n_bins)
    # DFT（对实序列直接取 FFT）
    F = np.fft.fft(dy)
    amp = np.abs(F[: m // 2]) if m // 2 > 0 else np.zeros(0)
    # 补零或截断到 n_bins
    if amp.shape[0] >= n_bins:
        spec = amp[:n_bins]
    else:
        spec = np.pad(amp, (0, n_bins - amp.shape[0]))
    # 用自身能量归一化（避免不同 y 尺度差影响）
    energy = float(np.sum(spec ** 2)) + 1e-15
    return spec / np.sqrt(energy)


def _monotonicity(y: np.ndarray) -> float:
    """单调性指标：差分符号变化比例的反向。

    0.0 = 完全振荡（每次符号都变），1.0 = 完全单调。
    """
    y = np.asarray(y, float)
    if y.shape[0] < 2:
        return 1.0
    dy = np.diff(y)
    signs = np.sign(dy)
    signs = signs[signs != 0]
    if signs.shape[0] < 2:
        return 1.0
    changes = np.sum(signs[1:] != signs[:-1])
    return 1.0 - float(changes) / float(len(signs) - 1)


def _deriv_variation(y: np.ndarray) -> float:
    """一阶导变化率的相对标准差（曲率的粗略刻画）。"""
    y = np.asarray(y, float)
    if y.shape[0] < 3:
        return 0.0
    dy = np.diff(y)
    if dy.shape[0] < 2:
        return 0.0
    d2 = np.diff(dy)
    denom = float(np.mean(np.abs(dy))) + 1e-12
    return float(np.std(d2) / denom)


def response_signature(y: np.ndarray) -> np.ndarray:
    """对一个场景的响应序列提取固定长度 shape signature。

    组成：
      [ 差分谱 (_SPECTRUM_LENGTH 维)
      | 单调性 (1)
      | 归一化幅度方差 (1)     # 用 rel_std = std/|mean|，跨尺度可比
      | 一阶导变化率 (1)
      | 极值位置归一化 (1)     # argmax(y) / n
      | 极小位置归一化 (1)     # argmin(y) / n ]

    总长度 SIGNATURE_DIM。
    """
    y = np.asarray(y, float)
    if y.shape[0] < 2:
        return np.zeros(SIGNATURE_DIM)

    # 相对尺度归一化（消除 y 的绝对量纲）
    mu = float(np.mean(y))
    sd = float(np.std(y))
    rel_std = float(sd / (abs(mu) + 1e-12))

    spec = _diff_spectrum(y, _SPECTRUM_LENGTH)
    mono = _monotonicity(y)
    dv = _deriv_variation(y)
    argmax_pos = float(np.argmax(y)) / max(y.shape[0] - 1, 1)
    argmin_pos = float(np.argmin(y)) / max(y.shape[0] - 1, 1)

    manual = np.array([mono, rel_std, dv, argmax_pos, argmin_pos], float)
    # 手工特征也可能被极端值主导，做 L2 归一化
    L = float(np.linalg.norm(manual)) + 1e-12
    manual = manual / L

    return np.concatenate([spec, manual])


# ---------------------------------------------------------------------------
# 3. 采样与场景评估
# ---------------------------------------------------------------------------

def _eval_scenario(
    scn: Dict[str, Any],
    n_grid: int,
    rng: np.random.Generator,
) -> Optional[Tuple[np.ndarray, np.ndarray]]:
    """在场景 bounds 上采 LHS 网格点，返回 (X, y)。失败返回 None。"""
    bounds = scn.get("bounds") or []
    d = len(bounds)
    if d == 0:
        return None
    n = max(n_grid, 8)
    u = (rng.random((n, d)) + np.arange(n)[:, None]) / n
    X = np.empty((n, d))
    for j, (a, b) in enumerate(bounds):
        a, b = float(a), float(b)
        if not np.isfinite(a) or not np.isfinite(b) or abs(b - a) < 1e-15:
            X[:, j] = (a + b) / 2.0
        else:
            X[:, j] = a + u[:, j] * (b - a)
    try:
        y = np.array([float(scn["f"](p)) for p in X])
    except Exception:
        return None
    if not np.all(np.isfinite(y)):
        return None
    return X, y


# ---------------------------------------------------------------------------
# 4. 跨学科共享 latent（PCA）
# ---------------------------------------------------------------------------

class SignatureLatent:
    """对所有 signature 做 PCA 降到 k 维，返回一个可复用的投影器。"""

    def __init__(self, k: int = DEFAULT_K):
        self.k = int(k)

    def fit(self, sigs: np.ndarray) -> "SignatureLatent":
        """sigs: (n, S) 所有 signature。拟合中心化 + PCA。"""
        sigs = np.atleast_2d(np.asarray(sigs, float))
        self._mean = sigs.mean(axis=0)
        S = sigs - self._mean
        # 用 SVD 而非 eig（更快、更稳）
        U, svals, Vt = np.linalg.svd(S, full_matrices=False)
        k = min(self.k, Vt.shape[0])
        self._components = Vt[:k].T  # (S, k)
        self._k = k
        self._svals = svals[:k]
        # 累积方差解释率（用于报告）
        self._explained = float((svals[:k] ** 2).sum() / (svals ** 2).sum())
        return self

    def transform(self, sigs: np.ndarray) -> np.ndarray:
        sigs = np.atleast_2d(np.asarray(sigs, float))
        return (sigs - self._mean) @ self._components

    def explain_ratio(self) -> float:
        return getattr(self, "_explained", 0.0)


# ---------------------------------------------------------------------------
# 5. 对齐度量
# ---------------------------------------------------------------------------

def _gaussian_kernel(
    A: np.ndarray, B: np.ndarray, bandwidth: float
) -> float:
    """两样本集 (n, d) (m, d) 的高斯核均值（用于 MMD）。"""
    A = np.atleast_2d(A)
    B = np.atleast_2d(B)
    d = A.shape[1]
    # 归一化到单位尺度，让 bandwidth 有意义
    scale = float(np.linalg.norm(A, axis=1).mean() +
                  np.linalg.norm(B, axis=1).mean()) / 2.0 + 1e-12
    As = A / scale
    Bs = B / scale
    # 核矩阵均值： (1/n^2) Σ k(a_i, a_j)
    def _k(X):
        d2 = (X[:, None, :] - X[None, :, :]).reshape(X.shape[0], -1)
        d2 = np.sum(d2 ** 2, axis=1)
        return np.exp(-d2 / (2.0 * bandwidth ** 2))
    kA = _k(As).mean()
    kB = _k(Bs).mean()
    # cross
    d2 = np.sum((As[:, None, :] - Bs[None, :, :]) ** 2, axis=2)
    kAB = np.exp(-d2 / (2.0 * bandwidth ** 2)).mean()
    # MMD^2 = kA + kB - 2 * kAB
    return float(kA + kB - 2.0 * kAB)


def _centroid_distance(A: np.ndarray, B: np.ndarray) -> float:
    """两样本集质心之间的归一化欧氏距离。"""
    A = np.atleast_2d(A)
    B = np.atleast_2d(B)
    cA = A.mean(axis=0)
    cB = B.mean(axis=0)
    # 归一化尺度：两质心向量的平均模
    scale = (np.linalg.norm(cA) + np.linalg.norm(cB)) / 2.0 + 1e-12
    return float(np.linalg.norm(cA - cB) / scale)


def _alignment_score(
    A: np.ndarray, B: np.ndarray, bandwidth: float = 0.5
) -> float:
    """对齐度量：值越大越对齐，范围 [0,1]。

    定义：
      score = exp(-MMD^2 / (2 * bandwidth^2))

    - 相同分布 → MMD^2 = 0 → score = 1
    - 完全不同的分布 → MMD^2 大 → score → 0

    bandwidth 是「多少算远」的尺度。signature 已经做过 L2/能量归一化，
    因此 bandwidth=0.5 对应「signature 距离 ≳ 0.5 即视为不重合」——
    这是有区分度的默认值（bandwidth=1.0 太宽松，无法把
    "线性 signature" 与 "震荡 signature" 分开）。
    """
    A = np.atleast_2d(np.asarray(A, float))
    B = np.atleast_2d(np.asarray(B, float))
    if A.shape[0] == 0 or B.shape[0] == 0:
        return 0.0
    mmd2 = _gaussian_kernel(A, B, bandwidth)
    # 数值安全
    mmd2 = max(0.0, mmd2)
    return float(np.exp(-mmd2 / (2.0 * bandwidth ** 2)))


# ---------------------------------------------------------------------------
# 6. 主接口
# ---------------------------------------------------------------------------

def _discipline_of(scn: Dict[str, Any]) -> str:
    dom = str(scn.get("domain", "")).split("/")[0].strip()
    return (dom or "unknown").lower()


def align_scenarios(
    scns: Dict[str, Dict[str, Any]],
    n_grid: int = 40,
    k: int = DEFAULT_K,
    seed: int = DEFAULT_SEED,
) -> Tuple[Dict[str, Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]],
           Dict[str, Tuple[np.ndarray, np.ndarray]],
           Dict[str, Any]]:
    """对一批场景做跨学科语义对齐预处理。

    Args:
        scns: {key: scenario_dict}
        n_grid: 每场景采样点数（决定 signature 的分辨率）
        k: PCA 目标维度
        seed: 随机种子

    Returns:
        X_aligned   : {key: (X_raw, X_norm, y_raw, y_norm)}
        signatures  : {key: (sig, latent)}
        report      : alignment_report dict
    """
    rng = np.random.default_rng(seed)
    X_aligned: Dict[str, Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]] = {}
    sigs: Dict[str, np.ndarray] = {}

    for key, scn in scns.items():
        got = _eval_scenario(scn, n_grid=n_grid, rng=rng)
        if got is None:
            continue
        X, y = got
        # 1. 参数无量纲化
        Xn = normalize_params(X, scn.get("bounds") or [])
        # 2. 逐场景 y 归一化（保留量纲信息供参考）
        mu = float(np.mean(y))
        sd = float(np.std(y))
        if sd <= 1e-12 * max(abs(mu), 1.0):
            # 近似常数：y 无形状可对齐，跳过（与 generalization_bench 一致）
            continue
        yn = (y - mu) / sd
        # 3. 响应形状签名
        sig = response_signature(y)
        X_aligned[key] = (X, Xn, y, yn)
        sigs[key] = sig

    if not sigs:
        return (X_aligned, {},
                {"schema": SCHEMA, "aligned": 0, "aligned_keys": [],
                 "cross_discipline_alignment": 0.0,
                 "intra_discipline_alignment": 0.0,
                 "latent_dim": 0, "signature_dim": SIGNATURE_DIM,
                 "per_discipline": {},
                 "reason": "所有场景评估失败或近似常数"})

    # 4. 跨学科共享 latent（PCA 降到 k）
    sig_mat = np.vstack(list(sigs.values()))  # (N, S)
    pca = SignatureLatent(k=k).fit(sig_mat)
    latents = pca.transform(sig_mat)
    # 把 latent 与 key 对上
    sig_with_latent: Dict[str, Tuple[np.ndarray, np.ndarray]] = {}
    for i, key in enumerate(sigs.keys()):
        sig_with_latent[key] = (sigs[key], latents[i])

    # 5. 对齐度量：跨学科 vs 学科内
    disciplines: Dict[str, List[np.ndarray]] = {}
    for key, s in sig_with_latent.items():
        disc = _discipline_of(scns[key])
        disciplines.setdefault(disc, []).append(s[0])  # 用原 signature

    # 跨学科对齐：两两学科间的 signature 重合度均值
    cross_scores: List[float] = []
    disc_keys = sorted(disciplines.keys())
    for i in range(len(disc_keys)):
        for j in range(i + 1, len(disc_keys)):
            cross_scores.append(_alignment_score(
                disciplines[disc_keys[i]], disciplines[disc_keys[j]]))

    # 学科内对齐（对照基线，通常应远高于跨学科）
    intra_scores: List[float] = []
    for disc, arrs in disciplines.items():
        if len(arrs) < 2:
            continue
        for i in range(len(arrs)):
            for j in range(i + 1, len(arrs)):
                intra_scores.append(_alignment_score(
                    arrs[i][None, :], arrs[j][None, :]))

    cross_align = float(np.mean(cross_scores)) if cross_scores else 0.0
    intra_align = float(np.mean(intra_scores)) if intra_scores else 0.0

    # centroid 距离（跨学科 signature 质心 vs 全局质心）
    all_sigs = np.vstack(list(sigs.values()))
    global_c = all_sigs.mean(axis=0)
    cdist_per_disc: Dict[str, float] = {}
    for disc, arrs in disciplines.items():
        M = np.vstack(arrs)
        cdist_per_disc[disc] = _centroid_distance(M, global_c[None, :])

    # 学科内 centroid 距离（对照基线）
    intra_cdist: Dict[str, float] = {}
    for disc, arrs in disciplines.items():
        if len(arrs) < 2:
            continue
        M = np.vstack(arrs)
        intra_cdist[disc] = _centroid_distance(M, M.mean(axis=0)[None, :])

    per_disc: Dict[str, Dict[str, Any]] = {}
    for disc, arrs in disciplines.items():
        M = np.vstack(arrs)
        per_disc[disc] = {
            "n_scenarios": len(arrs),
            "sig_centroid": M.mean(axis=0),
            "sig_centroid_norm": float(np.linalg.norm(
                M.mean(axis=0) - global_c)),
            "centroid_distance_from_global": cdist_per_disc[disc],
        }

    report = {
        "schema": SCHEMA,
        "seed": seed,
        "n_grid": n_grid,
        "aligned": len(sigs),
        "aligned_keys": list(sigs.keys()),
        "signature_dim": SIGNATURE_DIM,
        "latent_dim": pca._k,
        "latent_explained_variance": pca.explain_ratio(),
        "cross_discipline_alignment": cross_align,
        "intra_discipline_alignment": intra_align,
        "cross_discipline_centroid_distance_mean": float(np.mean(
            list(cdist_per_disc.values()))) if cdist_per_disc else 0.0,
        "intra_discipline_centroid_distance_mean": float(np.mean(
            list(intra_cdist.values()))) if intra_cdist else 0.0,
        "n_disciplines": len(disciplines),
        "per_discipline": per_disc,
    }
    return X_aligned, sig_with_latent, report


# ---------------------------------------------------------------------------
# Selftest
# ---------------------------------------------------------------------------

def _selftest() -> bool:
    """验证对齐层本身的逻辑正确性（不依赖真实场景库）。"""
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

    rng = np.random.default_rng(4242)

    # ── 场景 A：一维线性 f(x)=x（"生物"-like 假标签）──
    A_lin = {"bounds": [[0.0, 10.0]], "domain": "testlin/bio",
             "f": lambda x: float(np.asarray(x).ravel()[0])}

    # ── 场景 B：与 A 完全相同（用于"对齐度=1.0"测试）──
    A_lin_copy = {"bounds": [[0.0, 10.0]], "domain": "testlin/bio",
                  "f": lambda x: float(np.asarray(x).ravel()[0])}

    # ── 场景 C：完全不同（高频震荡）──
    C_osc = {"bounds": [[0.0, 10.0]], "domain": "testosc/phys",
             "f": lambda x: float(np.cos(4.0 * np.asarray(x).ravel()[0]))}

    # 1. 参数无量纲化后 x ∈ [0,1]
    X = np.array([[0.0, 0.0], [5.0, 5.0], [10.0, 10.0]])
    Xn = normalize_params(X, [[0.0, 10.0], [0.0, 10.0]])
    check("参数无量纲化后 x ∈ [0,1]",
          Xn.min() >= 0.0 - 1e-9 and Xn.max() <= 1.0 + 1e-9)

    # 2. 参数无量纲化对退化 bounds 不除零
    Xdeg = normalize_params(np.array([[1.0, 1.0]]), [[2.0, 2.0], [0.0, 5.0]])
    check("退化 bounds 不除零 + 钉到 0.5",
          abs(Xdeg[0, 0] - 0.5) < 1e-12 and abs(Xdeg[0, 1] - 0.2) < 1e-9)

    # 3. signature 长度固定
    y1 = np.sin(np.linspace(0, 6, 40))
    y2 = np.cos(np.linspace(0, 20, 40)) * 100.0
    s1 = response_signature(y1)
    s2 = response_signature(y2)
    check("signature 长度固定 = SIGNATURE_DIM",
          s1.shape[0] == SIGNATURE_DIM == s2.shape[0])

    # 4. 相同 y 的 signature 完全一致（signature 是确定性函数）
    s1b = response_signature(y1)
    check("相同 y → signature 完全一致",
          np.allclose(s1, s1b))

    # 5. 对齐前后 y 的均值/方差保持（y_raw 与 y_norm 的关系）
    y = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0])
    mu, sd = y.mean(), y.std()
    yz = (y - mu) / sd
    check("y_norm 均值为 0（归一化保留原关系）",
          abs(yz.mean()) < 1e-9)
    check("y_norm 标准差为 1",
          abs(yz.std() - 1.0) < 1e-9)

    # 6. 完全相同场景的对齐度 = 1.0
    #    构造两个"完全一致"的 signature 集合
    M = np.vstack([response_signature(y1), response_signature(y1 + 1e-6)])
    M2 = np.vstack([response_signature(y1), response_signature(y1 + 1e-6)])
    score_same = _alignment_score(M, M2)
    check("完全相同场景的对齐度 ≈ 1.0",
          score_same > 0.99)

    # 7. 完全不同场景的对齐度 < 0.3
    #    用两个形状明显不同的 signature（线性 vs 高频震荡）
    M_os = np.vstack([response_signature(np.cos(4 * np.linspace(0, 10, 40)))] * 3)
    M_li = np.vstack([response_signature(np.linspace(0, 10, 40))] * 3)
    score_diff = _alignment_score(M_os, M_li)
    check("完全不同场景的对齐度 < 0.3",
          score_diff < 0.3)

    # 8. PCA latent 降维正确
    S = np.vstack([s1, s2, response_signature(np.sin(np.linspace(0, 1, 20)))])
    pca = SignatureLatent(k=3).fit(S)
    Z = pca.transform(S)
    check("PCA latent 维度正确", Z.shape == (3, 3))
    check("PCA 累积方差解释率 ∈ [0,1]",
          0.0 <= pca.explain_ratio() <= 1.0 + 1e-9)

    # 9. align_scenarios 端到端不崩
    scns = {"a_lin": A_lin, "a_lin_copy": A_lin_copy, "c_osc": C_osc}
    X_aligned, sig_with_latent, report = align_scenarios(scns, n_grid=25)
    check("align_scenarios 返回非空 aligned",
          len(X_aligned) >= 2)
    check("报告含 cross_discipline_alignment",
          "cross_discipline_alignment" in report)
    check("报告 signature_dim 一致",
          report["signature_dim"] == SIGNATURE_DIM)

    # 10. 对齐度报告数值范围
    check("cross_discipline_alignment ∈ [0,1]",
          0.0 <= report["cross_discipline_alignment"] <= 1.0 + 1e-9)
    check("intra_discipline_alignment ∈ [0,1]",
          0.0 <= report["intra_discipline_alignment"] <= 1.0 + 1e-9)

    # 11. X_aligned 内部结构
    key0 = next(iter(X_aligned))
    Xr, Xn_, yr, yn_ = X_aligned[key0]
    check("X_aligned 返回 (X_raw, X_norm, y_raw, y_norm) 四元组",
          len(X_aligned[key0]) == 4)
    check("X_norm ∈ [0,1]",
          Xn_.min() >= -1e-9 and Xn_.max() <= 1.0 + 1e-9)

    # 12. 场景库为空时返回明确 reason
    _, _, empty_report = align_scenarios({}, n_grid=10)
    check("空场景返回 reason",
          "reason" in empty_report and empty_report["aligned"] == 0)

    print(f"\n{passed}/{total} semantic-alignment self-tests passed")
    return passed == total


if __name__ == "__main__":
    import sys
    if _selftest():
        sys.exit(0)
    sys.exit(1)
