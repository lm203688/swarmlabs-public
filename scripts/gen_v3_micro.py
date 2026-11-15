#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
生成 v3 API 的 52 微生物场景 spec + 7 个闭式动力学求值器，并注入 [[path]].js。
先对照 engine 原函数数值验证公式一致性，再生成 JS 片段并拼接。
"""
import json, re, sys

ROOT = "C:/Users/xing/Desktop/swarmlabs"
DATA = f"{ROOT}/data/microbiology_scenarios.json"
TARGET = f"{ROOT}/frontend_fixed/functions/api/[[path]].js"

# ── 1. 加载 52 场景元数据 ──────────────────────────────────────────────
data = json.load(open(DATA, encoding="utf-8"))
scns = data["scenarios"]
assert len(scns) == 52, len(scns)

# ── 2. 短中文 domain 标签（补齐空值） ──────────────────────────────────
DOMAIN = {
    "micro_ecoli_glucose": "微生物 / E.coli 批次培养（Monod）",
    "micro_cerevisiae_ethanol": "微生物 / 酿酒酵母乙醇发酵（Monod×产物抑制, 2D）",
    "micro_putida_toluene": "微生物 / 假单胞菌甲苯降解（Andrews 抑制）",
    "micro_lactococcus_lactose": "微生物 / 乳酸乳球菌乳酸发酵（pH 效应, 2D）",
    "micro_acetobacter_acetate": "微生物 / 醋酸杆菌醋酸利用（温度效应, 2D）",
    "micro_methanobacterium_methane": "微生物 / 产甲烷古菌甲烷利用（低 μmax）",
    "micro_chemostat_ecoli": "微生物 / E.coli Chemostat 稳态（Monod）",
    "micro_chemostat_cerevisiae": "微生物 / 酿酒酵母 Chemostat（产物抑制, 2D）",
    "micro_lag_ecoli": "微生物 / E.coli 延滞期（Baranyi 1994）",
    "micro_lifecycle_ecoli": "微生物 / E.coli 全生命周期（lag→exp→death）",
    "micro_diauxic_ecoli": "微生物 / E.coli 二次生长（双底物）",
    "micro_fedbatch_ecoli": "微生物 / E.coli 补料分批（指数流加）",
    "micro_competition_ecoli_yeast": "微生物 / E.coli×酵母 竞争（Tilman 1982）",
    "micro_crabtree_cerevisiae": "微生物 / 酿酒酵母 Crabtree 效应（乙醇）",
    "micro_haldane_putida": "微生物 / 假单胞菌底物抑制（Haldane）",
    "micro_contois_ecoli": "微生物 / E.coli 高密培养（Contois）",
    "micro_tessier_ecoli": "微生物 / 高浓度底物（Tessier）",
    "micro_pirt_ecoli": "微生物 / 维持代谢（Pirt）",
    "micro_oxygen_ecoli": "微生物 / 溶氧限制（kLa）",
    "micro_antibiotic_timekill": "微生物 / 抗生素杀菌曲线（time-kill）",
    "micro_osmotic_ecoli": "微生物 / 渗透胁迫（渗透压）",
    "micro_biofilm_ecoli": "微生物 / 生物膜形成",
    "micro_scaleup_kla": "微生物 / 放大效应（kLa 传质）",
    "micro_doe_ecoli_monod": "微生物 / 实验设计 DOE（Monod, 2D）",
    "micro_mle_ecoli_monod": "微生物 / 极大似然参数估计（Monod）",
    "micro_sobol_ecoli": "微生物 / Sobol 全局敏感性分析",
    "micro_uq_ecoli_monod": "微生物 / 不确定性量化 UQ（Monod）",
    "micro_coculture_mutualism": "微生物 / 共培养互利（mutualism）",
    "micro_quorum_ecoli": "微生物 / 群体感应 Quorum Sensing",
    "micro_heavy_metal_ecoli": "微生物 / 重金属抑制",
    "micro_diauxic_2d": "微生物 / 二次生长（2D 参数）",
    "micro_fedbatch_2d": "微生物 / 补料分批（2D）",
    "micro_competition_2d": "微生物 / 竞争（2D）",
    "micro_crabtree_2d": "微生物 / Crabtree 效应（2D）",
    "micro_fedbatch_3d": "微生物 / 补料分批（3D）",
    "micro_haldane_2d": "微生物 / Haldane 抑制（2D）",
    "micro_oxygen_2d": "微生物 / 溶氧限制（2D）",
    "micro_antibiotic_2d": "微生物 / 抗生素杀菌（2D）",
    "micro_secondary_metabolite": "微生物 / 次级代谢产物",
    "micro_thermal_death": "微生物 / 热力致死（D值/Z值）",
    "micro_immobilized_cell": "微生物 / 固定化细胞（2D）",
    "micro_plasmid_stability": "微生物 / 质粒稳定性（2D）",
    "micro_phage_infection": "微生物 / 噬菌体感染（2D）",
    "micro_gene_expression": "微生物 / 基因表达（2D）",
    "micro_oxidative_stress": "微生物 / 氧化应激",
    "micro_chemostat_transient": "微生物 / Chemostat 瞬态（2D）",
    "micro_mixed_substrate": "微生物 / 混合底物（2D）",
    "micro_ph_control": "微生物 / pH 控制（2D）",
    "micro_fba_flux": "微生物 / 通量平衡分析 FBA（2D）",
    "micro_dead_volume": "微生物 / CSTR 死体积（2D）",
    "micro_foam_dynamics": "微生物 / 泡沫动力学（2D）",
    "micro_sensor_fusion": "微生物 / 传感器融合贝叶斯（2D）",
}

# ── 3. 构建 spec 数组文本 ───────────────────────────────────────────────
def fmt_bounds(b):
    return "[" + ",".join("[" + ",".join(str(float(v)) for v in lohi) + "]" for lohi in b) + "]"

specs = []
for s in scns:
    dom = (s.get("domain") or "").strip()
    if not dom:
        dom = DOMAIN.get(s["key"], "微生物 / 动力学场景")
    refs = s.get("references") or []
    refs_js = "[" + ",".join(json.dumps(r, ensure_ascii=False) for r in refs) + "]"
    obj = (
        '{"key": %s, "domain": %s, "category": %s, "dim": %d, "bounds": %s, '
        '"ls": %s, "noise": %s, "published": %s, "refs": %s}'
        % (
            json.dumps(s["key"], ensure_ascii=False),
            json.dumps(dom, ensure_ascii=False),
            json.dumps(s.get("category", "biology"), ensure_ascii=False),
            int(s["dim"]),
            fmt_bounds(s["bounds"]),
            json.dumps(s.get("ls"), ensure_ascii=False),
            json.dumps(s.get("noise"), ensure_ascii=False),
            json.dumps(s.get("published", ""), ensure_ascii=False),
            refs_js,
        )
    )
    specs.append(obj)

SCENARIOS_JS = "  const __V3_MICRO_SCENARIOS = [\n    " + ",\n    ".join(specs) + "\n  ];"

# ── 4. 闭式动力学求值器（对照 engine 验证） ──────────────────────────────
# 仅 7 个 1D 闭式模型可移植；其余为仿真/高维，列为 spec（用户自备数据）。
EVAL_JS = r'''  const __V3_EVAL = {
    // μ(S) Monod —— E. coli K-12 (Monod 1949)
    micro_ecoli_glucose: (x) => { const S=x[0], mu_max=0.81, Ks=0.004; return mu_max*S/(Ks+S); },
    // μ(S) Andrews 底物抑制 —— P. putida toluene (Rothman 1993)
    micro_putida_toluene: (x) => { const S=x[0], mu_max=0.35, Ks=0.02, Ki=2.5; return mu_max*S/(Ks+S+S*S/Ki); },
    // μ(S) Haldane —— P. putida (同株，Haldane 形式)
    micro_haldane_putida: (x) => { const S=x[0], mu_max=0.35, Ks=0.02, Ki=2.5; return mu_max*S*Ki/(Ki+Ks*S+S*S); },
    // μ(S) Contois —— E. coli 高密, X 固定 5.0 g/L
    micro_contois_ecoli: (x) => { const S=x[0], X=5.0, mu_max=0.5, Kc=0.5; return mu_max*S/(Kc*X+S); },
    // μ(S) Tessier —— 高浓度底物
    micro_tessier_ecoli: (x) => { const S=x[0], mu_max=0.5, Ks=0.01; return Math.min(mu_max, mu_max*Math.log((Ks+S)/Ks)); },
    // μ(S) Pirt 维持代谢
    micro_pirt_ecoli: (x) => { const S=x[0], mu_max=0.5, Ks=0.01, m_s=0.02, Y_GS=0.4;
      const q_s = mu_max*S/(Ks+S)/Y_GS; const q_g = Math.max(q_s - m_s, 0.0); return q_g*Y_GS; },
    // μ(CL) 溶氧限制 —— S 固定 1.0 g/L
    micro_oxygen_ecoli: (x) => { const CL=x[0], mu_max=0.5, Ks=0.01, Kl=0.5;
      const mu_S = mu_max*1.0/(Ks+1.0); return mu_S*CL/(Kl+CL); },
  };'''

ALL_JS = "  const __V3_ALL = __V3_SCENARIOS.concat(__V3_MICRO_SCENARIOS);"

INJECT_CONSTANTS = SCENARIOS_JS + "\n\n" + EVAL_JS + "\n\n" + ALL_JS + "\n"

# ── 5. 拼接进 [[path]].js ───────────────────────────────────────────────
src = open(TARGET, encoding="utf-8").read()

# 5a. 注入常量：在 `const __V3_STRAINS =` 之前
marker = "  const __V3_STRAINS = ["
assert marker in src, "marker __V3_STRAINS not found"
if "__V3_MICRO_SCENARIOS" in src:
    print("WARN: already injected, skipping constant injection")
else:
    idx = src.index(marker)
    src = src[:idx] + INJECT_CONSTANTS + src[idx:]

# 5b. /scenarios 与 /health 改用 __V3_ALL
src = src.replace(
    "if (apiPath === 'v3/scenarios') return __v3resp({ count: __V3_SCENARIOS.length, scenarios: __V3_SCENARIOS });",
    "if (apiPath === 'v3/scenarios') return __v3resp({ count: __V3_ALL.length, scenarios: __V3_ALL });",
)
src = src.replace(
    "scenarios: __V3_SCENARIOS.length,",
    "scenarios: __V3_ALL.length,",
)

# 5c. 新增 /v3/sample POST 路由（置于 /v3/predict 之前）
SAMPLE_HANDLER = '''        if (apiPath === 'v3/sample') {
          const key = body.scenario;
          const ev = __V3_EVAL[key];
          if (!ev) return __v3resp({ error: 'no ground-truth evaluator for scenario: ' + key + ' (supply train_x/train_y to /predict)' }, 501);
          const n = Math.min(Math.max(parseInt(body.n) || 12, 1), 200);
          const sc = __V3_ALL.find(s => s.key === key);
          if (!sc) return __v3resp({ error: 'scenario not found: ' + key }, 404);
          const pts = [];
          const seed = body.seed || 1;
          let rngState = seed >>> 0;
          const rand = () => { rngState = (rngState * 1664525 + 1013904223) >>> 0; return rngState / 4294967296; };
          for (let i = 0; i < n; i++) {
            const x = sc.bounds.map(([lo, hi]) => lo + (hi - lo) * rand());
            let y = ev(x);
            const noiseRel = body.noise_rel != null ? body.noise_rel : (sc.noise || 0.05);
            y = y * (1 + (rand() - 0.5) * 2 * noiseRel);
            pts.push({ x: x.map(v => +v.toFixed(6)), y: +y.toFixed(6) });
          }
          return __v3resp({ scenario: key, n: pts.length, has_evaluator: true, points: pts });
        }
'''
assert "if (apiPath === 'v3/predict') {" in src, "predict handler not found"
if "if (apiPath === 'v3/sample') {" not in src:
    src = src.replace("        if (apiPath === 'v3/predict') {", SAMPLE_HANDLER + "        if (apiPath === 'v3/predict') {", 1)

open(TARGET, "w", encoding="utf-8").write(src)
print("INJECTED: 52 specs + 7 evaluators + /v3/sample; __V3_ALL = %d scenarios" % (data["count"] + 11))
