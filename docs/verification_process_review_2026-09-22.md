# 验证过程完备性审计 —— 拆解链与情报收集链（2026-09-22）

审计对象：「把某个事项通过实验验证」这条主链是否理顺。

结论先行：**验证过程已理顺，拆解链部分理顺，情报收集链未理顺**。三者不是一句话，是三个不同判据。

---

## 一、判据

判断不是靠读文档，是靠看代码是否能拦住错误：

| 链 | 判据 | 判据含义 |
|---|---|---|
| 验证过程 | 错的东西写不进来 | 红线在数据结构上不可表达，而非靠约定 |
| 拆解链 | 路由成功后是否真的回答了被问的问题 | 场景 PASS 不等于场景答了那个问题 |
| 情报收集链 | 缺口能否变成可执行任务 | 诊断说「缺锚定」之后有没有人知道该查什么 |

---

## 二、验证过程：理顺了

`engine/vlab_contract.py` 的 6 阶段 `intake → scope → build → verify → corroborate → deliver`，红线是**拒收**不是提示：

- `corroboration.as_input=true` → `ORDER_INVERTED` 直接拒收。真实数据作前置输入在 schema 上写不出来。
- `assert_corroboration_post_hoc` 双保险：白名单 + 数据摘要碰撞检测，防止换个键名把真实观测偷喂进 build。
- `anchor_intake.screen_candidate` 三档判定，**不存在自动采纳路径**：机械检查全过仍需具名确认人，否则最多 `NEEDS_HUMAN`。
- 交付物篡改必被判 `TAMPERED`（N15 验证：改一个字段 hard_verdict 从 PASS 变 MARGINAL 即被抓）。

自测 28/28 通过，其中 22 条是否定性测试——专测门禁会不会不响。这一层不需要加强。

## 三、拆解链：部分理顺

**已理顺**：`goal.quantity` + `goal.unit` 为必填，`unit` 缺失即拒收（因为锚定筛选需要量纲）。`design_space.variables` 的 lo/hi 倒置会被拦。

**审计前发现的缺口（已修）**：`_scope` 只做 discipline → scenario_key 的字符串路由。路由成功只意味着「场景在账本里」，**不意味着它回答了工作单提出的问题**。一个标 PASS 的场景完全可能产出的根本不是客户要验证的量——那样交付出去的报告看起来完整、实际答非所问，比明确拒绝更糟。

更硬的证据是：`DisciplineSpec` 之前**没有声明输出量的字段**。也就是说「验证生物量产量」的工作单可以被路由到一个不产出生物量产量的场景，而没有任何东西会拦下。

**修了什么**：

1. `DisciplineSpec` 新增 `output_quantity` / `output_unit`。只给能辩护的那一条（Microbiology → `biomass concentration` / `g/L`）填了实值，其余 9 个专业**留空**——留空是诚实标注，不是省略，让警告真实发声。
2. `vlab_contract.goal_quantity_binding()` 新增绑定校验，四态返回（键集固定，调用方不用写 `if in`）：

| 状态 | 触发条件 | 处置 |
|---|---|---|
| `MATCH` | 量名与单位量纲都一致 | 放行 |
| `MISMATCH` | 单位量纲不兼容 | **拒收** |
| `WARN` | 量名写法不同但量纲一致 | 告警交人工核 |
| `UNVERIFIABLE` | 专业未声明输出量 | 告警，不假装绑定过 |

这里有个容易做错的选择：**量名不匹配当初设计成拒收，结果误杀了合法请求**。契约规范示例用 `biomass yield`，声明值是 `biomass concentration`，两者指同一个量。量名是自由文本，写法差异不该硬拒；真正机器可判的是**单位量纲**（物理上产不产得出来）。改成分级后 mg/L 与 g/L 被正确识别为同量纲放行，`kmol/h` 被正确拒收。

3. 顺带修了一个既有不一致：`known_disciplines()` 只读 taxonomy，导致提交 `Microbiology` 被误报 `unknown_discipline`，而专业注册表明明能解析它。两份「已知专业」清单不一致会让 intake 输出假告警。已合并 taxonomy ∪ 注册表。

**新增 4 条门禁测试（N17–N20）**锁住这条行为，防止静默腐化。

**仍未做**（诚实标注）：

- 复合主张拆解。当前一个工作单只有一个 `goal.quantity`。「菌株 A 在 30°C/5%O₂ 下 48h 后产量 > 2 g/L」这类带条件、带时间、带比较的主张，无法表达为多子主张。需要 schema 升级。
- 无稳定 claim id。`work_order_id` 是整体内容哈希，无法指向「这份工作单的第 2 条主张」，因此工作单无法跨次运行累积发现。
- 无模糊路由。82 个场景里，调用方不知道确切 key 就进不去，只能落到 `discipline_onboarding_required`。这是最大的可用性缺口——76 个场景事实上只对知道 key 的人可达。

## 四、情报收集链：未理顺

**这是整条主链最脆弱的一环，也是 anchor 维度卡在 0.300 的机制原因。**

### 4.1 三段各管一段，中间是断的

```
ablation_plan      报告缺口（anchor / replication / sensitivity）
    ↓   **断在这里**
anchor_intake      筛提交上来的候选
wet_lab_anchors    19 条人工策展锚点
```

诊断永远在说「你缺锚定」，但没有任何东西说明：要查哪个量、哪个单位、避开哪些已饱和的来源家族、这条得过哪几道筛子才算闭环。

### 4.2 审计前必须澄清的两个误判

先纠正我最初的假设。仓库里**不是**完全没有情报相关代码：

- `engine/federated_evidence.py` —— 但查的是本地 13.7 万条结构化实体索引（`entity_store.json`），是离线语料查找，**不是活文献检索**。
- `engine/remote_lab.py` —— 是提交真实实验的 REST 客户端（OpenTrons/HPLC 协议 + 本地模拟器），**是跑实验不是采集文献锚点**。
- `engine/hypothesis_llm.py` —— 调 LLM 生成候选机理符号律，**是假设生成不是文献检索**，且依赖商业 LLM 网关。

精确结论：**仓库里没有任何代码会从 arXiv/Crossref/PubMed 检索出一个带引用的实测数值。** 这就是准确表述——不是「零情报收集」，而是「零外部文献检索产出可引用锚点」。

### 4.3 其余缺口

| 缺口 | 现状 | 后果 |
|---|---|---|
| 主动采集 | 无。所有锚点必须已由人知道出处后手工提交 | anchor 0.300，0/80 满分 |
| 缺口 → 任务翻译 | 无。`ablation_plan` 输出 `gaps: ["anchor"]` 后没有任何后续动作 | 诊断永远在转圈 |
| `plausible_range` 来源 | 可选，无生成器 | 缺失时筛选只剩量纲一层，大幅弱化 |
| `confirmed_by` | 裸字符串，无花名册、无双人规则 | 19 条锚点实质同一人确认 = bus factor 1 |
| `published` 字段 | 手写散文，非结构化文献记录 | 80 个场景 60 个无此字段，因为没有工具去写 |
| 撤稿检查 | `anchor_independence` 报 `retraction_checked: false` | 被撤来源会静默污染所有依赖它的锚点 |

## 五、加强件：证据采集简报生成器

新建 `engine/evidence_acquisition.py`，把缺口翻译成可执行取证任务。

### 设计纪律（写进代码，不靠约定）

1. **不编造检索结果**。模块从不声称「找到了某文献」，只生成任务书。产物中不存在任何 `retrieved_*` / `found_*` 字段，也没有入口能写进来。
2. **不降级筛子**。brief 里声明的 `screening_gate` 与 `anchor_intake.screen_candidate` 判据一一对应，`auto_accept_path` 恒为 False。
3. **饱和家族要避开**。`anchor_independence` 报独立性比率 0.5278 < 0.80、6 个聚类被多锚点共用；brief 据此列出应避开的家族，防止新锚点只是把同一条来源再数一次。

不联网、不调外部服务——与内核「离线独立成立」是同一条纪律。

### 实跑结果

```
scenarios assessed   : 82
briefs               : 211
  gap anchor         : 82
  gap replication    : 49
  gap sensitivity    : 78
  gap coverage       : 1
  gap fidelity       : 1
promotable now       : 21     ← 素材已在账本里，只差过筛 + 具名确认
offline executable   : 127    ← 不需要任何外部情报，纯计算
needs external intel : 190    ← 需要真正的外部检索，目前完全无自动化
source families      : 32  (saturated: 4)
anchor mean/at-full  : 0.300 / 0
```

注意 anchor 一栏：**82 个场景全部有 anchor 缺口，0 个满分**。而 `anchor_independence` 同时报 11 个场景可锚定（AGREE 3 / NEAR 3 / CONTRADICTED 2 / UNANCHORED 3）——两者不矛盾，`ablation_plan` 的 anchor 维度比「有一条 AGREE 锚点」要求更严。这个判据差异本身值得单独核一遍，本轮未展开。

### 最值得注意的一个数

**21 条 brief 属于 `promote_existing`**——账本里已经有可读的文献参数（合成锚点 18 条，CLEAN 9 条），只差过一遍 `anchor_intake` 的筛子 + 一个具名确认人。这是 anchor 维度从 0.300 往上抬的**最便宜路径**，成本是人工确认而非情报采集。

**127 条离线可执行**——replication（49）和 sensitivity（78）这两条缺口根本不需要外部情报，是纯计算。这意味着六维里的三轴中有两轴（replication 0.412、sensitivity 0.050）可以在零外部情报投入下独立抬升。

**剩下 190 条需要真正的外部检索**——这部分今天没有任何自动化。这是诚实的边界。

### 每条 anchor brief 的内容

```
target_quantity / target_unit      要测什么（抽不到就留空，不猜）
ledger_published_text              账本里已有的散文，供人核对
existing_anchors_on_scenario       是补强还是从零
avoid_source_families              应避开的饱和家族（4 个）
screening_gate.layers              verifiability → dimension → plausible_range → human_confirmation
screening_gate.auto_accept_path    False
plausible_range_request            是否缺合理区间（缺失会让筛选大幅弱化）
retraction_check_request           是否需要撤稿检查（全库当前 false）
not_evidence                       明确标注 brief ≠ evidence
```

### 接入门禁

`scripts/check_anchor_quality.py` 新增第 3 节，守一条会静默腐化的线：有人为了让计划看起来「有产出」而往 brief 里塞 `retrieved_value` / `found_in` 之类字段，任务书就变成了伪造的结论。该类字段一旦出现即判失败。

实跑结果：`PASS: 锚点质量检查通过（有警告）`，exit 0，2 项警告均为既有的诚实披露（独立性比率偏低、4 个饱和家族）。

### 修的一个抽取缺陷

正则 `[A-Za-z]` 不认希腊字母，把 `μmax` 截成了 `max`。量名错了会让取证任务去查一个不存在的量。已扩展到 `\u0370-\u03ff`（希腊字母区），`micro_*` 场景的目标量现在正确解析为 `μmax` / `h⁻¹`、`S_f` / `g/L`。

---

## 六、诚实边界

本次加强件**没有**做的事：

1. 没有实现任何联网检索。简报生成是离线完成的，符合自治红线（不引商业 LLM 密钥）。真正的检索执行器需要另做，且涉及联网，属下一轮决策。
2. 没有替任何锚点做确认。`confirmed_by` 仍需具名人——19 条锚点的 bus factor 1 问题**没有解决**，只是被暴露得更清楚。
3. 没有改拆解决的复合主张表达。那需要 schema 升级，属设计决策，不宜单方面推进。
4. `target_quantity` 是尽力抽取，不是权威声明。留空就是留空，不会编一个量名出来。

---

## 附：本轮改动清单

| 文件 | 改动 |
|---|---|
| `engine/evidence_acquisition.py` | 新建。证据采集简报生成器 |
| `engine/discipline_registry.py` | `DisciplineSpec` 新增 `output_quantity` / `output_unit` / `has_output_declared`；Microbiology 种子填实 |
| `engine/vlab_contract.py` | 新增 `goal_quantity_binding()` 四态校验；`known_disciplines()` 合并 taxonomy ∪ 注册表；`_load_sibling()` 修 dataclasses 注册坑；规范示例量名与声明对齐 |
| `scripts/check_vlab_gates.py` | 新增 N17–N20 四条绑定门禁测试（24 → 28 条） |
| `scripts/check_anchor_quality.py` | 新增证据采集简报诚实性守卫（4 项断言） |
| `scripts/check_public_numbers.py` | 修合计行谎报：`SCRIPT-CLAIM` 等 kind 会让门禁红但不出现在合计里，此前能打印 `0/0/0/0` 却 exit 1 |
| `data/evidence_acquisition_plan.json` | 新建产物，211 条 brief |
| `contributions/terminal-bench-science/.../task.toml` | 署名字段：`author_organization` 标注未注册实体、补 `author_profile`、`relevant_experience` 换成可核验数字、`conflicts_of_interest` 去掉不实「employee of a company」表述 |
| `frontend_fixed/{index,terms,lab}.html`、`frontend_fixed/blog/geo-research-knowledge-graph.html` | 模块数 67 → 70（新增模块后同步；共 5 处，含 `<script>` 与 JSON-LD 各一处） |
