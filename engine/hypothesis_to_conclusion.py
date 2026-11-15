"""假设驱动的端到端接口 —— 核心目标 L4 层（2026-10-08 新增）。

为什么需要这个模块
------------------
`docs/CORE_OBJECTIVE-20261004.md` §2.2 的验收标准是：

> 给定一个**从未在任何文献中被代理建模过**的跨学科科学问题，
> SwarmLabs 能在 ≤20 次虚拟实验内给出：
> 1. 一个带 95% 校准区间的定量结论
> 2. 一个明确的「当前结论在哪些条件下不成立」（适用域边界）
> 3. 一个可执行的下一步实验建议，且该建议被采纳后能提升结论的校准质量

§2.1 的层级诊断把「L4 科研假设驱动」标为**缺失**——当时的判断是
「当前是『跑场景』，不是『验证假设』」。本模块补的就是这一层。

它和已有模块的关系（不是重复）
------------------------------
| 模块 | 回答的问题 | 输入 |
|---|---|---|
| `engine/experiment_proposal.py` | 「这个**场景**的结论和下一步是什么」 | trust_surface 的一行 |
| **`engine/hypothesis_to_conclusion.py`**（本模块） | 「我**有个假设**，能不能验、怎么验」 | 一个自然语言 + 可证伪判据的假设 |

也就是说：`experiment_proposal` 是**场景视角**（我有一批数据，结论是什么），
本模块是**假设视角**（我有个想法，先告诉我能不能验）。

**诚实的边界（R1~R6 准入判据）**
--------------------------------
本模块**不会**因为假设「听起来像科研问题」就放行。它按 CORE_OBJECTIVE
§3.2 的 R1~R6 逐条判定，任一不满足就退回并明确说**缺哪一条**：

| 标准 | 判据（可自动检查的） |
|---|---|
| R1 跨学科 | ``disciplines`` 字段 ≥2 项 |
| R2 有真实未解问题 | ``open_question`` 非空（须人工审核，这里只做形式检查） |
| R3 有独立真值 | ``anchor_count >= 3`` |
| R4 可证伪 | ``falsifiable`` 为真且 ``falsification_criterion`` 非空 |
| R5 可外推 | ``transfer_hypothesis`` 非空 |
| R6 非 benchmark | ``benchmark_artifact`` 为假（纯数值优化题会被拒） |

**不宣称**：
- 不宣称任何假设「通过」——本模块只输出「可验 / 不可验 + 原因」，
  以及若可验时**用哪个已有场景去承载它**。
- 不宣称 ≤20 次虚拟实验一定能达成。A15 已证明 GP 不能外推，
  所以**假设若依赖外推，本模块会明确拒**（``needs_extrapolation``）。
- 不生成新的科学场景。**场景库是人工审核的资产**，本模块只在
  已有场景里做「假设 → 场景」的匹配，不凭空造场景。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

#: 核心目标 §3.5 要求的虚拟实验预算（验收标准是 ≤20 次）。
VIRTUAL_EXPERIMENT_BUDGET = 20

#: R1~R6 准入判据（对应 CORE_OBJECTIVE-20261004.md §3.2）
R1_MIN_DISCIPLINES = 2
R3_MIN_ANCHORS = 3

_SCHEMA = "swarmlabs.hypothesis-to-conclusion.v1"


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class HypothesisSpec:
    """一个待验证的科研假设（**输入**）。

    字段与 CORE_OBJECTIVE §3.2 的 R1~R6 一一对应——不是随手设计的，
    是把「准入判据」变成机器可检的形式。
    """

    statement: str                          # 假设陈述（必填）
    disciplines: List[str] = field(default_factory=list)   # R1
    open_question: str = ""                 # R2：文献里的未解问题
    anchor_count: int = 0                   # R3：独立已发表数据点数
    falsifiable: bool = False               # R4
    falsification_criterion: str = ""       # R4：什么结果会推翻它
    transfer_hypothesis: str = ""           # R5：邻近领域的迁移设想
    benchmark_artifact: bool = False        # R6：True = 纯数值优化题（拒绝）
    # 候选承载场景（可选；不给则由 ``match_scenario`` 自动匹配）
    candidate_scenario: str = ""
    #: 假设是否**依赖外推**。A15 已证明 GP 不能外推，故依赖外推的假设
    #: 在当前架构下不可验——本模块会明说，不假装能做。
    needs_extrapolation: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AdmissionResult:
    """R1~R6 准入判定结果。"""

    admitted: bool
    failures: List[str] = field(default_factory=list)   # 未过的判据（含说明）
    passed: List[str] = field(default_factory=list)    # 过的判据
    human_review_required: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class HypothesisVerdict:
    """对假设的完整裁决（**输出**）。"""

    hypothesis: str
    admission: AdmissionResult
    #: 若 admitted，用哪个场景承载；否则 None
    carrier_scenario: Optional[str] = None
    #: 若 admitted，直接给出三零件（来自 experiment_proposal）
    proposal: Optional[Dict[str, Any]] = None
    #: 若 !admitted，给出「要变成可验的还缺什么」——可操作的下一步
    what_would_make_it_verifiable: List[str] = field(default_factory=list)
    #: 诚实声明：本裁决**不是**科学结论，只是「能不能验」的前置判断
    disclaimer: str = ""
    generated_at: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": _SCHEMA,
            "hypothesis": self.hypothesis,
            "admission": self.admission.to_dict(),
            "carrier_scenario": self.carrier_scenario,
            "proposal": self.proposal,
            "what_would_make_it_verifiable": self.what_would_make_it_verifiable,
            "disclaimer": self.disclaimer,
            "generated_at": self.generated_at,
        }


# ---------------------------------------------------------------------------
# R1~R6 准入判定
# ---------------------------------------------------------------------------

def check_admission(h: HypothesisSpec) -> AdmissionResult:
    """按 R1~R6 逐条判定。任一不满足 → 不准入。

    与 CORE_OBJECTIVE §3.2 表格逐行对应；每条失败都给出**可操作**的
    补救说明（不是只说"不满足"）。
    """
    fails: List[str] = []
    passed: List[str] = []
    human: List[str] = []

    # R1 跨学科
    if len(h.disciplines) >= R1_MIN_DISCIPLINES:
        passed.append("R1 跨学科（≥2 学科）")
    else:
        fails.append(
            f"R1 未过：只有 {len(h.disciplines)} 个学科声明"
            f"（需 ≥{R1_MIN_DISCIPLINES}）。"
            f"补救：写清这个假设同时涉及哪两个学科——"
            f"若只涉及一个，它更可能是单学科 benchmark 而非跨学科问题")

    # R2 有真实未解问题（形式检查 + 标人工审核）
    if h.open_question.strip():
        passed.append("R2 有未解问题陈述")
        human.append("R2 需人工确认：文献里这个问题是否真的仍未解决")
    else:
        fails.append(
            "R2 未过：`open_question` 为空。"
            "补救：写清文献里这个问题的**未解部分**是什么——"
            "若文献已有定论，它不是研究问题，只是教科书内容")

    # R3 有独立真值
    if h.anchor_count >= R3_MIN_ANCHORS:
        passed.append(f"R3 独立真值（anchor_count={h.anchor_count}）")
    else:
        fails.append(
            f"R3 未过：独立已发表数据点 {h.anchor_count} 个"
            f"（需 ≥{R3_MIN_ANCHORS}）。"
            "补救：找出至少 3 组**独立于本项目**的已发表测量值。"
            "只有 1 个点的场景无法判断模型是否拟合了对——"
            "过拟合一个点也能完美吻合")

    # R4 可证伪
    if h.falsifiable and h.falsification_criterion.strip():
        passed.append("R4 可证伪（判据非空）")
    else:
        if not h.falsifiable:
            fails.append(
                "R4 未过：未标记为可证伪。"
                "补救：明确写出「什么观测结果会推翻这个假设」——"
                "写不出判据的假设不是科学假设，是信念")
        else:
            fails.append(
                "R4 未过：`falsification_criterion` 为空。"
                "补救：把证伪条件写成一句可判定的话")

    # R5 可外推
    if h.transfer_hypothesis.strip():
        passed.append("R5 有迁移设想")
    else:
        fails.append(
            "R5 未过：`transfer_hypothesis` 为空。"
            "补救：说明这个结论如果在邻近领域也成立，"
            "意味着什么——跨学科假设的价值就在这个迁移性")

    # R6 非 benchmark
    if not h.benchmark_artifact:
        passed.append("R6 非纯数值优化题")
    else:
        fails.append(
            "R6 未过：标记为 `benchmark_artifact`。"
            "补救：若它只是「证明 GP 实现正确」的数值题"
            "（如 Rastrigin/Brannin），它属于第 1 层方法学标定，"
            "不构成第 3 层科研场景")

    # A15 硬约束：依赖外推的假设在当前架构下不可验
    if h.needs_extrapolation:
        fails.append(
            "**A15 硬约束**：该假设依赖外推，而 GP 在训练域外的预测"
            "已实测退化为「训练集均值」（换核换 ls 都改不了）。"
            "因此它在当前架构下**不可验**——"
            "要么先把外推能力做成（路线 A/B，见 CORE_OBJECTIVE §5），"
            "要么把假设改写成域内可验的版本")

    return AdmissionResult(
        admitted=(len(fails) == 0),
        failures=fails,
        passed=passed,
        human_review_required=human,
    )


def what_would_make_verifiable(h: HypothesisSpec) -> List[str]:
    """给出「要变成可验的还缺什么」——可操作清单，不是空话。"""
    out: List[str] = []
    if len(h.disciplines) < R1_MIN_DISCIPLINES:
        out.append("补第二个学科的声明（跨学科是第 3 层的准入前提）")
    if not h.open_question.strip():
        out.append("查文献：写清这个问题的未解部分在哪")
    if h.anchor_count < R3_MIN_ANCHORS:
        out.append(
            f"补到 {R3_MIN_ANCHORS} 组独立已发表测量值"
            "（当前 "
            f"{h.anchor_count} 组）——这是「模型拟合了对」与"
            "「模型拟合了这个点」的唯一区别")
    if not (h.falsifiable and h.falsification_criterion.strip()):
        out.append("写一句「什么结果会推翻它」——不可证伪的假设不进体系")
    if not h.transfer_hypothesis.strip():
        out.append("说明结论在邻近领域的迁移含义")
    if h.needs_extrapolation:
        out.append(
            "**把假设改写成域内可验的版本**："
            "若它必须依赖外推，先明确接受「当前架构不可验」，"
            "或投入外推能力研发（CORE_OBJECTIVE §5 路线 A/B）")
    return out


# ---------------------------------------------------------------------------
# 假设 → 场景匹配
# ---------------------------------------------------------------------------

#: 领域关键词 → 场景 key 前缀的映射（用于自动匹配）。
#: **刻意保守**：只在明确命中时给建议，否则说"无匹配"而不是硬凑。
_DOMAIN_HINTS: Dict[str, Tuple[str, ...]] = {
    "microbiology": ("micro_", "microbio_"),
    "microorganisms": ("micro_", "microbio_"),
    "microbial": ("micro_", "microbio_"),
    "biological process": ("micro_", "microbio_"),
    "bioprocess": ("micro_", "microbio_"),
    "bioreactor": ("micro_", "chem_reactor", "pde_"),
    "fermentation": ("micro_", "microbio_", "chem_"),
    "chemical engineering": ("chem_", "micro_", "pde_", "energy_"),
    "chemical process": ("chem_",),
    "process engineering": ("chem_", "energy_"),
    "chemistry": ("chem_", "mol_"),
    "chemical": ("chem_", "mol_"),
    "molecular": ("mol_",),
    "materials": ("chem_", "energy_"),
    "catalysis": ("chem_", "mol_"),
    "optimization": ("opt_", "surrogate_"),
    "surrogate modeling": ("surrogate_",),
    "neuroscience": ("neuro_",),
    "neural": ("neuro_",),
    "physics": ("phys_",),
    "pde": ("pde_",),
    "numerical": ("pde_", "phys_"),
    "llm": ("llm_",),
    "ecology": ("eco_",),
    "heat": ("heat_", "pde_"),
    "heat transfer": ("heat_", "pde_", "energy_"),
    "energy": ("energy_", "heat_"),
    "thermodynamics": ("energy_", "heat_"),
    "finance": ("fin_", "quant_"),
    "economics": ("fin_", "eco_"),
}


def match_scenario(
    h: HypothesisSpec,
    available_keys: List[str],
) -> Tuple[Optional[str], str]:
    """把假设匹配到一个可用的场景 key。

    策略（**刻意保守，不硬凑**）：
    1. 假设若显式给了 ``candidate_scenario`` 且它存在 → 直接用
    2. 否则按学科关键词找前缀命中；**只在恰好一个命中**时返回
    3. 命中多个或零个 → 返回 None + 说明

    返回 ``(key 或 None, 说明)``。
    """
    if h.candidate_scenario:
        if h.candidate_scenario in available_keys:
            return h.candidate_scenario, "使用假设显式指定的承载场景"
        return None, (f"假设指定的场景 `{h.candidate_scenario}` "
                      f"不在可用场景库里")

    prefixes: List[str] = []
    for d in h.disciplines:
        key = d.strip().lower().split("/")[0]
        prefixes.extend(_DOMAIN_HINTS.get(key, ()))

    if not prefixes:
        return None, ("未提供可识别的学科关键词，"
                      f"无法自动匹配（已知映射：{sorted(_DOMAIN_HINTS)}）")

    hits = sorted({k for k in available_keys
                   if any(k.startswith(p) for p in prefixes)})
    if len(hits) == 1:
        return hits[0], f"按学科关键词匹配到唯一候选 {hits[0]}"
    if not hits:
        return None, (f"学科关键词 {prefixes} 在场景库里无匹配"
                      f"（库中 {len(available_keys)} 个场景）")
    return None, (f"学科关键词匹配到 {len(hits)} 个候选，"
                  f"需人工指定：{hits[:6]}"
                  f"{'...' if len(hits) > 6 else ''}")


# ---------------------------------------------------------------------------
# 顶层入口
# ---------------------------------------------------------------------------

def evaluate_hypothesis(
    hypothesis: HypothesisSpec,
    trust_surface_path: Optional[str] = None,
    repo_root: Optional[str] = None,
) -> HypothesisVerdict:
    """对假设做完整裁决。

    这是本模块的唯一对外入口，输出一律是 :class:`HypothesisVerdict`：
    **要么准入并给出承载场景与三零件，要么明确说不可验以及缺什么。**

    Args:
        hypothesis: 待验证的假设
        trust_surface_path: ``data/trust_surface.json`` 路径（可选）
        repo_root: 仓库根（可选，默认由本文件位置推断）
    """
    import time

    if repo_root is None:
        repo_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), ".."))
    if trust_surface_path is None:
        trust_surface_path = os.path.join(
            repo_root, "data", "trust_surface.json")

    admission = check_admission(hypothesis)

    ts_rows: List[Dict[str, Any]] = []
    if os.path.isfile(trust_surface_path):
        try:
            with open(trust_surface_path, encoding="utf-8") as f:
                ts_rows = json.load(f).get("rows", [])
        except Exception:                     # noqa: BLE001
            ts_rows = []

    available = [str(r.get("key")) for r in ts_rows if r.get("key")]

    # 未准入 → 不匹配场景，直接给出补救清单
    if not admission.admitted:
        return HypothesisVerdict(
            hypothesis=hypothesis.statement,
            admission=admission,
            carrier_scenario=None,
            proposal=None,
            what_would_make_it_verifiable=what_would_make_verifiable(hypothesis),
            disclaimer=(
                "本裁决**不是**对假设真假的判断，只回答"
                "「在当前架构与准入标准下，这个假设能不能被验证」。"
                f"未准入（{len(admission.failures)} 项判据未过）。"),
            generated_at=int(time.time()),
        )

    # 已准入 → 找承载场景
    key, match_note = match_scenario(hypothesis, available)
    if key is None:
        adm2 = AdmissionResult(
            admitted=False,
            failures=[f"承载场景缺失：{match_note}"],
            passed=admission.passed,
            human_review_required=admission.human_review_required
            + ["需人工指定承载场景（或补一个场景进库）"],
        )
        return HypothesisVerdict(
            hypothesis=hypothesis.statement,
            admission=adm2,
            carrier_scenario=None,
            proposal=None,
            what_would_make_it_verifiable=[
                "指定一个承载场景：在 hypothesis.candidate_scenario 里"
                "填场景 key，或让至少一个学科关键词在场景库中唯一匹配",
                match_note,
            ],
            disclaimer=(
                "R1~R6 全部通过，但**没有可承载的场景**——"
                "本模块不在已有场景外凭空造场景（场景库是人工审核资产）。"
                "这意味着该假设目前**无验证载体**。"),
            generated_at=int(time.time()),
        )

    # 找到 row → 复用 experiment_proposal 出三零件
    row = next((r for r in ts_rows if str(r.get("key")) == key), None)
    proposal: Optional[Dict[str, Any]] = None
    if row is not None:
        try:
            from .experiment_proposal import propose_experiment
            proposal = propose_experiment(row).to_dict()
        except Exception as e:                # noqa: BLE001
            proposal = {"_error": f"{type(e).__name__}: {e}"}

    # 预算与 A15 的诚实提醒
    notes: List[str] = []
    if row is not None:
        n_store = int(row.get("n_store") or 0)
        budget_left = VIRTUAL_EXPERIMENT_BUDGET - n_store
        notes.append(
            f"虚拟实验预算：已用 {n_store}/{VIRTUAL_EXPERIMENT_BUDGET}，"
            f"剩余 {budget_left} 次")
        if row.get("in_applicability") is False:
            notes.append(
                "⚠️ 承载场景超出平滑代理模型适用域（A14）——"
                "该假设即使准入也无法给出可信数值结论")
        if (row.get("rubric_passed") or 0) < (row.get("rubric_total") or 7):
            notes.append(
                f"⚠️ 承载场景 rubric {row.get('rubric_passed')}/"
                f"{row.get('rubric_total')} 未满通过——"
                "其 next_experiment 会标为不可 actionable")

    return HypothesisVerdict(
        hypothesis=hypothesis.statement,
        admission=admission,
        carrier_scenario=key,
        proposal=proposal,
        what_would_make_it_verifiable=notes,
        disclaimer=(
            f"准入通过（R1~R6 全过），承载场景 `{key}`"
            f"（{match_note}）。"
            "**注意**：这不意味着假设为真，只意味着「有一个带校准区间"
            "的验证路径」。结论的真假仍由 proposal.conclusion 给出，"
            "其不确定性由 CI 与 coverage 度量。"),
        generated_at=int(time.time()),
    )


# ---------------------------------------------------------------------------
# 批量入口：把假设库跑一遍
# ---------------------------------------------------------------------------

def load_hypotheses(path: str) -> List[HypothesisSpec]:
    """从 JSON 文件读假设列表。

    接受两种形态：
    - 顶层是 list → 直接是假设数组
    - 顶层是 dict 且有 ``hypotheses`` 键 → 取该键
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    items = data if isinstance(data, list) else data.get("hypotheses", [])
    out: List[HypothesisSpec] = []
    fields = {f for f in HypothesisSpec.__dataclass_fields__}
    for it in items:
        if not isinstance(it, dict):
            continue
        out.append(HypothesisSpec(**{k: v for k, v in it.items()
                                     if k in fields}))
    return out


def evaluate_all(
    hypotheses: List[HypothesisSpec],
    trust_surface_path: Optional[str] = None,
    repo_root: Optional[str] = None,
) -> Dict[str, Any]:
    """批量裁决 + 汇总。"""
    verdicts = [evaluate_hypothesis(h, trust_surface_path, repo_root)
                for h in hypotheses]
    n = max(len(verdicts), 1)
    n_adm = sum(1 for v in verdicts if v.admission.admitted)
    return {
        "schema": _SCHEMA,
        "generated_at": __import__("time").time().__int__(),
        "n_hypotheses": len(verdicts),
        "n_admitted": n_adm,
        "admitted_rate": round(n_adm / n, 4),
        "summary": {
            "note": ("admitted 只代表「R1~R6 准入判据通过 + 有承载场景」，"
                     "**不代表假设为真**。未准入的假设给出了"
                     "逐条可操作的补救清单。"),
        },
        "verdicts": [v.to_dict() for v in verdicts],
    }


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def _selftest() -> bool:
    passed = 0
    total = 0

    def check(name: str, cond: bool) -> None:
        nonlocal passed, total
        total += 1
        print(f"  {'PASS' if cond else 'FAIL'}  {name}")
        if cond:
            passed += 1

    def _good(**kw) -> HypothesisSpec:
        base = HypothesisSpec(
            statement="葡萄糖限制浓度如何影响大肠杆菌的最大比生长速率",
            disciplines=["microbiology", "chemical engineering"],
            open_question="Monod 常数随底物组成变化的机理未解",
            anchor_count=4,
            falsifiable=True,
            falsification_criterion="若实测 μmax 与 S0 无显著相关则推翻",
            transfer_hypothesis="结论应可迁移到酵母与放线菌",
        )
        for k, v in kw.items():
            setattr(base, k, v)
        return base

    # 1. 完整假设 → 准入
    a = check_admission(_good())
    check("complete hypothesis admitted", a.admitted)
    check("all 6 R-criteria passed", len(a.passed) >= 6)
    check("no failures", len(a.failures) == 0)
    check("R2 flagged for human review",
          any("人工" in x for x in a.human_review_required))

    # 2. R1 单学科 → 拒
    a1 = check_admission(_good(disciplines=["microbiology"]))
    check("single-discipline rejected (R1)", not a1.admitted)
    check("R1 failure explains remedy",
          any("R1" in f and "补救" in f for f in a1.failures))

    # 3. R3 anchor 不足 → 拒
    a3 = check_admission(_good(anchor_count=1))
    check("anchor_count<3 rejected (R3)", not a3.admitted)
    check("R3 failure mentions overfitting",
          any("过拟合" in f for f in a3.failures))

    # 4. R4 不可证伪 → 拒
    a4 = check_admission(_good(falsifiable=False))
    check("non-falsifiable rejected (R4)", not a4.admitted)
    a4b = check_admission(_good(falsification_criterion=""))
    check("empty falsification criterion rejected (R4)", not a4b.admitted)

    # 5. R5 无迁移 → 拒
    a5 = check_admission(_good(transfer_hypothesis=""))
    check("no transfer hypothesis rejected (R5)", not a5.admitted)

    # 6. R6 benchmark → 拒
    a6 = check_admission(_good(benchmark_artifact=True))
    check("benchmark artifact rejected (R6)", not a6.admitted)

    # 7. R2 空 → 拒
    a2 = check_admission(_good(open_question=""))
    check("empty open_question rejected (R2)", not a2.admitted)

    # 8. A15：依赖外推 → 拒（这是最重要的诚实判据）
    a8 = check_admission(_good(needs_extrapolation=True))
    check("extrapolation-dependent rejected (A15)", not a8.admitted)
    check("A15 failure cites mean-collapse",
          any("训练集均值" in f for f in a8.failures))

    # 9. 补救清单非空且可操作
    rem = what_would_make_verifiable(_good(disciplines=["x"], anchor_count=0))
    check("remedy list non-empty", len(rem) >= 2)
    check("remedy mentions anchor gap",
          any("独立已发表" in r for r in rem))

    # 10. 场景匹配
    keys = ["micro_lag_ecoli", "chem_langmuir", "opt_rastrigin2d"]
    # 用**只声明微生物**的学科列表测自动匹配（双学科会因同时命中
    # micro_/chem_ 前缀而变成歧义——那是下面第 13 项要测的行为）
    h_micro = HypothesisSpec(
        statement="葡萄糖限制浓度如何影响大肠杆菌的最大比生长速率",
        disciplines=["microbiology"])
    k1, n1 = match_scenario(h_micro, keys)
    check("auto-match micro prefix", k1 == "micro_lag_ecoli")
    m_ok, _ = match_scenario(_good(candidate_scenario="opt_rastrigin2d"), keys)
    check("explicit candidate honored", m_ok == "opt_rastrigin2d")
    m_bad, note_bad = match_scenario(
        _good(candidate_scenario="does_not_exist"), keys)
    check("nonexistent candidate -> None", m_bad is None)
    check("nonexistent candidate explains", "不在可用场景库" in note_bad)
    m_none, note_none = match_scenario(
        _good(disciplines=["microbiology", "chemical engineering"]),
        keys)
    # micro_ 命中 1、chem_ 命中 1 → 2 个候选 → 应拒（不硬凑）
    check("ambiguous match -> None (not guessing)", m_none is None)
    check("ambiguous match lists candidates",
          "候选" in note_none)

    # 11. 顶层：未准入 → 不给 proposal + 给补救
    v_bad = evaluate_hypothesis(_good(disciplines=["microbiology"]))
    check("unadmitted -> no carrier", v_bad.carrier_scenario is None)
    check("unadmitted -> no proposal", v_bad.proposal is None)
    check("unadmitted -> has remedy list",
          len(v_bad.what_would_make_it_verifiable) > 0)
    check("unadmitted disclaimer says not a truth judgment",
          "不是" in v_bad.disclaimer)

    # 12. 顶层：准入但无承载场景 → 明说无验证载体
    v_nocar = evaluate_hypothesis(
        _good(disciplines=["microbiology", "chemistry"]))
    check("admitted-but-no-carrier is not admitted",
          not v_nocar.admission.admitted)
    check("no-carrier disclaimer mentions 无验证载体",
          "无验证载体" in v_nocar.disclaimer)

    # 13. 映射表覆盖度回归（2026-10-08）
    #     起因：初版映射表漏了 `chemical engineering`（化学工程），
    #     而它是微生物场景最常见的副学科声明。漏了不会报错，
    #     只会让「双学科假设」被当成「单学科」匹配到唯一场景——
    #     这种缺口肉眼极难发现，故写成测试钉住。
    for dom in ("chemical engineering", "process engineering",
                "bioprocess", "bioreactor", "catalysis", "materials",
                "heat transfer", "thermodynamics", "microbial",
                "biological process", "fermentation", "neural"):
        check(f"domain hint exists: {dom}", dom in _DOMAIN_HINTS)

    # 用真实双学科组合验证不会静默塌成单学科
    k_ce, n_ce = match_scenario(
        _good(disciplines=["microbiology", "chemical engineering"]), keys)
    check("microbiology + chemical engineering is ambiguous (not silently 1)",
          k_ce is None)
    check("ambiguity note names both families",
          ("micro_" in n_ce and "chem_" in n_ce)
          or "候选" in n_ce)

    # 单学科仍应正常唯一匹配（不能被上面的修复误伤）
    k_one, _ = match_scenario(_good(disciplines=["microbiology"]), keys)
    check("single discipline still uniquely matched", k_one == "micro_lag_ecoli")

    # 14. schema 一致
    d = v_bad.to_dict()
    check("schema tag present", d["schema"] == _SCHEMA)
    check("admission serialized", "admission" in d)
    check("generated_at set", d["generated_at"] > 0)

    print(f"\n{passed}/{total} hypothesis-to-conclusion self-tests passed")
    return passed == total


if __name__ == "__main__":
    import sys
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if _selftest():
        # 用一个真实通过的假设跑通端到端
        h = HypothesisSpec(
            statement="葡萄糖限制浓度如何影响大肠杆菌最大比生长速率",
            disciplines=["microbiology", "chemical engineering"],
            open_question="Monod 常数随底物组成变化的机理未解",
            anchor_count=4, falsifiable=True,
            falsification_criterion="若实测 μmax 与 S0 无显著相关则推翻",
            transfer_hypothesis="结论应可迁移到酵母与放线菌",
            candidate_scenario="micro_lag_ecoli",
        )
        v = evaluate_hypothesis(h, repo_root=root)
        print("\n" + "=" * 70)
        print("端到端演示：假设 → 裁决 → 三零件")
        print("=" * 70)
        print(f"假设   {v.hypothesis}")
        print(f"准入   {'✅ 通过' if v.admission.admitted else '❌ 未通过'}")
        for p in v.admission.passed:
            print(f"        ✓ {p}")
        for f in v.admission.failures:
            print(f"        ✗ {f}")
        print(f"承载   {v.carrier_scenario}")
        for n in v.what_would_make_it_verifiable:
            print(f"  · {n}")
        print(f"\n{v.disclaimer}")
        sys.exit(0)
    sys.exit(1)
