# NeuroRobotics G1–G7 缺口框架

> 目的：为「机器人项目 / 神经机器人学」垂直域定义一套**缺口维度**（gap dimensions），
> 用 SwarmLabs 引擎现有能力去映射「能接 / 不能接」，驱动对外 API 协作的接入路径。
> 这套框架不是通用技术雷达，而是**以 SwarmLabs 能力为锚**的领域缺口度量。

## 七档缺口维度

| 档 | 维度 | 「已覆盖」判定 | 「缺口」判定 | SwarmLabs 现有能力 |
|---|---|---|---|---|
| **G1** | 数据可用性 | 真实神经/机器人观测公开可得 | 锁在临床/厂商私有孤岛 | 部分（仿真合成 OK；真实湿数据需外采） |
| **G2** | 物理基线存在性 | 有可写公式的物理模型（动力学/生物力学） | 软体接触/神经解码无闭式模型 | **强**（GP 即做物理残差代理） |
| **G3** | UQ 需求强度 | 安全关键 → 必须带置信区间 | ML 中心工作普遍忽略 UQ | **强**（UQ 一等公民，噪声地板 3%） |
| **G4** | 湿实验/真实锚点 | 已在真实受试者/硬件上验证 | 多数仅 MuJoCo 仿真验证 | 中（`wet_lab_anchors.py` 机制已建，需按场景填） |
| **G5** | 多保真度信号 | 仿真+真实两级信号并存 | 两级很少系统耦合 | 中（`multifidelity.py` 已有） |
| **G6** | 在线学习/实时性 | 端侧实时自适应 | 多为离线训练 | **弱**（当前 GP 离线 batch，无在线学习） |
| **G7** | 多 Agent 协作 | 与外部规划器/LLM 协作 | 多为单策略 | 中（`agentteams/` 已有基础） |

## 评分约定

- 每篇/每个子领域按 G1–G7 打分：`covered` / `partial` / `gap`
- 汇总各档覆盖率 → `缺口率 = 1 - 覆盖率`
- 缺口率最高的档 = 领域最痛、SwarmLabs 验证闸门价值最大的地方

## 与 SwarmLabs 内核的映射

- **G2 + G3 + G4 + G5** → 直接由 `SurrogateEngine`（GP+UQ）、`verification_gate.py`、
  `wet_lab_anchors.py`、`multifidelity.py` 覆盖 → **对外 API 可交付**。
- **G6** → 当前缺在线学习，需新增 `online_gp` 模块；短期不进生产路径。
- **G7** → `agentteams/` 基础在，但跨项目编排（机器人项目 ↔ SwarmLabs）属新场景，需 API 网关支撑。

## 引用依据

- 框架驱动的 gap 发现结果见 `frontend_fixed/reports/neuro_robotics_gap_scan_2026-09-16.html`
- 垂直实体桶见 `data/entity_buckets/neuro_robotics.json`
- 对外接入建议见 `docs/case_studies/neuro_robotics_integration.md`
