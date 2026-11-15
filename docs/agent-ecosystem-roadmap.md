# Agent 生态接入路线图（2026-09-24 版）

## 背景

用户提出两个方向：
1. 接入 Muse / GrokBot 等个人 agent 平台
2. 在个人 agent 大爆发前，布局 SwarmLabs 相关功能作为 agent 后端

调研后结论：
- **Grok Bot** 无公开 API（xAI 2026-08-22 官方口径），现在没机会
- **Muse** 2026-09-18 才开放开发者 portal，spec 未公开，仅申请入口
- **MCP 生态** SwarmLabs 已 80% 就位（A2A agent-card + SEP-2640 manifest 已发布），只需完善上架

## 现有资产盘点

**已有**：
- `.well-known/agent-card.json`（A2A 0.3.0，3 skills）
- `.well-known/mcp.json`（SEP-2640，3 capabilities + 3 scopes）
- `.well-known/jwks.json`（Ed25519 签名密钥）
- `docs/agent/README.md`（说明）
- Glama 上架（MCP 官方 registry）

**3 个 capabilities**：
1. `microbiology-simulate` — GP+UQ 引擎，63 场景
2. `entity-retrieve` — 157,866+ 结构化科研实体
3. `paper-claim-validate` — 论文声明 UQ 覆盖度审计

**3 个 scopes**（均白名单，无敏感权限）：
- `swarmlabs:read`
- `swarmlabs:compute`
- `swarmlabs:entities`

## 阶段划分

### 阶段 1（本次，2026-09-24）：MCP 生态铺满

**动作**：
- 完善 Glama 上的 SwarmLabs 页面（README + 使用示例 + scope 说明）
- 起草 Cursor / Cline / Windsurf / Claude Desktop 上架材料
- 提交 AIsa.one 目录（已有 agent-card）
- GitHub Marketplace 上架（MCP server 分类）
- 起草 Muse Connector 申请材料（等 Muse spec 公开后提交）

**风险**：
- 各市场审核政策差异大，需逐一确认
- 中国大陆无法直连部分海外目录
- Muse 审核流程不透明，无法预估时间

### 阶段 2（10 月，视 Muse spec 公布情况）

- Muse 官方若发布技术 spec（工具 schema、审核 SLA、分成条款），评估后决定是否投入 1-2 周开发 Muse-specific connector
- 若 spec 迟迟不公开，暂缓

### 阶段 3（11-12 月，视 xAI 政策）

- xAI 若开放 Grok Bot API，评估 Grok Bot 接入
- 期间通过 Grok Build CLI（`grok.com/build`）+ MCP 协议间接触达 Grok 生态

## 与准入判据的关系

**准入判据**：「让虚拟实验在无真实实验、无网络、无设备下更可替代」

**这个方向的定位**：
- **直接贡献**：0（不提升引擎能力）
- **间接贡献**：通过 agent 生态放大触达，让科研工作者在个人 agent 里调用 SwarmLabs，等于扩大虚拟实验的可替代性边界
- **风险**：分发工作量大，若核心引擎不进步，接入再多 agent 也无意义

**结论**：作为**副产品**做，不与核心引擎开发争夺资源。

## 关键决策记录

- **Muse 接入**：**纳入日程**，先起草申请材料（本次），等 spec 公开再提交
- **Grok Bot**：无接口，暂列观望
- **MCP 生态**：本次主攻，一次性铺开 6-8 个市场
- **自建 agent 平台**：不做，与准入判据冲突

## 参考链接

- Muse Platform: https://muse.ai/platform
- MCP Registry: https://registry.modelcontextprotocol.io/
- Claude Desktop MCP: https://docs.anthropic.com/en/docs/claude-code/mcp
- Cursor MCP: https://docs.cursor.com/mcp
- Glama: https://glama.ai/
- AIsa.one: https://aisa.one/

## 更新日志

- **2026-09-24**：初版路线图。Muse 接入纳入日程，本次生成申请草稿。
