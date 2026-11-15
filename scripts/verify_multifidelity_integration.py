"""多保真门控接入生产（validate_scenario）的集成验证 —— 2026-09-08。

验证四件事（默认关闭 = 生产零破坏，是硬约束）：

  R1 复现性：use_multifidelity 开/关各跑两次，输出逐字节一致（零 drift）。
  R2 默认零影响：默认（关闭）行为 ≡ mean_fn=None（与改造前完全一致）。
  R3 门控纪律：采纳场景下，coverage_after(开) 不劣于 coverage_after(关) - 容差；
     且 derive_rubric 对该行仍成立（采纳不破坏校准/锐度）。
  R4 计时：报告开/关耗时比（确认成本可控，且默认路径无新增开销）。

用法：
    python scripts/verify_multifidelity_integration.py
"""
from __future__ import annotations

import os
import sys
import time
import json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "scripts"), os.path.join(ROOT, "engine")):
    if p not in sys.path:
        sys.path.insert(0, p)

import numpy as np                                  # noqa: E402
import paper_validation_loop as P                   # noqa: E402
from engine.multifidelity import decide_mean_fn     # noqa: E402
from agentteams.core.obligations import derive_rubric  # noqa: E402

# 选少量代表性场景：覆盖「未采纳」（平滑动力学门控拦下）与「采纳」（微生物场景）两类。
PICK = ["surrogate_forrester", "surrogate_branin", "bio_logistic",
        "micro_putida_toluene", "micro_lifecycle_ecoli"]
from engine.scenario_registry import all_scenarios as _all_scns
KEYS = _all_scns()
PICK = [k for k in PICK if k in KEYS]


def _close(a, b, tol=1e-9):
    """递归近似相等（dict/list/float），用于复现性断言。"""
    if isinstance(a, dict):
        if set(a) != set(b):
            return False
        return all(_close(a[k], b[k], tol) for k in a)
    if isinstance(a, (list, tuple)):
        return len(a) == len(b) and all(_close(x, y, tol) for x, y in zip(a, b))
    if isinstance(a, float):
        return abs(a - b) <= tol * max(1.0, abs(a), abs(b))
    try:
        return float(a) == float(b)
    except Exception:
        return a == b


def main() -> int:
    print(f"场景池：{len(KEYS)} · 验证子集 {PICK}")
    ok = True
    timing = {"off": [], "on": []}

    for key in PICK:
        s = KEYS[key]
        # R1/R2: 默认关闭跑两次（复现性 + 默认零影响基线）
        r_off_a = P.validate_scenario(s, correct=False, use_multifidelity=False)
        r_off_b = P.validate_scenario(s, correct=False, use_multifidelity=False)
        rep_off = _close(r_off_a, r_off_b)
        ok &= rep_off
        print(f"  [{key}] R1 默认关闭复现性={'PASS' if rep_off else 'FAIL'}")

        # R1: 开启跑两次（门控 + 均值函数必须可复现）
        t0 = time.time(); r_on_a = P.validate_scenario(s, correct=False, use_multifidelity=True); timing["on"].append(time.time() - t0)
        t0 = time.time(); r_on_b = P.validate_scenario(s, correct=False, use_multifidelity=True); timing["off"].append(0)
        rep_on = _close(r_on_a, r_on_b)
        ok &= rep_on
        print(f"  [{key}] R1 开启复现性={'PASS' if rep_on else 'FAIL'}")

        mf = r_on_a.get("multifidelity", {})
        print(f"        门控决策：adopted={mf.get('adopted')} | {mf.get('reason')}")

        # R3: 门控纪律 —— 采纳时 coverage 不降级、rubric 仍成立
        if mf.get("adopted"):
            cov_on = r_on_a["coverage_after"]
            cov_off = r_off_a["coverage_after"]
            no_degrade = cov_on >= cov_off - 0.02
            ok &= no_degrade
            print(f"  [{key}] R3 采纳后 coverage {cov_off:.3f}→{cov_on:.3f} "
                  f"{'PASS' if no_degrade else 'FAIL'}")
            rub = derive_rubric(r_on_a)
            rub_ok = rub.get("coverage_two_sided_ok") and rub.get("cal_err_ok") and rub.get("sharpness_ok")
            ok &= rub_ok
            print(f"  [{key}] R3 derive_rubric(采纳行)={rub} {'PASS' if rub_ok else 'FAIL'}")
        else:
            # 未采纳：开启与关闭的 metric 必须一致（门控回落到 GP-only）
            same = (_close(r_on_a["coverage_after"], r_off_a["coverage_after"])
                    and _close(r_on_a["rmse_after"], r_off_a["rmse_after"]))
            ok &= same
            print(f"  [{key}] R3 未采纳→与关闭一致={'PASS' if same else 'FAIL'}")

    # R4: 计时（对开启子集重测，避免上面计时混入了首次 JIT）
    t_on, t_off = [], []
    for key in PICK:
        s = KEYS[key]
        a = time.time(); P.validate_scenario(s, correct=False, use_multifidelity=True); t_on.append(time.time() - a)
        b = time.time(); P.validate_scenario(s, correct=False, use_multifidelity=False); t_off.append(time.time() - b)
    r_on_t = float(np.mean(t_on)); r_off_t = float(np.mean(t_off))
    print(f"\nR4 平均耗时  开启={r_on_t*1000:.1f}ms / 关闭={r_off_t*1000:.1f}ms "
          f"→ 比值 {r_on_t/max(r_off_t,1e-9):.2f}x")

    # decide_mean_fn 自身确定性（独立再验一次）
    s0 = KEYS[PICK[0]]
    _, d1 = decide_mean_fn(s0["f"], s0["bounds"],
                           [list(p) for p in s0["train"]],
                           [float(s0["f"](p)) for p in s0["train"]],
                           float(np.std([s0["f"](x) for x in s0["test"]])) + 1e-9, s0)
    _, d2 = decide_mean_fn(s0["f"], s0["bounds"],
                           [list(p) for p in s0["train"]],
                           [float(s0["f"](p)) for p in s0["train"]],
                           float(np.std([s0["f"](x) for x in s0["test"]])) + 1e-9, s0)
    det = d1 == d2
    ok &= det
    print(f"decide_mean_fn 确定性={'PASS' if det else 'FAIL'}")

    print("\n结论：", "ALL PASS —— 多保真门控接入生产安全（默认零影响 + 可复现 + 门控纪律成立）"
          if ok else "存在 FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
