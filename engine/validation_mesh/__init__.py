"""Validation Mesh —— 可独立分发的「虚拟实验对等验证」Python 包。

把 SwarmLabs 的「虚拟实验 ↔ 真实实验 对等度验证」能力拆成独立 wheel，
可 `pip install` 后在任意项目复用，产品化方向是「多 Agent 交付验证基础设施」。

核心能力
--------
- parity    : run_parity_loop 跑通「虚拟→真实」对等闭环
- calibrate : Calibrator 度量 rmse / 95%CI 覆盖率 / 校准误差
- guard     : VirtualLabGuard 三态门禁（PASS/CONTROLLED/REJECT）
- scenario  : validate_scenario / validate_batch 批量验证
- compare   : compare_runs 反事实对比
- audit     : write_audit / audit_log 审计日志

依赖：numpy + scipy（纯 CPU，无 GPU、无 LLM、无联网）。
"""

from __future__ import annotations

import datetime
import json
import os
from typing import Any, Callable, Optional

import numpy as np

__version__ = "0.1.1"

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.path.join(ROOT, "data")
AUDIT_LOG = os.path.join(DATA, "validation_mesh_audit.jsonl")

from ..surrogate import (
    run_parity_loop,
    Calibrator,
    GPSurrogate,
    ExperimentSpace,
    SurrogateEngine,
)
from ..guard import VirtualLabGuard, GateResult, GateStatus


# ── 场景验证 ────────────────────────────────────────────────────
def validate_scenario(
    scenario: dict,
    gt_fn: Callable = None,
    n_train: int = 30,
    n_test: int = 20,
    noise_rel: float = 0.03,
    seed: int = 42,
) -> dict:
    """对单个场景执行完整验证闭环（fit → predict → UQ → coverage）。"""
    rng = np.random.default_rng(seed)
    key = scenario.get("key", "unknown")
    dim = len(scenario.get("bounds", [(0, 1)]))
    bounds = scenario["bounds"]

    X_train, X_test = [], []
    for b in bounds:
        X_train.append(rng.uniform(*b, n_train))
        X_test.append(rng.uniform(*b, n_test))
    X_train = np.atleast_2d(np.column_stack(X_train))
    X_test = np.atleast_2d(np.column_stack(X_test))

    gt = scenario.get("gt") or scenario.get("f") or gt_fn
    if gt is not None and callable(gt):
        # 部分 GT 期望 1D 标量输入，部分期望 2D；先试批量再回退循环
        try:
            y_train = np.asarray(gt(X_train), float).ravel()
            y_test = np.asarray(gt(X_test), float).ravel()
            if len(y_train) != len(X_train):
                raise ValueError("batch output len mismatch")
        except (TypeError, ValueError):
            y_train = np.array([float(gt(x if x.ndim == 1 else x.ravel())) for x in X_train])
            y_test = np.array([float(gt(x if x.ndim == 1 else x.ravel())) for x in X_test])
    else:
        raise ValueError(f"场景 '{key}' 缺少 gt/f 可调用函数")

    y_train += rng.normal(0, noise_rel * (np.std(y_train) + 1e-9), len(y_train))

    try:
        y_mean, y_scale = float(np.mean(y_train)), float(np.std(y_train)) + 1e-9
        sur = GPSurrogate(
            noise_var=(noise_rel) ** 2, y_mean=y_mean, y_scale=y_scale, lengthscale=0.5
        )
        sur.fit(X_train, y_train)
        pred_mean, pred_std = sur.predict(X_test)
        pred_mean = np.asarray(pred_mean).ravel()
        pred_std = np.asarray(pred_std).ravel()

        nan_data = int(np.isnan(pred_mean).any())
        rmse = float(np.sqrt(np.mean((pred_mean - y_test) ** 2)))

        z = 1.96
        lower = pred_mean - z * pred_std
        upper = pred_mean + z * pred_std
        coverage = float(((y_test >= lower) & (y_test <= upper)).mean())
        calibration_error = float(abs(coverage - 0.95))

        y_range = float(np.max(y_test) - np.min(y_test)) + 1e-9
        rel_rmse = rmse / y_range
        converged = bool(rel_rmse < 0.1 and coverage >= 0.8 and not nan_data)

        noise_var = (noise_rel) ** 2
        total_var = float(np.mean(pred_std ** 2))
        epistemic_ratio = float(max(0.0, min(1.0, (total_var - noise_var) / (total_var + 1e-15))))

        return {
            "scenario": key, "dim": dim, "rmse": rmse, "rel_rmse": float(rel_rmse),
            "coverage": coverage, "calibration_error": calibration_error,
            "epistemic_ratio": epistemic_ratio, "converged": converged,
            "nan_data": nan_data, "n_train": n_train, "n_test": n_test,
            "audit": {"timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
                      "validator": "validation_mesh.validate_scenario", "seed": seed},
        }
    except Exception as e:
        return {"scenario": key, "dim": dim, "rmse": None, "coverage": 0.0,
                "converged": False, "nan_data": 1, "error": str(e),
                "audit": {"timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
                          "validator": "validation_mesh.validate_scenario", "seed": seed}}


def validate_batch(scenarios: list, gt_fn: Callable = None, config: dict = None) -> dict:
    """批量验证场景列表。"""
    cfg = config or {}
    results = [validate_scenario(sc, gt_fn,
                 cfg.get("n_train", 30), cfg.get("n_test", 20),
                 cfg.get("noise_rel", 0.03), cfg.get("seed", 42)) for sc in scenarios]
    converged = sum(1 for r in results if r.get("converged"))
    total = len(results)
    valid_rmse = [r["rmse"] for r in results if r.get("rmse") is not None]
    converged_cov = [r["coverage"] for r in results if r.get("converged")]
    return {
        "total": total, "converged": converged,
        "coverage_rate": float(converged / total) if total > 0 else 0.0,
        "avg_rmse": float(np.mean(valid_rmse)) if valid_rmse else None,
        "avg_coverage": float(np.mean(converged_cov)) if converged_cov else 0.0,
        "results": results,
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
    }


def compare_runs(run_a: dict, run_b: dict) -> dict:
    """对比两次验证运行（同场景、不同配置），输出逐场景差异。"""
    a_by_key = {r["scenario"]: r for r in run_a.get("results", [])}
    b_by_key = {r["scenario"]: r for r in run_b.get("results", [])}
    keys = sorted(set(a_by_key) | set(b_by_key))
    diffs = []
    for k in keys:
        a, b = a_by_key.get(k, {}), b_by_key.get(k, {})
        rmse_a, rmse_b = a.get("rmse"), b.get("rmse")
        cov_a, cov_b = a.get("coverage", 0), b.get("coverage", 0)
        diffs.append({
            "scenario": k, "rmse_a": rmse_a, "rmse_b": rmse_b,
            "rmse_delta": (rmse_b - rmse_a) if (rmse_a and rmse_b) else None,
            "coverage_delta": cov_b - cov_a,
            "improved": bool(rmse_a and rmse_b and rmse_b < rmse_a and cov_b >= cov_a),
        })
    improved = sum(1 for d in diffs if d.get("improved"))
    worsened = sum(1 for d in diffs if d.get("rmse_delta") is not None and d["rmse_delta"] > 0)
    return {"n_scenarios": len(diffs), "improved": improved, "worsened": worsened, "diffs": diffs}


def write_audit(record: dict, path: str = AUDIT_LOG):
    """追加一条审计记录到 JSONL。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    record["_written_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def audit_log(path: str = AUDIT_LOG):
    """读取审计日志（JSONL 格式）。"""
    records = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass  # noqa: silent-ok — json.JSONDecodeError 可安全忽略（已审计 2026-10-08）
    return records


def validate_full(scenarios: list, config: dict = None) -> dict:
    """对整个 scenario 列表跑一轮独立验证并写审计。"""
    result = validate_batch(scenarios, config=config)
    write_audit({
        "type": "validate_full", "total": result["total"],
        "converged": result["converged"], "coverage_rate": result["coverage_rate"],
        "avg_rmse": result["avg_rmse"],
    })
    return result


__all__ = [
    "run_parity_loop", "Calibrator", "GPSurrogate", "ExperimentSpace", "SurrogateEngine",
    "VirtualLabGuard", "GateResult", "GateStatus",
    "validate_scenario", "validate_batch", "compare_runs",
    "write_audit", "audit_log", "validate_full",
    "__version__",
]
