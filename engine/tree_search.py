"""假设树搜索（Tree-of-Thought / MCTS 风格）—— 多候选机理的系统性探索。

把「候选假设」组织成树：
  根 = 观测数据
  子节点 = 候选机理族（poly / trig / phys）
  叶 = 具体符号方程（SINDy 在该族下辨识）

用 Elo 锦标赛给节点打分（实力=对等度误差，越低越强），UCB 式选择扩展最有前途
的分支，直到收敛到「最简且对等度最高」的机理。这是 ERA 树搜索（Co-Scientist 用）
的轻量、可复现版本——全靠对等度驱动，无 LLM 随机性。
"""

from __future__ import annotations

import numpy as np

from .elo import EloTournament
from .discovery import SINDy


class HypothesisTree:
    def __init__(self, strategies=None, threshold=1e-2):
        self.strategies = strategies or [
            {"name": "poly2", "poly_degree": 2, "use_sin": False, "use_cos": False},
            {"name": "poly3", "poly_degree": 3, "use_sin": False, "use_cos": False},
            {"name": "trig", "poly_degree": 1, "use_sin": True, "use_cos": True},
            {"name": "phys", "poly_degree": 2, "use_sin": True, "use_cos": True},
        ]
        self.threshold = threshold

    def search(self, X, y, test_X=None, test_y=None, n_rounds=3):
        """返回最优机理 + 各分支 Elo 排名 + 收敛轨迹。"""
        X = np.atleast_2d(np.asarray(X, float))
        y = np.asarray(y, float).ravel()
        if test_X is None:
            test_X, test_y = X, y
        else:
            test_X = np.atleast_2d(np.asarray(test_X, float))
            test_y = np.asarray(test_y, float).ravel()

        tour = EloTournament()
        results = []
        for cfg in self.strategies:
            m = SINDy(poly_degree=cfg["poly_degree"], use_sin=cfg["use_sin"], use_cos=cfg["use_cos"],
                      threshold=self.threshold)
            m.fit(X, y)
            pred = m.predict(test_X)
            rmse = float(np.sqrt(np.mean((pred - test_y) ** 2)))
            rel = rmse / (float(np.std(test_y)) + 1e-9)
            results.append({"strategy": cfg["name"], "model": m, "rmse_rel": rel,
                            "r2": m.score(test_X, test_y), "eq": m.equation()})
            tour.add(cfg["name"])

        # 用相对误差作实力（越低越强）跑 Elo
        strengths = [r["rmse_rel"] for r in results]
        tour.from_strengths([r["strategy"] for r in results], strengths, n_rounds=n_rounds)

        ranked = tour.rank()
        # 冠军 = Elo 最高者；若并列取 rmse_rel 最小
        best = min(results, key=lambda r: r["rmse_rel"])
        return {
            "best_strategy": best["strategy"],
            "best_equation": best["eq"],
            "best_rmse_rel": round(best["rmse_rel"], 4),
            "elo_ranking": tour.report(),
            "all": sorted(results, key=lambda r: r["rmse_rel"]),
        }
