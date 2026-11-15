#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Calibration RL — 让 coverage=0.95 从"经验校准"变"训练目标"。

借鉴点（2026-09-20）
-------------------
- **Laya / Jev 的 RLCD**（Reinforcement Learning for Calibrated Decisions）：
  奖励 = 严格评分规则（log-likelihood / Brier / strictly proper scoring rule）；
  期望奖励最大化**只当**报告的概率 = 真实频率时。"诚实概率"是训练目标，
  不是后处理。Laya 报告 ECE 从 0.466 → 0.081（refit temperature per question
  type）；Jev 用同类方法得到 ECE=0.246（发布材料）。
- **SwarmLabs 现状**：κ 校准是启发式（capped at 40）；coverage=0.95 是
  **约束**（3% 噪声地板 + 单侧下界），不是**训练目标**。

本模块做了什么
-------------
1. 把 ledger 里的 62 个场景抽成特征向量（r² / coverage / κ / cal_err /
   anchor_ok / replication_ok）+ 真实 verdict 标签；
2. 用**线性 softmax 策略** + **随机梯度策略梯度**训练一个 evidence → 4 档
   概率的映射；奖励是**严格评分规则**（log-likelihood），只有当报告概率
   匹配真实 verdict 时最大化；
3. Leave-one-out 交叉验证：每次留一个场景作测试，看预测概率是否合理；
4. 报告 **ECE**（Expected Calibration Error）—— 校准误差是校准训练的硬指标。

设计边界
-------
- **纯 stdlib + numpy**，无 GPU、无 PyTorch、无 TF；
- 数据规模小（62 场景 × 6 特征）；训练 steps=5000 在 1s 内完成；
- 训练数据高度不均衡（当前 62/62 PASS），所以本模块**主要用作"训练框架
  已就位"的证据**，而不是"精度提升"的证据。等有 MARGINAL/REFUTED 场景
  自然出现，训练集会自动变均衡；
- 不修改 gate 判定（hard verdict 保留）；本模块产出**校准元数据**供
  报告/账本/MCP 层读取。

用法
----
    from engine.calibration_rl import train, evaluate
    policy = train()
    ece = policy.expected_calibration_error()
    print(f"ECE={ece:.4f}")
    probs = policy.predict_for_scenario("bio_logistic")

    # 或 CLI：
    #   python engine/calibration_rl.py
    #   python engine/calibration_rl.py --steps 10000 --lr 0.05
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# ---- 类别定义（与 engine/verification_gate.py 保持一致）----
GRADES: Tuple[str, ...] = ("PASS", "MARGINAL", "REFUTED", "UNVALIDATED")
N_CLASSES: int = len(GRADES)

# ---- 特征定义（顺序即索引）----
FEAT_NAMES: Tuple[str, ...] = (
    "r2",
    "coverage",
    "kappa",
    "cal_err",
    "anchor_ok",
    "replication_ok",
)
N_FEATS: int = len(FEAT_NAMES)


def _load_ledger(path: Optional[str] = None) -> dict:
    """加载 gate_ledger.json（两个常见路径之一）。"""
    cands = [path] if path else [
        os.path.join(ROOT, "frontend_fixed", "reports", "gate_ledger.json"),
        os.path.join(ROOT, "data", "gate_ledger.json"),
    ]
    for p in cands:
        if p and os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return json.load(f)
    raise FileNotFoundError(f"gate ledger not found in {cands}")


def _evidence_vector(entry: dict) -> np.ndarray:
    """把 ledger entry 抽成特征向量。"""
    u = entry.get("uncertainty") or {}
    r2 = float(u.get("r2", 0) or 0)
    coverage = float(u.get("coverage", 0) or 0)
    kappa = float(u.get("calibration_scale_kappa", 1) or 1)
    cal_err = float(u.get("cal_err", 0.1) or 0.1)

    # anchor 加分：ledger entry 里若已带 anchor_ref 类字段则视为有锚
    anchor_ok = 1.0 if _has_anchor_field(entry) else 0.0
    replication_ok = 1.0 if entry.get("replication_passed", False) else 0.0

    return np.array(
        [r2, coverage, kappa, cal_err, anchor_ok, replication_ok],
        dtype=np.float64,
    )


def _has_anchor_field(entry: dict) -> bool:
    for k in ("anchor", "anchor_ref", "wet_lab_anchor", "citation_anchor"):
        v = entry.get(k)
        if v and (not isinstance(v, list) or any(v)):
            return True
    return False


def _true_label(entry: dict) -> int:
    """把 verdict 映射为类别索引（未知 verdict → UNVALIDATED）。"""
    v = entry.get("verdict", "UNVALIDATED")
    if v in GRADES:
        return GRADES.index(v)
    # ERROR 归到 UNVALIDATED（与 verification_gate 的 UNVALIDATED 语义一致）
    return GRADES.index("UNVALIDATED")


def _softmax(z: np.ndarray) -> np.ndarray:
    z = z - z.max()
    e = np.exp(z)
    return e / e.sum()


@dataclass
class CalibrationPolicy:
    """线性 softmax 策略 + 特征归一化参数。"""

    w: np.ndarray = field(default_factory=lambda: np.zeros((N_FEATS + 1, N_CLASSES)))
    mu: np.ndarray = field(default_factory=lambda: np.zeros(N_FEATS))
    sd: np.ndarray = field(default_factory=lambda: np.ones(N_FEATS))
    fit_stats: Dict[str, float] = field(default_factory=dict)
    n_scenarios: int = 0

    # ---- 训练 ----

    def fit(
        self,
        scenarios: Dict[str, dict],
        steps: int = 5000,
        lr: float = 0.05,
        seed: int = 0,
        verbose: bool = False,
    ) -> "CalibrationPolicy":
        """策略梯度训练。奖励 = log-likelihood（严格评分规则）。

        随机采样一个样本 → 前向算概率 → 反向算梯度 → 更新权重。
        严格评分规则保证：期望奖励最大化**只当**预测概率 = 真实类别频率。
        """
        keys = list(scenarios.keys())
        n = len(keys)
        if n == 0:
            raise ValueError("no scenarios to train on")

        X = np.stack([_evidence_vector(scenarios[k]) for k in keys])
        y = np.array([_true_label(scenarios[k]) for k in keys])

        # 特征归一化（避免量纲不均影响梯度）
        self.mu = X.mean(axis=0)
        sd = X.std(axis=0)
        sd[sd < 1e-6] = 1.0  # 常数特征用 sd=1，避免除零
        self.sd = sd
        X_norm = (X - self.mu) / self.sd
        X_norm = np.hstack([X_norm, np.ones((X_norm.shape[0], 1))])  # 加偏置

        self.n_scenarios = n
        rng = np.random.default_rng(seed)

        # 记录训练损失（log-likelihood，越大越好）
        losses: List[float] = []

        for step in range(steps):
            i = int(rng.integers(0, n))
            x = X_norm[i : i + 1]
            z = x @ self.w  # (1, n_class)
            p = _softmax(z[0])  # (n_class,)
            target = int(y[i])

            # log-likelihood 梯度：∂/∂w log p[y] = (onehot[y] - p) @ x
            onehot = np.zeros(N_CLASSES)
            onehot[target] = 1.0
            grad = (onehot - p).reshape(1, -1)
            dw = x.T @ grad  # (n_feat+1, n_class)
            self.w += lr * dw

            if verbose or step % max(1, steps // 10) == 0:
                log_p = float(math.log(max(p[target], 1e-12)))
                losses.append(log_p)
                if verbose:
                    print(f"  step {step}: log_p={log_p:.4f}")

        # 训练结束后跑一遍全量 log-likelihood
        Z = X_norm @ self.w
        P = np.stack([_softmax(z) for z in Z])
        mean_log_p = float(np.mean([math.log(max(P[i, y[i]], 1e-12)) for i in range(n)]))
        self.fit_stats = {
            "n_scenarios": n,
            "n_steps": steps,
            "lr": lr,
            "mean_log_likelihood": mean_log_p,
            "label_distribution": _label_dist(y),
        }
        return self

    # ---- 推理 ----

    def predict(self, evidence: np.ndarray) -> np.ndarray:
        """对单个证据向量返回 4 档概率分布。"""
        x = np.asarray(evidence, dtype=np.float64).reshape(1, -1)
        x = (x - self.mu) / self.sd
        x = np.hstack([x, np.ones((x.shape[0], 1))])
        z = x @ self.w
        return _softmax(z[0])

    def predict_for_scenario(self, entry: dict) -> np.ndarray:
        """对 ledger entry 返回 4 档概率分布。"""
        return self.predict(_evidence_vector(entry))

    def predict_probabilities(self, entry: dict) -> Dict[str, float]:
        """对 ledger entry 返回概率字典（键为 GRADES）。"""
        p = self.predict_for_scenario(entry)
        return {g: round(float(p[i]), 4) for i, g in enumerate(GRADES)}

    # ---- 校准评估 ----

    def expected_calibration_error(
        self,
        scenarios: Dict[str, dict],
        n_bins: int = 10,
    ) -> float:
        """ECE（Expected Calibration Error）。

        ECE = Σ_b (n_b / N) * |acc_b - conf_b|
          b = 概率分箱（0-0.1, 0.1-0.2, ..., 0.9-1.0）
          acc_b = 该箱内预测正确（top-1 命中真实标签）的比例
          conf_b = 该箱内 top-1 概率的平均值

        ECE=0 表示完美校准；ECE 越小越可信。参考值：
          - Laya zero-shot 基线：ECE=0.466
          - Laya refit temperature 后：ECE=0.081
          - Jev 1.13 发布材料：ECE=0.246
        """
        if self.w is None or self.n_scenarios == 0:
            return float("nan")

        keys = list(scenarios.keys())
        n = len(keys)
        if n == 0:
            return float("nan")

        bins: List[Tuple[int, List[Tuple[float, int]]]] = [(i, []) for i in range(n_bins)]

        for k in keys:
            entry = scenarios[k]
            p = self.predict_for_scenario(entry)
            target = _true_label(entry)
            top1 = int(np.argmax(p))
            top1_prob = float(p[top1])
            correct = 1 if top1 == target else 0

            # 分箱
            b = min(n_bins - 1, int(top1_prob * n_bins))
            bins[b][1].append((top1_prob, correct))

        ece = 0.0
        for _, entries in bins:
            if not entries:
                continue
            probs = [e[0] for e in entries]
            corrects = [e[1] for e in entries]
            n_b = len(entries)
            acc_b = sum(corrects) / n_b
            conf_b = sum(probs) / n_b
            ece += (n_b / n) * abs(acc_b - conf_b)
        return ece

    def leave_one_out_log_likelihood(self, scenarios: Dict[str, dict]) -> List[Dict]:
        """Leave-one-out 交叉验证：每次留一个场景，检查预测概率。

        对每个 held-out 场景，用剩下的 61 个训练策略，看 held-out 的
        top-1 是否命中真实 verdict。返回每场景的 {key, hard_verdict,
        predicted_top1, hit, log_likelihood}。
        """
        keys = list(scenarios.keys())
        n = len(keys)
        if n < 2:
            raise ValueError("need at least 2 scenarios for LOO")

        results: List[Dict] = []
        for held_out_idx, held_key in enumerate(keys):
            train_keys = [k for k in keys if k != held_key]
            train_set = {k: scenarios[k] for k in train_keys}
            # 每个 held-out 单独训练（steps 少一些，加速）
            sub = CalibrationPolicy()
            sub.fit(train_set, steps=500, lr=0.05, seed=0)
            p = sub.predict_for_scenario(scenarios[held_key])
            top1 = int(np.argmax(p))
            target = _true_label(scenarios[held_key])
            hit = top1 == target
            log_p = float(math.log(max(p[target], 1e-12)))
            results.append(
                {
                    "key": held_key,
                    "hard_verdict": scenarios[held_key].get("verdict"),
                    "predicted_top1": GRADES[top1],
                    "true_label": GRADES[target],
                    "hit": bool(hit),
                    "top1_prob": round(float(p[top1]), 4),
                    "log_likelihood": round(log_p, 4),
                    "probabilities": {g: round(float(p[i]), 4) for i, g in enumerate(GRADES)},
                }
            )
        return results


def _label_dist(y: np.ndarray) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for g in GRADES:
        out[g] = int((y == GRADES.index(g)).sum())
    return out


def train(
    ledger_path: Optional[str] = None,
    steps: int = 5000,
    lr: float = 0.05,
    seed: int = 0,
    verbose: bool = False,
) -> CalibrationPolicy:
    """便捷入口：加载 ledger 并训练策略。"""
    led = _load_ledger(ledger_path)
    scenarios = led.get("scenarios", {})
    policy = CalibrationPolicy()
    return policy.fit(scenarios, steps=steps, lr=lr, seed=seed, verbose=verbose)


def evaluate(policy: CalibrationPolicy, ledger_path: Optional[str] = None) -> Dict:
    """对训练好的策略做全面评估：ECE + LOO。"""
    led = _load_ledger(ledger_path)
    scenarios = led.get("scenarios", {})
    ece = policy.expected_calibration_error(scenarios)
    loo = policy.leave_one_out_log_likelihood(scenarios)
    loo_hits = sum(1 for r in loo if r["hit"])
    return {
        "ece": round(float(ece), 4),
        "loo": {
            "n": len(loo),
            "hits": loo_hits,
            "accuracy": round(loo_hits / max(1, len(loo)), 4),
            "mean_log_likelihood": round(
                float(np.mean([r["log_likelihood"] for r in loo])), 4
            ),
            "by_verdict": _group_by_verdict(loo),
        },
        "fit_stats": policy.fit_stats,
    }


def _group_by_verdict(loo: List[Dict]) -> Dict[str, Dict]:
    out: Dict[str, Dict] = {}
    for r in loo:
        v = r["hard_verdict"]
        if v not in out:
            out[v] = {"n": 0, "hits": 0, "mean_top1_prob": 0.0}
        out[v]["n"] += 1
        out[v]["hits"] += 1 if r["hit"] else 0
    for v, d in out.items():
        d["hits"] = d["hits"]
        d["accuracy"] = round(d["hits"] / d["n"], 4)
    return out


# ---- 便捷 CLI ----
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Calibration RL — RLCD-style policy training")
    parser.add_argument("--ledger", default=None, help="Path to gate_ledger.json")
    parser.add_argument("--steps", type=int, default=5000)
    parser.add_argument("--lr", type=float, default=0.05)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--skip-loo", action="store_true", help="Skip LOO cross-validation")
    args = parser.parse_args()

    print(f"Training calibration policy: steps={args.steps} lr={args.lr} seed={args.seed}")
    p = train(ledger_path=args.ledger, steps=args.steps, lr=args.lr,
              seed=args.seed, verbose=args.verbose)
    print(f"fit_stats: {json.dumps(p.fit_stats, ensure_ascii=False, indent=2)}")

    ece = p.expected_calibration_error(_load_ledger(args.ledger)["scenarios"])
    print(f"ECE (Expected Calibration Error): {ece:.4f}")
    print(f"  Ref: Laya zero-shot=0.466 · Laya refit=0.081 · Jev=0.246")

    if not args.skip_loo:
        print("\nLeave-one-out cross-validation:")
        loo = p.leave_one_out_log_likelihood(_load_ledger(args.ledger)["scenarios"])
        hits = sum(1 for r in loo if r["hit"])
        print(f"  LOO accuracy: {hits}/{len(loo)} = {hits/len(loo):.4f}")
        mean_ll = float(np.mean([r["log_likelihood"] for r in loo]))
        print(f"  LOO mean log-likelihood: {mean_ll:.4f}")
