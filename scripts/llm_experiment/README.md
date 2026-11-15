# SwarmLabs · LLM / 科学实验能力模块（`scripts/llm_experiment/`）

本模块把近期开源 LLM 实验技术（verl / OpenRLHF / AngelSpec）与深度学习基石
（通用近似定理、交叉注意力）沉淀为 SwarmLabs 可复用的**能力底座**。

> 诚实边界：这些是**构建期/分析期资产与可复用库**，运行在本地或未来后端服务中；
> SwarmLabs 线上运行时是 Cloudflare Pages（无 Python/PyTorch 常驻），故不在此常驻执行。

## 1. 数学基石：通用近似定理（UAT）
- **定理**（Cybenko 1989；Hornik 1991 推广）：连续非多项式激活（ReLU/Sigmoid/tanh/GELU）的
  单隐层前馈网络，可在任意紧集上以任意精度逼近任何连续函数。
- **对 SwarmLabs 的意义**：物理场解（PDE 解映射）是连续函数 → 存在某网络能表示它，
  这是 PIML/代理模型「能逼近物理场解」的依据。
- **必须诚实的边界**：UAT 只保证**存在性**，不保证①所需宽度（维度灾难）、②可训练性、
  ③泛化性。这正是 **PIML（物理先验）+ 主动学习（数据回流）** 飞轮存在的理由——
  用物理先验缩小假设空间，用实验数据保证泛化。
- 对应 GEO 权威页：`frontend_fixed/blog/geo-methods-mathematical-backbone.html`

## 2. 架构杠杆：交叉注意力双序列桥（`cross_attention_bridge.py`）
- **机制**：Q 来自目标序列（解码侧），K/V 来自源序列（编码侧），注意力矩阵为 `n_t×n_s` 矩形，
  天然适合长度/模态/角色不同的两路融合。
- **SwarmLabs 用法**：
  - 跨域代理模型：源=PDE 场表征，目标=参数/边界条件序列 → 跨域知识迁移；
  - 多保真度桥接：源=粗糙仿真，目标=高精度实验 → 廉价数据引导昂贵预测。
- 提供纯 NumPy 实现（随处可跑、CI 友好）+ 可选 PyTorch 实现（训练用）。
- 运行：`python cross_attention_bridge.py`

## 3. 实验账本 + rollout/train 解耦（`experiment_harness.py`）
借鉴 **verl**（rollout 与 train 解耦、实验追踪）与 **AngelSpec**（用真实指标而非代理指标评估）：
- `ExperimentTracker`：把每次实验的超参/指标/产物记入 `data/experiments/` 可复现账本，支持列表与对比。
- `rollout_then_train_demo()`：最小可运行示例，演示 候选生成(rollout)→代理拟合(train)→真实效果评估(online-eval) 的解耦闭环。
- 运行：`python experiment_harness.py`

## 4. 借鉴映射（来自开源调研）
| 来源 | 借鉴点 | 落地点 |
|------|--------|--------|
| verl / OpenRLHF | rollout-train 解耦、实验追踪 | `experiment_harness.py` |
| AngelSpec | 真实指标评估（非 loss） | harness 以真实 regret/指标为口径 |
| deerflow 2.0 | 沙箱执行、子Agent、中间件防御 | 记录为架构方向（沙箱/子Agent/LoopDetection 等） |
| 通用近似定理 | 代理模型表达能力依据 | 文档 + GEO 理论页 |
| 交叉注意力 | 多域/多保真度融合 | `cross_attention_bridge.py` |

## 5. 需用户决策（不可自治）
- 是否把 bridge / harness 接成**真实后端训练服务**（需 GPU/常驻算力，超 CF Pages 范围）；
- deerflow 式沙箱执行属架构级改造，是否立项由用户拍板。
