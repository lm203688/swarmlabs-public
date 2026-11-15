# 低成本湿实验对照协议（Wet-Lab Parity Protocol）

> **场景**：验证 `engine/surrogate.py` 代理模型对 Monod 生长的拟合是否与真实培养数据一致。
> **目标场景**：`micro_ecoli_glucose` / `ecoli_monod_growth`（锚点最多、设备最少、单次实验成本 < ¥500）。
> **对应锚点**：`data/real_anchors.json` 中 `ecoli_monod_ks_real`（K_s = 0.1 g/L，Zenodo 19416053，29 °C）与 `ecoli_monod_mu_max_real`（μ_max = 0.002667 min⁻¹）。
> **产出**：一份可回填的 `parity_check` JSON，喂给 `scripts/build_parity_report.py`，把 `trust_surface.experimental_ground_truth` 从 0 推到 ≥1。

---

## 一、为什么先做 Monod E. coli（选型依据）

`data/real_anchors.json` 覆盖 3 个场景（E. coli Monod / Langmuir 亚甲基蓝吸附 / 酵母乙醇发酵），全部低成本可复现，按投入产出比排序：

| 场景 | 锚点数 | 设备 | 单次时长 | 单次成本 | 建议顺序 |
|---|---|---|---|---|---|
| **E. coli Monod（本文档）** | 2（K_s, μ_max） | 摇床 + 分光光度计 | 1 天 | < ¥500 | **①** |
| Langmuir 亚甲基蓝吸附 | 2（q_max, K_L） | 磁力搅拌 + UV-Vis | 1 天 | ~¥800 | ② |
| 酵母乙醇发酵 | 2（yield, 抑制阈值） | 发酵罐 + GC | 3–7 天 | ~¥2000 | ③ |

选 ① 的三条理由：

1. **设备最简**：只有摇床 + 分光光度计（实验室标配，不额外采购）；
2. **时间最短**：OD600 生长曲线 1 天内跑完，20 个时间点即够拟合；
3. **锚点最密**：K_s 和 μ_max 两个参数都有文献实测值，parity 判据可以直接钉在数值上，不是主观打分。

---

## 二、设备与耗材清单

| 物品 | 规格 | 单价（估算） |
|---|---|---|
| E. coli 菌株 | BL21(DE3) 或 DH5α（实验室库存或 100 ¥） | ¥0–100 |
| LB 液体培养基 | 250 mL × 2 瓶（脱脂奶粉+胰蛋白胨+NaCl，或成品平板融化） | ¥80 |
| 葡萄糖 | 分析纯，1 g | ¥10 |
| 试管 | 15 mL × 30 支 | ¥15 |
| 无菌操作台 | 实验室已有 | ¥0 |
| 恒温摇床 | 29 °C ± 1 °C，200 rpm | ¥0（借用） |
| 分光光度计 | OD600，带 cuvette | ¥0（借用） |
| 移液器 | 200 μL + 2 mL | ¥0 |
| **合计** | | **¥105–205** |

*成本估算基于国内耗材 2026 年 9 月零售均价；如果实验室已有菌株和培养箱，仅需耗材 ~¥20。*

---

## 三、实验步骤

### 3.1 前一夜准备（Day -1, ~30 min）

1. **活化菌株**：从 -80 °C 甘油库取出 5 μL，接种到 5 mL LB + 50 μg/mL 抗生素（BL21 用卡那，DH5α 不用），37 °C 200 rpm 培养至过夜（~12 h）。
2. **灭菌葡萄糖母液**：10 g/L 葡萄糖水溶液，0.22 μm 滤膜过滤或 121 °C 灭菌 15 min。

### 3.2 Day 0 — 培养与采样（~6 h）

1. **接种**：过夜培养物按 1% v/v 接种到 25 mL LB + 1 g/L 葡萄糖的锥形瓶（3 瓶重复）。
2. **恒温培养**：29 °C（与锚点 `ecoli_monod_ks_real` 温度一致），200 rpm 摇床。
3. **OD600 采样点**（时间点选得密一些，让膝部有充分采样）：

   | 时间点 | 0 | 0.5 | 1 | 2 | 3 | 4 | 5 | 6 | 8 | 10 h |
   |---|---|---|---|---|---|---|---|---|---|---|
   | 采样 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

   *注：OD600 > 0.8 后进入饱和期，采样加密意义不大；但保留 8、10 h 两点让拟合器看到完整平台。*

4. **测量 OD600**：每次取 100 μL 装入 cuvette，空白以 LB 培养基校准。

### 3.3 平行实验（可选，为 μ_max 提供更稳估计）

再接种 10 支 15 mL 试管（分 5 组 × 2 重复），每组底物浓度不同：
- 0.01 g/L（远低于 K_s，观察近线性阶段）
- 0.05 g/L（~½ K_s）
- 0.1 g/L（= K_s 锚点值，最关键的比对点）
- 0.5 g/L（远大于 K_s，进入饱和期）
- 2 g/L（严重饱和）

每 30 min 采一次 OD600，共 6 h。用于直接估计每个 S 下的 μ。

---

## 四、数据处理与拟合

### 4.1 从 OD600 到生物量

E. coli 在 OD600 ∈ [0.02, 0.6] 线性区，转换系数 `X ≈ 0.42 gDW / OD600`（文献典型值）。
饱和后线性关系失效，此时仅用相对生长速率 μ 而不是绝对 X。

### 4.2 Monod 拟合

`μ = μ_max · S / (K_s + S)`，用 `scipy.optimize.curve_fit` 拟合同时得 μ_max 与 K_s。

### 4.3 回填到引擎

拟合结果写回 `engine/wet_lab_anchors.py::measured_anchors()`，格式：

```python
{
    "id": "ecoli_monod_lab_run_2026-09-15",
    "scenario": "ecoli_monod_growth",
    "parameter": "K_s",
    "value": <实测值>,
    "unit": "g/L",
    "source_type": "experimental",
    "temperature_C": 29,
    "notes": "Local lab run 2026-09-15, 3 replicates, mean ± SD"
}
```

### 4.4 触发 parity 重算

```bash
python scripts/build_parity_report.py     # 生成 frontend_fixed/reports/parity_report.json
python scripts/build_gate_ledger.py       # 重建门禁账本，trust_surface.experimental_ground_truth 应从 0 → ≥1
```

---

## 五、判定表（Parity Grading）

`scripts/build_parity_report.py` 用 4 档 parity grade 判定虚拟 vs 实测一致性，判据完全透明：

| 档位 | 判据（对 K_s 或 μ_max 任一参数） | 动作 |
|---|---|---|
| **STRONG** | 实测值落在文献共识区间内，**且** 落在 oracle 预测 ± 5% | parity PASS，场景可对外宣称"实验锚定" |
| **MODERATE** | 实测值落在文献共识区间内，偏差 oracle 5–20% | parity PASS 但需报告 UQ 加宽 |
| **LITERATURE_CONSENSUS** | 只有文献参考（无湿实验），偏差 oracle ≤ 20% | 保留 oracle 结论，parity 未闭环 |
| **SINGLE_SOURCE** | 单条文献来源，未做湿实验对照 | parity 未验证，保持 REFUTED/MARGINAL 判定 |

`data/real_anchors.json` 当前 19 条锚点的实际分布：
- STRONG: 0（尚无真实湿实验数据）
- MODERATE: 3
- LITERATURE_CONSENSUS: 3
- SINGLE_SOURCE: 13

**parity 从 0 → STRONG 的第一步**：本实验完成后，只要 E. coli Monod 的实测 K_s、μ_max 落在 oracle 5% 以内，就把该场景推到 STRONG。

---

## 六、失败模式与兜底

| 失败模式 | 征兆 | 兜底动作 |
|---|---|---|
| 菌株污染 | OD 时间序列出现拐点后骤降 | 弃用该重复，取未污染重复；重复仍不足 3 则本次实验作废 |
| 温度漂移 > ±2 °C | 培养箱读数异常 | 校准培养箱后重做，K_s 对温度极敏感 |
| 拟合不收敛 | `curve_fit` 抛 RuntimeError | 用 log10 变换坐标（对齐 `engine/design.py`），或减少初始点密度 |
| K_s 与 μ_max 强相关 | `curve_fit` 协方差矩阵退化 | 用参数约束 `bounds=([1e-4, 1e-4], [5, 0.01])` 收窄搜索 |
| 湿实验值离文献锚点 > 50% | 实测 K_s = 0.5 g/L vs 文献 0.1 g/L | 保留实测值（不修改文献锚点），把差异作为新证据提交 `engine/wet_lab_anchors.py` 供后续复核 |

---

## 七、时间线

| 阶段 | 时长 | 交付物 |
|---|---|---|
| Day -1 菌株活化 | 半天 | 活化菌株 |
| Day 0 培养与采样 | 半天 | OD600 时间序列 CSV |
| Day 1 拟合 + 回填 | 半天 | `engine/wet_lab_anchors.py` 新条目 + parity_report.json |
| **合计** | **~2 人日** | **parity PASS 或明确 REFUTED** |

---

## 八、诚实边界

- 本协议只覆盖 **K_s 与 μ_max 两个参数**；比生长速率 μ 完整时间序列、细胞代谢物谱、产物浓度**未包含**（这些需要更贵的设备，留到 ②③ 场景）。
- 单次实验 3 重复只能给出 K_s 的粗粒度估计（CV ~ 15–25%）；要 STRONG 档（± 5% 偏差）可能需要 5–10 重复。
- 文献锚点本身存在方法学差异（不同实验室 OD → 生物量转换系数不同），本次湿实验不消除文献误差，只增加**独立来源**。
- 若实测值偏离 oracle > 20%，正确做法是**保留 oracle REFUTED 判定**并公开差异——这是本项目相对竞品的核心差异（"沿用旧结论"变成可检测事实，`ledger_is_stale()` 报 stale）。

---

**生成**：2026-09-15 · 由 `docs/wetlab_protocol_2026-09-15.md` 单一真源
**上游**：`engine/wet_lab_anchors.py` (524 行) · `scripts/build_parity_report.py` (242 行) · `data/real_anchors.json` (19 条锚点，3 DOI 来源)
**下游**：parity PASS → `trust_surface.experimental_ground_truth` 0 → ≥1 → 飞轮可信度升级
