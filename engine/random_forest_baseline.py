"""RandomForest 基线（纯 numpy）—— 仅作外部对标基线，不参与生产预测。

设计目的
--------
在 V&V 报告里产出一行「RF 在同数据上得分 X vs 我方 GP 得分 Y」的对比证据，
直接服务于「验证闸门能区分」的定位：一个 naive baseline 在同一场景上的得分
是可信度的锚点。RF 不进 SurrogateEngine 主路径，只在报告生成时离线跑。

为什么不直接用 sklearn
----------------------
项目长期原则「纯 numpy 自治」（轻量、可审计、随处可跑）。sklearn 是编译 C 库，
会打破自治性。本模块用 ~200 行纯 numpy 实现一个功能足够的最小 RF：
- DecisionTree: 深度受限 CART（max_depth=4, min_samples_leaf=3）
- RandomForest: bagging + 均值预测 + 树间方差作 epistemic UQ
- Conformal: cross-conformal 校准区间宽度（与 conformal.py 同口径）

与 GP 的公平对比
----------------
- 用同一批 train/test 数据（paper_validation_loop 的 LHS 采样）
- 同样的 z-score 归一化
- 同样的 conformal 校准方法（cross-conformal）
- 同样的判定阈值（verification_gate.py 的 PASS/MARGINAL/REFUTED）

用法
----
    from engine.random_forest_baseline import evaluate_scenario, compare_with_gp
    rf_metrics = evaluate_scenario(domain)
    comparison = compare_with_gp(domain)
"""

from __future__ import annotations

import hashlib
import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _stable_seed(key: str) -> int:
    """用 md5 派生稳定种子（避免 PYTHONHASHSEED 导致跨进程不可复现）。"""
    return int(hashlib.md5(key.encode("utf-8")).hexdigest(), 16) % (2 ** 30)


def _norm_ppf(q: float) -> float:
    """标准正态分位数（标准库，无 scipy 依赖）。"""
    from statistics import NormalDist
    return float(NormalDist().inv_cdf(float(q)))


# ===========================================================================
# DecisionTree —— 最小 CART（纯 numpy）
# ===========================================================================
class DecisionTree:
    """深度受限 CART 回归树。

    分裂准则：方差减少（variance reduction）。
    阈值候选：每维 10 分位点（小样本下比遍历所有点更高效）。
    """

    def __init__(self, max_depth: int = 4, min_samples_leaf: int = 3):
        self.max_depth = max_depth
        self.min_samples_leaf = min_samples_leaf
        self.tree: dict | None = None

    def fit(self, X: np.ndarray, y: np.ndarray) -> "DecisionTree":
        self.tree = self._grow(X, y, depth=0)
        return self

    def _grow(self, X: np.ndarray, y: np.ndarray, depth: int) -> dict:
        n = len(y)
        y_mean = float(y.mean())

        # 终止条件
        if depth >= self.max_depth or n < 2 * self.min_samples_leaf or np.std(y) < 1e-12:
            return {"leaf": y_mean}

        # 找最佳分裂
        best_feat, best_thresh, best_gain = None, None, 0.0
        parent_var = float(np.var(y))
        d = X.shape[1]

        for f in range(d):
            # 10 分位点候选阈值
            quantiles = np.unique(np.quantile(X[:, f], np.linspace(0.1, 0.9, 9)))
            if len(quantiles) == 0:
                continue
            for t in quantiles:
                left_mask = X[:, f] <= t
                right_mask = ~left_mask
                n_l, n_r = int(left_mask.sum()), int(right_mask.sum())
                if n_l < self.min_samples_leaf or n_r < self.min_samples_leaf:
                    continue
                y_l, y_r = y[left_mask], y[right_mask]
                gain = parent_var - (
                    (n_l / n) * float(np.var(y_l))
                    + (n_r / n) * float(np.var(y_r))
                )
                if gain > best_gain:
                    best_gain = gain
                    best_feat = f
                    best_thresh = float(t)

        if best_feat is None:
            return {"leaf": y_mean}

        left_mask = X[:, best_feat] <= best_thresh
        right_mask = ~left_mask
        return {
            "feature": best_feat,
            "threshold": best_thresh,
            "left": self._grow(X[left_mask], y[left_mask], depth + 1),
            "right": self._grow(X[right_mask], y[right_mask], depth + 1),
        }

    def predict(self, X: np.ndarray) -> np.ndarray:
        X = np.atleast_2d(X)
        return np.array([self._predict_one(x, self.tree) for x in X])

    def _predict_one(self, x: np.ndarray, node: dict) -> float:
        if "leaf" in node:
            return node["leaf"]
        if x[node["feature"]] <= node["threshold"]:
            return self._predict_one(x, node["left"])
        return self._predict_one(x, node["right"])


# ===========================================================================
# RandomForest —— Bagging 集成（纯 numpy）
# ===========================================================================
class RandomForest:
    """Bagging 决策树集成。预测返回 (mean, std)。

    std 是树间预测的标准差（epistemic uncertainty），用于：
    1. 直接作预测区间（mu ± z * std）
    2. 经 conformal 校准后作保形区间
    """

    def __init__(self, n_estimators: int = 20, max_depth: int = 4,
                 min_samples_leaf: int = 3, seed: int = 0):
        self.n_estimators = n_estimators
        self.max_depth = max_depth
        self.min_samples_leaf = min_samples_leaf
        self.seed = seed
        self.trees: list[DecisionTree] = []
        self._y_mean = 0.0
        self._y_std = 1.0
        self._fitted = False

    def fit(self, X: np.ndarray, y: np.ndarray) -> "RandomForest":
        X = np.atleast_2d(np.asarray(X, float))
        y = np.asarray(y, float).ravel()
        self._y_mean = float(y.mean())
        self._y_std = float(y.std()) + 1e-12
        # z-score 归一化（与 GP 同口径）
        Xn = (X - X.mean(axis=0)) / (X.std(axis=0) + 1e-12)
        yn = (y - self._y_mean) / self._y_std

        rng = np.random.default_rng(self.seed)
        n = len(y)
        self.trees = []
        for i in range(self.n_estimators):
            # bootstrap 采样
            idx = rng.integers(0, n, size=n)
            tree = DecisionTree(self.max_depth, self.min_samples_leaf)
            tree.fit(Xn[idx], yn[idx])
            self.trees.append(tree)
        self._fitted = True
        return self

    def predict(self, X: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """返回 (mean, std)。mean=树均值，std=树间标准差。"""
        if not self._fitted:
            raise RuntimeError("call fit() first")
        X = np.atleast_2d(np.asarray(X, float))
        Xn = (X - self._X_train_mean) / (self._X_train_std + 1e-12)
        preds = np.array([t.predict(Xn) for t in self.trees])  # (n_trees, n_query)
        mean_n = preds.mean(axis=0)
        std_n = preds.std(axis=0) + 1e-12
        # 反归一化回原始量纲
        mean_out = mean_n * self._y_std + self._y_mean
        std_out = std_n * self._y_std
        return mean_out, std_out

    def _init_train_stats(self, X: np.ndarray):
        self._X_train_mean = X.mean(axis=0)
        self._X_train_std = X.std(axis=0) + 1e-12


# 修复：在 fit 里记录训练集统计量（用于 predict 时归一化）
# 原 fit 方法里已经计算了 Xn，但 predict 需要原始的 mean/std
_orig_rf_fit = RandomForest.fit

def _rf_fit_patched(self, X, y):
    X = np.atleast_2d(np.asarray(X, float))
    self._X_train_mean = X.mean(axis=0)
    self._X_train_std = X.std(axis=0) + 1e-12
    return _orig_rf_fit(self, X, y)

RandomForest.fit = _rf_fit_patched


# ===========================================================================
# Conformal 校准 —— 给 RF 的 UQ 加保形保证
# ===========================================================================
def _nonconformity(y_true, mu, sd, eps=1e-12):
    """标准化残差 |y - mu| / sd。"""
    return np.abs(np.asarray(y_true, float) - np.asarray(mu, float)) / (np.asarray(sd, float) + eps)


def _conformal_quantile(scores, alpha=0.05):
    """Split/Cross conformal 的分位数（有限样本修正）。"""
    s = np.sort(np.asarray(scores, float).ravel())
    n = len(s)
    if n == 0:
        return np.inf
    k = int(np.ceil((n + 1) * (1 - alpha)))
    k = min(max(k, 1), n)
    return float(s[k - 1])


def cross_conformal_scores(rf: RandomForest, X, y, k_folds=5, seed=0,
                           n_estimators=20) -> np.ndarray:
    """K 折交叉保形：训练 k 个 RF，收集 OOF 标准化残差。

    与 conformal.py 的 cross_conformal_scores 同口径，但用 RF 后端。
    小样本下 split conformal 标定集太小，cross-conformal 把每个点都用上。
    """
    X = np.atleast_2d(np.asarray(X, float))
    y = np.asarray(y, float).ravel()
    n = len(y)
    k = int(min(max(k_folds, 2), n))
    all_scores = []

    for rep in range(3):  # 3 次重复，抵抗单次划分的运气
        idx = np.random.default_rng(seed + rep).permutation(n)
        folds = np.array_split(idx, k)
        scores = np.zeros(n)
        for f_idx, fold in enumerate(folds):
            tr = np.setdiff1d(idx, fold)
            if len(tr) < 3:
                scores[fold] = 1.96  # 无法训练，退回高斯名义值
                continue
            rf_fold = RandomForest(n_estimators=n_estimators, seed=seed + rep * 100 + f_idx)
            rf_fold.fit(X[tr], y[tr])
            mu, sd = rf_fold.predict(X[fold])
            scores[fold] = _nonconformity(y[fold], mu, sd)
        all_scores.append(scores)
    return np.concatenate(all_scores)


# ===========================================================================
# 场景评估 —— 产出与 GP 可对比的指标
# ===========================================================================
def evaluate_scenario(domain: dict, alpha: float = 0.05, n_estimators: int = 20,
                      k_folds: int = 5, seed: int = 0, noise_rel: float = 0.03) -> dict:
    """评估 RF+Conformal 基线在某个场景上的表现，返回与 GP 同口径的指标。

    返回 dict 含：
      key, n_train, n_test, alpha
      r2 (out-of-sample), rmse, rmse_rel
      coverage (95% conformal interval), cal_err
      conformal_Q, width_ratio (vs gaussian z=1.96)
      nmpiw (normalized mean prediction interval width)
      verdict (PASS/MARGINAL/REFUTED, 同 verification_gate 阈值)
    """
    from engine.verification_gate import (
        PASS_R2, MARGINAL_R2, COVERAGE_PASS, COVERAGE_MARGINAL,
        GRADE_PASS, GRADE_MARGINAL, GRADE_REFUTED,
    )

    f = domain["f"]
    key = domain["key"]
    X = np.atleast_2d(np.asarray(domain["train"], float))
    y = np.array([f(np.atleast_1d(x)) for x in X], float).ravel()
    Xt = np.atleast_2d(np.asarray(domain["test"], float))
    yt = np.array([f(np.atleast_1d(x)) for x in Xt], float).ravel()

    n_train, n_test = len(y), len(yt)
    y_range = float(np.max(y) - np.min(y)) or 1.0

    # 交叉保形校准
    scores = cross_conformal_scores(
        RandomForest(n_estimators=n_estimators, seed=seed),
        X, y, k_folds=k_folds, seed=seed, n_estimators=n_estimators
    )
    Q = _conformal_quantile(scores, alpha=alpha)

    # 全量训练 RF
    rf = RandomForest(n_estimators=n_estimators, seed=seed)
    rf.fit(X, y)
    mu, sd = rf.predict(Xt)

    # 保形区间
    lo, hi = mu - Q * sd, mu + Q * sd

    # 指标计算
    rmse = float(np.sqrt(np.mean((mu - yt) ** 2)))
    ss_tot = float(np.sum((yt - yt.mean()) ** 2))
    r2 = float(1 - np.sum((yt - mu) ** 2) / (ss_tot + 1e-12))
    rmse_rel = rmse / (float(np.std(y)) + 1e-9)
    coverage = float(np.mean((yt >= lo) & (yt <= hi)))
    cal_err = float(abs(coverage - (1 - alpha)))
    nmpiw = float(np.mean(hi - lo)) / y_range

    # 判定（同 verification_gate 阈值）
    z = _norm_ppf(1 - alpha / 2.0)  # ≈1.96
    if r2 >= PASS_R2 and coverage >= COVERAGE_PASS:
        grade = GRADE_PASS
    elif r2 >= MARGINAL_R2 and coverage >= COVERAGE_MARGINAL:
        grade = GRADE_MARGINAL
    else:
        grade = GRADE_REFUTED

    return {
        "key": key,
        "n_train": n_train,
        "n_test": n_test,
        "alpha": alpha,
        "r2": round(r2, 4),
        "rmse": round(rmse, 6),
        "rmse_rel": round(rmse_rel, 4),
        "coverage": round(coverage, 4),
        "cal_err": round(cal_err, 4),
        "conformal_Q": round(Q, 4),
        "width_ratio_conformal_vs_gaussian": round(Q / float(z), 4) if Q < 100 else None,
        "nmpiw": round(nmpiw, 4),
        "grade": grade,
        "n_estimators": n_estimators,
        "method": "RandomForest (bagged CART, pure numpy) + cross-conformal",
    }


def evaluate_on_data(domain: dict, X: np.ndarray, y: np.ndarray,
                     Xt: np.ndarray, yt: np.ndarray,
                     alpha: float = 0.05, n_estimators: int = 20,
                     k_folds: int = 5, seed: int = 0) -> dict:
    """在给定数据上评估 RF+Conformal（与 GP 用同一批数据，公平对比）。

    这是 collect_evidence 的集成入口：用 collect_evidence 生成的 X/y/Xt/yt
    跑 RF，确保 GP 和 RF 看到完全相同的数据。
    """
    from engine.verification_gate import (
        PASS_R2, MARGINAL_R2, COVERAGE_PASS, COVERAGE_MARGINAL,
        GRADE_PASS, GRADE_MARGINAL, GRADE_REFUTED,
    )

    X = np.atleast_2d(np.asarray(X, float))
    y = np.asarray(y, float).ravel()
    Xt = np.atleast_2d(np.asarray(Xt, float))
    yt = np.asarray(yt, float).ravel()

    n_train, n_test = len(y), len(yt)
    y_range = float(np.max(y) - np.min(y)) or 1.0

    # 交叉保形校准
    scores = cross_conformal_scores(
        RandomForest(n_estimators=n_estimators, seed=seed),
        X, y, k_folds=k_folds, seed=seed, n_estimators=n_estimators
    )
    Q = _conformal_quantile(scores, alpha=alpha)

    # 全量训练 RF
    rf = RandomForest(n_estimators=n_estimators, seed=seed)
    rf.fit(X, y)
    mu, sd = rf.predict(Xt)

    # 保形区间
    lo, hi = mu - Q * sd, mu + Q * sd

    # 指标
    rmse = float(np.sqrt(np.mean((mu - yt) ** 2)))
    ss_tot = float(np.sum((yt - yt.mean()) ** 2))
    r2 = float(1 - np.sum((yt - mu) ** 2) / (ss_tot + 1e-12))
    rmse_rel = rmse / (float(np.std(y)) + 1e-9)
    coverage = float(np.mean((yt >= lo) & (yt <= hi)))
    cal_err = float(abs(coverage - (1 - alpha)))
    nmpiw = float(np.mean(hi - lo)) / y_range

    # 判定
    if r2 >= PASS_R2 and coverage >= COVERAGE_PASS:
        grade = GRADE_PASS
    elif r2 >= MARGINAL_R2 and coverage >= COVERAGE_MARGINAL:
        grade = GRADE_MARGINAL
    else:
        grade = GRADE_REFUTED

    return {
        "key": domain.get("key", "unknown"),
        "r2": round(r2, 4),
        "rmse": round(rmse, 6),
        "rmse_rel": round(rmse_rel, 4),
        "coverage": round(coverage, 4),
        "cal_err": round(cal_err, 4),
        "conformal_Q": round(Q, 4),
        "nmpiw": round(nmpiw, 4),
        "grade": grade,
        "n_estimators": n_estimators,
        "method": "RandomForest (bagged CART, pure numpy) + cross-conformal",
    }


def compare_with_gp(domain: dict, alpha: float = 0.05, n_estimators: int = 20,
                    k_folds: int = 5, seed: int = 0) -> dict:
    """同时跑 RF 和 GP，返回对比 dict（CLI 用，数据来自 domain["train"]/["test"]）。

    报告生成器请用 evaluate_on_data() 传入 collect_evidence 的同一批数据。
    """
    rf_metrics = evaluate_scenario(domain, alpha=alpha, n_estimators=n_estimators,
                                    k_folds=k_folds, seed=seed)

    # 用与 RF 完全相同的数据跑 GP（确保公平）
    try:
        from engine.surrogate import SurrogateEngine
        X = np.atleast_2d(np.asarray(domain["train"], float))
        y = np.array([domain["f"](np.atleast_1d(x)) for x in X], float).ravel()
        Xt = np.atleast_2d(np.asarray(domain["test"], float))
        yt = np.array([domain["f"](np.atleast_1d(x)) for x in Xt], float).ravel()

        eng = SurrogateEngine(domain)
        eng.fit(X, y, noise_rel=0.03)
        mu_gp, sd_gp = eng.predict(Xt)

        # GP 用同样的 conformal 校准（从 engine.conformal 导入）
        from engine.conformal import cross_conformal_scores as gp_cross, conformal_quantile as gp_q
        gp_scores = gp_cross(domain, X, y, k_folds=k_folds, seed=seed, noise_rel=0.03, repeats=3)
        gp_Q = gp_q(gp_scores, alpha=alpha)
        lo_gp, hi_gp = mu_gp - gp_Q * sd_gp, mu_gp + gp_Q * sd_gp

        from engine.verification_gate import (
            PASS_R2, MARGINAL_R2, COVERAGE_PASS, COVERAGE_MARGINAL,
            GRADE_PASS, GRADE_MARGINAL, GRADE_REFUTED,
        )
        rmse_gp = float(np.sqrt(np.mean((mu_gp - yt) ** 2)))
        ss_tot = float(np.sum((yt - yt.mean()) ** 2))
        r2_gp = float(1 - np.sum((yt - mu_gp) ** 2) / (ss_tot + 1e-12))
        coverage_gp = float(np.mean((yt >= lo_gp) & (yt <= hi_gp)))
        cal_err_gp = float(abs(coverage_gp - (1 - alpha)))

        if r2_gp >= PASS_R2 and coverage_gp >= COVERAGE_PASS:
            grade_gp = GRADE_PASS
        elif r2_gp >= MARGINAL_R2 and coverage_gp >= COVERAGE_MARGINAL:
            grade_gp = GRADE_MARGINAL
        else:
            grade_gp = GRADE_REFUTED

        gp_metrics = {
            "r2": round(r2_gp, 4),
            "coverage": round(coverage_gp, 4),
            "cal_err": round(cal_err_gp, 4),
            "conformal_Q": round(gp_Q, 4),
            "grade": grade_gp,
            "method": "GP (RBF kernel) + cross-conformal",
        }
    except Exception as e:
        gp_metrics = {"error": str(e), "grade": "ERROR"}

    delta = {
        "r2_gap": round(rf_metrics["r2"] - gp_metrics.get("r2", 0), 4),
        "coverage_gap": round(rf_metrics["coverage"] - gp_metrics.get("coverage", 0), 4),
    }

    headline = (
        f"GP R²={gp_metrics.get('r2', 'N/A')} / cov={gp_metrics.get('coverage', 'N/A')} "
        f"({gp_metrics.get('grade', 'N/A')}) vs "
        f"RF R²={rf_metrics['r2']} / cov={rf_metrics['coverage']} "
        f"({rf_metrics['grade']})"
    )

    return {
        "key": domain["key"],
        "gp": gp_metrics,
        "rf": rf_metrics,
        "delta": delta,
        "headline": headline,
    }


def main() -> int:
    """CLI: 全部场景跑 RF 基线对比。"""
    import argparse
    ap = argparse.ArgumentParser(description="RF+Conformal 基线对比")
    ap.add_argument("--key", help="单个场景 key")
    ap.add_argument("--all", action="store_true", help="全部场景")
    ap.add_argument("--out", help="输出 JSON 文件路径")
    ap.add_argument("--n-estimators", type=int, default=20)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    # 加载场景
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import paper_validation_loop as pvl
    from engine.microbiology_full import build_full_scenarios
    from scripts.paper_validation_loop import _build_micro_scenarios

    scenarios = [s for s in pvl.SCENARIOS if s.get("train") is not None and s.get("test") is not None]
    micro = _build_micro_scenarios()
    all_scenarios = scenarios + micro

    if args.key:
        all_scenarios = [s for s in all_scenarios if args.key in s["key"]]
    if not args.all and not args.key:
        # 默认只跑跨域场景（快）
        all_scenarios = scenarios

    if not all_scenarios:
        print("[err] no scenario matched")
        return 2

    results = []
    print(f"{'scenario':30s} | {'GP R²':>7s} {'GP cov':>7s} {'GP grade':>10s} | "
          f"{'RF R²':>7s} {'RF cov':>7s} {'RF grade':>10s}")
    print("-" * 90)
    for dom in all_scenarios:
        try:
            comp = compare_with_gp(dom, n_estimators=args.n_estimators, seed=args.seed)
            gp = comp["gp"]
            rf = comp["rf"]
            gp_grade = gp.get("grade", "ERR")
            gp_r2 = gp.get("r2", 0)
            gp_cov = gp.get("coverage", 0)
            print(f"{comp['key']:30s} | {gp_r2:7.4f} {gp_cov:7.4f} {gp_grade:>10s} | "
                  f"{rf['r2']:7.4f} {rf['coverage']:7.4f} {rf['grade']:>10s}")
            results.append(comp)
        except Exception as e:
            print(f"{dom['key']:30s} | !! {type(e).__name__}: {e}")

    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump({"generated_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S"),
                       "results": results}, f, ensure_ascii=False, indent=2)
        print(f"\n-> {args.out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
