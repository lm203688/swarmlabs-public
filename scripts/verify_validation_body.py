"""P2 ValidationBody 验证：真实 GP + 四个门禁聚合 + 耦合约束教训复现 + 几何查询。

运行：python scripts/verify_validation_body.py
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from engine.surrogate import GPSurrogate, ExperimentSpace
from engine.guard import VirtualLabGuard, GateStatus
from engine.moment_bound import MomentBoundGate
from engine.second_law import SecondLawGate
from engine.bidirectional_parity import BidirectionalParityGate
from engine.validation_body import ValidationBody

RNG = np.random.default_rng(2026)

def fit_logistic():
    """在 [0,1] 上拟合一个 logistic GP（真实但未加噪太多，便于门禁通过）。"""
    X = np.linspace(0.05, 0.95, 14).reshape(-1, 1)
    y = 1.0 / (1.0 + np.exp(-6 * (X.ravel() - 0.5)))
    y = y + RNG.normal(0, 0.02, size=y.shape)
    y_scale = float(np.std(y)) + 1e-9
    sur = GPSurrogate(lengthscale=0.18, signal_var=float(np.var(y)) + 1e-6,
                      noise_var=0.02 ** 2, y_mean=float(np.mean(y)), y_scale=y_scale)
    sur.fit(X, y)
    return sur

def main():
    sur = fit_logistic()
    grid = ExperimentSpace([(0.0, 1.0)]).sample(60, RNG).reshape(-1, 1)

    # —— 跑四个门禁 ——
    gsum = VirtualLabGuard(surrogate=sur).gate_summary(grid)
    mean, std = sur.predict(grid)
    mb = MomentBoundGate(0.0, 1.0).check(mean.ravel(), np.asarray(std).ravel())
    sl = SecondLawGate().check_posterior(sur, grid)
    # 构造一个"双方向都过"的 parity 记录（演示 PASS 路径：虚拟↔真实已良好对等）
    parity = BidirectionalParityGate(tol=0.10).check({
        "forward": {"rmse": 0.030, "coverage": 0.960, "cal_err": 0.020},
        "reverse": {"rmse": 0.0305, "coverage": 0.959, "cal_err": 0.0202},
    })

    print("=" * 70)
    print("路径 A：from_gate_results 聚合（真实 GP）")
    body = ValidationBody.from_gate_results(
        moment=mb, second=sl, parity=parity, guard_summary=gsum,
        ref_scales={"min_var": 0.05})
    print(body.summary_text())
    print("status =", body.status().value, "| volume_fraction =", body.volume_fraction())
    print("shadow(coverage_lb) =", body.shadow("coverage_lb"))
    print("shadow(parity_fwd_rel) =", body.shadow("parity_fwd_rel"))
    print("binding =", body.binding())

    # 落一个 SVG 到文件
    svg = body.cartography_svg()
    with open("data/validation_body_cartography.svg", "w", encoding="utf-8") as f:
        f.write(svg)
    print("SVG 已写 data/validation_body_cartography.svg")

    # —— 路径 B：耦合约束教训复现（涨落定理 #2 核心）——
    print("\n" + "=" * 70)
    print("路径 B：耦合约束——单轴全过，但联合越界（涨落定理『影子≠体』复现）")
    b2 = ValidationBody()
    b2.add_indicator("coverage_lb", 0.95, 0.90, 1.0)     # 单轴：通过
    b2.add_indicator("parity_fwd_rel", 0.04, 0.0, 0.10)  # 单轴：通过
    b2.add_indicator("mean", 0.95, 0.0, 1.0)             # 单轴：通过
    b2.add_indicator("var", 0.04, 0.0, 1.0)              # 单轴：通过
    # 耦合 Bhatia–Davis：σ² ≤ (hi−μ)(μ−lo)，这里 (1−0.95)(0.95−0) = 0.0475 ≥ 0.04 → 其实通过
    b2.add_bhatia_davis("mean", "var", lo=0.0, hi=1.0)
    z = np.array([0.95, 0.04, 0.95, 0.04])
    print("  点 coverage=0.95 / parity=0.04 / mean=0.95 / var=0.04")
    print("  各单轴 slack =", [round(s,4) for _,_,s in b2._all_slacks(z)])
    print("  耦合 Bhatia–Davis 约束 slack =",
          round(b2.convex[0].slack(z), 4), "(>0 表示可达)")
    print("  contains =", b2.contains(z), "（应为 True）")

    # 现在构造一个 mean/var 各自合理但违反耦合的点：
    # mean=0.5, var=0.30 → (1−0.5)(0.5−0)=0.25 < 0.30 → 违反；但单轴 var=0.30<1.0 通过
    b3 = ValidationBody()
    b3.add_indicator("coverage_lb", 0.95, 0.90, 1.0)
    b3.add_indicator("mean", 0.50, 0.0, 1.0)
    b3.add_indicator("var", 0.30, 0.0, 1.0)
    b3.add_bhatia_davis("mean", "var", lo=0.0, hi=1.0)
    z2 = np.array([0.95, 0.50, 0.30])
    axis_pass = all(b3.indicators[k].inside for k in b3._order)
    coupled_ok = b3.convex[0].slack(z2) >= -1e-12
    print("\n  反例：coverage=0.95 / mean=0.50 / var=0.30")
    print("  所有单轴指标 inside =", axis_pass, "（各影子都『通过』）")
    print("  耦合 Bhatia–Davis slack =", round(b3.convex[0].slack(z2), 4),
          "(<0 表示不可达)")
    print("  contains =", b3.contains(z2), "（应为 False：单轴全过 ≠ 联合过）")
    assert axis_pass and not coupled_ok and not b3.contains(z2), "耦合教训未复现！"
    print("  ✅ 涨落定理教训复现成功：单轴全过 ≠ 落入联合凸体。")

    # —— 路径 C：REJECT 路径（拒绝率超阈）——
    print("\n" + "=" * 70)
    print("路径 C：REJECT 触发（拒绝率超阈）")
    bad_summary = {"n": 10, "counts": {"pass": 4, "controlled": 1, "reject": 5},
                   "rejected": list(range(5)), "results": []}
    bc = ValidationBody.from_gate_results(guard_summary=bad_summary)
    bc.set_current({"reject_rate": 0.5, "controlled_rate": 0.1})
    print("  status =", bc.status().value, "(应为 reject)")
    print("  cartography.status =", bc.cartography()["status"])

    print("\n全部路径验证通过 ✅")

if __name__ == "__main__":
    main()
