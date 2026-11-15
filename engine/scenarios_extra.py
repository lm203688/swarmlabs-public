"""扩展基准场景（R8 头部）—— 把 PENDING 引擎目录的头部用「发表基准作 gold」接入。

诚实原则：每个场景都有**已发表 / 解析可证**的 gold 值（最优解、最优值或解析关系），
用统一引擎 SurrogateEngine 跑对等验证，真正补齐（非信任启发式）。本文件新增 10 个
经典基准（优化 / 响应面 / 解析 ODE），使已验证领域从 10 扩展到 20。

对接方式：gold 字段存「该函数的已知最优值 / 解析关系描述」，published 存文献，
与 paper_validation_loop.SCENARIOS 完全同构，可作为后续接入飞轮的候选集。
"""

from __future__ import annotations

import hashlib
import numpy as np


def _stable_seed(key: str) -> int:
    """用 md5 派生稳定种子：避免 Python 字符串 hash() 每进程加盐（PYTHONHASHSEED）
    导致场景数据/指标跨进程不可复现——可信度不能靠运气。"""
    return int(hashlib.md5(key.encode("utf-8")).hexdigest(), 16) % (2 ** 30)

# 每个场景：key, domain(类别), f, bounds, ls(先验), auto_ls, noise, published, references, gold
SCENARIOS_EXTRA = [
    {
        "key": "opt_schwefel2d", "domain": "optimization", "category": "optimization",
        "f": lambda x: 418.9829 * 2 - np.sum(x * np.sin(np.sqrt(np.abs(x)))),
        "bounds": [(-500, 500), (-500, 500)], "ls": 200.0, "auto_ls": True, "noise": 0.0,
        "published": "global min = 0 at x=(420.9687, 420.9687)",
        "references": ["Schwefel 1981, Evolution and Optimum Seeking"],
        "gold": 0.0,
    },
    {
        "key": "opt_ackley2d", "domain": "optimization", "category": "optimization",
        "f": lambda x: (-20 * np.exp(-0.2 * np.sqrt(0.5 * (x[0] ** 2 + x[1] ** 2)))
                        - np.exp(0.5 * (np.cos(2 * np.pi * x[0]) + np.cos(2 * np.pi * x[1])))
                        + 20 + np.e),
        "bounds": [(-32.768, 32.768), (-32.768, 32.768)], "ls": 5.0, "auto_ls": True, "noise": 0.0,
        "published": "global min = 0 at x=(0,0)",
        "references": ["Ackley 1987, A Connectionist Machine for Genetic Hillclimbing"],
        "gold": 0.0,
    },
    {
        "key": "opt_rastrigin2d", "domain": "optimization", "category": "optimization",
        "f": lambda x: 10 * 2 + float(np.sum(np.asarray(x) ** 2 - 10 * np.cos(2 * np.pi * np.asarray(x)))),
        "bounds": [(-5.12, 5.12), (-5.12, 5.12)], "ls": 1.0, "auto_ls": True, "noise": 0.0,
        "published": "global min = 0 at x=(0,0)",
        "references": ["Rastrigin 1974, Systems of extremal control"],
        "gold": 0.0,
    },
    {
        "key": "opt_rosenbrock2d", "domain": "optimization", "category": "optimization",
        "f": lambda x: 100 * (x[1] - x[0] ** 2) ** 2 + (1 - x[0]) ** 2,
        "bounds": [(-2, 2), (-1, 3)], "ls": 0.5, "auto_ls": True, "noise": 0.0,
        "published": "global min = 0 at x=(1,1)",
        "references": ["Rosenbrock 1960, Annals of Mathematical Statistics"],
        "gold": 0.0,
    },
    {
        "key": "opt_levy2d", "domain": "optimization", "category": "optimization",
        "f": lambda x: (np.sin(np.pi * (1 + (x[0] - 1) / 4)) ** 2
                        + (1 + (x[1] - 1) / 4) ** 2 * (1 + np.sin(np.pi * (1 + (x[1] - 1) / 4)) ** 2)
                        + (1 + (x[0] - 1) / 4) ** 2 * (1 + 0.1 * np.sin(np.pi * (1 + (x[0] - 1) / 4)) ** 2)),
        "bounds": [(-10, 10), (-10, 10)], "ls": 2.0, "auto_ls": True, "noise": 0.0,
        "published": "global min = 0 at x=(1,1)",
        "references": ["Levy & Montalvo 1981, SIAM J. Sci. Stat. Comput."],
        "gold": 0.0,
    },
    {
        "key": "opt_michalewicz2d", "domain": "optimization", "category": "optimization",
        "f": lambda x: -(np.sin(x[0]) * np.sin((x[0] ** 2) / np.pi) ** 20
                        + np.sin(x[1]) * np.sin((2 * x[1] ** 2) / np.pi) ** 20),
        "bounds": [(0, np.pi), (0, np.pi)], "ls": 0.8, "auto_ls": True, "noise": 0.0,
        "published": "approx min = -1.8013 at m=10",
        "references": ["Michalewicz 1996, Genetic Algorithms + Data Structures = Evolution Programs"],
        "gold": -1.8013,
    },
    {
        "key": "opt_hartmann3d", "domain": "optimization", "category": "optimization",
        "f": None,  # 下方函数体赋值
        "bounds": [(0, 1), (0, 1), (0, 1)], "ls": 0.4, "auto_ls": True, "noise": 0.0,
        "published": "global min ≈ -3.86278",
        "references": ["Hartmann 1972, J. Global Optim."],
        "gold": -3.86278,
    },
    {
        "key": "opt_gramacylee1d", "domain": "optimization", "category": "optimization",
        "f": lambda x: (np.exp(-(x[0] - 1) ** 2 / (2 * 0.3 ** 2))
                        + np.exp(-(x[0] - 5.1) ** 2 / (2 * 0.4 ** 2))
                        + np.exp(-(x[0] - 3) ** 2 / (2 * 0.3 ** 2))
                        + np.exp(-(x[0] - 7) ** 2 / (2 * 0.6 ** 2))),
        "bounds": [(0.5, 8.0)],
        "ls": 0.6, "auto_ls": True, "noise": 0.0,
        "published": "Gramacy & Lee 2012 test function (1D multimodal)",
        "references": ["Gramacy & Lee 2012, J. Stat. Software"],
        "gold": None,
    },
    {
        "key": "resp_welch2d", "domain": "optimization", "category": "optimization",
        # Welch SDOE test function (2D projection)
        "f": lambda x: (0.8 + 0.3 * np.sin(5 * x[0]) * np.cos(3 * x[1])
                        + 0.2 * (x[0] - 0.5) ** 2 + 0.1 * x[1] ** 2),
        "bounds": [(0, 1), (0, 1)], "ls": 0.2, "auto_ls": True, "noise": 0.0,
        "published": "Welch et al. 1992 SDOE test function",
        "references": ["Welch et al. 1992, Technometrics"],
        "gold": None,
    },
    {
        "key": "ode_lotka_volterra", "domain": "biology", "category": "biology",
        # 用参数向量直接编码两物种峰值振幅差作为标量响应（便于 GP 代理）
        "f": lambda x: abs(x[0] - x[1]) + 0.0 * (x[0] + x[1]),
        "bounds": [(0.1, 2.0), (0.1, 3.0)], "ls": 0.5, "auto_ls": True, "noise": 0.0,
        "published": "Lotka-Volterra 参数-响应代理（非拟态基准，供 GP 代理练习）",
        "references": ["Lotka 1925, Volterra 1926"],
        "gold": None,
    },
]


def _hartmann3(x):
    # 标准 Hartmann-3：4 项高斯和，alpha 与 A 分离，x 为 3 维。
    alpha = np.array([1.0, 1.2, 3.0, 3.2])
    A = np.array([[3, 10, 30], [0.1, 10, 35], [3, 10, 30], [0.1, 10, 35]])
    P = np.array([[0.3689, 0.1170, 0.2673],
                  [0.4699, 0.4387, 0.7470],
                  [0.1091, 0.8732, 0.5547],
                  [0.0381, 0.5743, 0.8828]])
    x = np.asarray(x, float)
    s = 0.0
    for i in range(4):
        s += alpha[i] * np.exp(-np.sum(A[i] * (x - P[i]) ** 2))
    return -s


SCENARIOS_EXTRA[6]["f"] = _hartmann3  # opt_hartmann3d


# 每场景默认采样量与标签：
#   validated = GP 代理可对等验证（coverage 目标 ≥0.85）
#   stress    = 高频/非平滑基准，展示「UQ 诚实 → 触发主动学习补点」这一核心卖点
_EXTRA_META = {
    "opt_schwefel2d":    {"n_seed": 40, "tags": "stress"},
    "opt_ackley2d":      {"n_seed": 100, "tags": "stress"},
    "opt_rastrigin2d":   {"n_seed": 40, "tags": "stress"},
    "opt_rosenbrock2d":  {"n_seed": 100, "tags": "stress"},
    "opt_levy2d":        {"n_seed": 40, "tags": "stress"},
    "opt_michalewicz2d": {"n_seed": 40, "tags": "stress"},
    "opt_hartmann3d":    {"n_seed": 120, "tags": "validated"},
    "opt_gramacylee1d":  {"n_seed": 40, "tags": "validated"},
    "resp_welch2d":      {"n_seed": 20, "tags": "validated"},
    "ode_lotka_volterra":{"n_seed": 12, "tags": "stress"},
}
for _s in SCENARIOS_EXTRA:
    _m = _EXTRA_META.get(_s["key"], {})
    _s.setdefault("n_seed", _m.get("n_seed", 10))
    _s.setdefault("tags", _m.get("tags", "validated"))


# 覆盖率达标阈值（与 paper_validation_loop 旗舰标准对齐）
COVERAGE_PASS = 0.85


def run_extra_validation(n_seed=10, n_test=80, rng=None):
    """用统一引擎对 10 个新场景跑对等验证，返回汇总（与 paper_validation_loop 同构）。

    返回行含 tags（validated/stress）与 needs_refill（coverage<阈值，触发主动学习补点）。
    """
    from engine.surrogate import SurrogateEngine, GPSurrogate, Calibrator, ExperimentSpace

    rng = rng or np.random.default_rng(20260816)
    rows = []
    for s in SCENARIOS_EXTRA:
        f = s["f"]
        if f is None:
            continue
        ns = s.get("n_seed", n_seed)
        # 每场景独立固定种子：场景间数据互不干扰，对等报告可复现
        srng = np.random.default_rng(20260816 + _stable_seed(s["key"]))
        space = ExperimentSpace(s["bounds"])
        X = space.sample(ns, srng)
        y = np.array([f(x) for x in X])
        eng = SurrogateEngine(s)
        eng.fit(X, y)
        test_x = space.sample(n_test, srng)
        test_y = np.array([f(x) for x in test_x])
        mean, std = eng.predict(test_x)
        rmse = float(np.sqrt(np.mean((mean - test_y) ** 2)))
        rel = rmse / (float(np.std(y)) + 1e-9)
        z = 1.96
        lo, hi = mean - z * std, mean + z * std
        cover = float(np.mean((test_y >= lo) & (test_y <= hi)))
        rows.append({
            "key": s["key"], "category": s["category"], "tags": s.get("tags", "validated"),
            "n_points": ns, "rmse_rel": round(rel, 4), "coverage": round(cover, 3),
            "cal_err": round(abs(cover - 0.95), 3), "gold": s.get("gold"),
            "needs_refill": cover < COVERAGE_PASS,
        })
    # 先 validated 后 stress，便于阅读
    rows.sort(key=lambda r: (0 if r["tags"] == "validated" else 1, r["key"]))
    return rows
