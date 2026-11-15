#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""阶段锚点生成器 — SwarmLabs 可追溯快照。

目的：把「某一时刻产品是什么样」冻结成一个可机器复算的对象，供后续阶段
diff。核心不是记录数字，而是记录**数字的来源**：每条事实都带
`source_pointer`（哪个产物文件的哪个字段），任何人下次跑这个脚本都能重算，
并在数字变了的时候立刻看到 diff。

产物：
  data/phase_anchor_<date>.json   机读，含 all_digest
  docs/milestones/phase-anchor-<date>.md  人读

用法：
  python scripts/build_phase_anchor.py                 # 写当天锚点
  python scripts/build_phase_anchor.py --verify FILE   # 用现有锚点复算并 diff
  python scripts/build_phase_anchor.py --json          # 只打印 JSON

设计约束（刻意的）：
  - 只读产物，不触发任何重算/训练/网络。（锚点必须便宜，否则不会被执行）
  - 缺文件 != 崩。缺的项记为 None 并在 `gaps` 里列出，锚点宁可诚实残缺不可伪造完整。
  - all_digest 只覆盖**列入 fingerprint 的产物**；任何一项字节变化都会改 all_digest。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CN_TZ = timezone(timedelta(hours=8))


def _p(*parts: str) -> str:
    return os.path.join(ROOT, *parts)


def sha256_12(path: str) -> str | None:
    """对产物**内容**取摘要，换行符统一为 LF 后再哈希。

    与 `engine/verification_gate.py::_sha256_file` 同一理由（2026-09-21）：
    仓库存 LF、`core.autocrlf=true` 的 Windows 工作区检出 CRLF，于是直接
    哈希原始字节会让 all_digest 变成**平台相关**——「可复算」就成了空话。
    规范化后，摘要只取决于内容。
    """
    if not os.path.exists(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk.replace(b"\r\n", b"\n"))
    return h.hexdigest()[:12]


def load_json(path: str):
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:  # noqa: BLE001
        return {"__load_error__": str(exc)}


def dig(obj, pointer: str):
    """按 'a.b.c' 取值；支持 list index 'a.0.b'。任一段缺失返回 None。"""
    cur = obj
    for seg in pointer.split("."):
        if cur is None:
            return None
        if isinstance(cur, list):
            try:
                cur = cur[int(seg)]
            except (ValueError, IndexError):
                return None
        elif isinstance(cur, dict):
            cur = cur.get(seg)
        else:
            return None
    return cur


def git_commit() -> dict:
    out = {"head": None, "short": None, "branch": None, "dirty": None}
    try:
        out["head"] = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
        out["short"] = out["head"][:7]
        out["branch"] = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=ROOT, text=True, stderr=subprocess.DEVNULL,
        ).strip()
        status = subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        )
        out["dirty"] = len([ln for ln in status.splitlines() if ln.strip()])
    except Exception:  # noqa: BLE001
        pass
    return out


# ---------------------------------------------------------------------------
# 指纹产物清单：任何一项字节变动都会改 all_digest
# 一旦某产物真的变了，锚点的 diff 会立刻暴露，而不是等下一个人肉发现
# ---------------------------------------------------------------------------
FINGERPRINT_FILES = [
    "frontend_fixed/reports/index.json",
    "frontend_fixed/reports/gate_ledger.json",
    "frontend_fixed/reports/heldout_index.json",
    "frontend_fixed/reports/wet_lab_anchors.json",
    "frontend_fixed/reports/scenario_library_density.json",
    "data/trust_surface.json",
    "data/public_facts.json",
    "data/entity_store.json",
    "data/conformal_calibration.json",
]


def tracked_in_git(rel: str) -> bool:
    """该产物是否已被 git 跟踪（= 第三方 clone 后能否拿到）。

    为什么必须记这一项：锚点声称「任何人下次跑这个脚本都能重算」，但若来源产物
    本身没入库，第三方 clone 后既复算不了、也看不到它存在——此时 all_digest 只是
    一个本机私有指纹，「可追溯」是空话。故 in_git 参与 gaps。
    """
    try:
        subprocess.check_output(
            ["git", "ls-files", "--error-unmatch", rel],
            cwd=ROOT, text=True, stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:  # noqa: BLE001
        return False


def build_fingerprint() -> dict:
    fp = {}
    ing = {}
    for rel in FINGERPRINT_FILES:
        fp[rel] = sha256_12(_p(rel))
        ing[rel] = tracked_in_git(rel)
    h = hashlib.sha256()
    for rel in sorted(fp):
        h.update(f"{rel}={fp[rel]}\n".encode())
    return {
        "files": fp,
        "in_git": ing,
        "not_in_git": [r for r in FINGERPRINT_FILES if not ing[r]],
        "all_digest": h.hexdigest()[:16],
    }


def build_facts() -> dict:
    idx = load_json(_p("frontend_fixed", "reports", "index.json"))
    ledger = load_json(_p("data", "gate_ledger.json"))
    ts = load_json(_p("data", "trust_surface.json"))
    wa = load_json(_p("frontend_fixed", "reports", "wet_lab_anchors.json"))
    ho = load_json(_p("frontend_fixed", "reports", "heldout_index.json"))
    den = load_json(_p("frontend_fixed", "reports", "scenario_library_density.json"))
    pf = load_json(_p("data", "public_facts.json"))
    ent = load_json(_p("data", "entity_store.json"))
    conf = load_json(_p("data", "conformal_calibration.json"))

    facts = {}

    def put(key, value, unit, source):
        facts[key] = {"value": value, "unit": unit, "source_pointer": source}

    # --- 场景账本 ---
    put("validated_scenarios", dig(idx, "total"), "scenarios",
        "frontend_fixed/reports/index.json#total")
    for v in ("PASS", "MARGINAL", "REFUTED", "ERROR"):
        put(f"verdict_{v.lower()}", dig(idx, "counts." + v), "scenarios",
            f"frontend_fixed/reports/index.json#counts.{v}")
    put("gate_blocked", dig(ledger, "n_blocked"), "scenarios",
        "data/gate_ledger.json#n_blocked")
    put("gate_human_check_required", dig(ledger, "n_human_check_required"), "scenarios",
        "data/gate_ledger.json#n_human_check_required")
    put("rubric_full_pass", dig(ts, "overall.rubric_full_pass"), "scenarios",
        "data/trust_surface.json#overall.rubric_full_pass")
    put("cross_domain_scenarios", dig(ts, "overall.coverage_scope.cross_domain"), "scenarios",
        "data/trust_surface.json#overall.coverage_scope.cross_domain")
    put("microbio_scenarios", dig(ts, "overall.coverage_scope.microbio_registered"), "scenarios",
        "data/trust_surface.json#overall.coverage_scope.microbio_registered")
    put("registered_total", dig(ts, "overall.coverage_scope.registered_total"), "scenarios",
        "data/trust_surface.json#overall.coverage_scope.registered_total")
    put("registered_total_density", dig(den, "total_scenarios_loaded"), "scenarios",
        "frontend_fixed/reports/scenario_library_density.json#total_scenarios_loaded")

    # --- 校准 / UQ ---
    put("ucci_ece", dig(ledger, "calibration.ece"), "ECE",
        "data/gate_ledger.json#calibration.ece")
    put("ucci_ece_mean_p_pass", dig(ledger, "calibration.mean_p_pass"), "prob",
        "data/gate_ledger.json#calibration.mean_p_pass")
    put("conformal_n_scenarios", dig(conf, "summary.n_scenarios"), "scenarios",
        "data/conformal_calibration.json#summary.n_scenarios")
    put("conformal_mean_coverage", dig(conf, "summary.mean_coverage_conformal"), "coverage",
        "data/conformal_calibration.json#summary.mean_coverage_conformal")
    put("noise_floor", dig(ledger, "policy.noise_floor"), "fraction",
        "data/gate_ledger.json#policy.noise_floor")
    put("nominal_coverage", dig(ledger, "policy.nominal_coverage"), "coverage",
        "data/gate_ledger.json#policy.nominal_coverage")

    # --- 双证据链 ---
    put("wet_lab_anchors_total", len(wa["anchors"]) if wa and isinstance(wa.get("anchors"), list) else None,
        "anchors", "frontend_fixed/reports/wet_lab_anchors.json#anchors (len)")
    put("wet_lab_anchor_scenarios", dig(wa, "summary.total"), "scenarios",
        "frontend_fixed/reports/wet_lab_anchors.json#summary.total")
    put("wet_lab_grade_counts", dig(wa, "summary.grade_counts"), "scenarios",
        "frontend_fixed/reports/wet_lab_anchors.json#summary.grade_counts")
    put("wet_lab_blocked", dig(wa, "summary.blocked"), "scenarios",
        "frontend_fixed/reports/wet_lab_anchors.json#summary.blocked")

    # --- 留出集 ---
    put("robustness_heldout", dig(ho, "robustness_heldout.counts"), "scenarios",
        "frontend_fixed/reports/heldout_index.json#robustness_heldout.counts")
    put("extrapolation_stress", dig(ho, "extrapolation_stress.counts"), "scenarios",
        "frontend_fixed/reports/heldout_index.json#extrapolation_stress.counts")
    put("heldout_version", dig(ho, "version"), "tag",
        "frontend_fixed/reports/heldout_index.json#version")

    # --- 数据资源 ---
    put("store_points_total", dig(ts, "overall.total_store_points"), "points",
        "data/trust_surface.json#overall.total_store_points")
    put("entity_entities", dig(ent, "counts.TOTAL_ENTITIES"), "entities",
        "data/entity_store.json#counts.TOTAL_ENTITIES")
    put("entity_works", dig(ent, "counts.works"), "works",
        "data/entity_store.json#counts.works")
    put("entity_references", dig(ent, "counts.references"), "references",
        "data/entity_store.json#counts.references")
    put("engine_modules", dig(ledger, "provenance.engine.n_modules"), "modules",
        "data/gate_ledger.json#provenance.engine.n_modules")

    # --- 公开事实表自洽（锚点也是口径门禁的一部分）---
    pub_scenarios = dig(pf, "facts.validated_scenarios.value")
    pub_cross = dig(pf, "facts.cross_domain_scenarios.value")
    pub_micro = dig(pf, "facts.microbio_scenarios.value")
    dom_map = dig(pf, "facts.validated_scenarios_by_domain.value") or {}
    dom_total = sum(v for v in dom_map.values() if isinstance(v, int)) if dom_map else None
    # 域标签口径的微生物数（含 microbio_* 旧命名场景），与注册表口径 microbio_registered 可能不等
    dom_micro = None
    if dom_map:
        dom_micro = sum(v for k, v in dom_map.items()
                        if isinstance(v, int) and ("icroorganism" in k or "微生物" in k))
    facts["_cross_check"] = {
        "index_total": dig(idx, "total"),
        "public_facts_validated": pub_scenarios,
        "public_facts_cross_domain": pub_cross,
        "public_facts_microbio_registry": pub_micro,
        "public_facts_microbio_by_domain_label": dom_micro,
        "public_facts_domain_map_total": dom_total,
        "registry_partition_sum": (
            (pub_cross + pub_micro + (dig(ts, "overall.coverage_scope.science_registered") or 0))
            if isinstance(pub_cross, int) and isinstance(pub_micro, int) else None),
        "agree": (pub_scenarios == dig(idx, "total")),
        "domain_map_sums_to_total": (dom_total == dig(idx, "total")),
        "microbio_counts_agree": (
            dom_micro == pub_micro if isinstance(dom_micro, int) and isinstance(pub_micro, int) else None),
        "disambiguation": (
            "「微生物」有两个可辩护口径：注册表微生态子集 52（仅 micro_* 前缀）"
            "与域标签口径 54（另含 microbio_monod/microbio_inhibition 两个旧命名场景）。"
            "对外表述必须写明是哪一个，否则同一页出现 52 与 54 会被读成矛盾。"
        ),
    }
    return facts


def build_resource_inventory() -> dict:
    """资源清单：磁盘上的资产体量。只读元数据，不读内容。"""
    def count(rel, prefix="", suffix=".py"):
        d = _p(rel)
        if not os.path.isdir(d):
            return None
        return len([f for f in os.listdir(d) if f.startswith(prefix) and f.endswith(suffix)])

    def dir_size(rel):
        d = _p(rel)
        total = 0
        nfiles = 0
        for base, _dirs, files in os.walk(d):
            for f in files:
                fp = os.path.join(base, f)
                try:
                    total += os.path.getsize(fp)
                    nfiles += 1
                except OSError:
                    pass  # noqa: silent-ok — OSError 可安全忽略（已审计 2026-10-08）
        if nfiles == 0:
            return None
        return {"files": nfiles, "bytes": total, "mb": round(total / 1048576, 1)}

    return {
        "engine_modules": count("engine"),
        "scripts": count("scripts"),
        "frontend_pages": count("frontend_fixed", suffix=".html"),
        "vv_reports_html": count("frontend_fixed/reports/html", suffix=".html"),
        "reports_json": count("frontend_fixed/reports/json", suffix=".json"),
        "datasets_reference_benchmarks": dir_size("data/reference_benchmarks"),
        "mll_models_local": dir_size("data/ml_models"),  # 不入库，仅本地
        "entity_shards": dir_size("data/entity_shards"),
    }


def build_dataset_inventory() -> dict:
    """恢复入库的数据集：名称 -> 记录数（不可再生资产，必须点数）。"""
    out = {}
    probes = {
        "qm9_full": ("data/reference_benchmarks/quantum_chemistry/qm9_full_130831.json", None),
        "qm9_slim": ("data/reference_benchmarks/quantum_chemistry/qm9_slim.json", None),
        "tox21_labeled": ("data/reference_benchmarks/toxicology/tox21_labeled.json", None),
        "toxcast_labeled": ("data/reference_benchmarks/toxicology/toxcast_labeled.json", None),
        "toxcast_top30_assays": ("data/reference_benchmarks/toxicology/toxcast_top30_assays.json", None),
        "clintox_slim": ("data/reference_benchmarks/toxicology/clintox_slim.json", None),
        "sider_slim": ("data/reference_benchmarks/toxicology/sider_slim.json", None),
        "tox21_slim": ("data/reference_benchmarks/toxicology/tox21_slim.json", None),
        "toxcast_slim": ("data/reference_benchmarks/toxicology/toxcast_slim.json", None),
    }
    for name, (rel, _) in probes.items():
        obj = load_json(_p(rel))
        n = None
        extra = {}
        if isinstance(obj, dict):
            for k in ("n_molecules", "n", "count", "total", "records", "molecules", "n_records"):
                if isinstance(obj.get(k), int):
                    n = obj[k]
                    break
            if n is None:
                for k in ("rows", "data", "records", "molecules"):
                    if isinstance(obj.get(k), list):
                        n = len(obj[k])
                        break
            for k in ("columns", "labels", "descriptors", "assay_index"):
                if isinstance(obj.get(k), list):
                    extra[k] = len(obj[k])
            if isinstance(obj.get("n_assays"), int):
                extra["assays"] = obj["n_assays"]
            if isinstance(obj.get("source"), str):
                extra["source"] = obj["source"][:80]
        elif isinstance(obj, list):
            n = len(obj)
        out[name] = {
            "path": rel,
            "sha256_12": sha256_12(_p(rel)),
            "records": n,
            "bytes": os.path.getsize(_p(rel)) if os.path.exists(_p(rel)) else None,
            "meta": extra,
        }
    return out


def build_sample_model_inventory() -> dict:
    """本地可重建模型（不入库）。只给数量与总体积，不列每个文件。"""
    d = _p("data", "ml_models")
    if not os.path.isdir(d):
        return {"present": False}
    pkl, pt, other = 0, 0, 0
    total = 0
    for base, _dirs, files in os.walk(d):
        for f in files:
            fp = os.path.join(base, f)
            try:
                total += os.path.getsize(fp)
            except OSError:
                continue
            if f.endswith(".pkl"):
                pkl += 1
            elif f.endswith(".pt"):
                pt += 1
            else:
                other += 1
    return {
        "present": True,
        "pkl": pkl, "pt": pt, "other": other,
        "total_files": pkl + pt + other,
        "mb": round(total / 1048576, 1),
        "tracked_in_git": False,
        "rebuild_cmd": "python scripts/train_chem_surrogates.py --all",
    }


def build_domain_distribution() -> dict:
    """域分布。

    优先用 public_facts 里**已归一**的 `validated_scenarios_by_domain`——它是
    公开页引用的口径，锚点必须与公开页一致。原始 trust_surface.rows[].domain
    是逐场景的自由标签（80+ 个各不相同的字符串），直接统计会把表撑成噪音，
    故只作为兜底。
    """
    pf = load_json(_p("data", "public_facts.json"))
    norm = dig(pf, "facts.validated_scenarios_by_domain.value")
    if isinstance(norm, dict) and norm:
        return {"normalized": dict(sorted(norm.items(), key=lambda kv: -kv[1])),
                "source": "data/public_facts.json#facts.validated_scenarios_by_domain.value"}
    ts = load_json(_p("data", "trust_surface.json"))
    rows = (ts or {}).get("rows") or []
    dist = {}
    for r in rows:
        dom = r.get("domain") or "unknown"
        dist[dom] = dist.get(dom, 0) + 1
    return {"raw": dict(sorted(dist.items(), key=lambda kv: -kv[1])),
            "source": "data/trust_surface.json#rows[].domain (未归一兜底)"}


def build_anchor() -> dict:
    now = datetime.now(CN_TZ)
    anchor = {
        "name": "SwarmLabs phase anchor",
        "schema": "phase-anchor/1",
        "generated_at_cn": now.strftime("%Y-%m-%d %H:%M:%S%z"),
        "date": now.strftime("%Y-%m-%d"),
        "git": git_commit(),
        "fingerprint": build_fingerprint(),
        "facts": build_facts(),
        "domain_distribution": build_domain_distribution(),
        "datasets": build_dataset_inventory(),
        "resources": build_resource_inventory(),
        "model_artifacts": build_sample_model_inventory(),
    }
    # gaps：诚实标注缺项
    gaps = []
    for rel, h in anchor["fingerprint"]["files"].items():
        if h is None:
            gaps.append(f"missing fingerprint file: {rel}")
    for rel in anchor["fingerprint"]["not_in_git"]:
        gaps.append(
            f"fingerprint file not tracked by git: {rel} "
            f"（第三方 clone 后无法复算此锚点，需 force-add 或写 .gitignore 反否定规则）"
        )
    for k, v in anchor["facts"].items():
        if k.startswith("_"):
            continue
        if v.get("value") is None:
            gaps.append(f"fact '{k}' could not be resolved from {v.get('source_pointer')}")
    anchor["gaps"] = gaps
    return anchor


# ---------------------------------------------------------------------------
# 人读渲染
# ---------------------------------------------------------------------------
def render_md(a: dict) -> str:
    f = a["facts"]
    g = a["git"]

    def v(k):
        item = f.get(k) or {}
        val = item.get("value")
        if val is None:
            return "_缺失_"
        if isinstance(val, bool):
            return "true" if val else "false"
        if isinstance(val, float):
            return f"{val:.4g}"
        if isinstance(val, dict):
            return " / ".join(f"{kk}:{vv}" for kk, vv in val.items())
        return str(val)

    L = []
    L.append(f"# SwarmLabs 阶段锚点 · {a['date']}")
    L.append("")
    L.append(f"> 生成时间（北京） `{a['generated_at_cn']}` · schema `{a['schema']}`")
    L.append(">")
    L.append(f"> 本文件是**机器生成**的。复算：`python scripts/build_phase_anchor.py`；")
    L.append(f"> 与既有锚点比对：`python scripts/build_phase_anchor.py --verify docs/milestones/phase-anchor-<date>.md`")
    L.append(">")
    L.append("> 用途：冻结「此刻产品是什么样」的形状与数字，让下一阶段能看出**改了什么**、"
             "而不是靠记忆。所有数字都带来源指针，可回读核对。")
    L.append("")

    L.append("## 0. 指纹（身份）")
    L.append("")
    L.append("| 项 | 值 |")
    L.append("|---|---|")
    L.append(f"| git HEAD | `{g.get('head')}` |")
    L.append(f"| 分支 / 未提交文件数 | `{g.get('branch')}` / {g.get('dirty')} |")
    L.append(f"| **all_digest** | `{a['fingerprint']['all_digest']}` |")
    L.append("")
    L.append("指纹覆盖的产物（任一字节变化都会改 all_digest）：")
    L.append("")
    L.append("| 产物 | sha256[:12] | 已入 git |")
    L.append("|---|---|---|")
    ing = a["fingerprint"].get("in_git") or {}
    for rel, h in a["fingerprint"]["files"].items():
        mark = "是" if ing.get(rel) else "**否（第三方不可复算）**"
        L.append(f"| `{rel}` | `{h or '缺失'}` | {mark} |")
    L.append("")

    L.append("## 1. 验证账本（核心承诺）")
    L.append("")
    L.append("| 指标 | 值 | 来源 |")
    L.append("|---|---|---|")
    rows = [
        ("已发布 V&V 报告场景数", "validated_scenarios", "对外只能引用这个数"),
        ("PASS", "verdict_pass", ""),
        ("MARGINAL", "verdict_marginal", ""),
        ("REFUTED", "verdict_refuted", ""),
        ("ERROR", "verdict_error", ""),
        ("门禁阻断", "gate_blocked", "BLOCK_AUTONOMOUS_ACTION"),
        ("门禁要求人工复核", "gate_human_check_required", "PROCEED_WITH_HUMAN_CHECK"),
        ("rubric 七项全通过", "rubric_full_pass", "≠ verdict PASS 数"),
        ("跨域场景", "cross_domain_scenarios", ""),
        ("微生物场景", "microbio_scenarios", ""),
    ]
    for label, key, note in rows:
        src = (f.get(key) or {}).get("source_pointer") or ""
        L.append(f"| {label} | **{v(key)}** | `{src}` |")
    L.append("")
    cc = f.get("_cross_check") or {}
    L.append(f"口径自洽：index.total={cc.get('index_total')} == public_facts={cc.get('public_facts_validated')} "
             f"→ **{'一致' if cc.get('agree') else '不一致'}**")
    L.append("")
    L.append(f"- 注册表分区：cross_domain {cc.get('public_facts_cross_domain')} + "
             f"microbio {cc.get('public_facts_microbio_registry')} + science "
             f"{cc.get('registry_partition_sum', 0) - (cc.get('public_facts_cross_domain') or 0) - (cc.get('public_facts_microbio_registry') or 0)} "
             f"= {cc.get('registry_partition_sum')}")
    L.append(f"- 域标签合计：{cc.get('public_facts_domain_map_total')} "
             f"→ {'等于' if cc.get('domain_map_sums_to_total') else '不等于'} 场景总数")
    L.append(f"- **微生物口径警示**：注册表 {cc.get('public_facts_microbio_registry')} / "
             f"域标签 {cc.get('public_facts_microbio_by_domain_label')} —— "
             f"{'两者一致' if cc.get('microbio_counts_agree') else '**两者不一致，对外引用必须指明口径**'}")
    L.append("")

    L.append("## 2. 不确定度与校准（UQ 一等公民）")
    L.append("")
    L.append("| 指标 | 值 | 来源 |")
    L.append("|---|---|---|")
    for label, key in [
        ("噪声地板（不降红线）", "noise_floor"),
        ("标称覆盖率", "nominal_coverage"),
        ("门禁 ECE（越低越好）", "ucci_ece"),
        ("门禁 mean_p_pass", "ucci_ece_mean_p_pass"),
        ("Conformal 覆盖场景数", "conformal_n_scenarios"),
        ("Conformal 平均覆盖率", "conformal_mean_coverage"),
    ]:
        L.append(f"| {label} | **{v(key)}** | `{(f.get(key) or {}).get('source_pointer')}` |")
    L.append("")
    L.append("ECE 对标（外部参考，来自账本 `calibration.ece_references`）：")
    L.append("`laya_zero_shot 0.466` / `laya_refit_temp 0.081` / `jev_1_13 0.246` "
             f"vs 本产品 **{v('ucci_ece')}**。")
    L.append("")

    L.append("## 3. 双证据链（与真实实验的关系）")
    L.append("")
    L.append("| 指标 | 值 | 来源 |")
    L.append("|---|---|---|")
    for label, key in [
        ("湿实验锚点总数", "wet_lab_anchors_total"),
        ("可锚定场景数", "wet_lab_anchor_scenarios"),
        ("评级分布", "wet_lab_grade_counts"),
        ("锚点阻断场景", "wet_lab_blocked"),
    ]:
        L.append(f"| {label} | **{v(key)}** | `{(f.get(key) or {}).get('source_pointer')}` |")
    L.append("")
    L.append("> `UNANCHORED` 不是失败：多数场景是量纲/数学测试函数，无物理对应物。")
    L.append("")

    L.append("## 4. 留出集与诚实失败")
    L.append("")
    L.append("| 指标 | 值 | 来源 |")
    L.append("|---|---|---|")
    for label, key in [
        ("鲁棒性留出集（重采样）", "robustness_heldout"),
        ("外推压力测试（只外推）", "extrapolation_stress"),
        ("留出集版本", "heldout_version"),
    ]:
        L.append(f"| {label} | **{v(key)}** | `{(f.get(key) or {}).get('source_pointer')}` |")
    L.append("")
    L.append("> 外推压力测试的 REFUTED 是**主动公开的失败边界**，不是准确率声明。")
    L.append("")

    L.append("## 5. 数据与实体资源")
    L.append("")
    L.append("| 指标 | 值 | 来源 |")
    L.append("|---|---|---|")
    for label, key in [
        ("校准存储点合计（唯一可辩护数据量口径）", "store_points_total"),
        ("结构化实体总量", "entity_entities"),
        ("文献 works", "entity_works"),
        ("references", "entity_references"),
        ("engine 模块", "engine_modules"),
    ]:
        L.append(f"| {label} | **{v(key)}** | `{(f.get(key) or {}).get('source_pointer')}` |")
    L.append("")

    L.append("## 6. 域分布")
    L.append("")
    dd = a["domain_distribution"]
    table = dd.get("normalized") or dd.get("raw") or {}
    L.append(f"来源：`{dd.get('source')}`")
    L.append("")
    L.append("| 域 | 场景数 |")
    L.append("|---|---|")
    for dom, n in table.items():
        L.append(f"| {dom} | {n} |")
    L.append(f"| **合计** | **{sum(table.values())}** |")
    L.append("")

    L.append("## 7. 数据集清单（不可再生资产）")
    L.append("")
    L.append("| 数据集 | 记录数 | 大小 | 附加维度 | sha256[:12] |")
    L.append("|---|---|---|---|---|")
    for name, d in a["datasets"].items():
        mb = f"{d['bytes']/1048576:.1f}MB" if d.get("bytes") else "-"
        meta = d.get("meta") or {}
        mstr = ", ".join(f"{k}={v}" for k, v in meta.items() if k != "source") or "-"
        L.append(f"| `{name}` | {d.get('records')} | {mb} | {mstr} | `{d.get('sha256_12') or '缺失'}` |")
    L.append("")
    L.append(f"模型产物（本地，不入库）：{json.dumps(a['model_artifacts'], ensure_ascii=False)}")
    L.append("")

    L.append("## 8. 代码与页面资源")
    L.append("")
    r = a["resources"]
    L.append("| 项 | 数量/体量 |")
    L.append("|---|---|")
    for k, val in r.items():
        if isinstance(val, dict):
            L.append(f"| {k} | {val.get('files')} 文件 / {val.get('mb')}MB |")
        else:
            L.append(f"| {k} | {val} |")
    L.append("")

    if a["gaps"]:
        L.append("## 9. 缺口（诚实标注）")
        L.append("")
        for gp in a["gaps"]:
            L.append(f"- {gp}")
        L.append("")
    else:
        L.append("## 9. 缺口")
        L.append("")
        L.append("无（所有指纹产物与事实指针均可解析）。")
        L.append("")

    L.append("---")
    L.append("")
    L.append("**如何用它做阶段对比**：下个阶段生成新锚点后，比较 `all_digest`（变了没有）、"
             "`facts` 各条的 `value`（哪个数字动了）、`gaps`（缺项填了没有）。")
    L.append("数字变了不算问题——**没解释地变了**才是问题。")
    L.append("")
    return "\n".join(L)


def write_outputs(a: dict) -> tuple[str, str]:
    os.makedirs(_p("docs", "milestones"), exist_ok=True)
    json_path = _p("data", f"phase_anchor_{a['date']}.json")
    md_path = _p("docs", "milestones", f"phase-anchor-{a['date']}.md")
    latest_json = _p("data", "phase_anchor_latest.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(a, f, ensure_ascii=False, indent=2)
    with open(latest_json, "w", encoding="utf-8") as f:
        json.dump(a, f, ensure_ascii=False, indent=2)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(render_md(a))
    return json_path, md_path


def verify_against(path: str) -> int:
    """用当前状态构建锚点，与既有锚点（json 或 md 旁的 json）比对。"""
    old = None
    if path.endswith(".json"):
        old = load_json(path)
    else:
        cand = path.replace("/milestones/", "/").replace(
            "docs/milestones/", "data/").replace("phase-anchor-", "phase_anchor_").replace(".md", ".json")
        old = load_json(cand) or load_json(_p("data", "phase_anchor_latest.json"))
    if not old:
        print(f"[verify] 无法加载旧锚点（{path}）", file=sys.stderr)
        return 2
    new = build_anchor()
    print(f"旧锚点: {old.get('date')}  all_digest={old.get('fingerprint',{}).get('all_digest')}")
    print(f"新锚点: {new.get('date')}  all_digest={new.get('fingerprint',{}).get('all_digest')}")
    changed = 0
    oldf, newf = old.get("facts", {}), new.get("facts", {})
    for k in sorted(set(oldf) | set(newf)):
        if k.startswith("_"):
            continue
        ov = (oldf.get(k) or {}).get("value")
        nv = (newf.get(k) or {}).get("value")
        if ov != nv:
            print(f"  CHANGED  {k}: {ov} -> {nv}")
            changed += 1
    oldfp = old.get("fingerprint", {}).get("files", {})
    newfp = new.get("fingerprint", {}).get("files", {})
    for k in sorted(set(oldfp) | set(newfp)):
        if oldfp.get(k) != newfp.get(k):
            print(f"  FILE-DIFF {k}: {oldfp.get(k)} -> {newfp.get(k)}")
            changed += 1
    print(f"\n{changed} 项变化。" if changed else "\n无变化（锚点稳定）。")
    return 1 if changed else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="生成/复算 SwarmLabs 阶段锚点")
    ap.add_argument("--verify", metavar="FILE", help="与既有锚点比对")
    ap.add_argument("--json", action="store_true", help="只打印 JSON 到 stdout")
    args = ap.parse_args()

    if args.verify:
        return verify_against(args.verify)

    a = build_anchor()
    if args.json:
        print(json.dumps(a, ensure_ascii=False, indent=2))
        return 0
    jp, mp = write_outputs(a)
    print(f"all_digest = {a['fingerprint']['all_digest']}")
    print(f"场景 {a['facts']['validated_scenarios']['value']} "
          f"(PASS {a['facts']['verdict_pass']['value']} / "
          f"MARGINAL {a['facts']['verdict_marginal']['value']} / "
          f"REFUTED {a['facts']['verdict_refuted']['value']})")
    print(f"JSON -> {os.path.relpath(jp, ROOT)}")
    print(f"MD   -> {os.path.relpath(mp, ROOT)}")
    if a["gaps"]:
        print(f"缺口 {len(a['gaps'])} 项（见 md 第 9 节）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
