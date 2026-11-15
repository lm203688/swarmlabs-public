# Muse Connector 申请草稿

## 状态

- **提交入口**: https://muse.ai/platform
- **提交时间**: 待用户批准
- **Muse 官方 spec 状态**: 未公开（截至 2026-09-24）
- **Muse 开放日**: 2026-09-18（Mark Zuckerberg 官方宣布）
- **Muse 平台规模**: 上线 10 天冲到 App Store Top 1（4.9 星 / 13000+ 评论）
- **Muse 定价**: Power $20/月，Maximum $100/月（Stripe Link 集成）

## 为什么现在做

- Muse 是**个人 agent**（非编码 agent），用户场景是"帮我做某件事"，而非"帮我写代码"
- 科研工作者（尤其研究生、独立研究员）用个人 agent 做日常事务、文献查询、笔记管理是常见路径
- SwarmLabs 的虚拟实验能力**天然适合作为科研场景的 agent 后端**：用户问"这个实验条件可行吗？"，Muse 调用 SwarmLabs API 返回带 UQ 的结果
- **零技术风险**：Muse 只要 REST API，SwarmLabs `/api/v3/*` 已经稳定
- **零内容风险**：Muse 的 Sentinel agent 会拦截所有出界数据，SwarmLabs 返回的是聚合结果（非敏感用户数据）

## 提交时填写的字段（Draft）

### 1. Product name
SwarmLabs — Virtual Science Lab for AI

### 2. What does your connector do?
SwarmLabs is an AI for Science virtual laboratory. It lets users ask Muse to run computational experiments across 82 scenarios (microbiology, synthetic biology, fermentation, chemical synthesis) with Gaussian Process + Uncertainty Quantification. Every result comes with a validation-and-verification (V&V) verdict (PASS / MARGINAL / REFUTED / ERROR), a three-dimensional evidence anchor check, and 42 fail-closed verification gates. Users can also retrieve 47,566+ structured scientific entities (trends, research gaps, cross-domain bridges, co-authorship insights) to inform their decisions.

### 3. How will users use it?
Natural language examples that Muse will route to SwarmLabs:

- "Can you verify if growing E. coli K-12 at 37°C in M9 medium with 200 rpm agitation is a plausible condition?" → `microbiology-simulate`
- "What are the current research gaps in synthetic biology for microbial strain design?" → `entity-retrieve`
- "Is the claim in this paper that yield increased 30% statistically supported?" → `paper-claim-validate`
- "Find a suitable fermentation parameter range for my protein expression system" → `microbiology-simulate`
- "What cross-domain bridges exist between enzyme engineering and materials science?" → `entity-retrieve`

### 4. Target audience
- PhD students and early-career researchers in biology, chemistry, materials science
- Independent AI4S researchers
- Small labs without dedicated computational infrastructure
- Anyone using Muse to help with scientific planning

### 5. Authentication model
- API key (Bearer token in Authorization header)
- Users obtain keys from https://license.swarmlabs.tools
- Free tier available for exploration
- Pro ¥39.9 / lifetime ¥199 (CNY); USD equivalent via Stripe
- x402 USDC stablecoin pay-per-use as long-term option

### 6. Data that users must approve
- SwarmLabs API key (needed to authenticate)
- User query (sent as JSON payload to /api/v3/* endpoints)
- Response is structured JSON, no PII returned

### 7. Data that SwarmLabs never requests
- User's full email, phone, or identity
- Third-party account credentials (Gmail, Calendar, etc.)
- Any file access
- Any payment credentials (handled by Stripe Link through Muse)

### 8. Rate limits and cost transparency
- Free tier: 100 requests/day per key
- Pro: 10,000 requests/day per key
- No surprise charges; per-request pricing documented

### 9. Compliance statement
- MIT-licensed open-source engine (https://github.com/lm203688/swarmlabs)
- No training data retention beyond session
- GDPR / CCPA-friendly (no PII collected)
- No data resale

### 10. Contact
- Lixing Li
- Email: 463102527@qq.com
- Website: https://swarmlabs.tools

## 待 Muse 官方补充的字段

Muse 官方页面目前仅公开三步流程（Describe → Review → Directory），**未公开以下字段**：
- 具体的技术 schema（REST 端点、认证流、错误码约定）
- 审核 SLA（预估 2-6 周）
- 分成条款（Stripe Link 分成比例）
- Sentinel agent 的数据流白名单

**建议**：本次不提交正式申请，先起草以上字段；等 Muse 官方发布详细 spec（预计 10 月）再正式提交。

## 触发条件（决定何时正式提交）

任一触发即提交：
1. `muse.ai/platform` 出现详细 spec 文档
2. 收到 Meta 主动联系邀请
3. 已有 Muse 用户明确询问"能不能接 SwarmLabs"

## 关联文件

- SwarmLabs API 文档：https://swarmlabs.tools/docs/api
- MCP manifest：https://swarmlabs.tools/.well-known/mcp.json
- A2A agent card：https://swarmlabs.tools/.well-known/agent-card.json
- License portal：https://license.swarmlabs.tools

## 更新日志

- **2026-09-24**：初版。Muse 官方 spec 未公开，本文件为草稿状态。
