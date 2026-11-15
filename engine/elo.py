"""Elo 候选锦标赛 —— 用对等度(误差)给多个假设/模型排名，替代 LLM 主观排序。

玩法：每个候选是一个「模型」（GP 代理 / 符号律 / PINN）。在测试网格上用
Calibrator 的 rmse 作为实力代理——误差越低越强。两两对弈按 Elo 公式更新积分。

诚实定位：这是 Co-Scientist Elo 锦标赛的**数值化、可复现**版本（无 LLM 随机性）。
LLM 负责「生成候选」，Elo+对等度负责「淘汰候选」，分工清晰、结论可证伪。
"""

from __future__ import annotations

import numpy as np


class EloTournament:
    """对一组候选按「实力」进行 Elo 排名。

    strength 语义：值越小越强（如 rmse、相对误差）。若提供 strength，则按
    两两比较（强者胜）初始化并收敛 Elo 积分；否则手动 update(winner, loser)。
    """

    def __init__(self, base_rating=1500.0, k=32.0):
        self.base = float(base_rating)
        self.k = float(k)
        self.ratings = {}
        self.history = []

    def add(self, name):
        if name not in self.ratings:
            self.ratings[name] = self.base

    def expected(self, a, b):
        return 1.0 / (1.0 + 10 ** ((self.ratings[b] - self.ratings[a]) / 400.0))

    def update(self, winner, loser):
        for n in (winner, loser):
            self.add(n)
        ea = self.expected(winner, loser)
        eb = self.expected(loser, winner)
        self.ratings[winner] += self.k * (1.0 - ea)
        self.ratings[loser] += self.k * (0.0 - eb)
        self.history.append((winner, loser))

    def from_strengths(self, names, strengths, n_rounds=3):
        """用 strength（越低越强）构造对弈并跑 n_rounds 轮。"""
        for n in names:
            self.add(n)
        arr = list(zip(names, strengths))
        rng = np.random.default_rng(7)
        for _ in range(n_rounds):
            order = list(range(len(arr)))
            rng.shuffle(order)
            for i in range(0, len(order) - 1, 2):
                a, b = order[i], order[i + 1]
                na, sa = arr[a]
                nb, sb = arr[b]
                if sa < sb:
                    self.update(na, nb)
                else:
                    self.update(nb, na)
        return self

    def rank(self):
        return sorted(self.ratings.items(), key=lambda kv: -kv[1])

    def report(self):
        rows = self.rank()
        return {
            "champion": rows[0][0],
            "champion_rating": round(rows[0][1], 1),
            "ranking": [(n, round(r, 1)) for n, r in rows],
            "n_matches": len(self.history),
        }
