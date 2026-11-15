# data/ml_models — 化学 / 工艺代理模型（可复现，二进制不入库）

## 为什么这里没有 .pkl

这批模型一度只存在于 `backups/_desktop_removed_2026-09-17/`，脱离仓库后无法追溯来源。
2026-09-21 处置原则：**数据集入库（不可再生），模型本体不入库（可再生）**。

- 数据集已恢复到 `data/reference_benchmarks/`（见下），是真正的数据护城河。
- 模型用 `scripts/train_chem_surrogates.py` 从数据集重建，固定 `random_state=4242`。
- 本地 `.pkl` / `.pt` 全部保留（未删），只是被 `.gitignore` 排除，
  避免每次重训都在仓库留下上百 MB 的二进制差异（与 `dist/` 同一处置逻辑）。

> 判定依据：能一条命令重造的东西不进 git；造不出来的东西才进 git。

## 数据集来源

| 数据集 | 路径 | 规模 | 说明 |
|---|---|---|---|
| QM9 全集 | `data/reference_benchmarks/quantum_chemistry/qm9_full_130831.json` | 130,831 × 16 | DFT B3LYP/6-31G(2df,p)，逐分子量化属性 |
| QM9 子集 | `data/reference_benchmarks/quantum_chemistry/qm9_slim.json` | 20,000 × 17 | 同上，带语义列名 |
| Tox21 标签 | `data/reference_benchmarks/toxicology/tox21_labeled.json` | 7,823 × 12 assay | 核受体 / 应激响应通路，真实活性标签 |
| Tox21 smiles | `data/reference_benchmarks/toxicology/tox21_slim.json` | 7,823 | 仅结构 |
| ToxCast 标签 | `data/reference_benchmarks/toxicology/toxcast_labeled.json` | 8,578 × 617 assay | 仅存非零活性（原始 48.7MB → 4.29MB 紧凑形态） |
| ToxCast top30 | `data/reference_benchmarks/toxicology/toxcast_top30_assays.json` | 4,000 × 30 | 覆盖度最高的 30 个端点 |
| ToxCast smiles | `data/reference_benchmarks/toxicology/toxcast_slim.json` | 8,578 | 仅结构 |
| ClinTox | `data/reference_benchmarks/toxicology/clintox_slim.json` | 1,480 | FDA 批准 / 临床毒性双标签 |
| SIDER | `data/reference_benchmarks/toxicology/sider_slim.json` | 1,427 | **仅结构，无标签**（见「已知缺口」） |

## 重建命令

```bash
# QM9 全集 6 目标（RF + GBR）+ Tox21 12 端点分类器
python scripts/train_chem_surrogates.py

# 快速冒烟（2 万分子）
python scripts/train_chem_surrogates.py --limit 20000

# 只重训一部分
python scripts/train_chem_surrogates.py --skip-tox21
python scripts/train_chem_surrogates.py --skip-qm9
```

产物：`qm9_<target>_regen.pkl`、`qm9_<target>_regen_gbr.pkl`、`tox21_regen_classifiers.pkl`、
`regen_report.json`。**统一带 `_regen` 后缀，不覆盖历史模型**，便于对照而非静默替换。

## 指标口径（重要）

`regen_report.json` 同时记录两个相对误差，**分母不同，不可混用**：

- `rf_rel_err_vs_std` = MAE / std(y_test) —— 对近零均值目标稳健，**本脚本默认口径**。
- `rf_rel_err_vs_mean` = MAE / |mean(y_test)| —— 便于与只给「相对误差」的旧报告对照。

### 重建保真度实测（2026-09-21）

训练集 = QM9 全集 130,831，80/20 切分，`random_state=4242`，RF(100 trees, depth 15)。
下表左列是本脚本重建结果，右列是历史 `qm9_full_meta.json` 的 MAE：

| 目标 | 重建 MAE | 历史 MAE | 偏差 |
|---|---|---|---|
| dipole | 0.9411 | 0.9500 | −0.9% |
| homo | 0.3894 | 0.3877 | +0.4% |
| lumo | 0.7368 | 0.7421 | −0.7% |
| gap | 0.7883 | 0.7974 | −1.1% |
| alpha | 2.0023 | 2.0234 | −1.0% |
| zpve | 0.0367 | 0.0366 | +0.3% |

**六目标全部在 ±1.1% 内复现** —— 说明恢复的数据集与历史模型同源，模型确实可从入库数据重造。

### 历史 `rel_err` 的分母无法还原（不可引用）

`qm9_full_meta.json` 只给 `rel_err` 不给分母。反推其隐含分母：

| 目标 | MAE | 历史 rel_err | 反推分母 | 与 std / mean 是否吻合 |
|---|---|---|---|---|
| dipole | 0.9500 | 3.214 | 0.2956 | 均不吻合 |
| homo | 0.3877 | 4.358 | 0.0890 | 均不吻合 |
| lumo | 0.7421 | 7.401 | 0.1003 | 均不吻合 |
| alpha | 2.0234 | 1.063 | 1.9032 | 均不吻合 |
| zpve | 0.0366 | 0.521 | 0.0702 | 均不吻合 |

结论：**历史 `rel_err` 既不是 MAE/std 也不是 MAE/|mean|，分母来源已不可考，一律不得对外引用**；
对外只用上表的绝对 MAE。

复盘教训：相对量不写分母，等于没写。同一份 `rel_err=3.21` 可以被读成 3.2% 也可以被读成 321%。


## 未重建的历史模型（保留在 data/ml_models/，不入库）

| 文件 | 大小 | 来源 | 说明 |
|---|---|---|---|
| `tox21_classifiers.pkl` | 16.8MB | 12 个端点分类器 | 可用 `train_tox21()` 重建（本脚本用逻辑回归作基线，历史版本可能是树模型） |
| `toxcast_regressors.pkl` | 1.8MB | ToxCast 回归器 | 数据在 `toxcast_labeled.json`，重建脚本待补 |
| `bio_{logD,logs,hydration,bace_pIC50}_rf.pkl` | 5–17MB | 17,085 行生物化学数据 | 重建脚本待补 |
| `bio_*_morgan.pkl` | 0.5–7.8MB | Morgan 指纹缓存 | RDKit 可重算 |
| `boiling_point` / `melting_point` / `heat_of_formation` `_rf13/_gbr/_morgan.pkl` | 0.1–3.2MB | NIST WebBook 271 分子 | 数据在 `data/_early_import/`（gitignore，本地留存） |
| `qm9_gnn{,_v2,_v3}.pt` | 0.17–1.27MB | MPNN 3/5 层 | 需 torch，环境未装 |
| `ensemble/*_ensemble.pkl` | 2.2–2.4MB | 集成模型 | 重建脚本待补 |
| `engine_models/*.pkl` | 0.5MB（8 个域） | 工艺域 ML 代理 | 小型，后续可直接入库 |

## 已知缺口（诚实标注）

1. **SIDER 无副作用标签** —— 恢复的 SIDER 只有 smiles + 描述符，
   真正的 side-effect 标签（27 类系统器官）在原始备份里也没有，**需重新抓取**。
2. **`bio_meta.json` 里 logD / hydration / bace_pIC50 三个 MAE 全是 0.689** ——
   数值完全相同，高度疑似误填或占位值，**不可对外引用**，重组前需实测。
3. **`bio_*` / `toxcast` / `ensemble` 重建脚本未写** —— 当前只有 QM9 + Tox21 可一条命令重造。
4. **`train_chem_surrogates.py` 的 QM9 特征只有元素计数 6 维** ——
   与历史 RF 同构，但化学信息量低（这也是 rel_err 偏大的原因）；
   升级路径是接 RDKit Morgan 指纹或 GNN 后端，属下一阶段工作。
