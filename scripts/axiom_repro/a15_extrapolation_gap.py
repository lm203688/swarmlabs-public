"""A15 外推不可达：GP 在训练域外塌向训练集均值。

这是 SwarmLabs 的硬约束（A15）：当前架构只支持设计域内插值验证，
不支持外推。本脚本复现这一失效模式。

失效模式描述：
- GP 核函数在训练域外趋零，预测塌向训练集均值
- 不是 bug，是 GP 的定义性质（核函数核外趋零）
- 换核、换 length scale 都无法解决
- 唯一出路是扩训练集覆盖外推区域

复现方法：
1. 在 [0, 1] 上采样训练点
2. 拟合 GP
3. 在 [1, 2]（训练域外）预测
4. 观察预测值塌向训练集均值

期望输出：
- 训练域内 rmse_rel < 0.05（插值 OK）
- 训练域外 rmse_rel > 0.5（外推失败）

关联：AXIOM_AUDIT.md#A15, generalization_bench.py#run_g1
"""
from __future__ import annotations

import sys
import os

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from engine.surrogate import GPSurrogate


def generate_training_data(n=20, seed=0):
    """生成训练数据：指数函数在 [0, 1] 上（单调递增，外推失败更明显）。"""
    rng = np.random.default_rng(seed)
    x = np.sort(rng.uniform(0, 1, n))
    y = np.exp(2 * x)  # 单调递增，外推时预测会塌向均值
    return x, y


def generate_test_data(n=20, x_min=0, x_max=1):
    """生成测试数据。"""
    x = np.linspace(x_min, x_max, n)
    y = np.exp(2 * x)
    return x, y


def fit_gp(x_train, y_train):
    """用 GPSurrogate 拟合 GP 模型。"""
    gps = GPSurrogate()
    gps.fit(x_train.reshape(-1, 1), y_train)
    return gps


def main():
    print("=" * 70)
    print("A15: GP 外推不可达复现")
    print("=" * 70)

    # 1. 训练数据（[0, 1]）
    x_train, y_train = generate_training_data(n=20, seed=0)
    print(f"\n训练域: [{x_train.min():.2f}, {x_train.max():.2f}]")
    print(f"训练点数: {len(x_train)}")
    print(f"训练均值: {y_train.mean():.4f}")
    print(f"训练 std: {y_train.std():.4f}")

    # 2. 拟合 GP
    gps = fit_gp(x_train, y_train)
    print("\nGP 拟合完成")

    # 3a. 训练域内测试（[0, 1]）
    x_test_in, y_test_in = generate_test_data(n=20, x_min=0.0, x_max=1.0)
    y_pred_in, _ = gps.predict(x_test_in.reshape(-1, 1))
    rmse_in = np.sqrt(np.mean((y_test_in - y_pred_in) ** 2))
    y_range_in = y_test_in.max() - y_test_in.min()
    rmse_rel_in = rmse_in / max(y_range_in, 1e-12)

    print(f"\n训练域内测试:")
    print(f"  rmse_rel = {rmse_rel_in:.4f}")

    # 3b. 训练域外测试（[1, 2]）
    x_test, y_test = generate_test_data(n=20, x_min=1.0, x_max=2.0)
    y_pred, _ = gps.predict(x_test.reshape(-1, 1))

    # 4. 评估
    rmse = np.sqrt(np.mean((y_test - y_pred) ** 2))
    y_range = y_test.max() - y_test.min()
    rmse_rel = rmse / max(y_range, 1e-12)

    print(f"\n训练域外测试:")
    print(f"  测试域: [{x_test.min():.2f}, {x_test.max():.2f}]")
    print(f"  真实均值: {y_test.mean():.4f}")
    print(f"  预测均值: {y_pred.mean():.4f}")
    print(f"  预测 std: {np.std(y_pred):.4f}")
    print(f"  rmse_rel = {rmse_rel:.4f}")

    # 5. 判定
    print("\n" + "-" * 70)
    print("判定:")

    in_domain_ok = rmse_rel_in < 0.05
    out_of_domain_fail = rmse_rel > 0.5

    if in_domain_ok:
        print(f"✓ 训练域内插值: rmse_rel = {rmse_rel_in:.4f} < 0.05（OK）")
    else:
        print(f"✗ 训练域内插值: rmse_rel = {rmse_rel_in:.4f} >= 0.05（意外）")

    if out_of_domain_fail:
        print(f"✓ 训练域外外推: rmse_rel = {rmse_rel:.4f} > 0.5（外推失败）")
        print(f"  预测均值 {y_pred.mean():.4f} 塌向训练均值 {y_train.mean():.4f}")
        print(f"  真实均值 {y_test.mean():.4f} 与预测差异巨大")
    else:
        print(f"✗ 训练域外外推: rmse_rel = {rmse_rel:.4f} < 0.5（意外成功）")

    if in_domain_ok and out_of_domain_fail:
        print("\n✓ A15 失效模式复现成功")
        print("  GP 在训练域内插值 OK，但在训练域外外推失败")
        print("  这是 A15 硬约束的直接量化证据")
        print("  唯一出路: 扩训练集覆盖外推区域，或改用其他方法（非 GP）")
        return True
    else:
        print("\n✗ A15 失效模式未完全复现")
        return False


def test_a15():
    """pytest 集成测试。"""
    return main()


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
