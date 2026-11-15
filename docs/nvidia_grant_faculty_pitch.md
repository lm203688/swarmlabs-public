# 一页纸 Pitch：邀请高校教授合伙申报 NVIDIA Academic Grant（Simulation & Modeling）

> 用途：用户（教授级高工 / 乌克兰工程院外籍院士，SwarmLabs 创始人）持此页去找一位有仿真 / 科学 ML 组的高校教授，请对方担任**主 PI（lead applicant）**，SwarmLabs 作为**技术负责人 / 产业合作方（co-PI）**联合申报。
> 适配 NVIDIA 2026 CFP：**Simulation and Modeling**（含 physics-informed machine learning、scientific simulation），最高 30,000 H100 80GB 小时，下一轮 **2026-09-30 截止**。

---

## 1. 我们是谁（30 秒版）
**SwarmLabs** —— 做「虚拟实验对等现实」的科研基础设施：用高斯过程 / 物理信息神经网络（PINN）做科学仿真代理模型，把**不确定性量化（UQ）作为一等公民**，再用主动学习闭环挑出「信息量最大的真实实验点」自我校准。

- 已落地纯 numpy 的 GP 代理 + 8 个验证场景（surrogate / PDE / 生物 / 微生物 / LLM 缩放），端到端零依赖、可离线复现。
- 已发布开源代码包（MIT）与公开 Demo 页，框架层就绪，**瓶颈在算力**：PINN / 神经算子 / 远程实验台（RemoteLab）当前无 GPU 无法启用。

## 2. 为什么是现在、为什么是 NVIDIA
- NVIDIA 2026 CFP「Simulation and Modeling」明确把 **physics-informed ML / scientific simulation** 列为核心方向 —— 与我们的技术栈**逐字对应**。
- 申请硬性条件「必须使用 NVIDIA 软件栈」：我们计划将代理模型底座迁移到 **PhysicsNeMo / Modulus + CUDA + RAPIDS**，正好借资助完成技术栈对齐。
- 算力需求：30,000 H100 小时用于训练 PDE 神经算子 + 大规模主动学习采样闭环。

## 3. 分工（请教授确认角色）
| 角色 | 承担方 | 职责 |
|---|---|---|
| **主 PI（lead applicant）** | 高校教授（您） | 满足「授予 PhD 高校全职 faculty」门槛；挂名与合规 |
| **co-PI / 技术负责人** | SwarmLabs（我方） | 全部代码、训练、部署、UQ 方法论、结题报告技术部分 |
| **产业 partner** | SwarmLabs | 提供可运行系统与真实验证数据，承诺成果落地 |

## 4. 您的收益（为什么值得挂名）
- 免费 30,000 H100 小时算力，直接加速您组的科学 ML / 仿真研究。
- NVIDIA Solutions Architect 技术辅导 + GTC 展示机会（对您组的曝光有用）。
- 与 SwarmLabs 的 UQ + 主动学习框架联合产出论文（NVIDIA 要求 Acknowledgment 署名，可共一/共通）。

## 5. 我们需要您做的（最小动作）
1. 确认愿意作为主 PI 申报（您单位的科研处备案由您方走）。
2. 提供单位名称、您的研究方向关键词（用于申请书「NVIDIA 技术如何加速」一节对齐）。
3. 9/30 前在 NVIDIA Academic Grants Portal 用您账号提交（模板我方按 CFP 要求填好英文稿，您过目后提交）。

## 6. 联系
- SwarmLabs 创始人：[姓名] —— 教授级高工 / 乌克兰工程院外籍院士
- 邮箱 / 微信：[待填]
- 代码与 Demo：swarmlabs.tools

---
*注：本页为模板，[待填] 处需用户补全；教授姓名/单位/方向由实际洽谈对象填入。NVIDIA 要求「必须使用 NVIDIA 软件栈」与「季度窗口 9/30」为硬性约束，申报前务必复核当季 portal 开放状态。*
