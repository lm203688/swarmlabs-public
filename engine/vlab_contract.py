#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VLab 工作单契约 —— 「一个外部实验验证请求」的**单一真源格式**。

它解决什么问题
--------------
在它之前，"客户发来一个需要验证的实验"这件事根本没有机器可读形态：只有
人读的需求描述，于是"虚拟实验独立成立"这条红线只能靠文档承诺，无法被程序
检查。本模块把那条红线变成**schema 层的不可表达性**：

  corroboration（真实数据）在契约里**位于 build 之后**，且显式声明
  `as_input` 必须为 false；一旦为 true，`validate_work_order` 直接判错。
  这不是提示，是拒收——**读法 B 在数据结构上无法写出来**。

为什么这条最重要
----------------
`docs/feasibility-virtual-lab-2026-09-21.md` §1 判定：真实数据作「前置输入」
（读法 B）会让产品从「替代真实实验」退化成「编排真实实验」，既违反 09-09
单一判据、也失去护城河。所以这条不是"最好别这么做"，是"这样写不进来"。

阶段顺序（不可倒置）
--------------------
    intake -> scope -> build -> verify -> corroborate -> deliver

- `build` 只接受 spec（专业标签 / 设计空间 / 方法族 / 种子），**结构上拿不到**
  任何真实观测；
- `corroborate` 才消费真实数据，且只能**增信**（parity chip），不得改判。

`assert_stage_order` / `assert_corroboration_post_hoc` 是这条不变式的可执行形式。

依赖：仅标准库（含同目录 `vlab_stdlib.py`，按路径加载）。
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Tuple

import importlib.util

_HERE = os.path.dirname(os.path.abspath(__file__))


def _stdlib():
    spec = importlib.util.spec_from_file_location(
        "_vlab_stdlib", os.path.join(_HERE, "vlab_stdlib.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


S = _stdlib()

# --------------------------------------------------------------------- 常量

SCHEMA = "swarmlabs-vlab-work-order/1.0"

#: 流水线阶段——顺序即语义。任何"跳过 verify 直接 corroborate"都是倒置。
STAGE_ORDER: Tuple[str, ...] = (
    "intake",       # 校验工作单
    "scope",        # 落成 scenario spec + 专业路由
    "build",        # 搭虚拟实验（只吃 spec）
    "verify",       # fail-closed 多维验证（引擎独立给出裁决）
    "corroborate",  # 真实数据事后作证（只增信，不改判）
    "deliver",      # 交付契约（可离线复验）
)

#: 允许的方法族。`auto` 表示由 scope 阶段按专业/目标路由。
METHOD_FAMILIES = (
    "auto", "gp", "pinn", "neural_operator", "fourier_operator",
    "multifidelity", "meta_learner", "conformal",
)

#: 目标方向
GOAL_DIRECTIONS = ("maximize", "minimize", "characterize")

#: build 阶段允许接收的输入键——白名单。**真实观测的键不在此列**，
#: 于是哪怕调用方想把真实数据塞进 build 也塞不进（会被 assert 拦下）。
BUILD_INPUT_KEYS = (
    "credential",          # 应用凭据（谁的实验）
    "discipline",          # 专业标签
    "goal",                # 目标量 + 方向
    "design_space",        # 设计空间（变量 + 区间 + 单位）
    "method",              # 方法族
    "scenario_key",        # 路由到已有内核场景时的键
    "seed",                # 随机种子（可复算）
    "claimed_refs",        # 文献引用（**引用元数据**，不是观测值）
)


class OrderInvertedError(ValueError):
    """把真实数据当 build 阶段输入（读法 B）时抛出——fail-closed。"""


class ContractError(ValueError):
    """工作单不符合契约。"""


# ------------------------------------------------------------------ 校验工具

_YEAR = re.compile(r"\b(19|20)\d{2}\b")


def _err(errors: List[dict], code: str, where: str, msg: str) -> None:
    errors.append({"code": code, "where": where, "message": msg})


def _warn(warnings: List[dict], code: str, where: str, msg: str) -> None:
    warnings.append({"code": code, "where": where, "message": msg})


def goal_quantity_binding(wo: Any, spec: Any = None) -> Dict[str, Any]:
    """比对「工作单要验证的量」与「场景实际产出的量」。

    为什么这条值得单独存在
    ----------------------
    `_scope` 只做 discipline → scenario_key 的路由。路由成功只意味着「场景在
    账本里」，**不意味着它回答了工作单提出的问题**。一个标 PASS 的场景完全可能
    产出的根本不是客户要验证的那个量——那样交付出去的报告看起来完整、实际答非
    所问，比明确拒绝更糟。

    三种结果（键集固定，调用方不必写 `if in`）：
      - MATCH        ：量名与单位量纲都一致
      - MISMATCH     ：单位量纲不兼容 → 上层作 error 拒收
      - WARN         ：量名写法不同但量纲一致 → 上层作 warning，交人工核
                       （量名是自由文本，"biomass yield" 与
                        "biomass concentration" 可以指同一个量；把写法差异
                        当拒收会把合法请求误杀）
      - UNVERIFIABLE ：专业档案未声明 output_quantity / output_unit → 只能 warn
                       （不声明就不假装绑定过）
    """
    unbound = {
        "status": "UNVERIFIABLE",
        "reason": "discipline declares no output_quantity/output_unit — the "
                  "goal-to-scenario binding cannot be checked",
        "goal_quantity": None, "declared_quantity": None,
        "goal_unit": None, "declared_unit": None,
        "dimension_match": None,
    }
    goal = wo.get("goal") if isinstance(wo, dict) else None
    if not isinstance(goal, dict):
        return unbound
    if spec is None or not getattr(spec, "has_output_declared", False):
        return unbound

    gq = (goal.get("quantity") or "").strip()
    gu = (goal.get("unit") or "").strip()
    dq = (getattr(spec, "output_quantity", "") or "").strip()
    du = (getattr(spec, "output_unit", "") or "").strip()

    q_ok = _norm_qty(gq) == _norm_qty(dq)
    # 单位优先用 anchor_intake 的量纲解析（能识别 mg/l vs g/l 这类换算），
    # 拿不到解析器时退回规范化字符串比较。
    dim_match: Optional[bool] = None
    try:
        a = _load_anchor_intake()
        pa = a.parse_unit(gu)
        pb = a.parse_unit(du)
        if pa is None or pb is None:
            dim_match = (_norm_unit(gu) == _norm_unit(du))
        else:
            dim_match = pa[0] == pb[0]
    except Exception:
        dim_match = _norm_unit(gu) == _norm_unit(du)

    status = "MATCH" if (q_ok and dim_match) else (
        "MISMATCH" if not dim_match else "WARN")
    reasons = []
    if not dim_match:
        reasons.append(
            f"goal.unit {gu!r} is not dimensionally compatible with "
            f"the scenario's output unit {du!r}")
    if not q_ok:
        reasons.append(
            f"goal.quantity {gq!r} is written differently from the declared "
            f"output {dq!r} — quantity names are free text, so this is a "
            f"warning for human review, not a rejection")
    return {
        "status": status,
        "reason": "; ".join(reasons) if reasons else
                  "goal quantity and unit match the scenario output",
        "goal_quantity": gq, "declared_quantity": dq,
        "goal_unit": gu, "declared_unit": du,
        "dimension_match": dim_match,
    }


def _norm_qty(s: str) -> str:
    """量名规范化：小写、压缩空白、去掉常见后缀词，便于跨写法比对。"""
    import re as _re
    t = (s or "").lower()
    t = _re.sub(r"[^a-z0-9 ]+", " ", t)
    t = _re.sub(r"\s+", " ", t).strip()
    for suf in ("value", "concentration of biomass"):
        t = t.replace(suf, "")
    return _re.sub(r"\s+", " ", t).strip()


def _norm_unit(s: str) -> str:
    return (s or "").strip().lower().replace(" ", "").replace("°", "")


def _load_sibling(name: str):
    """按路径加载同目录模块，并注册进 sys.modules。

    必须注册：dataclasses / typing 会在解析期查 `sys.modules[cls.__module__]`，
    查不到就抛 AttributeError。这是纯 spec 加载最容易踩的坑。
    """
    import sys as _sys
    spec = importlib.util.spec_from_file_location(
        f"_vlab_{name}", os.path.join(_HERE, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    _sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_anchor_intake():
    return _load_sibling("anchor_intake")


def _load_discipline_registry():
    return _load_sibling("discipline_registry")


def _resolve_discipline_spec(name: Any):
    """把 discipline 名解析成 DisciplineSpec；解析不到返回 None。

    惰性导入 discipline_registry（它反过来按路径加载本模块，急切导入会成环）。
    任何异常都返回 None —— 这使绑定检查退化为 UNVERIFIABLE（warn），
    而不是让整个 intake 崩掉。
    """
    if not S.nonempty_str(name):
        return None
    try:
        DR = _load_discipline_registry()
        specs = {s.name: s for s in DR.seeded_specs()}
        for nm, doc in DR.load_registered().items():
            specs[nm] = DR.DisciplineSpec.from_dict(doc)
        if name in specs:
            return specs[name]
        for s in specs.values():
            if s.category == name:
                return s
        return None
    except Exception:
        return None


def known_disciplines() -> Dict[str, dict]:
    """可提交的专业名清单：taxonomy ∪ 专业注册表。

    此前只读 `engine/domains.py` 的 taxonomy，于是提交 "Microbiology" 会被误报
    `unknown_discipline`——而专业注册表明明能解析它。两份「已知专业」清单不一致
    会让 intake 输出假告警，客户据此以为要重新选专业。两处必须合并。
    """
    out: Dict[str, dict] = {}
    try:
        dom = S.load_domains()
        cats = getattr(dom, "PENDING_CATEGORIES", {}) or {}
        active_labels = getattr(dom, "CATEGORY_LABELS", {}) or {}
        for name, info in cats.items():
            out[name] = {"source": "domains.PENDING_CATEGORIES",
                         "note": info.get("note", "")}
        for name, label in active_labels.items():
            out.setdefault(name, {"source": "domains.CATEGORY_LABELS",
                                  "note": label})
    except Exception:
        pass  # noqa: silent-ok — Exception 可安全忽略（已审计 2026-10-08）
    try:
        DR = _load_discipline_registry()
        for s in DR.seeded_specs():
            out.setdefault(s.name, {"source": "discipline_registry.seeded_specs",
                                    "note": s.note, "category": s.category})
        for nm, doc in DR.load_registered().items():
            out.setdefault(nm, {"source": "discipline_registry.registered",
                                "note": (doc or {}).get("note", "")})
    except Exception:
        pass  # noqa: silent-ok — Exception 可安全忽略（已审计 2026-10-08）
    return out


def validate_work_order(wo: Any) -> Dict[str, Any]:
    """校验工作单。返回 {ok, errors, warnings, digest}。

    **fail-closed**：任何一处不合法即 ok=False；调用方在 ok=False 时不得
    进入 build/verify 阶段（`intake` 阶段据此拒绝）。

    这条校验里有两类必须分清：
      - *格式错*（缺字段 / 类型不对 / 区间倒置）→ error，拒收；
      - *能力缺口*（专业未接入 / 方法族未知）→ 在 scope 阶段处理，不算 error。
    把二者混为一谈会让"未接入"伪装成"请求非法"，那是另一种不诚实。
    """
    errors: List[dict] = []
    warnings: List[dict] = []

    if not isinstance(wo, dict):
        _err(errors, "not_an_object", "$", "work order must be a JSON object")
        return {"ok": False, "errors": errors, "warnings": warnings, "digest": None}

    if wo.get("schema") != SCHEMA:
        _err(errors, "bad_schema", "schema",
             f"expected '{SCHEMA}', got {wo.get('schema')!r}")

    if not S.nonempty_str(wo.get("submitted_by")):
        _err(errors, "missing_field", "submitted_by",
             "submitted_by (who submits this experiment) is required")

    if not S.nonempty_str(wo.get("discipline")):
        _err(errors, "missing_field", "discipline",
             "discipline (professional domain) is required")

    # ---- goal ----
    goal = wo.get("goal")
    if not isinstance(goal, dict):
        _err(errors, "missing_field", "goal", "goal object is required")
    else:
        if not S.nonempty_str(goal.get("quantity")):
            _err(errors, "missing_field", "goal.quantity",
                 "goal.quantity (the quantity to validate) is required")
        if not S.nonempty_str(goal.get("unit")):
            _err(errors, "missing_field", "goal.unit",
                 "goal.unit is required — without it the dimensional screening "
                 "of corroboration data cannot run, which is exactly how "
                 "'confidently wrong' gets in")
        if goal.get("direction") not in GOAL_DIRECTIONS:
            _err(errors, "bad_enum", "goal.direction",
                 f"goal.direction must be one of {list(GOAL_DIRECTIONS)}")
        pr = goal.get("plausible_range")
        if pr is not None:
            if (not isinstance(pr, list) or len(pr) != 2
                    or not S.is_finite(pr[0]) or not S.is_finite(pr[1])):
                _err(errors, "bad_type", "goal.plausible_range",
                     "goal.plausible_range must be [lo, hi] finite numbers")
            elif float(pr[0]) >= float(pr[1]):
                _err(errors, "bad_range", "goal.plausible_range",
                     f"plausible_range lo({pr[0]}) must be < hi({pr[1]})")

    # ---- claim_id（可选，稳定指向「这条主张」）----
    # 不强制：强制会让所有既有工作单失效。但若调用方提供，则必须自洽——
    # 一个手写错的 claim_id 比没有 claim_id 更糟，因为它会污染跨次关联。
    cid = wo.get("claim_id")
    if cid is not None:
        if not S.nonempty_str(cid):
            _err(errors, "bad_type", "claim_id",
                 "claim_id must be a non-empty string when provided")
        elif not CLAIM_ID_RE.match(cid):
            _err(errors, "bad_format", "claim_id",
                 f"claim_id must match {CLAIM_ID_RE.pattern!r} "
                 "(derive it with new_claim_id instead of hand-typing)")
        elif cid != new_claim_id(wo):
            # 只警告不拒收：调用方可能在主张语义之外附加了序号。
            # 但静默接受一个不匹配的值会让「按 claim_id 查历史」查到别人的数据。
            _warn(warnings, "claim_id_mismatch", "claim_id",
                   f"provided {cid} but the goal content derives "
                   f"{new_claim_id(wo)}; cross-run association will be wrong")

    # ---- design_space ----
    ds = wo.get("design_space")
    if not isinstance(ds, dict) or not isinstance(ds.get("variables"), list) \
            or not ds.get("variables"):
        _err(errors, "missing_field", "design_space.variables",
             "design_space.variables must be a non-empty list")
    else:
        for i, v in enumerate(ds["variables"]):
            at = f"design_space.variables[{i}]"
            if not isinstance(v, dict):
                _err(errors, "bad_type", at, "variable must be an object")
                continue
            if not S.nonempty_str(v.get("name")):
                _err(errors, "missing_field", f"{at}.name", "variable name required")
            lo, hi = v.get("lo"), v.get("hi")
            if not S.is_finite(lo) or not S.is_finite(hi):
                _err(errors, "bad_type", at,
                     "variable lo/hi must be finite numbers")
            elif float(lo) >= float(hi):
                _err(errors, "bad_range", at,
                     f"variable lo({lo}) must be < hi({hi})")
            if not S.nonempty_str(v.get("unit")):
                _warn(warnings, "missing_unit", f"{at}.unit",
                      "variable has no unit — dimensional checks cannot be applied")

    # ---- method ----
    method = wo.get("method")
    if method is None:
        _warn(warnings, "missing_method", "method",
              "no method family declared; scope will route to 'auto'")
    elif not isinstance(method, dict):
        _err(errors, "bad_type", "method", "method must be an object")
    else:
        fam = method.get("family", "auto")
        if fam not in METHOD_FAMILIES:
            _err(errors, "bad_enum", "method.family",
                 f"method.family must be one of {list(METHOD_FAMILIES)}")

    # ---- goal 与场景输出的绑定（防止「路由成功但答非所问」）----
    binding = goal_quantity_binding(wo, _resolve_discipline_spec(wo.get("discipline")))
    if binding["status"] == "MISMATCH":
        # 单位量纲不兼容 —— 场景在物理上产不出被问的那个量，拒收。
        _err(errors, "goal_output_mismatch", "goal", binding["reason"])
    elif binding["status"] in ("WARN", "UNVERIFIABLE"):
        # 量名写法不同 / 专业未声明输出量 —— 记告警交人工核，不拒收。
        _warn(warnings, "goal_binding_" + binding["status"].lower(),
              "goal", binding["reason"])

    # ---- cost_of_error ----
    coe = wo.get("cost_of_error", 1.0)
    if not S.is_finite(coe) or not (0.0 <= float(coe) <= 10.0):
        _err(errors, "bad_range", "cost_of_error",
             "cost_of_error must be a number in [0, 10]")

    # ---- 红线：真实数据不得作前置输入 ----
    corr = wo.get("corroboration")
    if corr is not None:
        if not isinstance(corr, dict):
            _err(errors, "bad_type", "corroboration",
                 "corroboration must be an object")
        else:
            if corr.get("as_input") is True:
                # 读法 B：真实数据成为 build 的输入 —— 结构上拒收。
                _err(errors, "ORDER_INVERTED", "corroboration.as_input",
                     "真实数据只能事后作证，不得作为虚拟实验的前置输入 "
                     "(read-order A only). Set as_input=false or remove the "
                     "corroboration block.")
            pts = corr.get("points")
            if pts is not None:
                if not isinstance(pts, list):
                    _err(errors, "bad_type", "corroboration.points",
                         "corroboration.points must be a list")
                else:
                    for i, p in enumerate(pts):
                        at = f"corroboration.points[{i}]"
                        if not isinstance(p, dict):
                            _err(errors, "bad_type", at, "point must be an object")
                            continue
                        if not S.is_finite(p.get("y")):
                            _err(errors, "bad_type", f"{at}.y",
                                 "point.y (the observed value) must be a finite number")
                        if not S.nonempty_str(p.get("unit")):
                            _err(errors, "missing_unit", f"{at}.unit",
                                 "point.unit is required for dimensional screening")
                        if not S.nonempty_str(p.get("citation")):
                            _warn(warnings, "missing_citation", f"{at}.citation",
                                  "no citation — anchor screening will REJECT it")
                        if not S.nonempty_str(p.get("confirmed_by")):
                            _warn(warnings, "unconfirmed", f"{at}.confirmed_by",
                                  "no human confirmer — screening can at best "
                                  "return NEEDS_HUMAN, never ACCEPTED")

    # ---- 能力缺口（warn 而非 error，交由 scope 阶段处理）----
    disc = wo.get("discipline")
    if S.nonempty_str(disc):
        kd = known_disciplines()
        if kd and disc not in kd:
            _warn(warnings, "unknown_discipline", "discipline",
                  f"'{disc}' is not in the domains taxonomy "
                  f"({len(kd)} known) — scope will report onboarding required")

    if isinstance(method, dict) and method.get("family", "auto") == "auto":
        _warn(warnings, "auto_method", "method.family",
              "'auto' defers the method choice to scope")

    digest = S.canonical_sha256(wo) if not errors else None
    return {"ok": not errors, "errors": errors, "warnings": warnings,
            "digest": digest}


# ---------------------------------------------------------- 阶段不变式

def assert_stage_order(stages: List[dict]) -> None:
    """阶段记录必须是 STAGE_ORDER 的**严格递增子序列**。

    允许缺阶段（例如一次只跑到 verify 的诊断），但**不允许乱序**——
    corroborate 出现在 verify 之前即视为倒置，抛 OrderInvertedError。
    """
    idx = {name: i for i, name in enumerate(STAGE_ORDER)}
    last = -1
    for st in stages:
        name = (st or {}).get("stage")
        if name not in idx:
            raise ContractError(f"unknown stage in record: {name!r}")
        if idx[name] <= last:
            raise OrderInvertedError(
                f"stage order violated: '{name}' appears out of order "
                f"(stage sequence must follow {list(STAGE_ORDER)})"
            )
        last = idx[name]


def assert_corroboration_post_hoc(build_inputs: Dict[str, Any],
                                  corroboration: Optional[dict]) -> None:
    """build 的输入里**不得**出现真实观测。

    两条独立的检查，任一命中即抛：
      1. 白名单：build_inputs 的键必须都在 BUILD_INPUT_KEYS 内（真实观测
         的键名如 observations/real_points/y/points 不在白名单）；
      2. 数据隔离：若 corroboration 提供了点位，则 build_inputs 的任何字段
         都不得等于这些点位的规范摘要（防止"换个键名偷偷喂进去"）。
    """
    unknown = sorted(set(build_inputs) - set(BUILD_INPUT_KEYS))
    if unknown:
        raise OrderInvertedError(
            f"build stage received non-whitelisted input(s): {unknown}. "
            f"Allowed: {list(BUILD_INPUT_KEYS)}. Real observations must not "
            f"reach build (corroboration is post-hoc only)."
        )
    pts = ((corroboration or {}).get("points") or [])
    if not pts:
        return
    obs_digests = {S.canonical_sha256(p) for p in pts}
    for k, v in build_inputs.items():
        if S.canonical_sha256(v) in obs_digests:
            raise OrderInvertedError(
                f"build input '{k}' is byte-identical to a corroboration "
                f"point — the real data leaked into the virtual experiment"
            )


# ------------------------------------------------------------------ 工作单

def new_work_order_id(wo: dict) -> str:
    """由内容派生 work order id（可复算，不是随机数）。"""
    basis = {
        "submitted_by": wo.get("submitted_by"),
        "discipline": wo.get("discipline"),
        "goal": wo.get("goal"),
        "design_space": wo.get("design_space"),
        "scenario_key": wo.get("scenario_key"),
    }
    return "wo-" + S.short(S.canonical_sha256(basis), 16)


CLAIM_ID_RE = re.compile(r"^claim-[0-9a-f]{8,32}$")

#: claim id 的派生基准——**刻意不含** design_space / scenario_key / seed。
#: 主张「30°C 下产量 > 2 g/L」不会因为换了设计空间或换了路由场景而变成
#: 另一个主张；那正是需要跨次运行累积发现时靠得住的键。
_CLAIM_BASIS_KEYS = ("discipline", "goal")


def claim_basis(wo: dict) -> dict:
    """取出派生 claim id 的字段。goal 整体参与（quantity/unit/direction/
    plausible_range 任一变化都是新主张）。"""
    g = wo.get("goal") or {}
    return {
        "discipline": wo.get("discipline"),
        "goal": {
            "quantity": g.get("quantity"),
            "unit": g.get("unit"),
            "direction": g.get("direction"),
            "plausible_range": g.get("plausible_range"),
        },
    }


def new_claim_id(wo: dict) -> str:
    """由**主张本身**派生的稳定 id，与 work_order_id 分离。

    work_order_id 绑定整次请求（含 design_space），改一个变量区间就是新 id，
    因此无法指向「这份工作单的第 2 条主张」，也无法跨次运行累积发现。
    claim_id 只绑定主张语义，跨工作单可关联。
    """
    return "claim-" + S.short(S.canonical_sha256(claim_basis(wo)), 16)


def claim_digest(wo: dict) -> str:
    """主张的完整内容摘要（不截断），用于跨次运行的精确去重。"""
    return S.canonical_sha256(claim_basis(wo))


def work_order_digest(wo: dict) -> str:
    return S.canonical_sha256(wo)


def summarize(wo: dict) -> dict:
    """给日志/CLI 的紧凑摘要。"""
    ds = wo.get("design_space") or {}
    vars_ = ds.get("variables") or []
    return {
        "work_order_id": wo.get("work_order_id") or new_work_order_id(wo),
        "claim_id": wo.get("claim_id") or new_claim_id(wo),
        "submitted_by": wo.get("submitted_by"),
        "discipline": wo.get("discipline"),
        "quantity": (wo.get("goal") or {}).get("quantity"),
        "n_variables": len(vars_),
        "method": (wo.get("method") or {}).get("family", "auto"),
        "scenario_key": wo.get("scenario_key"),
        "n_corroboration_points": len(
            ((wo.get("corroboration") or {}).get("points") or [])
        ),
        "cost_of_error": wo.get("cost_of_error", 1.0),
    }


def schema_doc() -> dict:
    """机器可读的契约说明（供 CLI `vlab schema` 与文档引用）。

    2026-10-15 更新：新增 `nature_review_requirements` 字段，把 Nature
    综述 §2.3 提出的三需求显式化——scalability / generalizability /
    provenance-complete——每个需求对应本契约里的**具体字段**，让审查者
    能从 schema 层反查"这个 work order 声称支撑哪条综述要求"。
    """
    return {
        "schema": SCHEMA,
        "stage_order": list(STAGE_ORDER),
        "build_input_whitelist": list(BUILD_INPUT_KEYS),
        "method_families": list(METHOD_FAMILIES),
        "goal_directions": list(GOAL_DIRECTIONS),
        "red_line": (
            "corroboration.as_input MUST be false. Real observations are "
            "post-hoc corroboration only; the virtual experiment must reach "
            "its verdict without them. Declaring them as input is rejected."
        ),
        # 三需求对齐（Nature 综述 §2.3；2026-10-15 补齐 schema 层的显式声明）
        "nature_review_requirements": {
            "scalability": {
                "statement": "结论必须能在**规模化**参数/变量/样本上给出，"
                             "而非只在少数几个具体数值上成立",
                "schema_anchor": [
                    "goal.plausible_range",       # 允许量级跨度声明
                    "design_space.variables[].lo/hi",  # 变量域宽度
                    "method.family",              # 决定能否扩展到 dim>2
                ],
                "verified_by": "engine.experiment_proposal.verify_prediction",
            },
            "generalizability": {
                "statement": "结论必须能**跨参数/跨学科**迁移，不能只在"
                             "单一场景上成立",
                "schema_anchor": [
                    "discipline",
                    "scenario_key",
                    "corroboration.points[].citation",  # 外部锚点
                ],
                "verified_by": "engine.generalization_bench.run_g1/run_g2/run_g3",
                "known_boundary": "G3=0.0%（跨学科留一）——超范围场景"
                                  "必须走 assert_applicability 显式声明",
            },
            "provenance_complete": {
                "statement": "每一个数值/结论必须能追到**代码 commit + "
                             "训练点 seed + 锚点来源**",
                "schema_anchor": [
                    "claim_id",                     # 稳定溯源锚（INV-4）
                    "corroboration.points[].citation",
                    "corroboration.points[].confirmed_by",
                    "method.family",
                    "method.fingerprint",           # 由 build 阶段填入
                ],
                "verified_by": "engine.provenance_guard",
            },
        },
        "example": {
            "schema": SCHEMA,
            "submitted_by": "client-name",
            "discipline": "Biology",
            "goal": {"quantity": "biomass concentration", "unit": "g/L",
                     "direction": "characterize",
                     "plausible_range": [0.0, 200.0]},
            "design_space": {"variables": [
                {"name": "T", "unit": "degC", "lo": 20, "hi": 40},
                {"name": "S0", "unit": "g/L", "lo": 1, "hi": 20},
            ]},
            "method": {"family": "auto"},
            "cost_of_error": 2.0,
            "scenario_key": "micro_ecoli_glucose",
            "corroboration": {
                "as_input": False,
                "points": [{"x": {"T": 30, "S0": 10}, "y": 1.23,
                            "unit": "g/L",
                            "citation": "Client internal lab log 2026-08, run #7",
                            "confirmed_by": "client-lab-lead"}],
            },
        },
    }


__all__ = [
    "SCHEMA", "STAGE_ORDER", "METHOD_FAMILIES", "GOAL_DIRECTIONS",
    "BUILD_INPUT_KEYS", "OrderInvertedError", "ContractError",
    "known_disciplines", "validate_work_order", "assert_stage_order",
    "assert_corroboration_post_hoc", "new_work_order_id", "work_order_digest",
    "new_claim_id", "claim_digest", "claim_basis", "CLAIM_ID_RE",
    "summarize", "schema_doc", "goal_quantity_binding",
]
