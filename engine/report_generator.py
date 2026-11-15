"""V&V 报告生成器 —— 把「算了但不交付」的指标渲染成可归档文档。

为什么存在
----------
`engine/surrogate.py` 的 `SurrogateEngine.report()` 早就产出了 V&V 报告所需的
大部分内容，注释原话「生成一份可直发的领域报告」——但它返回一个 dict，
没有任何环节把它变成文档。线上唯一的 export 是 BibTeX。

业界（ASME V&V 10-2019 / FDA CM&S 报告指南 / Elicit / Google Literature Insights）
在交付形态上有明确共识：**付费用户拿到的是文档对象，不是一串数值**。
本模块补的就是这一层渲染。

设计原则（对齐「替代真实实验」方向，不做劣化版 Elicit）
------------------------------------------------------
1. **不让 LLM 写综述**。报告内容全部来自确定性计算（GP 代理 + UQ + 门禁），
   可重跑、可复现、无幻觉引用。这是相对 LLM 综述工具的诚实优势。
2. **局限性进执行摘要**，不放附录。FDA 指南明文要求 limitations 写在
   Executive Summary；这也是本项目的诚实招牌。
3. **带 Reproducibility Manifest**：seed / 参数 / 版本 / 重跑命令，
   收报告的人能重跑出同一个数。
4. **零网络依赖**：numpy + 本地场景库，无网、无设备、无真实实验台也能出报告。

报告结构（对齐 FDA CM&S outline + ASME V&V 10）
----------------------------------------------
 1 Executive Summary          结论 + 精度 + 覆盖率 + 局限性
 2 Context of Use             适用工况范围与 OOD 边界
 3 Method                     代理模型 / 核 / 长度尺度来源 / 噪声地板
 4 Code Verification          双种子复现检验（同一实验两次独立初始化）
 5 Uncertainty Quantification 精度、95% 覆盖率、校准误差、LOO
 6 Validation Metrics         PASS / MARGINAL / REFUTED 及判定依据
 7 Out-of-Disposition Gate    越界拒绝能力实测
 8 Recommendations            下一步最该做的实验点（主动学习 / BALD）
 9 Reproducibility Manifest   seed / 参数 / 版本 / 重跑命令
10 References                 场景的发表公式与文献

用法
----
    from engine.report_generator import generate
    md = generate("micro_ecoli_glucose", fmt="md", out="report.md")

CLI
---
    python engine/report_generator.py --list
    python engine/report_generator.py --scenario micro_ecoli_glucose --format md
    python engine/report_generator.py --scenario surrogate_branin --format html --out r.html
    python engine/report_generator.py --scenario pde_heat1d --format json
"""

from __future__ import annotations

import os

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

import datetime
import io
import json
import re
import subprocess
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine import design as _coords  # noqa: E402
from engine.surrogate import GPSurrogate, SurrogateEngine  # noqa: E402

Z95 = 1.96
NOMINAL_COVERAGE = 0.95

# 项目红线：噪声地板 3%，不随拟合下调（下调即过度自信）。
# SurrogateEngine.fit 的 noise_rel 默认同为 0.03，两者必须一致。
NOISE_FLOOR = 0.03

# 分级阈值与 skills/paper-claim-validation/scripts/grade_claim.py 保持同一数值体系，
# 避免同一项目出现两套"什么叫通过"的口径。
PASS_R2 = 0.90
PASS_COV_GAP = 0.05
MARGINAL_R2 = 0.70
MARGINAL_COV_GAP = 0.15

# 覆盖率用**单侧下界**判定（见 _verdict）：低于名义=过度自信（危险失败），
# 高于名义=保守（安全）。这两个数值此前硬编码在 _verdict 里 —— 提成常量是为了
# 让 CI 门禁（engine/microbiology_full.validate_full_scenarios）与报告路径共用
# **同一个**阈值真源，而不是各写一份字面量。
PASS_COV = 0.90
MARGINAL_COV = 0.80

GRADE_PASS = "PASS"
GRADE_MARGINAL = "MARGINAL"
GRADE_REFUTED = "REFUTED"

# 每维度默认采样规模（GP 为 O(n^3)，规模受控但足以支撑验证）
_DATA_SIZES = {1: (30, 40), 2: (50, 60), 3: (80, 60), 6: (120, 80)}
_DEFAULT_SIZES = (60, 60)


# ===========================================================================
# 场景注册表：8 跨域 + 52 微生物 = 60 个可出报告的场景
# ===========================================================================
def _normalize(scn: dict) -> dict:
    """把两套场景库（跨域 / 微生物）的字段统一成报告需要的形状。"""
    refs = list(scn.get("references") or [])
    if isinstance(refs, str):
        refs = [refs]
    ref = scn.get("ref")
    if ref and ref not in refs:
        refs.append(ref)
    out = dict(scn)
    out["references"] = refs
    out["published"] = scn.get("published", "") or ""
    out["domain"] = scn.get("domain", scn.get("category", ""))
    out["dim"] = int(scn.get("dim") or len(scn.get("bounds", [])))
    return out


def all_scenarios() -> dict:
    """返回 {key: scenario} 全量注册表。

    2026-10-08（P1-5）：本函数**不再是独立装配路径**。历史上它自己拼
    6 个来源（且用 setdefault 去重），与 ``paper_validation_loop`` 和
    ``scenario_library_density`` 各拼一份 —— 同一仓库三个场景总数。
    现在统一委派给 ``engine.scenario_registry``（单一真源），本函数只做
    schema 归一化的薄适配层。

    如需在无 RDKit 等可选依赖的环境下诊断来源构成，用
    ``scenario_registry.load_registry().source_counts``。
    """
    from engine.scenario_registry import scenario_list

    return {s["key"]: _normalize(s) for s in scenario_list()}


def get_scenario(key: str) -> dict:
    reg = all_scenarios()
    if key not in reg:
        raise KeyError(
            f"unknown scenario '{key}'. 可用 {len(reg)} 个，--list 查看；"
            f"相近：{sorted(k for k in reg if key[:6] in k)[:5]}"
        )
    return reg[key]


# ===========================================================================
# 确定性采样
# ===========================================================================
def _key_seed(key: str, extra: int = 0) -> int:
    """从场景 key 派生稳定整数种子（不用 hash()，避免 PYTHONHASHED salt 导致不可复现）。"""
    base = int.from_bytes(key.encode("utf-8")[:8].ljust(8, b"\x00"), "big") % (2 ** 31)
    return (base + int(extra)) % (2 ** 31)


def _lhs(bounds, n: int, seed: int) -> np.ndarray:
    """确定性拉丁超立方采样：每维分层、层内随机，覆盖优于纯随机。"""
    rng = np.random.default_rng(seed)
    b = np.asarray(bounds, float)
    d = b.shape[0]
    u = (np.arange(n)[None, :] + rng.random((d, n))) / n
    return (b[:, 0:1] + u * (b[:, 1:2] - b[:, 0:1])).T


# ===========================================================================
# 证据收集：把散落各处的 V&V 能力汇总成一份数据
# ===========================================================================
def _truth_oracle(f):
    """无噪声真值 oracle：评估对标的解析解 / 发表基准。"""

    class _O:
        @staticmethod
        def observe(x):
            return f(x)

    return _O()


def _noise_free_f(domain) -> callable:
    """返回剥离测量噪声后的 oracle——训练与验证都应对照**真值**，而非对照带噪 oracle。

    方法学要点（这是此前 R² 为负的根因）：场景的 `f` 多数自带末段加性高斯噪声
    （如 `specific_growth_rate(..., noise_std=0.05)`），用以模拟真实测量的不可复现性。
    但「虚拟实验 = 仿真」，本身没有测量噪声；若拿带噪 oracle 既当训练观测又当验证真值，
    GP 拟合的是噪声、验证的也是噪声，R² 必为负——这是错误的 V&V 设计，不是实现 bug。

    因此本函数临时把 `np.random.normal` 钉为恒 0，剥离末段加性噪声，得到无噪真值。
    场景声明在 `noise` 字段里的噪声幅度，只在报告中作为「若换成真实实验的测量噪声地板」
    上下文出现，不参与训练/验证。
    """
    raw = domain["f"]

    def clean(x):
        orig = np.random.normal
        np.random.normal = lambda *a, **k: 0.0  # 剥离末段加性噪声
        try:
            return float(raw(x))
        finally:
            np.random.normal = orig

    return clean


def _observe(domain, X, seed: int, obs_noise: float | None = None) -> np.ndarray:
    """按项目统一语义取训练观测值。

    默认 **无噪声真值**：虚拟实验直接查询地面真值函数，不受设备噪声影响——
    这正是「替代真实实验」相对湿实验的固有优势，与飞轮
    `scripts/paper_validation_loop.py` 的训练数据构造保持一致。

    噪声只以 **GP 先验中的相对地板（3%）** 形式存在，不随拟合下调；
    若显式传入 `obs_noise`，则以响应标准差为尺度注入相对观测噪声，
    用于模拟"观测本身也带误差"的保守情形。

    注意不要用 `SyntheticOracle`：它注入的是**绝对**噪声，与归一化空间的
    相对地板语义错配（对幅值小的场景会放大成数十倍噪声，导致 R² 为负的假失败）。
    """
    y = np.array([float(domain["f"](x)) for x in X], float)
    if not obs_noise:
        return y
    scale = float(np.std(y)) + 1e-12
    rng = np.random.default_rng(_key_seed(domain["key"], seed + 13))
    return y + float(obs_noise) * scale * rng.standard_normal(len(y))


def _r2(y_true, y_pred) -> float:
    ss_res = float(np.sum((np.asarray(y_true) - np.asarray(y_pred)) ** 2))
    ss_tot = float(np.sum((np.asarray(y_true) - np.mean(y_true)) ** 2))
    return 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")


def _loo_metrics(domain, X, y, auto_ls: bool | None = None,
                ls_override: float | None = None) -> dict | None:
    """留一交叉验证：n<=80 时做，给出 out-of-sample R² 与覆盖率的第二个视角。

    也是**配置选择的内部准则**——只用训练集，不碰测试集，因此用 LOO 挑长度尺度
    不会引入对测试集的乐观偏差（ASME V&V 10 要求验证指标不得由被验证数据挑出）。
    """
    n = len(X)
    if n < 4 or n > 80:
        return None
    if auto_ls is None:
        auto_ls = bool(domain.get("auto_ls", False))
    # ls 可以是标量（各向同性）或长度 d 的向量（网络/多轴 ARD）；GPSurrogate 两者都收，
    # 不能无条件 float()（多轴 ARD 会抛 "only 0-dimensional arrays..."，2026-09-15 实测）。
    _ls = ls_override if ls_override is not None else domain.get("ls", 0.5)
    _lsv = np.atleast_1d(np.asarray(_ls, float))
    ls = float(_lsv[0]) if _lsv.size == 1 else _lsv
    preds = np.empty(n)
    los, his = np.empty(n), np.empty(n)
    for i in range(n):
        mask = np.ones(n, bool)
        mask[i] = False
        sur = GPSurrogate(
            lengthscale=ls,
            noise_var=(domain.get("noise", 0.03)) ** 2,
            auto_ls=auto_ls,
        )
        sur.fit(X[mask], y[mask])
        mu, sd = sur.predict(X[i : i + 1])
        preds[i] = mu[0]
        los[i], his[i] = mu[0] - Z95 * sd[0], mu[0] + Z95 * sd[0]
    cov = float(np.mean((y >= los) & (y <= his)))
    return {
        "r2": round(_r2(y, preds), 4),
        "rmse": round(float(np.sqrt(np.mean((y - preds) ** 2))), 6),
        "coverage": round(cov, 3),
        "n": n,
    }


def _score_on_valset(domain, X, y, Xval, yval) -> list:
    """在独立验证集上评估各候选配置：返回该配置下的 R² 与 95% 覆盖率。

    验证集与训练集（seed 0）、官方测试集（seed 991）均独立 → 能暴露「过度平滑 /
    过度自信」这类在自家切分上看不出来的缺陷，同时不碰官方测试集（无乐观偏差，
    符合 ASME V&V 10「验证指标不得由被验证数据挑出」）。
    """
    cands_res = []
    _bls = np.atleast_1d(np.asarray(domain.get("ls", 0.5), float))
    base_ls = float(_bls[0]) if _bls.size == 1 else _bls  # 标量或 ARD 向量
    for mult, auto_ls in ((0.5, False), (1.0, False), (2.0, False), (1.0, True)):
        ls = base_ls * mult if not auto_ls else base_ls
        try:
            sur = GPSurrogate(
                lengthscale=ls,
                noise_var=(domain.get("noise", 0.03)) ** 2,
                auto_ls=auto_ls,
            )
            sur.fit(X, y)
            mu, sd = sur.predict(Xval)
            r2 = _r2(yval, mu)
            cov = float(np.mean((yval >= mu - Z95 * sd) & (yval <= mu + Z95 * sd)))
            key = "ARD (auto)" if auto_ls else f"iso ls={ls:.3g}"
            cands_res.append({"key": key, "auto_ls": auto_ls, "ls": ls,
                             "r2": r2, "coverage": cov})
        except Exception:
            continue
    return cands_res


def _raw_point(v, axes) -> list:
    """把建模坐标下的点映射回物理坐标并逐坐标四舍五入（供报告 / API 展示）。"""
    v = np.atleast_1d(np.asarray(v, float))
    if axes:
        v = _coords.to_raw(v.reshape(1, -1), axes)[0]
    return [round(float(c), 4) for c in np.atleast_1d(v)]


def _norm_ls(v):
    """ls 归一化：标量 → float（JSON 友好）；多轴 ARD → 保持长度 d 的向量。

    ⚠️ 不能无条件 float()：多轴 ARD 的长度尺度是向量，强转会抛
    "only 0-dimensional arrays can be converted to Python scalars"（2026-09-15 实测）。
    """
    a = np.atleast_1d(np.asarray(v, float))
    return float(a[0]) if a.size == 1 else a


def _choose_hyper(domain, X, y) -> dict:
    """超参选择（统一机制规则，见下）。

    规则：**数据量达到 ARD 门槛 n ≥ 4d+6 时，长度尺度由边际似然从数据学习**
    （`GPSurrogate._optimize_ls` 内含 LOO 守卫：学到的超参在 LOO 上不劣于先验才
    被采用，否则回落先验），不再用「先验 ls 的 0.5×/1×/2× 三档网格 + 验证集综合
    分数」挑选。

    为什么改（2026-09-15 实测根因）：验证集综合分数 `R² − 2·|cov − 0.95|` 对
    **边界层型响应**（值域变化集中在域宽 1–2% 的膝部、其余为饱和平台）几乎失明——
    验证点的 R² 由平台主导，膝部拟合好坏对分数影响极小，于是网格会稳定地选出
    「最平滑」的配置。实测 micro_competition_ecoli_yeast：网格选 iso ls=5 →
    R²=0.440；ARD 选学习长度尺度 → R²=0.996（同训练点、同官方测试集、同阈值）。
    边际似然是全局准则且自带 LOO 守卫，在此类响应上判别力更强。

    返回结构与 `_select_config` 一致，便于下游与报告无差别消费。
    """
    X = np.atleast_2d(np.asarray(X, float))
    n, d = X.shape
    if n >= 4 * d + 6:
        _ls = np.atleast_1d(np.asarray(domain.get("ls", 0.5), float))
        return {
            "chosen": {"auto_ls": True, "ls": (float(_ls[0]) if _ls.size == 1 else _ls.tolist())},
            "chosen_key": "ARD (auto)",
            "candidates": {},
            "note": ("数据量达到 ARD 门槛 n>=4d+6：长度尺度由边际似然学习，"
                     "并受 LOO 守卫约束（不劣于先验才采用）；"
                     "不使用人工先验网格，避免边界层响应下网格选型失明。"),
        }
    return _select_config(domain, X, y)


def _select_config(domain, X, y) -> dict:
    """按训练集**内部准则**自动挑长度尺度配置；官方测试集全程不参与。

    候选：各向同性 RBF @ ls 的 0.5×/1×/2× 三档 + 各向异性 ARD。

    选择标准不是单纯的留一 RMSE——那样在「近平/平滑」训练数据上会病态地偏好
    **最平滑**的模型，导致预测区间塌缩、覆盖率远低于 0.95（过度自信，正是我们产品
    要防止的反模式）。这里用一个**与训练集、测试集都独立的验证集**（固定 seed 4242），
    以综合分数 `score = R² − 2·|coverage − 0.95|` 选：拟合好且校准诚实（覆盖率≈名义）
    者胜出。该验证集是合成生成、仅用于模型选择，不碰官方测试集。

    返回 {'chosen': {...}, 'chosen_key': str, 'candidates': {key: {...}}, 'note': str}
    """
    n = len(X)
    if n < 6:  # 样本过少，退回 ARD
        return {"chosen": {"auto_ls": True, "ls": _norm_ls(domain.get("ls", 0.5))},
                "chosen_key": "ARD (auto)", "candidates": {},
                "note": "样本过少，已默认采用 ARD"}

    # 独立验证集（区别于训练 seed 0 与测试 seed 991）
    n_val = min(60, max(20, n))
    Xval = _lhs(domain["bounds"], n_val, _key_seed(domain["key"], 4242))
    yval = np.array([float(domain["f"](x)) for x in Xval], float)

    res = _score_on_valset(domain, X, y, Xval, yval)
    if not res:
        return {"chosen": {"auto_ls": True, "ls": _norm_ls(domain.get("ls", 0.5))},
                "chosen_key": "ARD (auto)", "candidates": {},
                "note": "验证集评估失败，已默认采用 ARD"}
    for c in res:
        c["score"] = round(float(c["r2"] - 2.0 * abs(c["coverage"] - NOMINAL_COVERAGE)), 4)
    best = max(res, key=lambda c: c["score"])
    cands_out = {c["key"]: {"r2": round(float(c["r2"]), 4),
                            "coverage": round(float(c["coverage"]), 3),
                            "score": c["score"]} for c in res}
    return {
        "chosen": {"auto_ls": best["auto_ls"], "ls": _norm_ls(best["ls"])},
        "chosen_key": best["key"],
        "candidates": cands_out,
        "note": ("按独立验证集的综合分数 R²−2·|cov−0.95| 选取"
                 "（兼顾拟合与校准诚实性；验证集与官方测试集独立）"),
    }


def _calibrate_scale(domain, X, y, cfg=None) -> float:
    """方差标定（conformal 风格的校准）：用与最终部署模型**完全相同结构**的 z-scored
    代理，在独立验证集上估计预测 std 应放大的倍数 κ，使 95% 区间真正覆盖约 95% 的
    测试点。只放宽不收紧（κ≥1），避免过度自信。

    诚实 UQ 的核心：GP 的预测 std 常系统性偏窄（过度自信），本项目方向是
    **宁可保守不可过度自信**，故 κ 仅向上校准。返回 1.0 表示无需放宽。

    ⚠️ 关键正确性约束（此前 15 个场景被判 REFUTED 的元凶）：
    标定所用的代理必须与 `collect_evidence` 最终构造的 `SurrogateEngine` 同构（均含
    y 的 z-score 归一化、同样的噪声地板、同样的 ARD 选择）。若此处误用未归一化的
    裸 `GPSurrogate`，残差/区间比会在量纲上错位（差一个 `y_scale` 因子），算出的 κ
    被应用到量纲不同的最终模型上 → 覆盖率惨崩、κ 退化成 1.0。必须以最终同构模型校准。
    """
    n_val = min(60, max(20, len(X)))
    # 独立校准集：seed 7311，与配置选择的验证集（4242）、官方测试集（991）都不同，
    # 避免校准 / 选择 / 判定共用同批点而引入乐观偏差（ASME V&V 10 同口径）。
    Xcal = _lhs(domain["bounds"], n_val, _key_seed(domain["key"], 7311))
    ycal = np.array([float(domain["f"](x)) for x in Xcal], float)
    eng = SurrogateEngine(domain)  # 与最终模型同构（含 z-score + 噪声地板 + ARD）
    eng.fit(X, y)
    mu, sd = eng.predict(Xcal)
    resid = np.abs(ycal - mu)
    target = Z95 * sd
    nonzero = target > 1e-9
    if int(nonzero.sum()) < 5:
        return 1.0
    ratio = resid[nonzero] / target[nonzero]
    kappa = float(np.percentile(ratio, 95))
    # 仅向上校准；上限 40 仅作数值安全网（防止病态场景把区间放大到无意义）。
    return float(min(max(kappa, 1.0), 40.0))


def _core_regime_mask(y) -> np.ndarray:
    """标记真值处于中央 80% 区间的测试点（剔除两端各 10% 的局地极端值）。

    用于「核心工况 R²」：近常数函数（如平区 μ≈0.8 的 Monod）的全局 R² 常被一个
    局地陡变/膝部单点拖垮，该视角给出操作工况下的真实拟合质量。仅作报告上下文，
    不改变正式判定所依据的全球 R²。
    """
    y = np.asarray(y, float)
    if len(y) < 10:
        return np.ones(len(y), bool)
    lo, hi = np.percentile(y, [10, 90])
    return (y >= lo) & (y <= hi)


def _smoothness_diagnostic(domain, n=40) -> dict:
    """响应面平滑度诊断：用真值 f 在拉丁超立方网格上的有限差分估计局部剧烈程度。

    返回 {'mean_grad':..., 'max_grad':..., 'high_freq':bool}。
    high_freq=True 时说明响应含高频/振荡成分，GP 平滑先验可能系统性地无法拟合，
    这种情况下 REFUTED 是**方法边界**而非实现 bug——必须如实写进局限性。
    """
    bounds = np.asarray(domain["bounds"], float)
    rng = np.random.default_rng(_key_seed(domain["key"], 777))
    X = _lhs(domain["bounds"], n, _key_seed(domain["key"], 778))
    y = np.array([float(domain["f"](x)) for x in X])
    # 用 y 标准差归一化梯度幅度，得到与尺度无关的相对剧烈度
    yscale = float(np.std(y)) + 1e-12
    grads = []
    h = 1e-3
    for i, x in enumerate(X):
        if i == 0:
            continue
        dx = X[i] - X[i - 1]
        norm = np.linalg.norm(dx)
        if norm < 1e-9:
            continue
        grads.append(float(np.abs(y[i] - y[i - 1]) / (norm * yscale)))
    grads = np.array(grads) if grads else np.array([0.0])
    mean_g, max_g = float(np.mean(grads)), float(np.max(grads))
    # 相对梯度 > ~5 视为高烈度（接近 1 个边界宽度内跨越数个标准差）
    return {"mean_grad": round(mean_g, 4), "max_grad": round(max_g, 4),
            "high_freq": bool(max_g > 5.0)}


def _replication_check(domain, n_train, n_test, seed_a=0, seed_b=101,
                       obs_noise: float | None = None) -> dict:
    """双种子复现检验：同一实验在两种独立初始化下的指标差异。

    对应 FDA 报告的 Code Verification —— 方法本身是否稳定，独立于本次运气。
    """
    out = {}
    for tag, sd in (("a", seed_a), ("b", seed_b)):
        eng = SurrogateEngine(domain)
        X = _lhs(domain["bounds"], n_train, _key_seed(domain["key"], sd))
        y = _observe(domain, X, sd, obs_noise)
        eng.fit(X, y)
        test = _lhs(domain["bounds"], n_test, _key_seed(domain["key"], sd + 999))
        m = eng.evaluate(test)
        mean, _ = eng.predict(test)
        y_true = np.array([domain["f"](x) for x in test])
        out[tag] = {
            "rmse": m["rmse"],
            "coverage": m["coverage"],
            "r2": round(_r2(y_true, mean), 4),
        }
    ra, rb = out["a"]["r2"], out["b"]["r2"]
    rel_rmse = abs(out["a"]["rmse"] - out["b"]["rmse"]) / max(
        abs(out["a"]["rmse"]), abs(out["b"]["rmse"]), 1e-9)
    cov_diff = abs(out["a"]["coverage"] - out["b"]["coverage"])
    return {
        "a": out["a"], "b": out["b"],
        "rel_rmse_diff": round(float(rel_rmse), 4),
        "cov_diff": round(cov_diff, 4),
        "passed": bool(rel_rmse <= 0.10 and cov_diff <= 0.10),
        "threshold": 0.10,
    }


def _ood_probe(domain, eng, n_in=120, n_out=60) -> dict:
    """越界门禁实测：域内点应放行，域外点（bounds 外扩 60%）应被拒。"""
    bounds = np.asarray(domain["bounds"], float)
    rng = np.random.default_rng(_key_seed(domain["key"], 31))
    Xin = _lhs(domain["bounds"], n_in, _key_seed(domain["key"], 32))
    span = bounds[:, 1] - bounds[:, 0]
    lo = bounds[:, 0] + 1.6 * span
    hi = bounds[:, 0] + 2.2 * span
    Xout = lo + rng.random((n_out, len(bounds))) * (hi - lo)

    def tally(Xs):
        r = eng.trust_boundary(Xs)
        st = r["status"]
        return {
            "trusted": int(sum(s == "trusted" for s in st)),
            "caution": int(sum(s == "caution" for s in st)),
            "out_of_distribution": int(sum(s == "out_of_distribution" for s in st)),
            "ood_threshold": round(float(r["ood_threshold"]), 4),
            "n": len(st),
        }

    inside, outside = tally(Xin), tally(Xout)
    rejection_rate = outside["out_of_distribution"] / max(outside["n"], 1)
    return {
        "inside": inside,
        "outside": outside,
        "rejection_rate": round(float(rejection_rate), 3),
        "effective": bool(rejection_rate >= 0.90),
    }


def _fit_stage(domain, waxes, n_train, seed, obs_noise, dim):
    """在指定坐标（[]=原始 / waxes=log10 坐标）下完成一整轮拟合并返回全部中间物。

    设计点 → 超参选择（ARD）→ κ 方差标定 → 拟合。两阶段升级共用这一段，避免
    「设计在 log 空间、预测在原始空间」这类静默数据破坏。
    """
    work = dict(domain)
    if waxes:
        work["bounds"] = _coords.warped_bounds(domain["bounds"], waxes)
        work["f"] = _coords.wrap_oracle(domain["f"], waxes, dim)
        # 先验长度尺度按「占域宽度比例」**逐轴**搬到 log 坐标（ARD 启用时仅作 LOO 兜底）
        work["ls"] = _coords.scaled_ls_prior(domain.get("ls", 0.5),
                                             domain["bounds"], waxes, dim)

    # 训练点设计：在**建模坐标**下做 LHS（等价于按数量级均匀覆盖浓度轴）
    X = _lhs(work["bounds"], n_train, _key_seed(domain["key"], seed))
    y_obs = _observe(work, X, seed, obs_noise)

    # 超参选择：数据量达 ARD 门槛即由边际似然学长度尺度（LOO 守卫），不再用先验网格。
    sel = _choose_hyper(work, X, y_obs)
    work = dict(work)
    work["auto_ls"] = bool(sel["chosen"]["auto_ls"])
    _lsv = np.atleast_1d(np.asarray(sel["chosen"]["ls"], float))
    work["ls"] = float(_lsv[0]) if _lsv.size == 1 else _lsv

    # 方差标定：用独立验证集估计预测 std 的放大系数 κ（只放宽不收紧）。
    kappa = _calibrate_scale(work, X, y_obs, sel["chosen"])

    eng = SurrogateEngine(work)
    eng.fit(X, y_obs)
    eng._sur.std_scale = kappa  # 下游 predict/evaluate/ood 自动用标定后的 std
    return {"work": work, "X": X, "y_obs": y_obs, "sel": sel, "kappa": kappa, "eng": eng,
            "ls": np.atleast_1d(np.asarray(eng._sur.ls, float)).tolist(),
            "ls_source": eng._sur.ls_source}


def collect_evidence(scenario: dict, n_train: int | None = None, n_test: int | None = None,
                     seed: int = 0, with_loo: bool = True,
                     with_replication: bool = True,
                     obs_noise: float | None = None,
                     with_baseline: bool = True) -> dict:
    """跑完一个场景的整套 V&V，返回结构化证据（不渲染）。

    `obs_noise`：观测噪声（相对响应标准差）。默认 None = 不注入，
    即虚拟实验直接查询地面真值函数（与飞轮一致）。

    这一步刻意与渲染分离：证据可用于报告、也可用于 CI 门禁或 API。
    """
    domain = dict(scenario)
    # 虚拟实验 = 仿真，无测量噪声：训练与验证都对照无噪真值（见 _noise_free_f）。
    domain["f"] = _noise_free_f(domain)
    dim = int(domain.get("dim") or len(domain["bounds"]))
    if n_train is None:
        n_train = int(domain.get("n_train") or _DATA_SIZES.get(dim, _DEFAULT_SIZES)[0])
    if n_test is None:
        n_test = int(domain.get("n_test") or _DATA_SIZES.get(dim, _DEFAULT_SIZES)[1])

    # ---- 建模坐标：两阶段升级（统一机制规则，见 engine/design.py）-----------------
    # 阶段 1：先在**原始坐标**拟合。阶段 2：仅当 ARD 学到的长度尺度撞搜索网格下界
    # （= 原始坐标分辨不出响应细节，纯训练侧可审计信号）且存在「跨 >2 个数量级的正轴」
    # 时，才升级到 log10 坐标重跑；若升级后该轴不再撞下界则采纳，否则保留原始坐标
    # （诚实保留失败，不硬凑）。判据与官方测试集完全无关，规则对全部场景一致施加。
    waxes_all = _coords.warp_axes(domain["bounds"])
    waxes = []
    key_seed = _key_seed(domain["key"], seed)
    st = _fit_stage(domain, [], n_train, seed, obs_noise, dim)
    escalation = {
        "candidate_axes": list(waxes_all),
        "triggered": False,
        "trigger": "ARD lengthscale at search-grid floor (%.2f) on a decades-spanning axis"
                   % _coords.LS_GRID_MIN,
        "raw_ls": st["ls"], "raw_ls_source": st["ls_source"],
    }
    if waxes_all and _coords.hit_lower_bound(st["ls"], st["ls_source"], waxes_all):
        st2 = _fit_stage(domain, waxes_all, n_train, seed, obs_noise, dim)
        if not _coords.hit_lower_bound(st2["ls"], st2["ls_source"], waxes_all):
            waxes, st = list(waxes_all), st2
            escalation.update({
                "triggered": True,
                "resolved_axes": list(waxes_all),
                "log_ls": st["ls"], "log_ls_source": st["ls_source"],
                "reason": "log10 coordinate resolved the grid-floor lengthscale",
            })
        else:
            escalation.update({
                "log_ls": st2["ls"], "log_ls_source": st2["ls_source"],
                "reason": "log10 did not resolve the grid-floor lengthscale "
                          "→ kept linear coordinates (honest, not forced)",
            })

    work, X, y_obs, sel, kappa, eng = (st["work"], st["X"], st["y_obs"],
                                       st["sel"], st["kappa"], st["eng"])

    # 官方测试集：**仍在原始 bounds 上抽点**（判据不失真），再映射到建模坐标去查询。
    Xt_raw = _lhs(domain["bounds"], n_test, _key_seed(domain["key"], seed + 991))
    Xt = _coords.to_model(Xt_raw, waxes) if waxes else Xt_raw
    metrics = eng.evaluate(Xt)
    mean, std = eng.predict(Xt)
    y_true = np.array([domain["f"](x) for x in Xt_raw])

    r2 = _r2(y_true, mean)
    rmse = metrics["rmse"]
    rmse_rel = float(rmse / (float(np.std(y_true)) + 1e-9))
    cov = metrics["coverage"]
    cov_gap = cov - NOMINAL_COVERAGE

    # 核心工况 R²：剔除真值最极端的 20% 测试点（近常数函数里往往是局地陡变/膝部单点），
    # 给出"操作工况下"的 R²。全局 R² 仍作为正式判定输入（不伪造），但报告同时给出
    # 这一更不易被单点局地特征拖垮的视角，避免误导。
    core_mask = _core_regime_mask(y_true)
    r2_core = _r2(y_true[core_mask], mean[core_mask]) if int(core_mask.sum()) > 3 else float("nan")

    verdict, why = _verdict(r2, cov)

    ev = {
        "scenario": {
            "key": domain["key"],
            "domain": domain.get("domain", ""),
            "category": domain.get("category", ""),
            "dim": dim,
            "bounds": [list(map(float, b)) for b in domain["bounds"]],
            "published": domain.get("published", ""),
            "references": domain.get("references", []),
        },
        "design": {
            "n_train": int(n_train),
            "n_test": int(n_test),
            "seed": int(seed),
            "sampling": ("Latin Hypercube in modelling coordinates (log10 on "
                         "decades-spanning axes)" if waxes else
                         "Latin Hypercube (deterministic, seeded by scenario key)"),
            "coordinate_transform": _coords.describe(domain["bounds"], waxes, bool(waxes)),
            "warped_axes": list(waxes),
            "coordinate_escalation": escalation,
            "kernel": "RBF (ARD)" if work.get("auto_ls") else "RBF (isotropic)",
            "lengthscale_source": "auto (marginal-likelihood ARD)" if work.get("auto_ls")
                                  else f"prior = {work.get('ls')}",
            "noise_floor": NOISE_FLOOR,
            "noise_model": "Gaussian, relative to response scale (honest-UQ floor, never relaxed)",
            "scenario_typical_noise": float(domain.get("noise", 0.03)),
            "obs_noise_injected": float(obs_noise or 0.0),
        },
        "metrics": {
            "r2": round(float(r2), 4),
            "r2_core": round(float(r2_core), 4),
            "rmse": round(float(rmse), 6),
            "rmse_rel": round(rmse_rel, 4),
            "coverage": cov,
            "coverage_gap": round(float(cov_gap), 3),
            "cal_err": metrics["cal_err"],
            "mean_std": metrics["mean_std"],
            "nominal_coverage": NOMINAL_COVERAGE,
        },
        "verdict": {"grade": verdict, "rationale": why},
        "recommendations": {
            "next_maxvar": _raw_point(eng.recommend_next(), waxes),
        },
        "config_selection": sel,
        "calibration_scale": round(float(kappa), 4),
        "smoothness": _smoothness_diagnostic(work),
        "provenance": _provenance(),
    }

    try:
        bald = eng.recommend_bald(n_candidates=200)
        ev["recommendations"]["next_bald"] = _raw_point(bald, waxes)
    except Exception:
        ev["recommendations"]["next_bald"] = None

    if with_loo:
        loo = _loo_metrics(work, X, y_obs)
        if loo:
            ev["loo"] = loo

    try:
        ev["ood_gate"] = _ood_probe(work, eng)
    except Exception:
        ev["ood_gate"] = None

    if with_replication:
        try:
            ev["replication"] = _replication_check(work, n_train, n_test, obs_noise=obs_noise)
        except Exception:
            ev["replication"] = None

    # ---- 基线对比：RF+Conformal 在同一数据上的得分（不参与生产预测） ----
    if with_baseline:
        try:
            from engine.random_forest_baseline import evaluate_on_data
            rf_metrics = evaluate_on_data(
                domain, X, y_obs, Xt_raw, y_true,
                alpha=1 - NOMINAL_COVERAGE, seed=_key_seed(domain["key"], seed)
            )
            # 构造对比 dict（GP 指标已在 ev["metrics"] 中）
            gp_r2 = ev["metrics"]["r2"]
            gp_cov = ev["metrics"]["coverage"]
            ev["baseline_comparison"] = {
                "rf": rf_metrics,
                "gp": {
                    "r2": gp_r2,
                    "coverage": gp_cov,
                    "grade": verdict,
                    "method": "GP (RBF kernel) + conformal calibration",
                },
                "delta": {
                    "r2_gap": round(rf_metrics["r2"] - gp_r2, 4),
                    "coverage_gap": round(rf_metrics["coverage"] - gp_cov, 4),
                },
                "headline": (
                    f"GP R²={gp_r2:.4f} / cov={gp_cov:.4f} ({verdict}) vs "
                    f"RF R²={rf_metrics['r2']:.4f} / cov={rf_metrics['coverage']:.4f} "
                    f"({rf_metrics['grade']})"
                ),
                "note": "RF 仅作外部对标基线，不参与生产预测；两者用同一批 train/test 数据",
            }
        except Exception as e:
            ev["baseline_comparison"] = {"error": str(e)}

    return ev


def _verdict(r2: float, cov: float) -> tuple[str, str]:
    """与 grade_claim.py 同口径的分级；校准采用**单侧下界**判定。

    校准哲学（对齐「诚实 UQ / 宁可保守不可过度自信」）：覆盖率低于名义值=过度自信
    （危险失败），高于名义值=保守（安全，仅浪费一点区间宽度）。因此 PASS/MARGINAL
    用覆盖率**下界**（cov ≥ 0.90 / 0.80）而非对称偏差；只有 r2 过低或覆盖严重不足
    才判 REFUTED。
    """
    if r2 >= PASS_R2 and cov >= PASS_COV:
        return GRADE_PASS, (f"out-of-sample R²={r2:.3f} ≥ {PASS_R2}, "
                            f"coverage={cov:.3f} ≥ {PASS_COV}（校准安全，保守亦可）")
    if r2 >= MARGINAL_R2 and cov >= MARGINAL_COV:
        return GRADE_MARGINAL, (f"R²={r2:.3f} ≥ {MARGINAL_R2} 且 coverage={cov:.3f} ≥ {MARGINAL_COV}，"
                                f"但有一项未达 PASS 门槛")
    why = []
    if r2 < MARGINAL_R2:
        why.append(f"R²={r2:.3f} < {MARGINAL_R2}（拟合不足）")
    if cov < MARGINAL_COV:
        why.append(f"coverage={cov:.3f} < {MARGINAL_COV}（过度自信，区间过窄）")
    return GRADE_REFUTED, "; ".join(why)


def _provenance() -> dict:
    """重跑所需的一切：版本、依赖、commit、时间戳。"""
    commit = "unknown"
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
            capture_output=True, text=True, timeout=5).stdout.strip() or "unknown"
    except Exception:
        pass  # noqa: silent-ok — Exception 可安全忽略（已审计 2026-10-08）
    return {
        "generated_at_utc": datetime.datetime.now(datetime.timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "code_commit": commit,
        "python": sys.version.split()[0],
        "numpy": np.__version__,
        "engine": "engine/surrogate.py :: SurrogateEngine (GP + UQ)",
        "network_required": False,
        "lab_required": False,
    }


def _limitations(ev: dict) -> list:
    """局限性：必须进执行摘要（FDA 明文要求），且必须是这一份报告的真实边界。"""
    m, d = ev["metrics"], ev["design"]
    n_test = d["n_test"]
    se = float(np.sqrt(0.95 * 0.05 / max(n_test, 1)))
    lim = [
        f"GP 噪声地板固定 {d['noise_floor']:.0%}（不随拟合下调）：低于此量级的差异在本方法中"
        f"不可分辨，不代表真实体系没有差异。"
        + (f"本次额外注入观测噪声 {d['obs_noise_injected']:.0%}。"
           if d["obs_noise_injected"] else "本次未注入观测噪声（虚拟实验直接查询真值函数）。"),
        f"覆盖率 {m['coverage']:.3f} 基于 {n_test} 个测试点，其自身标准误约 ±{se:.3f}；"
        f"覆盖率读数在 ±{2 * se:.3f} 内的波动不应被解读为模型变化。",
        f"结论仅在 bounds 内成立（维度 {ev['scenario']['dim']}）；越界查询由门禁显式拒绝，"
        f"不代表外推可用。",
        "本报告对标的是场景自带的解析解 / 发表基准函数，不是湿实验实测值；"
        "用于真实决策前需按 mirror-run 做一次对等度验收。",
        f"核函数为 {d['kernel']}，长度尺度来源：{d['lengthscale_source']}；"
        "核选择错误时 UQ 会系统性地过自信或过保守，本报告不自动排除该风险。",
        "报告全文由确定性计算生成，无 LLM 参与，因此不含自然语言综述——"
        "代价是可读性低于人工综述，收益是无幻觉引用、可逐行复现。",
    ]
    kappa = ev.get("calibration_scale", 1.0)
    if kappa and kappa > 1.05:
        lim.append(
            f"预测区间经方差标定放大 κ={kappa:.2f} 倍：独立验证集上原始 GP 区间过窄"
            f"（过度自信），本项目方向是宁可保守不可过度自信，故仅向上校准。"
            f"放大后的区间更诚实，但代价是略宽。")
    if m["rmse_rel"] > 0.10:
        lim.append(
            f"相对 RMSE {m['rmse_rel']:.3f} > 0.10：本代理可用于趋势判断，"
            f"不宜用于该工况下的定量交付。")
    if ev.get("replication") and not ev["replication"]["passed"]:
        lim.append("双种子复现检验未通过：该场景对初始化敏感，单独一次结论不可信。")
    sm = ev.get("smoothness")
    if sm and sm.get("high_freq"):
        lim.append(
            f"响应面平滑度诊断：最大局部相对梯度 {sm['max_grad']:.2f}（>5 视为高频/振荡），"
            f"GP 平滑先验可能系统性地无法贴合此类响应；若仍判 REFUTED，"
            f"属方法边界而非实现缺陷——本场景需更复杂的核或在物理尺度上重新参数化。")
    return lim


# ===========================================================================
# 报告数据模型（与渲染器解耦）
# ===========================================================================
def build_report(ev: dict) -> dict:
    s, d, m, v = ev["scenario"], ev["design"], ev["metrics"], ev["verdict"]
    lim = _limitations(ev)

    secs = []

    # 1 Executive Summary -----------------------------------------------------
    blocks = [
        {"type": "p", "text":
            f"场景 **{s['key']}**（{s['domain']}）在 {d['n_train']} 个虚拟实验点拟合后，"
            f"对 {d['n_test']} 个独立测试点的对等度判定为 **{v['grade']}**。"},
        {"type": "table",
         "headers": ["指标", "数值", "判定"],
         "rows": [
             ["Out-of-sample R²", f"{m['r2']:.4f}", "≥0.90 通过" if m["r2"] >= 0.90 else "低于 0.90"],
             ["核心工况 R²（剔除局地极端 20%）", f"{m['r2_core']:.4f}", "操作工况下的拟合质量"],
             ["RMSE", f"{m['rmse']:.6g}", f"相对 {m['rmse_rel']:.3f}"],
             ["95% 区间覆盖率", f"{m['coverage']:.3f}",
              f"偏离名义 {m['coverage_gap']:+.3f}"],
             ["校准误差 abs(cov−0.95)", f"{m['cal_err']:.3f}",
              "≤0.05 通过" if m["cal_err"] <= 0.05 else "超差"],
         ]},
        {"type": "p", "text": f"判定依据：{v['rationale']}。"},
        {"type": "h3", "text": "局限性（Limitations）"},
        {"type": "list", "items": lim},
    ]
    secs.append({"n": 1, "title": "Executive Summary", "blocks": blocks})

    # 2 Context of Use --------------------------------------------------------
    bounds_rows = [[f"x{i}", f"{b[0]:.6g}", f"{b[1]:.6g}"]
                   for i, b in enumerate(s["bounds"])]
    secs.append({"n": 2, "title": "Context of Use", "blocks": [
        {"type": "p", "text":
            "本报告的结论仅在下列参数区间内成立。区间由场景自带的物理/实验定义给出，"
            "不接受外推；越界查询在第 7 章的门禁中被显式拒绝，而不是返回一个无依据的标量。"},
        {"type": "table", "headers": ["维度", "下界", "上界"], "rows": bounds_rows},
        {"type": "p", "text":
            f"适用用途：{s['domain']}。发布基准：{s['published'] or '（场景未提供）'}"},
    ]})

    # 3 Method ----------------------------------------------------------------
    secs.append({"n": 3, "title": "Method", "blocks": [
        {"type": "p", "text":
            "代理模型为高斯过程（GP）回归，纯 numpy 实现，无外部求解器依赖。"
            "不确定性一等公民：噪声地板不随拟合下调，避免把观测噪声误当成可解释信号。"},
        {"type": "table", "headers": ["项", "取值"], "rows": [
            ["代理模型", "Gaussian Process (RBF)"],
            ["核函数", d["kernel"]],
            ["长度尺度来源", d["lengthscale_source"]],
            ["噪声模型", d["noise_model"]],
            ["GP 噪声地板", f"{d['noise_floor']:.0%}（归一化空间，红线：不随拟合下调）"],
            ["方差标定系数 κ", f"{ev['calibration_scale']:.3f}（≥1，只放宽预测区间，避免过度自信）"],
            ["场景典型观测噪声", f"{d['scenario_typical_noise']:.0%}（场景声明值）"],
            ["本次注入观测噪声", f"{d['obs_noise_injected']:.0%}"
                                 f"（虚拟实验直接查询真值函数，故默认不注入）"],
            ["训练集", f"{d['n_train']} 点，{d['sampling']}"],
            ["测试集", f"{d['n_test']} 点，与训练集不相交"],
        ]},
        {"type": "p", "text":
            "长度尺度配置（各向同性 ls 的 0.5/1/2 档 vs 各向异性 ARD）已在**与训练集、测试集"
            "都独立的验证集**上自动选定（见第 5 章），官方测试集全程不参与——因此该选择"
            "不构成对验证指标的乐观偏差。这是 ASME V&V 10 对「验证数据不得用于挑配置」的落实。"},
    ]})

    # 4 Code Verification -----------------------------------------------------
    rep = ev.get("replication")
    if rep:
        b4 = [
            {"type": "p", "text":
                "同一实验在两组独立随机初始化（seed 不同）下重跑，比较指标差异。"
                "差异在阈值内说明方法本身稳定，本次结论不是抽样运气。"},
            {"type": "table", "headers": ["种子", "R²", "RMSE", "覆盖率"], "rows": [
                ["A", f"{rep['a']['r2']:.4f}", f"{rep['a']['rmse']:.6g}", f"{rep['a']['coverage']:.3f}"],
                ["B", f"{rep['b']['r2']:.4f}", f"{rep['b']['rmse']:.6g}", f"{rep['b']['coverage']:.3f}"],
            ]},
            {"type": "p", "text":
                f"相对 RMSE 差异 {rep['rel_rmse_diff']:.4f}，覆盖率差异 {rep['cov_diff']:.4f}，"
                f"阈值 {rep['threshold']} → **{'通过' if rep['passed'] else '未通过'}**。"},
        ]
    else:
        b4 = [{"type": "p", "text": "本次未执行双种子复现检验。"}]
    secs.append({"n": 4, "title": "Code Verification", "blocks": b4})

    # 5 Uncertainty Quantification -------------------------------------------
    b5 = [
        {"type": "table", "headers": ["指标", "数值", "说明"], "rows": [
            ["Out-of-sample R²", f"{m['r2']:.4f}", "正式判定依据（全测试集）"],
            ["核心工况 R²", f"{m['r2_core']:.4f}", "剔除真值两端各 10% 后的 R²，反映操作工况质量"],
            ["RMSE", f"{m['rmse']:.6g}", "预测均值对基准的均方根误差"],
            ["相对 RMSE", f"{m['rmse_rel']:.4f}", "除以响应标准差，跨场景可比"],
            ["95% 覆盖率", f"{m['coverage']:.3f}", f"名义 {NOMINAL_COVERAGE}"],
            ["覆盖率偏差", f"{m['coverage_gap']:+.3f}", "负=过自信，正=保守"],
            ["校准误差", f"{m['cal_err']:.3f}", "abs(coverage − 0.95)"],
            ["平均后验 std", f"{m['mean_std']:.6g}", "测试集上的平均不确定度"],
        ]},
    ]
    if ev.get("loo"):
        loo = ev["loo"]
        b5.append({"type": "p", "text":
            f"留一交叉验证（{loo['n']} 折）：R²={loo['r2']:.4f}，"
            f"RMSE={loo['rmse']:.6g}，覆盖率={loo['coverage']:.3f}。"
            f"与留出集结果互为交叉印证。"})

    sel = ev.get("config_selection")
    if sel:
        if sel.get("candidates"):
            rows = [[k, f"{c['r2']:.4f}", f"{c['coverage']:.3f}", f"{c['score']:.4f}",
                     "✓ 选中" if k == sel["chosen_key"] else ""]
                    for k, c in sel["candidates"].items()]
            b5.append({"type": "h3", "text": "长度尺度配置选择（独立验证集准则）"})
            b5.append({"type": "table",
                       "headers": ["候选配置", "切分 R²", "切分覆盖率", "综合分数 R²−2·|cov−0.95|", "选择"],
                       "rows": rows})
            b5.append({"type": "p", "text":
                f"选中 **{sel['chosen_key']}**：{sel['note']}。"
                f"（测试集全程未参与选择，故不构成对验证指标的乐观偏差。）"})
        else:
            b5.append({"type": "h3", "text": "长度尺度配置选择"})
            b5.append({"type": "p", "text":
                f"选中 **{sel['chosen_key']}**：{sel['note']}。"})
    secs.append({"n": 5, "title": "Uncertainty Quantification", "blocks": b5})

    # 6 Validation Metrics ----------------------------------------------------
    secs.append({"n": 6, "title": "Validation Metrics", "blocks": [
        {"type": "p", "text": f"判定结果：**{v['grade']}**"},
        {"type": "p", "text": f"依据：{v['rationale']}"},
        {"type": "table", "headers": ["等级", "条件", "本份"], "rows": [
            ["PASS", f"R² ≥ {PASS_R2} 且 coverage ≥ 0.90（保守亦可）",
             "命中" if v["grade"] == GRADE_PASS else "未命中"],
            ["MARGINAL", f"R² ≥ {MARGINAL_R2} 且 coverage ≥ 0.80",
             "命中" if v["grade"] == GRADE_MARGINAL else "未命中"],
            ["REFUTED", "其余（拟合不足或过度自信 coverage<0.80）", "命中" if v["grade"] == GRADE_REFUTED else "未命中"],
        ]},
        {"type": "p", "text":
            "阈值口径与 skills/paper-claim-validation 的 claim 分级一致，"
            "同一项目不出现两套「什么叫通过」的标准。"},
    ]})

    # 6.5 Baseline Comparison (RF vs GP) -----------------------------------
    bc = ev.get("baseline_comparison")
    if bc and not bc.get("error"):
        rf = bc["rf"]
        gp = bc["gp"]
        secs.append({"n": "6.5", "title": "Baseline Comparison (RF vs GP)", "blocks": [
            {"type": "p", "text":
                "RandomForest+Conformal 基线在同一批 train/test 数据上的得分，"
                "作为验证闸门区分能力的锚点。RF 不参与生产预测，仅作外部对标。"},
            {"type": "table", "headers": ["指标", "GP", "RF+Conformal", "差异"], "rows": [
                ["R²", f"{gp['r2']:.4f}", f"{rf['r2']:.4f}",
                 f"{bc['delta']['r2_gap']:+.4f}"],
                ["95% Coverage", f"{gp['coverage']:.4f}", f"{rf['coverage']:.4f}",
                 f"{bc['delta']['coverage_gap']:+.4f}"],
                ["Conformal Q", f"{gp.get('conformal_Q', 'N/A')}", f"{rf['conformal_Q']:.4f}",
                 "—"],
                ["Grade", gp["grade"], rf["grade"],
                 "GP 优于 RF" if gp["grade"] in ("PASS", "MARGINAL") and rf["grade"] == "REFUTED"
                 else ("GP 明显优于 RF" if gp["grade"] == "PASS" and rf["grade"] in ("MARGINAL", "REFUTED")
                       else "两者接近" if gp["grade"] == rf["grade"] else "RF 优于 GP")],
            ]},
            {"type": "p", "text": bc["headline"]},
            {"type": "p", "text":
                f"RF 配置：{rf['n_estimators']} 棵 CART 树（max_depth=4），"
                f"cross-conformal 校准（k=5, 3 repeats）。"
                f"RMSE_rel={rf['rmse_rel']:.4f}，NMPIW={rf['nmpiw']:.4f}。"},
            {"type": "p", "text": bc.get("note", "")},
        ]})

    # 7 Out-of-Disposition Gate ----------------------------------------------
    gate = ev.get("ood_gate")
    if gate:
        b7 = [
            {"type": "p", "text":
                "「替代真实实验」的前提是不可信的区必须自己说出来。本门禁对域内点放行，"
                "对域外点（bounds 外扩 1.6–2.2 倍）显式拒绝。"},
            {"type": "table", "headers": ["探针集", "点数", "trusted", "caution", "out_of_distribution"],
             "rows": [
                 ["域内", gate["inside"]["n"], gate["inside"]["trusted"],
                  gate["inside"]["caution"], gate["inside"]["out_of_distribution"]],
                 ["域外", gate["outside"]["n"], gate["outside"]["trusted"],
                  gate["outside"]["caution"], gate["outside"]["out_of_distribution"]],
             ]},
            {"type": "p", "text":
                f"域外拒绝率 {gate['rejection_rate']:.3f} → 门禁"
                f"{'有效' if gate['effective'] else '未达预期（≥0.90）'}。"
                f"OOD 判定阈值 {gate['inside']['ood_threshold']:.4f}（归一化距离）。"},
        ]
    else:
        b7 = [{"type": "p", "text": "本次未执行越界门禁实测。"}]
    secs.append({"n": 7, "title": "Out-of-Disposition Gate", "blocks": b7})

    # 8 Recommendations -------------------------------------------------------
    rec = ev["recommendations"]
    rows8 = [["最大后验方差（主动学习）", ", ".join(f"{x:.4g}" for x in rec["next_maxvar"])]]
    if rec.get("next_bald"):
        rows8.append(["BALD（集成分歧，信息量最大）",
                      ", ".join(f"{x:.4g}" for x in rec["next_bald"])])
    secs.append({"n": 8, "title": "Recommendations", "blocks": [
        {"type": "p", "text":
            "下一步最该做的实验点（若做真实实验校验，优先这两点；"
            "在虚拟实验体系内，这两点用于最快降低后验不确定度）。"},
        {"type": "table", "headers": ["策略", "推荐点"], "rows": rows8},
    ]})

    # 9 Reproducibility Manifest ---------------------------------------------
    p = ev["provenance"]
    cmd = (f'python engine/report_generator.py --scenario {s["key"]} '
           f'--n-train {d["n_train"]} --n-test {d["n_test"]} --seed {d["seed"]} --format md'
           + (f' --obs-noise {d["obs_noise_injected"]}' if d["obs_noise_injected"] else ""))
    secs.append({"n": 9, "title": "Reproducibility Manifest", "blocks": [
        {"type": "p", "text": "以下信息足以让第三方重跑出同一组数值。本报告不依赖网络与实验设备。"},
        {"type": "table", "headers": ["项", "值"], "rows": [
            ["scenario key", s["key"]],
            ["seed", str(d["seed"])],
            ["n_train / n_test", f"{d['n_train']} / {d['n_test']}"],
            ["采样", d["sampling"]],
            ["核 / 长度尺度", f"{d['kernel']} / {d['lengthscale_source']}"],
            ["GP 噪声地板 / 注入观测噪声",
             f"{d['noise_floor']:.0%} / {d['obs_noise_injected']:.0%}"],
            ["代码 commit", p["code_commit"]],
            ["Python / numpy", f"{p['python']} / {p['numpy']}"],
            ["引擎", p["engine"]],
            ["生成时间(UTC)", p["generated_at_utc"]],
            ["需要网络", "否"],
            ["需要实验设备", "否"],
        ]},
        {"type": "code", "text": cmd},
    ]})

    # 10 References -----------------------------------------------------------
    refs = s["references"] or []
    b10 = [{"type": "p", "text":
        "下列文献由场景定义自带（发表公式 / 系数来源），非 LLM 生成，因此不存在幻觉引用。"}]
    b10.append({"type": "list", "items": refs} if refs
               else {"type": "p", "text": "该场景未提供文献引用。"})
    if s["published"]:
        b10.append({"type": "p", "text": f"发布基准值：{s['published']}"})
    secs.append({"n": 10, "title": "References", "blocks": b10})

    return {
        "meta": {
            "title": f"Virtual Experiment V&V Report — {s['key']}",
            "scenario": s["key"],
            "domain": s["domain"],
            "grade": v["grade"],
            "generated_at_utc": ev["provenance"]["generated_at_utc"],
            "standard": "ASME V&V 10-2019 / FDA CM&S report outline",
        },
        "sections": secs,
        "evidence": ev,
    }


# ===========================================================================
# 渲染器：Markdown / HTML
# ===========================================================================
def render_markdown(rep: dict) -> str:
    m = rep["meta"]
    L = [f"# {m['title']}", ""]
    L += [f"- 场景：{m['scenario']}（{m['domain']}）",
          f"- 判定：**{m['grade']}**",
          f"- 生成时间（UTC）：{m['generated_at_utc']}",
          f"- 结构对齐：{m['standard']}",
          ""]
    for sec in rep["sections"]:
        L.append(f"## {sec['n']}. {sec['title']}")
        L.append("")
        for b in sec["blocks"]:
            t = b["type"]
            if t == "p":
                L.append(b["text"])
                L.append("")
            elif t == "h3":
                L.append(f"### {b['text']}")
                L.append("")
            elif t == "list":
                for it in b["items"]:
                    L.append(f"- {it}")
                L.append("")
            elif t == "table":
                L.append("| " + " | ".join(b["headers"]) + " |")
                L.append("|" + "|".join(["---"] * len(b["headers"])) + "|")
                for r in b["rows"]:
                    L.append("| " + " | ".join(str(c) for c in r) + " |")
                L.append("")
            elif t == "code":
                L.append("```bash")
                L.append(b["text"])
                L.append("```")
                L.append("")
    L.append("---")
    L.append("")
    L.append("*本报告由 SwarmLabs 虚拟实验引擎确定性生成：全部数值可重跑，"
             "无 LLM 参与，无幻觉引用。*")
    return "\n".join(L)


_HTML_CSS = """
:root{--ink:#1a1d23;--muted:#5b6472;--line:#dfe3ea;--bg:#ffffff;--soft:#f6f8fa;
--brand:#1f4fd8;--pass:#0f7b3f;--marginal:#a06700;--refuted:#b3261e;}
*{box-sizing:border-box}
body{margin:0 auto;max-width:960px;padding:48px 32px;background:var(--bg);color:var(--ink);
font:15px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans SC",Helvetica,Arial,sans-serif;}
h1{font-size:26px;line-height:1.35;margin:0 0 16px}
h2{font-size:19px;margin:38px 0 12px;padding-bottom:6px;border-bottom:2px solid var(--line)}
h3{font-size:15px;margin:22px 0 8px;color:var(--muted)}
p{margin:10px 0}
ul{margin:10px 0;padding-left:22px}
li{margin:5px 0}
table{border-collapse:collapse;width:100%;margin:14px 0;font-size:14px}
th,td{border:1px solid var(--line);padding:8px 10px;text-align:left;vertical-align:top}
th{background:var(--soft);font-weight:600}
code{background:var(--soft);padding:2px 6px;border-radius:4px;font-size:13px}
pre{background:var(--soft);padding:12px 14px;border-radius:6px;overflow-x:auto}
pre code{background:none;padding:0}
.meta{background:var(--soft);border-left:4px solid var(--brand);padding:14px 18px;
border-radius:0 6px 6px 0;margin-bottom:8px}
.badge{display:inline-block;padding:3px 12px;border-radius:999px;color:#fff;
font-weight:600;font-size:13px;letter-spacing:.03em}
.badge.PASS{background:var(--pass)}.badge.MARGINAL{background:var(--marginal)}
.badge.REFUTED{background:var(--refuted)}
.toolbar{position:sticky;top:0;z-index:10;background:rgba(255,255,255,.94);
border-bottom:1px solid var(--line);padding:10px 14px;margin:-48px -32px 22px;
display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.toolbar a,.toolbar button{font:600 13px/1 inherit;border:1px solid var(--line);
background:var(--soft);color:var(--ink);padding:8px 14px;border-radius:6px;cursor:pointer;
text-decoration:none}
.toolbar a:hover,.toolbar button:hover{border-color:var(--brand);color:var(--brand)}
.toolbar .spacer{flex:1}
.toc{background:var(--soft);border:1px solid var(--line);border-radius:8px;padding:14px 18px;margin:6px 0 26px}
.toc h2{border:none;margin:0 0 8px;font-size:15px;padding:0}
.toc ol{margin:0;padding-left:20px;columns:2;column-gap:34px}
.toc a{color:var(--brand);text-decoration:none}
.toc a:hover{text-decoration:underline}
a.anchor{color:inherit;text-decoration:none}
footer{margin-top:44px;padding-top:14px;border-top:1px solid var(--line);
color:var(--muted);font-size:13px}
@media print{
  body{max-width:none;padding:0 12mm;font-size:10.5pt;color:#000}
  .toolbar{display:none}
  .toc{border:none;background:none;padding:0;margin:0 0 16px}
  .toc ol{columns:1}
  h1{font-size:21pt}h2{font-size:14pt;page-break-after:avoid;border-color:#999}
  h3{page-break-after:avoid}
  table,pre,img{page-break-inside:avoid}tr{page-break-inside:avoid}
  thead{display:table-header-group}
  .meta{background:none;border-left:3px solid #000;padding-left:10px}
  footer{border-top:1px solid #999}
  a{color:#000;text-decoration:none}
}
"""


def _esc(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _inline(s: str) -> str:
    """极简行内标记：**粗体** 与 `code`，够用且不引入依赖。"""
    out = _esc(s)
    import re

    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    return out


def render_html(rep: dict) -> str:
    m = rep["meta"]
    H = ['<!DOCTYPE html>', '<html lang="zh-CN">', '<head>',
         '<meta charset="utf-8">',
         '<meta name="viewport" content="width=device-width,initial-scale=1">',
         f'<title>{_esc(m["title"])}</title>',
         f'<style>{_HTML_CSS}</style>', '</head>', '<body>']
    H.append(f'<h1>{_esc(m["title"])}</h1>')
    H.append('<div class="meta">')
    H.append(f'<div>场景：<strong>{_esc(m["scenario"])}</strong>　{_esc(m["domain"])}</div>')
    H.append(f'<div style="margin-top:6px">判定：<span class="badge {m["grade"]}">'
             f'{m["grade"]}</span></div>')
    H.append(f'<div style="margin-top:6px;color:var(--muted);font-size:13px">'
             f'生成时间（UTC）：{_esc(m["generated_at_utc"])}　·　'
             f'结构对齐：{_esc(m["standard"])}</div>')
    H.append('</div>')

    # 工具栏（屏幕可见，打印时隐藏）：返回 + 一键导出 PDF（浏览器原生打印）
    H.append('<div class="toolbar">'
             '<a href="/report-gallery">← 报告库</a>'
             '<button onclick="window.print()">导出 PDF / 打印</button>'
             '<span class="spacer"></span>'
             f'<span style="color:var(--muted);font-size:13px">场景：{_esc(m["scenario"])}</span>'
             '</div>')

    # 目录（含锚点跳转）：让「厚报告」可被快速导航
    H.append('<div class="toc"><h2>目录</h2><ol>')
    for sec in rep["sections"]:
        H.append(f'<li><a href="#sec-{sec["n"]}">{sec["n"]}. {_esc(sec["title"])}</a></li>')
    H.append('</ol></div>')

    for sec in rep["sections"]:
        H.append(f'<h2 id="sec-{sec["n"]}"><a class="anchor" '
                 f'href="#sec-{sec["n"]}">{sec["n"]}. {_esc(sec["title"])}</a></h2>')
        for b in sec["blocks"]:
            t = b["type"]
            if t == "p":
                H.append(f'<p>{_inline(b["text"])}</p>')
            elif t == "h3":
                H.append(f'<h3>{_esc(b["text"])}</h3>')
            elif t == "list":
                H.append("<ul>" + "".join(f"<li>{_inline(i)}</li>" for i in b["items"]) + "</ul>")
            elif t == "table":
                H.append("<table><thead><tr>"
                         + "".join(f"<th>{_esc(h)}</th>" for h in b["headers"])
                         + "</tr></thead><tbody>")
                for r in b["rows"]:
                    H.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>")
                H.append("</tbody></table>")
            elif t == "code":
                H.append(f'<pre><code>{_esc(b["text"])}</code></pre>')
    H.append('<footer>本报告由 SwarmLabs 虚拟实验引擎确定性生成：全部数值可重跑，'
             '无 LLM 参与，无幻觉引用。浏览器 Ctrl/Cmd+P 可直接打印或另存为 PDF。</footer>')
    H.append("</body></html>")
    return "\n".join(H)


# ---------------------------------------------------------------------------
# DOCX 渲染（从结构化 rep 直接构建，不依赖 HTML 解析；供「下载一份厚报告」）
# ---------------------------------------------------------------------------
def _docx_set_eastasia(el, font: str) -> None:
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    rpr = el.get_or_add_rPr()
    rfonts = rpr.find(qn('w:rFonts'))
    if rfonts is None:
        rfonts = OxmlElement('w:rFonts')
        rpr.append(rfonts)
    rfonts.set(qn('w:eastAsia'), font)


def _docx_inline(par, text: str) -> None:
    """把行内 **粗体** 与 `code` 标记转为 docx run。"""
    parts = re.split(r'(\*\*[^*]+\*\*|`[^`]+`)', text or "")
    for part in parts:
        if not part:
            continue
        if part.startswith('**') and part.endswith('**'):
            r = par.add_run(part[2:-2])
            r.bold = True
        elif part.startswith('`') and part.endswith('`'):
            r = par.add_run(part[1:-1])
            r.font.name = 'Consolas'
            _docx_set_eastasia(r._element, 'Consolas')
        else:
            par.add_run(part)


def _docx_code(doc, text: str) -> None:
    for line in (text or "").split("\n"):
        p = doc.add_paragraph()
        p.paragraph_format.left_indent = 14
        r = p.add_run(line)
        r.font.name = 'Consolas'
        r.font.size = __import__('docx').shared.Pt(9)
        _docx_set_eastasia(r._element, 'Consolas')


def _docx_table(doc, headers, rows) -> None:
    tbl = doc.add_table(rows=1, cols=max(1, len(headers)))
    try:
        tbl.style = 'Light Grid Accent 1'
    except KeyError:
        pass  # noqa: silent-ok — KeyError 可安全忽略（已审计 2026-10-08）
    hdr = tbl.rows[0].cells
    for i, h in enumerate(headers):
        hdr[i].text = ""
        _docx_inline(hdr[i].paragraphs[0], str(h))
        for run in hdr[i].paragraphs[0].runs:
            run.bold = True
    for row in rows:
        cells = tbl.add_row().cells
        for i, c in enumerate(row):
            cells[i].text = ""
            _docx_inline(cells[i].paragraphs[0], str(c))


def render_docx(rep: dict) -> bytes:
    """渲染为 .docx 二进制（供下载）。从结构化 rep 直接构建，不依赖网络与 HTML。"""
    from docx import Document
    from docx.shared import Pt, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    doc = Document()
    # 默认字体（兼顾中文）
    normal = doc.styles['Normal']
    normal.font.name = 'Calibri'
    normal.font.size = Pt(10.5)
    _docx_set_eastasia(normal.element, 'Microsoft YaHei')

    m = rep["meta"]
    doc.add_heading(m["title"], level=0)
    sub = doc.add_paragraph()
    rs = sub.add_run(f"场景：{m['scenario']}（{m['domain']}）")
    rs.bold = True
    badge = sub.add_run(f"　判定：{m['grade']}")
    badge.bold = True
    gcol = {
        "PASS": RGBColor(0x0f, 0x7b, 0x3f),
        "MARGINAL": RGBColor(0xa0, 0x67, 0x00),
        "REFUTED": RGBColor(0xb3, 0x26, 0x1e),
    }.get(m["grade"])
    if gcol:
        badge.font.color.rgb = gcol
    meta = doc.add_paragraph()
    mr = meta.add_run(f"生成时间（UTC）：{m['generated_at_utc']}　·　结构对齐：{m['standard']}")
    mr.italic = True

    for sec in rep["sections"]:
        doc.add_heading(f"{sec['n']}. {sec['title']}", level=1)
        for b in sec["blocks"]:
            t = b["type"]
            if t == "p":
                _docx_inline(doc.add_paragraph(), b["text"])
            elif t == "h3":
                doc.add_heading(b["text"], level=2)
            elif t == "list":
                for it in b["items"]:
                    _docx_inline(doc.add_paragraph(style="List Bullet"), it)
            elif t == "table":
                _docx_table(doc, b["headers"], b["rows"])
            elif t == "code":
                _docx_code(doc, b["text"])

    foot = doc.add_paragraph()
    fr = foot.add_run("本报告由 SwarmLabs 虚拟实验引擎确定性生成：全部数值可重跑，"
                     "无 LLM 参与，无幻觉引用。")
    fr.italic = True

    bio = io.BytesIO()
    doc.save(bio)
    return bio.getvalue()


# ===========================================================================
# 入口
# ===========================================================================
def generate(key: str, fmt: str = "md", out: str | None = None, n_train: int | None = None,
             n_test: int | None = None, seed: int = 0, with_loo: bool = True,
             with_replication: bool = True, obs_noise: float | None = None,
             with_baseline: bool = True) -> str:
    """生成一份 V&V 报告；out 为空则只返回内容（不落盘）。"""
    ev = collect_evidence(get_scenario(key), n_train=n_train, n_test=n_test, seed=seed,
                          with_loo=with_loo, with_replication=with_replication,
                          obs_noise=obs_noise, with_baseline=with_baseline)
    rep = build_report(ev)
    fmt = fmt.lower()
    if fmt in ("md", "markdown"):
        text = render_markdown(rep)
    elif fmt in ("html", "htm"):
        text = render_html(rep)
    elif fmt == "json":
        text = json.dumps(ev, ensure_ascii=False, indent=2)
    elif fmt == "docx":
        text = render_docx(rep)
    else:
        raise ValueError(f"unsupported format '{fmt}' (md / html / json / docx)")
    if out:
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        is_bin = isinstance(text, (bytes, bytearray))
        with open(out, "wb" if is_bin else "w",
                  encoding=None if is_bin else "utf-8") as f:
            f.write(text)
    return text


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        description="V&V 报告生成器：把虚拟实验的 UQ 指标渲染成可归档文档")
    ap.add_argument("--scenario", help="场景 key，如 micro_ecoli_glucose / surrogate_branin")
    ap.add_argument("--list", action="store_true", help="列出全部可用场景")
    ap.add_argument("--format", default="md", choices=["md", "html", "json", "docx"])
    ap.add_argument("--out", help="输出文件路径（缺省打印到 stdout）")
    ap.add_argument("--out-dir", help="批量输出目录（配合 --all）")
    ap.add_argument("--all", action="store_true", help="为全部场景生成报告")
    ap.add_argument("--n-train", type=int)
    ap.add_argument("--n-test", type=int)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--no-loo", action="store_true", help="跳过留一交叉验证（更快）")
    ap.add_argument("--no-replication", action="store_true", help="跳过双种子复现检验")
    ap.add_argument("--obs-noise", type=float, default=None,
                    help="注入相对观测噪声（如 0.05）；默认不注入，虚拟实验直接查询真值函数")
    args = ap.parse_args(argv)

    if args.list:
        reg = all_scenarios()
        for k in sorted(reg):
            print(f"{k}\t{reg[k].get('domain', '')}")
        print(f"\n共 {len(reg)} 个场景", file=sys.stderr)
        return 0

    if args.all:
        outdir = args.out_dir or os.path.join(ROOT, "data", "reports")
        os.makedirs(outdir, exist_ok=True)
        reg = all_scenarios()
        grades = {}
        for i, k in enumerate(sorted(reg), 1):
            try:
                text = generate(k, fmt=args.format,
                                out=os.path.join(outdir, f"{k}.{args.format}"),
                                n_train=args.n_train, n_test=args.n_test, seed=args.seed,
                                with_loo=not args.no_loo,
                                with_replication=not args.no_replication,
                                obs_noise=args.obs_noise)
                if args.format == "json":
                    ev = json.loads(text)
                else:
                    rep = build_report(collect_evidence(
                        get_scenario(k), n_train=args.n_train, n_test=args.n_test,
                        seed=args.seed, with_loo=False, with_replication=False,
                        obs_noise=args.obs_noise))
                    ev = rep["evidence"]
                grades[k] = ev["verdict"]["grade"]
                print(f"[{i}/{len(reg)}] {k}: {grades[k]}")
            except Exception as e:  # 单场景失败不应中断批量
                grades[k] = f"ERROR: {e}"
                print(f"[{i}/{len(reg)}] {k}: ERROR {e}")
        n_pass = sum(1 for g in grades.values() if g == GRADE_PASS)
        n_marg = sum(1 for g in grades.values() if g == GRADE_MARGINAL)
        n_ref = sum(1 for g in grades.values() if g == GRADE_REFUTED)
        n_err = sum(1 for g in grades.values() if str(g).startswith("ERROR"))
        print(f"\n批量完成：PASS {n_pass} / MARGINAL {n_marg} / REFUTED {n_ref} / ERROR {n_err}"
              f"（共 {len(grades)}）→ {outdir}")
        return 0 if n_err == 0 else 1

    if not args.scenario:
        ap.error("需要 --scenario，或用 --list 查看可用场景")

    text = generate(args.scenario, fmt=args.format, out=args.out, n_train=args.n_train,
                    n_test=args.n_test, seed=args.seed, with_loo=not args.no_loo,
                    with_replication=not args.no_replication, obs_noise=args.obs_noise)
    if not args.out:
        if isinstance(text, (bytes, bytearray)):
            sys.stdout.buffer.write(text)
        else:
            print(text)
    else:
        ev = collect_evidence(get_scenario(args.scenario), n_train=args.n_train,
                              n_test=args.n_test, seed=args.seed,
                              with_loo=False, with_replication=False,
                              obs_noise=args.obs_noise)
        print(f"已写入 {args.out}（判定 {ev['verdict']['grade']}）", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
