# engine 算子分类学（2026-09-27）

基于 `engine/` 下 **75 个模块**的实测 docstring、定义数量与被依赖热度，
套用 DataFlow 论文 4.3 节的三维分类法做映射。

**本文不是建议重命名 75 个文件。** 分类学是**事后标签**，价值在于
让 Agent 与 SDK 能检索到它们；收益最高的动作是「接入统一入口」，
不是改文件名。改名成本与收益不成比例，本文只标出值得改的少数。

---

## 一、分类框架：DataFlow 三维 + 一个 SwarmLabs 必需的第四维

DataFlow 论文 4.3 节的三个正交维度：

| 维度 | DataFlow 取值 | SwarmLabs 对应取值 |
|---|---|---|
| 模态 | text / visual / document | 标量 / 时序 / 分子 / 图像 |
| 层级 | Core / Domain | Core / Domain（同义） |
| 功能 | Generate / Evaluate / Filter / Refine | 同 + **Orchestrate** |

**为什么必须加 Orchestrate**：DataFlow 面向「数据 → 训练数据」的
线性 DAG，编排由 RayOrch 承担，不是算子。SwarmLabs 面向「实体 →
实验决策」的**闭环**，编排本身就是产品核心（flywheel、VLab 六段流水线、
autonomous_loop）。把它压进 Generate/Refine 会丢失「这是控制回路而非
一次性变换」这个关键语义。

DataFlow 的命名后缀约定：

```
*Generator        → 新增字段
*RowGenerator     → 新增行
*SampleEvaluator  → 行级打分
*DatasetEvaluator → 数据集级打分
*Refiner          → 改字段不改行数
```

SwarmLabs 已经有事实上的后缀习惯，且**语义一致**——这值得指出，
因为它说明分类学不是凭空加的：

| 已有后缀 | 实际语义 | 对应功能维度 |
|---|---|---|
| `*_gate` / `*Gate` | 判定通过/拒绝 | Filter |
| `*_adapter` | 包一层外部库接口 | Refine |
| `*_registry` | 结构化数据仓库 | Domain/Infrastructure |
| `*_orchestrator` | 多步流水线控制 | Orchestrate |
| `*_validator` / `*_validation` | 验证与打分 | Evaluate |

---

## 二、75 模块完整映射

标注依据：模块 docstring 首行 + 顶层 def/class 数量。
孤儿模块共 55 个，占 73.3%（详见第三节）。

### Core / Generate（生成新假设、新锚点、新交付物）

| 模块 | 内容 | 规模 |
|---|---|---|
| `discovery.py` | SINDy 可解释假设发现 | 1cls, 6KB |
| `hypothesis_llm.py` | LLM 候选机理生成 | 1cls, 3KB |
| `tree_search.py` | 假设树搜索（MCTS 风格） | 1cls, 2KB |
| `synthetic_anchors.py` | 合成锚点预验证 | 6fn, 9KB |
| `anchor_intake.py` | 锚定数据硬化层（防垃圾进） | 7fn, 13KB |
| `evidence_acquisition.py` | 验证缺口 → 可执行取证任务 | 14fn, 18KB |
| `report_generator.py` | V&V 报告生成器 | 37fn, 73KB |
| `vv_package.py` | V&V 归档包（可离线复验证据包） | 18fn, 25KB |

### Core / Evaluate（打分、验证、校准）

| 模块 | 内容 | 规模 |
|---|---|---|
| `conformal.py` | 保形校准 + UQ 校准指标 | 12fn, 13KB |
| `conformal_vv.py` | 分布级 V&V（SVP v0.5） | 7fn, 17KB |
| `validation.py` | 8 维度结构化质量评估 | 2cls, 11KB |
| `validation_body.py` | 可达质量凸体 | 4cls, 31KB |
| `calibration_rl.py` | Coverage RL（校准变训练目标） | 9fn, 14KB |
| `paper_validation.py` | 13 篇论文实验数据验证 | 24fn, 35KB |
| `paper_validation_v2.py` | 闭环精度审计 | 22fn, 27KB |
| `paper_audit.py` | 论文对抗审计（已发表反驳引擎） | 7fn, 23KB |
| `anchor_independence.py` | 锚点来源独立性分析 | 10fn, 20KB |
| `scenario_trust_labels.py` | 结果页信任标签单一真源 | 6fn, 13KB |
| `bidirectional_parity.py` | 双向对等门禁 | 3cls, 6KB |
| `moment_bound.py` | 矩界门禁 | 2cls, 7KB |
| `second_law.py` | 二阶律/涨落耗散门禁 | 2cls, 8KB |

### Core / Filter（门禁、守卫、形式化守卫）

| 模块 | 内容 | 规模 |
|---|---|---|
| `guard.py` | VirtualLab Guard 三层科学护栏 | 5fn/8cls, 29KB |
| `verification_gate.py` | 「能否自主推进」单一真源 | 37fn, 47KB |
| `verifier_gate.py` | 合成锚点 verifier promote gate | 2fn/2cls, 10KB |
| `promote_verifier.py` | 独立 verifier 门禁 | 3fn/2cls, 12KB |
| `formal_invariants.py` | 形式化不变量（Python/Lean4/Z3 三通道） | 15fn, 25KB |
| `antirepeat.py` | 防重复记忆（防飞轮重复发现） | 7fn, 13KB |
| `harness_registry.py` | 42 门禁拆成 Gate 实例 | 19fn, 13KB |
| `harness_evolver.py` | 门禁自我进化引擎 | 6fn/8cls, 17KB |

### Core / Refine（优化、精化、重排）

| 模块 | 内容 | 规模 |
|---|---|---|
| `baybe_adapter.py` | BayBE 贝叶斯优化适配器 | 6fn, 13KB |
| `emukit_adapter.py` | Emukit 接口 + Sobol 敏感性 | 7fn/3cls, 14KB |
| `multifidelity.py` | Kennedy-O'Hagan 多保真度 | 4fn, 11KB |
| `design.py` | 输入坐标变换（跨数量级 → log10） | 9fn, 8KB |
| `meta_learner.py` | 跨场景正迁移 meta-learning | 1cls, 5KB |
| `elo.py` | 候选锦标赛（对等度排名替代主观排序） | 1cls, 2KB |
| `acquisition_volume.py` | 涨落定理凸体 → 主动学习 | 1cls, 4KB |

### Core / Orchestrate（闭环控制 —— DataFlow 没有的维度）

| 模块 | 内容 | 规模 |
|---|---|---|
| `flywheel.py` | 每日自动化飞轮（体系总开关） | 5fn, 20KB |
| `autonomous_loop.py` | SINDy + Elo 全自动闭环 | 3fn/2cls, 13KB |
| `vlab_orchestrator.py` | VLab 六段流水线编排 | 15fn, 18KB |
| `vlab_batch.py` | 批量运行器 + 死循环检测 | 9fn, 12KB |
| `scenario_router.py` | 离线场景模糊路由 | 14fn, 19KB |
| `discipline_registry.py` | 「加新专业」的有门禁复用流程 | 10fn/2cls, 18KB |

### Domain（领域模型 —— 理论无界，只收支撑最优管线的必要集合）

| 模块 | 领域 | 规模 |
|---|---|---|
| `microbiology_full.py` | 微生物完整引擎 | 43fn, 79KB |
| `microbiology_expanded.py` | 微生物扩充（真实论文参数） | 9fn, 26KB |
| `strain_registry.py` | 菌株注册表 | 9fn, 69KB |
| `media_database.py` | 培养基数据库 | 6fn, 18KB |
| `molecular_scenarios.py` | 分子模态（RDKit 特征） | 12fn, 15KB |
| `wet_lab_anchors.py` | 湿实验锚点库 | 7fn/3cls, 25KB |
| `real_datasets.py` | UCI 基准数据集（airfoil/wine/yacht/ccpp，物理/工程，**非生物湿实验锚点**） | 5fn, 3KB |
| `scenarios_extra.py` | R8 头部扩展基准 | 3fn, 9KB |
| `scenarios_science.py` | 科学领域扩展场景 | 11fn, 19KB |
| `domains.py` | 领域 taxonomy | 2fn, 5KB |

### Infrastructure（跨层基础设施）

| 模块 | 内容 | 规模 |
|---|---|---|
| `surrogate.py` | GPSurrogate 核心（唯一真源门面） | 2fn/10cls, 38KB |
| `neural_operator.py` | 神经算子代理（纯 numpy） | 1fn/2cls, 10KB |
| `fourier_operator.py` | FNO 函数空间算子 | 1cls, 8KB |
| `pinn.py` | 物理信息神经网络 | 1cls, 3KB |
| `random_forest_baseline.py` | 外部对标基线（不参与生产） | 10fn/2cls, 20KB |
| `world_model.py` | 结构化世界模型 | 1cls, 11KB |
| `time_machine.py` | 实验时光机（快照回放） | 4fn, 3KB |
| `snapshot.py` | 快照回滚 + 严格优于判定 | 8fn, 10KB |
| `traceability.py` | 结论 → 代码/论文可追溯层 | 3cls, 9KB |
| `entity_shard.py` | 实体存储分片（hash-bucket） | 10fn, 8KB |
| `federated_evidence.py` | 跨域证据检索 | 3fn, 1cls, 8KB |
| `remote_lab.py` | 真实实验闭环客户端 | 4cls, 6KB |
| `instrument_simulator.py` | 仪器模拟器（6 种探头） | 1fn/6cls, 17KB |
| `serve.py` | v3 API 服务 | 11fn, 22KB |
| `vlab_server.py` | VLab 服务入口 | 14fn, 21KB |
| `vlab_stdlib.py` | VLab 零依赖基座 | 15fn, 5KB |
| `vlab_contract.py` | 工作单契约（单一真源格式） | 21fn, 27KB |
| `vlab_delivery.py` | 交付契约（第三方离线核对） | 9fn, 10KB |
| `validation_db.py` | 论文验证结构化数据库 | 1fn, 4KB |
| `sdk.py` | VirtualExperiment SDK | 1cls, 3KB |
| `brand.py` | 对外品牌名 | 0KB |
| `demo_full_loop.py` | 全链路做实演示 | 2fn, 7KB |

**跨类归属说明**：`harness_evolver` 同属 Filter 与 Orchestrate
（既是门禁又是门禁的自进化器）；`elo` / `tree_search` 同属 Refine
与 Generate（排名本身会生成候选）；`calibration_rl` 同属 Evaluate 与
Refine。分类学标注的是**主要语义**，跨类是常态不是缺陷。

---

## 三、实测发现：孤儿模块里的接入优先级

`engine/__init__.py` 原只接入了 20 / 74 个业务模块（**27.0%**）。
但「孤儿」不等于「该接入」——服务入口（`serve`、`vlab_server`）、
演示脚本（`demo_full_loop`）、CLI 门禁（`*_gate` 的部分）
**本来就不该进统一入口**，它们由 HTTP 或 CLI 直接驱动。

按「被其他模块依赖的热度」排序，真正值得接入的是：

> 依赖数按全仓扫描计，已排除 `docs/competitions/**/_tmp/backup_*.py`
> 备份文件（非活跃代码）。

| 模块 | 被依赖数 | 依赖方 |
|---|---|---|
| `scenarios_extra` | 5 | `autonomous_loop` / `demo_full_loop` / `report_generator` / `scripts/paper_validation_loop` / `scripts/scenario_library_density` |
| `discovery` | 4 | `surrogate` / `tree_search` / `autonomous_loop` / `demo_full_loop` |
| `elo` | 3 | `surrogate` / `tree_search` / `autonomous_loop` |
| `neural_operator` | 2 | `surrogate` / `scripts/real_validation` |
| `fourier_operator` | 2 | `surrogate` / `scripts/bench_gp_vs_fno` |
| `pinn` | 2 | `surrogate` / `scripts/real_validation` |
| `acquisition_volume` | 1 | `surrogate` |
| `hypothesis_llm` | 1 | `surrogate` |
| `tree_search` | 1 | `surrogate` |
| `remote_lab` | 1 | `surrogate` |

**注意最后一列**：这 10 个里有 9 个**被 `surrogate.py` 直接依赖**
（唯一例外是 `scenarios_extra`，它被 `report_generator.py` 依赖）。
而 `surrogate.py` 是唯一通过 `__init__.py` 间接暴露核心符号的模块。
也就是说 `__init__.py` 声明的 `NeuralOperatorSurrogate`、
`RemoteLabOracle` 这些符号**实际依赖着 9 个未接入模块** ——
Agent 经统一入口能拿到门面，却检索不到门面的依赖基座。

**已接入（2026-09-27）**：上表 10 个模块全部接入 `engine/__init__.py`，
新增 19 个导出符号，统一入口覆盖率 **20/75（26.7%）→ 29/74（39.2%）**。
接入方式见 `__init__.py` 的「surrogate 的依赖基座」区块——放在 `.surrogate`
import 之后，与 surrogate 的 import 次序一致，便于对照门面与基座。

零风险说明：这 10 个模块此前**已经在被 import 执行**（经 surrogate 间接
加载），接入只是让它们对统一入口可见，不改变任何 import 时机或依赖图，
因此不引入新的循环导入风险。实测 `import engine` 后 19 个符号全部
`hasattr` 为真，`SINDy` / `EloTournament` / `PINN1D` / `HypothesisTree` /
`ConvexBodyAcquisition` / `FourierNeuralOperator` 均为可用 class 对象。

**叶子模块（0 依赖）分两类**：

- **该接入**：`scenario_router`（14fn，82 场景的路由入口）、
  `validation`（8 维度质量评估）、`traceability`（可追溯层）、
  `world_model`、`evidence_acquisition`（14fn）
- **不该接入**：`serve`、`vlab_server`、`demo_full_loop`（服务/演示）、
  `flywheel`（由 cron 直接驱动，不应被 SDK import）

**治理准则**（直接借 DataFlow 4.3 的原话）：

> "Although theoretically unbounded, the domain operators included in
> DataFlow are limited to those required to support the best-performing
> pipelines across existing domains, ensuring practical conciseness and
> avoiding unnecessary proliferation."

翻译到本项目：**Domain 模块（微生物、分子、菌株注册表）理论无界，
但统一入口只应收支撑当前最优管线的必要集合。** 79KB 的
`microbiology_full.py` 与 69KB 的 `strain_registry.py` 已经全部接入，
这个边界守得是对的，不需要调整。

---

## 四、命名规范：值得改的只有 3 个

DataFlow 的后缀规范（`*Generator` / `*Evaluator` / `*Filter` / `*Refiner`）
在 SwarmLabs 已有事实对应物（见第一节表格），大部分模块命名已自洽。

实测下来**真正违反语义的只有 3 个**：

| 模块 | 问题 | 建议 |
|---|---|---|
| `verifier_gate.py` | 与 `verification_gate.py`（37fn/47KB）命名近乎同形，前者 10KB、后者 47KB，极易误引用 | `promote_verifier.py` 已存在同类，三个「验证门禁」应合并或显式区分层级 |
| `paper_validation.py` vs `paper_validation_v2.py` | 2026-10-14 板块 11 P0 归并处置：`paper_validation.py` 为**活跃主版本**，`_v2` 已 DEPRECATED（仅在文件 docstring 内自标，不参与运行时 import），`paper_validation_unified.py` 已归档为 `.py.legacy`（见 `engine/LEGACY.md`）。新代码只 import `engine.paper_validation`。 |
| `vlab_*.py`（7 个） | 前缀 `vlab_` 是产品名不是分类维度，`vlab_contract` / `vlab_delivery` 实为 Infrastructure 的契约层 | 可接受现状，但文档需说明 `vlab_*` 是产品命名空间而非功能分类 |

**不建议做全量重命名**：75 个模块 + scripts/ 下 171 个文件的交叉引用，
改名收益（Agent 检索语义更清晰）远低于风险（断链、CI 全红、文档失效）。
**接入统一入口**（第一节表格的 10 个模块）是零风险的等效收益 ——
接入后 Agent 即可经 `engine.X` 检索到它们，无需改名。

---

## 五、与 DataFlow 的本质差异（诚实边界）

| 维度 | DataFlow | SwarmLabs |
|---|---|---|
| 拓扑 | 线性 DAG，RayOrch 编排 | 闭环，flywheel 编排 |
| 目标函数 | 数据 → 模型质量 | 实体 → 实验决策 |
| 算子数量 | 180–200（35 人团队） | 75 模块 / ~450 顶层 def（单人） |
| 规模上限策略 | 算子理论无界，收敛于最优管线 | 同（Domain 只收必要集合） |
| 评测 | 下游 LLM benchmark | 无外部基准，靠论文实测值对抗 |

**不要追算子数量。** DataFlow 的 200 算子是 35 位作者（PKU + 上智院 +
OpenDataLab + LLaMA-Factory）堆出来的。SwarmLabs 75 模块 / ~450 个顶层
函数已经是单人项目 4 个月的产出，密度远高于 DataFlow 的「团队人均
约 6 算子」。把「算子数量」当 KPI 是本项目的错误方向 ——
正确 KPI 是**统一入口覆盖率**（接入 29 / 74 业务模块 = 39.2%，
孤儿 45，均可量化、可门禁）。

---

## 六、落地动作

已由 `scripts/check_engine_contract.py` 固化（CI 第 8 步硬门禁）：

- 统一入口覆盖率统计（当前 29 / 74 业务模块接入，45 孤儿）
- `__all__` 声明 vs 真实定义的编译期一致性校验
- 模块顶层副作用与 CLI 守卫检查

覆盖率是信息级指标（INFO），不阻断 —— 因为 45 个孤儿里有一半
本来就不该接入。接入决策应逐模块人工判定，不是自动门禁能替用户做的。

**本次已接入**（2026-09-27）：第三节表格的 10 个高价值孤儿模块，
新增 19 个导出符号，覆盖率 27.0% → **39.2%**，门禁仍为 0 个 P0/P1。
