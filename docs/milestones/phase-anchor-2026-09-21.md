# SwarmLabs 阶段锚点 · 2026-09-21

> 生成时间（北京） `2026-09-21 12:16:21+0800` · schema `phase-anchor/1`
>
> 本文件是**机器生成**的。复算：`python scripts/build_phase_anchor.py`；
> 与既有锚点比对：`python scripts/build_phase_anchor.py --verify docs/milestones/phase-anchor-<date>.md`
>
> 用途：冻结「此刻产品是什么样」的形状与数字，让下一阶段能看出**改了什么**、而不是靠记忆。所有数字都带来源指针，可回读核对。

## 0. 指纹（身份）

| 项 | 值 |
|---|---|
| git HEAD | `498c9e0e3fb272c8a3425df5d282625e15ab1a01` |
| 分支 / 未提交文件数 | `main` / 599 |
| **all_digest** | `280fc9735019fb92` |

指纹覆盖的产物（任一字节变化都会改 all_digest）：

| 产物 | sha256[:12] | 已入 git |
|---|---|---|
| `frontend_fixed/reports/index.json` | `90559a7c0d22` | 是 |
| `frontend_fixed/reports/gate_ledger.json` | `d544c8f58e33` | 是 |
| `frontend_fixed/reports/heldout_index.json` | `539e93d4bef4` | 是 |
| `frontend_fixed/reports/wet_lab_anchors.json` | `07b634e9d06f` | 是 |
| `frontend_fixed/reports/scenario_library_density.json` | `bd8237be1060` | 是 |
| `data/trust_surface.json` | `5a32345ce977` | 是 |
| `data/public_facts.json` | `37d7443cd2af` | 是 |
| `data/entity_store.json` | `e51f8431298f` | 是 |
| `data/conformal_calibration.json` | `f6985d9d617e` | 是 |

## 1. 验证账本（核心承诺）

| 指标 | 值 | 来源 |
|---|---|---|
| 已发布 V&V 报告场景数 | **82** | `frontend_fixed/reports/index.json#total` |
| PASS | **79** | `frontend_fixed/reports/index.json#counts.PASS` |
| MARGINAL | **2** | `frontend_fixed/reports/index.json#counts.MARGINAL` |
| REFUTED | **1** | `frontend_fixed/reports/index.json#counts.REFUTED` |
| ERROR | **0** | `frontend_fixed/reports/index.json#counts.ERROR` |
| 门禁阻断 | **1** | `data/gate_ledger.json#n_blocked` |
| 门禁要求人工复核 | **2** | `data/gate_ledger.json#n_human_check_required` |
| rubric 七项全通过 | **35** | `data/trust_surface.json#overall.rubric_full_pass` |
| 跨域场景 | **10** | `data/trust_surface.json#overall.coverage_scope.cross_domain` |
| 微生物场景 | **52** | `data/trust_surface.json#overall.coverage_scope.microbio_registered` |

口径自洽：index.total=82 == public_facts=82 → **一致**

- 注册表分区：cross_domain 10 + microbio 52 + science 20 = 82
- 域标签合计：82 → 等于 场景总数
- **微生物口径警示**：注册表 52 / 域标签 54 —— **两者不一致，对外引用必须指明口径**

## 2. 不确定度与校准（UQ 一等公民）

| 指标 | 值 | 来源 |
|---|---|---|
| 噪声地板（不降红线） | **0.03** | `data/gate_ledger.json#policy.noise_floor` |
| 标称覆盖率 | **0.95** | `data/gate_ledger.json#policy.nominal_coverage` |
| 门禁 ECE（越低越好） | **0.0083** | `data/gate_ledger.json#calibration.ece` |
| 门禁 mean_p_pass | **0.957** | `data/gate_ledger.json#calibration.mean_p_pass` |
| Conformal 覆盖场景数 | **10** | `data/conformal_calibration.json#summary.n_scenarios` |
| Conformal 平均覆盖率 | **0.985** | `data/conformal_calibration.json#summary.mean_coverage_conformal` |

ECE 对标（外部参考，来自账本 `calibration.ece_references`）：
`laya_zero_shot 0.466` / `laya_refit_temp 0.081` / `jev_1_13 0.246` vs 本产品 **0.0083**。

## 3. 双证据链（与真实实验的关系）

| 指标 | 值 | 来源 |
|---|---|---|
| 湿实验锚点总数 | **19** | `frontend_fixed/reports/wet_lab_anchors.json#anchors (len)` |
| 可锚定场景数 | **11** | `frontend_fixed/reports/wet_lab_anchors.json#summary.total` |
| 评级分布 | **AGREE:3 / NEAR:3 / CONTRADICTED:2 / UNANCHORED:3** | `frontend_fixed/reports/wet_lab_anchors.json#summary.grade_counts` |
| 锚点阻断场景 | **['micro_thermal_death', 'microbio_monod']** | `frontend_fixed/reports/wet_lab_anchors.json#summary.blocked` |

> `UNANCHORED` 不是失败：多数场景是量纲/数学测试函数，无物理对应物。

## 4. 留出集与诚实失败

| 指标 | 值 | 来源 |
|---|---|---|
| 鲁棒性留出集（重采样） | **PASS:67 / MARGINAL:5 / REFUTED:10 / ERROR:0** | `frontend_fixed/reports/heldout_index.json#robustness_heldout.counts` |
| 外推压力测试（只外推） | **PASS:3 / MARGINAL:6 / REFUTED:73 / ERROR:0** | `frontend_fixed/reports/heldout_index.json#extrapolation_stress.counts` |
| 留出集版本 | **2026.09.15** | `frontend_fixed/reports/heldout_index.json#version` |

> 外推压力测试的 REFUTED 是**主动公开的失败边界**，不是准确率声明。

## 5. 数据与实体资源

| 指标 | 值 | 来源 |
|---|---|---|
| 校准存储点合计（唯一可辩护数据量口径） | **161** | `data/trust_surface.json#overall.total_store_points` |
| 结构化实体总量 | **157836** | `data/entity_store.json#counts.TOTAL_ENTITIES` |
| 文献 works | **21133** | `data/entity_store.json#counts.works` |
| references | **64917** | `data/entity_store.json#counts.references` |
| engine 模块 | **64** | `data/gate_ledger.json#provenance.engine.n_modules` |

## 6. 域分布

来源：`data/public_facts.json#facts.validated_scenarios_by_domain.value`

| 域 | 场景数 |
|---|---|
| Microorganisms | 54 |
| optimization | 9 |
| cheminformatics | 4 |
| Surrogate Modeling | 3 |
| chemistry | 2 |
| Heat Conduction (Physics-Informed | 1 |
| Biology | 1 |
| LLM | 1 |
| Chemical | 1 |
| Chemistry | 1 |
| biology | 1 |
| finance | 1 |
| neuroscience | 1 |
| ecology | 1 |
| energy | 1 |
| **合计** | **82** |

## 7. 数据集清单（不可再生资产）

| 数据集 | 记录数 | 大小 | 附加维度 | sha256[:12] |
|---|---|---|---|---|
| `qm9_full` | 130831 | 15.2MB | columns=17 | `53ad5679ea3d` |
| `qm9_slim` | 20000 | 5.3MB | - | `88227b0ee430` |
| `tox21_labeled` | 7823 | 2.7MB | labels=12, descriptors=7 | `16e97fb8807f` |
| `toxcast_labeled` | 8578 | 4.1MB | assay_index=617, assays=617 | `028ff538cc33` |
| `toxcast_top30_assays` | 4000 | 3.6MB | assays=30 | `97e81caaed76` |
| `clintox_slim` | 1480 | 0.3MB | - | `4b0fde338027` |
| `sider_slim` | 1427 | 0.1MB | - | `36e44138bc4d` |
| `tox21_slim` | 7823 | 0.5MB | - | `77894a402f0e` |
| `toxcast_slim` | 8578 | 0.6MB | - | `262baa83f7fd` |

模型产物（本地，不入库）：{"present": true, "pkl": 53, "pt": 3, "other": 17, "total_files": 73, "mb": 172.4, "tracked_in_git": false, "rebuild_cmd": "python scripts/train_chem_surrogates.py --all"}

## 8. 代码与页面资源

| 项 | 数量/体量 |
|---|---|
| engine_modules | 64 |
| scripts | 106 |
| frontend_pages | 16 |
| vv_reports_html | 82 |
| reports_json | 82 |
| datasets_reference_benchmarks | 19 文件 / 38.1MB |
| mll_models_local | 73 文件 / 172.4MB |
| entity_shards | 56 文件 / 13.0MB |

## 9. 缺口

无（所有指纹产物与事实指针均可解析）。

---

**如何用它做阶段对比**：下个阶段生成新锚点后，比较 `all_digest`（变了没有）、`facts` 各条的 `value`（哪个数字动了）、`gaps`（缺项填了没有）。
数字变了不算问题——**没解释地变了**才是问题。
