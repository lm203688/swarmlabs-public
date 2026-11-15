# 外部信号扫描裁决 · 2026-09-17

> 目的：对用户抛入的 8 项外部信号，逐项给出「对 SwarmLabs 是否有帮助」的诚实裁决与可执行动作。
> 纪律：所有事实锚定到已检索来源；无来源的判断标注为「推断」；不为了让结论好看而拔高相关性。

---

## 0. 先纠正转述（避免后续所有讨论建立在错名字上）

| 用户转述 | 实际 | 说明 |
|---|---|---|
| 文力所 | **力文所**（Levinthal Biotech，杭州力文所生物科技有限公司） | 2021-09 成立，创始人王浩博（北大博士，哈佛 Sergey Ovchinnikov 组博后）。刚完成数千万 Pre-A（金雨茂物领投） |
| 时空晶体能变**黑瞳** | **黑洞** | TU Wien + 法兰克福歌德大学，临界坍缩（critical collapse）→ 微型黑洞 |
| ZDTAICHU50-9B | **ZDTaichu 5.0-9B** | 紫东太初（中科院自动化所 + 武汉人工智能研究院），2026-09-15 开源 |
| levinharness | **Lévin™ Harness** | 与 PALLATOM **同一家公司**（力文所）。用户可能以为是两个独立来源，实际是「模型层 + 编排层」的同一主体 |
| 「文力所全原子蛋白设计平台 PALLATOM」 | 拼写为 **Pallatom** | bioRxiv 2024.08.16.608235；ICML 2025 Spotlight；被 NVIDIA 官网列为蛋白质基础模型（与 AlphaFold 3 / ESM3 并列） |

### ⚠️ 同名歧义：「OpenResearch」是两个完全不同的项目

| | A：alphaXiv 版 | B：openResearch1 版 |
|---|---|---|
| 仓库/站 | openresearch.sh，GitHub ~3.5k stars，MIT，一度 GitHub Trending 第一 | GitHub `openResearch1/openresearch`，Bun monorepo + Tauri 桌面 |
| 形态 | 本地优先工作台 + `orx` CLI，SQLite，`127.0.0.1:4791` | 研究操作系统，Web + 桌面 + SDK |
| 核心机制 | **实验树 = git 分支**。每次运行对「精确 commit」做不可变归档；每研究方向独立 git worktree 并行；compute 市场（H100/Slurm/K8s/Ray/HF Jobs/Modal） | **原子知识图谱**：`fact_claim` / `method_claim` / `theorem_claim` / `verification_claim` + evidence，显式保留失败实验、废弃分支、脆弱假设 |
| 对接现有 agent | 支持 Claude Code / Codex / OpenCode / Cursor，`orx install-skills` 装技能 | 自有 agent pipeline |

**对 SwarmLabs 而言 B 比 A 更贴（B 把「验证性结论」做成一等公民且保留失败面），但 A 的声量与分发力远大于 B。** 两者都要在定位文档里点名，不能混作一个。

---

## 1. 逐项裁决

| # | 信号 | 相关性 | 裁决 | 可复用物 |
|---|---|---|---|---|
| 1 | Sims 1971《无穷维参数空间中的分布滞后估计》 | **中**（概念） | 留作 honest-UQ 论证弹药 | 「贫集」= 与样本量无关的精度下限 |
| 2 | 时空晶体 → 微型黑洞（PRL，2026-06 起报道） | **中**（概念） | 留作方法论 + 门禁判据 | 人为极限里求解再映射；临界点判据 |
| 3 | 力文所 Pallatom | 低 | 不进路线图 | 无（生成层，非验证层） |
| 4 | 力文所 Lévin™ Harness | **中高** | 值得偷一个机制 | 模型插件 SDK（Plugin SDK） |
| 5 | OpenResearch（A/B） | **最高** | 竞争面须立即处置 + 借鉴 | commit 级不可变归档；skill/MCP 分发 |
| 6 | ZDTaichu 5.0-9B | 中（工具）+ **需披露的竞争压力** | 可作仪器视觉层 | OpenAI 兼容 vLLM 端点、空间/具身能力 |
| 7 | Robotaxi | **低**（仅叙事） | 只用一句话类比 | 「仿真替代实测已被市场验证」先例 |
| 8 | GPT-6 Astra / Claude Science / GPT-Rosalind 生态 | 已跟踪 | 维持既有判断 | 强化「harness 需要 gate」 |

---

## 2. 真正值得动的三件事

### A. 立即更新定位：把「可复现」和「有效」切开（防 OpenResearch 占位）

OpenResearch 自我陈述的措辞是：*「补齐 AI 科研的最后一公里基础设施，主打可复现性、可追溯性、可核查性」*——这与 SwarmLabs 的「可审计验证层」几乎同词。

**必须在 `docs/competitor_swarmlabs_positioning_2026-09-12.md` 追加一段硬切割：**

> 可复现 ≠ 有效。一个完全可复现的实验可以完全错误。OpenResearch 回答「这个数字出自哪个代码状态」；SwarmLabs 回答「这个声明在给定不确定度下是否可信、错了是否必须阻断」。前者是**谱系**，后者是**裁决**。二者互补，不是替代——而这正是 SwarmLabs 的护城河位置：**没有任何编排层会自己承担 fail-closed 的责任**，因为它一旦阻断就会伤害自己的产出指标。

这是本轮扫描中唯一需要当天处理的项。

### B. 借鉴：给 V&V 账本加 commit 级锚定

现状（据 `frontend_fixed/reports/gate_ledger.json` + `engine/verification_gate.py`）：账本按 **scenario key** 索引，记录 PASS/MARGINAL/REFUTED 与阈值。**缺少「这个 PASS 是对哪个引擎版本算出来的」。**

借鉴 OpenResearch 的「每次运行对精确 commit 不可变归档」：
- 账本每条增加 `engine_commit` + `oracle_digest`（对 oracle 定义做哈希，与既有 `source_digest` 同构）
- 效果：一次 V&V PASS 钉死在 exact engine revision 上。审计者问「两个月前的 62 PASS 是哪个版本」时可复现。「改 engine 后门禁账本自动再生」的飞轮（步骤 1.6）天然保证锚定随改动更新

风险：改 `build_gate_ledger.py` + `verification_gate.py` 需走回归 → 不是零成本，建议排进下一批。

### C. 借鉴：模型/场景插件接口（scenario library 的增长机制）

Lévin Harness 的机制：*「不在目录里的模型可通过 Python Plugin SDK 接入」*——把生态增长外包给用户。

SwarmLabs 现状：62 场景靠自建，surrogate 是唯一真源、无外部接入点。
- 加一个 oracle/surrogate 插件接口 + 提交校验（`validate_swarm_config` 那套 fail-closed 校验已有先例可复用，见 `agent_topology/load_organism_config.py`）
- 这是 62 场景库走向「外部贡献」的唯一路径，与「entity 47,566 → 100k」的数据护城河是同一动作的镜像

**可低成本试探的渠道**：把 `mcp/swarmlabs_vv_server.py`（已有 7 tools）做成 skill 形态，让 OpenResearch（`orx install-skills`）/ Claude Science / Lévin Harness 的用户能在自身 harness 里直接调 SwarmLabs gate。**这是本轮唯一可低成本获得的真实分发渠道**——因为这三家的用户已经在写「验证」这一步，只是他们手上没有独立裁决器。

---

## 3. 两项「概念弹药」（不写代码，用于对外材料）

### Sims 1971：为「噪声地板不降 + REFUTED 永久保留」找已发表先例

论文（*Ann. Math. Statist.* 42(5), 1971；Sims 2011 诺奖）的核心负面结论是 **贫集（meagre set）**：若真实滞后结构落在参数空间的贫集里，**无论样本量多大，精度都存在无法突破的本质下限**——「不是方法的问题，也不是数据不够的问题」。

这与 SwarmLabs 的两条既定硬约束同构：
- 「噪声地板 3% 不降」——承认并公示一个不可突破的地板，而非宣称持续逼近真值
- 「5 REFUTED 真实保留并硬阻断」——承认有些声明不可被证实

而论文的正面技巧——**换坐标系（时域卷积 → 频域乘法）把不可能变可能**——与 SwarmLabs 2026-09-15 的两阶段 log10 坐标升级（9 场景由训练侧长度尺度撞网格下界自动触发）是同一个动作，且该升级的独立生物学依据（E. coli 葡萄糖 Ks 跨 5 mg/L→30 μg/L，167×；μmax 与 Ks 不可同时测定）已记录。

**用法**：在 honest-UQ 长文/BP 里用一段支撑「主动声明精度上限不是能力不足，是 MATH 层面的正确姿势，1971 年就有诺奖级先例」。**零工程复用**，不要指望它给技术路线。

### 时空晶体：两条可迁移物

1. **方法论（同构于 log10 升级）**：研究者在「无限维」这个人为极限里求解——*「随着维度增加，方程反而变得更简单」*——再映射回 4 维。可用于回答「GP 外推退化时怎么办」：第三条路不是加数据，而是**换坐标或换极限**让它变良态。
2. **门禁判据（可落地）**：临界点附近「微小能量注入」翻转定性结局（消散 or 坍缩）。映射到 SwarmLabs：**近临界场景（阈值/双稳态，如 `micro_thermal_death` 的 z 值缺失、抑制型场景）的 R² 无意义**——应改为检验「预测是否落在分界正确一侧」。这是一条可加进 `engine/verification_gate.py` 的判据类型，但需要先定义「临界距离」的量度，不宜仓促。

---

## 4. 必须诚实披露的一处竞争压力：ZDTaichu 类「湿实验自动化」

ZDTaichu 5.0-9B 官方称已完成**科研干湿实验闭环验证**（试管按序入架、滴管液体转移），定位「高层规划大脑」，底层运动控制交给硬件。已在武汉/佛山/青岛具身训练场落地。

**方向与 SwarmLabs 相反**：SwarmLabs = 虚拟化湿实验（让你不必做）；ZDTaichu 类 = 自动化湿实验（让你做得更便宜）。

- 诚实读法：**湿实验自动化变便宜，会削弱「必须虚拟化」的紧迫性**。这是对 SwarmLabs 核心替换论的有效压力，须在战略文档中显式承认，不能装作看不见。
- 反驳依据（已有事实）：试剂/耗材/人力是边际成本，且自动化仍需时间与排期；虚拟化在**筛选**阶段仍是数量级更便宜。核心论点应表述为「自动化解决执行成本，验证解决决策成本」。
- 另有一个**真实工具价值**：该模型开源、ModelScope 可下（`TaichuAI/ZDTaichu5.0-9B`）、官方适配 vLLM v0.26.0 分支并提供 **OpenAI 兼容端点**（`:18050/v1`），空间基准 MindCube-tiny 78.27 / ViewSpatial 62.5 / ERQA 48 / RoboSpatial 56。SwarmLabs 的 `instrument_simulator`（仪器模拟）当前无视觉层——**读板、读胶图属其 ERQA/RoboSpatial 强项**。本地 RTX 3090/4090 可跑（bf16 约 18GB，3090 24GB 偏紧，需量化）。
- 同时它开放的**全流程数据管线**（含三维能力标签体系、CoT 合成 + 一致性校验、GRPO 可验证奖励）中，「空间框选坐标命中奖励」这类**可自动校验奖励信号**的构造思路，可对照 SwarmLabs 场景库元数据不可信的既有问题（Ks 偏离 44×、元数据自相矛盾 55×）——用可验证奖励反查标注质量。

---

## 5. Robotaxi：直接帮助≈0，只取一句话

**结论：不要把这个当市场。** SwarmLabs 无汽车数据、无 ISO 26262 / SOTIF 资质、销售周期与认证体系完全不同。把它写进路线图是错误配置。

唯一可提取的价值：它是**「用大规模仿真替代真实测试」已被市场验证且已盈利的现存最强先例**。

已核实数据（2026）：

| 主体 | 关键事实 |
|---|---|
| 小马智行 | 2025-11 广州核心城区单车运营盈利转正；2026-02 深圳月度单车转正；2026H1 Robotaxi 累计收入 4.78 亿元，同比 +98.9% |
| 萝卜快跑 | 累计自动驾驶里程超 3.5 亿公里（全无人 2.4 亿）；累计订单破 2300 万单；武汉 2025 全年无人订单超 1700 万单且**实现盈利** |
| 文远知行 | 全球 L4 车队约 3400 辆（Robotaxi 超 1800），覆盖 13 国 60 余城；2026H1 海外收入同比 +154% |
| Waymo | 11 个运营城市约 3000 辆，年底周订单目标 100 万次 |
| 特斯拉 | Cybercab 正式投运（2026-09 报道） |

**反向证据必须同时陈述**（否则是选择性引用）：小马智行 2025 全年净亏 5 亿元；文远知行 2025 全年净亏 16.55 亿元；千辆级车队被视为单车盈利分水岭——**单车模型转正 ≠ 公司盈利**。

**用法**：投资者材料一句话类比——「自动驾驶用仿真里程替代实车里程，把安全验证成本压到可商业化水平，2026 年单车模型已转正。SwarmLabs 在生命科学场景做同一件事。」仅此一句，不加第二句。

---

## 6. 没有任何一项解掉总闸门（必须直说）

本轮 8 项信号里，**没有一项触及 SwarmLabs 的真正卡点**：

- 注册有限责任公司（所有 T2 投资渠道的总闸门）
- bus factor = 1
- pre-revenue
- GitHub stars = 0

这些都是**用户侧硬阻塞**（见 `docs/hard_blockers_draft_2026-09-13.md`），外部技术信号无法代解。把时间投入在信号扫描上，边际收益已经很低——本轮的净产出是「1 个必须当天处理的定位切割 + 2 个排进下一批的借鉴 + 2 段对外材料弹药」。

**建议**：这类扫描收敛到**按需触发**（看到具体信号再查），不做常态化。

---

## 附：事实锚定来源

| 项 | 来源 |
|---|---|
| Sims 1971 | *Annals of Mathematical Statistics* 42(5), 1971；Sims 2011 诺贝尔经济学奖；贫集结论见论文正文 |
| 时空晶体 | Physical Review Letters；TU Wien（D. Grumiller, F. Ecker）与 Goethe Frankfurt（C. Ecker）；medium 报道起于 2026-06 |
| Pallatom | bioRxiv 10.1101/2024.08.16.608235；ICML 2025 Spotlight；NVIDIA 官网蛋白基础模型列表 |
| Lévin Harness | levinthal.design 官方页；杭州力文所；当前仅 macOS（Apple silicon），preview 非商用免费 |
| OpenResearch A | openresearch.sh；alphaXiv 团队；GitHub Trending 第一；`orx` CLI；127.0.0.1:4791 |
| OpenResearch B | GitHub `openResearch1/openresearch`；Scientific Atom 图谱（4 类 claim + evidence） |
| ZDTaichu 5.0-9B | 2026-09-15 开源；Qwen3.5-9B 语言骨干 + C-RADIOv4-H 视觉骨干；128K 上下文；ModelScope `TaichuAI/ZDTaichu5.0-9B`；vLLM v0.26.0 分支，`:18050/v1` |
| Robotaxi | 2026 北京车展量产计划；小马智行/文远知行 2025 年报与 2026H1 数据；工信部 L3/L4 强标 2026-02 征求意见 |
| Claude Science / GPT-Rosalind | Anthropic 2026-06-30 发布（Opus 4.8 + 60+ 数据库，reviewer agent 仍为同模型自查）；OpenAI 2026-04 发布，限美国企业客户；OpenAI LifeSciBench（173 位博士构建）最佳模型仅通过 36.1% 真实科研任务 |
