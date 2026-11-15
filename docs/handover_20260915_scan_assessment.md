# 核心工作成果包_20260915 — 扫描评估与引用决策

> 扫描时间：2026-09-15 20:36
> 来源：`C:\Users\xing\Desktop\核心工作成果包_20260915.zip（9.zip）`
> 说明：这是腾讯云工作机 `/home/z/my-project` 在 2026-09-15 重置前的**未推送快照**。
> 本文件记录扫描结论与"引用 / 不引用"的每条依据，可回溯。

## 包规模

| 项 | 值 |
|---|---|
| 条目数 | 2,731 |
| 解包体积 | 27.89 MB |
| 压缩包体积 | 9.46 MB |
| 主要扩展 | .html 1440 / .py 178 / .md 233 / .json 156 / .ts/.tsx 155 / .db 14 |
| 附属压缩包 | aishield-full.tar.gz 352KB / bionic-ai-site.tar.gz 60KB / oraclemind-updated.tar.gz 616KB |

## 顶层结构（按文件数）

| 目录 | 文件数 | 与本项目的关系 |
|---|---|---|
| genetech-tools/website | 295 | ❌ 明确排除 |
| oraclemind/src | 215 | ❌ 明确排除 |
| agent-ecosystem/website | 180 | ❌ 明确排除 |
| exo-science / brain-science / new-energy / life-science / robot-parts / deep-sea-tech / alien-minerals / nuclear-energy / quantum-computing | 各 87–132 | ❌ 明确排除 |
| skills/ | 132 | ⚠️ 部分可用（见 §B） |
| agents/ | 154 | ⚠️ 与现有 `agentteams/` 重叠 |
| kb-workflow/ | 40 | ❌ 面向 14 站（排除项） |
| 根目录 swarmlabs_*.{py,json,html} | 130+ | ⚠️ 见 §A |
| 根目录文档 | 20 | ⚠️ 见 §A |
| oraclemind / patent / physics / pharma / bees / core / docs / data / dashboard / download / tmp | 若干 | ❌ 见 §C |

## A. 建议**明确不引用**的（附依据）

### A1. `swarmlabs_*` 55 个引擎脚本 + `swarmlabs_*_result.json` / `_validation.json`

**规模**：55 个 `.py`，从 `swarmlabs_api_server_v3.py`（237KB）到 `swarmlabs_adsorption_engine.py`（25KB）等，含 crystal / adsorption / distillation / membrane / corrosion / battery / perovskite / scfluid / pcr / enzyme / quantum / md / materials / physics_v2 等。

**为什么不引用**：
1. **架构冲突**。当前项目 `engine/` 是 GP+UQ 单一真源（`surrogate.py`），噪声地板 3% 不降、覆盖率≈0.95，走的是"代理 + 校准"路线。旧脚本是**确定性物理公式 + 经验规则**（PFR/Plug-Flow / Arrhenius 参数硬编码），是另一套建模哲学。混入会破坏 `verification_gate.py` 的口径单一性。
2. **UQ 是事后贴标签，不是 GP 后验**。`swarmlabs_uncertainty.py` 用硬编码字典 `ENGINE_UNCERTAINTY = {'adsorption': {'mean_err': 6.4, ...}, ...}`，然后 `margin = 1.96 * std_err * abs(predicted_value)`——这是**事后固定 σ**，不是模型内生的不确定度。与本项目 UQ 是**一等公民**的原则直接冲突。
3. **验证数据是编的**。抽样 `swarmlabs_ammonia_validation.json`：`"ref": "Fe标准"` / `"低温高转化"` / `"高压"`——`ref` 字段是**中文描述性标签**，不是文献引用。违反项目铁律"不编造数据"。
4. **paper_database 也是编的**。`swarmlabs_paper_database/`：`battery.json` 的 `doi="handbook"`、`perovskite_solar.json` 的 `doi="NREL"`——用**非 DOI 字符串**充作 DOI。只有 `mof_adsorption.json` 和 `photocatalysis.json` 里有真 DOI（10.1038/s41598-021-04168-w、10.3390/molecules26061687 等），但每文件仅 5–6 条。

**结论**：整体弃用。**唯一残留价值**是 photocatalysis / mof_adsorption 两份里有 6–8 条带真 DOI 的文献点，可作 `engine/wet_lab_anchors.py` 的**扩展候选**——但需要人工核实每条 DOI 是否指向所述数值（本次未核实，因此不纳入本轮）。

### A2. `kb-workflow/scripts/geo_flow.py` 等 GEO 脚本

`geo_flow.py`（5.4KB）、`ai_seo_optimizer.py`、`graphify_engine.py`：内容为**占位式模板**——`'content': f'{topic}领域的最新发展概述。'`、`'content': f'2026年{topic}关键技术趋势。'`。面向 14 站（排除项）设计。不引用。

### A3. `agents/` 8-Agent 框架（scout / builder / strategist / growth / guardian / designer / operator / researcher）

与项目现有 `agentteams/` 模块**职能完全重叠**（scout ↔ executor、guardian ↔ verifier 等），且实现基于 2026-07 的 `glm-4-plus` 三模型交叉，已落后于当前 SenseNova 6.8-flash-lite 配置。不引用。

### A4. `kb-workflow/`、`mavenbio-borrow-analysis.md`、`小乌数据采集任务包`

面向 genetech-tools 14 站与"小乌数据采集"工作流，全部属于排除项。不引用。

### A5. `oraclemind/`、`genetech-tools/`、`robot-parts/`、`tcm-tools/`、`agent-ecosystem/`、14 站 website、`.git`（oraclemind 内嵌）

用户已明确排除。不引用。

### A6. `patent/`、`pharma/`、`bees/`、`physics/`、`core/`、`dashboard/`、`data/`、`docs/`、`download/`、`tmp/`

与当前项目无架构对齐；patent/ 内容为 .xml/.zip/.jpg 混合的历史专利素材；physics/ 是与 A1 重复的另一份 `physics_engine_v2.py`。不引用。

### A7. 根目录 45+ 一次性 `find*.py` / `xc*.py` / `fix*.py` / `deploy_*.py` / `restart_atex.py`

一次性脚手架脚本，无复用价值。不引用。

## B. **已引用**的（2 项，均已做安全审计）

### B1. `skills/competitive-analysispro/SKILL.md` → `~/.workbuddy/skills/competitive-analysispro/`

- 大小：11.5KB，纯 Markdown 指导
- 来源：Anthropic 官方 `competitive-analysispro` skill 内容
- 安全审计：**纯文本，无脚本、无网络调用、无 subprocess、无文件系统写入**（`grep` 检查通过）
- 引用理由：直接对齐本项目 `docs/competitor_swarmlabs_positioning_2026-09-12.md` 的竞品分析诉求；含 moat 评估表（网络效应 / 切换成本 / 规模经济 / 无形资产）、行业关键指标表、来源质量分级（10-K → earnings → sell-side → industry report → news）。
- 存放位置：用户级 skills 目录（SwarmLabs + aishield 均可用，跨项目通用）。

### B2. `skills/in-depth-research/SKILL.md` → `~/.workbuddy/skills/in-depth-research/`

- 大小：3.2KB，纯 Markdown 指导
- 安全审计：纯文本，无脚本、无网络调用。
- 引用理由：与项目"决策前多源调研、事实锚定"的执行原则一致；含 Scope→Search→Evaluate→Deepen→Synthesize→Document→Deliver 七步协议、来源评估五维（authority / recency / evidence / bias / corroboration）、四档深度分级（Quick / Standard / Thorough / Exhaustive）。
- 存放位置：用户级 skills 目录。

## C. 已跳过但**可再评估**的（1 项，涉及合规门槛）

### C1. `skills/code-analysis-skills/`（17KB SKILL.md + src/ 20 个 .py + publish.sh + tests/）

- 来源：`https://github.com/Wscats/code-analysis-skills`，MIT
- 内容：Git 历史描述性统计 → 生成开发者复盘报告（Markdown/HTML/JSON/PDF）
- **合规门槛**：SKILL.md 明确写入"必须自用或获全员书面同意；不得用于绩效考核 / 排名 / 薪酬 / 晋升；无环境变量绕过；CLI 硬闸门 `--i-have-consent`"。这是一份设计良好的合规约束。
- 未引用理由：本项目当前**无使用场景**（SwarmLabs 单开发者，无"其他开发者"数据；也不涉及绩效场景）。安装会增加未使用面，且 SKILL.md 明确要求 agent 在调用前主动向用户确认授权——当前无触发场景。
- 再评估触发条件：项目引入第二位开发者 + 用户明确要"团队复盘"时，再走完整安装 + 授权确认流程。

## D. 明确不建议（附一句拒绝理由）

| 项 | 拒绝理由 |
|---|---|
| `skills/market-analysis-cn/SKILL.md`（1.2KB） | 假冒"付费服务"外壳（`POST /analyze` + `$10 / $30 / $50 USDC`），内容为占位文本，无实质方法论。且含虚构定价字段，易被外部脚本误触发。 |
| `swarmlabs_*` 验证 JSON（ammonia / enzyme / benchmark_real） | `ref` 字段是中文描述性标签而非文献引用；违反"不编造数据"铁律。 |
| `swarmlabs_paper_database/{battery,perovskite_solar}.json` | `doi="handbook"` / `doi="NREL"` 为占位符，非真 DOI。 |
| `kb-workflow/deep-mine/`（18 个 .js/.py/.sh） | 面向 14 站的采集脚本，目标项目已排除。 |
| `aishield/` 全部（本包内 46 个文件） | 本包内的 aishield 是旧版（`guardrail-mcp/1.0.0.tgz`、`npm-package/aishield-mcp-2.1.0.tgz`），与当前 4.2.2（227 rules / 233 skill / 6 tools）差异巨大；本包内没有 marketing/ 目录（可能未打包进来）。用当前版本，不覆盖。 |
| `swarmlabs-mcp/server.py`（README 声明 11 工具） | 是分子性质预测平台，与本项目"AI4S 可信验证层"定位不重合。 |

## 引用总账

| 类别 | 数量 |
|---|---|
| 已引用到项目 | **2**（skills/competitive-analysispro、skills/in-depth-research） |
| 建议引用但需人工核实（paper_database 6–8 条真 DOI） | 1 项待办 |
| 已跳过但可再评估（含合规门槛） | 1（code-analysis-skills） |
| 明确不引用 | 6 大类（见 §A） |
| 明确不建议 | 6 项（见 §D） |

**一句话结论**：这个包的价值不在于"能搬多少进来"，而在于**它证明了过去两个月里存在过一整套伪造验证数据的旧路径**——这正是本项目要与之切割的东西。真正可直接复用的只有 2 个纯净的方法论 skill。

---

*附：包内 20 个 swarmlabs_* 引擎的引擎 ID 列表（备查）：adsorption / ammonia / battery / bioreactor / co2 / combustion / complexometric / corrosion / crystal / distillation / drying / electrodialysis / electroplating / enzyme / extraction / flocculation / fluidization / gassolid / ion_exchange / ionic_liquid / membrane / membrane_distillation / molecular / pcr / perovskite / photoFenton / photocatalysis / polymer / quantum / scfluid / sonochemical —— 与当前 62 场景库无任何重叠，说明是**两个独立的、并行演进**的建模体系。*
