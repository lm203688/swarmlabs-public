"""A14 适用域边界：roughness 阈值检测。

SwarmLabs 的 A14 适用域边界：roughness = mean|Δ²f| / std(f) > 0.3
即超平滑代理域（域内 0.0002~0.006 vs 边界 0.45~2.05）。

本脚本复现 A14 检测逻辑，展示如何判断一个场景是否超出平滑代理域。

失效模式描述：
- 当函数粗糙度超过阈值时，GP 代理模型不适用
- 加密训练点无用（30→480 仅从 1.46 降到 1.00）
- 必须用其他方法（如分片、物理信息约束）

复现方法：
1. 生成平滑函数（正弦）和粗糙函数（锯齿）
2. 计算 roughness
3. 对比阈值 0.3

期望输出：
- 正弦函数 roughness < 0.3（适用）
- 锯齿函数 roughness > 0.3（不适用）
"""
from __future__ import annotations

import sys
import os

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)


def compute_roughness(x, y, eps=1e-6):
    """计算函数粗糙度: mean|Δ²f| / std(f)"""
    y = np.asarray(y, float)
    x = np.asarray(x, float)

    # 二阶差分
    dy = np.diff(y)
    d2y = np.diff(dy)
    mean_abs_d2f = np.mean(np.abs(d2y))
    std_f = np.std(y) + 1e-12

    return mean_abs_d2f / std_f


def generate_smooth_function(n=100):
    """生成平滑函数（正弦）。"""
    x = np.linspace(0, 2 * np.pi, n)
    y = np.sin(x)
    return x, y


def generate_rough_function(n=100):
    """生成粗糙函数（锯齿波）。"""
    x = np.linspace(0, 2 * np.pi, n)
    # 锯齿波：周期性的线性上升 + 跳跃
    period = 0.5
    y = np.mod(x, period) / period
    y = y * 2 - 1  # [-1, 1]
    return x, y


def generate_noisy_function(n=100, noise_level=0.1):
    """生成带噪声的函数。"""
    x, y = generate_smooth_function(n)
    rng = np.random.default_rng(42)
    y_noisy = y + rng.normal(0, noise_level * np.std(y), n)
    return x, y_noisy


def main():
    print("=" * 70)
    print("A14: 适用域边界检测复现")
    print("=" * 70)

    roughness_threshold = 0.3

    functions = [
        ("正弦（平滑）", generate_smooth_function),
        ("锯齿（粗糙）", generate_rough_function),
        ("带噪正弦（中噪声）", generate_noisy_function),
    ]

    results = []
    for name, gen_func in functions:
        x, y = gen_func()
        roughness = compute_roughness(x, y)
        applicable = roughness <= roughness_threshold

        results.append((name, roughness, applicable))
        status = "✓ 适用" if applicable else "✗ 不适用"
        print(f"\n{name}:")
        print(f"  roughness = {roughness:.4f}")
        print(f"  阈值 = {roughness_threshold}")
        print(f"  判定: {status}")

    # 汇总
    print("\n" + "-" * 70)
    print("汇总:")
    print("-" * 70)
    print(f"{'函数':<20} {'roughness':<12} {'判定':<10}")
    print("-" * 70)
    for name, roughness, applicable in results:
        status = "适用" if applicable else "不适用"
        print(f"{name:<20} {roughness:<12.4f} {status:<10}")

    # 验证
    print("\n" + "-" * 70)
    print("验证:")

    # 正弦应该适用
    smooth_roughness = results[0][1]
    if smooth_roughness < roughness_threshold:
        print(f"✓ 正弦函数 roughness = {smooth_roughness:.4f} < {roughness_threshold}（适用）")
    else:
        print(f"✗ 正弦函数 roughness = {smooth_roughness:.4f} >= {roughness_threshold}（意外不适用）")

    # 锯齿应该不适用
    rough_roughness = results[1][1]
    if rough_roughness > roughness_threshold:
        print(f"✓ 锯齿函数 roughness = {rough_roughness:.4f} > {roughness_threshold}（不适用）")
    else:
        print(f"✗ 锯齿函数 roughness = {rough_roughness:.4f} <= {roughness_threshold}（意外适用）")

    if smooth_roughness < roughness_threshold and rough_roughness > roughness_threshold:
        print("\n✓ A14 失效模式复现成功")
        print("  roughness 阈值 0.3 能有效区分平滑/粗糙函数")
        print("  粗糙函数超出 GP 平滑代理域，需用其他方法")
        return True
    else:
        print("\n✗ A14 失效模式未完全复现")
        return False


def test_a14():
    """pytest 集成测试。"""
    return main()


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
