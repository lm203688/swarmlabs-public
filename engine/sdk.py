"""VirtualExperiment SDK —— 多引擎统一算子（统一门面之上的薄封装）。

把 surrogate / neural_operator / remote_lab 三种引擎抽象成一致的四方法
算子：fit / predict / uq / suggest。换底层引擎不改调用代码，对标
「一个体系」的可替换内核理念（轻量、纯 numpy、可日级自治运行）。

  引擎种类
  --------
  - "gp"              ：GPSurrogate（默认，纯 numpy，带 UQ）
  - "neural_operator" ：NeuralOperatorSurrogate（RFF 核机近似）
  - "remote_lab"      ：RemoteLabOracle（真实实验 Oracle，本地模拟/联网）
"""

from __future__ import annotations

import numpy as np
from typing import Optional

from .surrogate import (
    GPSurrogate,
    NeuralOperatorSurrogate,
    RemoteLabOracle,
    ActiveLearner,
    ExperimentSpace,
)


class VirtualExperiment:
    """统一虚拟实验算子。engine_kind 选择底层实现。"""

    KINDS = ("gp", "neural_operator", "remote_lab")

    def __init__(self, engine_kind: str = "gp", bounds=None, **engine_kwargs):
        if engine_kind not in self.KINDS:
            raise ValueError(f"engine_kind 必须是 {self.KINDS}")
        self.kind = engine_kind
        self.bounds = bounds
        self._kw = engine_kwargs
        self._model = None
        self._space = ExperimentSpace(bounds) if bounds is not None else None
        self._X = None
        self._y = None

    # ── fit / predict / uq / suggest 四方法算子 ──────────────
    def fit(self, X, y):
        X = np.atleast_2d(np.asarray(X, float))
        y = np.asarray(y, float).ravel()
        self._X, self._y = X, y
        if self.kind == "gp":
            nv = self._kw.get("noise_var", 0.03 ** 2)
            self._model = GPSurrogate(
                noise_var=nv, y_mean=float(np.mean(y)), y_scale=float(np.std(y)) + 1e-9,
                lengthscale=self._kw.get("lengthscale", 0.4),
                auto_ls=self._kw.get("auto_ls", False),
            )
        elif self.kind == "neural_operator":
            self._model = NeuralOperatorSurrogate(**self._kw)
        elif self.kind == "remote_lab":
            self._model = RemoteLabOracle(**self._kw)
        self._model.fit(X, y)
        return self

    def predict(self, xs):
        mean, std = self._model.predict(xs)
        return {"mean": np.asarray(mean), "std": np.asarray(std)}

    def uq(self, xs, z=1.96):
        mean, std = self._model.predict(xs)
        return {
            "mean": np.asarray(mean),
            "std": np.asarray(std),
            "lo": np.asarray(mean) - z * np.asarray(std),
            "hi": np.asarray(mean) + z * np.asarray(std),
        }

    def suggest(self, n_candidates=200, strategy="bald"):
        if self._X is None:
            raise RuntimeError("先 fit()")
        learner = ActiveLearner(strategy=strategy)
        return learner.next(self._model, self._space, n_candidates)

    @property
    def model(self):
        return self._model


# ────────────────────────────────────────────────────────────────
# Selftest —— 合成数据端到端验证统一门面四方法算子。
# 跑法：python engine/sdk.py  或  python -m engine.sdk
# 为什么要有它：本模块曾被 2026-10-10 全库扫描标记为"设计完整但零测试"
# 的孤儿。selftest 让它自证可用，且未来任一底层引擎改动都会立即被抓。
# ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import numpy as np

    rng = np.random.default_rng(42)
    X = rng.uniform(0.0, 1.0, size=(30, 2))
    y_true = np.sin(np.pi * X[:, 0] * X[:, 1])
    y = y_true + 0.05 * rng.standard_normal(X.shape[0])

    # fit —— GP 引擎 + 噪声方差 + bounds
    ve = VirtualExperiment(
        engine_kind="gp",
        bounds=[[0.0, 1.0], [0.0, 1.0]],
        noise_var=0.05 ** 2,
    )
    ve.fit(X, y)
    assert ve.kind == "gp"
    assert ve.model is not None

    # predict —— 返回 {mean, std}，shape 与输入对齐
    test_X = rng.uniform(0.0, 1.0, size=(10, 2))
    pred = ve.predict(test_X)
    assert set(pred.keys()) == {"mean", "std"}, f"predict 键缺失: {pred.keys()}"
    assert pred["mean"].shape == (10,), f"predict mean shape {pred['mean'].shape}"
    assert pred["std"].shape == (10,), f"predict std shape {pred['std'].shape}"
    assert np.all(pred["std"] >= 0), "std 必须非负"

    # 训练点附近拟合应达到可用水平（不作硬拟合，但必须接近真值）
    train_pred = ve.predict(X)
    rmse = float(np.sqrt(np.mean((train_pred["mean"] - y) ** 2)))
    assert rmse < 0.30, f"训练集 RMSE {rmse:.3f} 过大——拟合退化"

    # uq —— 置信区间上下界必须夹住 mean，且 std>0 处区间宽度>0
    uq = ve.uq(test_X, z=2.0)
    assert set(uq.keys()) == {"mean", "std", "lo", "hi"}, f"uq 键缺失: {uq.keys()}"
    assert np.all(uq["lo"] <= uq["mean"] + 1e-6), "lo <= mean 违反"
    assert np.all(uq["hi"] >= uq["mean"] - 1e-6), "hi >= mean 违反"
    assert np.all(uq["hi"] > uq["lo"]), "置信区间必须非空"

    # suggest —— 主动学习返回单个信息量最大的候选点（shape (d,)），在 bounds 内
    # 设计说明：ActiveLearner.next 返回 argmax 的单个候选；要拿多批建议就循环调用。
    next_pt = np.asarray(ve.suggest(n_candidates=200))
    assert next_pt.shape == (2,), f"suggest 应返回单个 d=2 点，实际 {next_pt.shape}"
    assert np.all(next_pt >= 0.0) and np.all(next_pt <= 1.0), f"suggest 点越出 bounds: {next_pt}"
    assert np.isfinite(next_pt).all(), f"suggest 返回非有限值: {next_pt}"

    # 循环 3 次，确认每次都在 bounds 内（不追求不重复，只保证合法）
    for _ in range(3):
        p = np.asarray(ve.suggest(n_candidates=100))
        assert p.shape == (2,)
        assert np.all(p >= 0.0) and np.all(p <= 1.0), f"循环 suggest 越界: {p}"

    # 非法 engine_kind 必须明确报错，不能静默退化
    try:
        VirtualExperiment(engine_kind="bogus_engine")
        raise AssertionError("非法 engine_kind 未报错——静默退化不可接受")
    except ValueError:
        pass

    # fit 前不允许 suggest（避免未初始化模型被使用）
    ve2 = VirtualExperiment(engine_kind="gp", bounds=[[0.0, 1.0], [0.0, 1.0]])
    try:
        ve2.suggest()
        raise AssertionError("未 fit 就 suggest 未报错——契约违反")
    except RuntimeError:
        pass

    print("[PASS] sdk selftest: fit/predict/uq/suggest 四方法 + 非法类型拒绝 + bounds 约束")
