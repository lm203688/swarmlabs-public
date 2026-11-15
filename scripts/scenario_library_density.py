"""Scenario-library density heatmap — quantifies scenario-library "known space" coverage.

Motivated by DeepMass/FragNet style "map any MS2 spectrum across the known
structural space" — they visualize the known / semi-known / unknown map rather
than hiding sparsity behind a global accuracy number.

This script does the equivalent for our 62-scenario library:

  1. Category coverage bar: validated scenarios per category vs pending engines
     per category (from engine.domains.PENDING_CATEGORIES).
  2. Dimensional density: for each scenario, compute
         log10 of parameter-space volume = product of (hi-lo) over bounds
     so that a 6-D scenario and a 1-D scenario are on the same footing
     (higher log-volume = more room for a new scenario to be "outside known").
  3. Category × dimension grid heatmap: cell = count of scenarios, coloured by
     density. Empty cells = known gap.
  4. Density verdict per category:
         - "dense"    : >= 5 scenarios
         - "thin"     : 2-4 scenarios
         - "sparse"   : 1 scenario
         - "gap"      : 0 scenarios but >= 1 pending engine

Outputs:
    frontend_fixed/reports/scenario_library_density.json   (data)
    frontend_fixed/reports/scenario_library_density.html   (view)

Honest principles:
  - Only counts scenarios that are actually loaded and usable (callable f,
    bounds, ls defined) — no phantom counts.
  - PENDING counts are exactly what PENDING_CATEGORIES says — do NOT invent
    numbers.
  - The HTML renders without any external CDN (no Chart.js / plotly) so it
    lives on CF Pages as a static asset.
"""

from __future__ import annotations

import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# Load every real scenario (same source of truth as paper_validation_loop)
# ---------------------------------------------------------------------------
def _load_scenarios():
    """单一真源：**直接问 engine.scenario_registry 要全量场景**。

    历史教训（2026-09-21 → 2026-10-08）：
      1. 本函数最初自己拼 SCENARIOS + MICRO + SCENARIOS_EXTRA → 72；
      2. 而 report_generator.all_scenarios() 是 82；可信面又是 62 ——
         「三套注册表三个数」。
      3. 2026-09-21 改为「先问 paper_validation_loop.ALL_SCENARIOS，
         失败才回落到旧拼接」，仍保留了一条**可能再次漂移**的兜底路径。
      4. 2026-10-08（P1-5）改为**直接委派 engine.scenario_registry**，
         彻底删除本地拼接 —— 装配只写一次。

    返回 (scenarios, source_note_list)。source_note 记录降级来源，
    供 HTML 报告如实标注「本表在缺 RDKit 的环境里少 2 个分子场景」。
    """
    from engine.scenario_registry import get_registry

    reg = get_registry()
    notes = [f"来源降级：{d}" for d in reg.degraded]
    if reg.source_counts.get("molecular", 0) == 0:
        notes.append("molecular 来源为 0（本环境无 RDKit）：头条数相对满配少 2")
    return list(reg.items), notes


def _scenario_dim(scen):
    """Param-space dimensionality = len(bounds)."""
    b = scen.get("bounds")
    if b is None:
        return 0
    return len(b)


def _scenario_log_vol(scen):
    """log10(product of axis ranges). NaN when bounds invalid."""
    b = scen.get("bounds")
    if not b:
        return math.nan
    prod = 1.0
    for hi_lo in b:
        try:
            lo, hi = float(hi_lo[0]), float(hi_lo[1])
        except Exception:
            return math.nan
        if hi <= lo:
            return math.nan
        prod *= (hi - lo)
    if prod <= 0:
        return math.nan
    return math.log10(prod)


def _category(scen):
    c = scen.get("category") or "unknown"
    # The micro scenarios have long-form category strings; bucket them all into
    # "biology" — that matches the CATEGORY_LABELS taxonomy used elsewhere.
    if "micro" in scen.get("key", "").lower():
        return "biology"
    return c


def build_data():
    # 2026-10-08（P1-5）：_load_scenarios 已统一返回 (全量场景, 降级说明)。
    core, source_notes = _load_scenarios()
    micro = []  # 微生物已含在 core（注册表单一致后无需再分桶）
    all_scen = core + micro
    usable = [s for s in all_scen if callable(s.get("f")) and s.get("bounds")]
    # De-dup by key (SCENARIOS_EXTRA and paper_validation_loop overlap on a few)
    seen, dedup = set(), []
    for s in usable:
        k = s.get("key")
        if k in seen:
            continue
        seen.add(k)
        dedup.append(s)
    usable = dedup

    # PENDING
    try:
        from engine.domains import PENDING_CATEGORIES
        pending = {k: v["count"] for k, v in PENDING_CATEGORIES.items()}
    except Exception:
        pending = {}

    # Per-scenario metrics
    rows = []
    for s in usable:
        dim = _scenario_dim(s)
        logv = _scenario_log_vol(s)
        cat = _category(s)
        rows.append({
            "key": s.get("key"),
            "category": cat,
            "dimension": dim,
            "log10_volume": (None if math.isnan(logv) else round(logv, 3)),
            "domain": (s.get("domain") or "")[:80],
        })

    # Aggregate per category
    per_cat = {}
    for r in rows:
        per_cat.setdefault(r["category"], []).append(r)
    cat_summary = {}
    for cat, rs in per_cat.items():
        dims = [r["dimension"] for r in rs]
        logvs = [r["log10_volume"] for r in rs if r["log10_volume"] is not None]
        cat_summary[cat] = {
            "count": len(rs),
            "dim_min": min(dims),
            "dim_max": max(dims),
            "dim_mean": round(sum(dims) / len(dims), 2),
            "log10_volume_mean": (round(sum(logvs) / len(logvs), 3) if logvs else None),
            "verdict": _verdict(len(rs)),
        }

    # Coverage bar: validated count per category vs pending count per category
    # PENDING keys are industry names (Chemistry/Environment/...); map them to
    # our internal categories where obvious, else show them as their own lane.
    # Direct-name collisions:
    name_map = {
        "chemistry": "Chemistry",
        "biology": "Biology",
        "pde": "Energy",        # PDE category is closest to Energy/Thermo
        "optimization": None,   # no direct industry mapping
        "llm": None,
    }
    # Merge validated (lowercase internal) and pending (TitleCase industry) into
    # one row per "concept", so the bar chart doesn't split Biology vs biology.
    # Canonical display name = the industry bucket when there is one, else the
    # internal category name.
    concept_map = {  # display_name -> (internal_cat_or_None, pending_key_or_None)
        "Biology":      ("biology", "Biology"),
        "Chemistry":    ("chemistry", "Chemistry"),
        "Energy/PDE":   ("pde", "Energy"),
        "Optimization": ( "optimization", None),
        "LLM":          ("llm", None),
    }
    coverage_rows = []
    for display, (internal, pkey) in concept_map.items():
        v = cat_summary.get(internal, {}).get("count", 0)
        p = pending.get(pkey, 0) if pkey else 0
        coverage_rows.append({
            "category": display,
            "internal": internal,
            "validated_scenarios": v,
            "pending_engines": p,
            "verdict": _verdict(v),
        })
    # Any remaining PENDING buckets (no internal category yet) get their own
    # zero-validated row so we don't hide them.
    for pkey, p in pending.items():
        if pkey in {v for _, v in concept_map.values()} or pkey == "NeuroRobotics":
            pass
        if pkey not in {v for _, v in concept_map.values()}:
            coverage_rows.append({
                "category": pkey,
                "internal": None,
                "validated_scenarios": 0,
                "pending_engines": p,
                "verdict": "gap",
            })

    # Category × dimension grid (heatmap cells)
    max_dim = max(r["dimension"] for r in rows) if rows else 1
    grid = {}
    for r in rows:
        grid.setdefault(r["category"], {}).setdefault(r["dimension"], 0)
        grid[r["category"]][r["dimension"]] += 1
    grid_out = {c: {str(d): n for d, n in sorted(v.items())}
                 for c, v in sorted(grid.items())}

    # Density verdict summary (G7 gap in neuro_robotics_gap_scan)
    dense = [c for c, s in cat_summary.items() if s["verdict"] == "dense"]
    thin = [c for c, s in cat_summary.items() if s["verdict"] == "thin"]
    sparse = [c for c, s in cat_summary.items() if s["verdict"] == "sparse"]
    gaps = [c for c, p in pending.items() if p >= 1 and _pending_matches(c, cat_summary) is None]

    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_scenarios_loaded": len(usable),
        "core_cross_domain": len(core),
        "micro_scenarios": len(micro),
        "categories": cat_summary,
        "coverage_bar": coverage_rows,
        "dim_x_volume": rows,
        "grid": grid_out,
        "max_dim": max_dim,
        "pending_categories": pending,
        "density_summary": {
            "dense_categories": dense,
            "thin_categories": thin,
            "sparse_categories": sparse,
            "gap_categories": gaps,
            "total_gap_pending_engines": sum(pending.get(c, 0) for c in gaps),
        },
        "note": (
            "Density measured in log10 of parameter-space volume so that "
            "1-D and 6-D scenarios are comparable. PENDING counts come "
            "verbatim from engine.domains.PENDING_CATEGORIES. Empty cells "
            "in the grid are known gaps, not noise."
        ),
        # 2026-10-08（P1-5）：来源降级如实披露。
        # 头条场景数（95）依赖可选第三方库 RDKit（2 个分子场景）。
        # 本表在无 RDKit 的环境里会少这 2 个 —— 这不是表错了，
        # 而是环境缺依赖；把它写进产物，读者才不会误判。
        "source_notes": source_notes,
        "registry_fingerprint": (
            __import__("engine.scenario_registry", fromlist=["x"])
            .get_registry()
            .fingerprint()
        ),
    }
    return data


def _verdict(n):
    if n >= 5:
        return "dense"
    if n >= 2:
        return "thin"
    if n >= 1:
        return "sparse"
    return "gap"


def _pending_matches(pending_key, cat_summary):
    """Return the matching internal category name for a PENDING key, or None."""
    direct = {
        "chemistry": "Chemistry",
        "biology": "Biology",
    }
    lower = pending_key.lower()
    if lower in direct and direct[lower] in cat_summary:
        return direct[lower]
    # NeuroRobotics is pending with 0 — that's still a real gap
    if pending_key == "NeuroRobotics":
        return None
    # Fuzzy match: category label vs pending name
    for c in cat_summary:
        if c.lower() in lower or lower in c.lower():
            return c
    return None


# ---------------------------------------------------------------------------
# HTML report — pure SVG, no external deps
# ---------------------------------------------------------------------------
def _published_count():
    """已发布 10 章 V&V 报告的场景数。

    口径单一真源是 data/public_facts.json（由 scripts/build_public_facts.py 从
    frontend_fixed/reports/index.json 生成）。本页自己加载的是**代码注册表**里的
    可用场景数（total_scenarios_loaded），两者不同：前者是「已交付并留有报告」，
    后者是「代码里能跑」。页面必须把这两个数分开写，不能混成一个「场景数」。
    读不到事实表时返回 None，页面退化成不引用该数（fail-closed，不猜）。
    """
    try:
        p = ROOT / "data" / "public_facts.json"
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8"))[
            "facts"]["validated_scenarios"]["value"]
    except Exception:
        return None


def _html(data):
    total = data["total_scenarios_loaded"]
    published = _published_count()
    cats = data["categories"]
    cov = data["coverage_bar"]
    grid = data["grid"]
    summary = data["density_summary"]

    # Panel A: coverage bar
    max_v = max((r["validated_scenarios"] for r in cov), default=1)
    max_p = max((r["pending_engines"] for r in cov), default=1)
    scale_v = 260 / max(max_v, 1)
    scale_p = 260 / max(max_p, 1)

    bar_rows = []
    y = 30
    for r in cov:
        v_w = r["validated_scenarios"] * scale_v
        p_w = r["pending_engines"] * scale_p
        bar_rows.append(
            f'<text x="200" y="{y}" text-anchor="end" font-size="12" fill="#333">{r["category"]}</text>'
            f'<rect x="210" y="{y-10}" width="{v_w:.1f}" height="12" fill="#2e7d32" opacity="0.85"/>'
            f'<text x="{210+v_w+4:.1f}" y="{y}" font-size="11" fill="#2e7d32">{r["validated_scenarios"]}</text>'
            f'<rect x="210" y="{y+6}" width="{p_w:.1f}" height="12" fill="#c62828" opacity="0.55"/>'
            f'<text x="{210+p_w+4:.1f}" y="{y+16}" font-size="11" fill="#c62828">{r["pending_engines"]} pending</text>'
        )
        y += 40
    panel_a_h = y + 10

    # Panel B: scatter of dim vs log10_volume
    points = []
    for r in data["dim_x_volume"]:
        d = r["dimension"]
        lv = r["log10_volume"]
        if lv is None:
            continue
        x = 60 + d * 42
        yb = 240 - max(0, min(240, (lv + 3) * 24))
        color = {"optimization": "#1565c0", "biology": "#2e7d32",
                 "pde": "#6a1b9a", "chemistry": "#e65100",
                 "llm": "#ad1457"}.get(r["category"], "#555")
        points.append(
            f'<circle cx="{x}" cy="{yb:.1f}" r="5" fill="{color}" opacity="0.75" '
            f'data-tip="{r["key"]} (dim={d}, log10vol={lv})">'
            f'<title>{r["key"]} · dim={d} · log10(vol)={lv}</title></circle>'
        )
    panel_b_svg = f"""
      <svg width="360" height="290" viewBox="0 0 360 290" xmlns="http://www.w3.org/2000/svg">
        <rect x="0" y="0" width="360" height="290" fill="#fafafa" stroke="#ddd"/>
        <text x="180" y="18" text-anchor="middle" font-size="13" font-weight="600" fill="#222">
          Scenario library: dimension vs log10(parameter-space volume)
        </text>
        <line x1="50" y1="250" x2="330" y2="250" stroke="#666"/>
        <line x1="50" y1="30" x2="50" y2="250" stroke="#666"/>
        <text x="190" y="278" text-anchor="middle" font-size="11" fill="#444">parameter dim (1 → 6+)</text>
        <text x="10" y="140" text-anchor="middle" font-size="11" fill="#444" transform="rotate(-90 10 140)">log10(vol)</text>
        {''.join(f'<text x="{60+i*42}" y="264" text-anchor="middle" font-size="10" fill="#666">{i+1}</text>' for i in range(7))}
        {''.join(points)}
        <g font-size="10" fill="#333">
          <rect x="180" y="42" width="140" height="86" fill="#fff" stroke="#eee"/>
          <text x="188" y="56" font-size="11" font-weight="600">Legend</text>
          <circle cx="194" cy="72" r="4" fill="#1565c0"/><text x="204" y="76">optimization</text>
          <circle cx="194" cy="88" r="4" fill="#2e7d32"/><text x="204" y="92">biology</text>
          <circle cx="194" cy="104" r="4" fill="#6a1b9a"/><text x="204" y="108">pde</text>
          <circle cx="194" cy="120" r="4" fill="#e65100"/><text x="204" y="124">chemistry</text>
          <circle cx="194" cy="136" r="4" fill="#ad1457"/><text x="204" y="140">llm</text>
        </g>
      </svg>
    """

    # Panel C: category × dim heatmap
    max_dim = data["max_dim"]
    cats_sorted = sorted(grid.keys())
    max_cell = max((n for c in grid.values() for n in c.values()), default=1)
    cells = []
    yy = 25
    for i, cat in enumerate(cats_sorted):
        cells.append(f'<text x="150" y="{yy+14}" text-anchor="end" font-size="12" fill="#333">{cat}</text>')
        for d in range(1, max_dim + 1):
            key = str(d)
            n = grid[cat].get(key, 0)
            opacity = 0.05 + 0.85 * (n / max_cell) if n > 0 else 0.02
            col = f'<rect x="{160 + (d-1)*36}" y="{yy}" width="34" height="30" '
            col += f'fill="#2e7d32" opacity="{opacity:.2f}" stroke="#999" stroke-width="0.5"/>'
            if n > 0:
                col += f'<text x="{160 + (d-1)*36 + 17}" y="{yy+18}" text-anchor="middle" font-size="11" fill="#fff">{n}</text>'
            else:
                col += f'<text x="{160 + (d-1)*36 + 17}" y="{yy+18}" text-anchor="middle" font-size="10" fill="#999">·</text>'
            cells.append(col)
        yy += 34
    for d in range(1, max_dim + 1):
        cells.append(f'<text x="{160 + (d-1)*36 + 17}" y="18" text-anchor="middle" font-size="10" fill="#555">dim {d}</text>')
    panel_c_svg = f"""
      <svg width="{200 + max_dim*36}" height="{yy+10}" viewBox="0 0 {200+max_dim*36} {yy+10}" xmlns="http://www.w3.org/2000/svg">
        <text x="10" y="14" font-size="12" font-weight="600" fill="#222">Category × dimension density</text>
        {''.join(cells)}
      </svg>
    """

    verdict_badge = {"dense": "dense", "thin": "thin", "sparse": "sparse", "gap": "gap", "no_scenarios": "gap"}
    def _badge(v):
        cls = verdict_badge.get(v, "gap")
        color = {"dense": "#2e7d32", "thin": "#f9a825", "sparse": "#e65100", "gap": "#c62828"}[cls]
        return f'<span class="badge" style="background:{color}">{v}</span>'

    rows_html = "".join(
        f'<tr><td><b>{c}</b></td>'
        f'<td>{s["count"]}</td>'
        f'<td>{s["dim_min"]}–{s["dim_max"]} (mean {s["dim_mean"]})</td>'
        f'<td>{s["log10_volume_mean"] if s["log10_volume_mean"] is not None else "n/a"}</td>'
        f'<td>{_badge(s["verdict"])}</td></tr>'
        for c, s in sorted(cats.items())
    )

    pending_rows = "".join(
        f'<tr><td>{c}</td><td>{p}</td></tr>' for c, p in sorted(data["pending_categories"].items())
    )

    dense_cnt = len(summary["dense_categories"])
    thin_cnt = len(summary["thin_categories"])
    sparse_cnt = len(summary["sparse_categories"])
    gap_cnt = len(summary["gap_categories"])
    total_gap_engines = summary["total_gap_pending_engines"]

    published_disp = str(published) if published is not None else "—"
    published_of = (f"{published} of them already have a published 10-chapter "
                    f"V&amp;V report") if published is not None \
        else "how many of them already have a published 10-chapter V&amp;V report " \
             "is tracked separately on the facts page"

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>SwarmLabs scenario-library density map</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  html,body{{margin:0;padding:0;background:#fafafa;color:#222;font:14px/1.55 -apple-system,Segoe UI,Roboto,sans-serif}}
  .wrap{{max-width:1080px;margin:0 auto;padding:24px}}
  h1{{font-size:22px;margin:0 0 6px 0}}
  h2{{font-size:16px;margin:28px 0 10px 0;color:#111;border-bottom:1px solid #ddd;padding-bottom:4px}}
  p.lead{{margin:0 0 20px 0;color:#444}}
  .kpis{{display:grid;grid-template-columns:repeat(6,1fr);gap:10px;margin:16px 0 24px 0}}
  .kpi{{background:#fff;border:1px solid #e0e0e0;border-radius:8px;padding:12px;text-align:center}}
  .kpi b{{display:block;font-size:22px;color:#111}}
  .kpi span{{font-size:11px;color:#666;text-transform:uppercase;letter-spacing:0.4px}}
  .grid2{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
  table{{width:100%;border-collapse:collapse;background:#fff;border:1px solid #e0e0e0;font-size:13px}}
  th,td{{text-align:left;padding:8px 10px;border-bottom:1px solid #eee}}
  th{{background:#f5f5f5;font-weight:600}}
  .badge{{color:#fff;padding:2px 8px;border-radius:10px;font-size:11px;text-transform:uppercase;letter-spacing:0.4px}}
  footer{{margin-top:36px;color:#666;font-size:12px;border-top:1px solid #ddd;padding-top:12px}}
  code{{background:#f0f0f0;padding:1px 4px;border-radius:3px;font-size:12px}}
</style>
</head>
<body>
<div class="wrap">
  <h1>Scenario-library density map</h1>
  <p class="lead">
    DeepMass and FragNet visualize the <em>known structural space</em> behind every
    MS2 spectrum rather than hiding sparsity behind a single accuracy number. This
    page does the equivalent for our {total} defined scenarios
    ({published_of}) — the honest, quantified answer to:
    <strong>how thick is the known-space, and where does it end?</strong>
  </p>

  <div class="kpis">
    <div class="kpi"><b>{total}</b><span>defined scenarios</span></div>
    <div class="kpi"><b>{published_disp}</b><span>with V&amp;V report</span></div>
    <div class="kpi"><b>{dense_cnt}</b><span>dense</span></div>
    <div class="kpi"><b>{thin_cnt}</b><span>thin</span></div>
    <div class="kpi"><b>{sparse_cnt}</b><span>sparse</span></div>
    <div class="kpi"><b>{total_gap_engines}</b><span>pending engines in gaps</span></div>
  </div>

  <h2>Category coverage — validated scenarios vs pending engines</h2>
  <svg width="560" height="{panel_a_h}" viewBox="0 0 560 {panel_a_h}" xmlns="http://www.w3.org/2000/svg">
    <rect x="0" y="0" width="560" height="{panel_a_h}" fill="#fff" stroke="#e0e0e0"/>
    <text x="210" y="18" font-size="12" fill="#2e7d32">■ validated scenarios</text>
    <text x="380" y="18" font-size="12" fill="#c62828">■ pending engines (PENDING_CATEGORIES)</text>
    {''.join(bar_rows)}
  </svg>

  <div class="grid2">
    <div>
      <h2>Dimension vs parameter-space volume</h2>
      {panel_b_svg}
    </div>
    <div>
      <h2>Category × dimension grid</h2>
      {panel_c_svg}
    </div>
  </div>

  <h2>Per-category density</h2>
  <table>
    <thead><tr><th>category</th><th>scenarios</th><th>dim (min–max, mean)</th><th>log10(vol) mean</th><th>verdict</th></tr></thead>
    <tbody>{rows_html}</tbody>
  </table>

  <h2>PENDING categories (industry engine backlog)</h2>
  <table>
    <thead><tr><th>industry bucket</th><th>engines waiting</th></tr></thead>
    <tbody>{pending_rows}</tbody>
  </table>

  <footer>
    Generated {data["generated_at"]} by <code>scripts/scenario_library_density.py</code>.<br>
    Density is measured as <code>log10(product of axis ranges)</code> so 1-D and 6-D
    scenarios share a scale. PENDING counts come verbatim from
    <code>engine/domains.py</code>. Empty grid cells are <em>known gaps</em>, not noise —
    this is the opposite of a hidden "R² = 1.0".
  </footer>
</div>
</body>
</html>
"""


def main():
    data = build_data()
    out_dir = ROOT / "frontend_fixed" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "scenario_library_density.json"
    html_path = out_dir / "scenario_library_density.html"
    json_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    html_path.write_text(_html(data), encoding="utf-8")
    print(f"[ok] {json_path}")
    print(f"[ok] {html_path}")
    print(f"     total_scenarios={data['total_scenarios_loaded']}  "
          f"categories={list(data['categories'].keys())}  "
          f"dense={data['density_summary']['dense_categories']}  "
          f"gap_pending_engines={data['density_summary']['total_gap_pending_engines']}")


if __name__ == "__main__":
    main()
