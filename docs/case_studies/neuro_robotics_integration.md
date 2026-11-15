# 神经机器人学（NeuroRobotics）× SwarmLabs 接入评估与建议

> 基于 `reports/neuro_robotics_gap_scan_2026-10-18.html`（数据源：`data/neuro_robotics_gap_scan_2026-10-18.json`）
> 的 20 篇文献 × G1–G7 七维 gap 打分，给出 SwarmLabs 与机器人项目（占位标识 `roboparts`）的接入路径、
> API 端点白名单、配额与诚实边界。
>
> 上一次版本引用了不存在的 `frontend_fixed/reports/neuro_robotics_gap_scan_2026-09-16.html`——本版本修复该指针。

## 1. Gap 发现结论（一句话）

2026-10-18 全库扫描 20 篇文献得出 gap rate 排序：
**G7=1.000 / G1=0.750 / G5=0.750 / G2=0.700 / G6=0.600 / G3=0.500 / G4=0.250**。

- **G7 多 Agent 协作**是唯一 100% 全 gap 维度（20 篇 0 篇覆盖），是**当前协作空间最大**的方向；
- **G2 物理基线**（神经解码侧）、**G5 系统级多保真度**、**G1 数据可用性**均 >0.70 gap；
- **G4 真实锚点** 0.25 最饱和，说明「真实硬件验证」这一维度已不缺；
- **G3 UQ** 0.50 中等饱和，安全控制侧已有 GP+Conformal，但**神经侧普遍缺 UQ**——SwarmLabs 强项。

对 SwarmLabs 而言，G7（API 网关编排）、G3/G5（GP+UQ+多保真）、G2（物理残差代理）都有**现成能力**；
G6 在线实时是**唯一硬空白**，短期需新增 `online_gp`（roadmap，非当前）。

## 2. 各维度 SwarmLabs 能接 / 不能接

| 维度 | 领域现状（gap rate） | SwarmLabs 能否接 | 接入方式 |
|---|---|---|---|
| G1 数据可用性 | 0.75（真实神经/机器人数据锁在孤岛） | **部分** | 机器人项目通过 API 回传本地观测 → 扩充锚点库 |
| G2 物理基线 | 0.70（腿足有 GP；神经解码无闭式模型） | **能（腿足/动力学）** | 复用 `SurrogateEngine` 做物理残差代理 |
| G3 UQ | 0.50（安全控制已有 GP+Conformal；神经侧缺 UQ） | **能（强项）** | `verification_gate.py` 提供覆盖率≥0.90 闸门 |
| G4 真实锚点 | 0.25（多在真实硬件验证；饱和） | **能（中）** | `wet_lab_anchors.py` 反推 + 文献区间对照 |
| G5 多保真度 | 0.75（域随机化常见；系统耦合少） | **能（中）** | `multifidelity.py` 两级信号融合 |
| G6 在线/实时 | 0.60（端侧实时自适应需求强；重模型难实时） | **暂不能** | 需新增 `online_gp`（roadmap，非当前） |
| G7 多 Agent | 1.00（几乎无跨项目编排） | **能（API 网关层）** | 本项目签发 project API key 即落地 |

## 3. P0 杠杆点（对齐 2026-10-18 gap 排名）

按「gap rate × SwarmLabs 现成能力 × 落地距离」排序：

| 优先 | 目标维度 | SwarmLabs 落地动作 | 复用点 |
|---|---|---|---|
| **P0-1** | G7 多 Agent | 签发 `sk-swlm-rbprts-*` project API key + `/v3/partner/usage` 用量自查 | 已有 API 网关、配额、审计（本文件 §4-5） |
| **P0-2** | G3 神经侧 UQ | 机器人项目把神经预测 POST 到 `/v3/verify` 拿覆盖率/κ/硬门禁 | `verification_gate.py`、两阶段门控（A13） |
| **P0-3** | G2 物理基线 | 机器人项目上传动力学残差样本 → 走 `/v3/gate` 检查代理 RMSE | `SurrogateEngine`、`trust_boundary` |
| **P1-1** | G5 系统级多保真 | 复用 `multifidelity.py::axis_grid`（对数网格）做粗/细两级融合 | 需扩展 `axis_grid` 到神经-机械耦合场景 |
| **P1-2** | G1 数据可用性 | 回传观测扩充 `wet_lab_anchors`（当前 19 条 → 目标 30+） | `data/wet_lab_anchors.json` |
| **P2** | G6 在线/实时 | **不接**（当前无能力，硬约束 A15 只支持域内插值） | roadmap |

## 4. 推荐接入路径（机器人项目视角）

```
机器人项目 (roboparts)
   │  HTTP GET 携带 X-API-Key: sk-swlm-rbprts-xxxx
   ▼
SwarmLabs /v3/gate            → 全量验证闸门账本（95 场景 + 唯一 REFUTED 硬阻断清单）
SwarmLabs /v3/anchors         → 湿实验锚定（19 条 oracle + 文献区间对照）
SwarmLabs /v3/benchmark       → 公开留出基准集（STORE_POINTS=229）
   │  机器人项目拿这些做：「我的动力学模型在 SwarmLabs 闸门上能拿多少分」
   ▼
SwarmLabs /v3/verify  (POST)  → 提交机器人项目自己的预测，精确复算 R²/覆盖率/κ + 硬门禁
SwarmLabs /v3/partner/usage  → 自查配额与审计（本会话 2026-10-18 已修 §2.7 认证门）
```

**注意**：`/v3/verify` 当前**不要求 key**（攻击面小、只读计算），但建议机器人项目也带 key 以便审计；
`/v3/partner/usage` 需带有效 key 才返回 200（2026-10-18 已修复漏认的入口条件）。

## 5. API 端点白名单（已签发 key 的 scope）

| 端点 | 方法 | 开放 | 说明 |
|---|---|---|---|
| `/v3/gate` `/v3/gate/{key}` | GET | ✅ | 验证闸门账本 |
| `/v3/anchors` `/v3/anchors/{key}` | GET | ✅ | 湿实验锚定 |
| `/v3/benchmark` `/v3/benchmark/{key}` | GET | ✅ | 留出基准集 |
| `/v3/verify` | POST | ✅（建议带 key） | 任意预测验证 |
| `/v3/partner/usage` | GET | ✅（需 key） | 配额/审计自查 |
| 引擎训练 API | — | ❌ | bus factor=1，不承诺 SLA |

## 6. 配额与审计

- 默认：`100 calls/min`、`10,000 calls/day`（够跑通，不够大规模滥用）。
- 携带有效 key 时计入配额 + 写入运行时审计账本（`/v3/partner/usage` 可自查）。
- 端点**默认仍公开**（GEO 漏斗 + 免费公开验证），无 key 也能读；带 key 仅额外获得配额计量与审计。
- 无效 key → `401`；缺失 key → 匿名放行（不破坏现有公开访问与部署复验）。

## 7. 诚实边界（必读）

- SwarmLabs 当前 **95 场景 / 91-3-1-0 发布口径**（唯一 REFUTED=`mol_suzuki_yield`），
  **bus factor=1**、**pre-revenue**、**GH Actions 分钟数耗尽**（2026-10-09 起，未解）——不宜对外承诺 SLA。
- 本 key 是**互操作演示凭证**，不是商业授权；正式商业授权走 `license.swarmlabs.tools`（当前 502，#411 未解）。
- 神经解码 / 软体接触动力学在 SwarmLabs 内**尚无对应场景**，短期靠「机器人项目回传数据 → 扩充锚点」
  方式渐进接入，不承诺开箱即用。
- **硬约束 A15**：SwarmLabs 只支持**设计域内插值验证**，不支持外推；
  机器人项目把超域外样本 POST 到 `/v3/verify` 会返回 A14 适用域边界告警（`roughness > 0.3`）。

## 8. 产品价值

「两个独立项目通过标准 HTTP API 对接、互相验证预测」是**多 Agent 协作 + 知识累积**能力的直接实证，
本接入即为可引用案例（见 `docs/case_studies/api_first_collab.md`）。

**外部价值锚点**（2026-10-18 gap 排名反向证明）：
- 领域内 G7 100% gap ⇒ SwarmLabs 的 API 网关 + project key 是**当前领域唯一现成方案**；
- 领域内 G3/G5 中位 gap ⇒ SwarmLabs 的 GP+UQ+多保真三件套**覆盖领域最缺的两维**；
- 领域内 G2 70% gap ⇒ SwarmLabs 的 `SurrogateEngine` 有**直接落点**（无需新造轮子）。

## 9. 版本记录

| 日期 | 变更 |
|---|---|
| 2026-10-18 | v2：接入 2026-10-18 全库 20 篇文献 gap 扫描；修复失效的 2026-09-16 报告引用；场景数 62→95；新增 §3 P0 杠杆表；新增 §7 A15 硬约束；新增 §8 外部价值锚点 |
| 2026-09-xx | v1：初版（引用的 gap 报告从未实际生成） |
