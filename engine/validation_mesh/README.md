# Validation Mesh

SwarmLabs 的「虚拟实验 ↔ 真实实验 对等度验证」独立 Python 包。
把项目内核里的等价验证能力拆成可 `pip install` 的 wheel，产品化方向是
「多 Agent 交付验证基础设施」。

## 能力

| 能力 | 入口 | 说明 |
|------|------|------|
| 对等闭环 | `run_parity_loop` | 用尽量少的真实观测把虚拟代理校准到接近真实实验 |
| 校准度量 | `Calibrator` | rmse / 95% CI 覆盖率 / 校准误差（UQ 是否诚实） |
| 三态门禁 | `VirtualLabGuard` | 每个查询点 PASS / CONTROLLED / REJECT，显式标注不可信区 |

## 安装

```bash
cd engine/validation_mesh
pip install .
```

## 用法

```python
import numpy as np
from validation_mesh import VirtualLabGuard, run_parity_loop, Calibrator, ExperimentSpace

# 1) 三态门禁：显式拒绝 OOD 点
X = np.array([[0.1], [0.5], [0.9]])
y = np.array([np.sin(3 * x[0]) for x in X])
guard = VirtualLabGuard.from_data(X, y, bounds=[(0.0, 1.0)])
print(guard.gate_summary([[0.5], [1.4]]))   # 1.4 远离训练区 -> REJECT

# 2) 对等闭环：虚拟代理逐步逼近真实
space = ExperimentSpace([(0.0, 1.0)])
history = run_parity_loop(space, oracle=lambda x: float(np.sin(3 * x[0])))
```

## 依赖

- numpy ≥ 1.21
- scipy ≥ 1.7

纯 CPU、无 GPU、无 LLM、无联网。可日级自治运行。
