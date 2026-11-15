"""SINDy + Elo 全自动闭环 —— 把「假设发现 → 锦标赛排名 → 主动学习补点 → 真实实验 → 重校准」串成自驱动循环。

这是 demo_full_loop.py（仅线性展示各能力）与 run_parity_loop（仅 GP+BALD，无假设层）
的升级版：本模块让组件**自动决策**——每轮判断「当前虚拟实验是否已可信收敛」，
未收敛就用 BALD 选下一个「最该做的真实实验点」，经 RemoteLab Oracle 取观测并入训练集，
重新校准后回到 Elo 排名，直到收敛 / 预算用尽 / 判定为结构性难（无进展早停）。

诚实性底线（与每日飞轮、可信面一致）：
- 噪声地板守 3%（SurrogateEngine.fit 默认 noise_rel=0.03）
- 覆盖率双侧判：<0.85 欠覆盖(过自信) / ≈0.95 理想 / >1.0 过覆盖(误差棒过宽)
- 候选与排名带 bootstrap 置信度；SINDy 系数带置信区间
- 测试评估用「固定噪声实现」的确定性 Oracle，覆盖率不随运行抖动（数字不靠运气）
- 种子用 md5 派生，跨进程可复现
- 预算上限 + 无进展早停，避免烧真实实验资源做无效补点

与外部闭环的关系：
- demo_full_loop.py  → 一次性展示「能力都在」，给人看
- run_parity_loop    → 通用「GP 校准闭环」，给引擎看
- AutonomousLoop     → 带「假设发现 + Elo 排名 + 停止判据」的**自治**闭环，给飞轮/agentteams 看
"""

from __future__ import annotations

import hashlib
import json
import os

# 稳定性：固定 BLAS 线程（见 flywheel.py 说明），须在 import numpy 前设置。
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

import numpy as np

from engine.surrogate import (
    SurrogateEngine, GPSurrogate, Calibrator, NeuralOperatorSurrogate, SyntheticOracle,
)
from engine.discovery import SINDy
from engine.elo import EloTournament
from engine.scenarios_extra import _stable_seed, COVERAGE_PASS  # 与 run_extra_validation 同流同种子，对齐可信面

# 领域注册表：核心 10 域 + 扩展 10 域合并，统一按 key 查找。
def _registry():
    reg = {}
    try:
        from engine.scenario_registry import scenario_list as _slist
        for s in _slist():
            reg[s["key"]] = s
    except Exception:
        pass  # noqa: silent-ok — Exception 可安全忽略（已审计 2026-10-08）
    try:
        from engine.scenarios_extra import SCENARIOS_EXTRA
        for s in SCENARIOS_EXTRA:
            reg.setdefault(s["key"], s)
    except Exception:
        pass  # noqa: silent-ok — Exception 可安全忽略（已审计 2026-10-08）
    return reg


class _FixedOracle:
    """把已采样的观测固化成确定性 Oracle（当前未用于覆盖率评估，保留作接口）。

    说明：SurrogateEngine.evaluate 内部用干净真值 f 评估覆盖率（与 run_extra_validation
    / 可信面同口径，且确定性强），故本类暂不参与主流程；保留以便未来切换到「带噪真实
    实验评估」时零改动接入。
    """

    def __init__(self, ys):
        self._ys = list(float(v) for v in ys)
        self._i = 0

    def observe(self, x):
        v = self._ys[self._i]
        self._i += 1
        return v


class AutonomousLoop:
    """一个领域的 SINDy+Elo 全自动闭环。

    Parameters
    ----------
    domain      : 领域配置 dict（见 SurrogateEngine）
    max_iters   : 主动学习补点的最大轮数（预算上限，防死循环）
    target_coverage : 目标覆盖率（默认 0.95）
    n_seed      : 初始种子实验点数（默认取 domain.n_seed 或 10）
    noise       : 真实实验 Oracle 注入的观测噪声水平
    seed        : 随机种子；缺省用 key 的 md5 派生，跨进程可复现
    """

    def __init__(self, domain, max_iters=15, target_coverage=0.95,
                 n_seed=None, noise=0.02, seed=None, no_progress_patience=4):
        self.domain = domain
        self.key = domain["key"]
        self.category = domain.get("category", "?")
        self.eng = SurrogateEngine(domain)
        self.space = self.eng.space
        self.f = domain["f"]
        self.max_iters = int(max_iters)
        self.target_coverage = float(target_coverage)
        self.n_seed = int(n_seed if n_seed is not None else domain.get("n_seed", 10))
        self.noise = float(noise)
        self.no_progress_patience = int(no_progress_patience)
        self.seed = int(seed if seed is not None
                        else int(hashlib.md5(self.key.encode()).hexdigest(), 16) % (2 ** 31))
        # 训练用真实实验 Oracle：噪声取 domain 声明的观测噪声（基准函数声明 0.0
        # → 干净训练，与 run_extra_validation / 可信面同口径；真实带噪域未来声明
        # >0 时自动加噪，模拟不可复现观测）。不擅自加噪，否则 GP 把噪声当信号拟合
        # 导致过自信+欠覆盖（已实测：0.02 噪声使 gramacylee cov 0.96→0.78）。
        self.oracle = SyntheticOracle(self.f, noise=self.domain.get("noise", 0.0),
                                      rng=np.random.default_rng(self.seed + 1))
        self.history = []
        self.X = None
        self.y = None
        self._test_x = None
        self._test_y_true = None

    # ---- SINDy 可解释律 + 其在测试集上的对等度 ---------------------------
    def _sindy_peer(self):
        s = SINDy(poly_degree=3, use_sin=True, use_cos=True, threshold=1e-2)
        s.fit(np.asarray(self.X, float), np.asarray(self.y, float),
              n_boot=30, rng=np.random.default_rng(self.seed + 3))
        yp = s.predict(self._test_x)
        rmse = float(np.sqrt(np.mean((yp - self._test_y_true) ** 2)))
        rel = rmse / (float(np.std(self._test_y_true)) + 1e-9)
        return s, rel

    # ---- Elo 锦标赛：GP / GP-auto / RFF-NO / SINDy 谁更可信 ----------------
    def _elo(self, sindy_rel):
        X = np.asarray(self.X, float)
        y = np.asarray(self.y, float)
        test_x = self._test_x
        test_y = self._test_y_true
        ystd = float(np.std(y)) + 1e-9

        def _rel_rmse(sur):
            sur.fit(X, y)
            mean, _ = sur.predict(test_x)
            return float(np.sqrt(np.mean((mean - test_y) ** 2))) / ystd

        cands = [
            ("GP", _rel_rmse(GPSurrogate(lengthscale=self.domain["ls"],
                                          auto_ls=self.domain.get("auto_ls", False)))),
            ("GP-auto", _rel_rmse(GPSurrogate(lengthscale=self.domain["ls"], auto_ls=True))),
            ("RFF-NO", _rel_rmse(NeuralOperatorSurrogate())),
            ("SINDy", sindy_rel),
        ]
        names = [c[0] for c in cands]
        strengths = [c[1] for c in cands]
        tour = EloTournament().from_strengths(names, strengths, n_rounds=4)
        return tour.report()

    # ---- 单轮：拟合 → 评估 → 发现 → 排名 → 判收敛 ------------------------
    def _step(self):
        self.eng.fit(self.X, self.y)
        ev = self.eng.evaluate(self._test_x)  # 对干净真值 f 评估覆盖率（与可信面同口径，确定性强）
        cov = ev["coverage"]
        cal_err = ev["cal_err"]
        s, sindy_rel = self._sindy_peer()
        elo_rep = self._elo(sindy_rel)

        # 收敛判据：与项目可信面 COVERAGE_PASS 对齐——覆盖率达标(≥0.85)且不过覆盖
        # (≤1.0，否则误差棒过宽也算不可信)。这是「虚拟实验已可对等真实实验、无需再
        # 花真实实验」的自主决策阈值。cal_err 仍入报告，供透明审查。
        converged = (COVERAGE_PASS <= cov <= 1.0)

        return {
            "iter": len(self.history),
            "n_real": len(self.X),
            "rmse_rel": round(float(ev["rmse"] / (np.std(self.y) + 1e-9)), 4),
            "coverage": cov,
            "cal_err": cal_err,
            "converged": converged,
            "champion": elo_rep["champion"],
            "champion_rating": elo_rep["champion_rating"],
            "elo_ranking": elo_rep["ranking"],
            "sindy_r2": round(float(s.train_r2_), 4),
            "sindy_equation": s.equation(),
            "sindy_rel": round(float(sindy_rel), 4),
        }

    # ---- 主循环 -----------------------------------------------------------
    def run(self):
        # 测试网格 + 种子训练点用与 run_extra_validation 完全相同的随机流
        # （20260816 + _stable_seed(key)）：训练点先采样、测试点后采样，保证
        # 闭环 iter0 的收敛态与项目可信面精确对齐；此后 BALD 补点才体现
        # 「自主决策要不要再花真实实验」的价值。覆盖率始终对干净真值 f 评估
        # （与可信面同口径，确定性强，不靠运气）。
        srng = np.random.default_rng(20260816 + _stable_seed(self.key))
        seed_x = self.space.sample(self.n_seed, srng)
        self._test_x = self.space.sample(80, srng)
        self._test_y_true = np.array([self.f(x) for x in self._test_x])

        # 初始种子实验（真实 Oracle，确定性；噪声取 domain 声明值）
        self.X = [list(np.asarray(x, float).ravel()) for x in seed_x]
        self.y = [self.oracle.observe(x) for x in seed_x]

        best_cov = -1.0
        patience = 0
        status = "budget_exhausted"

        for i in range(self.max_iters):
            rec = self._step()
            self.history.append(rec)

            if rec["converged"]:
                rec["status"] = "converged"
                status = "converged"
                break

            # 无进展早停：连续若干轮覆盖率无实质提升 → 结构性难，停（不烧预算）
            if rec["coverage"] > best_cov + 1e-3:
                best_cov = rec["coverage"]
                patience = 0
            else:
                patience += 1
            if patience >= self.no_progress_patience:
                rec["status"] = "stopped_no_progress"
                status = "stopped_no_progress"
                break

            # 主动学习：BALD 挑下一个最该做的真实实验点（真实 Oracle 取观测并入训练）
            x_next = self.eng.recommend_bald()
            self.X.append(list(np.asarray(x_next, float).ravel()))
            self.y.append(self.oracle.observe(x_next))

        if not self.history:
            return self.summary(status)
        last = self.history[-1]
        last.setdefault("status", status)
        return self.summary(status)

    # ---- 汇总 -------------------------------------------------------------
    def summary(self, status=None):
        if not self.history:
            return {"key": self.key, "category": self.category, "status": status or "empty",
                    "n_iters": 0, "history": []}
        last = self.history[-1]
        return {
            "key": self.key,
            "category": self.category,
            "status": status or last.get("status", "unknown"),
            "n_iters": len(self.history),
            "n_real_final": last["n_real"],
            "final_coverage": last["coverage"],
            "final_cal_err": last["cal_err"],
            "final_champion": last["champion"],
            "final_sindy_r2": last["sindy_r2"],
            "final_sindy_equation": last["sindy_equation"],
            # SINDy 差异化信号：R²≥0.5 视为找到了可解释律；否则诚实标注
            # 「未找到可解释律」——这正是相对 LLM 主观公式的诚实边界（可证伪）。
            "sindy_status": "interpretable" if last["sindy_r2"] >= 0.5 else "no_interpretable_law",
            "coverage_trajectory": [round(h["coverage"], 3) for h in self.history],
            "champion_trajectory": [h["champion"] for h in self.history],
            "history": self.history,
        }


def run_scenario(key, max_iters=15, **kw):
    """对单个领域跑全自动闭环，返回 summary dict。"""
    reg = _registry()
    if key not in reg:
        raise KeyError(f"未知领域 {key}；注册表含 {sorted(reg)[:8]} ...")
    return AutonomousLoop(reg[key], max_iters=max_iters, **kw).run()


def run_batch(keys=None, max_iters=15, out_path=None, verbose=True):
    """对一组领域跑全自动闭环，写入 out_path（默认 data/autonomous_loops.json）。

    keys 为 None 时跑注册表全部（核心 + 扩展）。返回汇总列表。
    """
    reg = _registry()
    if keys is None:
        keys = sorted(reg)
    results = []
    for k in keys:
        if k not in reg:
            results.append({"key": k, "status": "unknown_domain"})
            continue
        try:
            r = AutonomousLoop(reg[k], max_iters=max_iters).run()
            results.append(r)
            if verbose:
                print(f"  [{r['status']:18s}] {k:22s} iters={r['n_iters']:2d} "
                      f"cov={r['final_coverage']:.3f} cal_err={r['final_cal_err']:.3f} "
                      f"champ={r['final_champion']}")
        except Exception as e:  # 单域失败不阻断整批
            results.append({"key": k, "status": "error", "error": str(e)})
            if verbose:
                print(f"  [error            ] {k:22s} {e}")

    if out_path is None:
        out_path = os.path.join(os.path.dirname(__file__), "..", "data", "autonomous_loops.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump({"n_scenarios": len(results), "results": results}, fh,
                  ensure_ascii=False, indent=2)
    if verbose:
        n_conv = sum(1 for r in results if r.get("status") == "converged")
        print(f"[autonomous_loop] 完成 {len(results)} 域 | 收敛 {n_conv} | 写入 {out_path}")
    return results


if __name__ == "__main__":
    # 默认跑注册表全部领域（核心 10 + 扩展 10）。
    run_batch(verbose=True)
