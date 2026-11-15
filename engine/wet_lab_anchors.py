"""湿实验锚点库 —— 单一真源（与 engine/verification_gate.py 同级别）。

V&V 报告回答的是「这个代理模型是否忠实复现了 *oracle*」。但 oracle 本身是从哪里
来的？如果 oracle 的假想真实值偏离真实生物学两个数量级，那 R²=1.0 的 PASS 只是
**在错误的物理上完美**。本模块给出第二条独立证据链：

    V&V 自洽性（本报告）      ── 模型是否复现 oracle
    湿实验锚定（本模块）       ── oracle 是否符合真实实验

两者独立，任一缺失都不能宣称「可信」。

设计要点
--------
1. **不信任场景元数据的自述**。scenario["published"] 是字符串声明，可能互相矛盾
   （实测：microbio_monod 声称 Ks=0.22 g/L，micro_ecoli_glucose 声称 0.004 g/L，
   同一菌株同一底物差 55×）。所以所有 oracle 参数都用 _fit_monod() 从 oracle
   输出**拟合反推**，而不是抄字符串。
2. **区间而非点估计**。文献值本身跨多个数量级（见 literature 字段），
   所以判定用「落在区间内 / 在区间外但同数量级 / 数量级偏离」三档，
   而不是要求等于某个点值。
3. **诚实报告数量级偏离**。不修数据来迎合文献；偏离本身是最有价值的发现。

判定档位
--------
- AGREE        : oracle 值落在文献区间内
- NEAR         : 偏离区间但不超过 3×（一个数量级内）
- CONTRADICTED : 偏离超过 3× —— 需要人工介入，不能据此做实验决策
- UNANCHORED   : 该参数无独立文献锚点
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict

# ---------------------------------------------------------------------------
# 锚点表
# ---------------------------------------------------------------------------
# 每个锚点 = 一个可独立核实的实验量。区间来自**多来源**检索（见 sources），
# 而不是单一教材。sources 里标注了原始文献，可逐条复核。
#
# 三维证据判据（2026-09-24 加入）
# --------------------------------
# 一个锚点值可复用于新场景，必须**同时**满足三维匹配：
#   * experiment_system   — 实验体系（如 chemostat / batch_culture / thermal_inactivation）
#   * version             — 菌株 / 材料 / 版本（如 E_coli_K_12_variants / NF90 / Fe_cat）
#   * operating_condition — 工况（温度 / 压力 / 底物浓度等）
#
# 三维缺任一维 = 该锚点不可自动迁移，只能作人工参考。缺维度一律显式标记为
# UNANCHORED —— 不编造、不用 "unknown" 蒙混。这是本次改动的核心：把
# "证据能否复用"从"是否 wet-lab"一维判断，升级为三维交叉校验。
# 启发来源："质链 Agent"（商业航天任务技术状态与验证证据）
# 用「型号 × 版本 × 工况」三维判定旧验证结论能否复用；这套哲学对科研虚拟
# 实验同样适用。
@dataclass(frozen=True)
class Anchor:
    key: str
    label: str
    unit: str
    lo: float | None
    hi: float | None
    note: str
    sources: tuple[str, ...]
    # 锚定哪些场景；value_key 指向场景隐含参数的名字
    scenarios: tuple[str, ...]
    value_key: str = ""
    #: 该锚点是否足以支持「自动决策」（False = 仅供人工参考）
    decision_relevant: bool = True
    # --- 三维证据判据（2026-09-24）---
    #: 实验体系；未标注时 = UNANCHORED（禁止自动迁移）
    experiment_system: str = "UNANCHORED"
    #: 版本 / 菌株 / 材料；未标注时 = UNANCHORED
    version: str = "UNANCHORED"
    #: 工况（温度/压力/底物）；未标注时 = UNANCHORED
    operating_condition: str = "UNANCHORED"


#: 三维证据判据：未标注统一使用此常量；与 Grade.UNANCHORED 语义不同，
#: Grade.UNANCHORED 指"无文献区间"，此常量指"某维度缺标注"。
DIM_UNANCHORED = "UNANCHORED"


LIT_E_COLI_GLC_KS = (
    "Egli & Kovárová-Kovar 1998 MMBR 62:646-666 — E. coli/葡萄糖 Ks 因生理长期适应"
    "从 ~5 mg/L 降到 ~30 μg/L（167× 跨度，同一综述内）；并指出 μmax（批次，底物过量）"
    "与 Ks（恒化器，底物受控）**不可同时测定**",
    "Bader et al. 2013 'Chemical, Biochemical, and Engineering Terminology' 表 — "
    "E. coli 37°C 葡萄糖 μmax 0.8–1.4 h⁻¹, Ks 2–4 mg/L",
    "Ihssen et al. 2007（经 PMC5733555 Table 1 引）— K_G = 2.994e-6 mol/L = 0.54 mg/L",
    "BioProcessTools Monod 参数汇编 — E. coli K-12 37°C μmax 0.7–1.0 h⁻¹, Ks 2–4 mg/L",
)

LIT_E_COLI_GLC_MU = (
    "Bader et al. 表 — E. coli 37°C 葡萄糖 μmax 0.8–1.4 h⁻¹",
    "BioProcessTools — E. coli K-12 0.7–1.0 h⁻¹（最小培养基），BL21 0.8–1.2 h⁻¹（LB）",
    "Nanchen et al. 2006 AEM 72:1164 — MG1655 恒化器稀释率 >0.7 h⁻¹ 即洗出"
    "（→ μmax(MG1655, M9+1 g/L 葡萄糖) ≈ 0.6–0.7 h⁻¹）",
    "Senn 1994 BBA 1201:424 — E. coli B/r 富培养基 μmax ≈ 1.0 h⁻¹",
)

LIT_E_COLI_Maintenance = (
    "Pirt 1965 'Newer Studies in Microbiology' 经典表 — E. coli/葡萄糖 m_s = 0.057 g(gDW·h)⁻¹, "
    "Y_true = 2.27 g(gDW)⁻¹",
    "Nanchen et al. 2006 AEM 72:1164 Table 1 — MG1655 m_glc = 0.37 ± 0.09 mmol(gDW·h)⁻¹"
    "（= 0.067 g(gDW·h)⁻¹）；9 株 E. coli 跨度 0.11–0.38 mmol/(gDW·h)",
    "Nanchen 2006 — Y_max = 76 ± 3 gDW/mol 葡萄糖（= 0.42 g/g，注意与 Pirt 0.5 的差异）",
)

LIT_CEREVISIAE_GLC = (
    "Bader et al. 表 — S. cerevisiae 30°C 葡萄糖 μmax 0.5–0.6 h⁻¹, Ks 25 mg/L",
    "BioProcessTools — μmax 0.35–0.45 h⁻¹, Ks 25–180 mg/L（7.2× 跨度，由 Hxt6/7 与 "
    "Hxt1/3 转运体在不同浓度下的切换导致）",
    "Kompala et al. 2003 IECR 42:4175（经 Fogler 教材引）— 发酵途径 μmax=0.45 h⁻¹, Ks=0.05 g/L; "
    "葡萄糖氧化途径 μmax=0.33 h⁻¹, Ks=0.001 g/L",
    "Sorg et al. 2013 PLOS Genet（PMC3840306）— 自然分离株高通量显微成像 "
    "μmax 0.43–0.52 h⁻¹, Ks 0.1–0.2 mM = 10.8–21.6 mg/L",
)

LIT_THERMAL_ECOLI = (
    "GermEcological Meta 分析（actavetscand.biomedcentral.com/10.1186/1751-0147-44-1 表3）— "
    "E. coli D = 266 s @55°C, 39 s @60°C, 5.6 s @65°C, 0.4 s @72°C（95% CI 已给出）",
    "Morgan et al. 1988 JFP 51:132 — 人乳中 E. coli D60 = 31.5 s, z = 3.2°C",
    "Nechifor et al. 2013 JDS 117:4992 — 9 株 E. coli，D65 跨度 3.0–47.9 s（16× 株间差），"
    "z = 3.4–6.1°C",
    "Fellows 1988 食品工艺表 — E. coli D = 1.97 min @60°C（= 118 s），z = 4.67°C",
)


ANCHORS: dict[str, Anchor] = {
    "ecoli_glucose_Ks": Anchor(
        key="ecoli_glucose_Ks", label="E. coli / 葡萄糖 半饱和常数 Ks", unit="g/L",
        lo=0.000030, hi=0.005,   # 30 μg/L – 5 mg/L（Egli 1998 全区间）
        note=("真实生物学上 Ks 跨 167× 是可证的（生理适应），但**所有**独立来源都低于 "
              "5 mg/L；高于此值的场景参数与文献共识不符"),
        sources=LIT_E_COLI_GLC_KS, value_key="Ks",
        scenarios=("microbio_monod", "micro_ecoli_glucose", "micro_mle_ecoli_monod",
                   "micro_doe_ecoli_monod", "micro_uq_ecoli_monod", "micro_sobol_ecoli",
                   "micro_pirt_ecoli"),
        # 三维证据：Bader/BioProcessTools 明确 37°C；菌株为 K-12 变体（MG1655/B/r）；
        # 体系含恒化器（Nanchen 2006）与批次（Senn 1994）
        experiment_system="chemostat_or_batch", version="E_coli_K_12_variants",
        operating_condition="37C",
    ),
    "ecoli_glucose_mu_max": Anchor(
        key="ecoli_glucose_mu_max", label="E. coli / 葡萄糖 μmax", unit="h^-1",
        lo=0.60, hi=1.4,
        note="最小培养基 MG1655 约 0.6–0.7；富培养基 / B/r 可达 1.0–1.4",
        sources=LIT_E_COLI_GLC_MU, value_key="mu_max",
        scenarios=("microbio_monod", "micro_ecoli_glucose", "micro_mle_ecoli_monod",
                   "micro_doe_ecoli_monod", "micro_uq_ecoli_monod", "micro_sobol_ecoli",
                   "micro_pirt_ecoli"),
        # 三维证据：Bader 表 37°C；μmax 主要由批次法测得（底物过量）
        experiment_system="batch_culture", version="E_coli_K_12_variants",
        operating_condition="37C",
    ),
    "ecoli_glucose_maintenance": Anchor(
        key="ecoli_glucose_maintenance", label="E. coli / 葡萄糖 维持系数 m_s", unit="g/(gDW·h)",
        lo=0.051, hi=0.083,
        note=("按场景自称的菌株 MG1655 取值：Nanchen 2006 0.067 ± 0.016。"
              "若放宽到全部 9 株 E. coli，观测跨度为 0.020–0.068，"
              "即 m_s=0.02 恰落在物种观测下限 —— 判 NEAR 而非 CONTRADICTED。"
              "仅锚定 micro_pirt_ecoli：其他 Monod 场景的 m 虽写在 PAPER_PARAMS 里，"
              "但不进入 μ(S) 表达式，对 oracle 输出零影响，无法从输出反推。"),
        sources=LIT_E_COLI_Maintenance, value_key="m",
        scenarios=("micro_pirt_ecoli",),
        # 三维证据：维持系数只能从恒化器数据反推（Pirt 1965, Nanchen 2006 均恒化器）
        experiment_system="chemostat", version="E_coli_K_12_variants",
        operating_condition="37C",
    ),
    "cerevisiae_glucose_mu_max": Anchor(
        key="cerevisiae_glucose_mu_max", label="S. cerevisiae / 葡萄糖 μmax", unit="h^-1",
        lo=0.31, hi=0.60,
        note="0.31（低值）– 0.60（Bader 表）；Kompala 发酵途径 0.45",
        sources=LIT_CEREVISIAE_GLC, value_key="mu_max",
        scenarios=("micro_cerevisiae_ethanol", "micro_crabtree_cerevisiae"),
        experiment_system="batch_culture", version="S_cerevisiae",
        operating_condition="30C",
    ),
    "cerevisiae_glucose_Ks": Anchor(
        key="cerevisiae_glucose_Ks", label="S. cerevisiae / 葡萄糖 Ks", unit="g/L",
        lo=0.001, hi=0.180,
        note="1–180 mg/L，7.2× 跨度源于葡萄糖转运体切换（Hxt6/7 高亲和 vs Hxt1/3 低亲和）",
        sources=LIT_CEREVISIAE_GLC, value_key="Ks",
        scenarios=("micro_cerevisiae_ethanol",),
        experiment_system="batch_culture", version="S_cerevisiae",
        operating_condition="30C",
    ),
    "ecoli_thermal_D60": Anchor(
        key="ecoli_thermal_D60", label="E. coli 热死灭 D 值 @60°C", unit="s",
        lo=25, hi=132,
        note="31.5 s（人乳，Morgan 1988）– 118 s（Fellows 表）– 132 s（单株最大值，Nechifor 2013）",
        sources=LIT_THERMAL_ECOLI, value_key="D60",
        scenarios=("micro_thermal_death",),
        # 三维证据：热死灭实验（in vitro thermal inactivation），
        # 菌株多变（9 株 E. coli），D60 明确 60°C 定义
        experiment_system="thermal_inactivation_in_vitro",
        version="E_coli_various_strains",
        operating_condition="60C",
    ),

    # =========================================================================
    # 工艺论文数据锚点（源自 swarmlabs_real_paper_data.json）
    # 每个锚点区间 = 该域 30 条实测行的 [min, max]，跨多篇工艺论文综合。
    # 大多数锚点当前无匹配场景（scenarios=()），是**能力就绪**的文档锚点：
    # 一旦在相应域新增场景，直接接入即可。
    # =========================================================================
    "ammonia_haber_bosch_yield": Anchor(
        key="ammonia_haber_bosch_yield", label="Haber-Bosch 氨合成转化率 @ 400-500°C 100bar",
        unit="%",
        lo=5.0, hi=25.0,
        note="31 条 Haber-Bosch Fe 催化剂实测行（400-500°C，100 bar）；温度升高 100°C 转化率翻倍。",
        sources=("swarmlabs_real_paper_data.json: ammonia (31 rows, Haber-Bosch Fe cat)",),
        value_key="", scenarios=(), decision_relevant=False,
        experiment_system="fixed_bed_reactor", version="Fe_catalyst",
        operating_condition="400_500C_100bar",
    ),
    "adsorption_acid_carbon_capacity": Anchor(
        key="adsorption_acid_carbon_capacity", label="活性炭对亚甲基蓝的吸附容量 @ 25°C",
        unit="mg/g",
        lo=25.0, hi=280.0,
        note="30 条活性炭-亚甲基蓝实测行；50 mg/L 底物浓度下 45 mg/g，200 mg/L 下 150 mg/g，"
             "1000 mg/L 下 280 mg/g（Langmuir 上渐近）。",
        sources=("swarmlabs_real_paper_data.json: adsorption (30 rows, activated carbon/MB)",),
        value_key="", scenarios=("chem_langmuir",), decision_relevant=True,
        experiment_system="batch_adsorption", version="activated_carbon",
        operating_condition="25C",
    ),
    "bioreactor_ecoli_biomass": Anchor(
        key="bioreactor_ecoli_biomass", label="E. coli 批次反应器生物量（g/L, 37°C pH7）",
        unit="g/L",
        lo=1.0, hi=12.0,
        note="30 条批次实测行；24h 时间窗内生物量 1-12 g/L，随接种量与时间单调。",
        sources=("swarmlabs_real_paper_data.json: bioreactor (30 rows, e_coli batch)",),
        value_key="", scenarios=(), decision_relevant=False,
        experiment_system="batch_culture", version="E_coli",
        operating_condition="37C_pH7",
    ),
    "enzyme_lipase_activity": Anchor(
        key="enzyme_lipase_activity", label="脂肪酶活 @ pH 7.5 温度响应",
        unit="UM/mg",
        lo=0.4, hi=9.0,
        note="30 条脂肪酶实测行；最适 37°C 4.5 UM/mg，25°C 2.2，50°C 2.8（失活前）。",
        sources=("swarmlabs_real_paper_data.json: enzyme (30 rows, lipase)",),
        value_key="", scenarios=(), decision_relevant=False,
        experiment_system="enzymatic_assay", version="lipase",
        operating_condition="25_37_50C_pH7.5",
    ),
    "corrosion_carbon_steel_rate": Anchor(
        key="corrosion_carbon_steel_rate", label="碳钢腐蚀速率 @ 25°C (3.5%NaCl / 1M H2SO4)",
        unit="mm/y",
        lo=0.17, hi=1.39,
        note="30 条碳钢腐蚀实测行；3.5% NaCl 25°C 15 mm/y 0.17 mm/y，1M H2SO4 120 mm/y 1.39 mm/y（8× 加速）。",
        sources=("swarmlabs_real_paper_data.json: corrosion (30 rows, carbon steel)",),
        value_key="", scenarios=(), decision_relevant=False,
        experiment_system="immersion_corrosion", version="carbon_steel",
        operating_condition="25C_various_electrolytes",
    ),
    "photoFenton_removal_efficiency": Anchor(
        key="photoFenton_removal_efficiency", label="光-Fenton 亚甲基蓝去除率 @ 25°C 60 min",
        unit="%",
        lo=40.0, hi=96.0,
        note="30 条光-Fenton 去除率实测行；无光 60 min 40-50%，加可见光升至 85-95%。",
        sources=("swarmlabs_real_paper_data.json: photoFenton (30 rows, methylene blue)",),
        value_key="", scenarios=(), decision_relevant=False,
        experiment_system="photoFenton_decoding", version="MB_substrate",
        operating_condition="25C_60min",
    ),
    "membrane_nf_rejection": Anchor(
        key="membrane_nf_rejection", label="NF90 纳滤膜对 NaCl/Na2SO4 的截留率",
        unit="%",
        lo=92.0, hi=98.0,
        note="30 条 NF90 纳滤实测行；1000 mg/L NaCl @5bar 92%，Na2SO4 98%（二价离子截留更高）。",
        sources=("swarmlabs_real_paper_data.json: membrane (30 rows, NF90)",),
        value_key="", scenarios=(), decision_relevant=False,
        experiment_system="membrane_filtration", version="NF90",
        operating_condition="25C_5bar",
    ),
    "pcr_amplification_efficiency": Anchor(
        key="pcr_amplification_efficiency", label="PCR 扩增效率 @ 95°C 解链 35 循环",
        unit="%",
        lo=82.0, hi=96.0,
        note="30 条 PCR 实测行；模板 10^4 copies 22.5 Cq 效率 95%，10^2 copies 29.1 Cq 效率 94%。",
        sources=("swarmlabs_real_paper_data.json: pcr (30 rows)",),
        value_key="", scenarios=(), decision_relevant=False,
        experiment_system="PCR_thermal_cycling", version="unknown_primer_set",
        operating_condition="95C_denat_35_cycles",
    ),
    "perovskite_solar_pce": Anchor(
        key="perovskite_solar_pce", label="钙钛矿太阳能电池功率转换效率",
        unit="%",
        lo=8.5, hi=23.5,
        note="30 条钙钛矿太阳能电池实测行；薄膜制备工艺差异导致 PCE 8.5-23.5% 跨度。",
        sources=("swarmlabs_real_paper_data.json: perovskite (30 rows)",),
        value_key="", scenarios=(), decision_relevant=False,
        # note 明确说"薄膜制备工艺差异"—— 意味着 version 不可归一，
        # 且 note 未给温度/光照强度 → 三维都保留 UNANCHORED（诚实）
        experiment_system="thin_film_solar_cell", version="UNANCHORED",
        operating_condition="UNANCHORED",
    ),
    "battery_energy_density": Anchor(
        key="battery_energy_density", label="锂离子电池体积能量密度",
        unit="Wh/L",
        lo=95.0, hi=160.0,
        note="29 条锂电实测行；三元正极 140 Wh/L 均值，硅负极 95-120 Wh/L。",
        sources=("swarmlabs_real_paper_data.json: battery (29 rows)",),
        value_key="", scenarios=(), decision_relevant=False,
        experiment_system="lab_cell_assembly",
        version="UNANCHORED",  # 正极化学有信息，但电池体系未指明 → 保守 UNANCHORED
        operating_condition="UNANCHORED",
    ),
    "distillation_separation_factor": Anchor(
        key="distillation_separation_factor", label="精馏塔分离因子（乙醇-水）",
        unit="dimensionless",
        lo=0.79, hi=0.98,
        note="30 条乙醇精馏实测行；95% 塔板数下分离因子 0.79-0.98。",
        sources=("swarmlabs_real_paper_data.json: distillation (30 rows)",),
        value_key="", scenarios=(), decision_relevant=False,
        experiment_system="distillation_column",
        version="UNANCHORED",  # 塔板材质/填充未指明
        operating_condition="UNANCHORED",  # 温度/压力未指明
    ),
    "flocculation_removal_rate": Anchor(
        key="flocculation_removal_rate", label="絮凝去除率 @ 30 min 沉降",
        unit="%",
        lo=75.0, hi=96.0,
        note="30 条絮凝实测行；PAM 5 mg/L 75-85%，PAC 30 mg/L 90-96%。",
        sources=("swarmlabs_real_paper_data.json: flocculation (30 rows)",),
        value_key="", scenarios=(), decision_relevant=False,
        experiment_system="jar_test_flocculation",
        version="PAM_or_PAC_coagulant",
        operating_condition="30min_settling",
    ),
    "combustion_efficiency_gcc": Anchor(
        key="combustion_efficiency_gcc", label="燃气轮机燃烧效率 @ 100% 负荷",

        unit="%",
        lo=88.0, hi=95.0,
        note="30 条燃气轮机实测行；1800-3080 °C 燃烧室温度下效率 88-95%。",
        sources=("swarmlabs_real_paper_data.json: combustion (30 rows)",),
        value_key="", scenarios=(), decision_relevant=False,
        experiment_system="gas_combustion_turbine", version="UNANCHORED",
        operating_condition="1800_3080C_100pct_load",
    ),
}


# ---------------------------------------------------------------------------
# 三维证据完整性
# ---------------------------------------------------------------------------
#: 三维字段名 —— 顺序固定，供 evidence_completeness 与门禁共用
THREE_DIMS: tuple[str, ...] = (
    "experiment_system", "version", "operating_condition")


def _dim_value(a: Anchor, dim: str) -> str:
    """安全取三维字段值；缺失/None 一律返回 UNANCHORED。"""
    try:
        v = getattr(a, dim)
    except AttributeError:
        return DIM_UNANCHORED
    if v is None or (isinstance(v, str) and not v.strip()):
        return DIM_UNANCHORED
    return str(v)


def evidence_completeness(a: Anchor) -> dict:
    """一个锚点的三维完整性。

    返回：
      dimensions    = {实验体系, 版本, 工况} 每维取值（或 UNANCHORED）
      filled        = 已填充的维度数（0-3）
      fully_anchored= True 当且仅当三维全填
    """
    dims = {d: _dim_value(a, d) for d in THREE_DIMS}
    filled = sum(1 for v in dims.values() if v != DIM_UNANCHORED)
    return {
        "dimensions": dims,
        "filled": filled,
        "of": 3,
        "fully_anchored": filled == 3,
        "missing": [d for d, v in dims.items() if v == DIM_UNANCHORED],
    }


def anchors_3d_coverage() -> dict:
    """整库三维覆盖情况——用于门禁、报告与 /v3/anchors 汇总。

    关键指标：`decision_relevant_not_fully_anchored` —— 声明能自动决策的锚点
    里，有几条三维不全。这一列表不为空说明**决策依据不充分**，是显式红灯。
    """
    total = len(ANCHORS)
    by_completeness = {0: 0, 1: 0, 2: 0, 3: 0}
    for a in ANCHORS.values():
        c = evidence_completeness(a)
        by_completeness[c["filled"]] += 1
    dr = [a for a in ANCHORS.values() if a.decision_relevant]
    dr_not_full = [
        {"key": a.key, "missing": evidence_completeness(a)["missing"]}
        for a in dr if not evidence_completeness(a)["fully_anchored"]
    ]
    return {
        "total_anchors": total,
        "by_completeness": by_completeness,
        "decision_relevant_count": len(dr),
        "decision_relevant_not_fully_anchored": dr_not_full,
        "dim_unanchored_marker": DIM_UNANCHORED,
    }


# ---------------------------------------------------------------------------
# 判定
# ---------------------------------------------------------------------------
class Grade(str):
    AGREE = "AGREE"
    NEAR = "NEAR"
    CONTRADICTED = "CONTRADICTED"
    UNANCHORED = "UNANCHORED"


ALL_GRADES: tuple[str, ...] = (
    Grade.AGREE, Grade.NEAR, Grade.CONTRADICTED, Grade.UNANCHORED)


def grade_against_anchor(value: float | None, a: Anchor) -> tuple[Grade, float, str]:
    """返回 (档位, 相对偏离倍数, 说明)。

    相对偏离倍数 = max(value/hi, lo/value, 1.0)，即 value 落在区间外时是最近的
    边界值的多少倍。1.0 = 恰好落在区间内。
    """
    if value is None or not (a.lo is not None and a.hi is not None):
        if value is None:
            return (Grade.UNANCHORED, float("nan"),
                    f"{a.key}：oracle 输出中无法反推出「{a.label}」（该参数不进入本场景的"
                    f"闭式表达式，或对输出零影响），故不作锚定判定")
        return (Grade.UNANCHORED, float("nan"),
                f"{a.key}：文献区间未定义（lo/hi 缺失），无法判定")
    if a.lo <= value <= a.hi:
        return Grade.AGREE, 1.0, f"{value:g} 落在文献区间 [{a.lo:g}, {a.hi:g}] {a.unit} 内"
    if value > a.hi:
        ratio = value / a.hi
    else:
        ratio = a.lo / value if value > 0 else float("inf")
    if ratio <= 3.0:
        return Grade.NEAR, float(ratio), (
            f"{value:g} 偏离文献区间 [{a.lo:g}, {a.hi:g}] {a.unit} 约 {ratio:.2f}×（同数量级）")
    return Grade.CONTRADICTED, float(ratio), (
        f"{value:g} 偏离文献区间 [{a.lo:g}, {a.hi:g}] {a.unit} 达 {ratio:.1f}×（跨数量级）")


def worst_grade(grades: list[tuple[Grade, float, str]]) -> Grade:
    order = {Grade.UNANCHORED: 0, Grade.AGREE: 1, Grade.NEAR: 2, Grade.CONTRADICTED: 3}
    return max(grades, key=lambda g: order[g[0]])[0] if grades else Grade.UNANCHORED


#: 档位 → 门禁动作（与 engine/verification_gate.py 的 GATE_MAP 同风格）
ANCHOR_GATE_MAP = {
    Grade.AGREE: "PROCEED",
    Grade.NEAR: "PROCEED_WITH_HUMAN_CHECK",
    Grade.CONTRADICTED: "BLOCK_AUTONOMOUS_ACTION",
    Grade.UNANCHORED: "NO_WET_LAB_ANCHOR",
}


# ---------------------------------------------------------------------------
# 场景锚定结果
# ---------------------------------------------------------------------------
@dataclass
class ScenarioAnchor:
    scenario_key: str
    params: dict = field(default_factory=dict)      # 反推出的隐含参数
    checks: list = field(default_factory=list)      # [{anchor_key, value, grade, ratio, why}]
    grade: Grade = Grade.UNANCHORED
    gate: str = "NO_WET_LAB_ANCHOR"

    def to_json(self) -> dict:
        def _num(v):
            if v is None:
                return None
            try:
                f = float(v)
            except (TypeError, ValueError):
                return v
            # JSON 不允许 NaN/Inf —— 一律落为 null，避免下游解析失败
            if not math.isfinite(f):
                return None
            return f

        return {
            "scenario_key": self.scenario_key,
            "grade": self.grade,
            "gate": self.gate,
            "implied_params": {k: _num(v) for k, v in self.params.items()},
            "checks": [{"anchor_key": c["anchor_key"],
                        "value": _num(c.get("value")),
                        "unit": c.get("unit"), "lit_range": c.get("lit_range"),
                        "grade": c["grade"],
                        "ratio": (None if c.get("ratio") is None
                                  else _num(c["ratio"])),
                        "why": c.get("why")}
                       for c in self.checks],
        }


def summarize(entries: list[ScenarioAnchor]) -> dict:
    counts = {g: 0 for g in ALL_GRADES}
    for e in entries:
        counts[e.grade] += 1
    return {
        "total": len(entries),
        "grade_counts": counts,
        "anchor_gate_map": ANCHOR_GATE_MAP,
        "blocked": [e.scenario_key for e in entries if e.grade == Grade.CONTRADICTED],
    }


def anchors_index() -> list[dict]:
    out = []
    for a in ANCHORS.values():
        d = asdict(a)
        d["sources"] = list(d["sources"])
        d["scenarios"] = list(d["scenarios"])
        # 三维证据完整性 —— 让下游能一眼看出这个锚点可不可自动迁移
        d["evidence"] = evidence_completeness(a)
        out.append(d)
    return out
