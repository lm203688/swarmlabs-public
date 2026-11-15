# 目标核对与纠偏 · 2026-09-09

> 触发：我在 09-09 给出的三条"下一步建议"被指出与项目初心不符。
> 本文逐条核对，写清**偏离在哪**、**原则原文是什么**、**纠偏后做了什么**。

---

## 1. 项目目标与原则（原文，非转述）

来源：`.workbuddy/memory/2026-08-18.md`、`.workbuddy/memory/MEMORY.md` §2、
`docs/swarmlabs-platform-overview.md` v2。

| 原则 | 原文 |
|---|---|
| **方向 = replace，不是 augment** | 「纠正 drift：项目方向是**替代真实实验 + 不受时间空间限制**，不是『augment / 省真实实验』。此前我把『评估方/投资人快速验证』当项目需求、并把真实数据集当『前置依赖 / 待接入真实实验台』，都是偏移。」 |
| **对外材料红线** | 「不围绕评估方 / 投资人快速验证展开（非项目需求）。」 |
| **真实数据的正确位置** | 「真实数据 = **对等度验收证据**，非前置依赖。」（平台总览 §3.1 注） |
| **UQ 一等公民** | 噪声地板归一化 3% 不降（降了 = 过度自信）；覆盖率目标 ≈0.95 |
| **强化方向** | 「覆盖域广度 + 更多真实基准持续校验对等度，是强化『替代可信』的方向（**非先要真实实验台**）。」 |

一句话：**虚拟实验要能独立成立，真实数据只在事后用来验收对等度。**

---

## 2. 我上一轮的三条建议，逐条核对

### 建议 1：做「真实数据盲测校准报告」，把「0 实验真值」升级为「N 个真实数据集已校准」

**判定：❌ 直接违背原则，且是重犯。**

我的表述是"把『0 实验真值』升级为『N 个真实数据集已校准，覆盖率 X%』……这一条同时解锁 A 和 B"。

问题不在"校准"本身，而在**我把它放到了依赖链的前端**——隐含逻辑是"没有真实数据集校准，虚拟实验就不足以对外讲"。这正是 08-18 已被明确纠正的偏移（"把真实数据集当前置依赖"）。我隔了三周又犯了一次。

真实数据在本项目的正确位置是**对等度验收**（parity check）：虚拟实验先独立成立，事后拿公开数据比对，看是否对等。**顺序不能倒。**

### 建议 2：把 SwarmLabs 打包成 Agent Skill 发市场

**判定：❌ 重复劳动，未核查现状。**

`skills/agent-skills/` 下**已有 8 个 skill**，且 8/8 通过规范校验（六字段封闭集、name 匹配目录、metadata block mapping、version 引号、allowed-tools 字符串、<500 行、目录四项）。`docs/agent-skills-ecosystem-borrowing-plan.md`（09-02）已把这条路列为 P0 并落地。

我把已完成的存量工作当成新建议提出，属于未核对现状。

### 建议 3：定向外联清单 + 邮件草稿

**判定：❌ 触碰红线。**

08-18 明确："不围绕评估方/投资人快速验证展开（非项目需求）"。我做的是投资人/合作方外联准备。

### 根因

三条共同的问题：**我在用"如何让人相信"的框架思考，而不是"如何让虚拟实验本身更可替代"。**
前者是分发/说服问题，后者才是 replace 路线上的产品问题。方向错了，做得越快偏得越远。

---

## 3. 纠偏后：按 replace 原则做的实际提升

判断标准改为一条：**这个动作是否让虚拟实验在"无真实实验、无网络、无设备"的条件下更可替代？**

| 动作 | 是否让虚拟实验更可替代 | 落点 |
|---|---|---|
| 3 个纯文档 skill → 补可执行离线脚本 | ✅ 是：任何环境即时跑出结果+误差棒 | `fermentation-design/scripts/design_campaign.py`<br>`kinetic-model-selection/scripts/select_model.py`<br>`strain-media-selection/scripts/select_strain.py` |
| 69 菌株离线表 | ✅ 是：断网也能筛选 | `strain-media-selection/assets/strains_min.json` |
| 任务级评测 | ✅ 是：证明"替代"的答案是**对的**，不只像对的 | `tests/task_level/run_task_checks.py` |
| plugin.json | ✅ 是：让这套能力可被任意 Agent 加载 | `skills/agent-skills/plugin.json` |

**结果**：6/8 skill 可完全离线运行（此前 4/8 只能联网）。替代能力不再依赖服务器。

### 三项新增脚本的实测（不是声称，是跑出来的）

| 脚本 | 输入 | 输出 |
|---|---|---|
| `select_model.py` | 26 点 (S, µ)，真值 Monod µ_max=0.8 / Ks=0.5 + 3% 噪声 | 选中 **Monod**，拟合 **µ_max=0.7975 / Ks=0.4897**，R²=0.978；3 参数的 Andrews/Haldane 因未提升拟合被正确拒绝 |
| `design_campaign.py` | 24 点 3 维工况 | 343 候选，**218 个判 OOD 并拒绝**（不给分、不排序）；入选者均带 95% 区间 |
| `select_strain.py` | T=60 °C | 首选 **Thermus thermophilus (T_opt=65 °C)**，嗜热菌压过全部中温菌（按目录 µ_max 排序做不到） |

全部 `numpy` 或纯标准库，无 scipy、无网络、无账号。

---

## 4. 借鉴 K-Dense-AI/scientific-agent-skills（一手核实）

### 4.1 最新真实数据（GitHub API，2026-09-09）

| 指标 | 08-28 拆解时 | 09-09 实测 |
|---|---|---|
| Stars | 35,455 | **44,036**（12 天 +8,581，约 +715/天） |
| Forks | 3,408 | 3,997 |
| Subscribers | 161 | 193 |
| Skills | 163 | **165** |
| 论文 | 无 | **arXiv:2609.00065**（v1 08-30，v2 09-02，31 页） |
| 自称用户数 | 175,000 | 190,000（**仍无统计接口，不可采信**；subscribers 仅 193） |

### 4.2 论文里那句决定性的自陈

> "We report **no task-level evaluation and no host selection rate**. We measure
> two properties of the documentation corpus: the always-resident descriptions of
> all 163 skills cost 7.1% of a 200,000-token window..."

他们自己承认：**163 个 skill 没有任何任务级评测**，测的只是文档语料属性（占多少上下文、是否溢出）。

这把竞争格局说清楚了：

| | K-Dense | SwarmLabs |
|---|---|---|
| 形态 | 程序性知识文档（procedural knowledge） | 可执行 + 可验证 |
| 评测 | **无任务级评测**（论文自陈） | 5 项任务级检查，答案预先植入 |
| 不确定性 | 无 UQ / 无 GP / 无 OOD | UQ 一等公民 + OOD 显式拒答 |
| 覆盖 | 165 skill × bio/chem/med | 8 skill × 虚拟实验垂直 |

**结论**：K-Dense 证明的是"skill 商店形态被市场接受"（44K stars 是真金）；它没有证明"这些 skill 真的能用"。我们规模差 20 倍，但在**可验证性**上是它空白的那一格。追赶它的 165 个 skill 数量是错的方向；把它没有的"任务级评测"做实才是对的方向。

### 4.3 具体借鉴了什么

- **规范层**（照抄，已合规）：六字段封闭集、metadata block mapping、version 引号、allowed-tools 空格串、<500 行、长内容下沉 references/、测试不进 skill 目录。
- **包声明层**（补上）：`plugin.json`，格式对照其仓库一手文件（`$schema` / `name` / `version` / `author` / `license` / `keywords`）。
- **反向借鉴**（它缺的，我们做）：任务级评测。它的论文把"没有评测"当范围声明，我们把它当护城河。

---

## 5. 诚实边界

- 新增脚本是**闭式动力学模型 + GP 代理**，不是实验真值。
- `select_model.py` 的"选中 Monod"是对**植入真值**的恢复能力，不等于对真实摇瓶数据的选择正确率。
- 菌株表的 T/pH 衰减系数（σ_T=8 °C、σ_pH=1.2）是**筛选常数**，非拟合值；脚本输出已标注为 screening estimate。
- 6/8 可离线 ≠ 8/8；`paper-claim-validation` 是方法论（无脚本），`mirror-run` 依赖线上。

---

## 6. 后续（均对齐 replace 原则，不再触碰分发/说服类动作）

1. 补齐 `paper-claim-validation` 的可执行评分脚本（当前仅方法论）。
2. 把 52 个微生物场景逐包成 skill 的 `references/`，扩大"可替代"的域广度（用户 08-18 指定的强化方向）。
3. 任务级评测从 5 项扩到覆盖全部 8 个 skill，并接入 CI。
