# 跨学科语义对齐层尝试报告（2026-10-05）

## 1. 背景：为什么尝试跨学科语义对齐

### 1.1 问题起点

G3 留一学科外推自 2026-10-04 实测以来持续为 **0/14 学科（0%）**。
这是 SwarmLabs 叙事最大的漏洞——我们说"跨学科可信验证层"，但没有
证据证明"这套方法能跨学科用"。

2026-10-04 已完成量纲归一化（逐场景 z-score），微生物从 93325 降到
168.79（552.9× 改善），但 G3 仍是 0%。A15 记录了 GP 在训练域外
退化为均值的本质性质，同时指出跨学科 y 的函数形状不同是本质限制。

### 1.2 对齐层的动机

纯数值 GP 没有跨学科语义对齐层。不同学科的 f(x) 形状不同
（Monod 增长 vs Rastrigin 震荡 vs 化学 sigmoid），GP 只学 (x, y)
数值对，没有任何机制让它知道"化学反应的 x 方向和微生物的 x 方向
语义不同"。

对齐层试图在**预处理层**补上这个缺口：
- 把不同学科拉到同一个无量纲参数空间
- 提取响应形状签名，找出跨学科的共享结构
- 如果共享结构存在，GP 就能从对齐空间学到有意义的映射

### 1.3 与 A15 的关系

A15 说"GP 看不到训练域外"（核方法本质性质）。
A16 尝试回答"能不能通过对齐层补上跨学科的语义结构"。
如果对齐后 G3 仍为 0%，则 A16 成立——**跨学科语义对齐不能救 G3**。

---

## 2. 对齐方案

### 2.1 四层设计

| 层 | 操作 | 目的 |
|---|---|---|
| 1. 参数无量纲化 | 每个场景 X 用自身 bounds 归一化到 [0,1] | 消除量纲差异 |
| 2. 响应形状签名 | 差分谱(16维) + 单调性 + rel_std + 导变化率 + 极值位置 = 21维 | 提取函数"长什么样" |
| 3. PCA 共享 latent | 所有 signature 降维到 k=8 | 学跨学科共享低维空间 |
| 4. 对齐度量 | MMD-lite（高斯核）计算跨学科 vs 学科内重合度 | 量化对齐质量 |

### 2.2 关键设计决策

- **对齐层不是模型**：它是**预处理**，只把 X 从物理量纲变成无量纲
  位置 + 场景语义签名，仍然交给现有 GPSurrogate 用。
- **对齐度量是观测值**：signature 和 PCA latent 用于计算对齐度量
  报告，训练/预测只用了 X_norm（参数无量纲化）。
- **诚实优先**：如果对齐后 G3 仍为 0%，如实报告，不美化。

### 2.3 代码

- `engine/semantic_alignment.py`：对齐层实现（≤ 400 行），18 条
  selftest 全部通过
- `engine/generalization_bench.py::run_g3_aligned`：对齐后 G3 复测
- `engine/generalization_bench.py::run_g3_aligned_benchmark`：
  对齐前后对比基准，36 条 selftest 全部通过

---

## 3. 实验设置

| 参数 | 值 |
|---|---|
| seed | 4242 |
| n_train（每学科训练点数） | 30 |
| k（PCA 降维维度） | 8 |
| n_grid（signature 采样网格） | 40 |
| 场景总数 | 95 |
| 学科数 | 14（≥ 2 场景） |
| 跳过学科 | 0（14 学科全部 eligible） |
| 耗时 | 62.0s |

---

## 4. 结果

### 4.1 G3 对齐前后对比

| 指标 | 对齐前 | 对齐后 |
|---|---|---|
| G3 pass_rate | **0.0%** (0/14) | **0.0%** (0/14) |
| Delta | — | **0.0** |
| 结论 | — | **对齐无改善** |

**G3 对齐前后完全一致，对齐层没有任何改善。**

### 4.2 对齐度量

| 度量 | 值 | 解读 |
|---|---|---|
| cross_discipline_alignment | **0.9246** | 跨学科 signature 高度重合 |
| intra_discipline_alignment | **0.1665** | 学科内 signature 分散 |
| cross_discipline_centroid_distance | 0.4011 | 跨学科质心距离 |
| intra_discipline_centroid_distance | 0.0 | 学科内质心距离 |
| signature_dim | 21 | 响应形状签名维度 |
| latent_dim | 8 | PCA 降维后维度 |
| latent_explained_variance | 0.9274 | PCA 累积方差解释率 |
| n_disciplines | 14 | 参与对齐的学科数 |
| n_aligned_scenarios | 95 | 参与对齐的场景数 |

### 4.3 反直觉发现

`cross_discipline_alignment` = 0.9246（**高**），
`intra_discipline_alignment` = 0.1665（**低**）。

这意味着：
- **跨学科 signature 在 latent 空间高度重合**——几乎所有学科的
  响应形状在低维投影后落入同一个区域
- **学科内 signature 分散**——同一学科不同场景的形状差异大

**这不是好消息，是坏消息**：如果跨学科 signature 高度重合，
说明对齐层**没有学到任何跨学科的区分性结构**——所有学科都被
映射到同一个低维区域。GP 看到的仍然是一个混合的、无结构的
"参数-响应形状"，对齐层没有给 GP 提供任何额外的跨学科信息。

### 4.4 学科级明细

| 学科 | 对齐前 mean_rel | 对齐后 mean_rel | 变化 |
|---|---|---|---|
| biology | 2.06 | 1.98 | -0.08 |
| chemical | 2.83 | 2.87 | +0.04 |
| cheminformatics | 14.60 | 17.24 | +2.64 |
| chemistry | 4.15 | 3.89 | -0.26 |
| ecology | 7.53 | 6.71 | -0.82 |
| energy | 6.01 | 5.82 | -0.18 |
| finance | 1.56 | 1.55 | -0.01 |
| heat conduction | 1.81 | 1.93 | +0.13 |
| llm | 3.86 | 3.99 | +0.13 |
| microorganisms | 4.78 | 5.69 | +0.91 |
| neuroscience | 348.96 | 358.41 | +9.45 |
| optimization | 2.98 | 3.26 | +0.28 |
| surrogate modeling | 1.35 | 1.59 | +0.24 |
| 微生物 | 146.42 | 629.81 | **+483.38** |

部分学科有微小改善（biology -0.08、chemistry -0.26、ecology -0.82），
但幅度远不够达到 0.05 判据。微生物从 146→630（**恶化 4.3×**），
neuroscience 从 349→358。

---

## 5. 根因分析

### 5.1 与 A15 的关系

| 层面 | A15 | A16 |
|---|---|---|
| 问题 | GP 在训练域外退化为均值 | 跨学科 signature 无共同几何 |
| 本质 | 核函数性质（x 远离 xᵢ → k→0） | 函数形状空间不可对齐 |
| 可修否 | 不可修（本质性质） | 不可修（本质差异） |
| 证据 | G1=0%（内插 OK，外推崩塌） | G3 对齐后仍 0% |

A15 说"GP 看不到训练域外"，A16 说"即使把不同学科拉到同一个
参数空间，响应形状本身也没有共同结构可供 GP 学习"。两者互相
印证：**跨学科泛化的障碍不只在核函数外推，更在学科间的函数
形状本身没有可对齐的语义**。

### 5.2 为什么对齐度量高但 G3 仍为 0%

对齐度量 `cross_discipline_alignment = 0.9246` 看似很高，但这
恰恰是问题所在：

1. **对齐度量衡量的是"学科之间像不像"**——0.9246 意味着所有
   学科在 signature 空间几乎不可区分
2. **G3 需要的是"模型能从训练学科学到什么用于留出学科"**——
   如果所有学科看起来都一样，模型没有任何跨学科的结构信息可学
3. **signature 高度重合意味着对齐层抹平了学科差异**——这不是
   "成功对齐"，是"没有学到任何有区分性的特征"

换句话说：对齐度量高不等于对齐成功。真正的对齐应该是"让不同
学科映射到一个共享空间，但保留各自的结构差异"。而我们的对齐
度量高恰恰说明**学科差异在 signature 空间被抹平了**，GP 看不到
任何可以利用的跨学科结构。

---

## 6. Selftest 验证

### 6.1 semantic_alignment.py（18 条全部通过）

| # | 验证项 | 结果 |
|---|---|---|
| 1 | 参数无量纲化后 x ∈ [0,1] | PASS |
| 2 | 退化 bounds 不除零 + 钉到 0.5 | PASS |
| 3 | signature 长度固定 = SIGNATURE_DIM | PASS |
| 4 | 相同 y → signature 完全一致 | PASS |
| 5 | y_norm 均值为 0 | PASS |
| 6 | y_norm 标准差为 1 | PASS |
| 7 | 完全相同场景的对齐度 ≈ 1.0 | PASS |
| 8 | 完全不同场景的对齐度 < 0.3 | PASS |
| 9 | PCA latent 维度正确 | PASS |
| 10 | PCA 累积方差解释率 ∈ [0,1] | PASS |
| 11 | align_scenarios 返回非空 aligned | PASS |
| 12 | 报告含 cross_discipline_alignment | PASS |
| 13 | 报告 signature_dim 一致 | PASS |
| 14 | cross_discipline_alignment ∈ [0,1] | PASS |
| 15 | intra_discipline_alignment ∈ [0,1] | PASS |
| 16 | X_aligned 返回四元组 | PASS |
| 17 | X_norm ∈ [0,1] | PASS |
| 18 | 空场景返回 reason | PASS |

### 6.2 generalization_bench.py（36 条全部通过）

原有 24 条 + 新增 12 条对齐层 selftest，全部通过。

---

## 7. 结论与定位建议

### 7.1 核心结论

**跨学科语义对齐不能救 G3。**

- G3 对齐前：0/14 学科（0%）
- G3 对齐后：0/14 学科（0%）
- Delta：0.0
- 对齐层本身逻辑正确（18 条 selftest 全部通过）
- 对齐度量显示跨学科 signature 高度重合（0.9246），但 GP 仍
  无法学到有意义的跨学科映射

### 7.2 定位建议

根据用户 memory 中明确写下的原则：
> 「若 G3 修不到非零，项目定位应收窄为生物过程可信代理层」

A16 实测证实 G3 修不到非零（对齐前后均 0%），因此：

**项目定位应收窄为「生物过程可信代理层」**——把资源集中到那个
真正做得住的方向（微生物/生物场景的设计域内插值验证），不宣称
跨学科泛化能力。

具体而言：

1. **保留的能力**：
   - 微生物/生物场景的设计域内插值验证（52/95 场景）
   - 不确定性量化（conformal + 双侧 coverage）
   - 形式化验证（Python + Z3 + Lean 三通道）
   - 失效模式公开（A1~A16，16 条已证明公理/边界）
   - 实验闭环（conclusion + boundary + next_experiment）

2. **放弃的宣称**：
   - 跨学科外推验证（A16 实测证实不可行）
   - 跨学科机理泛化（A15 第 2 点 + A16）
   - 训练域外的外推预测（A15）

3. **收缩定位不丢人，拿未验证的主张去融资才丢人。**

### 7.3 未来方向

如果未来要真正突破 G3，需要：
- 不是预处理层对齐，而是**模型层**的跨学科结构学习
  （如多任务学习、条件 GP、神经算子等）
- 需要**学科标签**作为条件输入（当前 GP 完全不知道"这是哪个
  学科"）
- 需要**机理方程**作为约束（当前 GP 只学数值，不知道 Monod
  方程和 Rastrigin 函数是不同的机理）

这些方向都需要根本性的架构改变，不是在现有 GP 框架上加一个
预处理层能解决的。

---

## 8. 可复现

```python
# 对齐层 selftest
~/.workbuddy/binaries/python/envs/default/Scripts/python \
  engine/semantic_alignment.py

# G3 对齐前后对比
~/.workbuddy/binaries/python/envs/default/Scripts/python -c "
import sys; sys.path.insert(0, '.')
from engine.generalization_bench import run_g3_aligned_benchmark
res = run_g3_aligned_benchmark(seed=4242, n_train=30, k=8, n_grid=40)
print('Baseline:', res['baseline']['pass_rate'])
print('Aligned:', res['aligned']['pass_rate'])
print('Delta:', res['comparison']['delta'])
print('Conclusion:', res['comparison']['conclusion'])
"
```

---

## 9. 证据来源

| 结论 | 数据来源 |
|---|---|
| G3 对齐前 0% | `data/generalization_bench.json` |
| G3 对齐后 0% | `data/g3_aligned_benchmark.json` |
| 对齐度量 | `engine/semantic_alignment.py::align_scenarios` |
| 学科级明细 | `data/g3_aligned_benchmark.json` |
| 18 条 selftest | `engine/semantic_alignment.py` |
| 36 条 selftest | `engine/generalization_bench.py` |
| A15 背景 | `AXIOM_AUDIT.md` §A15 |
| A16 完整记录 | `AXIOM_AUDIT.md` §A16 |
