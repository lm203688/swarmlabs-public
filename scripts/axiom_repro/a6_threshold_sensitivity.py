"""A6 阈值选择假设复现。

AXIOM_AUDIT A6：以下阈值是合理的工程选择，不是可证明的数学最优。

复现方法：
1. 对 PASS R² 阈值做 ±10% 扰动
2. 观察 PASS/MARGINAL 边界移动
3. 验证阈值敏感性

期望输出：
- 阈值 ±10% 导致多少场景判定翻转
- 阈值敏感性分析结果
"""
from __future__ import annotations
import json
import os
import sys
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)


def load_gate_ledger():
    """加载 gate_ledger。"""
    path = os.path.join(ROOT, "data", "gate_ledger.json")
    if not os.path.exists(path):
        return []
    with open(path) as f:
        ledger = json.load(f)
    return ledger.get("results", [])


def analyze_threshold_sensitivity(results, original_threshold=0.90):
    """分析阈值敏感性。"""
    # 收集所有 R² 值
    r2_values = []
    for r in results:
        r2 = r.get("r2", r.get("r_squared"))
        if r2 is not None and isinstance(r2, (int, float)):
            r2_values.append(r2)
    
    if not r2_values:
        return None
    
    r2_values = np.array(r2_values)
    n_total = len(r2_values)
    
    # 原始阈值判定
    original_pass = (r2_values >= original_threshold).sum()
    original_fail = n_total - original_pass
    
    # 阈值扰动分析
    perturbations = [-0.10, -0.05, -0.02, 0.0, 0.02, 0.05, 0.10]
    analysis = []
    
    for pct in perturbations:
        new_threshold = original_threshold * (1 + pct)
        new_pass = (r2_values >= new_threshold).sum()
        new_fail = n_total - new_pass
        flips = abs(new_pass - original_pass)
        
        analysis.append({
            "perturbation": pct * 100,
            "new_threshold": new_threshold,
            "pass_count": new_pass,
            "fail_count": new_fail,
            "flips": flips,
            "flip_rate": flips / n_total * 100
        })
    
    return {
        "n_total": n_total,
        "original_threshold": original_threshold,
        "original_pass": int(original_pass),
        "original_fail": int(original_fail),
        "analysis": analysis,
        "r2_stats": {
            "mean": float(np.mean(r2_values)),
            "median": float(np.median(r2_values)),
            "std": float(np.std(r2_values)),
            "min": float(np.min(r2_values)),
            "max": float(np.max(r2_values)),
        }
    }


def main():
    print("=" * 60)
    print("A6 阈值选择假设复现")
    print("=" * 60)
    
    results = load_gate_ledger()
    print(f"\n加载场景结果: {len(results)}")
    
    if not results:
        print("❌ 无法加载 gate_ledger")
        return 1
    
    analysis = analyze_threshold_sensitivity(results)
    if not analysis:
        print("❌ 无 R² 数据")
        return 1
    
    # 显示 R² 统计
    stats = analysis["r2_stats"]
    print(f"\nR² 统计:")
    print(f"  场景数: {analysis['n_total']}")
    print(f"  均值: {stats['mean']:.4f}")
    print(f"  中位数: {stats['median']:.4f}")
    print(f"  标准差: {stats['std']:.4f}")
    print(f"  范围: [{stats['min']:.4f}, {stats['max']:.4f}]")
    
    # 显示原始判定
    print(f"\n原始阈值 ({analysis['original_threshold']:.2f}):")
    print(f"  PASS: {analysis['original_pass']}")
    print(f"  FAIL: {analysis['original_fail']}")
    
    # 显示敏感性分析
    print(f"\n阈值敏感性分析:")
    print(f"  {'扰动%':>8} {'新阈值':>8} {'PASS':>6} {'翻转':>6} {'翻转率':>8}")
    print(f"  {'-'*38}")
    
    max_flips = 0
    for a in analysis["analysis"]:
        print(f"  {a['perturbation']:>+7.1f}% {a['new_threshold']:>8.4f} {a['pass_count']:>6d} {a['flips']:>6d} {a['flip_rate']:>7.2f}%")
        max_flips = max(max_flips, a['flips'])
    
    # 敏感性评估
    print(f"\n敏感性评估:")
    max_flip_rate = max(a['flip_rate'] for a in analysis['analysis'])
    if max_flip_rate < 5:
        print(f"  ✅ 低敏感性: 最大翻转率 {max_flip_rate:.2f}% < 5%")
    elif max_flip_rate < 15:
        print(f"  ⚠️ 中等敏感性: 最大翻转率 {max_flip_rate:.2f}%")
    else:
        print(f"  ❌ 高敏感性: 最大翻转率 {max_flip_rate:.2f}% > 15%")
    
    # A6 结论
    print(f"\n{'=' * 60}")
    print("A6 结论:")
    print(f"  阈值是合理的工程选择，不是可证明的数学最优")
    print(f"\n  阈值列表:")
    print(f"  - PASS R²: 0.90")
    print(f"  - COVERAGE_PASS: 0.90")
    print(f"  - NOMINAL_COVERAGE: 0.95")
    print(f"  - ERROR 上限: 1%")
    print(f"  - MCAL 阈值: 0.05")
    print(f"\n  缓解措施:")
    print(f"  - 所有阈值公开在 verification_gate.thresholds()")
    print(f"  - build_confidence_profile 报告多级 α 的校准曲线")
    print(f"\n  未证明部分: 没有对阈值做敏感性分析或理论最优性证明")
    print(f"{'=' * 60}")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())