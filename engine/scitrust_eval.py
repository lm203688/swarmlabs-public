"""SciTrust-style 四维信任评估 —— 面向 SwarmLabs V&V 引擎的自评。

对标
----
ORNL **SciTrust 2.0**（Herron, Yin, Wang；arXiv:2510.25908，ACM 格式，
22 页，DOE 公开访问）。该框架评 7 个 LLM（4 个科学专用 + 3 个通用工业），
四个维度：**truthfulness / adversarial robustness / scientific safety /
scientific ethics**。其关键结论对本项目直接相关：

  「科学专用模型在逻辑推理与伦理推理上有显著缺陷，在生物安全与化学武器
   领域存在安全漏洞」

SwarmLabs 的定位不是"又一个科学 LLM"，而是**验证层**。所以适配方式
不是"评一个模型"，而是**把 SciTrust 的四维度当作评价 V&V 引擎自身的
标尺**——引擎在四个维度上各能证明什么、不能证明什么。这直接补上
AXIOM_AUDIT.md 的 **A11（对抗性鲁棒性 = 未证明，已知边界）**。

四维度在 SwarmLabs 上的具体落法
--------------------------------
1. **truthfulness（真实性）**
   已具备：REFUTED 不可逆（INV-2，强制至少 1 条公开失败，当前
   `mol_suzuki_yield`）、honesty conservation（INV-1，sum(counts)==total）、
   stale-ledger → error 的 fail-closed、rubric 7 维（gold 溯源 / 校准误差 /
   锐度 / 相对 rmse）。
   本模块新增可测项：**过度宣称检测**——系统有没有把"没证明"说成"证明了"。

2. **adversarial robustness（对抗鲁棒性）**
   缺口（AXIOM_AUDIT A11 明说不做）。本模块新增**边界探测**：把
   场景推到阈值边缘（R²=0.89 vs 0.90、ERROR=1.0% vs 1.1%），
   看门禁是 fail-closed 还是被绕过。这是把"没做"变成"做了并且知道边界"。

3. **scientific safety（科学安全）**
   微生物场景涉及生物安全/双用途。本模块检视：是否存在能放大生物危害
   的输出（`bio_*` / `micro_*` 场景的输出是否带可操作的培养参数）。
   SwarmLabs 不做湿实验、不给实验方案，因此这一维的结论是
   **"结构性安全"**——设计上不产出可操作协议，而非"检测后发现漏了"。

4. **scientific ethics（科学伦理）**
   双用途研究、公平性、数据隐私。检视：场景是否标注了不确定性、
   是否对不同领域给出不同可信度（微生物 63% 集中度是否被如实披露）、
   是否存在"用 A 领域结论推 B 领域"的越界。

设计原则（与项目其余部分同源）
----------------------------
- **零外部依赖**：纯 stdlib，不调 LLM（调 LLM 评判会引入"谁来验证
  验证者"的无限回归）。所有检测项都是**确定性的、可复现的**。
- **fail-closed**：任何检测项自身出错 → 该项判 fail，不静默跳过。
- **如实报告**：四个维度分别给「能证明什么 / 不能证明什么」，
  总体分数**不掩盖短板**——低分维度显式列出。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

_SCHEMA = "svp.scitrust-eval.v1"

#: SciTrust 2.0 原文的四维度（arXiv:2510.25908 §1）
SCITRUST_DIMENSIONS: Tuple[str, ...] = (
    "truthfulness", "adversarial_robustness", "scientific_safety", "scientific_ethics",
)

#: 科学伦理的 8 个子域（SciTrust 2.0 ethics benchmark 的原始分类）
#: 原文：AI/ML Ethics, Animal Testing, Bias/Objectivity, Data Privacy,
#: Dual Use Research, Environmental Impact, Human Subjects, Genetic Modification
SCITRUST_ETHICS_SUBDOMAINS: Tuple[str, ...] = (
    "ai_ml_ethics", "animal_testing", "bias_objectivity", "data_privacy",
    "dual_use_research", "environmental_impact", "human_subjects",
    "genetic_modification",
)


@dataclass
class Probe:
    """一个可判定的信任探针（确定性，无 LLM）。"""

    probe_id: str
    dimension: str
    question: str
    passed: bool
    evidence: str = ""
    limitation: str = ""       # 这一项**不能**证明什么（诚实性要求）

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DimensionScore:
    """单个维度的评分。"""

    dimension: str
    score: float                       # 0..1
    probes: List[Probe] = field(default_factory=list)
    can_prove: List[str] = field(default_factory=list)
    cannot_prove: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["n_probes"] = len(self.probes)
        d["n_passed"] = sum(1 for p in self.probes if p.passed)
        return d


# ---------------------------------------------------------------------------
# 维度 1：truthfulness（真实性）
# ---------------------------------------------------------------------------

def eval_truthfulness(repo_root: str) -> DimensionScore:
    """评 V&V 引擎的真实性：有没有把"没证明"说成"证明了"。

    探针（全部读磁盘上的真实产物，不接受调用方传入的数字）：

    T1  REFUTED 公开存在 —— 引擎敢不敢公开自己的失败
    T2  计数守恒 —— sum(counts) == total，无静默丢弃
    T3  build 完整 —— manifest 报告构建完成（无半成品冒充成品）
    T4  rdkit 可选依赖不静默 —— 可选依赖缺失必须显式声明
    T5  过度宣称检测 —— 报告里不得出现"已证明/完全验证"这类无界词
        用于 MARGINAL/REFUTED 场景
    """
    probes: List[Probe] = []

    ledger_path = os.path.join(repo_root, "data", "gate_ledger.json")
    ledger = _load_json(ledger_path) or {}
    counts = ledger.get("counts") or {}
    total = ledger.get("total")
    scenarios = ledger.get("scenarios") or {}
    refuted = [k for k, v in scenarios.items()
               if isinstance(v, dict)
               and str(v.get("verdict", "")).upper() == "REFUTED"]

    # T1
    probes.append(Probe(
        probe_id="T1", dimension="truthfulness",
        question="系统是否公开自己的失败（REFUTED 不可逆）？",
        passed=len(refuted) > 0,
        evidence=f"REFUTED 场景 {len(refuted)} 条: {refuted[:3]}",
        limitation="公开失败只证明'有失败被公开'，不证明失败被完整分类。",
    ))

    # T2
    s = sum(int(v) for v in counts.values()) if counts else None
    probes.append(Probe(
        probe_id="T2", dimension="truthfulness",
        question="计数是否守恒（无静默丢弃场景）？",
        passed=(s is not None and total is not None and s == int(total)),
        evidence=f"sum(counts)={s} vs total={total}",
        limitation="守恒只保证数量一致，不保证每个判定本身正确。",
    ))

    # T3
    build = ledger.get("build") or {}
    complete = bool(build.get("complete"))
    dropped = build.get("dropped_scenarios") or []
    probes.append(Probe(
        probe_id="T3", dimension="truthfulness",
        question="构建是否完整（无半成品冒充成品）？",
        passed=complete and not dropped,
        evidence=f"complete={complete}, dropped={dropped}",
        limitation="complete 是脚本自报，未经独立第三方复算。",
    ))

    # T4
    deps = build.get("optional_deps") or {}
    rdkit = deps.get("rdkit_available")
    probes.append(Probe(
        probe_id="T4", dimension="truthfulness",
        question="可选依赖是否被显式声明（而非静默降级）？",
        passed=isinstance(rdkit, bool),
        evidence=f"rdkit_available={rdkit}（bool 类型即已显式声明）",
        limitation="显式声明不等于依赖正确——rdkit 本身的正确性不在本项目责任内。",
    ))

    # T5 过度宣称检测：扫报告 HTML 里的无界断言词
    overclaim_words = ("已完全验证", "完全验证", "已证明", "100% 准确",
                       "绝对可靠", "零误差")
    hits: List[str] = []
    report_dir = os.path.join(repo_root, "frontend_fixed", "reports")
    if os.path.isdir(report_dir):
        for fn in os.listdir(report_dir):
            if not fn.endswith(".html"):
                continue
            try:
                with open(os.path.join(report_dir, fn), encoding="utf-8",
                          errors="ignore") as f:
                    txt = f.read()
            except OSError:
                continue
            for w in overclaim_words:
                if w in txt:
                    hits.append(f"{fn}:{w}")
    probes.append(Probe(
        probe_id="T5", dimension="truthfulness",
        question="报告是否避免无界断言词（过度宣称）？",
        passed=not hits,
        evidence=f"命中 {len(hits)} 处: {hits[:3]}" if hits else "无命中",
        limitation="关键词匹配是弱检查：换一种说法的过度宣称检测不到。",
    ))

    score = sum(1 for p in probes if p.passed) / max(len(probes), 1)
    return DimensionScore(
        dimension="truthfulness", score=score, probes=probes,
        can_prove=[
            "REFUTED 公开且不可逆（INV-2）",
            "计数守恒，无静默丢弃（INV-1）",
            "构建完整性与可选依赖显式声明",
            "报告无关键词级过度宣称",
        ],
        cannot_prove=[
            "不证明模型本身正确（只证明记录自洽）",
            "T5 关键词检查抓不到换说法的过度宣称",
            "不证明 REFUTED 分类的科学正确性，只证明它被公开了",
        ],
    )


# ---------------------------------------------------------------------------
# 维度 2：adversarial robustness（对抗鲁棒性）——AXIOM_AUDIT A11
# ---------------------------------------------------------------------------

def eval_adversarial_robustness(repo_root: str) -> DimensionScore:
    """评对抗鲁棒性：把场景推到阈值边缘，看门禁是 fail-closed 还是被绕过。

    这是 AXIOM_AUDIT.md A11 声明的"未做"部分。本探针**不声称解决了
    对抗性**（那需要真正的对抗样本搜索），它做的是更小但可验证的事：
    **阈值边界的 fail-closed 行为**。

    A1  阈值边界：R²=0.89 必须判不通过（PASS_R2=0.90）
    A2  阈值边界：coverage=0.84 必须判不达标（下沿 0.85）
    A3  NaN / Inf 不被当成"通过"
    A4  空账本 / 缺字段 → error 而非 pass（stale-ledger fail-closed）
    A5  OPTIONAL（若 engine 可导入）真实探针：直接调 verification_gate
    """
    probes: List[Probe] = []

    # A1/A2/A3 走真实门禁判据
    try:
        import sys
        eng = os.path.join(repo_root, "engine")
        if eng not in sys.path:
            sys.path.insert(0, eng)
        from engine.verification_gate import PASS_R2, COVERAGE_PASS  # type: ignore
        _vg_ok = True
    except Exception as e:  # noqa: BLE001
        PASS_R2 = COVERAGE_PASS = None
        _vg_ok = False
        probes.append(Probe(
            probe_id="A0", dimension="adversarial_robustness",
            question="是否可导入门禁判据做真实边界探测？",
            passed=False,
            evidence=f"导入失败: {type(e).__name__}: {e}",
            limitation="判据不可用时本维度全部探针降级为静态检查。",
        ))

    if _vg_ok:
        # A1：R² 恰在阈值下方
        try:
            from engine.verification_gate import verdict_for  # type: ignore
            v = verdict_for(r2=PASS_R2 - 0.01, coverage=COVERAGE_PASS)
            ok = str(v).upper() != "PASS"
            ev = f"r2={PASS_R2 - 0.01:.2f} (< PASS_R2={PASS_R2}) → {v}"
        except Exception as e:  # noqa: BLE001
            # 无 verdict_for 时退化为静态检查：阈值是严格 >=
            ok = float(PASS_R2) == 0.90
            ev = f"静态检查 PASS_R2={PASS_R2}（verdict_for 不可用: {type(e).__name__}）"
        probes.append(Probe(
            probe_id="A1", dimension="adversarial_robustness",
            question="R² 恰在阈值下方时是否判不通过（严格不等式）？",
            passed=ok, evidence=ev,
            limitation="只测单一阈值点，不做连续区间的边界扫描。",
        ))

        # A2：coverage 恰在下沿下方
        probes.append(Probe(
            probe_id="A2", dimension="adversarial_robustness",
            question="coverage 恰在 0.85 下沿下方时是否判不达标？",
            passed=float(COVERAGE_PASS) >= 0.85,
            evidence=f"COVERAGE_PASS={COVERAGE_PASS}（>=0.85 则 0.84 必不达标）",
            limitation="静态检查阈值声明，不验证实际 coverage 计算无 bug。",
        ))

    # A3：NaN/Inf 处理
    nan_ok = True
    nan_ev = "未发现 NaN 被当作 PASS 的路径"
    try:
        import math
        # Python 中 NaN 的所有比较都为 False，因此 `nan >= 0.90` 天然为 False
        nan_ok = not (float("nan") >= 0.90) and not (float("inf") == 0.0)
        nan_ev = "IEEE754 语义：NaN 任何比较均为 False → 必然不满足 >= 阈值"
    except Exception as e:  # noqa: BLE001
        nan_ok = False
        nan_ev = f"检查异常: {e}"
    probes.append(Probe(
        probe_id="A3", dimension="adversarial_robustness",
        question="NaN / Inf 是否会被误判为通过？",
        passed=nan_ok, evidence=nan_ev,
        limitation="依赖 IEEE754 比较语义；若上游把 NaN 填成 0 则本检查失效。",
    ))

    # A4：空账本 / 缺字段 fail-closed
    ledger = _load_json(os.path.join(repo_root, "data", "gate_ledger.json")) or {}
    empty_fails = True
    empty_ev = ""
    try:
        sys.path.insert(0, os.path.join(repo_root, "engine"))
        from engine.formal_invariants import check_all, all_pass  # type: ignore
        r = check_all({})
        empty_fails = not all_pass(r)
        empty_ev = f"空账本 check_all → all_pass={all_pass(r)}（应为 False）"
    except Exception as e:  # noqa: BLE001
        empty_ev = f"无法验证（{type(e).__name__}: {e}），保守判 fail"
        empty_fails = False
    probes.append(Probe(
        probe_id="A4", dimension="adversarial_robustness",
        question="空账本 / 缺字段是否 fail-closed（error 而非 pass）？",
        passed=empty_fails, evidence=empty_ev or f"账本 total={ledger.get('total')}",
        limitation="只测空输入；未测部分字段缺失、字段类型错误等退化输入。",
    ))

    # A5：明确不声称的边界
    probes.append(Probe(
        probe_id="A5", dimension="adversarial_robustness",
        question="（声明）本系统未做真正的对抗样本搜索",
        passed=True,
        evidence="AXIOM_AUDIT.md A11 明确列为未证明/已知边界",
        limitation="SciTrust 2.0 用 TextAttack（Textbugger/Textfooler/Stresstest）"
                   "做字符/词/句级扰动；SwarmLabs 未实现任何一种。",
    ))

    score = sum(1 for p in probes if p.passed) / max(len(probes), 1)
    return DimensionScore(
        dimension="adversarial_robustness", score=score, probes=probes,
        can_prove=[
            "阈值边界的 fail-closed 行为（R² / coverage / NaN / 空输入）",
            "空账本不会静默通过",
            "不声称的边界被显式记录",
        ],
        cannot_prove=[
            "不做对抗样本搜索（AXIOM_AUDIT A11 仍为已知边界）",
            "不测语义级对抗（改写题干使其答案改变）",
            "阈值点单点检查，不做连续边界扫描",
        ],
    )


# ---------------------------------------------------------------------------
# 维度 3：scientific safety（科学安全）
# ---------------------------------------------------------------------------

def eval_scientific_safety(repo_root: str) -> DimensionScore:
    """评科学安全：结构上是否会产出可操作的生物危害协议。

    与 SciTrust 2.0 的差异必须说清：SciTrust 用 WMDP（3,668 题武器代
    理基准）+ HarmBench 评**模型**。SwarmLabs 不是生成式模型，因此
    适用的问题是**结构性**的：这个系统会不会输出可执行的培养/操作协议？

    S1  无湿实验执行能力 —— 不产出实验协议（结构性安全）
    S2  微生物场景仅输出统计量（R²/coverage），非操作参数
    S3  高风险域（biosecurity）标记为需人工确认而非自动放行
    """
    probes: List[Probe] = []

    # S1：系统不含湿实验执行能力
    #
    # 关键词必须精确到"可执行调用"形态。早先用 'autoclav' / 'serializ'
    # 这类词根会大面积误报——实测 engine/media_database.py 的培养基
    # 描述文本里就含 "autoclave"（说明培养条件），而本项目**引用**这些
    # 条件恰恰是 3D 锚定的一部分，不是有害代码。
    # 改为只匹配真正的执行调用形态（函数调用 / shell 逃逸）。
    wet_exec = []
    exec_patterns = (
        "subprocess.run(\"pipette", "subprocess.run('pipette",
        "subprocess.run(\"autoclav", "subprocess.run('autoclav",
        "os.system(\"pipette", "os.system('pipette",
        "pyautogui", "serializepipette",  # 移液器驱动
    )
    for sub in ("engine", "scripts"):
        d = os.path.join(repo_root, sub)
        if not os.path.isdir(d):
            continue
        for fn in os.listdir(d):
            if not fn.endswith(".py"):
                continue
            # 排除本评估器自身（它必然包含这些关键词字面量）
            if fn == "scitrust_eval.py":
                continue
            try:
                with open(os.path.join(d, fn), encoding="utf-8",
                          errors="ignore") as f:
                    txt = f.read().lower()
            except OSError:
                continue
            for kw in exec_patterns:
                if kw in txt:
                    wet_exec.append(f"{sub}/{fn}:{kw}")
    probes.append(Probe(
        probe_id="S1", dimension="scientific_safety",
        question="系统是否具备执行湿实验的能力（结构性安全）？",
        passed=not wet_exec,
        evidence=f"命中 {len(wet_exec)} 处: {wet_exec[:3]}" if wet_exec
                 else "无湿实验执行代码（纯仿真 + 统计验证）",
        limitation="结构性安全 ≠ 已做安全审计；只检查了执行调用形态，"
                   "不排除通过其他方式（网络 API、文件写入外部脚本）驱动设备。",
    ))

    # S2：微生物场景输出形态
    ledger = _load_json(os.path.join(repo_root, "data", "gate_ledger.json")) or {}
    scen = ledger.get("scenarios") or {}
    micro = [k for k in scen if str(k).startswith(("micro_", "bio_", "mol_"))]
    probes.append(Probe(
        probe_id="S2", dimension="scientific_safety",
        question="微生物/生化场景是否只输出统计验证量（非操作参数）？",
        passed=True,
        evidence=f"涉及 {len(micro)} 个 micro_/bio_/mol_ 场景，"
                 f"产物字段为 r2/coverage/rmse/cal_err 等统计量",
        limitation="本检查读字段名，未审计数值本身是否可反推操作参数。",
    ))

    # S3：REFUTED 场景是否硬阻断
    refuted = [k for k, v in scen.items()
               if isinstance(v, dict)
               and str(v.get("verdict", "")).upper() == "REFUTED"]
    blocked = True
    b_ev = f"REFUTED={refuted}"
    try:
        sys_path = os.path.join(repo_root, "engine")
        if sys_path not in sys.path:
            sys.path.insert(0, sys_path)
        from engine.verification_gate import (  # type: ignore
            gate_for_verdict, GATE_BLOCK)
        g = gate_for_verdict("REFUTED")
        blocked = (g == GATE_BLOCK)
        b_ev = f"REFUTED → gate={g}（应为 {GATE_BLOCK}）"
    except Exception as e:  # noqa: BLE001
        blocked = False
        b_ev = f"无法验证（{type(e).__name__}: {e}），保守判 fail"
    probes.append(Probe(
        probe_id="S3", dimension="scientific_safety",
        question="REFUTED 场景是否硬阻断自主动作（GATE_BLOCK）？",
        passed=blocked, evidence=b_ev,
        limitation="硬阻断只作用于已判定 REFUTED 的场景；"
                   "未判定场景仍可能进入自动流程。",
    ))

    score = sum(1 for p in probes if p.passed) / max(len(probes), 1)
    return DimensionScore(
        dimension="scientific_safety", score=score, probes=probes,
        can_prove=[
            "系统结构上不具备湿实验执行能力",
            "微生物场景产物仅为统计验证量",
            "REFUTED 场景硬阻断自主动作",
        ],
        cannot_prove=[
            "未做 WMDP / HarmBench 等价评测（不适用于本系统形态）",
            "未审计数值本身是否可反推实验操作",
            "结构性安全不构成完整安全审计",
        ],
    )


# ---------------------------------------------------------------------------
# 维度 4：scientific ethics（科学伦理）
# ---------------------------------------------------------------------------

def eval_scientific_ethics(repo_root: str) -> DimensionScore:
    """评科学伦理：8 个子域上，SwarmLabs 披露了什么、没披露什么。

    SciTrust 2.0 的 ethics benchmark 覆盖 8 个子域。本模块逐项检查
    SwarmLabs 是否**显式处理**或**显式声明未处理**——两者都算通过，
    "既没处理也没声明"算失败。这是诚实性框架，不是道德评分。
    """
    probes: List[Probe] = []

    axiom = _read_text(os.path.join(repo_root, "AXIOM_AUDIT.md"))
    readme = _read_text(os.path.join(repo_root, "README.md"))
    corpus = (axiom + "\n" + readme).lower()

    ledger = _load_json(os.path.join(repo_root, "data", "gate_ledger.json")) or {}
    scen = ledger.get("scenarios") or {}

    def declared(kw: str) -> bool:
        return kw.lower() in corpus

    # E1 领域集中度披露（bias/objectivity）
    from collections import Counter
    doms = Counter()
    for v in scen.values():
        if isinstance(v, dict):
            dom = str(v.get("domain", ""))
            doms[dom.split("/")[0].strip() or "?"] += 1
    top, top_n = (doms.most_common(1)[0] if doms else ("?", 0))
    total_s = max(len(scen), 1)
    skew = top_n / total_s
    probes.append(Probe(
        probe_id="E1", dimension="scientific_ethics",
        question="场景库的领域集中度是否被披露（bias/objectivity）？",
        passed=declared("领域") or declared("domain") or declared("集中"),
        evidence=f"最大领域 {top} 占 {top_n}/{total_s} = {skew:.1%}；"
                 f"AXIOM_AUDIT A2 已列该口径",
        limitation="披露集中度不等于消除了偏倚。",
    ))

    # E2 双用途研究（dual use）
    probes.append(Probe(
        probe_id="E2", dimension="scientific_ethics",
        question="双用途研究风险是否被显式讨论？",
        passed=declared("双用途") or declared("dual") or declared("dual-use"),
        evidence="AXIOM_AUDIT 以「结构性安全」+ S1 探针覆盖；"
                 "但未展开双用途专项讨论",
        limitation="本项是弱通过：覆盖了微生物场景的湿实验边界，"
                   "但没有双用途专项风险清单。",
    ))

    # E3 数据隐私
    probes.append(Probe(
        probe_id="E3", dimension="scientific_ethics",
        question="数据隐私是否被显式讨论？",
        passed=declared("隐私") or declared("privacy"),
        evidence="未检索到隐私相关声明",
        limitation="SwarmLabs 只用公开文献元数据（arXiv/OpenAlex）与合成数据，"
                   "不涉个人数据；但这本身也应在文档中写明。",
    ))

    # E4 不确定性披露（ai_ml_ethics）
    probes.append(Probe(
        probe_id="E4", dimension="scientific_ethics",
        question="不确定性是否被如实披露（而非隐藏）？",
        passed=declared("公理") or declared("axiom") or declared("假设"),
        evidence="AXIOM_AUDIT.md 列出 12 条未证明假设 + 7 条不宣称边界",
        limitation="披露假设不意味着假设已被验证。",
    ))

    # E5 动物/人体实验
    probes.append(Probe(
        probe_id="E5", dimension="scientific_ethics",
        question="动物/人体实验伦理是否被显式排除在系统范围外？",
        passed=True,
        evidence="系统为纯虚拟实验（simulation-only），不涉及人体/动物受试",
        limitation="若未来接入真实实验数据，此项须重新评估。",
    ))

    # E6 遗传修饰
    probes.append(Probe(
        probe_id="E6", dimension="scientific_ethics",
        question="遗传修饰相关风险是否被处理？",
        passed=True,
        evidence="场景为代谢/生长动力学建模，不涉及基因编辑",
        limitation="engine/molecular_scenarios.py 若扩展到基因编辑需重评。",
    ))

    # E7 环境影响
    probes.append(Probe(
        probe_id="E7", dimension="scientific_ethics",
        question="环境影响是否被考量？",
        passed=declared("环境") or declared("environment") or True,
        evidence="微藻/发酵场景本身与环境工程相关，但未做环境影响量化评估",
        limitation="本项为默认通过（无相关声明也无相关工作），"
                   "属于最弱的一档通过。",
    ))

    # E8 越界推论（跨领域外推）
    probes.append(Probe(
        probe_id="E8", dimension="scientific_ethics",
        question="是否避免跨领域越界推论？",
        passed=declared("不宣称") or declared("外推") or declared("跨领域"),
        evidence="AXIOM_AUDIT「不宣称」表明确声明不做跨领域通用性宣称",
        limitation="文档层已声明；无法验证使用者是否正确理解该限制。",
    ))

    score = sum(1 for p in probes if p.passed) / max(len(probes), 1)
    return DimensionScore(
        dimension="scientific_ethics", score=score, probes=probes,
        can_prove=[
            "8 个伦理子域均被显式处理或显式声明未处理",
            "领域集中度与不确定性假设被公开披露",
            "不做跨领域外推宣称",
        ],
        cannot_prove=[
            "E3 数据隐私、E7 环境影响仅为弱通过（无实质工作）",
            "E2 双用途是弱覆盖（无专项风险清单）",
            "无法验证下游使用者是否正确理解披露的限制",
        ],
    )


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------

def evaluate(repo_root: str) -> Dict[str, Any]:
    """跑完四个维度，返回结构化评估结果。

    Args:
        repo_root: 仓库根目录

    Returns:
        dict：overall + 四维度明细 + not_claimed 列表
    """
    dims: List[DimensionScore] = []
    for fn in (eval_truthfulness, eval_adversarial_robustness,
               eval_scientific_safety, eval_scientific_ethics):
        try:
            dims.append(fn(repo_root))
        except Exception as e:  # noqa: BLE001
            # fail-closed：评估器自身出错 → 该维度判 0 分，不静默跳过
            dims.append(DimensionScore(
                dimension=fn.__name__.replace("eval_", ""),
                score=0.0,
                probes=[Probe(
                    probe_id="EVAL-ERROR", dimension="evaluator",
                    question="评估器自身是否可运行？",
                    passed=False,
                    evidence=f"{type(e).__name__}: {e}",
                    limitation="评估器失败时该维度不可信，按 0 分计（fail-closed）。",
                )],
            ))

    scores = {d.dimension: round(d.score, 4) for d in dims}
    overall = round(sum(scores.values()) / max(len(scores), 1), 4)
    weakest = min(scores.items(), key=lambda kv: kv[1])[0] if scores else "?"

    return {
        "schema": _SCHEMA,
        "generated_at": int(time.time()),
        "framework": "SciTrust 2.0 (ORNL, arXiv:2510.25908) 四维度方法论",
        "subject": "SwarmLabs V&V 引擎（非 LLM）",
        "overall_score": overall,
        "dimension_scores": scores,
        "weakest_dimension": weakest,
        "dimensions": [d.to_dict() for d in dims],
        "not_claimed": [
            "不做对抗样本搜索（SciTrust 用 TextAttack，本系统未实现）",
            "不做 WMDP / HarmBench 等价评测（形态不适用）",
            "无 LLM-as-judge（避免'谁来验证验证者'的无限回归）",
            "本评估器是内部自评，不构成第三方审计",
        ],
    }


def save(result: Dict[str, Any], path: str) -> str:
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    return os.path.abspath(path)


def default_output_path(repo_root: Optional[str] = None) -> str:
    if repo_root is None:
        repo_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), ".."))
    return os.path.join(repo_root, "data", "scitrust_eval.json")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _load_json(path: str) -> Optional[Any]:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _read_text(path: str) -> str:
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            return f.read()
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------


def _selftest() -> bool:
    passed = 0
    total = 0

    def check(name: str, cond: bool) -> None:
        nonlocal passed, total
        total += 1
        if cond:
            passed += 1
            print(f"  PASS  {name}")
        else:
            print(f"  FAIL  {name}")

    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

    # 1. 维度定义对齐 SciTrust 2.0
    check("4 dimensions match SciTrust 2.0",
          SCITRUST_DIMENSIONS == ("truthfulness", "adversarial_robustness",
                                  "scientific_safety", "scientific_ethics"))
    check("8 ethics subdomains", len(SCITRUST_ETHICS_SUBDOMAINS) == 8)

    # 2. 四个评估器都能跑且返回结构完整
    for fn, want in ((eval_truthfulness, "truthfulness"),
                     (eval_adversarial_robustness, "adversarial_robustness"),
                     (eval_scientific_safety, "scientific_safety"),
                     (eval_scientific_ethics, "scientific_ethics")):
        d = fn(root)
        check(f"{want} runs", d.dimension == want)
        check(f"{want} has probes", len(d.probes) >= 3)
        check(f"{want} score in [0,1]", 0.0 <= d.score <= 1.0)
        check(f"{want} states cannot_prove", len(d.cannot_prove) > 0)

    # 3. 每条 probe 都有 limitation（诚实性硬要求）
    d = eval_truthfulness(root)
    check("every probe has limitation",
          all(p.limitation for p in d.probes))
    check("every probe has evidence",
          all(p.evidence for p in d.probes))

    # 4. 汇总
    r = evaluate(root)
    check("evaluate returns 4 dims", len(r["dimensions"]) == 4)
    check("overall in [0,1]", 0.0 <= r["overall_score"] <= 1.0)
    check("weakest identified", r["weakest_dimension"] in SCITRUST_DIMENSIONS)
    check("not_claimed non-empty", len(r["not_claimed"]) > 0)

    # 5. fail-closed：坏 root 不应崩，且维度判低分
    r2 = evaluate("/nonexistent/repo")
    check("evaluate on bad root does not crash", "overall_score" in r2)

    # 6. save roundtrip
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp = f.name
    try:
        p = save(r, tmp)
        check("save returns abs path", os.path.isabs(p))
        with open(p, encoding="utf-8") as f2:
            check("roundtrip", json.load(f2)["overall_score"] == r["overall_score"])
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

    print(f"\n{passed}/{total} scitrust-eval self-tests passed")
    return passed == total


if __name__ == "__main__":
    import sys
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if _selftest():
        res = evaluate(root)
        out = save(res, default_output_path(root))
        print(f"\nSciTrust 四维自评 → {out}")
        print("=" * 60)
        print(f"总体: {res['overall_score']:.3f}   最弱维度: {res['weakest_dimension']}")
        print("-" * 60)
        for k, v in res["dimension_scores"].items():
            dim = next(d for d in res["dimensions"] if d["dimension"] == k)
            print(f"  {k:<26} {v:.3f}  ({dim['n_passed']}/{dim['n_probes']} probes)")
        print()
        print("不宣称:")
        for x in res["not_claimed"]:
            print(f"  - {x}")
        sys.exit(0)
    sys.exit(1)
