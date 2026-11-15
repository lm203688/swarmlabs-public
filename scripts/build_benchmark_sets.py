#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""导出「官方留出测试集」为静态 JSON —— 让任意 HTTP 智能体提交自己的预测并拿到诚实判定。

## 为什么是导出测试集，而不是把 oracle 近似成 JS

CF Pages 跑不了 Python/numpy，所以「任意预测的独立验证」一度只能在 MCP/引擎侧做。
一条直觉方案是把解析 oracle 用 Chebyshev 张量拟合成 JS 可求值的系数——但那会引入
**自身近似误差**，污染我们给出的 R²/覆盖率（等于用一个估计去检验另一个估计）。

更好的方案：**测试集本身是固定的、公开的**（本就是标准 benchmark 的做法）。
把 62 个场景的官方留出测试集（X, y_true）导出成静态 JSON 后：
  - 任何 HTTP 智能体可在这些点上预测、POST 上来；
  - Worker 用**精确**的真值复算 R² / 覆盖率 / κ；
  - 零近似误差，且 benchmark 变得可被独立复现与审计。

诚实约束：
  - 导出的只有**测试集**（seed 991）。训练集（seed 0）与配置选择验证集（seed 4242）
    不导出——我们的报告宣称仍建立在私有训练切分上，避免任何自我泄漏。
  - y 是**无噪真值**（与报告 collect_evidence 的口径一致：engine 用 _noise_free_f）。
  - 阈值 / 噪声地板 / 判定口径一个都不改。

输出：
  frontend_fixed/reports/benchmark/index.json      —— 清单 + 策略 + 每场景元数据
  frontend_fixed/reports/benchmark/<key>.json      —— 单场景 {X, y, ...}

用法：
  python scripts/build_benchmark_sets.py [--print]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

from engine import report_generator as RG  # noqa: E402
from engine import verification_gate as VG  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "frontend_fixed", "reports", "benchmark")

STANDARD = "ASME V&V 10-2019 (verification & validation)"


def build_one(key: str) -> dict:
    """单个场景的官方留出测试集（与报告 collect_evidence 完全同口径）。"""
    scn = RG.get_scenario(key)
    dom = dict(scn)
    dom["f"] = RG._noise_free_f(dom)          # 无噪真值（判据口径）
    dim = int(dom.get("dim") or len(dom["bounds"]))
    n_test = int(dom.get("n_test") or RG._DATA_SIZES.get(dim, RG._DEFAULT_SIZES)[1])

    # 与 collect_evidence 相同的抽点口径：原始 bounds + 测试种子 991
    Xt = RG._lhs(dom["bounds"], n_test, RG._key_seed(key, 991))
    yt = np.array([float(dom["f"](x)) for x in Xt], float)

    return {
        "scenario_key": key,
        "dim": dim,
        "bounds": [[float(a), float(b)] for a, b in np.asarray(dom["bounds"], float)],
        "n_test": int(n_test),
        "test_seed": 991,
        "test_seed_note": ("official held-out split used in the published V&V reports "
                           "(collect_evidence seed+991). Training split (seed 0) and the "
                           "model-selection split (seed 4242) are NOT published."),
        "ground_truth": "noise-free oracle response (same convention as the reports)",
        "X": [[float(c) for c in row] for row in Xt],
        "y": [float(v) for v in yt],
    }


def build_manifest(results: dict) -> dict:
    counts = {"PASS": 0, "MARGINAL": 0, "REFUTED": 0, "ERROR": 0}
    scenarios = {}
    for key, one in results.items():
        led = VG.ledger_entry(key)
        verdict = (led or {}).get("verdict")
        gate = (led or {}).get("gate")
        if verdict in counts:
            counts[verdict] += 1
        scenarios[key] = {
            "dim": one["dim"],
            "n_test": one["n_test"],
            "bounds": one["bounds"],
            "file": f"/reports/benchmark/{key}.json",
            "verdict": verdict,
            "gate": gate,
        }
    return {
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "standard": STANDARD,
        "purpose": (f"Public held-out test sets for the {len(results)} SwarmLabs scenarios. Any agent can "
                    "predict on these points and POST them to /v3/verify to receive an exact, "
                    "independent R²/coverage/κ verdict and a hard PROCEED|BLOCK gate."),
        "policy": VG.policy(),
        # 引擎指纹：/v3/verify 的响应要能自证"这批判定基于哪一版引擎"。
        # 与 gate_ledger.provenance.engine 同源，同一函数导出，不各写一份。
        "engine": VG.engine_fingerprint(),
        "oracle_digest": VG.oracle_digest(),
        # 机器可读阈值：HTTP Worker 无 numpy，却要在 /v3/verify 复算 verdict。
        # 把数值由引擎导出到本清单，避免 JS 侧另抄一份"什么叫通过"。
        "gate_policy": VG.thresholds(),
        "verify_endpoint": {
            "method": "POST",
            "path": "/v3/verify",
            "alt_path": "/v3/verify/{scenario_key}",
            "body": {"scenario_key": "<key>",
                     "predictions": [{"x": ["<dim floats>"], "y_pred": 0.0,
                                      "y_std": "optional 1-sigma"}]},
            "note": ("预测点必须与 <key>.json 的 X 逐点对应（同序同长）。"
                     "y_std 缺省时只复算 R²，coverage 返回 null。"),
        },
        "total": len(results),
        "total_test_points": int(sum(v["n_test"] for v in results.values())),
        "verdict_counts": counts,
        "scenarios": scenarios,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--print", dest="do_print", action="store_true",
                    help="只打印摘要，不写文件")
    args = ap.parse_args()

    t0 = time.time()
    keys = sorted(RG.all_scenarios().keys())
    os.makedirs(OUT_DIR, exist_ok=True)

    results = {}
    for i, key in enumerate(keys, 1):
        one = build_one(key)
        results[key] = one
        if not args.do_print:
            with open(os.path.join(OUT_DIR, f"{key}.json"), "w", encoding="utf-8") as f:
                json.dump(one, f, ensure_ascii=False, separators=(",", ":"))
        print(f"[{i:2d}/{len(keys)}] {key:34s} dim={one['dim']} n_test={one['n_test']}", flush=True)

    manifest = build_manifest(results)
    if not args.do_print:
        with open(os.path.join(OUT_DIR, "index.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        total_bytes = sum(os.path.getsize(os.path.join(OUT_DIR, f))
                          for f in os.listdir(OUT_DIR))
        print(f"\nwrote {len(keys)} scenario files + index.json -> {OUT_DIR}"
              f"  ({total_bytes/1024:.0f} KB, {time.time()-t0:.0f}s)")
    else:
        print(f"\n[print-only] {len(keys)} scenarios, "
              f"{manifest['total_test_points']} test points, "
              f"counts={manifest['verdict_counts']}")

    print(json.dumps({"total": manifest["total"],
                      "total_test_points": manifest["total_test_points"],
                      "verdict_counts": manifest["verdict_counts"],
                      "gate_map": manifest["policy"]["gate_map"]},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
