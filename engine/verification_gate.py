#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SwarmLabs 验证闸门 —— 「这一场景能不能被自主推进」的**单一真源**。

为什么单独成模块
----------------
同一件事（"R²/覆盖率到什么程度才算可信"）在本项目里被四处使用：
引擎报告判定、MCP 验证闸门、HTTP 门禁账本、每日飞轮硬闸门。
任何一处各写一份，迟早出现两套口径——那对"可信验证层"这个定位是致命的。
所有组件必须经由本模块拿结论。

战略语境（2026-09）
------------------
通用研究智能体（GPT-6 Astra 在 Insilico DDD 成药性基准登顶、Stanford Biomni、
DrugAgent）已能**无微调**生成科研结果；行业综述的一致警告是它们
"prone to plausible-but-wrong outputs, weak guardrails"，要求
"held-out benchmark + 量化不确定度 + 人类 checkpoint"。
本模块就是那道 checkpoint 的机器可执行形式：
**置信度不达标的场景，自主动作被硬阻断。**

红线（与 engine/report_generator.py 同一数值体系，不随拟合下调）
--------------------------------------------------------------
- 噪声地板 3% 恒定；
- 覆盖率单侧保守（区间过窄 → 判失败，而非放行）；
- κ 只放宽不确定度、绝不收窄（上限 40）。

设计哲学："No Anchored Counter, No PASS"（借自 Keygraph/Shannon 的
"No Exploit, No Report"，2026-09-16 引入）
--------------------------------------------------------------
Shannon 白盒渗透测试器的原则是：只报告有可复现 PoC 的漏洞；假设不成立
就丢弃，绝不进入最终报告。搬到这里就是——**一个场景只有在下面任一
「对照物」在位时才允许 PASS：**
  (a) 解析解 / 已发表基准（gold 值）；
  (b) 湿实验锚点（engine/wet_lab_anchors.py 中的 AGREE/NEAR）；
  (c) 独立复算（不同 surrogate 后端一致，且覆盖率达标）。
没有 (a)/(b)/(c) 任一 → 强制 MARGINAL 及以下，绝不 PASS。
这是 fail-closed 语义在 verdict→gate 映射上的对应物：GATE_MAP 未知
等级一律 BLOCK 是同一原则在决策层的落地。
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from typing import Any, Dict, List, Optional

from engine.ablation_verdict import grade_from_plan, grade_distribution

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---- 阈值（唯一来源）----
NOISE_FLOOR = 0.03
NOMINAL_COVERAGE = 0.95
PASS_R2 = 0.90
MARGINAL_R2 = 0.70
COVERAGE_PASS = 0.90
COVERAGE_MARGINAL = 0.80
KAPPA_CAP = 40.0

# ---- 判定等级 ----
GRADE_PASS = "PASS"
GRADE_MARGINAL = "MARGINAL"
GRADE_REFUTED = "REFUTED"
GRADE_ERROR = "ERROR"

# ---- 门禁决策 ----
GATE_PROCEED = "PROCEED"
GATE_HUMAN = "PROCEED_WITH_HUMAN_CHECK"
GATE_BLOCK = "BLOCK_AUTONOMOUS_ACTION"

GATE_MAP: Dict[str, str] = {
    GRADE_PASS: GATE_PROCEED,
    GRADE_MARGINAL: GATE_HUMAN,
    GRADE_REFUTED: GATE_BLOCK,
    GRADE_ERROR: GATE_BLOCK,
}

_GATE_LABEL: Dict[str, str] = {
    GATE_PROCEED: "autonomous action permitted",
    GATE_HUMAN: "human sign-off required before acting",
    GATE_BLOCK: "autonomous action blocked (confidence below threshold)",
}

DEFAULT_LEDGER = os.path.join(ROOT, "frontend_fixed", "reports", "gate_ledger.json")
DATA_LEDGER = os.path.join(ROOT, "data", "gate_ledger.json")


class ActionBlocked(RuntimeError):
    """在置信度不达标的场景上尝试自主动作时抛出。"""


def gate_for_verdict(verdict: str) -> str:
    """判定等级 → 门禁决策。未知等级一律硬阻断（fail-closed，不 fail-open）。"""
    return GATE_MAP.get(verdict, GATE_BLOCK)


def gate_label(gate: str) -> str:
    return _GATE_LABEL.get(gate, "unknown gate")


def policy() -> Dict:
    """门禁策略的**机器可读**描述——HTTP 门禁账本 / 公开基准清单 / MCP 共用同一份。

    任何对外暴露"我们怎么判"的地方都必须引用这里，不得各写一份。
    """
    return {
        "noise_floor": NOISE_FLOOR,
        "nominal_coverage": NOMINAL_COVERAGE,
        "pass": f"R2>={PASS_R2} and one-sided coverage>={COVERAGE_PASS}",
        "marginal": f"R2>={MARGINAL_R2} and coverage>={COVERAGE_MARGINAL}",
        "refuted": "below the MARGINAL bars",
        "kappa": f"variance-scale, only widens uncertainty, capped at {KAPPA_CAP:g}",
        "gate_map": dict(GATE_MAP),
    }


#: 95% 双侧区间的标准正态分位。与 engine/report_generator.py 的 Z95 同值——
#: 公开留出集上的 coverage 复算（HTTP Worker，无 numpy）必须用同一个数，
#: 否则"线上判定"与"报告判定"会给出不同的覆盖率。
Z95 = 1.96


def thresholds() -> Dict:
    """**纯数值**阈值，供无法 import Python 的消费者使用。

    HTTP Worker 跑不了 numpy，但它在 `/v3/verify` 里要复算 R²/coverage 并给出
    verdict —— 如果把这些阈值在 JS 里再抄一份，本项目就同时有了两套"什么叫
    通过"。所以由本模块导出数值，构建期写进公开基准清单，Worker 运行时读取：
    **数字只有一份，搬运过程可审计。**
    """
    return {
        "pass_r2": PASS_R2,
        "marginal_r2": MARGINAL_R2,
        "coverage_pass": COVERAGE_PASS,
        "coverage_marginal": COVERAGE_MARGINAL,
        "noise_floor": NOISE_FLOOR,
        "nominal_coverage": NOMINAL_COVERAGE,
        "kappa_cap": KAPPA_CAP,
        "z95": Z95,
        "grades": {
            "pass": GRADE_PASS,
            "marginal": GRADE_MARGINAL,
            "refuted": GRADE_REFUTED,
            "error": GRADE_ERROR,
        },
        "gate_map": dict(GATE_MAP),
        "source": "engine/verification_gate.py::thresholds()",
    }


def ledger_entry(key: str, ledger: Optional[dict] = None) -> Optional[dict]:
    """取单场景账本条目（不存在返回 None，不抛）。"""
    led = ledger if ledger is not None else load_ledger()
    if not led:
        return None
    return (led.get("scenarios") or {}).get(key)


def load_ledger(path: Optional[str] = None) -> Optional[dict]:
    """读取离线生成的门禁账本；不存在返回 None。"""
    for p in ([path] if path else [DEFAULT_LEDGER, DATA_LEDGER]):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            continue  # error_kind:DEGRADABLE — 尝试下一个候选账本路径；全部失败返回 None（调用方需判空）
    return None


# --------------------------------------------------------------------------
# 湿实验锚点索引 —— 第二条证据链与六维评分的接线
# --------------------------------------------------------------------------
#
# 历史缺陷：_find_anchor_field 只查账本条目内嵌的 4 个键
# （anchors / wet_lab_anchors / reference_anchors / published_refs），
# 但这 4 个键在 gate_ledger 的 82 个场景条目里**一个都不存在**——
# wet_lab_anchors.py 的产物从未写回账本。于是 anchor 维度 82/82 全部
# 落到默认分支，均值恒为 0.3000，是一个**死常量**，不反映任何真实锚定状态。
#
# 后果：已完成的锚定工作（19 条锚点、11 个场景分级 AGREE/NEAR/
# CONTRADICTED/UNANCHORED）对六维评分**完全没有影响**。anchor 卡在 0.300
# 的机制原因不是「锚定不足」，而是「锚定数据从未被接线」。
#
# 修法：按 scenario_key 做 join。wet_lab_anchors.json 的 scenarios 列表
# 每项自带 scenario_key + grade，与本模块账本的键天然对齐。
# --------------------------------------------------------------------------

WET_LAB_ANCHORS_JSON = os.path.join(
    ROOT, "frontend_fixed", "reports", "wet_lab_anchors.json")

_WET_LAB_ANCHOR_INDEX: Optional[Dict[str, dict]] = None


def load_wet_lab_anchor_index(path: Optional[str] = None) -> Dict[str, dict]:
    """加载湿实验锚点表并按 scenario_key 建索引；失败返回空字典。

    只读 `scenarios` 列表（每项含 scenario_key / grade / gate / checks），
    不读 anchors 列表——anchors 是参数级锚点，与场景级评分不是一回事。
    任何异常都退化为空索引，让评分回到「未评估」分支而不是让门禁崩掉。

    传 path 强制重读；不传则用进程内缓存（ablation_plan_batch 会对
    82 个场景逐场景调用，逐次读盘不可接受）。
    """
    global _WET_LAB_ANCHOR_INDEX
    if path is None and _WET_LAB_ANCHOR_INDEX is not None:
        return _WET_LAB_ANCHOR_INDEX
    p = path or WET_LAB_ANCHORS_JSON
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return {}  # error_kind:IGNORABLE — 索引缺失时降级为空 dict，_find_anchor_field 有默认分支
    out: Dict[str, dict] = {}
    for item in (d.get("scenarios") or []):
        k = item.get("scenario_key") if isinstance(item, dict) else None
        if isinstance(k, str) and k:
            out[k] = item
    if path is None:
        _WET_LAB_ANCHOR_INDEX = out
    return out


def anchor_assessment(key: str, entry: Optional[dict] = None) -> dict:
    """解析一个场景的湿实验锚定状态。

    优先级：
      1. 账本条目内嵌 anchor 字段（向后兼容——若未来把锚点写回账本）
      2. wet_lab_anchors.json 的 scenarios 索引（当前唯一真实来源）
      3. 都查不到 -> assessed=False

    返回键集固定（调用方不必写 `if in`）：
      grade         AGREE / NEAR / CONTRADICTED / UNANCHORED / None
      assessed      该场景是否被 wet_lab_anchors 评估过（有信息 vs 零信息）
      source        entry_field / wet_lab_anchors / none
      gate          wet_lab_anchors 给出的决策闸门（仅 assessed 时有意义）
      why           人读的一句解释
    """
    if entry is not None:
        a = _find_anchor_field(entry)
        if a:
            # 内嵌字段可能是 {key: value} 包装，grade 可能在里层
            inner = next(iter(a.values())) if isinstance(a, dict) else None
            if isinstance(inner, dict) and inner.get("grade"):
                grade = inner.get("grade")
            else:
                grade = a.get("grade") if isinstance(a, dict) else None
            return {"grade": grade, "assessed": True,
                    "source": "entry_field", "gate": None,
                    "why": "账本条目内嵌 anchor 字段"}

    idx = load_wet_lab_anchor_index()
    item = idx.get(key)
    if item:
        return {"grade": item.get("grade"),
                "assessed": True,
                "source": "wet_lab_anchors",
                "gate": item.get("gate"),
                "why": "wet_lab_anchors.json 场景分级"}

    return {"grade": None, "assessed": False, "source": "none",
            "gate": None, "why": "wet_lab_anchors 未评估该场景（零信息）"}


def gate_for_scenario(key: str, ledger: Optional[dict] = None) -> Optional[str]:
    led = ledger if ledger is not None else load_ledger()
    if not led:
        return None
    entry = (led.get("scenarios") or {}).get(key)
    return entry.get("gate") if entry else None


def blocked_scenarios(ledger: Optional[dict] = None) -> List[str]:
    led = ledger if ledger is not None else load_ledger()
    if not led:
        return []
    return list(led.get("blocked") or [])


def assert_action_allowed(key: str, ledger: Optional[dict] = None,
                          enforce_fresh: bool = True) -> str:
    """自主动作的硬闸门：REFUTED/未知 → 抛 ActionBlocked。

    返回门禁决策字符串（允许时）。调用方应把它当作**不可绕过的把关**，
    而不是提示。

    ``enforce_fresh=True``（默认）会在放行前追加一道 **指纹新鲜度闸门**：
    若账本里的引擎摘要与**当前**引擎摘要不一致（意味着引擎或场景 oracle
    已被改过、但账本尚未重算），本函数抛 ActionBlocked——绝不沿用旧账本
    结论。这就是"指纹变更 → 强制重验"钩子：任何改动都会让 old ledger 静默
    失效，而不是被继续当成有效。

    关闭 ``enforce_fresh`` 只在**离线归档/诊断**路径上使用；生产路径永远开。
    """
    gate = gate_for_scenario(key, ledger)
    if gate is None:
        raise ActionBlocked(f"{key}: no gate ledger entry — fail closed")
    if gate == GATE_BLOCK:
        raise ActionBlocked(f"{key}: {gate_label(gate)}")
    if enforce_fresh:
        assert_ledger_not_stale(ledger)
    return gate


def summarize(ledger: Optional[dict] = None) -> dict:
    """给飞轮/日报用的紧凑摘要。"""
    led = ledger if ledger is not None else load_ledger()
    if not led:
        return {"available": False}
    return {
        "available": True,
        "total": led.get("total"),
        "counts": led.get("counts"),
        "n_blocked": led.get("n_blocked"),
        "n_human_check_required": led.get("n_human_check_required"),
        "blocked": led.get("blocked"),
        "generated_at_utc": led.get("generated_at_utc"),
    }


# --------------------------------------------------------------------------
# Cite-checker —— 借 HyperResearch 的 cite-checker 与 Shannon 的
# "No Exploit, No Report" 哲学。对每条 PASS，必须在 ledger 条目中找到
# **可核验的锚点引用**，否则降级为 MARGINAL 并列入 cite_violations。
#
# 锚点识别顺序（首个命中即通过）：
#   1) 条目内嵌 anchors 字段（wet_lab_anchors.py 的产物）；
#   2) entry["report"] 指向的报告文件里存在可追溯的引用（简单启发式）；
#   3) entry["replication_passed"] 为真（不同 surrogate 后端一致 = 独立复算）。
# 若三者皆空 → cite_violation，说明这是「R² 达标但无独立对照」的 PASS。
# --------------------------------------------------------------------------

CITE_ANCHOR_KEYS = ("anchors", "wet_lab_anchors", "reference_anchors", "published_refs")
CITE_REPLICATION_KEYS = ("replication_passed", "cross_backend_ok", "cross_engine_ok")


def _find_anchor_field(entry: dict) -> Optional[dict]:
    for k in CITE_ANCHOR_KEYS:
        v = entry.get(k)
        if v and (not isinstance(v, list) or any(v)):
            return {k: v}
    return None


def _replication_evidence(entry: dict) -> bool:
    for k in CITE_REPLICATION_KEYS:
        if entry.get(k) in (True, "true", 1):
            return True
    return False


def _report_file_has_refs(report_path: Optional[object]) -> bool:
    """支持 report 字段为字符串（相对路径）或 dict（含 html/json 相对路径）。

    路径约定（SwarmLabs 报告库）：
      - '/reports/html/xxx.html'、'/reports/json/xxx.json' 相对 frontend_fixed/
      - 也接受绝对路径与相对仓库根的路径
    """
    if not report_path:
        return False
    if isinstance(report_path, dict):
        cands = []
        for k in ("html", "json", "path", "file"):
            v = report_path.get(k)
            if isinstance(v, str):
                cands.append(v)
        for c in cands:
            if _report_file_has_refs(c):
                return True
        return False
    if not isinstance(report_path, str):
        return False
    FRONTEND = os.path.join(ROOT, "frontend_fixed")
    if report_path.startswith("/"):
        cands = [
            os.path.join(FRONTEND, report_path.lstrip("/")),
            os.path.join(ROOT, report_path.lstrip("/")),
        ]
    else:
        cands = [
            report_path,
            os.path.join(FRONTEND, report_path),
            os.path.join(ROOT, report_path),
        ]
    for cand in cands:
        if os.path.exists(cand):
            try:
                with open(cand, encoding="utf-8", errors="ignore") as f:
                    head = f.read(200000)
                # 简单启发式：JSON 报告有 "reference" / "anchor" / "gold" 字段；
                # HTML 报告有 <cite> / DOI 链接。
                for marker in ('"reference"', '"anchor"', '"gold"', "doi.org", "<cite>"):
                    if marker in head:
                        return True
                return False
            except Exception:
                return False  # error_kind:IGNORABLE — 文件读失败即视为未引用；不阻断后续候选路径
    return False


def cite_check_scenario(key: str, entry: dict) -> dict:
    """对一个 PASS 场景做锚点核查，返回 {'ok', 'reasons', 'violations'}。

    非 PASS 场景自动 ok（它们本身就不被宣称「通过」）。
    """
    verdict = (entry or {}).get("verdict") or (entry or {}).get("grade")
    if verdict != GRADE_PASS:
        return {"ok": True, "verdict": verdict, "reasons": [], "violations": []}
    reasons: List[str] = []
    viol: List[str] = []
    anchor = _find_anchor_field(entry)
    if anchor:
        reasons.append(f"anchor_fields:{','.join(anchor.keys())}")
    else:
        viol.append("no_anchor_field")
    if _replication_evidence(entry):
        reasons.append("replication")
    if _report_file_has_refs(entry.get("report")):
        reasons.append("report_has_refs")
    ok = len(reasons) > 0
    if not ok:
        viol.append("PASS_without_any_anchor_or_replication")
    return {"ok": ok, "verdict": verdict, "reasons": reasons, "violations": viol, "key": key}


def cite_check_ledger(ledger: Optional[dict] = None) -> dict:
    """对整个账本跑 cite-checker。

    返回 {checked, pass_count, violations: [{key, violations}], summary}
    这个函数**不修改账本**——它是巡检与披露工具，最终结果写进
    data/gate_ledger_cite_audit.json 供报告层引用；对账本本身的降级由
    外部编排（如 scripts/build_gate_ledger.py）根据本结果决定是否执行。
    """
    led = ledger if ledger is not None else load_ledger()
    if not led:
        return {"available": False}
    scenarios = led.get("scenarios") or {}
    results: List[dict] = []
    violations: List[dict] = []
    n_pass = 0
    for key, entry in scenarios.items():
        r = cite_check_scenario(key, entry)
        results.append(r)
        if r["verdict"] == GRADE_PASS:
            n_pass += 1
        if r["verdict"] == GRADE_PASS and not r["ok"]:
            violations.append({"key": key, "violations": r["violations"]})
    return {
        "available": True,
        "checked": len(results),
        "pass_count": n_pass,
        "pass_with_anchor": n_pass - len(violations),
        "violations": violations,
        "n_violations": len(violations),
    }


def assert_pass_has_anchor(key: str, ledger: Optional[dict] = None) -> None:
    """硬闸门：若该场景是 PASS 且无锚点 → 抛 ActionBlocked。

    这是 cite_check 的强制版本；调用方（例如 MCP 的 verify_prediction）
    可用它把哲学落地成运行时行为，而不只是账本字段。
    """
    led = ledger if ledger is not None else load_ledger()
    entry = ((led or {}).get("scenarios") or {}).get(key)
    if entry is None:
        return  # 无条目本身由 assert_action_allowed 处理
    r = cite_check_scenario(key, entry)
    if r["verdict"] == GRADE_PASS and not r["ok"]:
        raise ActionBlocked(
            f"{key}: PASS without any anchor/replication reference — "
            f"violations={r['violations']} (No Anchored Counter, No PASS)"
        )


# --------------------------------------------------------------------------
# 溯源指纹 —— 让一次 PASS 钉死在 **exact engine revision** 上
#
# 动机：账本此前只按 scenario key 索引，于是「这个 PASS 是对哪一版引擎算的」
# 无处可查。引擎一改（哪怕只改一个长度尺度），旧 PASS 会**静默地**被当成
# 仍然有效——这正是 AI4S 里最危险的一类过期信任：数字还在，含义已经变了。
# 把 engine 源码指纹写进账本，任何改动都会改变摘要，"沿用旧结论"从此是
# 一个可检测的事实，而不是默认行为。
# --------------------------------------------------------------------------

#: 对判定有直接影响的模块（写进账本可读区，便于人工比对）
CORE_ENGINE_MODULES = (
    "surrogate.py",             # GP 代理 + 场景空间
    "microbiology_full.py",     # 51 微生物场景真值函数
    "scenarios_extra.py",       # 跨域场景
    "verification_gate.py",     # 本模块：阈值与 verdict→gate 映射
    "guard.py",                 # 三态门禁
    "wet_lab_anchors.py",       # 湿实验锚点（第二证据链）
    "report_generator.py",      # 10 章 V&V 报告
)

#: oracle（场景真值函数）的定义源。R²=1.0 只证明"代理忠实于 oracle"，
#: 不证明"oracle 符合真实生物学"——所以"这批判定是对哪版 oracle 算的"
#: 必须独立可查（见 engine/wet_lab_anchors.py 的双证据链设计）。
ORACLE_SOURCE_MODULES = ("microbiology_full.py", "scenarios_extra.py", "surrogate.py")


def _sha256_file(path: str) -> str:
    """对**内容**取摘要，换行符统一为 LF 后再哈希。

    为什么必须规范化（2026-09-21 修）
    ---------------------------------
    此前直接哈希原始字节，于是同一份仓库在不同平台上算出**不同指纹**：
    仓库索引里存的是 LF，而 `core.autocrlf=true` 的 Windows 工作区检出的
    是 CRLF。后果是账本在一台机器上"新鲜"、在另一台（含 CI 的 Linux 检出）
    上"过期"——`check_ledger_fresh.py` 于是**不可能跨平台同时为真**，
    这直接违背了「指纹可复算」的设计前提。

    规范化后，摘要只取决于仓库内容，与检出平台的换行风格无关。
    注意：这只改摘要的**输入规范化**，不改任何阈值或判定——重建账本后
    所有 verdict 逐位不变。
    """
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk.replace(b"\r\n", b"\n"))
    return h.hexdigest()


def _sha256_map(files: Dict[str, str]) -> str:
    return hashlib.sha256(
        json.dumps(files, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _git_head_commit() -> str:
    """读 ``.git`` 拿 HEAD commit，不依赖 git 命令（构建环境常无 git）。"""
    git = os.path.join(ROOT, ".git")
    try:
        with open(os.path.join(git, "HEAD"), encoding="utf-8") as f:
            ref = f.read().strip()
    except Exception:
        return ""  # error_kind:IGNORABLE — 无 .git 时返回空串，调用方按 "unavailable" 处理
    if ref.startswith("ref:"):
        try:
            with open(os.path.join(git, ref.split(":", 1)[1].strip()),
                      encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            return ""  # error_kind:IGNORABLE — ref 文件缺失则退回符号 ref 串
    return ref


def _load_remote_commit_pin() -> Dict[str, Any]:
    """读 ``.workbuddy-ai/last_push.json``（由 scripts/write_remote_commit_pin.py 写入）。

    该文件记录远端 main 最新 commit SHA + 时间 + 作者；本地 HEAD 与远端不一致
    是**常态**（Contents API 推送不推进本地 HEAD），因此本地 HEAD 只能作参考，
    远端 SHA 才是权威绑定。

    Returns:
        pin record dict，文件不存在/损坏返回空 dict。
    """
    pin = os.path.join(ROOT, ".workbuddy-ai", "last_push.json")
    try:
        with open(pin, encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict) or "remote" not in d:
            return {}
        return d
    except Exception:
        return {}  # error_kind:IGNORABLE — pin 缺失时退回"仅本地 HEAD"档，调用方仍能工作


def _hash_modules(names) -> Dict[str, str]:
    d = os.path.join(ROOT, "engine")
    out: Dict[str, str] = {}
    for name in names:
        p = os.path.join(d, name)
        try:
            out[name] = _sha256_file(p)
        except Exception:
            continue          # 文件缺失不阻断建账本；缺项本身就是可读事实
    return out


def engine_fingerprint() -> dict:
    """engine/ 源码指纹：``core``（可读核心模块）+ ``all_digest``（全量摘要）。

    Commit 绑定采用三档可信度（地基承重审计报告 §九 · 硬骨头 4）：

    1. **remote（权威）**：远端 main 最新 commit SHA，由
       ``scripts/write_remote_commit_pin.py`` 通过 Contents API 查询后写入
       ``.workbuddy-ai/last_push.json``。这是唯一可信的 commit 绑定，因为
       本仓库的推送走 Contents API，服务器端每次 PUT 都新建一个 commit，
       本地 HEAD 从不推进。
    2. **local（参考）**：本地 ``.git/HEAD``。仅作辅助线索——Contents API
       推送不会回写本地，本地 HEAD 通常**落后于**远端。
    3. **unavailable**：本地无 ``.git`` 时（构建环境常见）。

    无论哪一档，``all_digest`` 始终是**内容层**的权威绑定（对文件内容取
    SHA-256 摘要），与 commit 无关。commit 只是**时间戳层**的定位线索。

    本函数**不再**返回 "advisory only" 措辞——改用分级字段
    ``git_commit_binding_tier`` 明示当前档；调用方按需降级。
    """
    d = os.path.join(ROOT, "engine")
    all_files: Dict[str, str] = {}
    if os.path.isdir(d):
        for name in sorted(os.listdir(d)):
            if name.endswith(".py"):
                try:
                    all_files[name] = _sha256_file(os.path.join(d, name))
                except Exception:
                    continue  # error_kind:IGNORABLE — 单文件 hash 失败跳过；n_modules 与 all_digest 仍自洽
    core = _hash_modules(CORE_ENGINE_MODULES)
    local_commit = _git_head_commit()
    pin = _load_remote_commit_pin()
    remote = pin.get("remote") or {}
    remote_sha = remote.get("sha") or ""
    tier = "unavailable"
    if remote_sha:
        tier = "remote==local" if remote_sha == local_commit else "remote"
    elif local_commit:
        tier = "local"
    return {
        "git_commit": remote_sha or local_commit,
        "git_commit_source": (
            "remote via Contents API (pinned .workbuddy-ai/last_push.json)"
            if remote_sha else
            ("local .git HEAD" if local_commit else "unavailable")
        ),
        "git_commit_binding_tier": tier,
        "git_commit_binding": (
            "tier=remote: authoritative remote SHA pinned from Contents API; "
            "local HEAD may be behind (Contents API pushes create server-side "
            "commits that never advance local HEAD)"
            if tier == "remote" else
            ("tier=remote==local: remote and local HEAD agree" if tier == "remote==local"
             else ("tier=local: local HEAD only — run scripts/write_remote_commit_pin.py "
                   "to promote to remote tier"
                   if tier == "local" else
                   "tier=unavailable: no .git locally and no remote pin on disk"))
        ),
        "remote_commit_meta": {
            "message": remote.get("message", ""),
            "author": remote.get("author", ""),
            "committer_date": remote.get("committer_date", ""),
            "pinned_at_utc": pin.get("pinned_at_utc", ""),
        } if remote_sha else {},
        "local_head": local_commit,
        "n_modules": len(all_files),
        "core": core,
        "core_digest": _sha256_map(core),
        "all_digest": _sha256_map(all_files),
    }


def oracle_digest() -> dict:
    """场景 oracle 的源码指纹（第二证据链的第一环）。"""
    files = _hash_modules(ORACLE_SOURCE_MODULES)
    return {"files": files, "digest": _sha256_map(files)}


def ledger_is_stale(ledger: Optional[dict] = None) -> dict:
    """账本里的引擎指纹 vs **当前**引擎指纹是否一致。

    这是防止"过期信任"的运行时闸门：飞轮/服务在签发基于账本的决定前应当
    先问一次。不一致不代表结论错，但意味着**结论尚未针对当前引擎重算过**。
    """
    led = ledger if ledger is not None else load_ledger()
    if not led:
        return {"available": False, "reason": "no ledger"}
    prov = (led.get("provenance") or {})
    cur = engine_fingerprint()
    old = prov.get("engine") or {}
    return {
        "available": True,
        "ledger_all_digest": old.get("all_digest"),
        "current_all_digest": cur.get("all_digest"),
        "stale": bool(old.get("all_digest")) and old["all_digest"] != cur["all_digest"],
        "ledger_git_commit": old.get("git_commit"),
        "current_git_commit": cur.get("git_commit"),
        "ledger_generated_at_utc": led.get("generated_at_utc"),
    }


def assert_ledger_not_stale(ledger: Optional[dict] = None) -> dict:
    """硬闸门版本：账本过期则抛 ActionBlocked，绝不放行旧结论。

    「指纹变更 → 强制重验」的可执行形式。ledger_is_stale() 只是读事实；
    本函数把事实升级成决策：**引擎或场景 oracle 有任一改动而账本没刷新，
    所有基于该账本的判定（无论 PASS/MARGINAL）都拒绝沿用。**

    典型触发路径：
      - scripts/build_reports.py 跑完 → build_gate_ledger 刷新账本
        → ledger 内 all_digest == engine_fingerprint() 的 all_digest → 通过
      - 开发者改了 engine/surrogate.py 但没重跑账本
        → all_digest 变了 → 本函数抛 ActionBlocked
        → 飞轮/MCP/HTTP 侧都不会把旧 PASS 当成有效结论

    返回 ledger_is_stale() 的诊断字典（便于调用方 log）。
    """
    status = ledger_is_stale(ledger)
    if not status.get("available"):
        # 无账本 = 完全无凭据，也不放行；这与 assert_action_allowed 的
        # "no gate ledger entry — fail closed" 一致。
        raise ActionBlocked(
            "no gate ledger available — fail closed "
            "(run scripts/build_gate_ledger.py before consuming gates)"
        )
    if status.get("stale"):
        raise ActionBlocked(
            f"ledger engine fingerprint stale — refusing to reuse old verdicts. "
            f"ledger_all_digest={status.get('ledger_all_digest','?')[:16]}… "
            f"current_all_digest={status.get('current_all_digest','?')[:16]}… "
            f"(run scripts/build_gate_ledger.py to recompute against the current engine)"
        )
    return status


def force_revalidate_reason(ledger: Optional[dict] = None) -> Optional[str]:
    """给运维/日报用的诊断文本：账本为什么需要重算（不需要则返回 None）。

    与 assert_ledger_not_stale() 相对：本函数**不抛**，只返回原因，
    便于把「该重算」写进日报/CI log，而不是让飞轮直接死掉。
    """
    status = ledger_is_stale(ledger)
    if not status.get("available"):
        return "no ledger"
    if status.get("stale"):
        return (
            f"engine all_digest mismatch: ledger={status.get('ledger_all_digest','?')[:16]}… "
            f"current={status.get('current_all_digest','?')[:16]}…"
        )
    return None


# ---- 校准概率裁决（借 Jev noul + Laya typed-decisions + 错误成本阈值）----
#
# 背景（2026-09-20）：
#   TypeSafe Jev 1.13（2026-09-15）与 Convai Innovations Laya（2026-09-18）
#   都采用"输出概率而非生成 JSON"的决策层模式——单次前向返回类型化概率分布，
#   confidence 从分布派生而非独立字段；Jev 明确建议 "Act / Review / Human"
#   三路径，阈值随错误成本变化。Laya 用 RLCD（严格评分规则做 RL 奖励）训练
#   让"诚实概率"成为训练目标。
#
# SwarmLabs 现状：gate_decision 只输出离散 verdict，边缘场景直接翻档、
#   无中间态；κ 校准是启发式，coverage=0.95 是约束而非训练目标。
#
# 本区块把裁决映射为连续概率 + 从分布派生 confidence（Jev 模式），
# 并支持错误成本阈值参数化（Act/Review/Human 三路径）。
#
# 与硬 verdict 的关系：hard_verdict 保留兼容（旧代码仍读 verdict 字段），
#   probabilities 是新增字段。两者由同一份证据分量派生，不会出现两套口径。
# 与 Laya/Jev 的边界：Laya/Jev 做决策层（421M ModernBERT，33ms），SwarmLabs
#   做裁决层（GP+UQ，秒级）—— 不是竞品，是分层互补。这里借鉴的是**输出形式**
#   与**训练哲学**，不是模型架构或权重。

def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def _score_scale(val: float, low: float, high: float) -> float:
    """线性映射到 [-1, 1]：low → -1, high → +1。"""
    if high <= low:
        return 0.0
    t = (val - low) / (high - low)
    return _clamp01(t) * 2.0 - 1.0


def _evidence_components(entry: dict, key: str = "") -> dict:
    """从 ledger entry 抽证据分量：返回原始值 + 归一化分数。

    `key` 用于查湿实验锚点索引——锚定数据不在账本条目内，必须按
    scenario_key join（见 load_wet_lab_anchor_index 的注释）。
    """
    u = entry.get("uncertainty") or {}
    r2 = float(u.get("r2", 0) or 0)
    coverage = float(u.get("coverage", 0) or 0)
    kappa = float(u.get("calibration_scale_kappa", 1) or 1)
    cal_err = float(u.get("cal_err", 0.1) or 0.1)

    anchor = anchor_assessment(key, entry)
    anchor_grade = anchor.get("grade")
    replication_ok = bool(entry.get("replication_passed", False))

    return {
        "raw": {
            "r2": r2,
            "coverage": coverage,
            "kappa": kappa,
            "cal_err": cal_err,
            "anchor_grade": anchor_grade,
            # 是否真被评估过——AGREE 与「零信息」必须能区分
            "anchor_assessed": anchor["assessed"],
            "anchor_source": anchor["source"],
            "replication_ok": replication_ok,
        },
        "scores": {
            # R²: MARGINAL_R2 (0.70) → -1, PASS_R2 + 0.05 (0.95) → +1
            "r2": _score_scale(r2, MARGINAL_R2, PASS_R2 + 0.05),
            # Coverage: COVERAGE_MARGINAL (0.80) → -1, NOMINAL_COVERAGE (0.95) → +1
            "coverage": _score_scale(coverage, COVERAGE_MARGINAL, NOMINAL_COVERAGE),
            # κ: 1 (无额外放大) → -1, 20 (合理保守放大) → +1。κ 只在
            # coverage 不足时被拉起，权重小（0.05）所以负分影响有限。
            "kappa": _score_scale(kappa, 1.0, 20.0),
            # cal_err: 越小越好；0 → +1, 0.3 → -1
            "cal_err": _score_scale(-cal_err, -0.3, 0.0),
        },
    }


def calibrated_verdict(key: str, ledger: Optional[dict] = None,
                       cost_of_error: float = 1.0) -> dict:
    """校准概率版本的裁决。

    借鉴点：
      - Jev noul 模式：把离散 verdict 映射为连续概率分布
      - Jev Confidence 派生：从分布派生 confidence，不是独立字段
      - Jev Act/Review/Human 三路径：错误成本参数化阈值
      - Laya typed-decisions：多类型题型独立评估（此处一次返回 4 档）

    Args:
        key: 场景 key
        ledger: 可选账本字典（None 时自动加载）
        cost_of_error: 错误成本权重（0-10）
            - <1.0  低风险（默认 1.0）：宽松阈值，Act 更容易触发
            - 1.5-3.0 中等风险：默认阈值
            - >=3.0 高风险（涉及人/资金/合规）：保守阈值，更多 REVIEW

    Returns:
        {
            "hard_verdict": "PASS|MARGINAL|REFUTED|UNVALIDATED|ERROR",
            "probabilities": {"PASS", "MARGINAL", "REFUTED", "UNVALIDATED"},
            "confidence": float,
            "recommendation": "ACT|REVIEW|HUMAN",
            "evidence": {...},
            "cost_of_error": float,
            "cost_thresholds": {...},
            "rationale": str,
            "domain": str,
        }
    """
    # 三路径阈值（借 Jev Act/Review/Human）
    if cost_of_error >= 3.0:
        thr_pass, thr_review = 0.95, 0.75
    elif cost_of_error >= 1.5:
        thr_pass, thr_review = 0.90, 0.60
    else:
        thr_pass, thr_review = 0.80, 0.50

    e = ledger_entry(key, ledger)
    if e is None:
        return {
            "hard_verdict": "UNVALIDATED",
            "probabilities": {
                "PASS": 0.0, "MARGINAL": 0.0,
                "REFUTED": 0.0, "UNVALIDATED": 1.0,
            },
            "confidence": 1.0,
            "recommendation": "HUMAN",
            "evidence": {},
            "cost_of_error": cost_of_error,
            "cost_thresholds": {"pass": thr_pass, "review": thr_review},
            "rationale": "no ledger entry — fail closed",
            "domain": "",
        }

    parts = _evidence_components(e)
    s = parts["scores"]
    raw = parts["raw"]

    # 加权合成 —— R² 主导 (0.45)，覆盖率次之 (0.30)，κ 弱信号 (0.05)
    #   anchor/replication 只做加分、不做减分（SwarmLabs 62 场景只有 6 个
    #   有 anchor，若做减分会让绝大多数 PASS 场景被误判为可疑）
    composite = (
        0.45 * s["r2"]
        + 0.30 * s["coverage"]
        + 0.05 * s["kappa"]
        + 0.20 * s["cal_err"]
        + (0.05 if raw["anchor_grade"] in ("AGREE", "NEAR") else 0.0)
        + (0.05 if raw["replication_ok"] else 0.0)
    )
    composite = max(-1.0, min(1.0, composite))

    # 映射到 4 档概率 —— 中心点 0.5（PASS 门槛）/ -0.5（REFUTED 门槛），
    # 宽度 0.15：完美场景 composite≈0.85 → P_PASS≈0.90；
    # MARGINAL 场景 composite≈0 → P_PASS≈0.37；
    # REFUTED 场景 composite≈-0.7 → P_REFUTED≈0.83。
    p_pass = _sigmoid((composite - 0.5) / 0.15)
    p_refuted = _sigmoid((-composite - 0.5) / 0.15)
    p_marginal = max(0.0, 1.0 - p_pass - p_refuted)
    p_unvalidated = max(0.0, 1.0 - p_pass - p_refuted - p_marginal)

    # 归一化到 1
    total = p_pass + p_refuted + p_marginal + p_unvalidated
    if total > 1e-9:
        p_pass /= total
        p_refuted /= total
        p_marginal /= total
        p_unvalidated /= total

    # 展示层舍入到 4 位 —— 但四个分量独立舍入会让和偏离 1
    # （如 0.9999），违反「概率分布和=1」的不变量（CI 校准门禁断言
    # abs(sum-1)<1e-6）。用最大余量法：先舍入，再把残差补到最大分量，
    # 保证和恒等于 1 且每个分量仍是 4 位小数。
    r_pass = round(p_pass, 4)
    r_refuted = round(p_refuted, 4)
    r_marginal = round(p_marginal, 4)
    r_unvalidated = round(p_unvalidated, 4)
    residual = round(1.0 - (r_pass + r_refuted + r_marginal + r_unvalidated), 4)
    if residual != 0.0:
        if r_pass >= r_refuted and r_pass >= r_marginal and r_pass >= r_unvalidated:
            r_pass = round(r_pass + residual, 4)
        elif r_refuted >= r_marginal and r_refuted >= r_unvalidated:
            r_refuted = round(r_refuted + residual, 4)
        elif r_marginal >= r_unvalidated:
            r_marginal = round(r_marginal + residual, 4)
        else:
            r_unvalidated = round(r_unvalidated + residual, 4)
    p_pass, p_refuted, p_marginal, p_unvalidated = r_pass, r_refuted, r_marginal, r_unvalidated

    # confidence 从分布派生（Jev 模式：max - 0.5 × 次大差）
    sorted_probs = sorted([p_pass, p_refuted, p_marginal, p_unvalidated], reverse=True)
    confidence = _clamp01(sorted_probs[0] - 0.5 * sorted_probs[1])

    # hard_verdict 保持一致（离散判定不变，仅补概率视图）
    hard = e.get("verdict", "UNVALIDATED")
    if hard not in (GRADE_PASS, GRADE_MARGINAL, GRADE_REFUTED, GRADE_ERROR):
        hard = "UNVALIDATED"

    # 三路径建议（Act / Review / Human）
    if p_pass >= thr_pass and hard == GRADE_PASS:
        recommendation = "ACT"
    elif p_pass + p_marginal >= thr_review:
        recommendation = "REVIEW"
    else:
        recommendation = "HUMAN"

    return {
        "hard_verdict": hard,
        "probabilities": {
            "PASS": p_pass,
            "MARGINAL": p_marginal,
            "REFUTED": p_refuted,
            "UNVALIDATED": p_unvalidated,
        },
        "confidence": round(confidence, 4),
        "recommendation": recommendation,
        "evidence": raw,
        "cost_of_error": cost_of_error,
        "cost_thresholds": {"pass": thr_pass, "review": thr_review},
        "rationale": e.get("rationale", ""),
        "domain": e.get("domain", ""),
    }


def calibrated_verdict_batch(keys: List[str], ledger: Optional[dict] = None,
                             cost_of_error: float = 1.0) -> Dict[str, dict]:
    """批量校准评估（借 Jev 并行独立评估模式）。

    一次调用返回多个场景的校准概率——Laya/Jev 都强调"多问题并行独立评估，
    增加问题几乎不改变响应时间"，这里做等价的批量入口。
    """
    led = ledger if ledger is not None else load_ledger()
    return {k: calibrated_verdict(k, led, cost_of_error) for k in keys}


# --------------------------------------------------------------------------
# 消融规划器 —— 借 ScientistTwo Ablation Planner（2026-09-21）
# --------------------------------------------------------------------------
#
# ScientistTwo 的做法：对最优 ML 模型自动生成 N_p 个消融变体（删除/简化各组件），
# 量化每个组件对性能的贡献。本模块的等价物不是消融 ML 架构，而是：
#
#   对每个 PASS 场景，系统化地评估**验证覆盖率**——识别哪些验证维度已覆盖、
#   哪些缺失，并生成补强计划。
#
# 验证维度（六维）：
#   1. fidelity    — R² 模型保真度（vs oracle）
#   2. coverage    — 覆盖率（UQ 校准度）
#   3. anchor      — 湿实验锚定（vs 真实文献）
#   4. replication — 独立复算（不同后端一致）
#   5. benchmark   — 已发表基准对照
#   6. sensitivity — 灵敏度分析（Sobol/DOE）
#
# 每个维度 0–1 分，总分 < 0.5 → 需要补强。
# --------------------------------------------------------------------------

#: 验证维度名称 → (ledger 字段路径, 判定说明)
_VERIFY_DIMENSIONS: dict[str, tuple] = {
    "fidelity":    ("uncertainty.r2",               "R² ≥ 0.90 = 1.0, ≥ 0.70 = 0.5, 否则 0.0"),
    "coverage":    ("uncertainty.coverage",         "coverage ≥ 0.95 = 1.0, ≥ 0.90 = 0.7, ≥ 0.80 = 0.3, 否则 0.0"),
    "anchor":      ("_anchor_field",
                    "AGREE=1.0, NEAR=0.7, CONTRADICTED=0.0, UNANCHORED=0.3, "
                    "未评估=0.3（来源 wet_lab_anchors.json 按 scenario_key join）"),
    "replication": ("replication_passed",           "True=1.0, False=0.0"),
    "benchmark":   ("_has_benchmark",              "场景有解析解或已发表基准值 = 1.0, 否则 0.0"),
    "sensitivity": ("_has_sensitivity",            "存在 sobol/doe/uq 变体 = 1.0, 否则 0.0"),
}

#: 灵敏度分析场景前缀（已有 sobol/doe/uq 变体）
_SENSITIVITY_PREFIXES = ("micro_sobol_", "micro_doe_", "micro_uq_", "micro_mle_")

#: 灵敏度变体场景缓存（延迟加载，避免循环引用）
_ABLATED_SCENARIOS_CACHE: set | None = None

#: A-4 参数扰动审计缓存（data/sensitivity_ablation.json，延迟加载）
_SENSITIVITY_AUDIT_CACHE: dict | None = None

#: B-1 第二后端复算缓存（data/second_backend_replication.json，延迟加载）
_SECOND_BACKEND_CACHE: dict | None = None


def _second_backend_replication(key: str) -> Optional[dict]:
    """读取 B-1 第二 surrogate 后端复算中该场景的记录（无则返回 None）。

    文件由 scripts/build_second_backend_replication.py 生成；缺失即视为
    未复算（replication 维度拿 0 分——fail-closed）。
    """
    global _SECOND_BACKEND_CACHE
    if _SECOND_BACKEND_CACHE is None:
        path = os.path.join(ROOT, "data", "second_backend_replication.json")
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            _SECOND_BACKEND_CACHE = data.get("scenarios") or {}
        except (OSError, json.JSONDecodeError):
            _SECOND_BACKEND_CACHE = {}
    return _SECOND_BACKEND_CACHE.get(key)


def _sensitivity_audit(key: str) -> Optional[dict]:
    """读取 A-4 参数扰动审计中该场景的记录（无文件/无记录返回 None）。

    文件由 scripts/run_sensitivity_ablation.py 生成；缺失即视为未审计
    （sensitivity 维度拿 0 分并注明原因——fail-closed，不默认放行）。
    """
    global _SENSITIVITY_AUDIT_CACHE
    if _SENSITIVITY_AUDIT_CACHE is None:
        path = os.path.join(ROOT, "data", "sensitivity_ablation.json")
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            _SENSITIVITY_AUDIT_CACHE = data.get("scenarios") or {}
        except (OSError, json.JSONDecodeError):
            _SENSITIVITY_AUDIT_CACHE = {}
    return _SENSITIVITY_AUDIT_CACHE.get(key)


def _score_dimension(entry: dict, dim: str, key: str = "") -> tuple[float, str]:
    """对单个验证维度打分，返回 (分数, 说明)。

    Args:
        entry: 账本条目
        dim: 维度名
        key: 场景 key（用于灵敏度变体检测）
    """
    if dim == "fidelity":
        r2 = float((entry.get("uncertainty") or {}).get("r2", 0) or 0)
        if r2 >= PASS_R2:
            return 1.0, f"R²={r2:.3f} ≥ {PASS_R2}"
        elif r2 >= MARGINAL_R2:
            return 0.5, f"R²={r2:.3f} ≥ {MARGINAL_R2} 但 < {PASS_R2}"
        return 0.0, f"R²={r2:.3f} < {MARGINAL_R2}"

    if dim == "coverage":
        cov = float((entry.get("uncertainty") or {}).get("coverage", 0) or 0)
        if cov >= NOMINAL_COVERAGE:
            return 1.0, f"coverage={cov:.3f} ≥ {NOMINAL_COVERAGE}"
        elif cov >= COVERAGE_PASS:
            return 0.7, f"coverage={cov:.3f} ≥ {COVERAGE_PASS} 但 < {NOMINAL_COVERAGE}"
        elif cov >= COVERAGE_MARGINAL:
            return 0.3, f"coverage={cov:.3f} ≥ {COVERAGE_MARGINAL}"
        return 0.0, f"coverage={cov:.3f} < {COVERAGE_MARGINAL}"

    if dim == "anchor":
        a = anchor_assessment(key, entry)
        if not a["assessed"]:
            # 从未被评估 ≠ 已确认无锚点。两者都拿不到湿实验证据，
            # 但行动建议相反：前者要补评估，后者要换方法。分数保持一致
            # （0.3，不改变任何 verdict 阈值语义），靠 note 区分。
            return 0.3, f"未评估：{a['why']}"
        grade = a["grade"] or ""
        if grade == "AGREE":
            return 1.0, f"anchor grade={grade}（{a['source']}）"
        if grade == "NEAR":
            return 0.7, f"anchor grade={grade}（{a['source']}）"
        if grade == "CONTRADICTED":
            # fail-closed：与文献矛盾比无锚点更糟，必须阻塞自主决策
            return 0.0, f"anchor grade={grade}（{a['source']}）— 需人工介入"
        if grade == "UNANCHORED":
            return 0.3, f"anchor grade={grade}（{a['source']}）— 无可锚定闭式模型"
        return 0.3, f"anchor grade={grade or '(未标)'}（{a['source']}）"

    if dim == "replication":
        ok = bool(entry.get("replication_passed", False))
        b1 = _second_backend_replication(key)
        b1_note = ""
        if b1 is not None and b1.get("decision") == "contradict":
            # 同后端复算通过 ≠ 结论对模型家族稳健。矛盾必须写进 note——
            # 分数取现有最佳独立证据，但反证不可隐没（论文亦逐名列名）。
            b1_note = (f"；⚠ 第二后端矛盾（GP={b1['gp']['grade']} vs "
                       f"RF={b1['rf']['grade']}，结论对模型家族敏感）")
        if ok:
            return 1.0, "replication_passed=True（同后端异种子复算）" + b1_note
        # B-1（2026-10-18）：同后端复算未过（或无）时，消费第二 surrogate
        # 后端（RF+Conformal）在同数据上的独立复算。两种复算形式测不同的
        # 东西（实现稳定性 vs 模型家族鲁棒性），任一通过即计分。
        if b1 is not None and b1.get("score") is not None:
            return (b1["score"],
                    f"第二后端复算（B-1）：GP={b1['gp']['grade']} vs "
                    f"RF={b1['rf']['grade']} → {b1['decision']}")
        return 0.0, "replication_passed=False（无第二后端复算数据，跑 scripts/build_second_backend_replication.py）"

    if dim == "benchmark":
        # 检查报告文件是否包含 "gold" 或 "reference" 或 "published"
        has = _report_file_has_refs(entry.get("report"))
        return (1.0, "报告含解析解/基准引用") if has else (0.0, "无基准引用")

    if dim == "sensitivity":
        # 检查该场景是否本身是灵敏度变体（按场景名推断）
        has = any(key.startswith(p) for p in _SENSITIVITY_PREFIXES)
        if not has:
            # 检查是否有对应的 sobol/doe/uq 变体场景存在于账本
            base = key
            for p in _SENSITIVITY_PREFIXES:
                if base.startswith("micro_"):
                    variant = base.replace("micro_", p, 1)
                    if variant in _ensure_ablated_cache():
                        has = True
                        break
        if has:
            return 1.0, f"存在灵敏度变体 ({key})"
        # A-4（2026-10-18）：无变体场景时，消费参数扰动稳健性审计。
        # score = 1 - flip_rate = 扰动配置中 verdict 存活的比例。
        # 审计测出「零翻转」与「有变体」是同等强度的 sensitivity 证据。
        audit = _sensitivity_audit(key)
        if audit is not None:
            fr = audit.get("flip_rate")
            if fr is None:
                return 0.0, "扰动审计无 applicable 配置（该场景全部扰动运行失败）"
            return (round(1.0 - fr, 3),
                    f"参数扰动审计（A-4）：{audit.get('n_applicable')} 配置中 "
                    f"{audit.get('n_flipped')} 个 verdict 翻转（flip_rate={fr:.2f}）")
        return 0.0, "无灵敏度变体、无扰动审计数据（跑 scripts/run_sensitivity_ablation.py）"

    return 0.0, "未知维度"


def _ensure_ablated_cache() -> set:
    """延迟加载灵敏度变体场景集合（避免在函数定义时加载账本）。"""
    global _ABLATED_SCENARIOS_CACHE
    if _ABLATED_SCENARIOS_CACHE is None:
        led = load_ledger()
        scenarios = (led.get("scenarios") or {}) if led else {}
        _ABLATED_SCENARIOS_CACHE = set(scenarios.keys())
    return _ABLATED_SCENARIOS_CACHE


def ablation_plan(key: str, ledger: Optional[dict] = None) -> dict:
    """为场景生成系统化验证补强计划。

    借 ScientistTwo 的 Ablation Planner 概念：不是消融 ML 架构，而是
    系统化地评估验证覆盖，找出缺口，生成补强计划。

    Args:
        key: 场景 key
        ledger: 可选账本字典

    Returns:
        {
            "scenario_key": str,
            "verdict": str,
            "overall_coverage": float,  # 六维平均分
            "dimensions": {dim: {"score": float, "note": str}},
            "gaps": [str],              # 得分 < 0.5 的维度
            "recommendations": [str],   # 针对每个缺口的补强建议
            "priority": "HIGH|MEDIUM|LOW",
        }
    """
    led = ledger if ledger is not None else load_ledger()
    entry = (led.get("scenarios") or {}).get(key) if led else None

    if entry is None:
        return {
            "scenario_key": key,
            "verdict": "NOT_FOUND",
            "overall_coverage": 0.0,
            "dimensions": {},
            "gaps": ["场景不在账本中"],
            "recommendations": ["运行 scripts/build_gate_ledger.py 重建账本"],
            "priority": "HIGH",
        }

    verdict = entry.get("verdict", "UNKNOWN")
    dims: dict[str, dict] = {}
    gaps: list[str] = []
    recs: list[str] = []

    for dim in _VERIFY_DIMENSIONS:
        score, note = _score_dimension(entry, dim, key=key)
        dims[dim] = {"score": round(score, 3), "note": note}
        if score < 0.5:
            gaps.append(dim)

    overall = sum(d["score"] for d in dims.values()) / max(len(dims), 1)

    # 针对缺口的补强建议
    if "fidelity" in gaps:
        recs.append("R² 不足 → 增加训练样本或降低噪声地板后重跑 V&V")
    if "coverage" in gaps:
        recs.append("覆盖率不足 → 增大校准集规模或启用 κ 缩放")
    if "anchor" in gaps:
        recs.append("无湿实验锚定 → 在 engine/wet_lab_anchors.py 中添加该场景的参数锚点")
    if "replication" in gaps:
        recs.append("无独立复算 → 用不同 surrogate 后端重跑并比对结果")
    if "benchmark" in gaps:
        recs.append("无已发表基准 → 补充解析解或文献对照值")
    if "sensitivity" in gaps:
        recs.append("无灵敏度分析 → 添加 sobol/doe/uq 变体场景")

    # 优先级
    if overall < 0.4:
        priority = "HIGH"
    elif overall < 0.7:
        priority = "MEDIUM"
    else:
        priority = "LOW"

    return {
        "scenario_key": key,
        "verdict": verdict,
        "verdict_grade": grade_from_plan({
            "dimensions": dims,
        }),
        "overall_coverage": round(overall, 4),
        "dimensions": dims,
        "gaps": gaps,
        "recommendations": recs,
        "priority": priority,
        "method_note": (
            "验证覆盖六维评估（借 ScientistTwo Ablation Planner 概念）：fidelity/coverage/anchor/"
            "replication/benchmark/sensitivity。总分 < 0.5 → 建议补强。verdict_grade 为分级判定"
            "（A-3）：核心维度 fidelity/coverage 缺一即 FAIL；支撑维度 anchor/replication/"
            "sensitivity 零分个数决定 PASS_FULL / PASS_PARTIAL / PASS_WEAK；benchmark 计分但不参与分级。"
        ),
    }


def ablation_plan_batch(keys: Optional[list[str]] = None,
                        ledger: Optional[dict] = None,
                        only_gaps: bool = False) -> dict:
    """批量消融规划。

    Args:
        keys: 场景 key 列表（None 时评估全部 PASS 场景）
        ledger: 可选账本字典
        only_gaps: True 时只返回有缺口的场景
    """
    led = ledger if ledger is not None else load_ledger()
    if keys is None:
        scenarios = led.get("scenarios") or {} if led else {}
        keys = sorted(k for k, v in scenarios.items()
                      if (v or {}).get("verdict") == GRADE_PASS)

    plans = {}
    for k in keys:
        p = ablation_plan(k, led)
        if not only_gaps or p["gaps"]:
            plans[k] = p

    return {
        "total_assessed": len(keys),
        "with_gaps": sum(1 for p in plans.values() if p["gaps"]),
        "by_priority": {
            "HIGH": sum(1 for p in plans.values() if p["priority"] == "HIGH"),
            "MEDIUM": sum(1 for p in plans.values() if p["priority"] == "MEDIUM"),
            "LOW": sum(1 for p in plans.values() if p["priority"] == "LOW"),
        },
        "grade_distribution": grade_distribution(plans),
        "plans": plans,
    }


if __name__ == "__main__":
    import pprint

    _audit = cite_check_ledger()
    pprint.pprint({"summarize": summarize(), "cite_audit": _audit})
