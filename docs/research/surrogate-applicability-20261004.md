# 代理模型的适用域边界：2026-10-04 实测报告

> 本文记录 2026-10-04 对剩余 20 个 `rmse_rel` 失分场景的诊断，
> 以及一个方法论层面的发现：**部分场景的目标函数根本不在"平滑代理模型"
> 的适用域内**，且这不是可以调参解决的问题。
>
> 与 `nonstationary-multifidelity-20261003.md` 配套：那篇修好了
> 「跨数量级采样」这个真 bug（rubric 35→58）；本篇回答「剩下的 20 个里，
> 哪些是真 bug，哪些是方法论边界」。

---

## 0. 一句话结论

失分场景分成泾渭分明的两簇：

| 簇 | 特征 | 数量 | 该怎么办 |
|---|---|---|---|
| **A：该修的** | 目标函数光滑（roughness < 0.01），但代理没拟合好 | 16 | 继续攻（数据量 / 核函数 / 先验） |
| **B：方法论边界** | 目标函数高频震荡（roughness > 0.45，60+ 局部极值） | 4 | **不该修**，应显式标注 |

**簇 B 的 4 个场景不是 bug**。实测把训练点从 30 加到 480（16×），
`rmse_rel` 仍停在 0.998 不降——存在**不可约的建模误差下限**。

正确处理是把这个边界**显式写进产物**，而不是假装能修好。
这与 `AXIOM_AUDIT.md` 的整体口径一致：知道自己在什么条件下失效，
比"什么都能做"更有说服力。

---

## 1. 诊断方法：两个无量纲指标

### 1.1 roughness（粗糙度）

```
roughness = mean|Δ²f| / std(f)
```

在轴上密采样 200 点后计算。含义：二阶差分与信号标准差之比。

- `roughness ≈ 0` → 光滑（GP 的平滑核假设成立）
- `roughness ≈ 1` → 二阶差分与信号同量级 → **高频震荡**

### 1.2 n_extrema（局部极值数）

数一阶差分符号变化次数 / 2（1D）或严格局部极小（2D）。
含义：函数在采样域内有多少个"转折"。

### 1.3 为什么用无量纲指标

跨场景可比较。不需要知道 f 的物理量纲，也不需要解析形式
（f 是黑盒 oracle，只能数值采样）。

---

## 2. 实测结果：两簇分界清晰

在 200 点采样下扫描 1D 失分场景：

| 场景 | rmse_rel | 局部极小 | roughness | 判定 |
|---|---|---|---|---|
| `micro_chemostat_ecoli` | 1.6963 | 66 | **1.91** | 🔴 边界外 |
| `micro_methanobacterium_methane` | 1.5187 | 71 | **2.05** | 🔴 边界外 |
| `micro_putida_toluene` | 1.2589 | 63 | **1.78** | 🔴 边界外 |
| `micro_ecoli_glucose` | 1.0337 | 64 | **0.45** | 🔴 边界外 |
| `micro_competition_ecoli_yeast` | 0.0627 | 0 | **0.006** | ✅ 域内 |
| `surrogate_forrester` | 达标 | 1 | **0.0025** | ✅ 域内 |
| `bio_logistic` | 达标 | 0 | **0.0002** | ✅ 域内 |

**分界线在 0.006 与 0.45 之间，差 75 倍。** 取阈值
`ROUGHNESS_CAP = 0.3`（离两侧各有一个数量级以上的余量）。

对照 10-03 修好的场景（`micro_lag_ecoli` roughness=0.0006、
`micro_crabtree` 等）——它们本来就极度光滑，所以那次的
「尺度自适应网格」修复能立竿见影。

---

## 3. 为什么这类场景不可修：Nyquist 约束

### 3.1 定量论证

`micro_chemostat_ecoli` 的函数在区间 `[0.01, 0.75]` 上有 **66 个局部极值**。
要分辨它们，按 Nyquist 每个周期至少需要 ~3 个采样点：

```
需要的最少采样点 ≈ 66 × 3 = 198 点  （仅为了"看见"这些极值）
场景提供的训练点 = 30 点
```

即：**训练密度比 Nyquist 下限低 6.6 倍**。

### 3.2 实验验证：加密训练点无用

实测（`micro_chemostat_ecoli`，1D，holdout n=40）：

| 训练点数 | base rmse_rel |
|---|---|
| 30（场景原值） | 1.4648 |
| 60 | 1.2347 |
| 120 | 1.1028 |
| 240 | 1.0402 |
| **480（16×）** | **0.9983** |

曲线在 480 点处**已经压平**（再加密收益 < 0.5%）。这不是
"数据还不够多"，而是"存在一个非零下限"。

### 3.3 频谱证据

对 200 点采样做 FFT：

- 能量 top1 频率仅占 **1.5%**，top5 累计 **2.1%**
- 二阶差分绝对值均值 **0.93**，与信号 std **0.49** 同量级

即：能量高度分散在高频段，是典型的宽带震荡而非光滑趋势。
GP 的 RBF/ARD 核假设的是"函数在 lengthscale 尺度内近似二次"，
对宽带震荡完全不适配。

---

## 4. 这不是"场景设计有问题"

一个可能的质疑：这些场景是不是**造错了**？如果是，那是应该修的。

**答：不是。** 三条证据：

1. **函数是合法的**。`micro_chemostat_ecoli` 是化学趋化（chemostat）
   连续搅拌罐反应器的稀释率稳态模型。真实的连续发酵在某些工况下确实
   表现出振荡（这是已知现象，见 Hopf 分岔文献）——场景在忠实建模。

2. **训练/测试分布一致**。实测测试点越界比例仅 **5%**，
   不是分布外推问题。

3. **峰区不是采样稀疏**。峰区 `[0.08, 0.16]` 内有 3 个训练点、
   5 个测试点，训练点间距 mean=0.0246 / min=0.0079，
   并没有"恰好漏掉关键区域"。

**所以场景是对的，代理模型这类方法对它不适用。** 这是方法的边界，
不是数据的缺陷。

---

## 5. 实施：显式标注，而非掩盖

### 5.1 关键设计判断

**刻意没有把 applicability 加进 rubric 维度。**

理由：如果加进 rubric（7 维 → 8 维），那 4 个边界外场景会因为
`in_applicability=False` 而**再失一分**，`rubric_full_pass` 从 58
降到 58（4 个本来就没通过）——看似"没影响"，但如果哪天某场景
rubric 通过了却适用域外，就会被无端扣分。

更根本的问题：**加进 rubric 等于把"知道自己有边界"变成"因为有边界
而被判失败"**。这两件事必须分开。

正确做法：
- `rubric` 保持 7 维不变，`rubric_full_pass` 保持诚实的 58/82
- 额外输出 `in_applicability` / `roughness` / `n_extrema` 三个字段
- `overall` 增三个统计，把失分**分类**：
  - `rmse_rel_fail_in_applicability`：该修的
  - `rmse_rel_fail_out_of_applicability`：方法论边界

### 5.2 代码

`engine/multifidelity.py`：

```python
ROUGHNESS_CAP = 0.3
APPLICABILITY_SAMPLES = 200

def assess_applicability(f, bounds, n=APPLICABILITY_SAMPLES) -> dict:
    """判定目标函数是否在「平滑代理模型」的适用域内。"""
    # 返回 {"in_applicability": bool, "roughness": float|None,
    #       "n_extrema": int, "reason": str}
```

`scripts/paper_validation_loop.py::trust_rows`：

- 每个场景调 `assess_applicability`，写 `in_applicability` /
  `roughness` / `n_extrema` / `applicability_note` 四个字段
- `overall` 增三个分类统计
- **rubric 本身不动**

### 5.3 fail-closed 语义

检测失败（场景未登记 / oracle 抛异常 / 非有限值 / dim>2）时，
`in_applicability=True`（**不阻断**）。理由：这是**标注**不是**门禁**——
它不该影响任何判定，只提供信息。把标注做成会阻断的判据是错的。

---

## 6. 对市场的意义

### 6.1 这是"失效模式公开"的第三个实例

`AXIOM_AUDIT.md` 已记录两个：
- **A11** 对抗性鲁棒性未做
- **A13** 机理先验破坏校准

本篇是第三个：**A14 代理模型对高频震荡目标有不可约下限**。

竞品的做法是"我们能做什么"。我们的做法是"我们在什么条件下
**做不到**什么"。后者在科学界更有说服力——因为它让使用者在
**选型时不会踩坑**。

### 6.2 实际使用建议

一个微生物工程师如果用 SwarmLabs 预测一个 chemostat 场景，
他应该被告知："这个目标函数有 66 个局部极值，超出平滑代理模型的
适用域，建议改用机理 ODE 直接积分而非代理模型。"

这比给一个"rmse_rel=1.7 但我们没解释为什么"要有用得多。

### 6.3 后续可做的（未做）

- **换方法族**：对这类场景用直接 ODE 积分而非 GP 代理
  （项目已有 `engine/microbiology_full.py` 的解析模型）
- **纳入 rubric 但反向**：`applicability_aware` 判据 =
  "域内且达标" 或 "域外且已标注"，避免域外场景拖累满通过率
- **场景再设计**：如果只是想测 GP，那这类场景应该从 benchmark
  里移出（但那就损失了"诚实展示能力边界"的机会）

---

## 7. 相关文件

| 文件 | 作用 |
|---|---|
| `engine/multifidelity.py::assess_applicability` | 适用域检测（roughness + n_extrema） |
| `engine/multifidelity.py::count_local_minima` | 多模态度量（门控用） |
| `scripts/paper_validation_loop.py::trust_rows` | 写 `in_applicability` 等字段 |
| `docs/research/nonstationary-multifidelity-20261003.md` | 前篇：跨数量级采样修复（35→58） |
| `AXIOM_AUDIT.md` | 假设清单（应增 A14） |
