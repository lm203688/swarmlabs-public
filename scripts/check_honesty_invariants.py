#!/usr/bin/env python3
"""check_honesty_invariants.py — 诚实 V&V 口径的独立断言守卫（CI 硬门禁）。

为什么这个脚本存在
-------------------
scripts/check_public_numbers.py 已经能查「产物之间的口径一致性」，但它判据的
**参照物本身也是生成链的产物**：frontend_fixed/reports/index.json 与
frontend_fixed/reports/gate_ledger.json 都由 scripts/build_reports.py 产出，
同一个解释器。用带 rdkit 的解释器跑得到 82；用缺 rdkit 的解释器跑，两个产物
**同时**塌成 80，彼此仍然完全一致 —— 一致性检查通过，而唯一那条 REFUTED
（mol_suzuki_yield）已经从公开账本里消失了。

2026-09-23 线上实测就是这样被打脸的：口径截断不是小事故，它让公开记录从
「79 PASS / 2 MARGINAL / 1 REFUTED」变成「78 PASS / 2 MARGINAL / 0 REFUTED」，
即本站相对竞品的核心差异点（敢公开一条诚实失败）被环境依赖悄悄删掉了。

因此这里放的是**不依赖生成链的业务不变量**：场景总数、判定分布、REFUTED 名单。
它们是这个产品对外承诺的事实，属于人工审阅后显式变更的量，而不是每次重建
重新推导的派生值。任何一次重建让下面任一条不成立，就该停在线上之前。

用法
----
    python scripts/check_honesty_invariants.py
    python scripts/check_honesty_invariants.py --json

退出码
------
    0  全部不变量成立
    2  至少一条不变量被破坏（阻断）

仅标准库，可在 GitHub Actions 直接运行。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# 不变量声明区
# ---------------------------------------------------------------------------
# 改这里的数字之前，先想清楚一件事：你是在**发布缩减包**，还是在**修复回归**？
# 二者的区别在于「有没有人来审阅这次变更」。把这里当成唯一的变更入口，
# 就逼着那次缩减变成一条可读的 commit，而不是解释器里一次静默 return []。
# 2026-10-05：82 → 95（+13）。新增来自 engine/scenarios_extended.py
# 的 7 个「只有 1 个场景」的学科（heat / llm / chemical / finance /
# neuroscience / ecology / energy 各 +2，其中 finance 的 1 个是负对照
# 测试资产不进 benchmark）。目的：让 G3（留一学科外推）覆盖全部 14 学科
# ——G3 要求 min_scenarios>=2，单场景学科在统计上无意义。
# **不是发布缩减包**，是修复「学科覆盖是广度数字而非深度证据」这个缺口
# （见 docs/CORE_OBJECTIVE-20261004.md §1.2 缺口 2）。
EXPECTED_TOTAL_SCENARIOS = 95

# verdict -> 允许条数。PASS 不写在这里：它是总数减去其余三类的余数，
# 允许它浮动，避免每次新增场景都要手改这份断言。
EXPECTED_COUNTS = {
    # MARGINAL 2 → 3：新增场景里 1 个判 MARGINAL（rubric 未全通过）。
    # 这是诚实的判定——宁可记 MARGINAL 也不假装 PASS。
    "MARGINAL": 3,
    # REFUTED 保持 1：新增 13 个场景**零 REFUTED**。
    # 期间曾出现 2 个，都已修掉（都不是方法的局限，是我的设计缺陷）：
    #   - neuro_lif_fi：bounds 设计错误（R·I 上界 20 V vs 阈值 50 mV，
    #     99.9% 采样点落在饱和区）→ 按乘积可达范围重设 bounds
    #   - fin_put_call_parity：零函数负对照，正确判定就是「被门禁判负」，
    #     进 benchmark 会把「诚实判负」与「场景失败」混在一起 → 移出，
    #     改作 selftest 断言（残差 < 1e-8）
    "REFUTED": 1,
    "ERROR": 0,
}

# 必须**公开存在**的失败场景。
# 注意方向：这里断言的是「这条失败记录必须还在」，不是「必须没有失败」。
# 如果把这一行删掉，代码仍然能跑、账本仍然自洽，只是公开面变成一个从未失败过的
# 平台 —— 那正是我们要防的那个静默通道。
EXPECTED_REFUTED = frozenset({"mol_suzuki_yield"})


def _load(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def check() -> list[dict]:
    findings: list[dict] = []

    idx = _load(ROOT / "frontend_fixed/reports/index.json")
    ledger = _load(ROOT / "frontend_fixed/reports/gate_ledger.json")

    for name, obj in (("index.json", idx), ("gate_ledger.json", ledger)):
        if obj is None:
            findings.append({
                "check": f"artifact present: {name}",
                "ok": False,
                "detail": "产物缺失或不是合法 JSON —— 无法证明发布完整",
            })
    if idx is None or ledger is None:
        return findings

    # ---- 1. 构建完整性 -----------------------------------------------------
    build = ledger.get("build") or {}
    if build.get("complete") is not True:
        findings.append({
            "check": "build.complete == true",
            "ok": False,
            "detail": f"账本自报 complete={build.get('complete')!r}；"
                      "不完整的构建不能对外发布",
        })

    # ---- 2. 禁止静默丢弃 ---------------------------------------------------
    dropped = list(build.get("dropped_scenarios") or [])
    if dropped:
        findings.append({
            "check": "build.dropped_scenarios == []",
            "ok": False,
            "detail": f"本次构建丢弃了 {len(dropped)} 个场景：{dropped}",
        })

    # ---- 3. 场景总数 -------------------------------------------------------
    total = idx.get("total")
    if total != EXPECTED_TOTAL_SCENARIOS:
        findings.append({
            "check": f"total scenarios == {EXPECTED_TOTAL_SCENARIOS}",
            "ok": False,
            "detail": f"index.json#total={total!r}。若这是有意的缩减包发布，"
                      "请先改本脚本的 EXPECTED_TOTAL_SCENARIOS 并说明理由；"
                      "否则这是重建环境缺 rdkit 导致的截断（2026-09-23 实测事故）",
        })

    # ---- 4. 判定分布 -------------------------------------------------------
    counts = ledger.get("counts") or {}
    for verdict, expected in EXPECTED_COUNTS.items():
        if counts.get(verdict) != expected:
            findings.append({
                "check": f"counts.{verdict} == {expected}",
                "ok": False,
                "detail": f"gate_ledger#counts.{verdict}={counts.get(verdict)!r}",
            })

    # ---- 5. REFUTED 名单必须完整保留 ---------------------------------------
    scenarios = ledger.get("scenarios") or {}
    actual_refuted = {k for k, v in scenarios.items()
                      if isinstance(v, dict) and v.get("verdict") == "REFUTED"}
    if not actual_refuted:
        findings.append({
            "check": "at least one publicly disclosed REFUTED scenario",
            "ok": False,
            "detail": "公开账本里没有任何失败记录。一个「从未失败过」的验证平台，"
                      "不是能力更强的证据 —— 通常是被删掉了失败记录",
        })
    missing = EXPECTED_REFUTED - actual_refuted
    if missing:
        findings.append({
            "check": f"expected REFUTED scenarios present: {sorted(EXPECTED_REFUTED)}",
            "ok": False,
            "detail": f"以下必须公开保留的失败记录消失了：{sorted(missing)}",
        })
    extra = actual_refuted - EXPECTED_REFUTED
    if extra:
        findings.append({
            "check": "REFUTED set reviewed",
            "ok": False,
            "detail": f"新增了未在此脚本登记的 REFUTED 场景：{sorted(extra)}。"
                      "新增失败记录本身是对的（诚实），但它也需要一次显式审阅，"
                      "请把它加进 EXPECTED_REFUTED",
        })
    return findings


def main() -> int:
    findings = check()
    if "--json" in sys.argv:
        print(json.dumps({"findings": findings,
                          "failures": sum(1 for f in findings if not f["ok"])},
                         ensure_ascii=False, indent=2))
    else:
        print(f"[check_honesty_invariants] expected total={EXPECTED_TOTAL_SCENARIOS}, "
              f"counts={dict(EXPECTED_COUNTS)}, refuted={sorted(EXPECTED_REFUTED)}")
        if not findings:
            print("PASS  全部诚实口径不变量成立")
        else:
            n = 0
            for f in findings:
                if not f["ok"]:
                    n += 1
                    print(f"  FAIL [{n}] {f['check']}")
                    print(f"        {f['detail']}")
            print(f"FAIL  {n} 条不变量被破坏")
    return 2 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
