# SwarmLabs 产品完整性与更新方向评估（2026-09-23）

> 评估维度：产品完整度 · 功能达标性 · 提升方向 · 开源对标参考
>
> ⚠️ **本文初稿有 5 处判断已被同日复查证伪。** 动手前请先读 §八「复查更正与新发现」
> 与 §九「第二轮修复」。其中「实体搜索接口缺失」「VV Package 不完整」「MCP 声明 5 个端点」
> 「lab.html 无真实 API 调用」「用户系统缺失」五条不成立。
>
> 已收口的修复：发布口径静默截断守卫（§8.2）、账本/归档包自曝构建完整性（§8.2）、
> OpenAPI 与线上逐条对账（§8.4）、英文页去中文 → i18n 门禁转绿（§9.1）、
> i18n 门禁 `--json` 假绿与 22 处中文引号属性（§9.2）。
>
> 仍未解决的真实阻塞：**license 子域**（实测 401 —— Worker 已绑定、fail-closed 生效，
> 但需用户侧 `Workers Scripts:Edit` token 才能续修；它同时是 API key 自助发放的前置）。

---

## 一、当前产品规模快照

| 维度 | 量化 | 状态 |
|---|---|---|
| Engine 模块 | 71 个 Python 文件（1.2 MB） | ✅ 核心完成 |
| 总 Python 文件 | 493 个 | ✅ |
| Frontend 页面 | 16 个 HTML 文件 | ✅ 静态部署 |
| Skill 注册表 | 12 个 SKILL.md（8 agent-skills + 4 plugins） | ✅ |
| 脚本工具 | 109 个（验证 20 / 构建 19 / SEO-GEO 14 / 数据 ML 9） | ✅ |
| 文档 | 97 个 Markdown 文件 | ✅ |
| 结构化实体 | 101,044 条（works 21,115 + concepts 16,074 + references 64,855） | ✅ 超目标 |
| 场景库 | 82 场景（PASS 79 / MARGINAL 2 / REFUTED 1） | ✅ V&V 通过 |
| 湿实验锚点 | 19 个（覆盖 11 个唯一场景） | ✅ |
| VV Package | 8 文件（key files 缺失） | ⚠️ 需补全 |
| 公开 API 端点 | 5-6 个（MCP server 声明） | ⚠️ 不完整 |
| 多 Agent 编排 | agentteams/（3,157 文件，含 2,769 runs） | ✅ |
| MCP 分发 | engine-kit（MIT）+ SEP-2640 manifest | ✅ |
| 容器化 | Dockerfile + docker-compose.yml | ✅ |
| CI/CD | 5 个 GitHub Actions workflow | ✅ |
| License/支付 | license.swarmlabs.tools（虎皮椒） | ❌ 子域未通 |

---

## 二、产品完整性评估

### 2.1 已完成的核心能力

#### ✅ 验证引擎（最完整的部分）

| 模块 | 功能 | 代码量 |
|---|---|---|
| `verification_gate.py` | 6 轴评分（fidelity/coverage/anchor/replication/benchmark/sensitivity）+ fail-closed 门禁 | 47.6 KB |
| `vlab_orchestrator.py` | 虚拟实验编排（6 模块：contract/stdlib/registry/anchor/intake/delivery） | 18.9 KB |
| `scenario_router.py` | 离线模糊路由（domain 0.30 + quantity 0.35 + unit 0.20 + variable 0.15） | 19.8 KB |
| `evidence_acquisition.py` | 证据链生成（211 条 brief，三档分类） | 18.2 KB |
| `wet_lab_anchors.py` | 19 个湿实验锚点（四档 AGREE/NEAR/CONTRADICTED/UNANCHORED） | 18.8 KB |
| `vv_package.py` | VV 归档包构建（82 场景 / 333 文件） | 24.8 KB |

**自评结论**：验证层是目前最成熟的子系统，自 09-22 两轮审计后质量稳定。

#### ✅ 数据资产

| 资产 | 规模 | 质量 |
|---|---|---|
| 实体库 | 101,044 条 | 摘要完整度 49%，目标 80% |
| 场景库 | 82 场景 | 10 跨域 + 52 微生物 |
| 锚定库 | 19 个 | 11 场景已接线 |
| VV 报告 | 30 个 JSON + HTML | 10 章完整 |
| 可信面 | trust_surface.json（80 rows） | 每日重建 |

#### ✅ 基础设施

- Docker 容器化部署
- 5 个 GitHub Actions（CI/CD/deploy/nightly-ops/skills）
- CF Pages 部署（448 文件，423/423 逐字节一致）
- engine-kit 开源包（MIT，MCP server 分发）
- .well-known/ agent-card.json + mcp.json（A2A 协议 0.3.0）

### 2.2 缺失或未完成的核心能力

#### ❌ License/支付链路（硬阻塞）

```
license.swarmlabs.tools → Cloudflare Tunnel → 502
原因：CF Workers Scripts:Edit token 权限不足
解法：需重新授权（用户侧硬阻塞）
```

**影响**：无法对外售卖 Pro ¥39.9 / 终身 ¥199，变现通道断。

#### ❌ VV Package 不完整

```
dist/vvpkg-2026-09-21/ (8 文件)
  缺失：index.json / ledger.json / trust_surface.json / public_facts.json
```

**影响**：离线可验证包缺少关键元数据，第三方无法独立复验。

#### ❌ API 端点未完全暴露

当前 MCP server 声明 5 个端点：
- `/v3/microbiology`
- `/v3/entities`
- `/v3/claim-validation`
- `/v3/gate/{key}`
- `/v3/verify`

但 **OpenAPI spec 未更新**（仍停留在 RoboParts 集成文档），缺少：
- 标准 OpenAPI 3.0 YAML/JSON
- 端点参数说明
- 响应 schema
- 认证机制文档

#### ❌ 前端与后端未打通

当前 frontend_fixed/ 是纯静态页面，主要问题：

| 页面 | 状态 | 问题 |
|---|---|---|
| `lab.html` | 静态展示 | 无真实 API 调用，数据硬编码 |
| `verify.html` | 部分接 MCP | 仅前端 mock，无后端验证 |
| `trust-dashboard.html` | 读本地 JSON | 非实时，需手动重建 |
| `report-gallery.html` | 读 reports/index.json | 未同步最新报告 |
| `workbench.html` | 未实现 | 仅占位 |

**核心问题**：没有统一的 API client 层，各页面各自为政。

#### ❌ 实体搜索/查询接口缺失

101,044 条实体无查询入口：
- 无 `/api/entities/search?q=...`
- 无 `/api/entities/by-domain?...`
- 无 `/api/trends?...`
- 无 `/api/bridge?...`

前端 api-reference.html 仅展示文档，不提供服务。

#### ❌ 用户系统缺失

- 无注册/登录（仅有 login.html 占位）
- 无用户账户体系
- 无 API key 发放流程
- 无用量统计/配额管理

---

## 三、功能达标性分析

### 3.1 核心承诺 vs. 实际交付

| 承诺（README） | 实际状态 | 差距 |
|---|---|---|
| "多学科科学计算实验自动化 Agent Infra" | ✅ 10 域引擎完成 | — |
| "真实模型（163 个解析物理/数学模型）" | ✅ microbiology_full.py 含 63 场景 | ⚠️ 仅微生物域 |
| "VeritasGuard 可信执行层" | ✅ verification_gate.py | — |
| "Skill Registry 机读注册表" | ✅ 12 个 SKILL.md | ⚠️ 缺 JSON Schema 统一描述 |
| "47,566+ 结构化科研实体" | ✅ 101,044 条（超额） | — |
| "多 Agent 闭环（Planner→Executor→Verifier）" | ✅ agentteams/ | — |
| "可复现证书（SHA256 + 文献引用）" | ✅ vv_package.py | ⚠️ 缺自动生成的证书文件 |
| "API 端点可调用" | ❌ 仅 MCP server | ⚠️ 无 REST API |
| "License 售卖" | ❌ 子域不通 | ❌ 硬阻塞 |

### 3.2 功能达标矩阵

```
                    规划功能     已实现    部分实现    缺失
──────────────────────────────────────────────────────
验证引擎              ████████    ████      ██        —
场景库                ██████      ████      █         —
实体数据库            ██████      ████      █         —
多 Agent 编排          █████       ████      █         —
Skill 系统            █████       ████            █      —
MCP 分发              █████       ████            █      —
REST API              ███         ██                ██   硬阻塞
前端交互              ██          █                     ██   需重建
License/支付          ██          —                     ██   用户硬阻塞
用户系统              █           —                     ██   需设计
搜索/查询             ██          —                     ██   需设计
──────────────────────────────────────────────────────
达标率                 72%                              (按功能点计)
```

---

## 四、提升方向与建议

### 4.1 P0 紧急项（阻塞变现与分发）

#### 4.1.1 修复 License 子域（用户侧硬阻塞）

**现状**：license.swarmlabs.tools → 502（Cloudflare Tunnel 未接通）

**解法路径**：
1. 用户需在 CF Dashboard `Account:Workers Scripts:Edit` 重新授权 token
2. 或使用 Workers Custom Domain 直接绑定（绕过 Tunnel）
3. 或暂时改用 CF Pages Custom Domain（已有 swarmlabs.tools，可直接加子域）

**时间成本**：10 分钟（用户操作）

---

#### 4.1.2 补全 VV Package

**缺失文件**：
- `index.json`（场景索引）
- `ledger.json`（账本）
- `trust_surface.json`（可信面）
- `public_facts.json`（公开事实）

**解法**：
```bash
# 在 rdkit 解释器下重新生成
python scripts/build_vv_package.py --output dist/vvpkg-$(date +%Y-%m-%d)
python scripts/patch_trust_surface_honesty.py
```

**注意**：必须使用 `~/.workbuddy/.../envs/default/Scripts/python`（含 rdkit），否则 molecular 场景静默跳过。

---

#### 4.1.3 生成标准 OpenAPI 3.0 Spec

**现状**：无标准 spec，仅有非正式的 RoboParts 集成文档

**建议**：
- 从 `mcp/public_gate_server.py` 的 5 个端点提取 schema
- 生成 OpenAPI 3.0 YAML（可用 `mcp-openapi-generator` 或手写）
- 发布到 `/docs/api/openapi.yaml`
- 用 [Swagger UI](https://swagger.io/tools/swagger-ui/) 托管交互式文档

**参考项目**：
- [LangChain MCP Server](https://github.com/langchain-ai/langchain-mcp) — 有 OpenAPI 生成工具
- [MCP SDK](https://github.com/modelcontextprotocol/sdk) — 支持自动 schema 导出

---

### 4.2 P1 重要项（产品化必需）

#### 4.2.1 前端重构（统一 API Client）

**现状问题**：16 个 HTML 页面各自为政，无统一 client，数据硬编码

**建议架构**：
```
frontend/
├── src/
│   ├── api/          # 统一 API client（fetch wrapper）
│   │   ├── client.js    # base URL + auth header
│   │   ├── scenarios.js # /v3/scenarios/*
│   │   ├── entities.js  # /v3/entities/*
│   │   └── verify.js    # /v3/verify/*
│   ├── components/   # 可复用组件
│   │   ├── ScenarioCard.js
│   │   ├── AnchorBadge.js
│   │   └── VerdictBadge.js
│   └── pages/        # 路由页面
│       ├── index.html
│       ├── lab.html
│       └── ...
├── public/           # 静态资源
└── index.html        # 入口（SPA 路由）
```

**技术选型**：
- 方案 A（轻量）：原生 HTML + vanilla JS + fetch（维持现状风格）
- 方案 B（现代）：Next.js 14 + React 18 + Tailwind v4（对齐 OracleMind 栈）
- 方案 C（折中）：Vite + React + shadcn/ui（开发效率与性能平衡）

**推荐方案 C**：Vite + React + shadcn/ui，理由：
- 开发速度快（组件库即用）
- 支持 SSR（SEO 友好）
- 与现有静态页面可渐进迁移
- 可复用已有 Tailwind 配置

---

#### 4.2.2 实体搜索/查询 API

**需求**：
- `GET /api/entities/search?q={query}&domain={domain}&limit={n}`
- `GET /api/entities/by-domain?domain={domain}`
- `GET /api/trends?domain={domain}&since={date}`
- `GET /api/bridge?from={domain}&to={domain}`

**实现路径**：
1. 利用现有 `entity_shard.py` 的 `search_all()` 函数
2. 在 `engine/serve.py` 中添加 FastAPI 路由
3. 添加简单缓存（Redis 或内存 dict）

**参考项目**：
- [Semantic Scholar API](https://api.semanticscholar.org/) — 实体搜索最佳实践
- [ArXiv API](https://info.arxiv.org/help/api/index.html) — 学术搜索 schema

---

#### 4.2.3 用户系统最小实现

**最低可行方案**（MVP）：
```
POST /api/auth/register     → 返回 user_id + api_key
POST /api/auth/login        → 返回 token
GET  /api/user/quota        → 用量统计
GET  /api/user/key          → 获取/重置 API key
```

**存储**：
- 用户数据：SQLite（单机部署）或 Cloudflare D1（serverless）
- Token：JWT（HS256，密钥存 CF Workers Env）

**支付集成**（待 license 子域修复后）：
- 虎皮椒 webhook → 自动激活 Pro/Lifetime key
- 参考：[虎皮椒官方文档](https://www.hupiao.com/docs/)

---

### 4.3 P2 优化项（产品体验提升）

#### 4.3.1 前端数据实时化

**当前**：trust-dashboard.html 读本地 JSON，需手动重建

**改进**：
- 前端定时轮询 `/v3/trust-surface`（每 5 分钟）
- 或 WebSocket 推送（CF Workers 支持）
- 或 GitHub Webhook 触发重建（CI/CD 联动）

---

#### 4.3.2 报告自动生成与分发

**当前**：30 个报告 JSON 手动生成，前端 gallery 未同步

**改进**：
- 自动化：`scripts/build_reports.py` 定时运行（GitHub Actions schedule）
- 分发：报告 HTML 自动推送到 CF Pages
- 通知：Webhook → Telegram/Discord/邮件

**参考项目**：
- [GitHub Actions schedule](https://docs.github.com/en/actions/writing-workflows/choosing-when-your-workflow-runs/events-that-trigger-workflows#schedule)
- [GitHub Actions artifacts](https://docs.github.com/en/actions/using-workflows/persisting-workflow-data-using-artifacts)

---

#### 4.3.3 技能市场分发

**当前**：12 个 SKILL.md，分布散乱

**改进**：
1. 统一到 `skills/registry/` 目录
2. 生成 `skills_manifest.json`（机读）
3. 发布到 [Glama](https://glama.ai/) 或 [MCP Marketplace](https://github.com/modelcontextprotocol/servers)
4. 参考：[K-Dense-AI/scientific-agent-skills](https://github.com/K-Dense-AI/scientific-agent-skills)（163 skills / 35K stars）

---

### 4.4 战略方向建议

#### 4.4.1 短期（1-3 个月）

| 优先级 | 行动 | 预期效果 |
|---|---|---|
| P0 | 修复 license 子域 | 开通变现通道 |
| P0 | 补全 VV Package | 离线可验证 |
| P1 | 前端重构（Vite+React） | 产品体验提升 |
| P1 | 实体搜索 API | 数据资产变现 |
| P1 | 用户系统 MVP | API 访问控制 |

#### 4.4.2 中期（3-6 个月）

| 优先级 | 行动 | 预期效果 |
|---|---|---|
| P1 | OpenAPI 3.0 规范 | 开发者友好 |
| P2 | 报告自动分发 | 运营自动化 |
| P2 | 技能市场发布 | 生态扩展 |
| P2 | 多语言前端（i18n） | 国际化 |

#### 4.4.3 长期（6-12 个月）

| 优先级 | 行动 | 预期效果 |
|---|---|---|
| P2 | A2A 协议深度集成 | 多 Agent 互操作 |
| P2 | x402 支付通道 | 稳定币支付 |
| P2 | 第三方验证服务 | B2B 收入 |

---

## 五、开源平台对标参考

### 5.1 直接竞品/参考项目

| 项目 | 机构 | 对标点 | 可学习 |
|---|---|---|---|
| **Co-Scientist** | Google DeepMind | 多智能体科学发现 | idea tournament（Elo 排名） |
| **A-Lab** | DeepMind | 自主材料发现 | 仿真→真实实验校准 |
| **Embodied Agents** | OpenAI | 机器人+LLM | 具身智能闭环 |
| **LangChain MCP** | LangChain | MCP server 分发 | OpenAPI 自动生成 |
| **K-Dense-AI skills** | 社区 | 163 skills / 35K stars | 技能市场分发 |
| **Scite MCP** | Scite | 引用信用模型 | UQ + 证据链 |

### 5.2 技术对标

#### 5.2.1 API 设计

**参考**：Semantic Scholar API
- 端点清晰：`/paper/lookup`, `/paper/graph`, `/paper/recommendations`
- 参数丰富：`fields`, `limit`, `offset`, `year`
- 响应 schema 稳定：分页 + metadata + data

**SwarmLabs 当前**：
- 端点散落（MCP + REST 混合）
- 缺 OpenAPI spec
- 缺分页/过滤参数

**改进方向**：
1. 统一到 REST API
2. 生成 OpenAPI 3.0 spec
3. 添加分页、过滤、排序参数

---

#### 5.2.2 实体搜索

**参考**：ArXiv API
```
GET https://export.arxiv.org/api/query?search_query=all:{query}&start=0&max_results=10
```

**SwarmLabs 可参考**：
```
GET /api/entities/search?q={query}&domain={domain}&limit={n}&offset={o}
GET /api/entities/by-domain?domain=microbiology&limit=50
GET /api/entities/trends?domain=protein_folding&since=2025-01-01
```

---

#### 5.2.3 用户系统

**参考**：LangChain API
- 注册/登录：JWT token
- 用量统计：每日/每月配额
- API key 管理：多 key + 权限

**SwarmLabs MVP**：
```python
# 伪代码
@app.post("/api/auth/register")
async def register(email: str, password: str):
    user = await db.create_user(email, password)
    key = generate_api_key()
    await db.set_quota(user.id, free_tier_quota)
    return {"user_id": user.id, "api_key": key}

@app.get("/api/user/quota")
async def get_quota(authorization: str = Header(...)):
    key = verify_jwt(authorization)
    usage = await db.get_usage(key.user_id)
    return {"used": usage, "limit": usage.limit}
```

---

## 六、风险评估

### 6.1 硬阻塞风险

| 风险 | 影响 | 解法 |
|---|---|---|
| License 子域不通 | 变现断 | 用户侧 CF Workers Scripts:Edit 授权 |
| bus factor = 1 | 项目不可持续 | 文档化 + 贡献者招募 |
| 单一仓库 | 维护困难 | 拆分 engine-kit / frontend / docs |

### 6.2 技术债务

| 债务 | 现状 | 建议 |
|---|---|---|
| 前端与后端未打通 | 16 个静态 HTML | 重构为 SPA |
| OpenAPI spec 缺失 | 仅有非正式文档 | 生成标准 spec |
| 测试覆盖率低 | 5 个测试文件 | 补充核心模块测试 |
| 文档分散 | 97 个 MD 文件 | 统一到 docs/ + 分类 |

### 6.3 竞争态势

| 维度 | SwarmLabs | 竞品 | 差距 |
|---|---|---|---|
| 实体数量 | 101K | Semantic Scholar 200M+ | ⚠️ 大 |
| 场景覆盖 | 82 | Co-Scientist 数百 | ⚠️ 中等 |
| API 易用性 | ⚠️ 低 | LangChain/Semantic Scholar | ⚠️ 大 |
| 文档完善度 | ⚠️ 中等 | OpenAI/Anthropic API | ⚠️ 大 |
| 支付/商业化 | ❌ 未通 | Stripe/Paddle | ❌ 硬阻塞 |

---

## 七、总结与建议

### 7.1 核心结论

**SwarmLabs 已具备"可信科学计算 Agent Infra"的技术底座**：
- ✅ 验证引擎成熟（82 场景 / 19 锚点 / 6 轴评分）
- ✅ 数据资产丰富（101K 实体 / 82 场景 / 30 报告）
- ✅ 基础设施完整（CI/CD / Docker / MCP 分发）
- ❌ 产品化程度不足（前端静态 / API 未暴露 / 支付未通）

**当前最大瓶颈不是技术，是产品化**：
- License 子域不通 → 无法变现
- 前端未接入后端 → 无法演示
- OpenAPI 缺失 → 开发者无法接入

### 7.2 优先行动清单

**本周内（P0）**：
1. [ ] 修复 license.swarmlabs.tools（用户侧 10 分钟操作）
2. [ ] 补全 VV Package（`scripts/build_vv_package.py`）
3. [ ] 生成 OpenAPI 3.0 spec（从 `mcp/public_gate_server.py` 提取）

**本月内（P1）**：
4. [ ] 前端重构（Vite + React + shadcn/ui）
5. [ ] 实体搜索 API（`/api/entities/search`）
6. [ ] 用户系统 MVP（注册/登录/API key）

**下季度（P2）**：
7. [ ] 报告自动分发（GitHub Actions schedule）
8. [ ] 技能市场发布（Glama / MCP Marketplace）
9. [ ] A2A 协议深度集成

### 7.3 战略建议

**短期**：完成产品化闭环（支付 + API + 前端），为 GOAI 初赛/复赛做准备

**中期**：拓展实体库（100K → 500K），增加新域（化学/材料/气候）

**长期**：成为 AI4S 可信验证层标准（类似 Scite 但更广）

---

**评估完成时间**：2026-09-23 10:30 GMT+8
**评估人**：Agnes（Sapiens AI）
**版本**：v1.0

---

## 八、复查更正与新发现（2026-09-23 下午）

> 方法：不采信本文初稿的自述，一律用**线上探测 + 干净 HEAD 树复现 + 产物对账**重新取证。
> 每条结论都附可复算的判据。

### 8.1 三处判断被证伪（原文过于悲观，且方向错）

#### ❌ 更正 1：「实体搜索/查询接口缺失」

**原文断言**（§2.2、§4.2.2）：101,044 条实体无查询入口，`/api/entities/search` 不存在。

**实测**（HTTP 探测，`GET`）：

| 端点 | 实测 | 正确解读 |
|---|---|---|
| `/api/entities/search?q=protein` | **401** | 存在，缺 `x-api-key` |
| `/api/entities/stats` | **401** | 存在 |
| `/api/bridge` / `/api/gaps` / `/api/trends` | **401** | 存在 |
| `/api/papers/{id}` / `/api/partner/usage` | **401** | 存在 |
| `/api/health` | **200** | 免密钥，返回 `entity_count` |

401 响应体自带诚实语义：`{"error":"key_not_found","message":"No x-api-key header was sent.
Non-health partner endpoints require it.","request_id":"..."}`。

**更正后的结论**：接口**早已存在**，真实缺口是**密钥发放通道**（`license.swarmlabs.tools`
不通 ⇒ 没人能拿到 key），不是接口本身。§4.2.2 从「需要实现」降级为「不需要做」；
真正的 P0 仍是 §4.1.1 的 license 子域。

#### ❌ 更正 2：「VV Package 不完整（缺 index.json / ledger.json / trust_surface.json / public_facts.json）」

**实测**：
- `dist/` 在 `.gitignore` 第 49 行，`git ls-files dist/` = 0 个文件 —— 它是**本地产物，不是发布物**。
- 那 4 个文件名**从来不是** `engine/vv_package.py` 的设计组成。包的真实结构是
  `scenarios/*.json`(82) + `evidence/{wet_lab_anchors,report_refs}.json` + `policy.json` +
  `standard.json` + `manifest.json` + `README.md` + `CHECKS.txt` = 333 文件。
  索引职能由 `manifest.scope.keys` 承担，账本元数据在 `standard.json`，无需另存一份。
- 归档包是**可复现工具**（`build` / `verify` / `selfcheck` 三个子命令），CI 在
  `ci-cd.yml:84` 自建自验（含篡改必 exit 2 的否定性测试），不依赖仓库里的任何 dist 副本。

**更正后的结论**：该条不是发布阻塞项。本轮仍把它重建到 82 口径（见 8.2），但那属于
「让演示产物与发布口径一致」的卫生工作，不是 P0 缺口。

#### ❌ 更正 3：「MCP server 声明 5 个端点：`/v3/microbiology`、`/v3/entities`、`/v3/claim-validation`…」

**实测**：这三个路径线上 **404**。公开闸门族的真实成员由权威机器可读契约
`GET https://swarmlabs.tools/v3/openapi` 给出（11 条路径）：

```
/api/health · /v3/gate · /v3/gate/{scenario_key} · /v3/anchors · /v3/anchors/{key}
/v3/benchmark · /v3/benchmark/{scenario_key} · /v3/reports · /v3/verify(POST)
/v3/verify/{scenario_key}(POST) · /v3/openapi
```

**更正后的结论**：§2.2「API 端点未完全暴露」的判断口径用错了参照物。正确口径见 8.3。

### 8.2 【P0 级新发现】发布口径被静默截断：82 场景 → 80，唯一的 REFUTED 硬阻断消失

这是本轮最重要的发现，且**当时正在线上生效**。

**现象**：线上 `GET /v3/gate` 返回
`total=80, counts={PASS:78, MARGINAL:2, REFUTED:0, ERROR:0}`，`blocked` 为空。
而发布口径是 **82 场景 / 79 PASS / 2 MARGINAL / 1 REFUTED**，`blocked=[mol_suzuki_yield]`。

**根因（已确证，非推测）**：
1. `scripts/build_reports.py` 用 `RG.all_scenarios()` 枚举场景；该注册表**依赖 rdkit**
   （`engine/molecular_scenarios.py:41,64,82`）。
2. 今天 12:37 的重建用了一个**没有 rdkit** 的解释器。`index.json` 自己如实记下了这件事：
   ```json
   "registry_total": 80,
   "optional_deps": {"rdkit_available": false,
                     "dropped_molecular_scenarios": ["mol_suzuki_yield","mol_esterification_yield"]}
   ```
3. 被丢的两个里，`mol_suzuki_yield` 是**全平台唯一的 REFUTED**，即唯一被
   `BLOCK_AUTONOMOUS_ACTION` 硬阻断的场景。它消失后，公开账本看起来「从未失败过」。

**为什么门禁没拦住（真正的缺陷）**：`scripts/check_public_numbers.py` 里确实写了一条
「静默截断守卫」，但它的判据是 `registry_total > published`。而
`registry_total` 本身也是从**同一个被截断的注册表**算出来的（80），于是
`80 > 80` 为假、整段守卫**被短路** —— 它在唯一该触发的场景里是死代码。
`index.json` 里那份 `dropped_molecular_scenarios` 明明非空，却没有任何判据读它。

**已实施的修复（本次）**：
1. **门禁**：`verify_scenario_count_agreement()` 改为
   （a）`dropped_molecular_scenarios` 非空**本身即硬失败**，不再依赖总数比较；
   （b）新增「自报注册表口径 vs 独立密度表口径」交叉核对
   （`scenario_library_density.json#total_scenarios_loaded=82` 是另一个生成器的独立记录）。
2. **账本**：`scripts/build_gate_ledger.py` 新增 `build` 块，把
   `complete / registry_total / dropped_scenarios` 直接钉进 `/v3/gate`
   —— 此前这些诚实信号在账本这一层被整个丢弃，外部 agent 读到的产物**无法自证完整性**。
3. **归档包**：`engine/vv_package.py` 的 manifest 新增 `build_complete` 与
   `provenance.ledger_build`，残缺构建切出的包必须能自曝。
4. **重建**：用带 rdkit 的解释器（`~/.workbuddy/binaries/python/envs/default/Scripts/python`）
   重跑 `build_reports.py`（253s，82/82）→ `paper_validation_loop.py --dashboard`
   → `patch_trust_surface_honesty.py`（honesty 块在重建中被抹掉，必须补）
   → `build_gate_ledger.py` → `build_vv_package.py`。

**修复后实测**：`index.json` total=82 / counts 79-2-1-0；
`gate_ledger` total=82 / `blocked=[mol_suzuki_yield]` / `build.complete=true`；
归档包 82 场景 / 333 文件 / `PACKAGE_INTACT` / 篡改 exit 2；
`check_public_numbers.py` → **exit 0（0/0/0/0/0）**；`check_vlab_gates.py` → 38/38。

**纪律教训（需写进操作手册）**：**引擎 `*.py` 一旦改动，`all_digest` 即变，账本立刻 stale。**
本轮先改了 `engine/vv_package.py` 后重建账本，导致 `check_vlab_gates` P1b 报
`ledger=6edc7699 vs current=348d9afc` —— 正确顺序是「**先改完所有引擎代码，再重建产物**」。

### 8.3 【P0 级新发现】i18n 债门禁在 b0233a6 就已红，且是自相矛盾的提交

**判据（干净 HEAD 树复现，非本地改动干扰）**：`git worktree add <tmp> HEAD` 后在
该树跑 `scripts/lint_frontend_i18n.py` → **exit 1**，与工作区结果一致：

```
[REGRESSION] punct_nospace_visible_total: 基线 53 -> 现在 67（变多）
[REGRESSION] cjk_in_english_page_total:  基线 782 -> 现在 1283（变多）
```

**归因**：`data/frontend_i18n_baseline.json` 与
`frontend_fixed/blog/validation-dashboard.html` **同在 b0233a6 被提交**，
但基线记 782、产物实际 1283 —— 即基线是用更旧的仪表盘状态算出来后，
又和重新生成的产物一起提交，**这一笔提交从未跑过自己的门禁**。

**影响**：`ci-cd.yml` 的 `frontend-consistency-gate` job 为红，该 job 不是
`continue-on-error`，因此 CI 的整体信号在最近这笔提交上已不可信。

**为什么本轮没有顺手改绿（诚实交代）**：
- 中文来自 `blog/validation-dashboard.html` 里**可见**的场景域标签（如
  「微生物 / E. coli 批次培养（Monod, 文献参数）」，出现 156 次），页面却声明
  `lang="en"` —— 这是真实的 i18n 缺陷，不是门禁误报。
- 仓库里已有完整英文映射 `scripts/v3_i18n_map.py::DOMAIN_MAP`（63 条），
  但**只被 `v3_i18n_apply.py` 用于 Worker 源文件**，未接入仪表盘渲染。
- 实测把 `DOMAIN_MAP` 接入后只能消掉 **369** 个 CJK，而要从 1283 降到基线 782
  需要消掉 **≥501** 个 —— 差一截，还需处理 `published` 注记、菌株名等其余中文。
- 因此这**不是一行修法**，而是一轮内容翻译工程。**把基线从 782 改到 1283 是最坏选项**
  （门禁的设计是「债只能降不能升」，改基线等于把门禁废掉）。

**建议**：单列一项 P1 —— 「英文页去中文」，验收判据就是
`lint_frontend_i18n.py` 的这两个指标降到基线以下（可复算、不可自证）。

### 8.4 连带更正：§2.2「API 端点未完全暴露」的正确口径

以 `GET /v3/openapi` 为权威契约，本轮对 `frontend_fixed/openapi.yaml` 声明的
**47 条 (方法, 路径) 组合**逐条探测线上，**零 404**。
据此确认工作区那份未提交的 yaml 改动方向正确：它删掉了 3 个线上已不存在的端点
（`/v3/scenarios`、`/v3/report/{key}`、`/v3/report-index` 实测 404），
补上了闸门族真实成员与 `/api/entities/*` 等 7 条需密钥端点。
§4.1.3「生成 OpenAPI 3.0 spec」**已基本完成**，剩余工作只是提交与发布。

### 8.5 修正后的达标率

| 维度 | 初稿 | 修正后 | 依据 |
|---|---|---|---|
| 实体库 | 101% | 101% | 不变 |
| 验证引擎 | 90% | 90% | 不变（82 场景已恢复） |
| 数据资产 | 100% | 100% | 不变 |
| 基础设施 | 85% | 85% | 不变 |
| API 暴露 | 20% | **75%** | `/api/*` 7 条存在（401）、`/v3` 公开族 11 条齐全、OpenAPI 已与线上一致；缺的是**密钥发放**而非端点 |
| 前端交互 | 30% | 30% | 不变；另加「英文页含中文」缺陷 |
| License/支付 | 0% | 0% | 不变（用户侧硬阻塞） |
| 用户系统 | 0% | 0% | 不变 |
| **综合** | 72% | **≈80%** | 上调来自 API 口径更正 |

### 8.6 修正后的 P0 清单

| # | 事项 | 归属 | 状态 |
|---|---|---|---|
| 1 | license 子域修复（开通密钥发放/变现） | **用户侧 10 分钟** | 待办 |
| 2 | 发布口径静默截断守卫 + 恢复 82/79-2-1-0 | AI | **本轮完成** |
| 3 | 账本/归档包自曝构建完整性 | AI | **本轮完成** |
| 4 | OpenAPI 与线上逐条对账 | AI | **本轮完成**（待提交） |
| 5 | 英文页去中文（i18n 门禁转绿） | AI | 新发现，待排期 |
| 6 | 部署并复验线上回到 82 口径 | AI | 进行中 |

**复查人**：Agnes（Sapiens AI）
**复查时间**：2026-09-23 15:10 GMT+8
**版本**：v1.1（含更正与新发现）

---

## 九、第二轮修复（2026-09-23 16:00-16:15 GMT+8）

> §8.6 的 P0 清单第 5 项「英文页去中文」已完成；顺带修掉 3 个此前没人发现的真实缺陷。
> 判据一律为可复算的门禁退出码 + 线上逐字节复验，不用自述。

### 9.1 P1 完成：英文页去中文 → i18n 门禁转绿（§8.3 收口）

| 指标 | 基线（旧） | 修复前 | 修复后 | lint 判定 |
|---|---|---|---|---|
| `cjk_in_english_page_total` | 782 | 1283 | **0** | 无回归 |
| `punct_nospace_visible_total` | 53 | 67 | **15** | 无回归 |
| `lang_mismatch` | 1 | 1 | **0** | 无回归 |
| `comma_splice_visible_total` | 9 | 9 | **8** | 无回归 |

`python scripts/lint_frontend_i18n.py` → **exit 0**（此前 exit 1、2 项回归）。

**修法的关键选择：只在渲染层翻译，不动 engine 数据。**
`engine` 的 `domain` / `published` 是中文内部口径，同时进 `trust_surface.json`、
报告 md 与账本；在那里改成英文会连带改掉中文交付物。因此英文解析收敛到
`scripts/paper_validation_loop.py::_en()` 一个函数，渲染站点各自调用
（卡片头 / 排名表 / 可信面表格）。英文文本全部取自既有单一真源
`scripts/v3_i18n_map.py`，**没有手写第二套译文**。

**上一轮"差一截"的算术补上了**：§8.3 记录「DOMAIN_MAP 只能消 369，离 ≥501 差一截」。
差额来自 44 个兜底 domain（`微生物 / <key>`）——`DOMAIN_MAP` 只有 52 条中文短标签、
没有 key 形式。本轮把这条链接上：`gen_v3_micro.DOMAIN`（key→中文短标签）**静态 AST 解析**
后经 `DOMAIN_MAP` 取值，得到 `微生物 / <key>` 的英文（44 条）。
实测覆盖率 **52/52**（8 个走 engine 中文描述、44 个走 key 映射），
无需新写任何翻译；`_EN_TEXT` 总条目 = 63(DOMAIN_MAP) + 5(SCIENCE_PUBLISHED_MAP) + 44 = 112。

为什么用 `ast` 静态解析而不是 `import gen_v3_micro`：该脚本在 import 期会重写
`frontend_fixed/functions/api/[[path]].js`，把它当依赖库导入会带来写文件的副作用。

**新增的构建期自检**：`build_dashboard()` 写完 HTML 后调用 `_warn_untranslated()`，
页面仍有中文即打 stderr 警告并列出前 8 段。刻意**只警告不抛异常**——真门禁是 lint
（基线式 + 否定性自检），这里只让漏译在构建日志里当场可见。

### 9.2 顺带发现并修复的 3 个真实缺陷

#### (a) i18n 门禁的 `--json` 模式恒返回 0（假绿）

`lint_frontend_i18n.py` 的 `--json` 分支在比对基线**之前**就 `return 0`，
于是文本模式 exit 1、JSON 模式 exit 0 —— 同一份代码给出两个相反结论。
**判据分叉本身就是假绿**：任何消费 JSON 的一方（脚本、agent、看板）都会读到"通过"。
修复：抽出 `compare_to_baseline()` / `exit_code_for()` 供两条路径共用，
JSON 输出新增 `regressions` / `improvements` / `ok` 字段，`--json` 在回归时返回 1。
`--selftest` 从 8 项扩到 **11 项**，新增 `json-mode-not-false-green` 等 3 项方向性断言
（自检第一次跑就抓到作者自己的一个断言写错，说明这个 harness 确实在拦）。

#### (b) 22 处中文引号被当成 HTML 属性分隔符，线上 14 个元素样式失效

`scripts/paper_validation_loop.py` 里存在 `class=“sc“`、`style=“background:...“`
这类写法（U+201C 当引号）。按 HTML5 规范，U+201C 不是合法属性引号，
它会被并入**未加引号的属性值**：`class=“sc“` 得到的类名是 `“sc“`，
与 CSS 选择器 `.sc` 不匹配 —— 亦即这些元素的样式**从来没生效过**。

受影响并已随本轮部署生效：旗舰徽章、成熟度徽章（9 个）、PINN / DeepONet 卡片、
UCI 实测数据面板、博客入口卡。共 **22 处属性**，线上页面 `=“` 计数 **14 → 0**。
（这类缺陷不会被任何现有门禁抓到——HTML 仍是"合法"的，只是样式静默丢失。）

#### (c) 5 处跨行字符串隐式拼接缺空格

`punct_nospace` 从 67 降到 15 的全部来源（该页 52 处 → 0）：

| 位置 | 命中片段 | 次数 | 修法 |
|---|---|---|---|
| `suggest_next_experiments` 收敛分支 | `Anchor;New` | 38 | 加 `"; "` |
| 欠拟合分支 | `points.Current` | 7 | 加 `" Current"` |
| 临界收敛分支 | `line.Recommend` | 5 | 加 `" Recommend"` |
| 覆盖率偏低分支 | `control,Recommend` | 1 | 加 `", Recommend"` |
| UCI 面板说明 | `data;Demonstrating` | 1 | 加 `"; "` |

剩余 15 处分布在三个**静态**页面（`lab.html` 4、`ai-research-uncertainty.html` 6、
`physics-ai-uq-credibility.html` 5），是独立的老债，不在本轮范围。

### 9.3 基线收紧（说明为什么这不是"改基线消红"）

`data/frontend_i18n_baseline.json` 的 4 项指标被**下调**（其余 9 项逐字未动），
并在文件里写入说明。理由：
- 上调基线是禁止行为（§8.3 已写明），本轮没有上调任何一项；
- 但把一个已经降到 0 的指标留在 782，等于给"回到 782"留一条静默回退通道
  —— 门禁会判"无回归"。这种"基线滞后"本身就是假绿的一种，与本项目一贯纪律冲突；
- 收紧的代价为零：`cjk=0` 意味着**任何**中文回灌都会被抓住，而此前要涨到 783 才报警。

### 9.4 再次更正初稿 §2.2 的两条

#### 更正 4：「前端与后端未打通 · lab.html 无真实 API 调用、数据硬编码」——不成立

按页统计 `fetch(` 调用（`grep -c`，可复算）：

| 页面 | `fetch(` | `lang` | 实际状态 |
|---|---|---|---|
| `lab.html` | **19** | zh-CN | 真实调用 API；中文**已声明**，不属 i18n 缺陷 |
| `pricing.html` | 7 | en | 接支付/套餐 |
| `login.html` | 3 | en | 接 `/api/v2/auth/*` |
| `failure-cases.html` | 3 | en | 接闸门数据 |
| `workbench.html` | 2 | en | 已接线 |
| `api-reference.html` | 2 | en | 可试调 |
| `verify.html` | 1 | en | 接闸门 |
| `report-gallery.html` | 1 | en | 读 `index.json` |
| `index.html` / `trust-dashboard.html` | 0 | — | 静态/生成页，设计如此 |

**更正后的结论**：「没有统一 API client 层」这个判断成立（各页各自 fetch），
但「lab.html 数据硬编码、无真实调用」与实测不符。§4.2.1 应从「重建前端」
降级为「抽一个薄 client 层」的重构项，优先级低于 §4.2.1 原文假设。

#### 更正 5：「用户系统缺失：无注册/登录（仅有 login.html 占位）」——不成立

线上探测（`GET`，可复算）：

| 端点 | 实测 | 解读 |
|---|---|---|
| `/api/v2/auth/login` | **401** | 存在，需凭据 |
| `/api/v2/auth/register` | **400** `{"error":"invalid_email","message":"请提供有效邮箱"}` | 存在，且在做真实字段校验 |
| `/api/v1/register` | **401** | 存在 |
| `/api/v2/credits` / `/api/v2/user/me` | 404 | 确实不存在 |

`frontend_fixed/functions/api/[[path]].js` 内有 KV 支撑的用户存储
（`user:<email>`、`token:<token>`，TTL 365 天 / 7 天）与审计日志。
**更正后的结论**：注册/登录**已实现且有持久化**；「用户系统」的真实缺口收窄为
**配额/用量视图**（`/api/v2/credits`、`/api/v2/user/me` 不存在）与
**API key 自助发放**——后者仍被 license 子域阻断，与 §8.1 更正 1 是同一根因。

**顺带发现的 i18n 残留（新，未修）**：`/api/v2/auth/register` 的错误文案仍是中文
（`请提供有效邮箱`）。`scripts/v3_i18n_map.py::MSG_MAP`（199 条）覆盖的是 v3 路由，
v2 认证路由漏了。这属于"公开 API 面向英文用户但报错为中文"的债，
建议与 §4.2.1 的 client 层一起排期。

### 9.5 本轮门禁与线上复验实测

| 检查 | 结果 |
|---|---|
| `lint_frontend_i18n.py` | **exit 0**（4 项指标低于基线） |
| `lint_frontend_i18n.py --selftest` | **11/11** |
| `lint_frontend_i18n.py --json` | exit 0（修复后不再恒 0） |
| `check_public_numbers.py` | **exit 0**（0 MISMATCH / 0 UNSOURCED / 0 STALE-FACT / 0 COUNT-DISAGREEMENT） |
| `check_engine_gate.py` | 52/52 场景 PASS，最差 R²=0.954 / cov=0.917（阈值一字未改） |
| `check_ledger_fresh.py` | exit 0（`all_digest=348d9afc26a673e0`） |
| `check_vlab_gates.py` | **38/38** |
| `check_vlab_server.py` | **30/30** |
| `build_vv_package.py build / selfcheck` | 82 场景 / 87 文件 sha256 / STDLIB_ONLY_OK |
| `deploy_pages.py` | 424/424 资产逐字节一致；部署后复验 **15/15** |
| 线上 `GET /v3/gate` | `total=82` / 79-2-1-0 / `blocked=[mol_suzuki_yield]` / `build.complete=true` |
| 线上 `GET /blog/validation-dashboard` | 300,489 B；**CJK=0**；`=“` 属性 **0** |

**顺带更正 §8.1 更正 2 的一个数字**：当时记「归档包 333 文件」。本轮重建后实测
`dist/vvpkg` = **89 个文件**（manifest 内 `integrity.n_files` = 87，
即 82 个 `scenarios/*.json` + `evidence/*.json` ×2 + `policy.json` + `standard.json`
+ `README.md` + `CHECKS.txt` + `manifest.json`，其余 2 个不参与 sha256 清单）。
「333」应为更早一次构建的数字，结论（包是完整的、`manifest.json` 承担索引职能）不变。

**复查人**：Agnes（Sapiens AI）
**复查时间**：2026-09-23 16:15 GMT+8
**版本**：v1.2（第二轮修复）
