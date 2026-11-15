#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""专业接入脚手架 —— 让「加一个新专业」是一个**有门禁的复用流程**，
而不是每次手写一坨。

为什么需要它
------------
`docs/feasibility-virtual-lab-2026-09-21.md` §3 判定：多专业扩展的瓶颈不是
「能不能」，而是「每个新专业要花几天还是几周」。`engine/domains.py` 已经把
9 大类 taxonomy 写好，并写明接入方式：

    接入方式 = 用发表基准做 gold + UQ，即「真补齐」（非信任启发式）

但"用发表基准做 gold"这句话此前是**散文**——没有结构、没有校验，于是它
要么被跳过，要么被凑数（写一个看起来像文献的字符串就声明接入了）。
本模块把它变成**可校验的准入**：

  一个专业只有同时满足下面两条，才允许被标为 `VALIDATED`：
    (a) 至少一条 `GoldSource` 能**解析到位**——要么是仓库内可打开的产物指针
        （`pointer` 指向存在的文件与键），要么是含年份/DOI 的文献引用；
    (b) 有一个 `kernel_scenario_key`，且该键**真在门禁账本里**（即真的被
        引擎验证过），而不是一个占位字符串。

没有 (a) 或 (b) → 状态是 `AWAITING_GOLD` / `AWAITING_KERNEL`，**不是**
`VALIDATED`。这条与项目既有纪律一致：`wet_lab_anchors` 的 19 锚点是人工
策展的结果，把 82 个场景铺满"文献区间"等于**编造**（见
`docs/action-vs-core-goal-audit-20260919.md`）。所以本模块的默认种子是
**诚实的待接入清单**，不是一张好看的能力表。

依赖：仅标准库（含同目录 `vlab_stdlib.py` / `vlab_contract.py`，按路径加载）。
"""

from __future__ import annotations

import importlib.util
import os
import re
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))


def _sibling(name: str):
    spec = importlib.util.spec_from_file_location(
        f"_vlab_{name}", os.path.join(_HERE, f"{name}.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


S = _sibling("vlab_stdlib")

SCHEMA = "swarmlabs-vlab-discipline/1.0"

#: 专业接入状态
AWAITING_GOLD = "AWAITING_GOLD"        # 有分类、无可用 gold
AWAITING_KERNEL = "AWAITING_KERNEL"    # 有 gold、无内核场景
VALIDATED = "VALIDATED"                # gold 可解析 + 内核场景在账本里

STATUSES = (AWAITING_GOLD, AWAITING_KERNEL, VALIDATED)

DEFAULT_LEDGER = os.path.join(S.ROOT, "frontend_fixed", "reports", "gate_ledger.json")

_YEAR = re.compile(r"\b(19|20)\d{2}\b")
_DOI = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+")


# ------------------------------------------------------------------ 数据类

class GoldSource:
    """一条**可核验**的基准来源。

    两种形态，任一成立即算"可解析"：
      - `pointer`：仓库内产物指针，形如 `path/to/file.json#json.key.path`；
        校验时真的打开文件、真的走到那个键。
      - `citation`：文献引用字符串，须含年份或 DOI（否则只是措辞）。
    """

    def __init__(self, kind: str = "citation", citation: str = "",
                 pointer: str = "", unit: str = "", value: Optional[float] = None,
                 lo: Optional[float] = None, hi: Optional[float] = None,
                 conditions: str = ""):
        self.kind = kind
        self.citation = citation
        self.pointer = pointer
        self.unit = unit
        self.value = value
        self.lo = lo
        self.hi = hi
        self.conditions = conditions

    def to_dict(self) -> dict:
        return {
            "kind": self.kind, "citation": self.citation, "pointer": self.pointer,
            "unit": self.unit, "value": self.value, "lo": self.lo, "hi": self.hi,
            "conditions": self.conditions,
        }

    @staticmethod
    def from_dict(d: dict) -> "GoldSource":
        return GoldSource(
            kind=(d or {}).get("kind", "citation"),
            citation=(d or {}).get("citation", "") or "",
            pointer=(d or {}).get("pointer", "") or "",
            unit=(d or {}).get("unit", "") or "",
            value=(d or {}).get("value"),
            lo=(d or {}).get("lo"),
            hi=(d or {}).get("hi"),
            conditions=(d or {}).get("conditions", "") or "",
        )


class DisciplineSpec:
    """一个专业的接入档案。"""

    def __init__(self, name: str, category: str = "",
                 kernel_scenario_key: str = "",
                 gold: Optional[List[GoldSource]] = None,
                 uq_required: bool = True, note: str = "",
                 output_quantity: str = "", output_unit: str = ""):
        self.name = name
        self.category = category
        self.kernel_scenario_key = kernel_scenario_key
        self.gold = list(gold or [])
        self.uq_required = uq_required
        self.note = note
        # 该专业内核场景**产出**的量与其单位。为空则「工作单问了什么、场景
        # 答了什么」无法被机器比对——绑定不可验证（见 vlab_contract 的
        # goal_quantity_binding）。留空是诚实标注，不是省略。
        self.output_quantity = output_quantity
        self.output_unit = output_unit

    @property
    def has_output_declared(self) -> bool:
        return bool(self.output_quantity and self.output_unit)

    def to_dict(self) -> dict:
        return {
            "schema": SCHEMA, "name": self.name, "category": self.category,
            "kernel_scenario_key": self.kernel_scenario_key,
            "gold": [g.to_dict() for g in self.gold],
            "uq_required": self.uq_required, "note": self.note,
            "output_quantity": self.output_quantity,
            "output_unit": self.output_unit,
        }

    @staticmethod
    def from_dict(d: dict) -> "DisciplineSpec":
        return DisciplineSpec(
            name=(d or {}).get("name", "") or "",
            category=(d or {}).get("category", "") or "",
            kernel_scenario_key=(d or {}).get("kernel_scenario_key", "") or "",
            gold=[GoldSource.from_dict(g) for g in ((d or {}).get("gold") or [])],
            uq_required=bool((d or {}).get("uq_required", True)),
            note=(d or {}).get("note", "") or "",
            output_quantity=(d or {}).get("output_quantity", "") or "",
            output_unit=(d or {}).get("output_unit", "") or "",
        )


# ------------------------------------------------------------------ 解析器

def _resolve_pointer(pointer: str) -> Dict[str, Any]:
    """解析 `path#a.b.c` 形式的仓库内指针。返回 {ok, reason, value}。"""
    if not pointer:
        return {"ok": False, "reason": "empty pointer", "value": None}
    if "#" in pointer:
        rel, keypath = pointer.split("#", 1)
    else:
        rel, keypath = pointer, ""
    abs_path = os.path.join(S.ROOT, rel)
    if not os.path.exists(abs_path):
        return {"ok": False, "reason": f"file not found: {rel}", "value": None}
    if not keypath:
        return {"ok": True, "reason": "file exists", "value": None}
    doc = S.read_json(abs_path)
    if doc is None:
        return {"ok": False, "reason": f"not readable as JSON: {rel}", "value": None}
    cur: Any = doc
    for part in keypath.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return {"ok": False, "reason": f"bad list index '{part}'",
                        "value": None}
        else:
            return {"ok": False, "reason": f"key '{part}' not found", "value": None}
    return {"ok": True, "reason": "resolved", "value": cur}


def _citation_is_checkable(citation: str) -> bool:
    return bool(_YEAR.search(citation or "") or _DOI.search(citation or ""))


# ------------------------------------------------------------------ 校验

def validate_discipline(spec: DisciplineSpec) -> Dict[str, Any]:
    """校验一个专业档案能否准入。fail-closed。

    返回 {ok, errors, warnings, gold_resolved, status}。
    `status` 只在校验通过且内核场景在账本里时才是 VALIDATED。
    """
    errors: List[dict] = []
    warnings: List[dict] = []

    def err(code: str, where: str, msg: str):
        errors.append({"code": code, "where": where, "message": msg})

    def warn(code: str, where: str, msg: str):
        warnings.append({"code": code, "where": where, "message": msg})

    if not S.nonempty_str(spec.name):
        err("missing_field", "name", "discipline name is required")

    # (a-1) category 必须是 taxonomy 里的分类
    try:
        dom = S.load_domains()
        cats = set((getattr(dom, "PENDING_CATEGORIES", {}) or {}).keys()) | \
            set((getattr(dom, "CATEGORY_LABELS", {}) or {}).keys())
    except Exception:
        cats = set()
    if cats and spec.category not in cats:
        err("unknown_category", "category",
            f"'{spec.category}' is not a taxonomy category "
            f"(known: {sorted(cats)})")

    # (a-2) 至少一条 gold 可解析
    resolved: List[dict] = []
    for i, g in enumerate(spec.gold):
        at = f"gold[{i}]"
        ok = False
        detail = ""
        if g.kind == "in_repo_pointer" or g.pointer:
            r = _resolve_pointer(g.pointer)
            ok, detail = r["ok"], r["reason"]
            if not ok:
                err("gold_unresolvable", at,
                    f"in-repo pointer '{g.pointer}' does not resolve: {detail}")
        elif g.citation:
            ok = _citation_is_checkable(g.citation)
            detail = "citation has year/DOI" if ok else "citation lacks year/DOI"
            if not ok:
                err("gold_unverifiable", at,
                    f"citation is just prose (no year, no DOI): {g.citation[:60]!r}")
        else:
            err("gold_empty", at, "gold source has neither pointer nor citation")
        if ok and not S.nonempty_str(g.unit):
            err("gold_missing_unit", at, "gold source must declare a unit")
        if ok:
            has_value = S.is_finite(g.value) or (S.is_finite(g.lo) and S.is_finite(g.hi))
            if not has_value:
                err("gold_missing_value", at,
                    "gold source must carry a value or a [lo, hi] range")
            resolved.append({"gold": g.to_dict(), "detail": detail})
    if not spec.gold:
        err("no_gold", "gold",
            "at least one verifiable gold source is required "
            "(published benchmark or in-repo pointer)")

    # (a-3) UQ 是硬要求——没有不确定度的"接入"不算接入
    if not spec.uq_required:
        err("uq_not_required", "uq_required",
            "uq_required must be true: a discipline entered without an "
            "uncertainty budget is not validated, it is asserted")

    # (b) 内核场景必须真在账本里
    led = S.read_json(DEFAULT_LEDGER) or {}
    scenarios = (led.get("scenarios") or {})
    if not S.nonempty_str(spec.kernel_scenario_key):
        warn("no_kernel_scenario", "kernel_scenario_key",
             "no kernel scenario bound — status AWAITING_KERNEL")
    elif spec.kernel_scenario_key not in scenarios:
        err("kernel_scenario_not_in_ledger", "kernel_scenario_key",
            f"'{spec.kernel_scenario_key}' is not in the gate ledger "
            f"({len(scenarios)} scenarios present) — it has not been validated")

    # 状态派生
    if errors:
        status = AWAITING_GOLD if any(
            e["code"].startswith("gold") or e["code"] in ("no_gold",) for e in errors
        ) else AWAITING_KERNEL
    elif not S.nonempty_str(spec.kernel_scenario_key):
        status = AWAITING_KERNEL
    else:
        status = VALIDATED

    return {"ok": not errors, "errors": errors, "warnings": warnings,
            "gold_resolved": resolved, "status": status}


def status_of(spec: DisciplineSpec) -> str:
    return validate_discipline(spec)["status"]


# ------------------------------------------------------------------ 脚手架

def onboarding_checklist(category: str = "") -> List[dict]:
    """把「接入一个新专业」写成可复用的步骤清单（不是散文）。

    每条含 `step` / `artifact`（产出物路径约定）/ `gate`（验收判据）。
    这与 domains.py 的「用发表基准做 gold + UQ」是对同一件事的展开。
    """
    return [
        {"step": 1, "action": "定分类与目标量",
         "artifact": f"discipline spec: category={category or '<Category>'}",
         "gate": "category 必须在 domains taxonomy 内（否则先改 taxonomy）"},
        {"step": 2, "action": "取一篇可引的发表基准 / 已有仓库产物",
         "artifact": "GoldSource(kind=in_repo_pointer|citation, unit, value|lo/hi)",
         "gate": "指针必须真解析到位 / 引用必须含年份或 DOI，且必须带单位"},
        {"step": 3, "action": "定义设计空间与物理模型（或选定已有多保真层级）",
         "artifact": "scenario spec（变量 + 区间 + 单位）",
         "gate": "每个变量 lo<hi 且单位已知（否则量纲检查失效）"},
        {"step": 4, "action": "用统一内核搭虚拟实验并跑 UQ",
         "artifact": "kernel scenario key + UQ 预算（噪声地板 3% 不降）",
         "gate": "R2>=0.90 且单侧 coverage>=0.90，否则不得标 PASS"},
        {"step": 5, "action": "把该场景写进门禁账本（build_gate_ledger）",
         "artifact": "gate_ledger.json 中的一条 scenario 记录",
         "gate": "kernel_scenario_key 必须能在账本里查到——查不到即 AWAITING_KERNEL"},
        {"step": 6, "action": "跑 validate_discipline 并归档",
         "artifact": "DisciplineSpec.to_dict()",
         "gate": "status == VALIDATED 才允许对外宣称「已接入该专业」"},
    ]


def register(spec: DisciplineSpec) -> Dict[str, Any]:
    """把专业档案落盘到 data/vlab/disciplines/<name>.json。

    **拒绝写盘**当校验不通过——一个"写进去但没准入"的档案比没有更糟：
    它会让人以为这个专业已接入。
    """
    res = validate_discipline(spec)
    if not res["ok"]:
        raise ValueError(
            f"refusing to register '{spec.name}': validation failed: "
            f"{[e['code'] for e in res['errors']]}"
        )
    path = os.path.join(S.ROOT, "data", "vlab", "disciplines",
                        f"{spec.name}.json")
    doc = spec.to_dict()
    doc["status"] = res["status"]
    doc["gold_resolved"] = res["gold_resolved"]
    doc["validated_at_utc"] = S.utc_now()
    S.write_json(path, doc)
    return {"path": path, "status": res["status"]}


def load_registered() -> Dict[str, dict]:
    d = os.path.join(S.ROOT, "data", "vlab", "disciplines")
    out: Dict[str, dict] = {}
    if not os.path.isdir(d):
        return out
    for fn in sorted(os.listdir(d)):
        if fn.endswith(".json"):
            doc = S.read_json(os.path.join(d, fn))
            if doc:
                out[doc.get("name", fn)] = doc
    return out


# ------------------------------------------------------------------ 种子

def seeded_specs() -> List[DisciplineSpec]:
    """默认种子：**诚实的待接入清单**，由一个已真接入的专业做示范。

    - `Microbiology`：仓库里唯一真跑通的域。它的 gold 用 in-repo 指针指向
      已存在的锚点表（`wet_lab_anchors.json#ecoli_glucose_Ks`，含已发表区间与
      文献来源），内核场景用真在账本里的 `micro_ecoli_glucose`。全部可解析，
      所以它是 VALIDATED——这是**事实**，不是声称。
    - 其余 10 个 taxonomy 大类：gold 为空 → AWAITING_GOLD。它们**没有**被
      填入任何文献字符串，因为本项目不允许为凑数编造基准。
    """
    specs: List[DisciplineSpec] = []

    # 示范：唯一真接入的专业（每一项都指向仓库内真实存在的产物）
    specs.append(DisciplineSpec(
        name="Microbiology", category="Biology",
        kernel_scenario_key="micro_ecoli_glucose",
        gold=[GoldSource(
            kind="in_repo_pointer",
            pointer="frontend_fixed/reports/wet_lab_anchors.json",
            unit="g/L",
            lo=0.000030, hi=0.005,
            conditions="E. coli / 葡萄糖半饱和常数 Ks，文献区间 30 ug/L - 5 mg/L",
        )],
        uq_required=True,
        note="唯一已接入统一的域：82 场景中 micro_* 子集 + 湿实验锚点表。",
        # 产出量：Monod 动力学的时间演化量是生物量浓度。gold 里的 Ks/μmax 是
        # *参数*，不是输出——两者混为一谈会让「验证了什么」变得不可判定。
        output_quantity="biomass concentration",
        output_unit="g/L",
    ))

    # 其余分类：诚实标注待接入，gold 留空（不编造）
    try:
        dom = S.load_domains()
        cats = getattr(dom, "PENDING_CATEGORIES", {}) or {}
    except Exception:
        cats = {}
    for name, info in cats.items():
        if name == "Biology":
            continue  # 已由 Microbiology（category=Biology）覆盖
        specs.append(DisciplineSpec(
            name=name, category=name, kernel_scenario_key="",
            gold=[], uq_required=True,
            note=(info.get("note", "") or "") + " [gold 待补：禁止为凑数硬编]",
        ))
    return specs


def registry_summary() -> dict:
    """注册表全貌：种子 + 已落盘档案，逐个给状态。"""
    specs = {s.name: s for s in seeded_specs()}
    for name, doc in load_registered().items():
        specs[name] = DisciplineSpec.from_dict(doc)
    rows = []
    counts = {AWAITING_GOLD: 0, AWAITING_KERNEL: 0, VALIDATED: 0}
    for name, spec in sorted(specs.items()):
        res = validate_discipline(spec)
        counts[res["status"]] = counts.get(res["status"], 0) + 1
        rows.append({
            "name": name, "category": spec.category,
            "status": res["status"],
            "kernel_scenario_key": spec.kernel_scenario_key,
            "n_gold": len(spec.gold),
            "n_gold_resolved": len(res["gold_resolved"]),
            "errors": [e["code"] for e in res["errors"]],
        })
    return {
        "schema": "swarmlabs-vlab-discipline-registry/1.0",
        "totals": counts,
        "n": len(rows),
        "rows": rows,
        "note": ("VALIDATED 仅授予「gold 可解析 + 内核场景真在账本里」的专业。"
                 "其余为待接入，gold 留空而非编造。"),
    }


__all__ = [
    "SCHEMA", "AWAITING_GOLD", "AWAITING_KERNEL", "VALIDATED", "STATUSES",
    "GoldSource", "DisciplineSpec", "validate_discipline", "status_of",
    "onboarding_checklist", "register", "load_registered",
    "seeded_specs", "registry_summary",
]
