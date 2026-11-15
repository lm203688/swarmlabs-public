"""A10 单点评估假设复现。

AXIOM_AUDIT A10：每个场景是单点快照（一次训练→一次测试），不覆盖：
- 时间序列
- 多起点敏感性
- 分布漂移

复现方法：
1. 多次重复运行同一场景
2. 计算 R² 方差
3. 评估结果稳定性

期望输出：
- R² 方差
- 稳定性评估
"""
from __future__ import annotations
import json
import os
import sys
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)


def simulate_repeated_runs(n_runs=10, base_r2=0.92, noise=0.02, seed=42):
    """模拟多次重复运行的 R² 方差。"""
    rng = np.random.default_rng(seed)
    r2_values = []
    
    for i in range(n_runs):
        # 模拟运行间的随机性
        r2 = base_r2 + rng.normal(0, noise)
        r2 = max(0.0, min(1.0, r2))  # 裁剪到 [0, 1]
        r2_values.append(r2)
    
    return r2_values


def analyze_stability(r2_values):
    """分析结果稳定性。"""
    r2 = np.array(r2_values)
    
    stats = {
        "mean": float(np.mean(r2)),
        "std": float(np.std(r2)),
        "min": float(np.min(r2)),
        "max": float(np.max(r2)),
        "range": float(np.max(r2) - np.min(r2)),
        "cv": float(np.std(r2) / np.mean(r2)) if np.mean(r2) > 0 else 0,  # 变异系数
    }
    
    # 稳定性评估
    if stats['cv'] < 0.01:
        stability = "✅ 高稳定性 (CV < 1%)"
    elif stats['cv'] < 0.05:
        stability = "⚠️ 中等稳定性 (CV 1-5%)"
    else:
        stability = "❌ 低稳定性 (CV > 5%)"
    
    stats['stability'] = stability
    return stats


def check_temporal_stability():
    """检查时间序列稳定性（模拟）。"""
    print("\n时间序列稳定性（模拟）:")
    
    # 模拟跨批次变化
    batch_r2 = []
    for batch in range(5):
        # 每批次有轻微的分布漂移
        drift = 0.01 * batch
        batch_r2.append(0.92 - drift + np.random.normal(0, 0.02))
    
    print(f"  批次 R²: {[f'{r:.4f}' for r in batch_r2]}")
    print(f"  批次间方差: {np.var(batch_r2):.6f}")
    
    # 评估
    if np.std(batch_r2) < 0.02:
        print(f"  ✅ 批次间稳定性良好")
    else:
        print(f"  ⚠️ 批次间存在漂移")


def main():
    print("=" * 60)
    print("A10 单点评估假设复现")
    print("=" * 60)
    
    # 模拟多次运行
    print("\n模拟多次重复运行 (n=10):")
    r2_values = simulate_repeated_runs(n_runs=10, base_r2=0.92, noise=0.02)
    
    # 分析稳定性
    stats = analyze_stability(r2_values)
    print(f"\nR² 统计:")
    print(f"  均值: {stats['mean']:.4f}")
    print(f"  标准差: {stats['std']:.4f}")
    print(f"  范围: [{stats['min']:.4f}, {stats['max']:.4f}]")
    print(f"  变异系数: {stats['cv']:.4f} ({stats['cv']*100:.2f}%)")
    print(f"  稳定性: {stats['stability']}")
    
    # 时间序列稳定性
    check_temporal_stability()
    
    # A10 结论
    print(f"\n{'=' * 60}")
    print("A10 结论:")
    print(f"  每个场景是单点快照，不覆盖时间序列/多起点/分布漂移")
    print(f"\n  缓解措施:")
    print(f"  - LHS（Latin Hypercube Sampling）保证采样均匀性")
    print(f"  - harness_evolver.py 的 paired-eval 支持多轮对比")
    print(f"\n  未证明部分: 没有做多次重复运行的方差分析")
    print(f"{'=' * 60}")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())