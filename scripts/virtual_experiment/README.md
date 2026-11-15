# virtual_experiment —— 虚拟实验对等现实框架

## 核心哲学
**现实实验本质上也是"编码世界"**：物理规律 = 微分方程，随机动力学 = 噪声 + 采样。
虚拟实验只是主动削减干扰项、用数学/算演加速收敛。SwarmLabs 的目标不是"替代"真实实验，
而是让虚拟实验逐步达到 **与现实实验同等可信** 的程度——关键在两点：

1. **UQ 是一等公民**：真实实验有误差棒，虚拟实验也必须有（GP 的 std / 集成 / 保形预测）。
2. **主动学习闭环**：用虚拟实验挑出"信息量最大的真实实验点"来校准自己，
   用最少的真实实验把虚拟代理逼到对等（见 `run_parity_loop`，对标 arxiv:2605.06756）。

## 与竞品的差异点
| 竞品 | 做什么 | 我们的不同 |
|---|---|---|
| FutureHouse Robin / DeepMind Co-Scientist | LLM 多智能体提假设、设计实验、**人做实验** | 我们**仿真 + 量化不确定性 + 挑最该做的真实实验**，虚拟层可独立达标 |
| DeerFlow 2.0 | 调研→产出 SuperAgent 编排 | 我们是**科学实验闭环**（非文档/网页产出） |
| AngelSpec | 投机采样推理加速 | 我们关注**实验效率**（主动学习省真实实验）而非推理加速 |

## 模块
- `parity_framework.py`：ExperimentSpace / GPSurrogate / NeuralOperatorSurrogate(桩) /
  SyntheticOracle / RemoteLabOracle(桩) / Calibrator / ActiveLearner / run_parity_loop
- `fidelity_ladder.py`：保真度阶梯（GP→PIML→神经算子→校准数字孪生）+ 算力就绪度

## 算力策略（框架优先，上规模在后）
- 现在可跑：GP 代理、主动学习、校准（纯 numpy，无 GPU）。
- 桩已留接口、待融资/算力充足时启用：PINN/神经算子训练、RemoteLab/HPC 对接。
- 这本身就是融资叙事：轻量算力已证明方法成立，规模扩张只需 GPU。

## 自测
```bash
python parity_framework.py   # 跑通虚拟→真实对等闭环，看 rmse↓ coverage→0.95
python fidelity_ladder.py   # 打印保真度阶梯
```
