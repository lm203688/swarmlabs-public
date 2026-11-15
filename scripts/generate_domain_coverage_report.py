#!/usr/bin/env python3
"""Generate the domain-level coverage report for the 95-scenario corpus.

从 `frontend_fixed/reports/scenario_library_density.json` 提取分类密度，
生成一份可读的 markdown 报告，标注每个 domain 的覆盖情况（dense / thin / sparse）
和已知缺口（gap_categories）。

用法：
    python scripts/generate_domain_coverage_report.py           # 生成到 reports/upgrade/
    python scripts/generate_domain_coverage_report.py --out DIR # 指定输出目录

诚实边界（板块 09 P0 修正）：
    - 95 场景的分布是**已知**的（scenario_library_density.json 是单一真源）
    - "dense / thin / sparse" 的判定阈值是**硬编码**的（1.25 / 0.75 分位）
    - "gap_categories" 来自 engine.domains.PENDING_CATEGORIES，是**声明的**缺口
      而非"我们不知道"的缺口。真实未察觉的缺口本模块无法自动发现。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

# ── Repo root into sys.path ──
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


SOURCE = Path(_ROOT) / "frontend_fixed" / "reports" / "scenario_library_density.json"
DEFAULT_OUT = Path(_ROOT) / "reports" / "upgrade" / "domain_coverage_95_scenarios_2026-10-14.md"


def _load_source() -> Dict:
    if not SOURCE.exists():
        raise FileNotFoundError(f"source file missing: {SOURCE}")
    return json.loads(SOURCE.read_text(encoding="utf-8"))


def _categorize_verdict(verdict: str, count: int, total: int) -> Tuple[str, str]:
    """把 verdict 映射为视觉标记 + 说明。"""
    pct = count / total * 100 if total else 0
    if verdict == "dense":
        return "🟢 dense", f"{count} 场景 ({pct:.1f}%)，参数空间采样充足"
    if verdict == "thin":
        return "🟡 thin", f"{count} 场景 ({pct:.1f}%)，稀疏但非空"
    if verdict == "sparse":
        return "🔴 sparse", f"{count} 场景 ({pct:.1f}%)，仅覆盖少量参数"
    return f"⚪ {verdict}", f"{count} 场景 ({pct:.1f}%)"


def _generate_report(data: Dict) -> str:
    total = data.get("total_scenarios_loaded", 0)
    categories = data.get("categories", {})
    density = data.get("density_summary", {})
    note = data.get("note", "")

    # 按 count 降序
    sorted_cats = sorted(categories.items(), key=lambda x: -x[1]["count"])

    lines = []
    lines.append("# 95 场景库 · Domain-level 覆盖报告")
    lines.append("")
    lines.append(f"**生成日期**：{datetime.now(timezone.utc).isoformat()}")
    lines.append(f"**单一真源**：`{SOURCE.relative_to(_ROOT)}`")
    lines.append(f"**总场景数**：{total}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 1. 分类密度总览")
    lines.append("")
    lines.append("| Domain | Verdict | Count | Dim 范围 | Log10 参数体积 |")
    lines.append("|---|---|---|---|---|")

    for name, stats in sorted_cats:
        badge, _ = _categorize_verdict(stats["verdict"], stats["count"], total)
        dim_range = f"{stats['dim_min']}–{stats['dim_max']}" if stats["dim_min"] != stats["dim_max"] else str(stats["dim_min"])
        vol = f"{stats['log10_volume_mean']:.3f}"
        lines.append(f"| {name} | {badge} | {stats['count']} | {dim_range} | {vol} |")

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 2. 分类说明")
    lines.append("")

    dense = density.get("dense_categories", [])
    thin = density.get("thin_categories", [])
    sparse = density.get("sparse_categories", [])
    gaps = density.get("gap_categories", [])

    lines.append(f"**🟢 Dense（{len(dense)}）**：{', '.join(dense) if dense else '(无)'}")
    lines.append("")
    lines.append(f"**🟡 Thin（{len(thin)}）**：{', '.join(thin) if thin else '(无)'}")
    lines.append("")
    lines.append(f"**🔴 Sparse（{len(sparse)}）**：{', '.join(sparse) if sparse else '(无)'}")
    lines.append("")
    lines.append(f"**⚪ Gap（{len(gaps)}，未实现）**：{', '.join(gaps) if gaps else '(无)'}")
    lines.append("")
    lines.append(f"**待实现引擎数**：{density.get('total_gap_pending_engines', '?')}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 3. 关键观察")
    lines.append("")

    # 找出 top-3 密集 domain 和最薄弱 domain
    top3 = [c for c, _ in sorted_cats[:3]]
    smallest = [c for c, s in sorted_cats if s["count"] == 1][:3]

    lines.append(f"- **最密集 3 个 domain**：{', '.join(top3)} —— 占 {sum(categories[c]['count'] for c in top3)}/{total} = {sum(categories[c]['count'] for c in top3)/total*100:.1f}% 场景")
    lines.append(f"- **仅 1 场景的 domain**：{', '.join(smallest) if smallest else '(无)'} —— G3 holdout-discipline 测试的样本量瓶颈")
    lines.append(f"- **参数空间跨度最大的 domain**：{max(categories.items(), key=lambda x: x[1]['log10_volume_mean'])[0]}（log10 volume = {max(s['log10_volume_mean'] for s in categories.values()):.3f}）")
    lines.append(f"- **维度跨度最大的 domain**：{max(categories.items(), key=lambda x: x[1]['dim_max'])[0]}（最高 dim = {max(s['dim_max'] for s in categories.values())}）")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 4. 与 73/95 外推压力的关联")
    lines.append("")
    lines.append(
        "- **biology** 56 场景最密集，但这不代表 biology 泛化能力强——"
        "G3 holdout-discipline 0/14 学科通过说明跨学科（不是跨场景）才是难点。"
    )
    lines.append("")
    lines.append(
        "- **pde / llm / finance / neuroscience / ecology / energy** 各只有 1 场景——"
        "G3 测试需要至少 3 场景/学科才有统计意义，这些 domain 在 G3 上被自动跳过。"
    )
    lines.append("")
    lines.append(
        "- **待实现 5 个 gap domain（Environment / Materials / Separation / Analysis / Pharma）** "
        "—— 加起来 105 个待实现引擎，这是「覆盖广度」的真正瓶颈，不是当前 95 场景的问题。"
    )
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 5. 诚实边界")
    lines.append("")
    lines.append(f"> {note}")
    lines.append("")
    lines.append("- **dense / thin / sparse 的判定阈值是硬编码的**（log10 volume 分位）；"
                 "换一个阈值会有不同分类。本报告的目的是**暴露分布**，不是定义『覆盖合格』。")
    lines.append("- **gap_categories 是声明的缺口**，不是『我们不知道』的缺口。"
                 "真实未察觉的 domain 需要领域专家参与才能发现。")
    lines.append("- **本报告不改变 73/95 判负结论**。分类密度不影响外推压力测试的判定。")
    lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help=f"output path (default: {DEFAULT_OUT})")
    args = parser.parse_args()

    data = _load_source()
    report = _generate_report(data)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report, encoding="utf-8")
    print(f"[OK] wrote {args.out} ({args.out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
