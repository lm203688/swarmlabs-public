#!/usr/bin/env python3
"""scripts/build_frontend_metrics.py — 前端展示数字由构建注入，不再硬编码。

Report §P0-C (2026-10-16 晚 / 10-17):
  "本轮有了实证伤害：frontend_fixed/index.html 里一个硬编码的 227
   造成了 CI MISMATCH，只能手改。
   做三件事：... 前端数字改由构建注入 ..."

设计
----
前端展示层（`frontend_fixed/index.html`）中出现的**业务指标数字**
（场景数、校准点数、PASS/REFUTED 计数、引擎模块数）不再手写，
统一由 `scripts/build_frontend_metrics.py` 从数据源推导注入。

两种模式
--------
    --check  校验当前 index.html 中的展示数字与数据源一致；
             任何漂移即 exit 1 并列出期望值 vs 实际值。
             CI 每次 push 都跑这个模式。
    --fix    用数据源最新值重写 index.html。
             本地手动执行；执行后需 review + commit。

约定
----
在 index.html 中，每个业务指标数字前后用 HTML 注释包住占位符：
    <!-- {{METRIC_NAME}} -->228<!-- /{{METRIC_NAME}} -->
这样：
    * 浏览器渲染时注释不可见，页面显示数字正常
    * 构建脚本能通过固定 token 定位并替换
    * 未标注数字（比如 3/6/73）保持原样；只有已注册的 METRIC 被管控

数据源（唯一真源）
------------------
    指标                数据源
    -----------------   -----------------------------------------
    SCENARIOS_VALIDATED data/trust_surface.json: overall.scenarios
    STORE_POINTS        data/trust_surface.json: overall.total_store_points
    GATE_PASS           data/gate_ledger.json: counts.PASS
    GATE_MARGINAL       data/gate_ledger.json: counts.MARGINAL
    GATE_REFUTED        data/gate_ledger.json: counts.REFUTED
    EXT_PASS            BENCHMARK.json: core_result.by_verdict.PASS
    EXT_MARGINAL        BENCHMARK.json: core_result.by_verdict.MARGINAL
    EXT_REFUTED         BENCHMARK.json: core_result.by_verdict.REFUTED
    ENGINE_MODULES      len(engine/*.py) excluding __init__.py

用法
----
    python scripts/build_frontend_metrics.py --check     # CI 用
    python scripts/build_frontend_metrics.py --fix       # 更新展示

退出码
------
    0  一致 / 已修复
    1  发现漂移，需运行 --fix 或修数据
    2  文件缺失或数据源不可读
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, Optional


ROOT = Path(__file__).resolve().parent.parent
HTML = ROOT / "frontend_fixed" / "index.html"
TRUST_SURFACE = ROOT / "data" / "trust_surface.json"
GATE_LEDGER = ROOT / "data" / "gate_ledger.json"
BENCHMARK = ROOT / "benchmarks" / "extrapolation-bench" / "BENCHMARK.json"
ENGINE_DIR = ROOT / "engine"


# =============================================================================
# Metrics resolver
# =============================================================================
def _load_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def resolve_metrics() -> Dict[str, int]:
    """Derive all frontend metric values from the data sources of truth."""
    metrics: Dict[str, int] = {}

    # trust_surface
    if TRUST_SURFACE.exists():
        ts = _load_json(TRUST_SURFACE)
        ov = ts.get("overall", {})
        metrics["SCENARIOS_VALIDATED"] = int(ov.get("scenarios", 0))
        metrics["STORE_POINTS"] = int(ov.get("total_store_points", 0))

    # gate_ledger
    if GATE_LEDGER.exists():
        gl = _load_json(GATE_LEDGER)
        counts = gl.get("counts", {})
        metrics["GATE_PASS"] = int(counts.get("PASS", 0))
        metrics["GATE_MARGINAL"] = int(counts.get("MARGINAL", 0))
        metrics["GATE_REFUTED"] = int(counts.get("REFUTED", 0))

    # extrapolation bench
    if BENCHMARK.exists():
        eb = _load_json(BENCHMARK)
        bv = eb.get("core_result", {}).get("by_verdict", {})
        metrics["EXT_PASS"] = int(bv.get("PASS", 0))
        metrics["EXT_MARGINAL"] = int(bv.get("MARGINAL", 0))
        metrics["EXT_REFUTED"] = int(bv.get("REFUTED", 0))

    # engine module count — 真源 scripts/engine_module_count.py (L-01 2026-10-17)
    if ENGINE_DIR.exists():
        # scripts/ 不是 package，从当前脚本目录导入
        import sys
        _scripts_dir = str(Path(__file__).resolve().parent)
        if _scripts_dir not in sys.path:
            sys.path.insert(0, _scripts_dir)
        from engine_module_count import engine_module_count as _ec  # noqa: E402
        metrics["ENGINE_MODULES"] = _ec(ENGINE_DIR)

    return metrics


# =============================================================================
# Placeholder markers
# =============================================================================
# Convention: <!-- {{NAME}} -->value<!-- /{{NAME}} -->
OPEN_RE = re.compile(r"<!--\s*\{\{([A-Z_][A-Z0-9_]*)\}\}\s*-->(.*?)<!--\s*/\{\{\1\}\}\s*-->", re.DOTALL)


def parse_current(html: str) -> Dict[str, str]:
    """Return {name: currently_shown_value} for all placeholders in html."""
    return {m.group(1): m.group(2).strip() for m in OPEN_RE.finditer(html)}


def replace_all(html: str, metrics: Dict[str, int]) -> str:
    """Replace every placeholder's inner text with the resolved value."""
    def _sub(m):
        name = m.group(1)
        if name not in metrics:
            raise KeyError(f"Placeholder {{{{{name}}}}} has no resolver in build_frontend_metrics.resolve_metrics()")
        return f"<!-- {{{{{name}}}}} -->{metrics[name]}<!-- /{{{{{name}}}}} -->"
    return OPEN_RE.sub(_sub, html)


# =============================================================================
# CLI
# =============================================================================
def main(argv) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="verify HTML matches data sources (exit 1 on drift)")
    mode.add_argument("--fix", action="store_true", help="rewrite HTML from data sources")
    args = ap.parse_args(argv)

    if not HTML.exists():
        print(f"[FAIL] {HTML} not found", file=sys.stderr)
        return 2

    html = HTML.read_text(encoding="utf-8")
    metrics = resolve_metrics()

    if not metrics:
        print("[FAIL] no metrics resolved — data sources unavailable", file=sys.stderr)
        return 2

    current = parse_current(html)

    print(f"[build_frontend_metrics] mode={'fix' if args.fix else 'check'}")
    print(f"  html: {HTML.relative_to(ROOT)}")
    print(f"  metrics resolved: {len(metrics)}")

    if not current:
        print("[INFO] no placeholders found in HTML — nothing to manage")
        print("  (If metrics are hardcoded in prose, they won't be tracked. Add placeholders manually.)")
        return 0

    if args.fix:
        new_html = replace_all(html, metrics)
        if new_html != html:
            HTML.write_text(new_html, encoding="utf-8")
            print(f"[OK] HTML updated with {len(metrics)} fresh values")
        else:
            print("[OK] HTML already up-to-date (no changes needed)")
        return 0

    # --check mode
    drifts = []
    for name, resolved in metrics.items():
        if name not in current:
            # metric known to builder but not present in HTML — informational only
            continue
        shown = current[name]
        if shown != str(resolved):
            drifts.append({
                "metric": name,
                "shown_in_html": shown,
                "expected_from_data": str(resolved),
            })

    # also: placeholders in HTML that have no resolver
    orphan = [n for n in current if n not in metrics]
    if orphan:
        print(f"[WARN] {len(orphan)} placeholders in HTML without a resolver: {orphan}")

    if drifts:
        print(f"\n[FAIL] {len(drifts)} drift(s) found between frontend display and data sources:")
        print(f"  {'METRIC':<25} {'HTML SHOWS':<15} {'DATA SAYS':<15}")
        print(f"  {'-'*55}")
        for d in drifts:
            print(f"  {d['metric']:<25} {d['shown_in_html']:<15} {d['expected_from_data']:<15}")
        print("\n  Fix: python scripts/build_frontend_metrics.py --fix")
        return 1

    print(f"[OK] {len(current)} placeholder(s) all match data sources of truth")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
