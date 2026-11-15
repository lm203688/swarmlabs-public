# CI 常红根因诊断（2026-09-18）

对象：`lm203688/swarmlabs` · workflow `SwarmLabs CI/CD` (`.github/workflows/ci-cd.yml`)
现象：#249 / #250 / #251 **连续 failure**（其余 Tech Radar / 夜间运维 / Deploy 全 success）

---

## 结论摘要

CI 红灯是**三层互相独立的病因**叠加，**全都不是「引擎算错了」**：

| # | 病因 | 状态 |
|---|---|---|
| 1 | 远端仓库缺 34 个源码文件 → `import engine` 直接失败 | ✅ 已修（2 个原子提交，+34 / -0） |
| 2 | CI 依赖的验证路径**自己手搓 GP、口径与项目已发布的 V&V 不一致** | ✅ 已修并复验（52/52） |
| 3 | 该基准对若干场景设计偏弱（R² 被拉低） | ✅ 已修：删除重复 harness，改为**完全委托报告路径**（§3） |

**收口状态：CI #253 / #254 / #255 全绿**（#254 为 `push` 自动触发，顺带验证真实链路）。

> ⚠️ 本文档经过一次**自我纠正**。首轮诊断把 coverage 失真归因于「噪声标定与目标尺度脱钩」，
> 只对了一半；据此做的**引擎级改动已完整回退**（见 §2.4）。
>
> ⚠️ §3–§5 已于同日**二次更新**：原先列为「待拍板」的 4 项，经项目方决策**全部执行完毕**（§6）。

---

## 1. 第一层：远端仓库缺 34 个源码文件（已修）

远端 `engine/surrogate.py` 早已在远端，它 `from .discovery import SINDy` / `.elo` / `.pinn` 等
7 个模块——**这些模块一个都不在远端**，故 `import engine` 必炸。规模：本地 `engine/*.py` 53 个，
远端只有 40 个。

两个关键坑：

- **本地 `origin/main` 引用是过期的**（沙箱内 `git fetch` 连不上 github.com）。它显示「落后一个月」，
  而远端真实 HEAD 是当天 00:35。信本地引用会得出完全错误的结论 → 必须走 API 取真实状态。
- 仓库里原有的 `api_push_tree.py` 是「用本地 HEAD 整棵树替换远端」，而本地 HEAD 只跟踪 398 个
  文件、远端有 953 个 → **用它修缺件会一口气删掉远端 584 个文件**。故新写
  `scripts/push_files_atomic.py`（Git Data API + `base_tree` 继承，**只增不删**）。

| 提交 | 内容 | 远端 blob |
|---|---|---|
| `798fee5` | import 闭包 19 文件 + 根 `README.md`/`LICENSE` | 953 → 974（+21/-0） |
| `f1e7e9b` | 补齐 engine 整包 13 文件（含 `__init__` 必需的 `brand`） | 974 → 987（+13/-0） |

复验：变更 **+34/-0**；远端 engine **零未解析导入**；本地跑 CI 的 step6/step7
（`get_strain_registry()` 69 株 ≥60、`engine.serve.OPENAPI_SPEC` 10 端点 ≥8）全过。

---

## 2. 第二层：CI 验证路径的口径错（本轮修，这是主因）

### 2.1 病灶

`engine/microbiology_full.py::validate_full_scenarios` 原先**自己手搓 GP**：

```python
gp = GPSurrogate(noise_var=noise**2, lengthscale=ls)   # ← 不传 y_mean / y_scale
gp.fit(train_xs_norm, train_ys)
...
within = np.abs(pred_mean - test_ys) <= 1.96 * pred_std
converged = coverage > 0.55
```

两处都错，且**都是「口径」错**：

**(a) 它绕过了对外部署的模型。** 生产路径是 `SurrogateEngine.fit`，它会做 `y_scale = std(y)`
的 z-score 归一化 + 3% 噪声地板 + ARD 选择。而这里用**裸 `GPSurrogate`**，`_rscale` 默认 1.0
→ `predict()` 返回的 `std` **恒 ≈1.0**，与目标量纲、与模型真实残差都脱钩。

**(b) 它拿带噪 oracle 既当训练观测、又当验证真值。** 场景的 `f` 多数自带 5–10% 乘性测量噪声
（`_make_oracle("ecoli_glucose", noise_std=0.05)`）。这样 GP 拟合的是噪声、验证的也是噪声。

### 2.2 证据：为什么「45/52 通过」是巧合，不是质量

| 场景 | y_std | RMSE | 旧 coverage |
|---|---|---|---|
| `micro_antibiotic_2d` | 0.0018 | 0.001 | **1.000** |
| `micro_fedbatch_ecoli` | 34 | 4.87（**拟合好**） | **0.100** |
| `micro_lag_ecoli` | 1510 | 832 | **0.000** |

`pred_std ≡ ≈1.0` 是个恰好卡在中间的常数：量纲远小于 1 的场景被**过度覆盖**（coverage 白拿
1.000），量纲远大于 1 的场景被**欠覆盖**（0.000）。即旧口径下「45/52」只反映目标量纲恰好大于
1 的场景数，**与拟合质量无关**。

项目自己的文档早写过同一结论：

- `report_generator._noise_free_f` docstring：「拿带噪 oracle 既当训练观测又当验证真值……
  **R² 必为负**——这是错误的 V&V 设计，不是实现 bug。」
- `report_generator._calibrate_scale` docstring：「**此前 15 个场景被判 REFUTED 的元凶**：
  标定所用的代理必须与最终模型同构（均含 z-score）……若误用未归一化的裸 `GPSurrogate`，
  残差/区间比会在量纲上错位（差一个 `y_scale` 因子）→ 覆盖率惨崩。」

**报告路径早已按这两条修过，CI 验证路径当时漏修。**

### 2.3 修法：复用报告路径的同一口径（口径单一）

```python
dom = dict(sc); dom["f"] = _noise_free_f(sc)               # 无噪真值（虚拟实验=仿真）
eng = SurrogateEngine(dom); eng.fit(train_xs, train_ys)     # 与部署模型同构
kappa = _calibrate_scale(dom, train_xs, train_ys)           # 独立校准集 seed 7311，只放宽
pred_mean, pred_std = eng.predict(test_xs); pred_std *= kappa
```

- 真值：`_noise_free_f` 剥离末段加性噪声
- 模型：`SurrogateEngine`（z-score + 3% 噪声地板 + ARD）—— 对外部署的同一模型
- 区间：`_calibrate_scale` 的 κ（只放宽不收窄，上限 40）—— 覆盖率靠**诚实标定**回正，
  **不靠加宽区间粉饰**
- 新增 `r2` / `rmse_rel`（= rmse / std(y_true)）与 `coverage` 并列暴露：
  **coverage 衡量 UQ 是否诚实，r2/rmse_rel 衡量模型是否有用，必须分开看**，
  避免「区间宽到能覆盖」伪装成达标

**未下调任何阈值**：`converged = coverage > 0.55` 与 CI 的 `>= 50` 一字未改。

### 2.4 曾尝试并回退：不要在 `GPSurrogate` 里新建标定机制

首轮我在 `GPSurrogate.fit` 内加了「失配感知方差地板」（用闭式 LOO 残差抬高 `nv`）。
它在 CI 路径上确实把 52/52 拉绿了（coverage 均值 0.963、cal_err 0.042，rmse 也改善），
**但已完整回退**，因为它与 κ **机制重复**：两套「什么叫可信」并存，违反项目「口径单一」红线，
且会波及全部消费方（`guard`/`serve`/`sdk`/`multifidelity`/`paper_validation`…），
改动对外数字的风险远大于收益。回退后 `engine/surrogate.py` 与改动前 **sha256 逐字节一致**。

（闭式 LOO 公式本身经验证正确，与逐点重拟合的暴力 LOO 吻合到 3–4 位有效数字：
`micro_ecoli_glucose` 0.05310 vs 0.05323、`micro_fedbatch_ecoli` 0.8502 vs 0.8490。
若将来需要「模型自身残差尺度」的诊断，可复用此式，但**不要再造第二套标定**。）

---

## 3. 第三层：基准偏弱 —— 修法不是「调基准」，而是「删掉重复的基准」

修完口径后（仍是 CI 自己的均匀随机 + 30 点）：

| 指标 | 值 |
|---|---|
| converged | **52 / 52**（`coverage_rate` 100.0） |
| coverage | 均值 0.967，≥0.90 的 49 个 |
| R²（对照无噪真值） | 均值 0.777，≥0.90 的 27 个，min **-0.784** |
| rmse_rel | 均值 0.353 |
| κ | 均值 7.89，最大 34.78 |

同一批场景在**报告口径**下 R²≥0.954：

| 场景 | 旧 CI 基准 R² | 报告口径 R² |
|---|---|---|
| `micro_ecoli_glucose` | 0.047 | **0.9922** |
| `micro_doe_ecoli_monod` | -0.784 | **0.9983** |
| `micro_lag_ecoli` | 0.435 | **0.9988** |

### 3.1 先试过「调基准」，实测更差 —— 所以放弃

曾尝试把 CI 采样换成 LHS 以逼近报告口径，实测**更糟**（`micro_ecoli_glucose` R² 0.047 → **-232**）。
原因是该场景为「饱和区 + 膝部陡变」，全局 R² 会被单个膝部点拖垮（这正是
`report_generator._core_regime_mask` 记录的现象，公开 index 因此另有 `r2_core` 字段）。
**为了贴近对外数字去调采样，与下调阈值同类**，故不做。

### 3.2 正解：CI 不再「模仿」报告，而是**调用**报告

关键线索写在该函数的 docstring 里：

> `collect_evidence`：**「这一步刻意与渲染分离：证据可用于报告、也可用于 CI 门禁或 API」**

也就是说报告路径早就为此设计好了入口，CI 当年却另写了一份。**问题不是「CI 基准不够好」，
而是「同一件事有两份实现」**。修法即**删除重复实现**，让 CI 完全委托：

```python
from engine.report_generator import collect_evidence, all_scenarios
evidence = collect_evidence(all_scenarios()[key])   # 与公开报告逐字同一条路径
```

CI 侧因此自动获得：**LHS 空间填充 + 按维度取 `_DATA_SIZES`(30/50/80/120) +
跨数量级轴 log10 坐标升级 + ARD + κ 标定 + 无噪真值**，一行都不用抄。

**判据：修「同一件事有两份实现」的问题，正确结果是「少一份实现」，不是「再改一份」。**

### 3.3 结果：两侧逐位相同

| 验证 | 结果 |
|---|---|
| CI 口径 vs 已发布 `reports/index.json` | **51/52 逐位相同**（唯一差异是 §6 故意修的 `micro_lag_ecoli`，且**变好**） |
| `micro_ecoli_glucose` r2 | 两侧同为 **0.9922**（旧 CI 基准下是 0.047） |
| 全量 62 场景 PASS | **62/62**，r2 min **0.954**，coverage min **0.917** |
| 耗时 | **3.1–3.4 s / 52 场景**（CI 无压力） |

### 附带发现（真实、非阻塞）

1. ~~**`micro_lag_ecoli` 场景本身不物理**~~ → 已在 §6-③ 修复。
2. ~~**CI 的 `coverage_report(scenarios)` 传参形式无效**~~ → 已在 §6-④ 修复（并补了两个静默缺口）。
3. **远端仓库原本既无 `README.md` 也无 `LICENSE`**（本地 `1de10d6` 那次从未落地），已补。
4. **本地工作树 vs `HEAD` 有 469 处未提交改动**，远端靠 API 逐文件推送同步 —— git 历史已严重
   脱节，这是「缺件」问题的温床。
5. **`scripts/push_files_atomic.py` 自身有个点文件 bug**：路径归一化误用
   `r.lstrip("./")`（`lstrip` 参数是**字符集**、不是前缀），导致 `.github/...` 被剥成
   `github/...` 并**静默 SKIP** —— 即该工具此前无法推送任何点文件路径。已在 §6-④ 一并修复。

---

## 4. 验证证据（均为实跑，非推断）

| 验证项 | 结果 |
|---|---|
| CI step5（改为 `scripts/check_engine_gate.py`） | **52/52 PASS** · 最差 r2 **0.954** / coverage **0.917** · 44 能力 / 0 缺失 |
| CI step6 菌株库 | 69 ≥ 60 · PASS |
| CI step7 API 端点 | 10 ≥ 8 · PASS |
| `audit_closure()` | 52/52 · coverage_rate 100.0 · 44 能力 · 0 缺失 · **CLOSED** |
| 报告路径回归（8 场景 `collect_evidence` vs 已发布 index.json） | r²/coverage **8/8 完全一致** |
| `engine/surrogate.py` | 回退后与改动前 **sha256 逐字节一致**（`43231fddc23f1d92`） |
| 论文链路回归（`lag_phase_model` 无 `X_max` 路径） | 输出与原实现 **逐位相同**（`max|Δ| = 0`） |
| **线上 vs 本地**（`index.json` + `gate_ledger.json`，62 场景逐字段） | **0 处差异** |

线上复核：`GET /reports/index.json` 与 `GET /reports/gate_ledger.json` 均 `PASS 62 / MARGINAL 0 /
REFUTED 0`，`/v3/gate` 总数 62、`n_blocked 0`，`/v3/gate/micro_lag_ecoli` 返回
`gate=PROCEED · r2=0.9999 · coverage=1.0 · rmse_rel=0.0085`。

> ⚠️ 复核坑：CF 边缘会把 `/reports/*.json` 缓存（首次读仍返回旧值）。核对线上必须加
> cache-buster（`?cb=<rand>` + `Cache-Control: no-cache`），否则会误判「部署未生效」。

---

## 5. 提交清单

| 提交 | 内容 | 远端 blob |
|---|---|---|
| `798fee5` | import 闭包 19 文件 + 根 `README.md` / `LICENSE` | 953 → 974（+21/-0） |
| `f1e7e9b` | 补齐 engine 整包 13 文件（含 `__init__` 必需的 `brand`） | 974 → 987（+13/-0） |
| `f266a39` | `validate_full_scenarios` 改走报告口径 + 诊断文档 | 987 → 988（+1/-0） |
| `7897fce` | 补推 `scripts/push_files_atomic.py`（此前只在本地） | 988 → 989（+1/-0） |
| `4bd7277` | **4 项口径修复**（§6）+ `scripts/check_engine_gate.py` | 989 → 990（+1/-0） |

全程 **+37 / -0**，无一次删除远端文件。

---

## 6. 4 项口径决策：执行记录

项目方拍板「4 项都要完成」，逐项如下。

### ① CI 断言纳入拟合质量

`validate_full_scenarios` 的 `converged` 语义从 `coverage > 0.55` 升级为
**「按对外报告同一门禁判 PASS」= `R² ≥ 0.90` 且 `coverage ≥ 0.90`（单侧下界）**。
阈值一字未改，只是把**同一个门禁**用上：新增 `r2_min` / `coverage_min` 断言与
`worst_scenario` 输出，让 CI 真的看得见拟合质量，而非只看区间宽度。

### ② 两侧 R² 口径统一

见 §3.2：**删除 CI 侧的重复 harness，改为完全委托 `report_generator.collect_evidence`**。
最直接的收益是**删掉了一份「裸 GP + 带噪真值」的错误实现**，而不只是「把数字调好看」。

### ③ `micro_lag_ecoli` 容量上限（场景不物理）

`lag_phase_model` 读入 `X_max` 却从不使用，用纯指数累积 `X0·exp(∫μ)`：t=20h 输出
**7048 g/L**，而场景声明 `X_max = 15.0`（E. coli 实际约 100–200 g/L），值域跨 **704887×**。

改为「**声明了 `X_max` 才启用**」的逻辑斯蒂自限积分：

```
X_j = X_{j-1} + μ_eff · X_{j-1} · Δt,   μ_eff = μ · max(0, 1 − X_{j-1}/X_max)
```

| | 修前 | 修后 |
|---|---|---|
| t=20h 输出 | 7048.87 | **14.9458**（≈ X_max） |
| 值域比 | 704887× | 1493× |
| 单调非减 | — | ✅ |

**关键约束**：论文验证链路（`paper_validation` / `paper_audit` 的 `paper_baranyi_1994`）
传入的 params **不含 `X_max`**，实测输出与原实现**逐位相同（`max|Δ| = 0`）** → 零扰动。

已**同步重生成**该场景对外产物（`index.json` / `gate_ledger`(公开+data) / `benchmark` 单场景文件），
并证明**只有该条目变更**：总数 62、PASS 62、测试点 3110 均不变。

> 附带确认：`predict_baranyi`（另一份 Baranyi 实现）是**死代码**，从未被调用。

### ④ `ci-cd.yml` 传参清理 + 两个静默缺口

| 问题 | 修法 |
|---|---|
| `coverage_report(scenarios)` —— 把场景**列表**当 `seed` 传（签名为 `coverage_report(seed=...)`），参数错位、静默失效 | 改 `coverage_report()` |
| 断言写在内联 `python -c` 里，**无法本地原样复跑** —— 这正是「CI 红而本地绿」长期查不出的原因 | 提成 `scripts/check_engine_gate.py`，本地跑的就是 CI 那一条 |
| `on.push.paths` 过滤**没有 `engine/**`** → **引擎改动根本不触发 CI** | 补 `engine/**` 与 `.github/workflows/**` |
| `push_files_atomic.py` 的 `lstrip("./")` 静默吞掉点文件路径（见 §3 附带发现 5） | 改为只剥一次显式 `./` 前缀 |

