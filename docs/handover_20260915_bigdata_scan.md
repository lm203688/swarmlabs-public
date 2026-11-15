# 大数据与模型包_20260915.zip 扫描评估

**扫描时间**：2026-09-15 21:04 UTC+8
**来源文件**：`C:\Users\xing\Desktop\大数据与模型包_20260915.zip`（127.4 MB，1,506 条目 / 346.2 MB 解压）
**扫描目标**：识别可直接引用到 SwarmLabs 主项目的资产；明确否决项以防架构混乱。

## 1. 包结构总览

| 子树 | 条目 | 内容 |
|---|---:|---|
| `swarmlabs_ml_models/` | 61 | 22 个 scikit-learn/PyTorch 模型（.pkl/.pt）+ 15 个 meta.json |
| `projects-review/` | 1,033 | 6 个子树的项目备份（详见 §4） |
| `backup/` | 402 | 已排除项目（GeneTech/OracleMind/AIShield/DMTL/HealthLens/云盘 Agent）的备份 |
| 根级 | 9 | 5 个数据 JSON + `git-unpushed.bundle` + 3 个 tar.gz |

## 2. 已引用（4 份数据 + 1 份 README）

**目录**：`data/reference_benchmarks/cheminformatics/`（共 1.7 MB）

| 文件 | 规模 | 数据源 | 用途 |
|---|---:|---|---|
| `chembl_lipophilicity_slim.json` | 4,200 分子 / 948 KB | ChEMBL Lipophilicity（Delaney 2014） | GP 引擎 logD 预测基准 |
| `bace_slim.json` | 1,513 分子 / 329 KB | BACE1 pIC50 | 分子对接亲和力预测基准 |
| `freesolv_slim.json` | 642 分子 / 141 KB | FreeSolv（Chodera & Pande 2009） | 水合自由能基准 |
| `esol_delaney_slim.json` | 1,128 分子 / 239 KB | ESOL / Delaney | 水溶性基准 |

**处理动作**：从 26,791 条 `swarmlabs_bio_chem_data.json` 拆分按 source，精简到 7–11 个必需字段（保留 SMILES + 目标 + 结构描述符）；用 `separators=(',',':')` 压缩。

**质量验证**：4 个数据集 SMILES 全部唯一、目标值无缺失。

**为什么值得引用**：
1. 这是**分子性质预测事实上的标准基准**（MoleculeNet 收录）——SwarmLabs 当前 62 场景全是合成动力学/物理/微生物，缺真实化学实验锚定；这四份直接补齐。
2. 上游均有可查 DOI/引用，符合 `engine/wet_lab_anchors.py` 的「多来源文献区间」锚定哲学，不需要再反推参数。
3. 1.7 MB 落在 CF Pages 剩余配额内，可离线评估不占线上路径。

## 3. 评估中但未引用

### 3.1 `swarmlabs_qm9_full.json` / `qm9_data.json`（22.8 + 6.7 MB）

**内容**：130,831（full）/ 20,000（子集）分子 DFT B3LYP/6-31G(2df,p) 计算的 6 个目标（dipole、HOMO、LUMO、gap、alpha、zpve），原子计数特征（n_C/n_H/n_N/n_O/n_F）+ n_atoms。

**为何不引**：
- **DFT 是参考解非实验**，锚定价值低于真实实验数据集（ChEMBL/FreeSolv 等）。
- 22.8 MB 直接超 CF Pages 剩余配额。
- SwarmLabs GP 引擎当前假设是「少量观测点插值 + UQ」，不是「大规模回归」；引入 130K 分子需要新建训练管线。
- 若日后要做「大规模化学属性外推压测」（呼应 `scripts/make_held_out_benchmark.py` 框架 B），可作为外部 held-out 源单独处理。

### 3.2 `swarmlabs_toxcast_data.json`（46.5 MB）

**内容**：8,578 分子 × 39–246 assays 的高维稀疏毒性数据（Tox21/ACEA/ATG 系列）。

**为何不引**：
- 稀疏高维结构（每分子 assay 数不均衡）超出当前引擎单输出 GP 的建模假设。
- 已做精简版（4,000 分子 × 30 assays = 3.8 MB），但作为多输出 UQ 场景仍需专项评估管线，不属于当前「直接引用」范围。
- 保留在临时目录 `_scan_bm/slim_data/toxcast_top30_assays.json` 作为未来多输出 benchmark 候选。

### 3.3 `swarmlabs_training_data_large.json`（3.4 MB，9,536 分子）

**内容**：SMILES + 结构描述符 + `exp_heat_of_formation_kJ_mol` + `exp_boiling_point_C`，但 `source: "Joback基团贡献法"`。

**为何不引**：
- **Joback 基团贡献法是计算方法，不是实验**——虽然命名里带 `exp_` 前缀，但本质是规则算法输出，不能作为真实锚点。
- 与 `engine/wet_lab_anchors.py` 的锚定哲学冲突（该文件强调「多来源文献区间而非点估计」，Joback 单方法单点估计是反向例）。

### 3.4 `swarmlabs_ml_models/`（22 个 .pkl/.pt 模型，共 108 MB）

**内容**：RandomForest 13 树、GradientBoostingRegressor、MPNN GNN v1/v2/v3（PyTorch）等，覆盖 QM9 六目标、logD、hydration、BACE、Tox21 分类等。

**为何不引**：
- **架构冲突**：SwarmLabs 主引擎是 GP（Gaussian Process）+ UQ（`engine/surrogate.py`）；引入 RF/GBR/MPNN 会造成「三套代理模型」的维护灾难。
- **UQ 无法保证**：RF/GBR 只能事后做 LOO/残差估计，不具备 GP 原生 σ 输出，与 `verification_gate.py` 的覆盖率判定（cov≥0.90 单侧下界）不兼容。
- `meta.json` 里的 MAE 数字（如 dipole MAE=0.95、logD MAE=0.689）**没有置信区间**、验证集划分方式（"5-fold CV"/"80/20"）**没有种子**、没有 held-out 验证记录——**不能作为验证证据**。
- 训练数据规模可疑：`meta.json` 声称 "271 分子 NIST WebBook"、`qm9_meta.json` 声称 "20,000 分子"、`qm9_full_meta.json` 声称 "130,831 分子"，同时并存三个不同规模的 QM9 训练，来源不清晰。

### 3.5 `swarmlabs_ml_models/engine_models/`（8 个材料科学模型）

**内容**：`ammonia / adsorption / combustion / corrosion / perovskite / polymer / battery / photocatalysis` 各一个 GBR，100 训练样本，"物理方程基线 + ML 残差校正"。

**为什么有价值但不引**：
- **思路正确**（Physics-informed ML：`physics_baseline + residual_correction`）——SwarmLabs 引擎本质也是这个思路（GP 拟合 oracle 残差），所以架构上吻合。
- **训练规模致命缺陷**：`training_samples=100`，且未见物理基线是真实模型还是合成函数；`physics_mae / corrected_mae / improvement_pct` 都是自报指标，无种子无 CI。
- **建议**：把「physics + residual」的思路在 `engine/flywheel.py` 里正式化，但**不采用这批现成模型**。

## 4. 明确否决（不引用）

| 子树/文件 | 大小 | 否决理由 |
|---|---:|---|
| `projects-review/swarm-research/swarm-backup/` | 500+ KB | 老 Swarm 架构（chemistry_bee / literature_bee / verification_bee + scheduler.py + credits.py）——**与当前 SwarmLabs 完全不同工程**，属于废弃的多 Agent 科研管线；引入会污染当前引擎 |
| `projects-review/agent-trust/` | 89 条目 | Agent 信任协议项目（HN 推广 + 微信小程序 + agent-trust-protocol-backup）——**与 SwarmLabs 无关**，与 AIShield 有部分重叠但代码不同 |
| `projects-review/all-projects/extracted/roboparts/` | 613 条目 | RoboParts 项目——**用户已明确排除** |
| `projects-review/all-projects/extracted/AutoResearchPro/` | 2,920 B | 只有 README 规划，无代码实现 |
| `projects-review/all-projects/extracted/agent-swarm/` | ~12 KB | 本地 LLM 路由（gemma4/deepseek/qwen3-coder），与 SwarmLabs 引擎无关 |
| `projects-review/all-projects/extracted/TrashBin-Hardware/`、`ShopFlow-ImageProcessor/` | 各 3 条目 | 与当前范围无关 |
| `backup/` 全部 | 402 条目 | GeneTech 14 站、OracleMind、AIShield、DMTL、云盘 Agent、HealthLens——**全部是排除项目**的备份 |
| `backup/03_Φ£éτ╛ñτºæτáöswarmlabs/` | 12 条目 | 名字带 swarmlabs 但实际是备份子目录（未逐条核查，规模小不影响决策） |
| `genetech-13sites-full.tar.gz` / `kb-ecosystem-full-*.tar.gz` | 19.8 MB | GeneTech 站点包 + 知识库生态——**排除项目** |
| `git-unpushed.bundle` | 22.1 MB | 未推 git bundle——需用户明确来源项目；跨项目 bundle 不该静默引入 |
| `swarmlabs_uncertainty.py`（若存在于 projects-review） | — | 之前扫描确认过：假 UQ（`margin=1.96*std_err*|y|`），不引 |
| `market-analysis-cn` / 假冒付费外壳类内容 | — | 之前扫描确认过：合规风险 |

## 5. 遗留价值（值得单独跟进）

1. **QM9 全集（130,831 分子）** 是「大规模分子性质外推」的天然 held-out 源。可后续做「SwarmLabs GP 引擎 vs DFT 参考解」的诚实压测，输出到 `swarmlabs-benchmarks` 仓库。数据仍在 `_scan_bm/tmp_data/swarmlabs_qm9_full.json`（22.8 MB）。
2. **ToxCast 多输出 UQ 场景**（4,000 分子 × 30 assays）是「多输出 GP + 覆盖率联合保证」的天然场景，与 `engine/convex-body-validation-aggregator` 技能思路吻合。数据在 `_scan_bm/slim_data/toxcast_top30_assays.json`（3.8 MB）。
3. **engine_models 的 physics+residual 范式** 值得作为设计模式写入 `docs/` 但不用现成模型。

## 6. 落地清单

| 动作 | 状态 |
|---|---|
| 4 个化学基准数据落 `data/reference_benchmarks/cheminformatics/` | ✅ |
| README 说明来源、质量、未纳入原因 | ✅ |
| 本评估文档 `docs/handover_20260915_bigdata_scan.md` | ✅ |
| CF Pages 部署（暂不部署，只落本地项目） | ⏸ 待用户决定是否上线 |
| `scripts/build_chem_report.py` 新增化学基准 V&V 报告 | ⏳ 建议下一步做 |
| QM9 全集独立 held-out benchmark | ⏳ 建议单独任务 |

## 7. 与上一次 zip 扫描的对照

上一个包 `核心工作成果包_20260915.zip（9.zip`（9.46 MB，2,731 条目）主要是**skills + 旧 swarmlabs 建模谱系**；本次包（127 MB，1,506 条目）是**大数据 + 训练模型**。两者互补：
- 上包价值：方法论技能（competitive-analysispro / in-depth-research 已引用）
- 本包价值：真实实验数据锚点（本次 4 个基准已引用）

两次扫描合计**明确否决**的重复项：老 swarmlabs 引擎脚本、伪造验证数据、假 UQ 代码、排除项目代码——口径一致。
