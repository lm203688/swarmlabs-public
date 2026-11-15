"""领域分类 taxonomy —— 围绕「使用场景」组织整个项目。

两个来源：
  - ACTIVE  ：已用统一引擎 SurrogateEngine 验证的领域
              （来自 scripts/paper_validation_loop.SCENARIOS，含 UQ + 主动学习）
  - PENDING ：155 引擎目录（scripts/validate_models.js ENGINE_CATEGORIES）
              映射的 9 大类，等待接入统一内核——化学/能源/材料/制药…
              接入方式 = 用发表基准做 gold + UQ，即「真补齐」（非信任启发式）

这是「项目围绕使用场景把资源理顺」的落地：每个领域都是统一内核的一个应用实例。
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# 155 引擎目录 → 9 大类（来源：scripts/validate_models.js ENGINE_CATEGORIES）
# 这些是大厂/产业真实使用场景，接入统一内核后即成为「已验证领域」。
# ---------------------------------------------------------------------------
PENDING_CATEGORIES = {
    "Chemistry": {
        "count": 11,
        "representative": ["suzuki", "heck", "sonogashira", "hydrogenation", "esterification"],
        "note": "反应产率高斯响应面可作 gold；Suzuki 等已有发表基准",
    },
    "Environment": {
        "count": 18,
        "representative": ["photocatalysis", "co2", "water_splitting", "pyrolysis"],
        "note": "污染物降解动力学、碳转化效率",
    },
    "Energy": {
        "count": 12,
        "representative": ["battery_li_ion", "fuel_cell", "electrolysis", "ammonia"],
        "note": "电池/电解效率曲线、容量衰减",
    },
    "Materials": {
        "count": 24,
        "representative": ["deposition_cvd", "sintering", "polymerization_addition",
                           "crystallization_antisolvent"],
        "note": "工艺-性能映射、晶体生长",
    },
    "Biology": {
        "count": 6,
        "representative": ["fermentation_ethanol", "fermentation_lactic", "enzyme"],
        "note": "与已验证微生物场景（Monod/抑制）互补",
    },
    "Separation": {
        "count": 31,
        "representative": ["distillation", "membrane", "extraction"],
        "note": "分离纯度响应面、级联优化",
    },
    "Analysis": {
        "count": 8,
        "representative": ["titration", "chromatography", "mass_spec"],
        "note": "仪器响应/校准曲线",
    },
    "Pharma": {
        "count": 24,
        "representative": ["dissolution", "pk_modeling", "sterilization"],
        "note": "释放动力学、药代动力学",
    },
    "Electrochemistry": {
        "count": 4,
        "representative": ["electroplating", "electrodialysis"],
        "note": "电镀/电渗析效率",
    },
    "NeuroRobotics": {
        "count": 0,
        "representative": ["neural_decoding", "soft_robotics_sim2real", "safe_legged_control", "myoelectric_prosthesis"],
        "note": "神经机器人学垂直桶（2026-09-16 开）：脑机接口/神经解码、软体机器人 sim-to-real、安全腿足控制、肌电假肢共享控制。待接入统一内核方式=用 G1-G7 gap 发现驱动的真实观测做 gold+UQ。",
    },
}
TOTAL_PENDING = sum(c["count"] for c in PENDING_CATEGORIES.values())  # 138

CATEGORY_LABELS = {
    "optimization": "代理建模 / 优化",
    "biology": "生物 / 微生物动力学",
    "pde": "物理信息 / PDE",
    "llm": "LLM / 缩放律",
    "chemistry": "化学 / 反应与吸附动力学",
}


def active_domains():
    """已用统一引擎验证的领域（来自 scenario_registry 单一真源）。

    2026-10-08 修 bug：原实现只读 ``P.SCENARIOS``（基础 82），
    **漏掉** ``scenarios_extended`` 贡献的 13 个，
    于是日报长期打印「已验证 10 个领域」——真实值是 10 个规范学科 / 95 场景
    （`scenario_registry.disciplines()` 归并口径；对外「14 学科」为口径膨胀，
    2026-10-28 修正）。
    2026-10-10（P1-5）改为**直接委派 engine.scenario_registry**，
    彻底消除三套注册表漂移——装配只写一次。
    """
    from engine.scenario_registry import scenario_list
    scenarios = scenario_list()

    out = {}
    for s in scenarios:
        cat = s.get("category", "uncategorized")
        out.setdefault(cat, []).append({
            "key": s["key"],
            "domain": s["domain"],
            "dim": len(s["bounds"]),
            "auto_ls": s.get("auto_ls", False),
            "published": s.get("published", ""),
            "references": s.get("references", []),
        })
    return out


def domain_tree():
    """合并 ACTIVE + PENDING，生成统一领域树（一个体系的全景）。"""
    active = active_domains()
    tree = {
        "active": active,
        "pending": PENDING_CATEGORIES,
        "totals": {
            "active_domains": sum(len(v) for v in active.values()),
            "pending_engines": TOTAL_PENDING,
            "pending_categories": len(PENDING_CATEGORIES),
        },
    }
    return tree


if __name__ == "__main__":
    import json

    t = domain_tree()
    print(f"ACTIVE 已验证领域: {t['totals']['active_domains']} 个")
    for cat, items in t["active"].items():
        print(f"  [{cat}] {CATEGORY_LABELS.get(cat, cat)}: {len(items)} 个")
        for it in items:
            print(f"      - {it['key']}  ({it['dim']}D)")
    print(f"\nPENDING 待接入引擎: {t['totals']['pending_engines']} 个 / {t['totals']['pending_categories']} 大类")
    for cat, info in t["pending"].items():
        print(f"  [{cat}] {info['count']} 引擎  e.g. {', '.join(info['representative'][:3])}")
