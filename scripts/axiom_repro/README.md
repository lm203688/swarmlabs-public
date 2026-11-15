# A1~A17 失效模式复现库

每个脚本对应 AXIOM_AUDIT.md 中的一个失效模式，可独立运行验证。

## 目录

| 脚本 | 失效模式 | 状态 |
|---|---|---|
| `a1_scenario_representativeness.py` | A1 场景代表性 | 📋 骨架 |
| `a2_training_points.py` | A2 训练点数 | 📋 骨架 |
| `a3_kernel_choice.py` | A3 核函数选择 | 📋 骨架 |
| `a4_hyperparameter_tuning.py` | A4 超参调优泄漏 | 📋 骨架 |
| `a5_noise_floor.py` | A5 噪声地板 | 📋 骨架 |
| `a6_cross_validation.py` | A6 交叉验证 | 📋 骨架 |
| `a7_extrapolation.py` | A7 外推 | 📋 骨架 |
| `a8_dim_scaling.py` | A8 维度缩放 | 📋 骨架 |
| `a9_noise_injection.py` | A9 噪声注入 | 📋 骨架 |
| `a10_metric_choice.py` | A10 指标选择 | 📋 骨架 |
| `a11_adversarial.py` | A11 对抗性 | 📋 骨架 |
| `a12_oof_leakage.py` | A12 OOF 泄漏 | 📋 骨架 |
| `a13_two_stage_gate.py` | A13 两阶段门控 | 📋 骨架 |
| `a14_applicability_domain.py` | A14 适用域边界 | 📋 骨架 |
| `a15_extrapolation_gap.py` | A15 外推不可达 | 📋 骨架 |
| `a16_semantic_alignment.py` | A16 语义对齐 | 📋 骨架 |
| `a17_log_scale.py` | A17 对数表示 | 📋 骨架 |

## 运行方式

```bash
# 单个复现
python scripts/axiom_repro/a15_extrapolation_gap.py

# 全部运行（跳过骨架）
python scripts/axiom_repro/run_all.py

# pytest 集成
pytest scripts/axiom_repro/ -v
```

## 设计原则

1. **每个脚本自包含**：不依赖其他脚本的中间产物
2. **最小可运行**：每个脚本 < 200 行，可在 30 秒内完成
3. **诚实输出**：明确标注「这是已知失效模式，不是 bug」
4. **可验证**：输出包含期望值，可自动化对比
