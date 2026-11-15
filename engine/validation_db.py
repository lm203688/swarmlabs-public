"""论文验证结构化数据库 (Paper Validation Results DB)。

每条记录包含：
  - paper_id: 唯一标识（格式: author_year_key）
  - benchmark_key: 对应的引擎场景 key
  - model_type: 使用的动力学模型
  - predicted[]: 预测值数组
  - actual[]: 论文实测值数组
  - error_metrics: {rmse, mae, mape, r_squared, coverage_95}
  - status: PASS / MARGINAL / FAIL
  - timestamp: ISO datetime

对标：Galaxy benchmark pipeline / Benchling validation
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from engine.paper_validation import run_paper_benchmark, BENCHMARKS, run_closed_loop_test


def run_all_benchmarks_and_export() -> str:
    """运行所有论文基准测试 + 端到端闭环验证，结果写入结构化 JSON"""
    results = []

    print("=" * 70)
    print("论文验证结构化数据库 — 生成中")
    print("=" * 70)

    for bm in BENCHMARKS:
        print(f"  正在验证: {bm['key']} ...")
        r = run_paper_benchmark(bm)
        record = {
            "paper_id": f'{r.get("key", "?")}',
            "benchmark_key": r.get("key", "?"),
            "paper_title": r.get("title", ""),
            "paper_citation": r.get("paper", ""),
            "domain": r.get("domain", ""),
            "model_type": bm.get("key", "").replace("paper_", ""),
            "n_points": r.get("n_points", 0),
            "unit": r.get("unit", ""),
            "predicted": r.get("predicted", []),
            "actual": r.get("actual", []),
            "error_metrics": {
                "rmse": round(r.get("rmse", 0), 6),
                "mae": round(r.get("mae", 0), 6),
                "mape_pct": round(r.get("mape", 0), 4),
                "r_squared": round(r.get("correlation", 0) ** 2, 6),
                "coverage_95": round(r.get("coverage", 0), 4),
            },
            "status": r.get("status", "FAIL"),
            "timestamp": datetime.now().isoformat(),
        }
        results.append(record)
        status_str = "PASS" if r.get("status") == "PASS" else ("MARGINAL" if r.get("status") == "MARGINAL" else "FAIL")
        print(f"    {status_str}  r={r.get('correlation',0):.3f}  RMSE={r.get('rmse',0):.4f}  "
              f"MAPE={r.get('mape',0):.2f}%")

    # 端到端闭环验证
    print("\n  正在验证端到端闭环 ...")
    loop_result = run_closed_loop_test(seed=42)
    loop_record = {
        "paper_id": "closed_loop_validation",
        "benchmark_key": "closed_loop",
        "paper_title": "端到端闭环验证（GP代理→预测→UQ→主动学习）",
        "n_train": loop_result.get("n_train", 0),
        "n_test": loop_result.get("n_test", 0),
        "n_active": loop_result.get("n_active", 0),
        "error_metrics": {
            "rmse_before": round(loop_result.get("rmse_before", 0), 6),
            "rmse_after": round(loop_result.get("rmse_after", 0), 6),
            "rmse_improvement_pct": round(loop_result.get("rmse_improvement_pct", 0), 2),
            "uq_coverage_before": round(loop_result.get("uq_coverage_before", 0), 4),
        },
        "status": "PASS" if loop_result.get("converged") else "MARGINAL",
        "timestamp": datetime.now().isoformat(),
    }
    results.append(loop_record)
    print(f"    RMSE: {loop_result.get('rmse_before',0):.5f} → {loop_result.get('rmse_after',0):.5f} "
          f"({loop_result.get('rmse_improvement_pct',0):.1f}% 改善)")

    # 写入 JSON
    db = {
        "database_info": {
            "name": "Paper Validation Results DB",
            "version": "1.0.0",
            "engine": "SwarmLabs Microbiology Engine",
            "description": "结构化论文实验数据验证结果，覆盖 Monod/Andrews/Contois/Haldane/Pirt/Luedeking-Piret/Tessier/Baranyi/Fed-batch/Chemostat/Thiele/Antibiotic/Thermal 共 13 种动力学模型 + 端到端闭环",
        },
        "stats": {
            "total_benchmarks": len(results),
            "pass": sum(1 for r in results if r["status"] == "PASS"),
            "marginal": sum(1 for r in results if r["status"] == "MARGINAL"),
            "fail": sum(1 for r in results if r["status"] == "FAIL"),
            "total_data_points": sum(r.get("n_points", 0) for r in results if r["paper_id"] != "closed_loop_validation"),
        },
        "results": results,
    }

    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "validation_results.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

    print(f"\n  验证结果已写入: {path}")
    print(f"  {db['stats']['pass']} PASS / {db['stats']['marginal']} MARGINAL / {db['stats']['fail']} FAIL")
    print(f"  总数据点: {db['stats']['total_data_points']}")
    print("=" * 70)

    return path


if __name__ == "__main__":
    run_all_benchmarks_and_export()
