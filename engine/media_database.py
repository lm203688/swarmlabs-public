"""培养基数据库 (Culture Media Database)。

对标：CultureMech (10K+ 培养基配方，CC0 协议) / ATCC 培养基目录
核心：每种培养基的完整配方（原料、浓度、pH、温度），关联适用菌株。

数据来源：
  - 教科书经典配方：Brock's Microbiology / Shuler & Kargi
  - DSMZ / ATCC 公开培养基档案
  - 文献：De Man Rogosa Sharpe (1960), MRS (1960), YPD (经典), LB (1944)
"""

from __future__ import annotations

import json
import os

MEDIA_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "required": ["media_id", "name", "type", "ingredients", "pH"],
    "properties": {
        "media_id": {"type": "string", "pattern": "^[a-z_]+$"},
        "name": {"type": "string"},
        "type": {"enum": ["broth", "agar", "powder", "liquid", "minimal", "complex"]},
        "ingredients": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "amount_gL": {"type": "number"},
                    "source": {"type": "string"},
                },
            },
        },
        "pH": {"type": "number"},
        "temperature_opt_C": {"type": "number"},
        "sterilization": {"type": "string"},
        "applicable_strains": {"type": "array", "items": {"type": "string"}},
        "refs": {"type": "array", "items": {"type": "string"}},
    },
}


MEDIA: list[dict] = [
    {
        "media_id": "LB",
        "name": "Luria-Bertani Broth",
        "type": "complex",
        "ingredients": [
            {"name": "Tryptone (peptone)", "amount_gL": 10.0},
            {"name": "Yeast extract", "amount_gL": 5.0},
            {"name": "NaCl", "amount_gL": 10.0},
        ],
        "pH": 7.0, "temperature_opt_C": 37.0, "sterilization": "autoclave_121C_20min",
        "applicable_strains": ["ecoli_K12_MG1655", "ecoli_B121_BL21", "pputida_MT2"],
        "refs": ["Sambrook & Russell 2001 Molecular Cloning"],
    },
    {
        "media_id": "TB_terrific",
        "name": "Terrific Broth (protein expression)",
        "type": "complex",
        "ingredients": [
            {"name": "Bacto tryptone", "amount_gL": 12.0},
            {"name": "Bacto yeast extract", "amount_gL": 24.0},
            {"name": "Glycerol", "amount_gL": 4.0},
            {"name": "K2HPO4", "amount_gL": 12.0},
            {"name": "KH2PO4", "amount_gL": 4.0},
            {"name": "NaCl", "amount_gL": 0.1},
        ],
        "pH": 7.0, "temperature_opt_C": 37.0, "sterilization": "autoclave_121C_20min",
        "applicable_strains": ["ecoli_B121_BL21", "ecoli_K12_MG1655"],
        "refs": ["Trend & Stinchcomb 1976 Mol Gen Genet"],
    },
    {
        "media_id": "M9_minimal",
        "name": "M9 Minimal Salt Medium",
        "type": "minimal",
        "ingredients": [
            {"name": "Na2HPO4", "amount_gL": 6.0},
            {"name": "KH2PO4", "amount_gL": 3.0},
            {"name": "NaCl", "amount_gL": 0.5},
            {"name": "NH4Cl", "amount_gL": 1.0},
            {"name": "MgSO4_2H2O", "amount_gL": 0.024},
            {"name": "CaCl2", "amount_gL": 0.01},
            {"name": "Glucose (optional)", "amount_gL": 1.0},
        ],
        "pH": 7.2, "temperature_opt_C": 37.0, "sterilization": "autoclave_121C_20min",
        "applicable_strains": ["ecoli_K12_MG1655", "ecoli_B121_BL21", "pputida_MT2"],
        "refs": ["Sambrook & Russell 2001 Molecular Cloning"],
    },
    {
        "media_id": "YPD",
        "name": "Yeast Peptone Dextrose (YPD)",
        "type": "complex",
        "ingredients": [
            {"name": "Yeast extract", "amount_gL": 10.0},
            {"name": "Peptone (bacto)", "amount_gL": 20.0},
            {"name": "Glucose (dextrose)", "amount_gL": 20.0},
        ],
        "pH": 5.0, "temperature_opt_C": 30.0, "sterilization": "autoclave_121C_20min",
        "applicable_strains": ["scerevisiae_ATCC26010", "cpastoris_GS115"],
        "refs": ["Rose & Broach 1994 Methods Enzymol 253:61"],
    },
    {
        "media_id": "BMMY",
        "name": "Buffered Methanol Medium (Pichia expression)",
        "type": "minimal",
        "ingredients": [
            {"name": "1M HCl", "amount_gL": 0.06},
            {"name": "K2HPO4", "amount_gL": 0.6},
            {"name": "KH2PO4", "amount_gL": 1.0},
            {"name": "Na2SO4", "amount_gL": 0.16},
            {"name": "MgSO4_2H2O", "amount_gL": 0.004},
            {"name": "NaOH (adjust pH)", "amount_gL": 0.06},
            {"name": "CaCl2", "amount_gL": 0.0004},
            {"name": "CoCl2", "amount_gL": 0.00016},
            {"name": "CuSO4_5H2O", "amount_gL": 0.0008},
            {"name": "MnSO4_5H2O", "amount_gL": 0.002},
            {"name": "ZnSO4_7H2O", "amount_gL": 0.016},
            {"name": "FeSO4_7H2O", "amount_gL": 0.002},
            {"name": "K2SO4", "amount_gL": 0.16},
            {"name": "Biocide (500X)", "amount_gL": 0.5},
            {"name": "Methanol (100% v/v)", "amount_gL": 1.0},
        ],
        "pH": 6.0, "temperature_opt_C": 25.0, "sterilization": "filter_0.22um",
        "applicable_strains": ["cpastoris_GS115"],
        "refs": ["Invitrogen Pichia Expression Manual"],
    },
    {
        "media_id": "MRS_broth",
        "name": "De Man, Rogosa and Sharpe (MRS) Broth",
        "type": "complex",
        "ingredients": [
            {"name": "Peptone", "amount_gL": 10.0},
            {"name": "Beef extract", "amount_gL": 10.0},
            {"name": "Yeast extract", "amount_gL": 5.0},
            {"name": "Glucose", "amount_gL": 20.0},
            {"name": "Acetic acid", "amount_gL": 0.12},
            {"name": "Tween 80", "amount_gL": 2.0},
            {"name": "KH2PO4", "amount_gL": 2.0},
            {"name": "NaAc (sodium acetate)", "amount_gL": 5.0},
            {"name": "MgSO4_7H2O", "amount_gL": 0.2},
            {"name": "MnSO4_4H2O", "amount_gL": 0.05},
        ],
        "pH": 6.2, "temperature_opt_C": 37.0, "sterilization": "autoclave_121C_15min",
        "applicable_strains": ["llactis_NZ9000", "lactobacillus_plantarum_148"],
        "refs": ["De Man, Rogosa & Sharpe 1960 J Appl Bacteriol 23:130"],
    },
    {
        "media_id": "GAM_broth",
        "name": "GAM Broth (Growth medium for lactic acid bacteria)",
        "type": "complex",
        "ingredients": [
            {"name": "Polypeptone", "amount_gL": 10.0},
            {"name": "Caspase peptone", "amount_gL": 10.0},
            {"name": "Beef extract", "amount_gL": 3.0},
            {"name": "Yeast extract", "amount_gL": 5.0},
            {"name": "Glucose", "amount_gL": 5.0},
            {"name": "Tween 80", "amount_gL": 2.0},
            {"name": "DL-cysteine-HCl·H2O", "amount_gL": 0.5},
            {"name": "Na2HPO4_7H2O", "amount_gL": 2.0},
            {"name": "KCl", "amount_gL": 0.1},
            {"name": "NaAc·3H2O", "amount_gL": 2.0},
            {"name": "MgSO4_7H2O", "amount_gL": 0.02},
            {"name": "MnSO4_4H2O", "amount_gL": 0.005},
        ],
        "pH": 6.2, "temperature_opt_C": 30.0, "sterilization": "autoclave_121C_15min",
        "applicable_strains": ["llactis_NZ9000", "lactobacillus_plantarum_148"],
        "refs": ["Nakagawa et al. 2006 Food Rev Int 22:45"],
    },
    {
        "media_id": "Weimberg_medium",
        "name": "Weimberg Medium (ABE fermentation)",
        "type": "complex",
        "ingredients": [
            {"name": "Soymilk powder", "amount_gL": 30.0},
            {"name": "Corn steep liquor (CSL)", "amount_gL": 50.0},
            {"name": "Yeast extract", "amount_gL": 1.0},
            {"name": "Glucose", "amount_gL": 40.0},
            {"name": "MgSO4_7H2O", "amount_gL": 0.1},
            {"name": "MnSO4_4H2O", "amount_gL": 0.001},
            {"name": "FeSO4_7H2O", "amount_gL": 0.001},
            {"name": "CaCO3 (buffer)", "amount_gL": 10.0},
        ],
        "pH": 6.2, "temperature_opt_C": 37.0, "sterilization": "autoclave_121C_20min",
        "applicable_strains": ["clostridium_acetobutylicum_ATCC824"],
        "refs": ["Weimberg 1894 Z Physiol Chem 28:648"],
    },
    {
        "media_id": "TY_broth",
        "name": "TY Broth (Rhizobium)",
        "type": "complex",
        "ingredients": [
            {"name": "Tryptone", "amount_gL": 5.0},
            {"name": "Yeast extract", "amount_gL": 3.0},
            {"name": "NaCl", "amount_gL": 0.01},
        ],
        "pH": 7.0, "temperature_opt_C": 28.0, "sterilization": "autoclave_121C_15min",
        "applicable_strains": ["rhizobium_leguminosarum_biovar"],
        "refs": ["Broughton & Kado 1976 Methods Microbiol 9:915"],
    },
    {
        "media_id": "MS_medium",
        "name": "Streptomyces MS Medium (antibiotic production)",
        "type": "complex",
        "ingredients": [
            {"name": "Sucrose", "amount_gL": 20.0},
            {"name": "Corn steep liquor", "amount_gL": 20.0},
            {"name": "MgSO4_7H2O", "amount_gL": 0.05},
            {"name": "MnSO4_5H2O", "amount_gL": 0.002},
            {"name": "FeCl3_6H2O", "amount_gL": 0.004},
            {"name": "NaCl", "amount_gL": 0.5},
        ],
        "pH": 7.0, "temperature_opt_C": 28.0, "sterilization": "autoclave_121C_20min",
        "applicable_strains": ["streptomyces_coelicolor_A3"],
        "refs": ["Wolstadt 1969 Antibiotics 22:711"],
    },
    {
        "media_id": "SH_buffer",
        "name": "Halobacterium SH Buffer",
        "type": "complex",
        "ingredients": [
            {"name": "Saturated NaCl", "amount_gL": 350.0},
            {"name": "MgSO4_7H2O", "amount_gL": 3.0},
            {"name": "CaCl2_2H2O", "amount_gL": 0.2},
            {"name": "KCl", "amount_gL": 0.4},
            {"name": "Na2HPO4", "amount_gL": 0.12},
            {"name": "Glucose", "amount_gL": 1.0},
            {"name": "Peptone", "amount_gL": 2.0},
        ],
        "pH": 6.8, "temperature_opt_C": 42.0, "sterilization": "autoclave_121C_15min",
        "applicable_strains": ["halobacterium_salinarum_NRC1"],
        "refs": ["Kushner 1978 Bacteriol Rev 42:111"],
    },
    {
        "media_id": "TSB_thermophile",
        "name": "Thermophile Tryptic Soy Broth",
        "type": "complex",
        "ingredients": [
            {"name": "Tryptic soy broth (base)", "amount_gL": 30.0},
            {"name": "Yeast extract", "amount_gL": 5.0},
        ],
        "pH": 7.5, "temperature_opt_C": 65.0, "sterilization": "autoclave_121C_15min",
        "applicable_strains": ["thermus_aquaticus_YT1"],
        "refs": ["Bettlach et al. 1984 Eur J Biochem 145:185"],
    },
    {
        "media_id": "BG11_medium",
        "name": "BG-11 Medium (cyanobacteria)",
        "type": "minimal",
        "ingredients": [
            {"name": "NaNO3", "amount_gL": 1.5},
            {"name": "K2HPO4", "amount_gL": 0.04},
            {"name": "MgSO4_7H2O", "amount_gL": 0.025},
            {"name": "CaCl2_2H2O", "amount_gL": 0.0036},
            {"name": "FeSO4_7H2O", "amount_gL": 0.0025},
            {"name": "Citric acid (Fe chelator)", "amount_gL": 0.0025},
            {"name": "Na2-EDTA", "amount_gL": 0.0005},
            {"name": "Na2CO3", "amount_gL": 0.017},
            {"name": "Trace metal solution (A5)", "amount_gL": 0.05},
            {"name": "Trace mineral solution (B5)", "amount_gL": 0.05},
            {"name": "Vitamin B12 (0.01mg/mL)", "amount_gL": 0.1},
        ],
        "pH": 10.0, "temperature_opt_C": 25.0, "sterilization": "autoclave_121C_15min",
        "applicable_strains": ["synechococcus_elongatus_BP1"],
        "refs": ["Rippka et al. 1979 J Gen Microbiol 111:1"],
    },
    {
        "media_id": "Zymomonas_broth",
        "name": "Zymomonas Mobilis Growth Medium",
        "type": "complex",
        "ingredients": [
            {"name": "Yeast extract", "amount_gL": 5.0},
            {"name": "Peptone", "amount_gL": 5.0},
            {"name": "MgSO4_7H2O", "amount_gL": 0.4},
            {"name": "NH4Cl", "amount_gL": 0.4},
            {"name": "Na2HPO4_12H2O", "amount_gL": 3.0},
            {"name": "KH2PO4", "amount_gL": 1.5},
            {"name": "CaCl2_2H2O", "amount_gL": 0.01},
            {"name": "MnSO4_4H2O", "amount_gL": 0.005},
            {"name": "FeSO4_7H2O", "amount_gL": 0.001},
            {"name": "Glucose", "amount_gL": 20.0},
        ],
        "pH": 6.5, "temperature_opt_C": 30.0, "sterilization": "autoclave_121C_15min",
        "applicable_strains": ["zmobilis_ZM4"],
        "refs": ["Pai & Lapidus 1977 Appl Microbiol 33:136"],
    },
    {
        "media_id": "CGM_broth",
        "name": "Corynebacterium Glutamicum Medium",
        "type": "complex",
        "ingredients": [
            {"name": "Yeast extract", "amount_gL": 5.0},
            {"name": "Bacto-peptone", "amount_gL": 10.0},
            {"name": "MgSO4_7H2O", "amount_gL": 0.12},
            {"name": "NH4Cl", "amount_gL": 1.0},
            {"name": "Glucose", "amount_gL": 20.0},
            {"name": "K2HPO4", "amount_gL": 0.5},
            {"name": "NaCl", "amount_gL": 0.3},
            {"name": "CaCO3", "amount_gL": 2.0},
        ],
        "pH": 7.0, "temperature_opt_C": 32.0, "sterilization": "autoclave_121C_15min",
        "applicable_strains": ["cglutamicum_RUT212"],
        "refs": ["Puhler & Kalinowski 2004 Microbiology 150:857"],
    },
    {
        "media_id": "Ruminococcus_medium",
        "name": "Ruminococcus Medium (anaerobic, rumen bacteria)",
        "type": "complex",
        "ingredients": [
            {"name": "Peptone", "amount_gL": 5.0},
            {"name": "Yeast extract", "amount_gL": 10.0},
            {"name": "Hemin", "amount_gL": 0.005},
            {"name": "Meningococcus extract", "amount_gL": 1.0},
            {"name": "Tiol acetate", "amount_gL": 0.2},
            {"name": "Tween 40", "amount_gL": 1.0},
            {"name": "Vitamin mix", "amount_gL": 0.5},
            {"name": "Cellulose (cellulose filter disks)", "amount_gL": 5.0},
            {"name": "NaHCO3", "amount_gL": 2.0},
        ],
        "pH": 6.8, "temperature_opt_C": 39.0, "sterilization": "anaerobic_121C_15min",
        "applicable_strains": ["bfermentans_ATCC19078"],
        "refs": ["Cotta & Wolin 1984 Appl Environ Microbiol 48:318"],
    },
    {
        "media_id": "Acetobacter_broth",
        "name": "Acetobacter Growth Medium",
        "type": "complex",
        "ingredients": [
            {"name": "D-glucose", "amount_gL": 15.0},
            {"name": "Peptone", "amount_gL": 2.0},
            {"name": "MgSO4_7H2O", "amount_gL": 0.05},
            {"name": "MnSO4_4H2O", "amount_gL": 0.001},
            {"name": "CaCl2_2H2O", "amount_gL": 0.01},
            {"name": "KH2PO4", "amount_gL": 1.0},
            {"name": "K2HPO4", "amount_gL": 1.0},
        ],
        "pH": 6.0, "temperature_opt_C": 30.0, "sterilization": "autoclave_121C_15min",
        "applicable_strains": ["acalcoaceticus_ATCC15668"],
        "refs": ["Rogness et al. 1961 Biotech Bioeng 3:1"],
    },
    {
        "media_id": "Methanobacterium_broth",
        "name": "MSB-1 Medium (Methanobacterium)",
        "type": "minimal",
        "ingredients": [
            {"name": "NH4Cl", "amount_gL": 0.5},
            {"name": "KH2PO4", "amount_gL": 0.2},
            {"name": "NaCl", "amount_gL": 0.25},
            {"name": "KCl", "amount_gL": 0.1},
            {"name": "MgSO4_7H2O", "amount_gL": 0.01},
            {"name": "CaCl2_2H2O", "amount_gL": 0.001},
            {"name": "FeSO4_7H2O", "amount_gL": 0.001},
            {"name": "CO2/N2/H2 (80:15:5 v/v/v)", "amount_gL": 0.0},
        ],
        "pH": 6.7, "temperature_opt_C": 37.0, "sterilization": "anaerobic_121C_15min",
        "applicable_strains": ["mtrichosporium_OB3b"],
        "refs": ["Zehnder & Trüper 1976 Arch Microbiol 107:227"],
    },
    {
        "media_id": "BYPG",
        "name": "Biomass Yeast Peptone Glycerol (Pichia pre-culture)",
        "type": "complex",
        "ingredients": [
            {"name": "Biomass (C14)", "amount_gL": 13.4},
            {"name": "Yeast extract", "amount_gL": 3.0},
            {"name": "Glycerol", "amount_gL": 1.0},
        ],
        "pH": 6.0, "temperature_opt_C": 30.0, "sterilization": "autoclave_121C_15min",
        "applicable_strains": ["cpastoris_GS115"],
        "refs": ["Invitrogen Pichia Expression Manual"],
    },
    {
        "media_id": "Zhu_auto_induction",
        "name": "Zhu Autoinduction Medium (DE3 expression)",
        "type": "complex",
        "ingredients": [
            {"name": "Glucose", "amount_gL": 2.0},
            {"name": "Glycerol", "amount_gL": 2.0},
            {"name": "Lactose", "amount_gL": 0.5},
            {"name": "Tryptone", "amount_gL": 4.0},
            {"name": "Yeast extract", "amount_gL": 2.0},
            {"name": "MgSO4_7H2O", "amount_gL": 0.2},
        ],
        "pH": 7.0, "temperature_opt_C": 20.0, "sterilization": "autoclave_121C_15min",
        "applicable_strains": ["ecoli_B121_BL21"],
        "refs": ["Zhu et al. 2005 Protein Sci 14:1056"],
    },
]


def get_media_registry() -> list[dict]:
    """返回完整培养基注册表"""
    return list(MEDIA)


def get_media_registry_path() -> str:
    data_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
    return os.path.join(data_dir, "media_database.json")


def export_media_database():
    """导出培养基数据库到 JSON"""
    path = get_media_registry_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(MEDIA, f, ensure_ascii=False, indent=2)
    return path


def get_media(media_id: str) -> dict | None:
    for m in MEDIA:
        if m["media_id"] == media_id:
            return m
    return None


def get_strain_media(strain_id: str) -> list[dict]:
    """获取适用某菌株的所有培养基"""
    return [m for m in MEDIA if strain_id in m.get("applicable_strains", [])]


def media_summary() -> dict:
    by_type = {}
    for m in MEDIA:
        t = m["type"]
        by_type[t] = by_type.get(t, 0) + 1
    return {"total_media": len(MEDIA), "by_type": by_type, "media_ids": [m["media_id"] for m in MEDIA]}


if __name__ == "__main__":
    path = export_media_database()
    summary = media_summary()
    print("=" * 70)
    print(f"培养基数据库 — 导出完成")
    print(f"文件: {path}")
    print(f"总数: {summary['total_media']} 种")
    print(f"类型分布: {summary['by_type']}")
    print()
    for m in MEDIA:
        n_ingredients = len(m["ingredients"])
        print(f'  {m["media_id"]:20s} {m["name"]:40s} '
              f'pH={m["pH"]} T={m["temperature_opt_C"]}°C '
              f'({n_ingredients} 成分)')
    print("=" * 70)
