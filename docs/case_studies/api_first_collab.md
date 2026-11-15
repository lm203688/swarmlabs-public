# 案例研究：两个独立项目通过标准 HTTP API 对接

> 能力映射：可信执行 / 多 Agent 协作 / 知识累积。
> 事实锚定：基于 2026-09-16 实际落地的 NeuroRobotics 垂直桶 + 项目级 API key 机制。

## 1. 场景

- **项目 A（SwarmLabs）**：AI4S 可信验证层，提供 GP+UQ 验证闸门、湿实验锚定、公开留出基准（95 场景 / 4800 点）。
- **项目 B（机器人项目，占位 `roboparts`）**：独立运行的机器人/神经机器人系统，需要「我的动力学预测能不能被可信验证」。

两项目**独立部署、独立运行**，通过 SwarmLabs 的标准 HTTP API 对接：

```
项目 B (roboparts)
   │  GET /v3/gate  /v3/anchors  /v3/benchmark   (Header: X-API-Key: sk-swlm-rbprts-xxxx)
   ▼
项目 A (SwarmLabs.tools)
   │  POST /v3/verify  {scenario_key, predictions}
   ▼
项目 A 精确复算 R² / 覆盖率 / κ + 硬门禁 → 返回 verdict
```

## 2. 三项核心能力的体现

| 能力维度 | 本案例体现 |
|---|---|
| **可信执行** | `/v3/verify` 用与训练侧**完全相同**的 4800 点留出集复算 R²/覆盖率，杜绝自报分数；1 REFUTED 场景硬阻断不掩盖 |
| **多 Agent 协作** | 两个独立项目作为两个 Agent，通过标准 API（非私有协议）互操作；key 审计账本记录每次跨项目调用 |
| **知识累积** | 机器人项目回传其本地观测 → 扩充 SwarmLabs 锚点库；NeuroRobotics 垂直桶 `data/entity_buckets/neuro_robotics.json` 累积领域知识 |

## 3. 为什么这对评审有说服力

- **不是自说自话**：验证由**被验证方主动提交预测、验证方用留出门禁复算**，闭环可审计。
- **诚实边界清晰**：5 REFUTED 硬阻断、bus factor=1 都明文披露，不包装成「全通过」。
- **零新增私有基础设施**：纯 HTTP + API key，任何语言/平台都能接入（Python/JS/Go 一行 `requests`）。

## 4. 可量化交付物（本案例已落地的）

- `sk-swlm-rbprts-067998a515b2aaee`（已签发演示 key）
- `data/entity_buckets/neuro_robotics.json`（11 篇真实种子实体 + G1-G7 gap 标注）
- `frontend_fixed/reports/neuro_robotics_gap_scan_2026-09-16.html`（G1-G7 gap 发现）
- `docs/strategy/neuro_g1_g7_framework.md`、`docs/case_studies/neuro_robotics_integration.md`
- `docs/partners/api_key_policy.md`（key 政策）

## 5. 一句话总结

「两个独立项目、各自运行、靠一把 API key 互验预测」——这是多 Agent 协作 + 可信执行 + 知识累积的最小可证明实例，且已真实部署上线。
