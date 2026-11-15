# SwarmLabs Agent Card & MCP Manifest 发布说明

**发布日**：2026-09-22
**依据**：MCP SEP-2640（2025-09-13 Final，Skills 官方收编）+ A2A 协议（Google A2A 1.0）
**目标**：让第三方 agent 通过标准发现路径自动发现 SwarmLabs API，对齐 AIsa 的「Agent-Readable Web」叙事。

## 文件清单

| 文件 | 用途 | 消费者 |
|---|---|---|
| `.well-known/agent-card.json` | A2A agent 身份与能力声明 | A2A client（Copilot Studio、Google Agentspace、其他 A2A 客户端） |
| `.well-known/mcp.json` | MCP SEP-2640 manifest | Claude Code、Cline、Cursor 等 MCP client |
| `docs/agent/README.md` | 本说明文件 | 人类开发者 |
| `docs/agent/agent-card.example.json` | agent-card.json 副本（供人类阅读） | 文档 |

## A2A 协议要点

SwarmLabs v3 microbiology API 声明的 3 个 skill：

1. **microbiology-63** — 63 个 GP+UQ 场景（11 跨域 + 52 微生物）
2. **structured-science-entities** — 157,866+ 结构化科研实体检索（持续扩充中）
3. **tech-radar-promote** — signal → candidate → promote → effect 五态机闭环

对齐三项核心能力方向：**可信执行 / 多 Agent 协作 / 知识累积**。

## MCP SEP-2640 manifest 要点

- **requiredScopes**：`swarmlabs:read` / `swarmlabs:compute` / `swarmlabs:entities` — 无高危 scope（wallet/desktop/admin）
- **auth**：`Authorization` header + Bearer token；付费走 license.swarmlabs.tools（Pro ¥39.9 / 终身 ¥199）
- **allowedTools**：3 个白名单工具（microbiology-simulate / entity-retrieve / paper-claim-validate）
- **compatibility**：声明与 Claude Code / Cline / Cursor / OpenCode / A2A 客户端兼容
- **离线可用**：`offlineAvailable: true`（4 个纯计算 skill 可打包分发，无需 API 后端）

## 与 AIsa 的对位

| 维度 | AIsa | SwarmLabs |
|---|---|---|
| 定位 | unified API gateway + Agent Skills + x402 | 科研计算 API + 47k+ 结构化实体 + 数据飞轮 |
| 分发入口 | OpenClaw / ClawHub | A2A `.well-known/agent-card.json` + MCP `.well-known/mcp.json` |
| 支付 | x402 稳定币 | 虎皮椒（Pro ¥39.9 / 终身 ¥199）+ x402 长期路线 |
| 差异化 | 通用工具目录 5000+ API | 科研 domain 深度（157,866 条实体） |

**长期选项**：license 售卖通道可考虑走 x402 稳定币按需调用（战略决策，本轮不改代码）。

## 部署

CF Pages 部署时 `.well-known/` 目录会被静态托管，`https://swarmlabs.tools/.well-known/agent-card.json` 与 `https://swarmlabs.tools/.well-known/mcp.json` 可被任意 A2A / MCP client 自动拉取。

## 信任声明

- `signature` 字段声明 Ed25519 签名算法与 JWKS 端点，但签名值需发布密钥后填充
- `expiration: 2026-12-31T23:59:59Z` 便于轮换
- 无 `wallet:read` / `payment:execute` / `desktop:control` 等高危 scope —— **AIShield mcp_manifest_scan 判定为安全**（对齐 AIsa AgentPay Guard quote-first 原则）

## AIShield 自审

用 AIShield 的 `scanner/mcp_manifest_scan.py`（2026-09-22 新增）扫本 manifest：

- 无 `manifest_overprivileged_scope` finding（scope 均在白名单）
- 无 `manifest_supply_install` finding（`npx --yes @swarmlabs/cli install` 是官方包管理器）
- 无 `manifest_hardcoded_credential` finding（token 走 Authorization header，不硬编码）
- `manifest_unsigned` finding（签名值待发布密钥后填充）— 中危，接受
- `manifest_no_expiry` finding 已被 `expiration` 字段覆盖 — 通过

## 未来扩展

- SwarmLabs 走 x402 稳定币按需调用（战略决策待定）
- 发布 A2A agent card 到 AIsa 生态目录（如果 aisa.one 有开放目录）
- 加 JWKS 端点供 Ed25519 密钥验证
