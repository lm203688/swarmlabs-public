#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verification-gate ledger builder.

把已发布的场景 V&V 判定（数量由注册表动态决定），转成一份**机器可读的门禁账本**：每个场景一条
`PROCEED / PROCEED_WITH_HUMAN_CHECK / BLOCK_AUTONOMOUS_ACTION` 决策 +
一等公民的不确定度预算（噪声地板 / κ / 单侧覆盖率 / 校准误差）。

为什么需要它
------------
1. **HTTP 侧的可信闸门**：CF Pages 跑不了 Python/numpy，任意预测的独立验证
   （`verify_prediction`）只能在 MCP/引擎侧做；但"这个场景现在能不能信"这个问题
   可以纯静态回答——把账本离线生成成 JSON，`/v3/gate` 直接托管即可，
   任何 HTTP 智能体都能在**不跑 Python** 的前提下查询门禁决策。
2. **飞轮硬闸门**：置信度不达标的场景（REFUTED）必须**阻断自主动作**——
   账本是飞轮里"哪些候选被阻断 + 为什么"的可审计留痕，对齐 5 态 board。
3. **口径单一**：门禁映射与 `mcp/swarmlabs_vv_server.py::_gate_decision` 完全一致，
   避免同一项目出现两套"什么叫可以推进"。

红线（与引擎一致，不随拟合下调）
--------------------------------
- 噪声地板 3% 恒定；κ 只放宽不收窄（上限 40）；覆盖率单侧保守判定。
- REFUTED 场景**真实保留**，不因"好看"从账本里删除。

用法
----
    python scripts/build_gate_ledger.py            # 生成账本
    python scripts/build_gate_ledger.py --print    # 生成并打印摘要
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# 口径唯一来源：engine/verification_gate.py（引擎/MCP/HTTP 账本/飞轮共用）
from engine import verification_gate as VG  # noqa: E402
from engine.verification_gate import (  # noqa: E402
    COVERAGE_MARGINAL,
    COVERAGE_PASS,
    GATE_MAP,
    KAPPA_CAP,
    MARGINAL_R2,
    NOMINAL_COVERAGE,
    NOISE_FLOOR,
    PASS_R2,
)

INDEX_PATH = os.path.join(ROOT, "frontend_fixed", "reports", "index.json")
OUT_PUBLIC = os.path.join(ROOT, "frontend_fixed", "reports", "gate_ledger.json")
OUT_DATA = os.path.join(ROOT, "data", "gate_ledger.json")


def _compute_calibration_meta(entries: dict) -> dict:
    """训练一个校准策略并计算 ECE（借 Laya/Jev RLCD 严格评分规则）。

    Args:
        entries: build_ledger 里已经构建好的 scenarios dict（key → entry）

    Returns:
        {
            "method": str, "steps": int, "lr": float,
            "ece": float,
            "ece_references": {"laya_zero_shot": 0.466, "laya_refit": 0.081, "jev": 0.246},
            "label_distribution": {...},
            "mean_log_likelihood": float,
            "mean_p_pass": float,
            "note": str,
        }

    失败时（缺 ledger / 训练异常）返回 {"error": ...}，不影响账本构建。
    """
    try:
        import numpy as np  # 校验 numpy 可用
        from engine.calibration_rl import CalibrationPolicy
    except Exception as e:
        return {"error": f"calibration_rl unavailable: {e}"}

    if not entries:
        return {"error": "no scenarios to calibrate"}

    # 训练一个线性 softmax 策略（策略梯度 + log-likelihood 奖励）
    policy = CalibrationPolicy()
    steps, lr, seed = 3000, 0.05, 0
    try:
        policy.fit(entries, steps=steps, lr=lr, seed=seed)
    except Exception as e:
        return {"error": f"policy training failed: {e}"}

    try:
        ece = policy.expected_calibration_error(entries)
    except Exception as e:
        return {"error": f"ECE computation failed: {e}"}

    # P_PASS 分布（诊断用）
    p_passes = [policy.predict_probabilities(e).get("PASS", 0.0) for e in entries.values()]
    mean_p_pass = float(np.mean(p_passes)) if p_passes else None
    # 训练集实际档位分布（动态，避免"62/62 PASS"类硬编码漂移）
    n_pass = sum(1 for e in entries.values() if (e.get("verdict") or "").upper() == "PASS")
    n_scenarios = len(entries)

    return {
        "method": "linear_softmax_rlcd_log_likelihood",
        "steps": steps,
        "lr": lr,
        "ece": round(float(ece), 4),
        "ece_references": {
            "laya_zero_shot": 0.466,
            "laya_refit_temperature": 0.081,
            "jev_1_13": 0.246,
        },
        "label_distribution": policy.fit_stats.get("label_distribution", {}),
        "mean_log_likelihood": round(
            float(policy.fit_stats.get("mean_log_likelihood", 0.0)), 4
        ),
        "mean_p_pass": round(float(mean_p_pass), 4) if mean_p_pass is not None else None,
        "n_scenarios": n_scenarios,
        "note": (
            "ECE (Expected Calibration Error) 度量报告概率与真实频率的匹配程度；"
            f"越低越好。当前 {n_pass}/{n_scenarios} PASS，训练集仍不均衡"
            "（账本已含 MARGINAL/REFUTED 场景），ECE 偏低主要是分布内验证，"
            "不代表生产泛化能力；外推域（A15）一律不给出 PASS。"
        ),
    }


def _next_step_for(verdict: str, gate: str, r2: float | None,
                   coverage: float | None, cal_err: float | None) -> dict:
    """给每个场景生成一份**可操作**的下一步建议。

    报告里说"REFUTED"用户读得懂，但"所以呢？"没有答案就是黑盒。
    本函数把 4 档 verdict × 常见失败模式映射成 6 条确定性的可操作建议：
    - PASS / PROCEED        → "已可信，直接使用"
    - MARGINAL / HUMAN_CHECK → 按失守维度给出 3 条候选路径
    - REFUTED / BLOCK       → 按失守维度给出 4 条候选路径
    - ERROR                 → "构建异常，重跑 scripts/build_reports.py"

    输出格式：{action, why, expected_effect, effort}
    - action:        一句话动作（可执行）
    - why:           为什么这一步（挂到失守维度）
    - expected_effect: 预期效果（不夸大）
    - effort:        S / M / L 三档
    """
    r2_flag = (r2 is not None and r2 < MARGINAL_R2)
    cov_flag = (coverage is not None and coverage < (NOMINAL_COVERAGE - 0.05))
    cal_flag = (cal_err is not None and cal_err > 0.10)

    if gate == "PROCEED" and verdict == "PASS":
        return {
            "action": "已可信，直接使用；结论附带校准区间与适用域边界",
            "why": "所有 rubric 维度通过（r² ≥ 0.99、coverage ≥ 0.95、cal_err < 0.05）",
            "expected_effect": "结论可直接落地到决策",
            "effort": "S",
        }

    # REFUTED 类：先按失守维度选主建议
    if verdict == "REFUTED":
        if cov_flag:
            return {
                "action": "先做外推压力测试：跑 scripts/validate_conformal_cross.py --scenario <key>",
                "why": f"coverage={coverage} < 标称 0.90；说明训练域外推时 GP 退化到训练集均值",
                "expected_effect": "定位是否为 A15 定义性质（外推不可达）",
                "effort": "M",
            }
        if r2_flag:
            return {
                "action": "先加密训练点或改用 multifidelity：见 engine/multifidelity.py::decide_mean_fn",
                "why": f"r²={r2} < 0.95；代理模型欠拟合",
                "expected_effect": "若仍不达 → 目标函数超出平滑代理适用域（A14 roughness > 0.3）",
                "effort": "M",
            }
        if cal_flag:
            return {
                "action": "跑 physics-constrained conformal 注入物理先验",
                "why": f"cal_err={cal_err} > 0.10；标称区间过宽",
                "expected_effect": "外推点覆盖率提升；仍不达 → 场景本身高频震荡",
                "effort": "M",
            }
        return {
            "action": "把该场景加入 SCENARIOS_DISABLED 或改用真实锚点验证",
            "why": "多指标同时失守，代理模型对该场景整体不适用",
            "expected_effect": "诚实记录失败；下一轮迭代再评估",
            "effort": "L",
        }

    # MARGINAL 类
    if gate == "PROCEED_WITH_HUMAN_CHECK":
        if cov_flag:
            return {
                "action": "跑 adaptive_conformal 滚动更新分位数",
                "why": f"coverage={coverage} 边缘低于阈值",
                "expected_effect": "覆盖回升到标称附近",
                "effort": "S",
            }
        return {
            "action": "人工复核：查看 report/html 与 traceability 字段",
            "why": "整体接近门槛但未越界",
            "expected_effect": "确定是否可降级使用",
            "effort": "S",
        }

    # ERROR / BLOCK 类
    return {
        "action": "重跑 scripts/build_reports.py 定位构建异常",
        "why": f"verdict={verdict}，构建层问题而非模型层",
        "expected_effect": "构建恢复后即可重新判定",
        "effort": "S",
    }


def build_ledger() -> dict:
    with open(INDEX_PATH, encoding="utf-8") as f:
        idx = json.load(f)

    scenarios = idx.get("scenarios", [])
    entries: dict = {}
    blocked, human_check = [], []

    for s in scenarios:
        key = s.get("key")
        verdict = s.get("verdict", "ERROR")
        gate = GATE_MAP.get(verdict, "BLOCK_AUTONOMOUS_ACTION")
        if gate == "BLOCK_AUTONOMOUS_ACTION":
            blocked.append(key)
        elif gate == "PROCEED_WITH_HUMAN_CHECK":
            human_check.append(key)

        r2 = s.get("r2")
        coverage = s.get("coverage")
        cal_err = s.get("cal_err")

        entries[key] = {
            "gate": gate,
            "verdict": verdict,
            "rationale": s.get("rationale"),
            "domain": s.get("domain"),
            "published": s.get("published"),
            "dim": s.get("dim"),
            "n_train": s.get("n_train"),
            "n_test": s.get("n_test"),
            # ---- first-class uncertainty budget ----
            "uncertainty": {
                "noise_floor": NOISE_FLOOR,
                "nominal_coverage": NOMINAL_COVERAGE,
                "coverage": coverage,
                "calibration_scale_kappa": s.get("calibration_scale"),
                "cal_err": cal_err,
                "r2": r2,
                "r2_core": s.get("r2_core"),
                "rmse_rel": s.get("rmse_rel"),
            },
            # ---- 可操作下一步（板块 04 P1，2026-10-15 新增）----
            # 报告说 REFUTED 但没告诉用户"所以呢？"就是黑盒。
            # 这里按失守维度给出确定性动作 + 预期效果 + 工作量。
            "actionable_next_step": _next_step_for(verdict, gate, r2, coverage, cal_err),
            "high_freq": s.get("high_freq"),
            "replication_passed": s.get("replication_passed"),
            "report": {
                "html": s.get("html"),
                "json": s.get("json"),
                "docx": s.get("docx"),
            },
        }

    counts = idx.get("counts", {})
    # 本次构建是否丢过场景（可选依赖缺失）。index.json 是唯一声明处；
    # 老产物没有该字段时按「没丢」处理，不能因此把账本判成不完整。
    dropped = list(((idx.get("optional_deps") or {})
                    .get("dropped_molecular_scenarios")) or [])
    return {
        "name": "SwarmLabs verification gate ledger",
        "version": datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0).strftime("%Y-%m-%d"),
        "generated_at_utc": datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "standard": idx.get("standard"),
        # ---- 构建完整性：账本自己是「哪一次构建」的产物 -------------------
        # 为什么这一块必须存在：/v3/gate 是**外部 agent 实际读的那个产物**，
        # 而它此前只搬运 counts/total，把 index.json 里的构建元数据全丢了。
        # 2026-09-23 实测：rdkit 缺失的解释器重建后，账本变成
        # 「78 PASS / 2 MARGINAL / 0 REFUTED」而**没有任何痕迹**说明有 2 个场景
        # 根本没被构建；唯一的 REFUTED 硬阻断（mol_suzuki_yield）就这样消失，
        # 公开账本看起来像「从未失败过」。诚实失败记录是本项目相对竞品的核心
        # 差异点，它被环境依赖悄悄删掉而产物不自证，是最坏的一类失真。
        # 现在把 drop 名单直接钉进账本：读者不必去翻 index.json 才能发现。
        "build": {
            "complete": not dropped,
            # 2026-10-16 晚评估报告 §7 P0-3 架构反转修复：
            # 数据层（gate_ledger.json）的 build.source 曾指向展示层
            # （frontend_fixed/reports/index.json），让读者以为数据是从
            # 展示产物派生——这正是历史上 82/95 分母混乱的根因。
            # 单一真源是构建脚本本身；index.json 只是同一构建过程的
            # 兄弟产物，不作为数据层的"来源"。
            "source": "scripts/build_gate_ledger.py",
            "reads_from": "frontend_fixed/reports/index.json",
            "registry_total": idx.get("registry_total"),
            "optional_deps": idx.get("optional_deps") or {},
            "dropped_scenarios": list(dropped),
            "note": (
                "complete=false 表示本次构建缺少可选依赖（通常为 rdkit），"
                "dropped_scenarios 里的场景**没有进入这个账本**——它们是代码里"
                "已注册、但本次没被构建的。不要把这种账本当作完整发布物："
                "用带 rdkit 的解释器重跑 scripts/build_reports.py 后重建账本。"
            ) if dropped else (
                "本次构建未丢弃任何场景（registry_total == total），"
                "账本覆盖注册表中的全部场景。"
            ),
        },
        "policy": dict(
            VG.policy(),
            note=(
                "Confidence below the MARGINAL bar blocks autonomous action on that "
                "scenario. Verifying a model's OWN predictions at arbitrary inputs "
                "requires the engine and is served by the MCP server "
                "(mcp/swarmlabs_vv_server.py) and, on the published held-out test sets, "
                "by POST /v3/verify. This ledger answers the static question "
                '"can this scenario be trusted right now?" for any HTTP agent.'
            ),
        ),
        "counts": counts,
        "total": idx.get("total", len(entries)),
        "n_blocked": len(blocked),
        "n_human_check_required": len(human_check),
        "blocked": sorted(blocked),
        "human_check_required": sorted(human_check),
        # ---- 校准元数据（借 Laya/Jev RLCD 严格评分规则，2026-09-20 新增）----
        # 用线性 softmax 策略 + log-likelihood 奖励训练一个 evidence → 4 档概率
        # 的映射，然后算 ECE（Expected Calibration Error）。参考值：
        #   Laya zero-shot=0.466 · Laya refit=0.081 · Jev=0.246
        # ECE 越低越说明"报告的概率"匹配"真实频率"。训练集含 PASS/MARGINAL/
        # REFUTED 多档，但仍不均衡，ECE 偏低是"分布内验证"，不代表生产泛化能力。
        "calibration": _compute_calibration_meta(entries),
        # ---- 溯源：这个 PASS 是对哪一版引擎/oracle 算的 ----
        # 没有这一段，账本只能回答"某场景是否可信"，无法回答"这个结论还有效吗"。
        # engine.all_digest 一改即变，使"沿用旧结论"成为可检测事实。
        "provenance": {
            "engine": VG.engine_fingerprint(),
            "oracle": VG.oracle_digest(),
            "report_index_sha256": VG._sha256_file(INDEX_PATH),
            "builder": "scripts/build_gate_ledger.py",
            "note": (
                "判定钉死在 engine.all_digest 上：引擎或场景 oracle 任一改动都会"
                "改变该摘要，ledger_is_stale() 随即报 stale。这不是说旧结论错，"
                "而是说它尚未针对当前引擎重算过。"
            ),
        },
        "scenarios": entries,
    }


def gate_for_key(ledger: dict, key: str):
    return ledger.get("scenarios", {}).get(key)


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the verification gate ledger")
    ap.add_argument("--print", dest="do_print", action="store_true",
                    help="print a short summary after building")
    args = ap.parse_args()

    if not os.path.exists(INDEX_PATH):
        print(f"[gate] index not found: {INDEX_PATH} — run scripts/build_reports.py first",
              file=sys.stderr)
        return 2

    ledger = build_ledger()

    os.makedirs(os.path.dirname(OUT_PUBLIC), exist_ok=True)
    os.makedirs(os.path.dirname(OUT_DATA), exist_ok=True)
    for out in (OUT_PUBLIC, OUT_DATA):
        # newline="\n"：账本是入库产物，必须与平台无关。默认文本模式在
        # Windows 会把 "\n" 译成 CRLF，导致每次本机重建都产生**全文件 diff**，
        # 也让下游对它的字节哈希变成平台相关（2026-09-21 修）。
        with open(out, "w", encoding="utf-8", newline="\n") as f:
            json.dump(ledger, f, ensure_ascii=False, indent=2)
            f.write("\n")

    if args.do_print:
        print("[gate] counts:", ledger["counts"])
        print("[gate] blocked:", ledger["n_blocked"], ledger["blocked"])
        print("[gate] human_check:", ledger["n_human_check_required"],
              ledger["human_check_required"])
        prov = ledger.get("provenance") or {}
        eng = prov.get("engine") or {}
        print("[gate] engine:", eng.get("git_commit", "")[:12] or "(no git)",
              "modules=", eng.get("n_modules"),
              "all_digest=", str(eng.get("all_digest"))[:16])
        print("[gate] oracle_digest:", str((prov.get("oracle") or {}).get("digest"))[:16])
        print("[gate] wrote:", OUT_PUBLIC)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
