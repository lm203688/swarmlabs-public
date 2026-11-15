#!/usr/bin/env python3
"""Generate 3 core figures for paper-1 (Applied Intelligence submission).

Figures:
  fig1_tdea_anchor_triple.png   - TDEA 三维锚定示意图
  fig2_scenario_distribution.png- 95 场景验证分布图
  fig3_extrapolation_failure.png- 外推失效曲线
"""

import json
import math
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
FIG_DIR = ROOT / "outputs" / "paper-1-applied-intelligence" / "figures"
FIG_DIR.mkdir(parents=True, exist_ok=True)

# ── shared style ──────────────────────────────────────────────
plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "font.size": 9,
    "axes.titlesize": 10,
    "axes.labelsize": 9,
    "xtick.labelsize": 8,
    "ytick.labelsize": 8,
    "legend.fontsize": 8,
    "figure.dpi": 300,
    "savefig.bbox": "tight",
    "savefig.pad_inches": 0.05,
})

# ══════════════════════════════════════════════════════════════
# Figure 1: TDEA Three-Dimensional Anchor Triple
# ══════════════════════════════════════════════════════════════
def fig1_tdea_anchor_triple():
    """Conceptual diagram: 1-D flag → 3-D triple A=(system, version, condition)."""
    fig, ax = plt.subplots(figsize=(7.2, 5.0))
    ax.set_xlim(-0.5, 9.5)
    ax.set_ylim(-0.5, 6.5)
    ax.axis("off")

    # ── left panel: 1-D anchor (legacy) ──
    left_x = 1.5
    ax.add_patch(mpatches.FancyBboxPatch(
        (left_x - 1.2, 1.0), 2.4, 2.5,
        boxstyle="round,pad=0.15",
        facecolor="#f0f0f0", edgecolor="#888", linewidth=1.2,
    ))
    ax.text(left_x, 3.2, "Legacy 1-D anchor", ha="center", va="bottom",
            fontsize=8, style="italic", color="#555")
    ax.text(left_x, 2.7, "wet_lab = ✓ / ✗", ha="center", va="center",
            fontsize=10, fontfamily="monospace")
    ax.text(left_x, 2.2, "(boolean flag)", ha="center", va="center",
            fontsize=8, color="#888")

    # arrow
    ax.annotate("", xy=(4.3, 2.25), xytext=(2.8, 2.25),
                arrowprops=dict(arrowstyle="-|>", linewidth=2, color="#c0392b"))
    ax.text(3.55, 2.55, "TDEA lift", ha="center", va="bottom",
            fontsize=8, color="#c0392b", fontweight="bold")

    # ── right panel: 3-D triple ──
    right_x = 6.8
    triple_items = [
        ("experiment_system", "E. coli K-12", "S. cerevisiae S288c"),
        ("version",           "Monod, 1949",  "Kissling et al. 2022"),
        ("operating_condition", "μ = 0.05 h⁻¹",  "30 °C, pH 6.5"),
    ]
    y_base = 4.8
    ax.add_patch(mpatches.FancyBboxPatch(
        (right_x - 2.4, 0.2), 4.8, 5.4,
        boxstyle="round,pad=0.2",
        facecolor="#eaf2f8", edgecolor="#2980b9", linewidth=1.5,
    ))
    ax.text(right_x, 5.65, "TDEA Triple  A = (s, v, c)", ha="center", va="bottom",
            fontsize=10, fontweight="bold", color="#2980b9")

    for i, (dim, ex1, ex2) in enumerate(triple_items):
        y = y_base - i * 1.6
        # dimension label
        ax.text(right_x - 2.2, y + 0.15, f"${dim}$", ha="left", va="center",
                fontsize=9, fontfamily="monospace", color="#2980b9")
        # example values
        ax.text(right_x - 2.2, y - 0.2, f"e.g. {ex1}", ha="left", va="center",
                fontsize=8, color="#333")
        ax.text(right_x - 2.2, y - 0.5, f"     {ex2}", ha="left", va="center",
                fontsize=8, color="#333")
        # separator
        if i < 2:
            ax.plot([right_x - 2.2, right_x + 2.0], [y - 0.8, y - 0.8],
                    color="#aab", linewidth=0.5, linestyle=":")

    # ── bottom: gate verdict ──
    ax.text(4.8, 0.1, "Gate verdict:  all 3 dims filled  →  auto-transfer OK\n"
                       "                  any dim missing  →  HUMAN REVIEW (fail-closed)",
            ha="center", va="bottom", fontsize=8, color="#c0392b",
            bbox=dict(boxstyle="round,pad=0.3", facecolor="#fdf2f2", edgecolor="#e74c3c"))

    fig.savefig(FIG_DIR / "fig1_tdea_anchor_triple.png")
    plt.close(fig)
    print(f"  fig1_tdea_anchor_triple.png  ✓")


# ══════════════════════════════════════════════════════════════
# Figure 2: 95-Scenario Validation Distribution
# ══════════════════════════════════════════════════════════════
def fig2_scenario_distribution():
    """Per-domain rubric pass/fail breakdown across 95 scenarios."""
    with open(ROOT / "data" / "trust_surface.json") as f:
        rows = json.load(f)["rows"]

    # classify domain into major buckets
    def classify_domain(domain: str) -> str:
        d = domain.lower()
        if "微生物" in d or "micro" in d or "bio" in d or "coli" in d:
            return "Microbiology"
        if "optimization" in d or "surrogate" in d or "forrester" in d:
            return "Optimization"
        if "chem" in d or "cheminformatics" in d or "mol" in d:
            return "Chemistry"
        if "llm" in d or "neuro" in d:
            return "LLM / Neuroscience"
        if "heat" in d or "therm" in d or "energy" in d:
            return "Physics / Energy"
        if "ecology" in d or "nicholson" in d or "ricker" in d:
            return "Ecology"
        if "finance" in d or "merton" in d or "put_call" in d:
            return "Finance"
        return "Other"

    buckets = defaultdict(lambda: {"total": 0, "pass": 0})
    for r in rows:
        b = classify_domain(r["domain"])
        buckets[b]["total"] += 1
        if r.get("rubric_passed", 0) == r.get("rubric_total", 7):
            buckets[b]["pass"] += 1

    # sort by total desc
    sorted_buckets = sorted(buckets.items(), key=lambda x: -x[1]["total"])

    labels = [k for k, _ in sorted_buckets]
    totals = [v["total"] for _, v in sorted_buckets]
    passes = [v["pass"] for _, v in sorted_buckets]
    fails = [t - p for t, p in zip(totals, passes)]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9.0, 4.8),
                                     gridspec_kw={"width_ratios": [1.4, 1]})

    # ── left: stacked bar ──
    x = np.arange(len(labels))
    w = 0.55
    ax1.barh(x, passes, height=w, color="#27ae60", label="Rubric full-pass", edgecolor="white")
    ax1.barh(x, fails, left=passes, height=w, color="#e74c3c", label="Rubric failed", edgecolor="white")
    ax1.set_yticks(x)
    ax1.set_yticklabels(labels)
    ax1.set_xlabel("Scenario count")
    ax1.set_title("95-Scenario Rubric Pass/Fail by Domain")
    ax1.legend(loc="lower right", framealpha=0.9)
    # annotate totals
    for i, (t, p) in enumerate(zip(totals, passes)):
        pct = p / t * 100 if t > 0 else 0
        ax1.text(t + 0.3, i, f"{p}/{t} ({pct:.0f}%)", va="center", fontsize=8, color="#333")
    ax1.set_xlim(0, max(totals) * 1.25)

    # ── right: pie chart of pass rate ──
    total_all = sum(totals)
    pass_all = sum(passes)
    fail_all = total_all - pass_all
    colors = ["#27ae60", "#e74c3c"]
    wedges, texts, autotexts = ax2.pie(
        [pass_all, fail_all], labels=["Pass", "Fail"], colors=colors,
        autopct=lambda p: f"{p:.1f}%\n({int(round(p*total_all/100))})",
        startangle=90, textprops={"fontsize": 9},
        wedgeprops={"edgecolor": "white", "linewidth": 1},
    )
    for at in autotexts:
        at.set_color("white")
        at.set_fontweight("bold")
    ax2.set_title(f"Overall: {pass_all}/{total_all} scenarios pass rubric")

    fig.suptitle("SwarmLabs 95-Scenario Validation Distribution", fontsize=11, y=1.01)
    fig.savefig(FIG_DIR / "fig2_scenario_distribution.png")
    plt.close(fig)
    print(f"  fig2_scenario_distribution.png  ✓")


# ══════════════════════════════════════════════════════════════
# Figure 3: Extrapolation Failure Curve
# ══════════════════════════════════════════════════════════════
def fig3_extrapolation_failure():
    """G1 param-extrapolation: interp_rel vs extrap_rel for each scenario."""
    with open(ROOT / "data" / "generalization_bench.json") as f:
        g1 = json.load(f)["g1_param_extrapolation"]
    results = g1.get("results", [])
    tested = [r for r in results if r.get("na") is not True]
    n_total = g1.get("total", len(results))
    n_na = g1.get("not_applicable", sum(1 for r in results if r.get("na")))
    n_pass = g1.get("passed", sum(1 for r in tested if r.get("ok")))

    if not tested:
        print("  WARNING: no tested scenarios in g1")
        return

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9.5, 4.8),
                                     gridspec_kw={"width_ratios": [1.3, 1]})

    # ── left: scatter interp_rel vs extrap_rel (log-log) ──
    interp = [r["interp_rel"] for r in tested if r.get("interp_rel") and r["interp_rel"] > 0]
    extrap = [r["extrap_rel"] for r in tested if r.get("extrap_rel") and r["extrap_rel"] > 0]
    keys = [r["key"] for r in tested if r.get("interp_rel") and r["interp_rel"] > 0]

    ok_mask = [(r["interp_rel"] or 1e-9) > 0 and (r["extrap_rel"] or 1e-9) > 0
               and r.get("ok") for r in tested]
    ref_mask = [(r["interp_rel"] or 1e-9) > 0 and (r["extrap_rel"] or 1e-9) > 0
                and not r.get("ok") for r in tested]

    # only plot points where both are available
    valid = [r for r in tested
             if r.get("interp_rel", 0) > 0 and r.get("extrap_rel", 0) > 0]
    xi = [r["interp_rel"] for r in valid]
    xe = [r["extrap_rel"] for r in valid]
    xok = [r for r in valid if r.get("ok")]
    xref = [r for r in valid if not r.get("ok")]

    if xok:
        ax1.scatter([r["interp_rel"] for r in xok], [r["extrap_rel"] for r in xok],
                    c="#27ae60", s=60, edgecolors="white", linewidth=0.5,
                    label="PASS", zorder=3)
    if xref:
        ax1.scatter([r["interp_rel"] for r in xref], [r["extrap_rel"] for r in xref],
                    c="#e74c3c", s=60, edgecolors="white", linewidth=0.5,
                    label="REFUTED", zorder=3, marker="X")

    # 1:1 line
    lmin = min(min(xi), min(xe)) * 0.3
    lmax = max(max(xi), max(xe)) * 3
    ax1.plot([lmin, lmax], [lmin, lmax], "--", color="#888", linewidth=0.8, label="1:1 (no degradation)")
    # 2× threshold line
    ax1.plot([lmin, lmax], [lmin * 2, lmax * 2], ":", color="#e67e22", linewidth=0.8, label="2× threshold")

    ax1.set_xscale("log")
    ax1.set_yscale("log")
    ax1.set_xlabel("Interpolation RMSE$_{rel}$")
    ax1.set_ylabel("Extrapolation RMSE$_{rel}$")
    ax1.set_title(f"G1 Param Extrapolation: {len(xref)}/{len(valid)} scenarios REFUTED")
    ax1.legend(loc="upper left", framealpha=0.9)
    ax1.grid(True, alpha=0.3)

    # annotate worst ratios
    worst = sorted(xref, key=lambda r: -(r.get("ratio", 0)))[:3]
    for r in worst:
        ax1.annotate(f"{r['key']}\n{r.get('ratio', 0):.0f}×",
                     xy=(r["interp_rel"], r["extrap_rel"]),
                     textcoords="offset points", xytext=(8, 4),
                     fontsize=7, color="#c0392b",
                     arrowprops=dict(arrowstyle="-", linewidth=0.5, color="#c0392b"))

    # ── right: bar chart of ratio distribution ──
    ratios = [r.get("ratio", 0) for r in valid]
    ratios_sorted = sorted(ratios, reverse=True)

    if ratios_sorted:
        x = np.arange(len(ratios_sorted))
        colors = ["#e74c3c" if r > 2 else "#27ae60" for r in ratios_sorted]
        ax2.bar(x, ratios_sorted, color=colors, edgecolor="white", width=0.6)
        ax2.set_yscale("log")
        ax2.set_xticks(x)
        ax2.set_xticklabels(
            [r["key"].replace("_", "\n") for r in
             sorted(valid, key=lambda r: -r.get("ratio", 0))],
            rotation=45, ha="right", fontsize=6)
        ax2.axhline(y=2, color="#e67e22", linestyle=":", linewidth=1, label="2× threshold")
        ax2.set_ylabel("Extrap / Interp ratio")
        ax2.set_title("Extrapolation degradation ratio (log scale)")
        ax2.legend(loc="upper right")
        ax2.grid(True, alpha=0.3, axis="y")

    fig.suptitle(f"G1 Param Extrapolation Stress Test  ({n_pass} pass / {n_total} tested, {n_na} N/A)",
                 fontsize=10, y=1.01)
    fig.savefig(FIG_DIR / "fig3_extrapolation_failure.png")
    plt.close(fig)
    print(f"  fig3_extrapolation_failure.png  ✓")


# ══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("Generating paper-1 core figures ...")
    fig1_tdea_anchor_triple()
    fig2_scenario_distribution()
    fig3_extrapolation_failure()
    print("Done. Figures in:", FIG_DIR)
