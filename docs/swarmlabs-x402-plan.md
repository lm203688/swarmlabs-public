# SwarmLabs 走 x402 通道实施方案

**日期**：2026-09-22
**背景**：AIsa（aisa.one）已产品化 AgentPay Guard（quote-first / per-request-per-task-per-time 三层限额 / cost 无法封顶就不执行），x402 稳定币支付已成 agent 生态标配。SwarmLabs 的 license 售卖（虎皮椒 Pro ¥39.9 / 终身 ¥199）需要考虑是否走 x402 通道。

## 现状

| 维度 | 当前状态 |
|---|---|
| 支付通道 | 虎皮椒（微信/支付宝），license.swarmlabs.tools 已就绪 |
| 定价 | Pro ¥39.9 / 终身 ¥199 |
| 后端 API | swarmlabs.tools/api/v3，OpenAI 兼容推理 + 47,566+ 结构化实体 |
| A2A/MCP 元数据 | `.well-known/agent-card.json` + `.well-known/mcp.json` 已发布（2026-09-22） |
| x402 支持 | 无 |

## 三档方案

### 方案 A：只加 x402 支付后端，虎皮椒主通道保留

**改动**：
- 新增 `swarmlabs/tools/x402_facilitator.py`：接入 Circle x402 facilitator（或 x402-native 的 Solana/Base 端点）
- 在 `license.swarmlabs.tools` 加一个 `x402` 支付选项，与虎皮椒并列
- 保留虎皮椒作为主通道（国内用户首选）

**成本**：约 1-2 天开发，加 x402 facilitator SDK 集成测试
**收益**：
- 海外 agent 用户可通过 x402 按需调用（无信用卡/无跨境支付问题）
- 与 AIsa 生态对接，可上架 OpenClaw/ClawHub
- 国内用户无变化，虎皮椒体验不受影响

**风险**：
- x402 在中国大陆无用户，实际转化可能很低
- Circle facilitator 需要稳定的海外账户与合规材料

### 方案 B：x402 主通道 + 虎皮椒兜底

**改动**：
- 主通道切到 x402，Pro ¥39.9 → 约 $5.5 (USDC) / 终身 ¥199 → 约 $28 (USDC)
- 虎皮椒降级为「国内用户备选」入口，仅在 x402 支付失败时提示
- 定价改为按月订阅制（x402 更适合按需计费）

**成本**：3-5 天开发，需重构计费逻辑、定价模型、后台账单系统
**收益**：
- 面向全球 agent 市场（无地域限制）
- 与 AIsa / Circle / Coinbase / Solana 生态深度对接
- 定价灵活（按需 / 按量 / 按时长）

**风险**：
- 中国大陆用户几乎无法使用（USDC 需要海外钱包）
- 定价体系重构，现有 ¥39.9 定价需要保留迁移路径
- 稳定币合规风险（中国大陆监管不明）

### 方案 C：暂缓 x402，先观察 AIsa 生态成熟度

**改动**：不动代码，只在 `docs/agent/README.md` 里加一段「x402 长期路线」说明
**成本**：0（本轮已做）
**收益**：
- 避免过早投入
- 观察 AIsa 生态成熟度、竞品 x402 支付接入率
- 保留未来切换的灵活性

**风险**：
- 如果 x402 生态快速成熟，SwarmLabs 会失去早期布局的优势
- 海外 agent 用户仍需通过虎皮椒，转化率低

## 推荐

**短期（本轮已做）选方案 C**：不动代码，只加文档说明。理由：
1. 中国大陆用户占 SwarmLabs 目标市场主体，x402 转化有限
2. 47,566+ 结构化实体是核心资产，与支付方式解耦
3. AIsa 生态尚未成熟到必须抢首批接入的程度（截至 2026-09 只有 100M+ transactions，agent 生态仍在早期）
4. GOAI 2026 初赛评审关注的是技术深度而非支付通道

**中期（2026-Q4）观察指标**：
- AIsa 是否开放 skill 目录、SwarmLabs 是否能上架
- x402 facilitator 是否有中国大陆可用选项
- 海外 agent 用户占比是否突破 10%

**触发条件**：任何一条达到后启动方案 A。

## 未做

- 具体 x402 facilitator 集成代码（等用户拍板方案后再动）
- 定价模型重构（方案 B 才需要）
- 后台账单系统改造（方案 B 才需要）

## 相关文件

- `.well-known/mcp.json`：声明 requiredScopes，不含 wallet/payment（避免 AIShield mcp_manifest_scan 报过度代理）
- `.well-known/agent-card.json`：声明 apiKey 鉴权，未声明 x402
- `docs/agent/README.md`：加 x402 长期路线说明
- `docs/swarmlabs-x402-plan.md`：本文件
