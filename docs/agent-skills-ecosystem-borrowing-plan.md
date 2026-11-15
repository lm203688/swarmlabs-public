# Agent Skills 生态 8 仓库借鉴方案（SwarmLabs 提升分析）

> 调研日期：2026-09-02 ｜ 数据：GitHub API 实时拉取（stars/forks/license/updated）+ README 一手提取
> 姊妹篇：`docs/aminer-deepdive-borrowing-plan.md`（AMiner DeepDive 借鉴）

## 一、8 个仓库实测数据

| 仓库 | Stars | 规模 | 定位 | 许可 | 更新 |
|---|---|---|---|---|---|
| `Imbad0202/academic-research-skills` | **45,191** | 4 核心 skill | research → write → review → revise → finalize（human-in-the-loop） | NOASSERTION | 09-01 |
| `K-Dense-AI/scientific-agent-skills` | **41,822** | 165 skills | 科学代理技能库（bio/chem/med） | MIT | 08-31 |
| `anthropics/knowledge-work-plugins` | **23,817** | 插件集 | 知识工作者插件市场（Claude Cowork） | Apache-2.0 | 09-02 |
| `wanshuiyin/Auto-claude-code-research-in-sleep` (ARIS) | **15,610** | 82 skills | 自主 ML 研究：跨模型评审 / idea 发现 / 实验自动化 | MIT | 08-26 |
| `Orchestra-Research/AI-Research-SKILLs` | **12,243** | 98 skills / 23 类 | AI 研究与工程（训练/评测/推理/MLOps） | MIT | 06-16 |
| `Master-cai/Research-Paper-Writing-Skills` | **6,475** | — | ML/CV/NLP 论文写作 | MIT | 06-23 |
| `brycewang-stanford/Auto-Empirical-Research-Skills` (AERS) | **3,646** | 23,000+（76 核心） | 社科实证研究，8 学科 | NOASSERTION | 09-01 |
| `aipoch/medical-research-skills` | **1,796** | 数百 | 医学研究全流程（按研究阶段组织） | MIT | 07-15 |

合计 ≈ **15 万 stars**。这个生态已经不是"趋势"，是既定事实。

## 二、三个关键发现

### 发现 1：「验证 / 信任」的命名已被抢占 —— 但内涵不同（已一手核实）

| 项目 | 说法 | 实质 |
|---|---|---|
| SwarmLabs | 可信面 / trust_surface | **18 个基准 + UQ 覆盖率 + OOD 红区**（数学支撑） |
| AERS | 信任面 · Trust surface (rigor stats) | **19 个数值基准（每次跑重算 gold 值）+ 42 评测场景 / 217 条 rubric**（混合，rubric 为主） |
| Imbad0202 | verifiability & feasibility track | **ISO/IEC 42001 精神的审计轨迹/证据记录**（溯源，非不确定性数学） |

**结论**：SwarmLabs 的"验证"定位在**命名上不唯一**，直接说"我们做验证"会被淹没。
差异化必须精确表述为：**唯一做"不确定性量化 + 失效边界"验证的** ——
别人回答"它复现了吗 / 有没有证据链"，我们回答"**置信度多少、在哪儿会翻车**"。

> 对外文案建议：从 "verification" 改为 **"math-backed verification"** / "uncertainty-quantified verification"。

### 发现 2：无人在做「虚拟实验」—— 这是真空白区

8 个仓库**全部**是 literature → write → review 流水线，**没有一个**涉及 GP 代理 / UQ / 主动学习 / 实验锦标赛 / OOD 门禁。
最接近的只有：ARIS 的 `Experiment Bridge` 工作流 + `/result-to-claim` 声明状态；Orchestra 的 `Agent-Native Research Artifact`。二者都不碰 UQ。

→ SwarmLabs 的虚拟实验验证层仍是**结构性空白**，这是真正的护城河，也验证了 P0-1（验证账本）方向正确。

### 发现 3：分发范式已标准化为「市场 + 可安装 skill」

`anthropics/knowledge-work-plugins` 确立 "add marketplace → install plugin" 模式；K-Dense / Orchestra / Imbad0202 均支持 `npx skills add`、`gh skill install`。
→ SwarmLabs 若走「Agent Skills 分发」商业化（memory 已列为核心变现路径之一），**必须遵守这套规范**，否则进不了市场。

## 三、可借鉴清单

### P0 — 直接搬（低成本 / 高杠杆）
1. **Agent Skills 打包 + 市场分发**（anthropics + K-Dense）
   把 SwarmLabs 能力打成可安装 skill：`virtual-experiment` / `uq-coverage` / `ood-guard` / `monod-fit` / `fermentation-design` 等，
   每个一个 `SKILL.md`，发 marketplace。已有基础：`skills/registry/labguard_gate.json`。
   **规范要点**（K-Dense 一手规范，已验证）：`name` 1–64 位仅小写字母+数字+连字符且匹配目录名；`description` ≤1024 字符、第三人称+触发词；`metadata` 必须 block mapping（写成 JSON flow mapping 会让 strictyaml 整段解析失败）；`metadata.version` 必须带引号；`allowed-tools` 是**空格分隔字符串**非 list；`SKILL.md` < 500 行，长内容下沉 `references/`；测试放 `tests/<name>/` 绝不进 `skills/`。
2. **结果 → 声明状态跟踪**（ARIS `/result-to-claim`）
   把 paper validation 暴露成"每条结论的验证状态"。**已完成 → `frontend_fixed/verify.html`（验证账本，18 基准 / 7 个 OOD 红区 / 端到端 +77.8%）**。
3. **反重复记忆**（ARIS：失败 idea 写入记忆，避免未来 ideation 重复）
   加到 `flywheel.py`：已探索过的研究空白 / 已失败的验证不再重复推荐。

### P1 — 改造后搬
4. **公开真实跑分**（ARIS 第 5 节 "Score Progression (Real Run)"）
   SwarmLabs 已有 validation-dashboard；应强化"real run 透明度"叙事，这是对抗"rubric 式严谨度"的武器。
5. **人机协同而非全自动**（Imbad0202 专章 "Why human-in-the-loop, not full automation?"）
   SwarmLabs 的 VirtualLab Guard 三态门禁正好对应；文案明确"我们不做全自动，我们做**可审核的自动**"。
6. **可信面展示格式**（AERS）借鉴其表格形态，但**用真实 UQ 数字填充**而非 rubric 打分 → 差异化声明点。
7. **垂直深度领域打包**（K-Dense / aipoch 做法）
   SwarmLabs 的 52 微生物场景 + 21 菌株 + 20 培养基是它们没有的深度资产，可打包 `microbio-fermentation-kit` 争夺细分。
8. **"每次跑重算 gold 值"**（AERS benchmark/ 做法）→ 与 SwarmLabs 每日飞轮同向，应在对外文案中强调"每次运行重算，非静态快照"。

### P2 — 观察 / 择机
9. **夜间自动研究循环**（ARIS "sleep & wake up to results"）：飞轮已有雏形，可加 overnight experiment tournament。
10. **Agent-Native Research Artifact**（Orchestra）：结构化可机读产物，与 SwarmLabs 157,728 条结构化实体同向，可对齐格式提升互操作。
11. **按研究阶段组织技能库**（aipoch：文献 79 / 方案设计 68 / 组学 52 / 临床 61 / 药物 26 / 写作 66 / 流程 77）：信息架构值得借鉴来组织 SwarmLabs 场景库。

## 四、不要做的（风险红线）

- **不要在 skill 数量上追赶**：K-Dense 165、AERS 23,000+、aipoch 数百。数量竞赛我们必输且无意义。
- **不要在「文献检索 / 综述」正面战场对撞**：AMiner + Imbad0202 + Master-cai 已占满。
- **不要声称"唯一做验证的"**：Imbad0202 与 AERS 已在做。要说 **"唯一用可执行数学（UQ 覆盖率 / OOD 门禁）做验证的"**。
- **不要为了凑数降低噪声地板**（3% 归一化红线）：一旦放宽就失去全部差异化信用。

## 五、落地建议

| 序 | 动作 | 成本 | 依赖 |
|---|---|---|---|
| 1 | `verify.html` 上线 `/verify` 路由 + 在博客/首页挂入口 | 低 | 已完成生成，需部署 |
| 2 | 建 `swarmlabs-skills` 公开仓，5–8 个 SKILL.md，发 marketplace | 低（零成本） | P0-1 规范 |
| 3 | flywheel 加反重复记忆 | 低 | — |
| 4 | 对外文案统一改为 math-backed verification | 极低 | — |

**建议先做 1 + 2**：1 是把"我们有验证层"变成用户可感知特性的最小切片（已完成代码）；2 是商业化（Agent Skills 分发）的直接路径且零成本。
