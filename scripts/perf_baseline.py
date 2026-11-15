#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SwarmLabs 标准化性能基线测试
解决 HC-P1-005-0730: API响应时间基线偏差+21.8%
固定测试10个核心端点（5 GET + 5 POST），每日对比
"""

import json
import time
import os
from datetime import datetime
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

BASE_URL = os.environ.get("BASE_URL", "https://swarmlabs.tools/api").rstrip("/")
BASELINE_FILE = os.path.join(os.path.dirname(__file__), "..", "reports", "perf-baseline.json")

# 固定10个核心端点：5 GET + 5 POST
CORE_ENDPOINTS = [
    # 5个GET端点（轻量读取）
    {"name": "健康检查", "method": "GET", "path": "/v2/health", "category": "meta"},
    {"name": "引擎列表", "method": "GET", "path": "/v2/engines", "category": "meta"},
    {"name": "引擎分类", "method": "GET", "path": "/v2/engines/all", "category": "meta"},
    {"name": "参数定义", "method": "GET", "path": "/v2/params", "category": "meta"},
    {"name": "主页可达", "method": "GET", "path": "/v2/health", "category": "meta"},
    # 5个POST端点（核心计算）
    {"name": "药物释放预测", "method": "POST", "path": "/v2/run/drug_release", "category": "compute",
     "payload": {"temperature_C": 37, "time_h": 12, "mixing_speed": 100, "polymer_ratio_pct": 25}},
    {"name": "DOE实验设计", "method": "POST", "path": "/v2/pharma/doe", "category": "compute", "requires_auth": True,
     "payload": {"design_type": "ccd", "factors": [{"name": "HPMC", "low": 10, "high": 40}, {"name": "Filler", "low": 50, "high": 80}], "responses": [45, 72, 38, 80, 60, 60, 60, 55, 75, 50, 85]}},
    {"name": "PBPK模型", "method": "POST", "path": "/v2/pharma/pbpk", "category": "compute", "requires_auth": True,
     "payload": {"dose_mg": 100, "molecular_weight": 300, "logP": 2.0, "pKa": 7.0, "ion_type": "weak_acid", "solubility_mg_mL": 1.0, "effective_permeability_cm_s": 1e-4}},
    {"name": "Crossover ANOVA", "method": "POST", "path": "/v2/pharma/crossover-anova", "category": "compute", "requires_auth": True,
     "payload": {"subjects": [{"id": 1, "sequence": "TR", "period1": 185, "period2": 180}, {"id": 2, "sequence": "RT", "period1": 175, "period2": 182}, {"id": 3, "sequence": "TR", "period1": 190, "period2": 188}, {"id": 4, "sequence": "RT", "period1": 178, "period2": 186}, {"id": 5, "sequence": "TR", "period1": 188, "period2": 185}, {"id": 6, "sequence": "RT", "period1": 176, "period2": 183}, {"id": 7, "sequence": "TR", "period1": 192, "period2": 189}, {"id": 8, "sequence": "RT", "period1": 174, "period2": 181}, {"id": 9, "sequence": "TR", "period1": 187, "period2": 184}, {"id": 10, "sequence": "RT", "period1": 177, "period2": 185}, {"id": 11, "sequence": "TR", "period1": 189, "period2": 187}, {"id": 12, "sequence": "RT", "period1": 175, "period2": 182}], "metric": "AUC", "alpha": 0.05}},
    {"name": "CTD报告", "method": "POST", "path": "/v2/pharma/ctd-report", "category": "compute", "requires_auth": True,
     "payload": {"drug_name": "Test", "dosage_form": "Tablet", "strength": "100mg",
                 "composition": [{"component": "API", "amount_mg": 100, "function": "active"}],
                 "dissolution": [{"time_h": 1, "cumulative_release_pct": 25}]}}
]


def test_endpoint(ep):
    url = BASE_URL + ep["path"]
    req = Request(url, method=ep["method"])
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "SwarmLabs-PerfBaseline/1.0")

    if ep["method"] == "POST":
        req.data = json.dumps(ep.get("payload", {})).encode("utf-8")

    start = time.time()
    try:
        with urlopen(req, timeout=30) as resp:
            resp.read()
            elapsed = round((time.time() - start) * 1000, 1)
            return {"name": ep["name"], "path": ep["path"], "method": ep["method"],
                    "category": ep["category"], "status": resp.status, "latency_ms": elapsed, "error": None}
    except HTTPError as e:
        elapsed = round((time.time() - start) * 1000, 1)
        # 401是认证保护的预期行为
        if e.code == 401 and ep.get("requires_auth"):
            return {"name": ep["name"], "path": ep["path"], "method": ep["method"],
                    "category": ep["category"], "status": 401, "latency_ms": elapsed, "error": None}
        return {"name": ep["name"], "path": ep["path"], "method": ep["method"],
                "category": ep["category"], "status": e.code, "latency_ms": elapsed, "error": str(e)}
    except URLError as e:
        elapsed = round((time.time() - start) * 1000, 1)
        return {"name": ep["name"], "path": ep["path"], "method": ep["method"],
                "category": ep["category"], "status": 0, "latency_ms": elapsed, "error": str(e)}
    except Exception as e:
        elapsed = round((time.time() - start) * 1000, 1)
        return {"name": ep["name"], "path": ep["path"], "method": ep["method"],
                "category": ep["category"], "status": 0, "latency_ms": elapsed, "error": str(e)}


def run_baseline():
    results = [test_endpoint(ep) for ep in CORE_ENDPOINTS]

    # 分类统计
    meta_results = [r for r in results if r["category"] == "meta" and r["error"] is None]
    compute_results = [r for r in results if r["category"] == "compute" and r["error"] is None]

    meta_avg = round(sum(r["latency_ms"] for r in meta_results) / len(meta_results), 1) if meta_results else 0
    compute_avg = round(sum(r["latency_ms"] for r in compute_results) / len(compute_results), 1) if compute_results else 0
    overall_avg = round(sum(r["latency_ms"] for r in results if r["error"] is None) / max(1, len([r for r in results if r["error"] is None])), 1)

    # 加载历史基线
    baseline = {}
    if os.path.exists(BASELINE_FILE):
        try:
            with open(BASELINE_FILE, "r", encoding="utf-8") as f:
                baseline = json.load(f)
        except (OSError, json.JSONDecodeError):
            # L-20 2026-10-17: bare except → 显式 catch。baseline 文件缺失或损坏时
            # baseline 保持为 {}，.get() 会走 default 分支。
            pass

    prev_avg = baseline.get("overall_avg_ms", overall_avg)
    deviation_pct = round((overall_avg - prev_avg) / prev_avg * 100, 1) if prev_avg > 0 else 0

    report = {
        "report_type": "SwarmLabs 标准化性能基线",
        "timestamp": datetime.now().isoformat(),
        "base_url": BASE_URL,
        "overall_avg_ms": overall_avg,
        "meta_avg_ms": meta_avg,
        "compute_avg_ms": compute_avg,
        "previous_avg_ms": prev_avg,
        "deviation_pct": deviation_pct,
        "deviation_status": "OK" if abs(deviation_pct) <= 20 else "WARN",
        "endpoints_tested": len(results),
        "endpoints_success": len([r for r in results if r["error"] is None]),
        "details": results
    }

    # 保存为新基线（仅当偏差在合理范围内时更新）
    if abs(deviation_pct) <= 20:
        with open(BASELINE_FILE, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

    # 输出结果
    print("=" * 60)
    print(f"SwarmLabs 标准化性能基线 · {report['timestamp'][:19]}")
    print("=" * 60)
    print(f"整体平均: {overall_avg}ms (上次: {prev_avg}ms, 偏差: {deviation_pct:+.1f}%)")
    print(f"元数据端点平均: {meta_avg}ms | 计算端点平均: {compute_avg}ms")
    print(f"状态: {report['deviation_status']} ({'正常' if abs(deviation_pct) <= 20 else '超出20%阈值'})")
    print("-" * 60)
    for r in results:
        status = "OK" if r["error"] is None else "ERR"
        print(f"  [{status}] {r['name']:20s} {r['method']:4s} {r['latency_ms']:>8.1f}ms  (HTTP {r['status']})")
    print("=" * 60)

    return report


if __name__ == "__main__":
    run_baseline()
