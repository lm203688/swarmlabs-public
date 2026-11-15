# 三个总闸门处置报告（2026-09-17）

> 口径：**能做的技术动作已直接做掉；做不到的给逐步操作 + 链接。**
> 所有结论均为一手实测（DNS / HTTP / API / CI），不引记忆、不引旧文档。

## 0. 结论速览

| 闸门 | 技术侧我做了什么 | 还剩什么 | 谁做 |
|---|---|---|---|
| **bus factor = 1** | 治理/连续性/安全/贡献四件套 + CODEOWNERS + CI + 免责披露；把「失败模式不可收拾」改成「失败模式可恢复」 | 真正把它从 1 变成 N | **用户**（招第二位维护者） |
| **GH stars = 0** | 两个仓库元数据、topics（含 MCP 自动索引 topic）、CI badge、`v0.2.0` release、把 gate 做成三种可分发形态 | 无法制造 star；需要**被第三方提及** | **用户**（提交/发帖，链接已给） |
| **pre-revenue** | 实测探明支付后端**是活的**，并**纠正了一处错误结论**（`license.` 子域不存在，价格也与记忆不符） | 端到端下单需登录态；定价冲突需拍板 | **用户**（登录+支付实测） |

---

## 1. bus factor = 1

### 1.1 已落地的技术动作

| 文件 | 作用 |
|---|---|
| `engine-kit/GOVERNANCE.md` | **明文写出 bus factor = 1、无公司实体、无 SLA、pre-revenue**，并给出连续性计划 |
| `engine-kit/SECURITY.md` | 私密上报通道、范围、以及本项目最在意的风险类别（**任何把 gate 判定放松的输入**） |
| `engine-kit/CONTRIBUTING.md` | 本地检查清单 + 两条硬不变量 + Skill 目录规范 |
| `engine-kit/CODEOWNERS` | 把两条硬不变量（stdlib-only、fail-closed）写在**它们约束的文件旁边** |
| `engine-kit/CHANGELOG.md` + `CITATION.cff` | 版本可追溯 + 可被引用 |
| `.github/ISSUE_TEMPLATE/*` + `pull_request_template.md` | 贡献者无需先问就能贡献 |
| `.github/workflows/ci.yml` | 4 个 job：语法/JSON、**stdlib-only 机械门禁**、实时冒烟、MCP 协议冒烟 |

### 1.2 核心判断：bus factor = 1 降低不了，但**伤害可以**

一味说「多招人」是空话。对 MIT + 零依赖的项目，真正的缓解是让**单点故障不strand数据**：

1. **代码可 fork**：MIT，无 CLA，无版权转让，`skills/vv-gate/` 零第三方依赖。任何人在任何机器上 vendor 一份即可，**不可被撤回**。
2. **产物是静态 + 摘要锚定的**：`gate_ledger.json` / `wet_lab_anchors.json` / `benchmark/*.json` 都是纯文件，且带 `engine.all_digest` / `oracle_digest`。你**已经抓到手的那一份**永远可复核。
3. **裁决器可重实现**：`POST /v3/verify` 是 (你的预测, 已发布 y, 已发布阈值) 上的纯算术，规格写在 `gate-semantics.md` §2/§6.2，**约 60 行任何语言可复现**。

这三点合起来 = 「服务挂了，你的数据不作废，裁决能力可重建」。这是我在这个规模下能给出的**诚实**答案。

### 1.3 只有用户能做的（附链接）

- [ ] **招第二位维护者**：把仓库转到一个 org 名下（至少让账号不是单点）
      → <https://github.com/lm203688/swarmlabs-engine-kit/settings>
- [ ] 在仓库开 `good first issue` 标签的入门任务，降低参与门槛
      → <https://github.com/lm203688/swarmlabs-engine-kit/issues/new>
- [ ] （可选）在 README 顶部加一行「maintainer wanted / co-maintainer welcome」——**这是最便宜的真实解法**

---

## 2. GH stars = 0

### 2.1 已落地的技术动作（全部实测）

| 动作 | 仓库 | 实测结果 |
|---|---|---|
| 重写 description，**以 gate 为卖点** | `swarmlabs-engine-kit` | 200 |
| 补齐 topics **15 个**（含 `mcp-server` / `agent-skills`，这两个是 MCP 目录自动索引的触发词） | `swarmlabs-engine-kit` | 200 |
| 补齐 topics **12 个** + description + homepage | `swarmlabs-benchmarks`（原 topics = **0**） | 200 |
| 发布 release `v0.2.0` | `swarmlabs-engine-kit` | <https://github.com/lm203688/swarmlabs-engine-kit/releases/tag/v0.2.0> |
| CI badge（**真实**，非装饰） | `swarmlabs-engine-kit` | Actions run **#21 success** |
| 可分发形态 ×3 | engine-kit | CLI skill / 零依赖 MCP server / 公开 HTTP 端点 + OpenAPI |

### 2.2 三个可分发形态（这是"被提及"的前提）

| 形态 | 怎么用 | 依赖 |
|---|---|---|
| `skills/vv-gate/scripts/vv_gate.py` | `python vv_gate.py verify <key> --pred p.json` → **退出码 2 = BLOCK**，直接挂 CI | **零** |
| `vv_gate_server.py` | 一行 MCP host 配置，6 个工具含 `verify_prediction` | **零**（手写 JSON-RPC，不装 `mcp` SDK） |
| `POST /v3/verify` + `GET /v3/openapi` | 不装任何东西即可验；框架可直接 ingest 契约 | **零** |

### 2.3 诚实说清：stars 我造不出来

`0 → 1` 的机制是**外部提及**，不是仓库内部完善。我能做的是把「被提及时值得点」的条件备齐（可运行、可验证、有 CI、有 release、有清晰定位），**不能**替第三方开口。

### 2.4 需要用户提交的（按 ROI 排序，附链接与所需材料）

| # | 渠道 | 链接 | 需要准备 | 类型 |
|---|---|---|---|---|
| 1 | **Glama MCP 目录** | <https://glama.ai/mcp/servers> | 仓库已带 `mcp-server` topic，通常可自动索引；否则手动加 | 免费 |
| 2 | **Smithery** | <https://smithery.ai> | repo URL + 一句话；按 GitHub 自动抓取 | 免费 |
| 3 | **PulseMCP** | <https://www.pulsemcp.com/submit> | repo URL + 分类 + 描述 | 免费 |
| 4 | **mcp.so** | <https://mcp.so/submit> | 同上 | 免费 |
| 5 | **awesome-mcp-servers（PR）** | <https://github.com/punkpeye/awesome-mcp-servers> | 按 README 格式加一行，提 PR | 免费，需 GitHub 账号 |
| 6 | **modelcontextprotocol/servers（community 段）** | <https://github.com/modelcontextprotocol/servers> | 同上 | 免费，需 PR |
| 7 | **Show HN** | <https://news.ycombinator.com/showhn.html> | 标题写成「Adjudicate, don't just reproduce」这类主张，正文给 30 秒可复现命令 | 免费 |
| 8 | **r/LLMDevs / r/MachineLearning** | <https://www.reddit.com/r/LLMDevs/> | 同上，**不要**当广告发，写成「我做了个能对 agent 说 no 的闸门，附 44× 反例」 | 免费 |
| 9 | **Product Hunt** | <https://www.producthunt.com/posts/new> | 截图 + tagline | 免费 |

**投放建议**：素材直接用**双链分歧**这个反例（`microbio_monod`：V&V 链 PASS / 锚点链 BLOCK，44×）——这是最不可替代的传播点，因为它展示的是「我们公开了自己的一半错误」。**不要**用「62/62 PASS」当标题，那看起来像又一个自夸的 demo。

> ⚠️ 诚实提醒：以上 9 条全部需要**用户本人**的账号操作（我不能代持账号）。其中 5、6 是 PR，我可以帮你把 PR 正文和那一行条目写好——说一声即可。

---

## 3. pre-revenue

### 3.1 本轮实测结论（**纠正了两处错误认知**）

| 探测 | 实测结果 |
|---|---|
| `license.swarmlabs.tools` | **DNS 不存在**（`Non-existent domain`）——旧文档称「已就绪」是**错的** |
| `GET /api/v2/payment/plans` | **200**，真实返回 4 档 + 4 积分包 + 24 项计费单价 |
| `POST /api/v2/payment/checkout` | **401** `请先登录后再购买`（端点存在） |
| `POST /api/v2/payment/xunhu/checkout` | **401**（虎皮椒通道已接） |
| `GET /api/v2/account/balance` | **401** `请先登录` |
| `frontend_fixed/pricing.html` | 客户端有完整流程：下单 → 弹窗 → **每 3 秒轮询** → 手动复查 |

**线上实际价格（以线上为准）**：

| 档 | `price_monthly` | 折算 |
|---|---|---|
| free | 0 | ¥0（50 分/日，单日 5 引擎上限） |
| pro | 19900 | **¥199/月**（2000 分） |
| team | 59900 | ¥599/月（不限分） |
| enterprise | 99900 | ¥999（`price_type: lifetime`） |

积分包：100 分 ¥19 · 500 分 ¥79 · 2000 分 ¥299 · 5000 分 ¥699。
单次调用最贵 `pop_pk` 40 分，`pinn` 50 分。

> **必须纠正的记忆**：长期记忆与旧文档写的是「Pro ¥39.9 / 终身 ¥199」。
> **线上没有这两档**。任何对外材料、投资页、定价页若沿用 ¥39.9，即构成对外的错误报价。
> 唯一权威来源：<https://swarmlabs.tools/api/v2/payment/plans>

### 3.2 已做 / 未做

- ✅ 已在 `docs/pricing/premium-tiers.md` 写入本轮的**事实校正块**（含探测表 + 线上真实价格）
- ✅ 支付后端、虎皮椒通道、客户端轮询流程：**确认存在**
- ❌ **端到端下单未验证**：未登录只能验到 401。**因此目前不能声称"支付可用"**，只能说"支付端点存在"

### 3.3 只有用户能做的（按顺序）

- [ ] **1. 登录后跑一次真实小额订单**，走完 下单 → 收银台 → 回调 → 权益发放
      → <https://swarmlabs.tools/pricing>（需登录账号；若缺测试账号，先注册一个）
- [ ] **2. 确认虎皮椒商户后台的回调地址与密钥已配**
      → 虎皮椒商户后台 <https://admin.xunhupay.com>（需你的账号）
- [ ] **3. 定价拍板（冲突待决）**：线上已有 `team ¥599/月`，而 `premium-tiers.md` 建议 `Team ¥4999/年`。
      二者不可能同时成立——**改线上配置**还是**改文档**，请你定
- [ ] **4. 决定 license 挂路径还是子域**：现状在主域路径；若要 `license.` 子域，需先在 CF 建 DNS 记录
      → <https://dash.cloudflare.com> → swarmlabs.tools → DNS

### 3.4 获客渠道（技术侧已备好承接物）

gate 免费公开是**漏斗**，license 解锁引擎 API（不是解锁报告）。承接物已就位：

| 资产 | 路径 |
|---|---|
| 报告库入口（免费公开，GEO 漏斗） | `frontend_fixed/report-gallery.html` |
| 英文 GEO 长文（诚实失败率） | `frontend_fixed/blog/honest-failure-rate.html` |
| 课题申报验证层话术 | `validation-dashboard.html` 的 `.grant` 区块 |
| 定价页 | `frontend_fixed/pricing.html` |

---

## 4. 本轮附带修复的真实缺陷（非计划内）

| 缺陷 | 根因 | 状态 |
|---|---|---|
| `/v3/anchors/{scenario_key}` **永远 404** | `wet_lab_anchors.json::scenarios` 是**数组**，Worker 用 `[k]` 下标索引 → 永不命中 | 已修（统一 `pickByKey` 同时吃数组/字典）+ 404 回传 `available_keys` |
| `/v3/anchors/{key}` **缺 `vv_gate` 那一半** | 文档声称双链并列，实际只返回湿实验链 | 已修（从门禁账本 join，新增 `chains_agree` / `dual_chain_note`） |
| `/v3/openapi` **不存在** | deploy 脚本一直在检查一个从未建立的端点 | 已建（OpenAPI 3.1，11 条路径） |
| `python -m mcp.server` **加载到 PyPI 的 `mcp` SDK** | 仓库内 `mcp/` 目录与 MCP SDK 同名遮蔽 | 已修（提到顶层 `mcp_server.py`） |
| `examples/quickstart.py` / `mcp_server.py` **未装包即崩** | 无 `src/` 回退 | 已修 |
| deploy 复验**把传播延迟误报为 FAIL** | `get()` 对 404 立即返回不重试 | 已修（用 `/v3/openapi` 当部署标记做传播等待，实测第 2 次探测即就绪） |
| `gh_push.py` **只能推项目根** | root 硬为项目根（历史靠手写 Contents API 绕开） | 已修（新增 `--strip-prefix` + `--dry-run`） |
| CI 一次限流 → **整个 job 红** | 传输层不重试瞬时故障 | 已修（`429/5xx` 退避重试，`400/404/405` 立即返回） |

---

## 5. 事实核对表（本轮所有对外数字的唯一来源）

| 数字 | 值 | 复现命令 |
|---|---|---|
| 场景数 / 测试点数 | 62 / 3110 | `python vv_gate.py selftest` |
| 账本判定 | PASS 62 / MARG 0 / REFUTED 0 / ERROR 0 | `GET /v3/gate` |
| **`n_blocked`** | **0**（**不要**引用非零值） | 同上 |
| 锚点链 | 3 AGREE / 3 NEAR / 2 CONTRADICTED / 2 UNANCHORED | `GET /v3/anchors` |
| 引擎指纹 | `all_digest` 51 模块 / `core_digest` 7 模块 / commit `1de10d63` | `GET /v3/gate` → `provenance` |
| 双链分歧实例 | `microbio_monod`：V&V `PROCEED`(r²=0.9997) vs 锚点 `BLOCK`(Ks 偏离 44×) | `GET /v3/anchors/microbio_monod` |
| 线上定价 | pro ¥199/月 · team ¥599/月 · enterprise ¥999 终身 | `GET /api/v2/payment/plans` |
