# 收尾三项 + LLM-as-a-Verifier 评估 + OPC 竞赛符合性判断（2026-09-22）

用户要求把上轮标注的「未做」尽量补齐，并回答两个新问题。本文档记录三项落地、两处顺带发现的真缺陷、以及对 LLM-as-a-Verifier 与 2026 怀柔科学城 OPC 挑战赛的判断。

---

## 一、先说顺带挖出来的两处缺陷（比计划内容更重要）

### 1. anchor 维度是死常量——两条证据链从未接线

上轮审计说「anchor 卡在 0.300」，暗示是锚定不足。实际根因不是这个。

`verification_gate._score_dimension` 的 anchor 分支调 `_find_anchor_field(entry)`，后者只在**账本条目内部**查四个键：`anchors` / `wet_lab_anchors` / `reference_anchors` / `published_refs`。而 `gate_ledger.json` 的 82 个场景条目里，**这四个键一个都不存在**：

```
ledger entry keys: ['dim','domain','gate','high_freq','n_test','n_train',
                    'published','rationale','replication_passed','report',
                    'uncertainty','verdict']
grade distribution:  NO anchor field  82
-> anchor-dim mean = 0.3000
```

82/82 全部落到默认分支 `return 0.3, "无 anchor 字段"`。所以 0.300 不是「锚定覆盖率」，是一个**与被锚定状态完全无关的常量**。

后果是：`wet_lab_anchors.py` 里那套完整的分级（`grade_against_anchor()`，AGREE/NEAR/CONTRADICTED/UNANCHORED 四档，19 条锚点、11 个场景）对六维评分**没有任何影响**。已经做完的锚定工作，从未进入过评分。这就是 anchor 卡在 0.300 的机制原因——不是锚定不够，是锚定数据从未被接线。

修法：按 `scenario_key` 做 join。`wet_lab_anchors.json` 的 `scenarios` 列表每项自带 `scenario_key` + `grade`，与账本键天然对齐（11 个键全部命中，0 个失配）。新增 `anchor_assessment()` 统一解析，两级优先：账本内嵌字段（兼容未来写回）→ 湿实验锚点索引。

接线后：

| 维度 | 之前 | 现在 |
|---|---|---|
| anchor 均值 | 0.3000（常量） | **0.3329**（真实分布） |
| 分布 | 82 个 0.3 | 1.0×3 / 0.7×3 / 0.3×74 / 0.0×2 |

新拿到的真实信息里有两条值得单独记：`micro_thermal_death` 与 `microbio_monod` 是 **CONTRADICTED**（0.0）——虚拟实验与文献直接矛盾。前者 oracle 在 60°C 灭活量为 0，而实测 E. coli 在 60°C 有有限 D 值。这两个场景此前被埋在一个没有区分度的 0.3 里。

分数语义刻意保持不兼容变更：CONTRADICTED 仍给 0.0（fail-closed，矛盾必须阻塞），UNANCHORED 与「从未评估」都给 0.3——但 note 里明确区分两者，因为行动建议相反：前者要换方法，后者要补评估。

### 2. scope 路由把编造的 key 判成「已接入内核」

测试模糊路由时撞见的。`_scope` 原逻辑：

```python
if scenario_key and scenario_key in scenarios:
    mode = "existing_kernel"
elif spec is not None and DR.status_of(spec) == DR.VALIDATED and scenario_key:
    mode = "existing_kernel"      # ← 缺陷在此
```

只要 discipline 是 VALIDATED 且 `scenario_key` 非空，就判 `existing_kernel`——**不检查这个 key 在不在账本里**。所以 `micro_totally_unknown` 会被判成已接入内核。

为什么此前没炸：verify 阶段查不到 verdict 会 fail-closed，最终不会给出错误裁决。但中间状态是错的——`mode=existing_kernel` 明确告诉调用方「这是已验证场景」，`scenario_in_ledger` 却返回 False。一个声称存在、实际不存在的场景。

修法是删掉第二个分支，`existing_kernel` 只有一种入口：key 必须在账本里。

---

## 二、三项补齐

### 2.1 稳定 claim id（`engine/vlab_contract.py`）

`work_order_id` 是整体内容哈希，绑定 `design_space`——改一个变量区间就是新 id，无法指向「这份工作单的第 2 条主张」，也无法跨次运行累积发现。

新增 `claim_id`，派生基准只取 `discipline` + `goal`（quantity / unit / direction / plausible_range），**刻意不含** `design_space` / `scenario_key` / `seed`。主张「30°C 下产量 > 2 g/L」不会因为换了扫描区间或换了路由场景而变成另一个主张。

- 不强制：强制会让所有既有工作单失效
- 提供则必须自洽：格式错（`bad_format`）拒收；格式对但内容不符（`claim_id_mismatch`）**只警告不拒收**——静默接受一个不匹配的值，会让「按 claim_id 查历史」查到别人的数据，那比没有 claim_id 更糟

实测：design_space hi 40→55 与 scenario_key 切换，claim_id 不变、work_order_id 变；goal.quantity 与 goal.unit 变化，claim_id 变。

### 2.2 离线模糊路由（`engine/scenario_router.py`，新建）

82 个场景里，调用方不知道确切 `scenario_key` 就进不去，只能落到 `discipline_onboarding_required`。76 个场景事实上只对知道 key 的人可达。

四个信号，合计权重 1.0：专业域 0.30、量名词重叠 0.35、单位 0.20、变量名 0.15。

三条纪律写进代码：

1. **纯专业域命中不足以路由**。最初只要求「domain 或文本命中」，结果「Microbiology + biomass」靠前缀猜中了完全无关的 `micro_acetobacter_acetate`，分数正好卡在 0.35。改成硬约束：quantity / unit / variable 必须至少有一个非零。宁可空手，不猜。
2. **不做量纲校验**。unit 只做字面匹配（mg/L 与 g/L 不归并）——那是 `goal_quantity_binding` 的职责。两条链路各管一段，才不会有一条悄悄失效而另一条也没发现。
3. **分数并列时不按字母序**。`micro_ecoli_glucose` 与两个无关场景同为 0.50，字母序把它排到第 4，被候选窗口截掉。tie-break 改用 anchor 分级 + 复算通过状态——一个有 AGREE 锚点的场景比字母序靠前是值得辩护的。

修了个 token 缺陷：key 里 `ackley2d` 的 `2d` 是维度后缀，去掉后才匹配得上查询词 `ackley`。`logD` 不受影响（没有数字前缀），它的 D/S 是有意义的区分符。

接入点在 `_scope`：只输出 `suggested_scenarios`，**绝不因此改判 mode**。路由器不能替调用方决定路由目标，否则等于绕过 onboarding 这道门。

验证：8/8 正例命中预期场景，4/4 负例（纯专业域 / 无关领域 / 无关量 / 空查询）全部拒绝，确定性可复算，空索引不崩。

### 2.3 门禁：新增 10 条，38/38

N21–N25 锁 claim id（可派生 / design space 不变性 / goal 变化产生新 id / 内容不符警告 / 格式错拒收），N26–N30 锁路由（编造 key 不伪装成内核 / 失败时给候选 / 候选不改判 mode / 纯专业域拒绝 / 确定性）。

---

## 三、LLM-as-a-Verifier 是否值得借鉴

结论先说：**概念上两点有呼应，实现路径全部不能用。**

Stanford + UC Berkeley + NVIDIA 那篇（arXiv 2607.05391，2026-07）的核心是：不读模型采样输出的文本分数，而是读评分位置 token 的完整 log-prob 分布求数学期望，得到 0–1 的连续浮点分数并天然携带不确定性；三个可缩放维度是评分粒度、重复评估集成、准则分解；PPT 把 Best-of-N 从 O(N²) 降到 O(Nk) 并抵消位置偏见。

**呼应的两点**：

- 准则分解就是六维 `ablation_plan`（fidelity / coverage / anchor / replication / benchmark / sensitivity）。这不是借鉴，是同一件事。
- 「连续分数 + 置信度而非离散标签」——`calibrated_verdict` 已经输出 `probabilities` 四档分布，不是 PASS/FAIL。同样不是待借鉴项。

另外两条反而是**外部佐证**：Lu et al. 2025-12 发现 solver–verifier 同源时 false positive 随相似度上升——这正是 `anchor_independence` 度量独立性比率 0.5278 的原因；LR²Bench 报 autoregressive transformer 在约束满足上有约 20–23% 的结构性上限。这两个结果都在支持我们把闸门做成确定性 fail-closed，而不是交给模型判断。

**不能用的一部分**：logit 期望分数需要 API 后端返回完整 logits，PPT 与 Best-of-N 需要反复调用 LLM。我们的红线是不引商业 LLM 密钥 + 离线独立成立，这些前提一个都不满足。而且 SwarmLabs 是确定性内核，不存在「多条候选轨迹择优」这个问题——PPT 要解决的问题我们结构上就没有。

净评估：**没有可移植的代码级借鉴**。它确认了我们的方向（确定性闸门 + 准则分解 + 独立证据源），但没给出能落地的东西。

---

## 四、2026 怀柔科学城 AI for Science OPC 挑战赛符合性

赛事全称「2026 怀柔科学城 AI for Science·OPC 创新挑战赛」，官网 `opc.book2048.com`，报名走「怀柔科创」小程序。两赛道：

| | 揭榜挂帅 | 48h 黑客松 |
|---|---|---|
| 报名截止 | **2026-10-10**（剩 18 天） | 2026-11-10 |
| 人数 | **团队 ≥ 2 人** | 可单人，≤ 5 人 |
| 形式 | 方案竞标真实订单 | 11-15~16 怀柔现场封闭 48h |
| 奖金 | 10 万/榜，优秀方案 3万/1万/5千 | 独立池 10 万，一等奖 1 万×5 方向 |

技术契合度最高的是 **S5「多智能体可信研判与证据链溯源原型」**（发榜方拓尔思 300229，10 万）——「可信度量、证据溯源与因果校验、推理透明全程可审」几乎逐条对应六维验证 + 锚定 + 可离线复验。赛事自身的叙事也直接指向 SwarmLabs：破解 AI「预测快、验证慢」的困境，要求打通干湿实验闭环。

**但揭榜挂帅有一个硬约束我改不了：团队 ≥ 2 人。** solo founder 无法单独报名。这不是技术问题，是资格问题。

黑客松的「AI for Science 智能体开发」方向可单人参赛，但要求**本人在怀柔现场封闭 48 小时**——这是需要你本人到场的事，我不能替你决定。

**我的建议**：

1. 揭榜挂帅 S5 技术上是量身定做的题，但 18 天里找一位合作者 + 完成方案竞标，节奏很紧。值得投入的前提是你愿意拉一个人进来（高校研究者或工程合伙人）。这需要你拍板，因为涉及署名与合作分配。
2. 黑客松可单人，但 11 月中旬现场封闭 48 小时是硬要求。如果能到场，10 万独立池 + 一等奖 1 万/方向 + 大科学装置开放验证环境，性价比不低。
3. 无论走哪条，有一个准备项是共同的：赛事评审标准明确是「可交付性、真实场景中的可验证性」，不是论文不是 demo。我们的离线独立成立 + 六维验证是这个场景下的强项，但需要有一个**能在评审面前当场跑通**的锚点案例——`micro_ecoli_glucose`（AGREE）配合 `micro_thermal_death`（CONTRADICTED）正好是一对：一个证明它能对，一个证明它敢错。
4. 赛事要求「真实场景可验证」，与我们的「离线独立成立」不冲突，但需要显式区分：虚拟实验独立成立是**前提**，真实锚定是**增强**。这个区分在演示时必须讲清楚，否则容易被理解为回避验证。

---

## 五、当前状态

| 门禁 | 结果 |
|---|---|
| VLab negative gate | **38/38**（新增 10 条） |
| TB-Science contract | 53/53 |
| check_public_numbers | **exit 0**，0 MISMATCH / 0 UNSOURCED / 0 OTHER / 0 STALE-FACT / 0 COUNT-DISAGREEMENT |
| check_anchor_quality | 仅 2 项警告（独立性 0.5278 < 0.80、4 饱和家族），均为既有诚实披露 |

账本 82 场景：PASS 79 / MARGINAL 2 / REFUTED 1 / ERROR 0。

六维均值（接线后）：

| fidelity | coverage | anchor | replication | benchmark | sensitivity | overall |
|---|---|---|---|---|---|---|
| 0.9817 | 0.9732 | **0.3329** | 0.4024 | 1.0000 | 0.0488 | 0.6232 |

缺口计数：sensitivity 78 / anchor 76 / replication 49 / fidelity 1 / coverage 1。

engine 模块数 70 → **71**（新增 `scenario_router.py`），已同步 4 处公开页引用。

### 仍未做的（如实标注）

1. **没实现任何联网检索**。brief 生成全程离线。185 条需要真正外部检索的任务书今天仍无自动化——这是诚实边界，不是遗漏。
2. **没有替任何锚点做确认**。bus factor 1 未解决。接线只是让问题暴露得更清楚。
3. **未做复合主张拆解**。一个工作单仍只有一个 `goal.quantity`。带条件、带时间、带比较的主张（「菌株 A 在 30°C/5%O₂ 下 48h 后产量 > 2 g/L」）无法表达为多子主张。需要 schema 升级，属设计决策。
4. **路由器不能替代人做路由决定**。它只给候选，`mode` 不变。
5. **改动未推送**。
