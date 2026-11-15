#!/usr/bin/env python
"""真实数据集接进门禁：用 UCI 真实实验数据验证 SurrogateEngine。

目标：把 engine/real_datasets.py 的 4 个 UCI 数据集（airfoil/wine/yacht/ccpp）
接进验证流程，产出真实数据上的性能基准报告。

这是「真实数据锚定」——从全合成 oracle 到有真实实验证据的关键一步。

用法：
    python scripts/wire_real_datasets.py
"""
from __future__ import annotations

import json
import os
import sys
import time
import numpy as np

os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

DATA = os.path.join(ROOT, "data")

from engine.real_datasets import REGISTRY, load as load_real
from engine.conformal import picp, mpiw, interval_score


def evaluate_real_dataset(name, dataset, train_frac=0.7, seed=42, alpha=0.05):
    """对单个真实数据集做 train/test 划分 + GP 代理拟合 + 区间评估。"""
    from engine.surrogate import SurrogateEngine

    X = np.asarray(dataset["X"], float)
    y = np.asarray(dataset["y"], float)
    n = len(y)

    # 划分
    rng = np.random.default_rng(seed)
    idx = rng.permutation(n)
    n_train = int(n * train_frac)
    train_idx, test_idx = idx[:n_train], idx[n_train:]

    X_train, y_train = X[train_idx], y[train_idx]
    X_test, y_test = X[test_idx], y[test_idx]

    # 构造 domain — bounds 从训练数据推断，ls 用默认值
    bounds = [(float(X_train[:, j].min()), float(X_train[:, j].max()))
              for j in range(X_train.shape[1])]
    domain = {
        "key": f"real_{name}",
        "f": None,  # 不需要
        "train": X_train,
        "test": X_test,
        "dim": X_train.shape[1],
        "bounds": bounds,
        "ls": 0.4,  # GPSurrogate 默认长度尺度
    }

    # 拟合 GP 代理
    eng = SurrogateEngine(domain)
    eng.fit(X_train, y_train, noise_rel=0.03)
    mu, sd = eng.predict(X_test)

    # R²
    ss_res = float(np.sum((y_test - mu)**2))
    ss_tot = float(np.sum((y_test - y_test.mean())**2))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0

    # 区间
    z = 1.96
    lo, hi = mu - z * sd, mu + z * sd
    coverage = picp(y_test, lo, hi)
    y_range = float(np.max(y) - np.min(y)) or 1.0
    nmpiw = mpiw(lo, hi, y_range)
    iscore = interval_score(y_test, lo, hi, alpha)

    return {
        "dataset": name,
        "category": dataset["category"],
        "n_total": n,
        "n_train": n_train,
        "n_test": len(y_test),
        "dim": int(X_train.shape[1]),
        "citation": dataset["citation"],
        "desc": dataset["desc"],
        "r2": round(r2, 4),
        "coverage": round(coverage, 4),
        "nmpiw": round(nmpiw, 4),
        "interval_score": round(iscore, 4),
        "verdict": (
            "PASS" if r2 >= 0.90 and coverage >= 0.80 else
            "MARGINAL" if r2 >= 0.70 and coverage >= 0.70 else
            "REFUTED"
        ),
    }


def main():
    print(f"{'='*60}")
    print(f"真实数据集接进门禁 — UCI 实验数据验证")
    print(f"{'='*60}")

    t0 = time.time()
    results = []

    for name in REGISTRY:
        print(f"\n--- {name} ---")
        try:
            ds = load_real(name)
            X, y = ds["X"], ds["y"]
            print(f"  {ds['desc']}")
            print(f"  Shape: X={X.shape}, y={y.shape}")
            print(f"  y range: [{y.min():.4f}, {y.max():.4f}], std={y.std():.4f}")

            r = evaluate_real_dataset(name, ds)
            results.append(r)

            print(f"  R²={r['r2']:.4f}  coverage={r['coverage']:.4f}  "
                  f"nmpiw={r['nmpiw']:.4f}  verdict={r['verdict']}")
        except Exception as e:
            print(f"  !! {type(e).__name__}: {e}")
            results.append({
                "dataset": name,
                "error": f"{type(e).__name__}: {e}",
                "verdict": "ERROR",
            })

    elapsed = time.time() - t0

    # ── 汇总 ──
    n_ok = sum(1 for r in results if r.get("verdict") in ("PASS", "MARGINAL"))
    n_pass = sum(1 for r in results if r.get("verdict") == "PASS")
    n_marg = sum(1 for r in results if r.get("verdict") == "MARGINAL")
    n_ref = sum(1 for r in results if r.get("verdict") == "REFUTED")
    n_err = sum(1 for r in results if r.get("verdict") == "ERROR")

    r2s = [r["r2"] for r in results if "r2" in r]
    covs = [r["coverage"] for r in results if "coverage" in r]

    payload = {
        "version": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "method": "UCI real experimental data → SurrogateEngine → R² + coverage + interval score",
        "n_datasets": len(results),
        "elapsed_seconds": round(elapsed, 2),
        "summary": {
            "total": len(results),
            "pass": n_pass,
            "marginal": n_marg,
            "refuted": n_ref,
            "error": n_err,
            "mean_r2": round(float(np.mean(r2s)) if r2s else 0, 4),
            "median_r2": round(float(np.median(r2s)) if r2s else 0, 4),
            "mean_coverage": round(float(np.mean(covs)) if covs else 0, 4),
            "median_coverage": round(float(np.median(covs)) if covs else 0, 4),
        },
        "results": results,
        "interpretation": (
            "首次将真实实验数据（UCI 数据集）接进验证门禁。"
            "这是从「全合成 oracle」到「有真实证据锚点」的关键一步。"
            "R² 和覆盖率反映了 GP 代理在真实数据上的泛化能力。"
            "experimental_ground_truth_count 从 0 提升到 "
            f"{sum(1 for r in results if r.get('verdict') in ('PASS', 'MARGINAL'))}。"
        ),
    }

    os.makedirs(DATA, exist_ok=True)
    with open(os.path.join(DATA, "real_dataset_validation.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*60}")
    print(f"汇总: PASS={n_pass}, MARGINAL={n_marg}, REFUTED={n_ref}, ERROR={n_err}")
    if r2s:
        print(f"平均 R²={np.mean(r2s):.4f}, 中位 R²={np.median(r2s):.4f}")
    if covs:
        print(f"平均覆盖率={np.mean(covs):.4f}, 中位覆盖率={np.median(covs):.4f}")
    print(f"耗时: {elapsed:.1f}s")
    print(f"→ data/real_dataset_validation.json")


if __name__ == "__main__":
    sys.exit(main())
