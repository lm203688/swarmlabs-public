# SwarmLabs 完整性复核 + 开源技术扫描 + 提升机会（2026-09-24）

> 方法：线上探测 + 干净重建 + 产物对账（不采信自述）。所有结论附可复算判据。
> 配套动作：本轮已修复线上 `/v3/gate` 被 rdkit 缺失重建截断的事故（82/79-2-1-0 已恢复并部署+推送）。

---

## 0. 调整后的核心目标口径（评审基线）

- **战略定位**：AI4S **可信验证层**——替代（replace）真实实验，而非 augment；诚实 UQ + 10 章 V&V 是核心差异点，不与智谱正面竞争。
- **不可让步的红线**：UQ 一等公民（噪声地板 3% 不降、覆盖率≈0.95）；引擎 `engine/` 唯一真源；真实数据仅作 parity 验收且须在虚拟实验独立成立之后。
- **诚实差异点**：敢展示 REFUTED（当前 `mol_suzuki_yield`）、敢展示 CONTRADICTED（wet_lab vs vv_gate 公开分歧）、口径严格（82 场景、dropped 不得静默消失）。
- **动作准入唯一判据**：「这个动作是否让虚拟实验在『无真实实验、无网络、无设备』下更可替代？」

---

## 1. 完整性复核（线上实测）

### 1.1 ⚠️ 紧急事故（本轮已修）

**现象**：线上 `GET /v3/gate` 返回 `total=80 / counts{78,2,0,0} / build.complete=false / dropped=[mol_suzuki_yield, mol_esterification_yield]`。
**真因**：一次**无 rdkit** 的解释器跑了 `build_reports.py`，把两个 molecular 场景（含唯一 REFUTED）静默丢出注册表。工作树里暂存区本是 82（正确），但工作树被覆盖成 80，且从未部署——于是线上+工作树双重撒谎「零失败」。
**已执行修复**（固定链路，rdkit 解释器 `envs/default`）：
`build_reports.py` → `paper_validation_loop.py --dashboard` → `patch_trust_surface_honesty.py` → `build_gate_ledger.py` → `build_vv_package.py` → 修正 `index.html` 校准点 168→169 → 门禁全绿 → `deploy_pages.py`（424/424 + 15/15 复验）→ Git Data API 推送 26 文件（commit `ff0046a8`）。
**复验**：线上 `gate` 现 `82 / 79-2-1-0 / complete=true / dropped=[]`。

> **这是第二次发生同一事故**（首次 09-23 中午）。根因不是代码而是**构建纪律未固化**：CI 无法强制（fine-grained PAT 缺 Workflows:write），本地/夜行重建可能用错解释器。见 §3-P0-1。

### 1.2 健康项（线上实测）

| 维度 | 实测 | 判据 |
|---|---|---|
| `/v3/gate` | **82 / 79-2-1-0 / complete=true** | 本轮修复后 |
| `license.swarmlabs.tools/health` | `version 2.0` | 路由修复后稳定 |
| P1 用户系统 | `/v2/user/me`·`/v2/credits`·`/v2/account/platform-apikey` 全部 401/200 | 已部署 |
| i18n 门禁 | `lint_frontend_i18n.py` exit 0 | 基线式 + 否定性自检 |
| VV 归档包 | 82 场景 / 87 文件 sha256 / `STDLIB_ONLY_OK` | `build_vv_package.py selfcheck` |
| 公开数字门禁 | exit 0（0/0/0/0/0） | 本轮刷新事实表后 |
| 实体搜索 API | `/api/entities/search` 带 key 返回 200 | 早已存在，仅缺密钥发放 |

### 1.3 仍存在的缺口（真实，非自述）

| # | 缺口 | 严重度 | 阻塞方 | 判据 |
|---|---|---|---|---|
| G1 | **82→80 截断复发风险**（构建环境未锁定） | 🔴 高 | 纪律/CI | 本轮第二次发生 |
| G2 | KV 配额根治未决（注册仍北京 ~08:00 重置前受限） | 🔴 高 | 用户拍板（Workers Paid $5/月 vs 现状） | P0-1 fail-loud 已部署但仍受配额钳制 |
| G3 | 四套密钥未打通（slb_/gtk_/GUX_/静态名单） | 🟡 中 | 用户拍板 | `/v3` x-api-key 静态名单与 gtk_ 兑换体系正交 |
| G4 | 测试覆盖低（engine 73 个 py、仅 12 个 test 文件） | 🟡 中 | 可自主 | `check_vlab_gates` 是门禁非单测 |
| G5 | `trust-dashboard.html` 仍读本地 JSON，非实时 | 🟡 中 | 可自主 | fetch 计数 0（设计如此但违背实时化 P2） |
| G6 | v2 认证路由报错文案仍为中文 | 🟢 低 | 可自主 | `/api/v2/auth/register` 返「请提供有效邮箱」 |
| G7 | 前端统一 API client 层缺失（各页各自 fetch） | 🟢 低 | 可自主 | 19/7/3/… 分散 |

---

## 2. 开源技术扫描（2026-09-24 实查）

> 检索锚点：AI4S surrogate+UQ、多智能体编排、MCP/A2A 生态、agent 评测框架、科学 ML/知识图谱/可复现性。下列均为 2026 仍在活跃维护或当年新发。

### 2.1 Surrogate + UQ（最贴合核心差异点）

| 开源/文献 | 机构 | 可借鉴点 | 与 SwarmLabs 关系 |
|---|---|---|---|
| **DeepSurrogate** (arXiv:2503.20528, 2026-06) | Texas A&M / LANL | 可解释函数型 DNN surrogate + **Monte Carlo dropout UQ**，5 万空间点 20 次仿真 <10min | 我方 surrogate 是解析/参数化；可补 NN surrogate 的 epistemic UQ 分支 |
| **TRIE**（Stochastic PDE Surrogate Eval, arXiv:2607.00196, 2026-07） | SCAILab | 用 **CRPS + 不变测度** 评随机 PDE surrogate 的校准与长期统计保真 | 强化我方 UQ rubric 的「校准/长期统计」诊断 |
| **Conformal Prediction**（Thorium-U233 硕士论文, 2026-02, SciPost） | IIT Jammu | **分布无关有限样本覆盖保证**（90% marginal coverage）+ OOD 否决机制 | 与我方「诚实 UQ、拒绝超域外推」哲学**完全一致**，是最该采纳的增强 |
| **GPyTorch / GPJax / tinygp** | Cornell / JAX 社区 (MIT/Apache) | GPU 可扩展 GP 仿真器、稀疏变分推断 | 为我方黑箱仿真器提供 GP emulator 选项 |

### 2.2 多智能体编排

| 框架 | 状态 | 取舍 | 对我方的意义 |
|---|---|---|---|
| **LangGraph** | 生产级（checkpoint/HITL/replay/审计） | 显式状态机，调试最佳 | 我方 `vlab_orchestrator` 是纯 stdlib + fail-closed，**更严格但无可观测性** |
| CrewAI | 快速组队 | 角色/任务隐喻 | 适合上游「科学发现 crew」而非验证闸门 |
| AutoGen | **维护模式** | 不建议新项目 | 规避 |
| **OpenTelemetry GenAI 语义约定** | CNCF/OSS | agent/tool/MCP span 标准 | **缺口**：我方无 trace/可观测层，应采纳 OTel GenAI 约定做 agent 轨迹审计 |

> 我方 fail-closed + 否定性测试（22/22）在「诚实/防失控」上**强于**通用编排框架的 happy-path；短板是**可观测性**与**持久化回放**。

### 2.3 MCP / A2A 生态（我方已部分领先）

- **MCP 已成事实标准**：2026-07-28 修订为**全无状态核心**（去掉 session/握手），天生契合 CF Workers 无状态边缘部署；12,000+ 服务器、TypeScript+Python SDK 累计下载超 10 亿。
- **A2A 1.0 GA**（2026-04，Google/AWS/MS/SAP 等 150+ 组织）：agent-to-agent 协作标准，与 MCP（agent-to-tool）构成两层栈。
- **我方现状**：已发布 `engine-kit` MCP server（零依赖 stdio）+ `.well-known/agent-card.json`（A2A 0.3.0）+ SEP-2640 manifest，并自审 0 findings。
- **可提升**：① MCP server 补 **HTTP+SSE 无状态传输**（对齐 2026-07 修订 + 边缘部署）；② agent-card 升到 **A2A 1.0** 并深化 delegation。

### 2.4 Agent 评测 / 验证框架

| 框架 | 许可证 | 可借鉴 |
|---|---|---|
| **UK AISI Inspect AI** | MIT | 参考 agent-eval 标准：`Task = Dataset + Solver + Scorer`，沙箱工具，**校准 metric** |
| **HELM**（Stanford CRFM） | Apache-2.0 | **多指标 scorecard**（accuracy+calibration+robustness+fairness+toxicity+efficiency）——与我方 6 轴评分同构 |
| **lm-evaluation-harness** (EleutherAI) | MIT | 静态基准复现、decontamination、YAML 任务 |
| **Promptfoo / Langfuse** | MIT | 红队扫描 + OTel 轨迹；Langfuse 近零成本接入 |

> 我方 `verification_gate`（6 轴）+ `check_vlab_gates`（否定性 38 条）已具 Inspect/HELM 的思想雏形。最该补的是 **calibration（校准）指标**与**红队/对抗性验证**维度。

### 2.5 科学 ML / 知识图谱 / 可复现性

- **OpenAlex + Semantic Scholar API**：我方 entity_store 已用 OpenAlex；可加 **Semantic Scholar 引文图**做「研究空白/桥接」检测增强。
- **NetworkX / igraph**：合著网络分析（我方已有该维度），可升到社区发现 + 中心性。
- **rdflib（RDF）**：把实体库导出为可查询知识图谱，利于 agent 消费。
- **可复现存证**：**Software Heritage** 存档 + **DataCite DOI** 注册 → 给我方 VV 包/场景库加上**不可变溯源**，直接强化「可信」主张（当前仅 sha256 manifest）。

---

## 3. 提升机会（按「核心目标契合度 × 可行性」排序）

### P0 — 护住核心差异点（防止再撒谎）

| 项 | 动作 | 可行性 | 备注 |
|---|---|---|---|
| **P0-1 固化 82 口径** | 构建环境锁定 rdkit；CI 跑 `check_public_numbers` 的 `dropped≠[]` 硬失败 | 需用户授予 **Workflows:write**（当前 PAT 缺，是硬阻塞） | 本轮第二次踩；不固化会反复打脸诚实主张 |
| **P0-2 KV 配额根治** | Workers Paid $5/月（1M 写/天）或 fail-loud+重试 | 用户拍板花钱 | 解除注册丢用户 |

### P1 — 产品化必需

| 项 | 动作 | 可行性 |
|---|---|---|
| P1-1 四套密钥统一 | gtk_/GUX_/slb_/静态名单 → 单一兑换体系，接 `/v3` x-api-key | 用户拍板接哪套 |
| P1-2 引擎单测 | 给 `verification_gate`/`surrogate`/`wet_lab_anchors` 加单测（至少边界/负值） | 可自主，零成本 |
| P1-3 v2 报错英文化 | `MSG_MAP` 扩到 v2 认证路由 | 可自主，零成本 |
| P1-4 校准指标 | 在 UQ rubric 加 calibration/CRPS（借 TRIE/HELM） | 可自主，强化诚实 UQ |

### P2 — 体验与生态（非零成本或需设计）

| 项 | 动作 |
|---|---|
| P2-1 trust-dashboard 实时化 | 接 `/v3/gate`，5min 轮询，替代读本地 JSON |
| P2-2 可观测性 | 采纳 OpenTelemetry GenAI 语义约定做 agent 轨迹 |
| P2-3 MCP HTTP 传输 | 补无状态 HTTP+SSE，对齐 2026-07 修订 + CF Workers |
| P2-4 A2A 1.0 深化 | agent-card 升级 + delegation |
| P2-5 可复现存证 | VV 包 mint DOI + Software Heritage 存档 |
| P2-6 Conformal Prediction | 为 NN surrogate 分支加分布无关覆盖保证 |

---

## 4. 建议下一步（待用户拍板/授权）

1. **立即（AI 可做，无需花钱）**：P1-2（引擎单测）、P1-3（v2 报错英文化）、P1-4（校准指标）、P2-1（dashboard 实时化）——这些不触碰花钱/品牌/账号硬阻塞，可直接排期执行。
2. **需用户授权**：P0-1 的 Workflows:write（根治复发）、P0-2 的 Workers Paid $5/月、P1-1 的密钥体系选型。
3. **战略级（观察触发）**：P2-3/P2-4 的 MCP/A2A 升级可等 AIsa 生态开放或海外占比破 10% 再启动（与既有 x402 路线 C 一致）。

**一句话结论**：核心可替代性底座已稳（82 场景诚实 V&V + 101K 实体 + MCP 分发），当前最大风险不是技术而是**纪律固化（82 口径别再被截断）**与**配额/密钥的最后一公里**；开源侧最该采纳的是 **Conformal Prediction（诚实 UQ）**、**HELM 式校准指标**、**OTel 可观测性**三项，全部零成本且与核心差异点同构。
