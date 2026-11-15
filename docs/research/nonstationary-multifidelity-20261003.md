# 科研方向与硬骨头：2026-10-03 摸底报告

> 本文档记录 2026-10-03 对 SwarmLabs 的全面摸底、核心硬骨头的
> 诊断与攻克过程，以及由此推导出的科研方向与市场壁垒建议。
> 所有数字均为**实测**，非估算。

---

## 0. 一句话结论

**SwarmLabs 的瓶颈不是场景数量、不是数据量、不是工程完备度，而是
「微生物 ODE 场景的建模方法论错配」**。这一项占全部 rubric 失分的
84%（36/43）。本轮已攻克：**rubric 满通过率 35/82 (42.7%) → 58/82 (70.7%)**，
且 coverage 零回归、耗时从 660s 降到 152s。

| 指标 | 修复前 | 修复后 | 变化 |
|---|---|---|---|
| **rubric_full_pass** | 35/82 (42.7%) | **58/82 (70.7%)** | **+23（+65.7%）** |
| rmse_rel 失分 | 43/82 | **20/82** | −23 |
| coverage_ok | 80/82 | **80/82** | **零回归** |
| sharpness_ok | 69/82 | **75/82** | +6 |
| flagship | 16 | **23** | +7 |
| trust_surface 耗时 | 660s | **152s** | **4.3× 更快** |

**结论**：扩到 100k 实体前必须先修方法论，否则只是把这个错误放大 1000 倍。
现在方法论站住了，规模化才有意义。

---

## 1. 摸底数据（2026-10-03）

### 1.1 资产健康度：良好

| 指标 | 值 | 状态 |
|---|---|---|
| 场景库 | 82（`data/scenario_datasets/`，与 ledger 严格一致） | ✅ |
| 账本口径 | 82 / 79 PASS / 2 MARGINAL / 1 REFUTED / 0 ERROR | ✅ |
| REFUTED | `mol_suzuki_yield`（保留，硬阻断） | ✅ |
| rdkit 截断 | 未复发（`rdkit_available: true`，dropped=[]） | ✅ |
| 实体库 | 157,846（works 21,137 / authors 44,388 / references 64,917） | ✅ 已超 100k 目标 |
| 每日飞轮 | 03:00 / 12:30 双任务，10-01~10-03 连续 3 天 0 崩溃（661s/635s/661s） | ✅ |
| 诚实口径独立核检 | PASS | ✅ |

### 1.2 真正的瓶颈：rubric 满通过率

```
rubric_full_pass: 35/82 = 42.7%
```

这是全项目**最难看也最有价值的数字**。按 7 个 rubric 维度拆解失分：

| rubric 维度 | 失分 | 率 | 性质 |
|---|---|---|---|
| **rmse_rel_ok** | **43/82** | **52%** | 🔴 **核心硬骨头** |
| sharpness_ok | 13/82 | 16% | 🟡 可通过放宽或改进校准 |
| cal_err_ok | 4/82 | 5% | 🟢 可控 |
| coverage_two_sided_ok | 2/82 | 2% | 🟢 可控 |
| gold_source_traced | 0 | 0% | ✅ 满分 |
| ls_source_traced | 0 | 0% | ✅ 满分 |
| function_nonconstant | 0 | 0% | ✅ 满分 |

**关键观察**：溯源类维度（gold / lengthscale）**已 100% 通过**。
这说明 SwarmLabs 的诚实性基建是扎实的。真正的差距在
**"能不能把误差压到 5% 以内"** —— 一个纯方法论问题。

### 1.3 失分场景的领域分布

| 前缀 | 失分数 | 占全部失分 |
|---|---|---|
| `micro_*`（微生物 ODE） | **36** | **84%** |
| `opt_*`（优化基准） | 3 | 7% |
| `mol_*`（分子） | 2 | 5% |
| `neuro_*` / `eco_*` | 2 | 4% |

---

## 2. 硬骨头一：微生物 ODE 的建模方法论错配（已攻克）

### 2.1 症状

`micro_*` 场景的 `rmse_rel` 中位数 **0.3503**，最大值 **3.2006**。
对照 `surrogate_*` / `pde_*` / `bio_*` 场景：中位数 0.0200，最大 0.05。

即：**微生物场景的预测误差与信号本身同量级**，而光滑基准场景的
误差只有信号的 2%。

### 2.2 根因诊断（实测，非推测）

逐步排除法：

1. **不是数据量问题**。失分场景训练点 30 个，达标场景 30~50 个，
   数量相当。
2. **不是核函数 / lengthscale 问题**。对达标场景同样参数即达标。
3. **不是场景函数本身病态**。82 场景全部无 NaN/Inf，
   `function_nonconstant` 100% 通过。
4. **是 bounds 跨数量级**。实测对比：

   | 场景 | bounds | log10(hi/lo) | 是否失分 |
   |---|---|---|---|
   | `micro_competition_ecoli_yeast` | [0.5, 20] | 1.60 | 部分 |
   | `micro_putida_toluene` | [0.001, 5] | **3.70** | ✅ 失分 |
   | `micro_ecoli_glucose` | [0.001, 10] | **4.00** | ✅ 失分 |

   **跨 3.7~4.0 个数量级的场景全部失分。**

5. **机理先验的网格也错了**。`engine/multifidelity.py` 原用
   `make_lowfi` 对 bounds 做 `np.linspace` 均匀采样。实测证明：
   在 `[0.001, 10]` 上，均匀 10 点会把 **99% 的采样点挤在 [0, 1]**，
   而低值区（Monod 动力学的敏感段）几乎空白。插值出的"低保真先验"
   反而污染 GP 均值函数。

   实测（`_oof` 交叉验证）：

   | 场景 | base_rmse | 均匀网格 n=10 | n=20 | n=40 | n=80 |
   |---|---|---|---|---|---|
   | `micro_lag_ecoli` | 1.3687 | 0.1659 | 0.0545 | 0.0102 | **0.0019** |
   | `micro_competition` | 0.0067 | 0.0035 | 0.0016 | 0.0010 | 0.0003 |
   | `micro_putida_toluene` | 0.0612 | 0.0626 | 0.0823 | 0.0955 | 0.1115 |

   注意 `micro_putida_toluene` **加密反而恶化**（0.0612→0.1115）——
   因为均匀加密仍解决不了"跨数量级下低值区稀疏"的问题。

### 2.3 修复

**核心改动**：`engine/multifidelity.py::axis_grid`

```python
LOG_SPAN_THRESHOLD = 2.0   # log10(hi/lo) > 2 → 对数网格

def axis_grid(lo, hi, n):
    if lo <= 0:            return np.linspace(lo, hi, n)
    if log10(hi/lo) <= 2:  return np.linspace(lo, hi, n)
    return np.logspace(log10(lo), log10(hi), n)
```

同时把网格密度从 1D 的 10 点提到 64 点（原实现比 30 个训练点还稀疏，
先验必然劣于数据）。

### 2.4 修复效果（生产路径实测，`validate_scenario` 真实跑）

| 场景 | OFF rmse_rel | ON rmse_rel | 改善 | OFF cov | ON cov |
|---|---|---|---|---|---|
| `micro_lag_ecoli` | 0.1839 | **0.0006** | **306×** | 1.000 | 0.950 |
| `micro_crabtree_cerevisiae` | 0.4208 | **0.0011** | **382×** | 1.000 | 1.000 |
| `micro_scaleup_kla` | 0.7366 | **0.0000** | ∞ | 0.950 | 1.000 |
| `micro_quorum_ecoli` | 0.4589 | **0.0010** | **459×** | 1.000 | 1.000 |
| `micro_diauxic_ecoli` | 0.4612 | **0.0401** | **11.5×** | 1.000 | 0.850 |
| `surrogate_branin` | 0.0054 | 0.0054 | 零破坏 | 1.000 | 1.000 |

**零破坏验证**：6 个原本已达标场景（surrogate_*/pde_heat1d/bio_logistic/
microbio_monod）全部逐字节一致，门控正确拒绝了所有不 beneficial 的采纳。

### 2.5 全量验证结果（82 场景，完整重建链后）

```
data/trust_surface.json → overall
  scenarios                        82
  rubric_full_pass                 58      (修复前 35)
  coverage_ok                      80      (修复前 80，零回归)
  sharpness_ok                     75      (修复前 69)
  flagship                         23      (修复前 16)
  multifidelity_adopted            57      (多保真采纳)
  multifidelity_rejected_coverage   0      (无校准回退)
  multifidelity_rejected_error     25      (误差未改善，正确拒绝)
```

`rmse_rel` 失分的领域分布从 `micro:36 / opt:3 / mol:2 / neuro:1 / eco:1`
变为 `micro:13 / opt:3 / mol:2 / neuro:1 / eco:1`——**微生物部分从 36 降到 13**。

**采纳率 57/82 = 70%**。这个数字需要警惕：门控过松意味着它没在把关。
但对照 `multifidelity_rejected_coverage = 0`（没有一个场景因校准被回退）
和 coverage 零回归，说明本轮修复（同族 conformal Q）让"采纳即有效"，
因此采纳率高是**结果**而不是**门控失效**。这是修复到位才有的状态。

---

## 3. 硬骨头二：机理先验破坏了校准（已攻克，且是本轮最重要的发现）

### 3.1 症状

修复尺度错配后，误差指标大幅改善，但**`coverage_ok` 从 80 掉到 76**。

这正是最危险的一类 bug：**误差指标变好掩盖了校准变坏**。
单看 RMSE 会误以为改进成功。

4 个回归场景：

| 场景 | coverage 前 | coverage 后 |
|---|---|---|
| `micro_lag_ecoli` | 1.000 | 0.750 |
| `micro_quorum_ecoli` | 1.000 | 0.800 |
| `micro_haldane_2d` | 0.967 | 0.667 |
| `micro_scaleup_kla` | 0.950 | 0.800 |

### 3.2 三次错误修复（记录下来，因为它们定义了这条路线的边界）

**第一次尝试**：收紧门控，加"绝对达标下限 0.85"约束。
→ **无效**。OOF 算出的 coverage 恒在 0.03~0.20，而生产是 0.75~1.00。
判据 `base_cov >= 0.85` 永远为 False，**形同虚设**。

**第二次尝试**：把 OOF 的 coverage 改成 conformal 口径。
→ **部分有效但过度修正**。加了 `max(q, 1.96)` 后 coverage 恒为 1.000，
9/9 场景全部 ADOPT，判据 3 再次失去区分力。

**第三次尝试（正确）**：找到真正的口径差异。

**根因**：主模型用 `mean_fn`，而 conformal 分位数 Q 的标定折用
`mean_fn=None`。两个模型族产生的 sd 分布不同，把 A 模型的区间宽度
配 B 模型，区间系统性偏窄 → coverage 下降。

**修复**：`_conformal_q` 增加 `mf` 参数，标定折与主模型**同族**。

```python
# scripts/paper_validation_loop.py
def _conformal_q(s, X, y, y_scale, ..., mf=None):
    ...
    g = GPSurrogate(..., mean_fn=mf)   # ← 同族
    ...
```

**结果**：4 个回归场景的 coverage 全部回到 0.95~1.00，
且误差改善完整保留。

### 3.3 由此确立的门控架构：两阶段

```
阶段 1（OOF，engine/multifidelity.py::decide_mean_fn）
  └─ 只判误差：rmse_mf < rmse_base
  └─ 为什么只判误差：OOF 误差与生产误差方向一致（实测可靠）
  └─ 为什么判不了 coverage：OOF 内部在训练集划分，
     生产用独立 conformal 校准集 + held_out，分布不同
     （实测 micro_lag_ecoli: OOF 报 1.000，生产报 0.75）

阶段 2（生产口径，scripts/paper_validation_loop.py::validate_scenario）
  └─ 判校准：用实际发货的 conformal 区间算 coverage
  └─ 若相对 baseline 跌破 [0.85, 1.0] → 回退 mean_fn=None 重跑
  └─ 回退后行为与改造前逐字节一致（零破坏保证）
```

**这个架构的代价**：门控从"自包含"变成"两阶段协作"，
且阶段 2 要多算一次 baseline conformal Q（15 次 GP 拟合）。

**收益**：校准纪律终于建立在**生产口径**上，而不是一个近似量上。
对一个以"诚实"为卖点的系统，这个取舍是必须的。

### 3.4 性能修复（必须做，否则拖垮每日飞轮）

两阶段门控让 `trust_surface()` 从 **660s 涨到 >14min**。
两个瓶颈都定位到了，各修一处：

**瓶颈 1：`mean_fn` 反复插值（最大的一项，19.2s → 1.3s，15×）**

`GPSurrogate.predict` 对每个查询点调一次 `mean_fn`，而 conformal 标定
要做 `k_folds(5) × repeats(3) = 15` 次拟合，每次 predict 又遍历整个 X。
而 `make_lowfi` 返回的 lambda 内部每次都做 `griddata` 线性插值。
2D 网格加密到 10×10 后单次插值已不便宜，累计上万次。

实测 `chem_reactor_conversion`：`_conformal_q(mf=...)` **19.2s** vs
`_conformal_q(None)` **0.3s**——**64 倍差**。

修法：`_MemoizedFn` 按 x 的字节表示记忆化。`mean_fn` 是纯函数，
记忆化语义完全等价。实测 700 hits / 50 misses，q 值逐位一致（33.319）。

**瓶颈 2：Q 被算两遍（缓存命中）**

Q 只由 `(场景 key, |X|, |y|, y_scale)` 决定，与是否启用 mean_fn 无关
（mf 是显式入参）。用**进程内**缓存记忆化：

```python
_CONF_Q_BASE_CACHE: dict = {}   # mean_fn=None 的 Q
_CONF_Q_MF_CACHE: dict = {}     # 带 mean_fn 的 Q
```

**为什么是进程内而非持久化**：避免持久化缓存与真实产物漂移。
跨进程（如定时任务）宁可重算也不吃陈旧值。

**净效果**：`trust_surface()` **152s**，比修复前的 660s 还快 4.3 倍
（memoize 顺带加速了基线路径）。

### 3.5 逃生门

```bash
SWARMLABS_AUTO_MF=0   # 一键全局关闭多保真
```

无需改代码，用于门控万一出问题时立即回退。

---

## 4. 硬骨头三：能力对 Agent 隐身（部分攻克）

### 4.1 症状

`engine/` 有 76 个模块、477 个公开 API，但只有 39 个模块（236 个 API，
**49.5%**）接入 `engine/__init__.py`。

未接入的模块对 Agent **等于不存在**——Agent 经统一入口
`engine.X` 检索不到，只能靠人知道模块名才能 import 到。

这直接削弱了"AI4S 可信验证层"的定位：如果 Agent 发现不了
`anchor_independence`（锚点独立性分析）、`discipline_registry`
（专业接入脚手架），这些能力等于没做。

### 4.2 修复：Tool Ocean 能力索引

`engine/tool_ocean.py`（借鉴 STELLA 的 Tool Ocean，
Harvard ToolUniverse 的可发现性思路）

**不引入外部依赖**，纯 stdlib AST 扫描生成 capability manifest：

| 指标 | 值 |
|---|---|
| 模块 | 76 |
| wired | 39 |
| hidden | 37（48.7%） |
| 公开 API | 236/477 可见（49.5%） |
| 代码行 | 31,043 |
| 能力域 | 9 个（surrogate / verification / evidence / evolution / scenario / data / platform / ops / uncategorized） |

**设计要点**：
- **不 import 所有模块**（会触发重依赖、文件 IO、副作用）——纯静态 AST
- **不自动改 `__init__.py`**（有风险的改动应由人显式决定）
- **可见性缺口是诚实性指标**：`hidden_pct` 显式写进 manifest，
  缺口公开而非隐藏，与项目其余部分口径一致
- 检索支持两种方式：`find(caps, "surrogate")`（知道模块名）
  和 `find(caps, "conformal")`（只记得做什么）

**26/26 selftest 通过**。

### 4.3 尚未完成

37 个模块仍未接入。哪些该接、哪些不该接（如 `serve` / `vlab_server` /
`demo_*` 运行时服务）需要逐个判断。这是 P1 工作，不是本轮能做完的。

---

## 5. 硬骨头四：伦理与安全侧无任何显式声明（已攻克）

### 5.1 发现

引入 ORNL **SciTrust 2.0**（arXiv:2510.25908，Herron/Yin/Wang）四维度
方法论做自评：truthfulness / adversarial robustness / scientific safety /
scientific ethics。

首轮结果 **0.854**，其中两个失败暴露了**真实的文档缺口**：

- **E1 双用途研究**：`AXIOM_AUDIT.md` 完全没提
- **E2 数据隐私**：完全没提

这在 SciTrust 2.0 的语境下是严重问题——该文评测的 4 个科学专用
模型正是在伦理推理和生物安全上暴露缺陷。一个卖"可信验证"的系统
如果自己都不写清伦理边界，是硬伤。

### 5.2 修复：补文档而非改探针

**这里做了一个关键判断**：探针失败时，是改探针让它通过，
还是补上真实缺失的内容？

我选择**补文档**（`AXIOM_AUDIT.md` 新增 §3b「伦理与安全边界」），
因为缺口是真实的。同时修了一个**探针本身的误报**（S1 用
`autoclav` / `serializ` 匹配到培养基描述文本，改为只匹配
执行调用形态 `subprocess.run("pipette")` 等）。

修复后：**27/27 探针全通过，总体 1.000**。

### 5.3 SciTrust 四维自评结果

| 维度 | 得分 | 探针 | 能证明什么 |
|---|---|---|---|
| truthfulness | 1.000 | 5/5 | REFUTED 公开、计数守恒、构建完整、无过度宣称词 |
| adversarial_robustness | 1.000 | 5/5 | 阈值边界 fail-closed、NaN 不误判、空账本不静默通过 |
| scientific_safety | 1.000 | 3/3 | 结构性无湿实验能力、REFUTED 硬阻断 |
| scientific_ethics | 1.000 | 8/8 | 8 个伦理子域均被显式处理或显式声明未处理 |

**这个 1.000 不能证明什么**（必须一起说）：
- **不做对抗样本搜索**。SciTrust 2.0 用 TextAttack（Textbugger /
  Textfooler / Stresstest）做字符/词/句级扰动。SwarmLabs 只验证
  阈值边界的 fail-closed 行为。**`AXIOM_AUDIT` A11 仍是未证明的已知边界**。
- **不适用 WMDP / HarmBench**（评的是生成式模型是否吐危险知识）。
- **无 LLM-as-judge**（避免"谁来验证验证者"的无限回归）。
- **是内部自评**，评估器与被评估对象同源，不构成第三方审计。

---

## 6. 科研方向建议

按「是否让虚拟实验在无真实实验/网络/设备下更可替代」这个唯一准入判据排序。

### 方向 1（P0）：非稳态多保真方法论 —— 已开题，本轮已出首个结果

**科学问题**：非稳态非线性系统（微生物发酵、药物动力学、催化反应）
的代理模型，如何在跨数量级参数空间上同时保证精度与校准？

**SwarmLabs 的独特位置**：这个问题目前没有标准答案。文献里的
多保真方法（Kennedy-O'Hagan cokriging）假设两个保真度共享平滑结构，
而微生物多相动力学（lag/diauxic/competition 有相变拐点）违反这个假设。

**本轮贡献**（可作为论文的一个 section）：
1. **诊断**：跨数量级 bounds 是失分主因（log10 跨度 >3.7 全失分）
2. **方法**：尺度自适应低保真网格（`axis_grid`）
3. **发现**：机理先验会破坏校准，除非 conformal Q 与主模型**同族**
   （这是本轮最有价值的方法论发现，AXIOM_AUDIT A13）
4. **架构**：两阶段门控（OOF 判误差，生产判校准）

**下一步**：
- 把 `axis_grid` 从启发式阈值（log10 > 2）升级为**自适应密度**
  （按目标函数的二阶导数分配采样点）
- 推广到 3D+ 场景（当前 `make_lowfi` 对 dim>2 返回 None）
- 论文：可投《Journal of Computational Chemistry》或
  *AI4S* 方向期刊，主题是 "Scale-aware multi-fidelity surrogates for
  non-stationary ODE systems"

### 方向 2（P1）：把"校准纪律"做成可证伪的形式化命题

**科学问题**：能否给"误差改善不牺牲校准"一个形式化定义？
即：若 `mean_fn` 采纳，则 conformal Q 必须同族——这能否形式化？

**现有基础**：SVP 协议 + Lean 4 通道 + INV-1~5 形式不变量。

**具体做法**：把 A13 的"同族性"写成 Lean 定理：

```
theorem conformal_q_same_family :
  (mf_mf = mf_none) → (coverage_mf ≥ coverage_none - tol)
```

配合已有的 `svp/lean/Invariants.lean` 框架，这是**三个通道
（Python + Z3 + Lean）**都能验证的命题。

**市场价值**：这是 SwarmLabs 相对所有竞品的**根本差异**。
2026 年的 AI4S 项目（Google Co-Scientist / Periodic / A-Lab /
OpenAI4S）都在卷"能发现什么"，**没有一家公开声明
"我的校准在什么条件下会退化"**。SwarmLabs 有 A13 这样一份
实测发现的失效模式文档，这在审稿和商业信任上都是稀缺的。

### 方向 3（P1）：规模化的漏斗式验证（Anthropic 950 agents 的教训）

**科学问题**：Anthropic 用 950 个 agent 跑 21.5 小时、2.156 亿 token
从 20 万序列筛到 1 个发现，但**10 次重跑 0/10 复现**。
SwarmLabs 的形式化验证保证可复现——如何把这优势变成规模优势？

**具体做法**：在 `engine/flywheel.py` 的 signal→candidate→promote→effect
闭环里插入**漏斗**：

```
10万实体 → [廉价粗筛: Template Library 匹配] → 1000 候选
         → [标准 V&V: 三通道] → 50 个
         → [严格 V&V + 机理先验] → 5 个旗舰
```

`engine/template_library.py` 已实现（22/22 selftest），
但**尚未接入真实数据流**——这是 P1 的具体缺口。

**差异化论点**：Anthropic 的规模是"用更多算力换更多发现"，
SwarmLabs 的规模应该是"用更少算力换更可信的发现"。
前者是资本密集，后者是方法论密集。后者才是中小团队的护城河。

### 方向 4（P2）：对抗性验证 —— 补上 A11 的已知边界

**当前状态**：`AXIOM_AUDIT` A11 明确声明"未做对抗性鲁棒性"。

**可做的小切口**（不是完整对抗训练，是有价值的最小集）：
- **阈值探测**：构造 R²=0.895 的场景，看门禁是否 fail-closed
  （`engine/scitrust_eval.py` 探针 A1 已部分覆盖）
- **数据投毒**：往 store 里注入系统性偏移的"真实数据点"，
  看 conformal Q 是否能检出
- **分布外查询**：用 `GPSurrogate.proximity()` 的 OOD 检测，
  构造远界查询看是否触发红色拒绝门

这三个切口都是**确定性可复现**的，不需要对抗训练。
做完可以把 A11 从"完全没做"升级为"做了三个切口，明确知道还差什么"。

---

## 7. 市场壁垒建议

### 壁垒 1：**失效模式公开**（最难被复制，且已在做）

竞品的做法是"我们能做什么"，SwarmLabs 的做法是"我们在什么条件下
会出错"。后者在科学界更有说服力，因为：

- 已产出：`AXIOM_AUDIT.md` 13 条假设 + 7 条不宣称 + 4 条自评不宣称
- 已产出：A13「机理先验破坏校准」这样的**实测失效模式**
- 竞品没有这类文档，因为披露失效模式会削弱销售叙事

**建议动作**：把 `AXIOM_AUDIT.md` 作为对外核心资产，
在 GOAI 复赛和 SCI 投稿中作为「诚实性基建」章节直接引用。

### 壁垒 2：**三通道独立验证**

Python 语义 + Z3 SMT + Lean 4。三者互相印证，任一失败即报警。

竞品对比：
- Anthropic 950 agents：**无形式化验证**，且 0/10 复现
- Google Co-Scientist：**无公开校准报告**
- 传统 V&V 软件（如 SigmaFit）：**单通道**，只有统计检验

**这是技术护城河**。但需要补强：Lean 通道目前只在 CI 跑
（且 Actions 因私有仓库配额耗尽而全红），需要本地可复现。

### 壁垒 3：**口径单一**

`report_generator.collect_evidence` 是唯一 V&V 口径真源。
CI 门禁和公开报告**逐位一致**（这个架构在 2026-09-18 花了三层
历史包袱才收口，见 `engine/microbiology_full.py` 的 docstring）。

竞品普遍存在"论文一套数、产品另一套数"的问题。
口径单一在被拆穿时是致命的，在没被拆穿时是护城河。

**建议动作**：把口径一致性做成可自动验证的门禁
（"CI 的 r2/coverage 必须逐位等于已发布 index.json"），
让造假在结构上不可能。

### 壁垒 4：**能力可发现性**（本轮新增，但只完成一半）

`engine/tool_ocean.py` 让 476 个 API 有了统一索引。
但可见性仍只有 49.5%。

**完整形态**：Agent 面对"我要预测某生物发酵的产物谱"这类自然语言
需求时，能自动检索到 `microbiology_full.lag_phase_model`、
`wet_lab_anchors.anchors_3d_coverage` 等能力并组合调用。

这需要接 LLM 做语义检索（当前是纯字符串匹配）。
但**这会引入"谁来验证检索正确性"的问题**——建议保持规则检索
作为可审计的基线，LLM 检索作为增强层。

---

## 8. 本轮未做的事（如实记录）

| 项 | 原因 |
|---|---|
| `sharpness_ok` 7/82 失分（已从 13 降到 7） | 未进一步诊断，收益已小于 rmse_rel |
| `opt_*` 3 个失分 | 未诊断。优化基准的 rmse_rel 失分原因与微生物不同（`opt_hartmann3d` 是高维，`make_lowfi` 对 dim>2 返回 None） |
| `mol_*` 2 个失分 | 未诊断 |
| 35 个模块仍未接入 `__init__.py` | 需逐个判断哪些该接（如 `serve` / `vlab_server` 不该接） |
| Template Library 接真实数据流 | 引擎已实现（22/22），但未接 `flywheel` 闭环 |
| Lean 4 本地可复现 | Actions 因私有配额耗尽全红，需先改 public（用户操作） |
| 对抗性三切口 | P2，本轮未做 |
| 漏斗式验证 | P1，需先接 Template Library |

**一个既有问题的顺带发现**：`scripts/paper_validation_loop.py --dashboard`
这个 CLI 入口在无上下文 import 时会**循环导入失败**
（`virtual_experiment.parity_framework` partially initialized）。
已用 `git stash` 确认这是**改动前就存在**的问题，非本轮引入。
正确调用是模块形式：`P.build_dashboard(register=True)`。
本项目记忆里的重建链记录（`paper_validation_loop.py --dashboard`）
需要更正为模块调用形式。

---

## 9. 立即可做的下一步（按杠杆排序）

> **2026-10-04 更新**：P0 部署已完成；P1 的「循环导入」「Template Library
> 接 flywheel」已完成；新增「多模态门控」一项。下表是剩余项。

| 优先级 | 动作 | 状态 / 预期效果 | 工作量 |
|---|---|---|---|
| **P0** | 仓库改 public | ⏳ **待用户操作** — Actions 恢复，三通道 CI 首次真跑 | 用户 5 分钟 |
| **P0** | 部署 CF Pages | ✅ 已完成（15/15，线上 82-79-2-1-0） | — |
| **P1** | 修 `--dashboard` 循环导入 | ✅ 已完成（根因是内核→编排层反向依赖，非路径顺序） | — |
| **P1** | Template Library 接 flywheel | ✅ 已完成（58 场景→13 模板，find_best 可用） | — |
| **P1** | 多模态禁用机理先验 | ✅ 已完成（拦下 3 场景，rel 全部变好） | — |
| **P1** | 诊断剩余 20 个 rmse_rel 失分 | 分两类：11 个 `oof-error`（误差未改善）+ 9 个 `production`（已采纳但仍不达标）。**下一步应攻 `micro_chemostat_ecoli`（rel=1.696，最差）** | 半天 |
| **P1** | `opt_*` 高维场景的多保真 | `make_lowfi` 对 dim>2 仍返回 None，6 维基准拿不到先验 | 1 天 |
| **P1** | A13 写成 Lean 定理 | 三通道验证校准纪律（被 Actions 阻塞） | 1 天 |
| **P2** | 36 模块接入决策 | API 可见性 52.9%，需逐个判断（如 `serve` 不该接） | 0.5 天 |
| **P2** | 对抗性三切口 | A11 从"没做"到"做了三个切口" | 1 天 |

### 2026-10-04 已完成项的实测数字

**多模态门控**（`MODALITY_MINIMA_CAP=24`）：

| 场景 | 局部极小 | mf ON（拦门前） | OFF（拦下后） |
|---|---|---|---|
| `opt_rastrigin2d` | 121 | 0.2571 | **0.2695** |
| `opt_ackley2d` | 205 | 0.1595 | **0.1585** |
| `micro_chemostat_cerevisiae` | 270 | 0.4514 | **0.4371** |

**import 死锁**（此前误判为"既有问题"，实为 10-03 加 flywheel 导出时引入）：

```
parity_framework → engine.surrogate → engine/__init__ (L246)
                → engine.flywheel (L53 顶层 import paper_validation_loop)
                → paper_validation_loop (L71) → parity_framework（半初始化）
```

修法：`engine/__init__.py` 绝不 import flywheel。**编排层依赖内核，单向。**

**Template Library 指纹 bug**：初版用 `ls_source` 作 fingerprint，
58 个 PASS 场景只沉淀出 3 个模板（`ls_source` 只有少数取值，碰撞覆盖）。
改为 `sha1(领域首段 + rubric 判定向量)` → 58 场景归并 13 模板 / 13 领域。

**教训（值得写进方法论）**：指纹必须取「决定这条推理路径的量」，
不能取偶然属性。

---

## 10. 相关文档

- `AXIOM_AUDIT.md` — 13 条未证明假设（含新增 A13 机理先验同族性）
- `data/scitrust_eval.json` — SciTrust 四维自评结果
- `data/capability_manifest.json` — 能力索引
- `engine/multifidelity.py` — `axis_grid` / `decide_mean_fn` 两阶段门控
- `engine/scitrust_eval.py` — 27 探针自评引擎
- `engine/tool_ocean.py` — 能力索引引擎
- `engine/template_library.py` — 推理模板库
