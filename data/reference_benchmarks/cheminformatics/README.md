# Cheminformatics 参考基准数据集

**来源**：`大数据与模型包_20260915.zip` → `swarmlabs_bio_chem_data.json`（26,791 条记录，8 个子集）→ 拆分 + 精简字段。

**引入日期**：2026-09-15（zip 扫描评估后引用）。

**用途**：为 SwarmLabs GP+UQ 引擎提供**真实实验值**的外部锚定，补齐当前 62 场景全部为合成/教科书物理量的空白。这四个数据集是**分子性质预测领域事实上的标准基准**（见 MoleculeNet），任何声称「AI4S 验证层」的引擎都必须能对它们给出可审计的性能。

## 数据集

| 文件 | 分子数 | 目标 | 数据源 | 上游 DOI/引用 |
|---|---:|---|---|---|
| `chembl_lipophilicity_slim.json` | 4,200 | `exp_logD`（实测油水分配系数） | ChEMBL Lipophilicity | Delaney D.F., *J.Chem.Info.Model.* 2014;54:1781 |
| `bace_slim.json` | 1,513 | `exp_pIC50`（BACE1 抑制剂 pIC50） | BACE/MS-SPrint 基准 | Kočaň et al. 2014；MoleculeNet |
| `freesolv_slim.json` | 642 | `exp_hydration_free_energy_kcal_mol`（水合自由能） | FreeSolv | Chodera & Pande, *J.Chem.Phys.* 2009 |
| `esol_delaney_slim.json` | 1,128 | `exp_logS`（水溶性） | ESOL / Delaney | Delaney 2004；MoleculeNet |

**总规模**：原始精简集约 1.7 MB；`featurized/` 下另有 ~4.1 MB 离线 RDKit 特征化产物（Morgan 2048 + 结构描述符），仅仓库内由离线 Python GP 引擎消费，**不部署 CF Pages**（CF Pages 当前存储已近满，且 V8 Worker 无 Python 运行时）。

## 质量验证（已跑）

- 4 个数据集 **SMILES 全部唯一**，无缺失目标值。
- ESOL 有 5 个重复 SMILES（1,128 vs 1,123 unique），不影响基准。
- 目标值范围：logD ∈ [-1.5, 4.5]、pIC50 ∈ [~5, ~11]、hydration ΔG ∈ [-15, 5] kcal/mol、logS ∈ [-13, +3]。

## 未纳入本目录但值得后续处理的数据

| 数据集 | 规模 | 排除原因 |
|---|---:|---|
| `swarmlabs_qm9_full.json`（130,831 分子 DFT B3LYP） | 22.8 MB | CF Pages 剩余配额不足；且是 DFT 参考解非实验值，用于锚定的价值低于上述四组 |
| `swarmlabs_qm9_data.json`（20,000 分子子集） | 6.7 MB | 同上 |
| `swarmlabs_toxcast_data.json`（8,578 分子 × 30+ assays） | 46.5 MB | 稀疏高维（每分子 assay 数 39–246 不均衡），需先做 assay 筛选；作为多输出 UQ 场景可后续单独引入 |
| `swarmlabs_training_data_large.json`（9,536 分子，Joback 生成） | 3.4 MB | 目标是 Joback 基团贡献法计算值，非实验；不能作真锚点 |
| `Tox21 / SIDER / ClinTox` 子集 | — | Tox21/SIDER 是多标签分类，ClinTox 是二分类，不是当前引擎的连续量 UQ 主赛道 |

## 与现有 SwarmLabs 引擎的对齐

SwarmLabs 当前 62 场景全部为合成动力学/物理/微生物模型（`engine/microbiology_full.py` 51 微生物 + 11 跨域）；**没有化学分子性质场景**。把本目录作为「外部锚点」引入时，需：

1. **新建场景** `chemistry_logD`、`chemistry_pIC50` 等，用 SMILES 描述符（MW/xlogp/TPSA/HBD/HBA/rot_bonds/n_atoms/rings）作特征向量 x ∈ ℝ^10；引擎 GP+UQ 预测目标。
2. 用现有 V&V 口径（`verification_gate.py`：PASS = R²≥0.90 且 cov≥0.90）作判定，把「GP 引擎 vs 这些真实基准」的诚实成绩写入报告库。
3. 与 `engine/wet_lab_anchors.py` 的锚点机制对接：这些数据集的实测值正是文献锚点的直接来源（不需要再反推动力学参数）。

**注意**：本目录本身**不参与 CF Pages 部署**，除非显式在 `deploy_pages.py` 里加入白名单；默认仅在训练/评估脚本中作为离线参照。
