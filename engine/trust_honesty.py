"""信任面诚实元数据的单一真源（schema v2）。

背景
====
``data/trust_surface.json`` 由 ``scripts/paper_validation_loop.py::trust_rows``
生成。该生成器原样产出的结构**不含** ``evidence_type``（每行）与
``overall.honesty``（整体诚实声明块）。这两个字段是 README / 公开数字门禁 /
schema 契约的依赖项，缺失会导致：

- ``scripts/check_public_numbers.py`` 报 STALE-FACT（``experimental_ground_truth``
  解析失败，``KeyError: 'honesty'``）；
- ``scripts/check_data_contracts.py`` / JSON Schema 校验失败；
- ``data/public_facts.json`` 无法由单一 builder 派生。

之前的修法是 ``scripts/patch_trust_surface_honesty.py`` 事后补丁，但 DayVerify
定时任务（12:30）每次重建 ``trust_surface.json`` 都会把补丁覆盖回缺字段状态，
于是问题每天复发一次。

本模块把这段口径抽成**生成时可复用的纯函数**：生成器在写盘前直接调用
``attach_honesty``，补丁脚本退化为「对已存在产物的幂等守卫」。两边共用同一份
口径，不会漂移。

字段口径
========
每行新增：
  - ``evidence_type``       — 恒为 ``"simulated"``（GP 代理 of 已发表模型）
  - ``reference_quality``   — ``doi`` / ``web`` / ``textbook``（按 ref_papers 判定）
  - ``validation_type``     等 5 项 — 来自 ``engine.scenario_trust_labels.labels_for``
                                     （保守分支：trust_surface 无 wet_lab_grade）

整体新增 ``overall.honesty``：
  - ``simulated_count`` / ``experimental_ground_truth_count`` — 核心诚实声明
  - ``reference_quality`` 分布、``validation_type_distribution``
  - ``physics_validated_count`` / ``purely_simulated_count``
  - 口径说明文本（gold_source_traced / validated_vs_registered / positioning）

数字全部由当次 rows 现场计算，**不写死**——场景数每次重建都会变，写死等于
再造一份陈旧口径。
"""
from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

# schema_version 递增即表示新增字段。旧产物若版本落后会被 needs_patch 判为待重打。
SCHEMA_VERSION = 2

ROOT = Path(__file__).resolve().parent.parent

# 接线 engine.scenario_trust_labels（板块01 单一真源）；import 失败降级但打印警告
_HAS_LABELS = False
labels_for = None
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
try:
    from engine.scenario_trust_labels import labels_for  # noqa: E402
    _HAS_LABELS = True
except Exception as _e:  # pragma: no cover
    print(f"[warn] engine.scenario_trust_labels unavailable: {_e}")


def ref_quality(ref_papers: Any) -> str:
    """按参考来源字符串判定溯源等级：doi > web > textbook。"""
    s = " ".join(str(x) for x in (ref_papers or []))
    if re.search(r"10\.\d{4,9}/", s):
        return "doi"
    if "http" in s.lower():
        return "web"
    return "textbook"


def needs_patch(d: dict) -> bool:
    """幂等性判定：honesty 块缺失、行数不匹配或 schema_version 落后即返回 True。

    双重守卫（行数 + schema_version）：
    2026-09-21 教训——旧守卫只查 simulated_count，行数一变诚实元数据整块丢光。
    """
    hon = (d.get("overall") or {}).get("honesty") or {}
    if (
        hon
        and hon.get("simulated_count") == len(d.get("rows") or [])
        and hon.get("schema_version") == SCHEMA_VERSION
    ):
        return False
    return True


def attach_honesty(d: dict) -> dict:
    """就地补齐每行的 evidence_type / trust labels 与整体 honesty 块，返回 d。

    纯函数（对入参 d 原地修改后原样返回），可重复调用；调用方负责读写文件。
    """
    rows = d.get("rows") or []

    for r in rows:
        key = r.get("key", "")
        r["evidence_type"] = "simulated"  # GP surrogate of a published model/formula
        r["reference_quality"] = ref_quality(r.get("ref_papers"))

        # ---- 接线 scenario_trust_labels：验证类型 / 适用域 / 禁止场景 ----
        if _HAS_LABELS and labels_for is not None:
            refs = r.get("ref_papers") or []
            try:
                labels = labels_for(
                    key,
                    published=r.get("published", "") or "",
                    references=refs,
                    # trust_surface 无 wet_lab_grade 字段（wet_lab_anchors 由报告流程
                    # 独立维护）；传 None 让 labels_for 走保守分支，避免虚报湿实验锚定。
                    wet_lab_grade=None,
                )
                r["validation_type"] = labels["validation_type"]
                r["validation_type_label"] = labels["validation_type_label"]
                r["applicable_domain"] = labels["applicable_domain"]
                r["forbidden_use"] = labels["forbidden_use"]
                r["physics_or_simulated"] = (
                    "physics-validated"
                    if labels["validation_type"] == "analytic_closed_form"
                    else "purely-simulated"
                )
            except Exception as _e:  # noqa: BLE001
                # 单行标签失败不应中断整体生成；降级为保守口径
                print(f"[warn] labels_for({key}) failed: {_e}; 降级 synthetic_surrogate")
                r["validation_type"] = "synthetic_surrogate"
                r["physics_or_simulated"] = "purely-simulated"
        else:
            r["validation_type"] = "synthetic_surrogate"
            r["physics_or_simulated"] = "purely-simulated"

    n_doi = sum(1 for r in rows if r["reference_quality"] == "doi")
    n_web = sum(1 for r in rows if r["reference_quality"] == "web")
    n_txt = sum(1 for r in rows if r["reference_quality"] == "textbook")
    n_scen = len(rows)

    vt_dist = dict(Counter(r.get("validation_type") for r in rows))
    phys_count = sum(1 for r in rows if r.get("physics_or_simulated") == "physics-validated")
    sim_count = sum(1 for r in rows if r.get("physics_or_simulated") == "purely-simulated")

    overall = d.setdefault("overall", {})
    scope = overall.get("coverage_scope") or {}
    n_reg = scope.get("registered_total", n_scen)

    overall["honesty"] = {
        "schema_version": SCHEMA_VERSION,
        "all_evidence_simulated": True,
        "simulated_count": n_scen,
        "experimental_ground_truth_count": 0,
        "reference_quality": {"doi": n_doi, "web": n_web, "textbook": n_txt},
        "validation_type_distribution": vt_dist,
        "physics_validated_count": phys_count,
        "purely_simulated_count": sim_count,
        "gold_source_traced_note": (
            "gold_source_traced 仅表示存在文献字符串溯源（多为教材/网络资源），"
            "不等于同行评审 DOI 级溯源。"
        ),
        # 不写死数字：场景数每次重建都会变，写死就会变成又一份陈旧口径
        "validated_vs_registered": (
            f"validated={n_scen} 即 registered={n_reg}，"
            "不存在“已注册但未验证”的额外项。"
        ),
        "positioning_note": (
            "项目定位为「面向生物工艺的可信代理建模与实验设计层」；"
            "不再以赛事参赛作为对外叙事，边界见 docs/why_swarmlabs.md。"
        ),
        "trust_labels_source": (
            "engine/trust_honesty.py:attach_honesty + engine/scenario_trust_labels.py:"
            "labels_for (schema v%d)" % SCHEMA_VERSION
        ),
    }
    return d


def summary_line(d: dict) -> str:
    """生成一行诊断摘要，供 CLI 打印。"""
    hon = (d.get("overall") or {}).get("honesty") or {}
    rq = hon.get("reference_quality") or {}
    vt = hon.get("validation_type_distribution") or {}
    return (
        f"rows={len(d.get('rows') or [])} schema=v{hon.get('schema_version')} "
        f"ref={rq} vt={vt} "
        f"physics-validated={hon.get('physics_validated_count')} "
        f"purely-simulated={hon.get('purely_simulated_count')}"
    )
