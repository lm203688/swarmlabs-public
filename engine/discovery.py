"""SINDy 可解释假设发现 —— 把「无 LLM 创意层」短板转为差异化长板。

纯 numpy，零依赖、零 LLM。给定观测 (X, y)，从候选库（多项式 + 三角函数）
中**稀疏**辨识出简洁的符号方程。每个系数带 bootstrap 置信区间，结论可复现、
可证伪、带误差棒。

与 LLM 方案对比（诚实）：arXiv:2604.13316 实测 LLM 科研代理 68% 忽略证据、
仅 26% 据反证修正，生成的公式是「注释性」而非「可验证」的。SINDy 是数据驱动
的稀疏回归，结论可证伪——这正是「虚拟实验对等现实」需要的创意层形态。
"""

from __future__ import annotations

import itertools

import numpy as np


class SINDy:
    """Sequentially Thresholded Least Squares 稀疏辨识。

    Parameters
    ----------
    poly_degree : 多项式候选最高次数（含）
    use_sin / use_cos : 是否加入三角函数候选
    threshold : STLSQ 系数阈值（归一化空间）
    """

    def __init__(self, poly_degree=3, use_sin=False, use_cos=False, threshold=1e-2):
        self.poly_degree = int(poly_degree)
        self.use_sin = bool(use_sin)
        self.use_cos = bool(use_cos)
        self.threshold = float(threshold)
        self.coef_ = None
        self.active_ = None
        self.names_ = None
        self.train_r2_ = None
        self.coef_std_ = None

    # ---- 候选库 -----------------------------------------------------------
    def _library(self, X):
        n, d = X.shape
        cols, names = [np.ones(n)], ["1"]
        idx = list(range(d))
        for deg in range(1, self.poly_degree + 1):
            for combo in itertools.combinations_with_replacement(idx, deg):
                term = np.ones(n)
                label = []
                for i in combo:
                    term = term * X[:, i]
                    label.append(f"x{i}")
                cols.append(term)
                names.append("*".join(label) if len(label) > 1 else label[0])
        if self.use_sin:
            for i in idx:
                cols.append(np.sin(X[:, i]))
                names.append(f"sin(x{i})")
        if self.use_cos:
            for i in idx:
                cols.append(np.cos(X[:, i]))
                names.append(f"cos(x{i})")
        return np.column_stack(cols), names

    # ---- 拟合 -------------------------------------------------------------
    def fit(self, X, y, n_boot=40, rng=None):
        X = np.atleast_2d(np.asarray(X, float))
        y = np.asarray(y, float).ravel()
        Theta, names = self._library(X)
        self.names_ = names
        col_mean = Theta.mean(0)
        col_std = Theta.std(0) + 1e-9
        Th = (Theta - col_mean) / col_std

        coef, *_ = np.linalg.lstsq(Th, y, rcond=None)
        for _ in range(12):
            small = np.abs(coef) < self.threshold
            coef[small] = 0.0
            active = ~small
            if active.sum() == 0:
                break
            A = Th[:, active]
            c, *_ = np.linalg.lstsq(A, y, rcond=None)
            new = np.zeros_like(coef)
            new[active] = c
            if np.allclose(new, coef, atol=1e-10):
                coef = new
                break
            coef = new

        self.coef_ = coef
        self.active_ = np.abs(coef) >= self.threshold
        # 固化训练集标准化统计：predict 必须对预测集用「训练统计」标准化，
        # 否则训练/预测特征尺度不一致 → 样本外预测偏差（Elo 对 SINDy 的排名会失真）。
        self._col_mean = col_mean
        self._col_std = col_std
        yp = Th @ coef
        self.train_r2_ = float(1 - np.sum((y - yp) ** 2) / (np.sum((y - y.mean()) ** 2) + 1e-12))

        # bootstrap 系数置信区间（诚实 UQ）
        rng = rng or np.random.default_rng(2026)
        boots = []
        for _ in range(n_boot):
            sel = rng.integers(0, len(y), len(y))
            try:
                c, *_ = np.linalg.lstsq(Th[sel], y[sel], rcond=None)
                for _ in range(6):
                    s = np.abs(c) < self.threshold
                    c[s] = 0.0
                    act = ~s
                    if act.sum() == 0:
                        break
                    cc, *_ = np.linalg.lstsq(Th[sel][:, act], y[sel], rcond=None)
                    nn = np.zeros_like(c)
                    nn[act] = cc
                    if np.allclose(nn, c, atol=1e-10):
                        c = nn
                        break
                    c = nn
                boots.append(c)
            except np.linalg.LinAlgError:
                continue
        if boots:
            self.coef_std_ = np.std(np.array(boots), axis=0)
        else:
            self.coef_std_ = np.zeros_like(coef)
        return self

    # ---- 预测 / 打分 -------------------------------------------------------
    def predict(self, X):
        X = np.atleast_2d(np.asarray(X, float))
        Theta, _ = self._library(X)
        # 必须用训练集统计标准化（self._col_mean/_col_std 由 fit 固化），
        # 预测集自身统计会随采样偏移，导致系数作用在错误尺度上。
        Th = (Theta - self._col_mean) / self._col_std
        return Th @ self.coef_

    def score(self, X, y):
        yp = self.predict(X)
        y = np.asarray(y, float).ravel()
        return float(1 - np.sum((y - yp) ** 2) / (np.sum((y - y.mean()) ** 2) + 1e-12))

    # ---- 方程字符串（可直发报告） ----------------------------------------
    def equation(self, var_names=None):
        if self.coef_ is None:
            raise RuntimeError("call fit() first")
        var_names = var_names or [f"x{i}" for i in range(1000)]
        terms = []
        for name, c, a in zip(self.names_, self.coef_, self.active_):
            if not a:
                continue
            disp = name
            if name == "1":
                disp = ""
            sign = "+" if c >= 0 else "-"
            mag = abs(c)
            terms.append(f"{sign} {mag:.4g}*{disp}" if disp else f"{sign} {mag:.4g}")
        eq = " ".join(terms).strip()
        eq = eq.replace("+ -", "- ")
        eq = eq[2:] if eq.startswith("+ ") else eq
        return f"y = {eq}"

    def raw_equation(self, var_names=None):
        """文本教科书形态方程：把 STLSQ 归一化空间的系数还原到原始尺度。

        STLSQ 阈值作用于「归一化后」的系数，因此 equation() 打印的是归一化空间。
        要让公式能被「教科书」直接读懂（例如 Van der Pol μ=1 时系数就是 -1, +1, -1），
        需要除以候选列的标准差。常数列（"1"）的 std 已被 SINDy 设为 1e-9，此处对
        常数列不做除法（其系数即原始常数）。
        """
        if self.coef_ is None:
            raise RuntimeError("call fit() first")
        terms = []
        col_std = getattr(self, "_col_std", None)
        for name, c, a in zip(self.names_, self.coef_, self.active_):
            if not a:
                continue
            disp = name
            if name == "1":
                disp = ""
                mag = abs(c)
            elif col_std is not None:
                mag = abs(c) / float(col_std[list(self.names_).index(name)])
            else:
                mag = abs(c)
            sign = "+" if c >= 0 else "-"
            terms.append(f"{sign} {mag:.4g}*{disp}" if disp else f"{sign} {mag:.4g}")
        eq = " ".join(terms).strip()
        eq = eq.replace("+ -", "- ")
        eq = eq[2:] if eq.startswith("+ ") else eq
        return f"y = {eq}"

    def summary(self):
        return {
            "equation": self.equation(),
            "train_r2": round(self.train_r2_, 4),
            "n_terms": int(self.active_.sum()),
            "coef_std_max": round(float(np.max(self.coef_std_[self.active_])), 4) if self.active_.any() else 0.0,
        }


class SINDySelect:
    """SINDy 变体的稀疏阈值自动选择器（Branas-Garza et al. 2021, arXiv:2108.02698）。

    动机：`SINDy.threshold` 是单点标量，太大会漏掉正确项（例如 Van der Pol 的
    `x^2 * v` 系数 -2.086 被 0.01 阈值筛掉），太小又引入过参数。SINDySelect
    在候选阈值网格上跑 K-fold CV，选**最稀疏**且 CV RMSE 仍低于 `(1 + tol) × 最小 RMSE`
    的模型——即「最简洁且几乎无损」的判据。

    参数
    ----
    tol : 允许的最大 RMSE 相对劣化（默认 1e-2 = 允许 ≤1% 的预测退化）
    n_folds : K 折交叉验证的 K（默认 5）
    n_thresholds : 候选阈值数量（默认 10，覆盖 log10 空间 [-4, 0]）
    """

    def __init__(self, poly_degree=3, use_sin=False, use_cos=False,
                 tol=1e-2, n_folds=5, n_thresholds=10):
        self.poly_degree = int(poly_degree)
        self.use_sin = bool(use_sin)
        self.use_cos = bool(use_cos)
        self.tol = float(tol)
        self.n_folds = int(n_folds)
        self.n_thresholds = int(n_thresholds)
        self.fitted_ = None
        self.selected_threshold_ = None
        self.cv_rmse_curve_ = None
        self.cv_terms_curve_ = None
        self.threshold_grid_ = None

    def fit(self, X, y, rng=None):
        X = np.atleast_2d(np.asarray(X, float))
        y = np.asarray(y, float).ravel()
        n = len(y)
        rng = rng or np.random.default_rng(2026)

        # 阈值网格：log10 均匀分布，从 1e-4 到 1.0
        self.threshold_grid_ = np.logspace(-4, 0, self.n_thresholds)

        # K 折索引
        idx = rng.permutation(n)
        folds = np.array_split(idx, self.n_folds)

        # 对每个候选阈值跑 CV
        rmse_curve = []
        terms_curve = []
        for thr in self.threshold_grid_:
            rmses = []
            for fi in range(self.n_folds):
                val = folds[fi]
                tr = np.setdiff1d(idx, val, assume_unique=True)
                try:
                    s = SINDy(poly_degree=self.poly_degree,
                               use_sin=self.use_sin, use_cos=self.use_cos,
                               threshold=thr)
                    s.fit(X[tr], y[tr], n_boot=0)
                    yv = s.predict(X[val])
                    rmses.append(np.sqrt(np.mean((y[val] - yv) ** 2)))
                except np.linalg.LinAlgError:
                    rmses.append(np.inf)
            rmse_curve.append(float(np.mean(rmses)))
            # 记录该阈值下的模型稀疏度（用全数据拟合一次，只作参考）
            s_probe = SINDy(poly_degree=self.poly_degree,
                            use_sin=self.use_sin, use_cos=self.use_cos,
                            threshold=thr)
            s_probe.fit(X, y, n_boot=0)
            terms_curve.append(int(s_probe.active_.sum()))

        self.cv_rmse_curve_ = rmse_curve
        self.cv_terms_curve_ = terms_curve

        # 判据：从最稀疏到最不稀疏排序，找 CV RMSE ≤ (1+tol)*min_rmse 的最小稀疏模型
        order = np.argsort(terms_curve)  # 从稀疏到稠密
        min_rmse = min(rmse_curve)
        threshold = self.threshold_grid_[int(order[0])]  # 默认取最稀疏
        for k in order:
            if rmse_curve[k] <= (1.0 + self.tol) * min_rmse:
                threshold = self.threshold_grid_[int(k)]
                break

        self.selected_threshold_ = float(threshold)
        # 用选定的阈值在全数据上拟合，得到最终模型（带 bootstrap CI）
        self.fitted_ = SINDy(poly_degree=self.poly_degree,
                             use_sin=self.use_sin, use_cos=self.use_cos,
                             threshold=threshold)
        self.fitted_.fit(X, y, n_boot=40, rng=rng)
        return self

    def predict(self, X):
        return self.fitted_.predict(X)

    def score(self, X, y):
        return self.fitted_.score(X, y)

    def equation(self, var_names=None):
        return self.fitted_.equation(var_names=var_names)

    def summary(self):
        return {
            "equation": self.equation(),
            "selected_threshold": round(self.selected_threshold_, 6),
            "train_r2": round(self.fitted_.train_r2_, 4),
            "n_terms": int(self.fitted_.active_.sum()),
            "cv_min_rmse": round(min(self.cv_rmse_curve_), 4),
            "threshold_grid": [round(float(t), 6) for t in self.threshold_grid_],
            "cv_rmse_curve": [round(float(r), 6) for r in self.cv_rmse_curve_],
            "cv_terms_curve": self.cv_terms_curve_,
        }
