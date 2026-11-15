"""Conformal calibration + UQ calibration metrics (pure numpy).

借鉴来源（只借鉴算法与指标定义，不引入依赖、不复制代码）：
  - MAPIE (scikit-learn-contrib/MAPIE, BSD-3) 与 deel-ai/puncc：保形预测
    (conformal prediction) 的核心思想 —— 用 out-of-fold 非一致性分数把区间
    缩放到"有限样本保证的边际覆盖"，而不是信任模型自己宣称的方差。
  - uncertainty-toolbox (MIT)：UQ 校准指标体系 —— PICP / MPIW /
    miscalibration area / interval score。单看 coverage 会被"无限宽的区间"
    刷满分，必须和 sharpness 一起判。
  - 理论依据：Vovk et al. cross-conformal；Angelopoulos & Bates,
    "A Gentle Introduction to Conformal Prediction" (2021)。

为什么需要它（本仓库的具体病灶）：
  engine/surrogate.py 的 Calibrator 用固定 z=1.96 的高斯区间，coverage 常
  出现 1.0（名义 0.95）—— 即区间过宽、过度保守。而 trust_surface 的 rubric
  只给 coverage 打分，等于"区间越宽分越高"。保形校准把区间宽度学到数据上，
  并允许用 sharpness 一起评价。

用法：
    python engine/conformal.py                 # 全部场景对比
    python engine/conformal.py --key branin    # 单场景
    python engine/conformal.py --alpha 0.10
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine.surrogate import GPSurrogate, SurrogateEngine  # noqa: E402


# ---------------------------------------------------------------- 保形分数
def nonconformity_std(y_true, mu, sd, eps=1e-12):
    """标准化残差 |y - mu| / sd。sd 已含观测噪声（surrogate.predict 保证）。"""
    return np.abs(np.asarray(y_true, float) - np.asarray(mu, float)) / (np.asarray(sd, float) + eps)


def conformal_quantile(scores, alpha=0.05, method="higher"):
    """Cross/split conformal 的分位数（有限样本修正）。

    Q = 第 ceil((n+1)(1-alpha)) 小的分数。用 method='higher' 取保守侧，
    保证覆盖不低于名义水平（Vovk 的 smoothed/marginally valid 构造）。
    """
    s = np.sort(np.asarray(scores, float).ravel())
    n = len(s)
    if n == 0:
        return np.inf
    k = int(np.ceil((n + 1) * (1 - alpha)))
    k = min(max(k, 1), n)
    # 第 k 小（1-indexed）
    return float(s[k - 1])


def _fit_engine(domain, X, y, noise_rel=0.03):
    """用统一内核 SurrogateEngine 拟合（z-score 归一化 + 相对噪声地板，与飞轮一致）。"""
    eng = SurrogateEngine(domain)
    eng.fit(X, y, noise_rel=noise_rel)
    return eng


def cross_conformal_scores(domain, X, y, k_folds=5, seed=0, noise_rel=0.03, repeats=3):
    """K 折交叉保形：训练 k 个模型，收集 out-of-fold 标准化残差。

    小样本下 split conformal 的标定集太小（分位数不稳），cross-conformal
    把每个点都用上，是 n 小（我们 n_seed 常为 6~12）时的正确变体。
    每折独立做 z-score 归一化，避免标定泄漏。

    repeats>1 时重复不同随机划分（repeated cross-conformal），把所有折的
    OOF 分数合并再取分位数——抵抗单次划分的运气成分。注意：重复带来的
    分数间相关性使严格的有限样本保证变为近似，此处按经验校准使用。
    """
    X = np.atleast_2d(np.asarray(X, float))
    y = np.asarray(y, float).ravel()
    n = len(y)
    k = int(min(max(k_folds, 2), n))
    all_scores = []

    for rep in range(max(1, int(repeats))):
        idx = np.random.default_rng(seed + rep).permutation(n)
        folds = np.array_split(idx, k)
        scores = np.zeros(n)
        for f in folds:
            tr = np.setdiff1d(idx, f)
            if len(tr) < 2:
                scores[f] = 1.96  # 无法训练，退回高斯名义值
                continue
            eng = _fit_engine(domain, X[tr], y[tr], noise_rel=noise_rel)
            mu, sd = eng.predict(X[f])
            scores[f] = nonconformity_std(y[f], mu, sd)
        all_scores.append(scores)
    return np.concatenate(all_scores)


# ---------------------------------------------------------------- 指标体系
def picp(y_true, lo, hi):
    """Prediction Interval Coverage Probability：真实覆盖率。"""
    y_true = np.asarray(y_true, float)
    return float(np.mean((y_true >= lo) & (y_true <= hi)))


def mpiw(lo, hi, y_range=None):
    """Mean Prediction Interval Width；给 y_range 时返回归一化 NMPIW（越小越 sharp）。"""
    w = float(np.mean(np.asarray(hi, float) - np.asarray(lo, float)))
    if y_range:
        return w / float(y_range)
    return w


def interval_score(y_true, lo, hi, alpha=0.05):
    """Winkler interval score：同时惩罚覆盖不足与区间过宽，越低越好。

    S = (hi-lo) + 2/alpha*(lo-y)_+ + 2/alpha*(y-hi)_+
    """
    y = np.asarray(y_true, float)
    lo = np.asarray(lo, float)
    hi = np.asarray(hi, float)
    pen = (2.0 / alpha) * (np.maximum(lo - y, 0.0) + np.maximum(y - hi, 0.0))
    return float(np.mean((hi - lo) + pen))


def miscalibration_area(y_true, mu, sd, levels=None):
    """校准曲线下的偏差面积（uncertainty-toolbox 的 miscalibration area 思路）。

    对每个名义水平 p，用高斯分位数 z_p 造区间，比较经验覆盖率；
    area = mean_p |emp_p - p|。0 = 完美校准，越大越糟。
    """
    y = np.asarray(y_true, float)
    mu = np.asarray(mu, float)
    sd = np.asarray(sd, float) + 1e-12
    if levels is None:
        levels = np.linspace(0.05, 0.95, 19)
    devs = []
    for p in levels:
        z = _norm_ppf(0.5 + p / 2.0)
        emp = np.mean(np.abs(y - mu) <= z * sd)
        devs.append(abs(emp - p))
    return float(np.mean(devs))


def _norm_ppf(q):
    """标准正态分位数。用标准库 statistics.NormalDist 的逆 CDF（精确，无 scipy 依赖）。"""
    from statistics import NormalDist
    return float(NormalDist().inv_cdf(float(q)))


# ---------------------------------------------------------------- 场景对比
def evaluate_scenario(domain, alpha=0.05, seed=0, noise_rel=0.03, k_folds=5, repeats=3):
    """对比 baseline(z=1.96) 与 cross-conformal 的区间质量。

    用场景自带的 train/test 固定集（与飞轮 validate_scenario 同一批数据），
    因此结果可与 data/trust_surface.json 的 coverage_after 直接对照。
    """
    f = domain["f"]
    X = np.atleast_2d(np.asarray(domain["train"], float))
    y = np.array([f(np.atleast_1d(x)) for x in X], float).ravel()
    Xt = np.atleast_2d(np.asarray(domain["test"], float))
    yt = np.array([f(np.atleast_1d(x)) for x in Xt], float).ravel()
    y_range = float(np.max(y) - np.min(y)) or 1.0

    scores = cross_conformal_scores(domain, X, y, k_folds=k_folds, seed=seed,
                                    noise_rel=noise_rel, repeats=repeats)
    Q = conformal_quantile(scores, alpha=alpha)

    eng = _fit_engine(domain, X, y, noise_rel=noise_rel)
    mu, sd = eng.predict(Xt)

    z = _norm_ppf(1 - alpha / 2.0)   # ≈1.96
    lo_b, hi_b = mu - z * sd, mu + z * sd
    lo_c, hi_c = mu - Q * sd, mu + Q * sd

    out = {
        "key": domain["key"],
        "n_train": int(len(y)), "n_test": int(len(yt)), "alpha": alpha,
        "conformal_Q": round(Q, 4),
        "gaussian_z": round(float(z), 4),
        "width_ratio_conformal_vs_gaussian": round(Q / float(z), 4),
        "baseline": {
            "picp": round(picp(yt, lo_b, hi_b), 4),
            "nmpiw": round(mpiw(lo_b, hi_b, y_range), 4),
            "interval_score": round(interval_score(yt, lo_b, hi_b, alpha), 4),
            "miscal_area": round(miscalibration_area(yt, mu, sd), 4),
        },
        "conformal": {
            "picp": round(picp(yt, lo_c, hi_c), 4),
            "nmpiw": round(mpiw(lo_c, hi_c, y_range), 4),
            "interval_score": round(interval_score(yt, lo_c, hi_c, alpha), 4),
            "miscal_area": round(miscalibration_area(yt, mu, sd * (Q / float(z))), 4),
        },
    }
    b = out["baseline"]
    c = out["conformal"]
    out["delta"] = {
        "picp": round(c["picp"] - b["picp"], 4),
        "nmpiw": round(c["nmpiw"] - b["nmpiw"], 4),
        "interval_score": round(c["interval_score"] - b["interval_score"], 4),
    }
    # 判定：覆盖率不显著低于名义水平(允许 1/(n+1)+0.02 的有限样本松弛) 且更 sharp 才算改善
    target = 1 - alpha
    slack = 1.0 / (len(scores) + 1) + 0.02
    c_ok = c["picp"] >= target - slack
    # Q 远大于高斯 z：说明该场景在 K 折重训下 OOF 残差重尾 ——
    # GP 的 UQ 在该场景不稳定，是体检信号，不是 conformal 的失败。
    out["q_explodes"] = bool(Q > 3.0)
    out["verdict"] = (
        "IMPROVED" if (c_ok and c["nmpiw"] < b["nmpiw"] - 1e-6) else
        "STILL_CONSERVATIVE" if (b["picp"] > target + 0.02 and c_ok) else
        "NO_GAIN" if c_ok else "UNDER_COVERS"
    )
    return out


def load_scenarios():
    """复用 paper_validation_loop 的跨域场景定义（统一内核，不另造）。"""
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import paper_validation_loop as pvl
    return [s for s in pvl.SCENARIOS if s.get("train") is not None and s.get("test") is not None]


def main() -> int:
    args = sys.argv[1:]
    alpha = 0.05
    only = None
    if "--alpha" in args:
        alpha = float(args[args.index("--alpha") + 1])
    if "--key" in args:
        only = args[args.index("--key") + 1]

    scen = load_scenarios()
    if only:
        scen = [s for s in scen if only in s[0]]
    if not scen:
        print(f"[err] no scenario matched (only={only})")
        return 2

    rows = []
    print(f"{'scenario':26s} {'Q':>6s} {'Q/z':>6s} | {'base_cov':>8s} {'conf_cov':>8s} "
          f"| {'base_w':>7s} {'conf_w':>7s} | {'ΔIS':>8s}  判定")
    for dom in scen:
        try:
            r = evaluate_scenario(dom, alpha=alpha)
        except Exception as e:
            print(f"{dom['key']:26s} !! {type(e).__name__}: {e}")
            continue
        rows.append(r)
        print(f"{r['key']:26s} {r['conformal_Q']:6.3f} {r['width_ratio_conformal_vs_gaussian']:6.3f} | "
              f"{r['baseline']['picp']:8.3f} {r['conformal']['picp']:8.3f} | "
              f"{r['baseline']['nmpiw']:7.4f} {r['conformal']['nmpiw']:7.4f} | "
              f"{r['delta']['interval_score']:+8.4f}  {r['verdict']}")

    if not rows:
        return 1

    n_imp = sum(1 for r in rows if r["verdict"] == "IMPROVED")
    n_cons = sum(1 for r in rows if r["verdict"] == "STILL_CONSERVATIVE")
    n_explode = sum(1 for r in rows if r["q_explodes"])
    # 用中位数：均值会被 Q 爆炸的少数场景（重尾残差）拉偏，不能代表典型表现
    med = lambda xs: float(np.median(xs))
    med_bw = med([r["baseline"]["nmpiw"] for r in rows])
    med_cw = med([r["conformal"]["nmpiw"] for r in rows])
    med_bc = med([r["baseline"]["picp"] for r in rows])
    med_cc = med([r["conformal"]["picp"] for r in rows])
    med_bis = med([r["baseline"]["interval_score"] for r in rows])
    med_cis = med([r["conformal"]["interval_score"] for r in rows])
    avg_bc = float(np.mean([r["baseline"]["picp"] for r in rows]))
    avg_cc = float(np.mean([r["conformal"]["picp"] for r in rows]))
    print(f"\n覆盖率(中位数): baseline {med_bc:.4f} -> conformal {med_cc:.4f} (目标 {1-alpha:.2f})")
    print(f"宽度(中位数):   {med_bw:.4f} -> {med_cw:.4f} ({(med_cw/med_bw-1)*100:+.1f}%)")
    print(f"区间分数(中位数，越低越好): {med_bis:.4f} -> {med_cis:.4f} ({(med_cis/med_bis-1)*100:+.1f}%)")
    print(f"IMPROVED {n_imp}/{len(rows)}，仍过度保守 {n_cons} 个，Q 重尾告警 {n_explode} 个")

    payload = {
        "generated_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S"),
        "alpha": alpha,
        "method": "repeated cross-conformal (K-fold out-of-fold standardized residuals, R repeats)",
        "borrowed_from": [
            "MAPIE (scikit-learn-contrib/MAPIE, BSD-3): conformal prediction interval construction",
            "deel-ai/puncc: conformal + calibration workflow",
            "uncertainty-toolbox (MIT): PICP/MPIW/miscalibration-area/interval-score metrics",
            "Vovk cross-conformal; Angelopoulos & Bates 2021",
        ],
        "summary": {
            "n_scenarios": len(rows),
            "median_coverage_baseline": round(med_bc, 4),
            "median_coverage_conformal": round(med_cc, 4),
            "median_width_baseline": round(med_bw, 4),
            "median_width_conformal": round(med_cw, 4),
            "median_interval_score_baseline": round(med_bis, 4),
            "median_interval_score_conformal": round(med_cis, 4),
            "mean_coverage_baseline": round(avg_bc, 4),
            "mean_coverage_conformal": round(avg_cc, 4),
            "n_improved": n_imp,
            "n_still_conservative": n_cons,
            "n_q_heavy_tail_warning": n_explode,
        },
        "rows": rows,
    }
    os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)
    with open(os.path.join(ROOT, "data", "conformal_calibration.json"), "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)
    print("\n-> data/conformal_calibration.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
