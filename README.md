# SwarmLabs · 面向生物工艺的可信代理建模与实验设计层

> **[CI](https://github.com/lm203688/swarmlabs-public/actions) ·
> [Honesty Gate](https://github.com/lm203688/swarmlabs-public/actions/workflows/honesty-gate.yml) ·
> [Nightly Ops](https://github.com/lm203688/swarmlabs-public/actions/workflows/nightly-ops.yml) ·
> [Lean Invariants](https://github.com/lm203688/swarmlabs-public/actions/workflows/lean-invariants.yml) ·
> [Skills CI](https://github.com/lm203688/swarmlabs-public/actions/workflows/skills.yml)**
>
> [![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
> [![Code of Conduct](https://img.shields.io/badge/Code%20of%20Conduct-Covenant%20v2.1-orange.svg)](CODE_OF_CONDUCT.md)
> [![Security Policy](https://img.shields.io/badge/Security-90%20day%20disclosure-red.svg)](SECURITY.md)
> [![Python](https://img.shields.io/badge/Python-3.11%2B-green.svg)](CONTRIBUTING.md)
> [![Coverage audit](https://img.shields.io/badge/Coverage-93%2F95-brightgreen.svg)](data/trust_surface.json)
> [![Honesty](https://img.shields.io/badge/Honesty--95%20scenarios--91%20PASS%20%2F%203%20MARGINAL%20%2F%201%20REFUTED-yellow.svg)](data/gate_ledger.json)


> **定位（2026-10-08 收缩后）**：SwarmLabs 是面向**生物过程（发酵 / 微生物 / 酶催化）**
> 的可信代理建模层——每个结论附带**校准区间**与**适用域边界**，越界自动阻断；
> 输出不是预测值，而是**下一个最该做的实验**。
>
> **为什么收缩**：项目自测的三级泛化基准（G1 参数外推 / G2 留一维 / G3 留一学科）
> 实测结果为 **G3 = 0/14 学科通过**（平均相对误差 206%~1459%）。这与"跨学科通用"
> 的叙事不符，是 **GP 代理在 7D+ 维度的方法学局限**，非实现缺陷。
> 我们选择**如实报告并收缩定位**，把资源集中在真正做得住的方向。
> 完整实测证据见 [`data/g3_aligned_benchmark.json`](data/g3_aligned_benchmark.json)。

---

## 0. 诚实边界（先读这一节）

| 主张 | 实测状态 | 证据 |
|---|---|---|
| 跨学科泛化（G3） | 🔴 **未达成**——0/14 学科通过 | `data/g3_aligned_benchmark.json` |
| 参数外推（G1） | 🔴 **未达成**——通过率 0 | `data/generalization_bench.json` |
| **外推压力测试** | 🔴 **73/82 REFUTED**（n_registry=95 已注册 / n_tested=82 实测 / n_axis=771）——仅 3 PASS、6 MARGINAL、73 REFUTED | `frontend_fixed/reports/heldout_index.json:extrapolation_stress` |
| 有真实实验真值 | 🔴 **0 个场景**——95 场景全部为已发表模型的 GP 代理（模拟） | `data/trust_surface.json:overall.honesty` |
| 文献溯源质量 | 🟡 95/95 有文献字符串溯源，但**仅 1 篇为同行评审 DOI**，94 篇为教材/网络资源 | `overall.honesty.reference_quality` |
| 生物过程内插可信 | 🟢 **达成**——coverage 93/95、fail-closed 门禁、A1~A14 失效模式公开 | `data/gate_ledger.json` |

**我们不做的事**（明确边界，避免过度主张）：
- ❌ 不做跨学科通用验证（G3 实测已证伪）
- ❌ 不做 LLM 假设生成 / 文献辩论（定位是**验证层**，不是创意层）
- ❌ 不做参数军备竞赛（纯 numpy + CPU，零 GPU、零付费 API）
- ❌ 不宣称"替代一切真实实验"（仅在生物过程域、且在适用域边界内成立）
- ⚠️ 分子场景需 rdkit 依赖：当前实现 2/2 个分子场景（Suzuki coupling + esterification yield），rdkit 缺失时这两个场景被静默跳过——独立不变量层会捕获此截断

---

## 1. 我们真正有什么

- **真实模型库**：95 个验证场景（`data/gate_ledger.json`），底层是可调用的解析物理/生物/化学模型
  （Monod 生长、Arrhenius、Lotka-Volterra、Hodgkin-Huxley、Black-Scholes 等），**非示意性占位值**
- **可信执行层（VeritasGuard）**：每个输出附带文献证据链、置信度评分、反事实分析、
  **内容决定论的可复现证书**（`core/trust_layer.py`，签名与签发时间无关，可被第三方独立复算）
- **fail-closed 验证闸门**：`PASS → PROCEED` / `MARGINAL → 人工复核` / `REFUTED → 阻断自主行动`
- **失效模式公开**：A1~A14 共 14 条已证明的公理与边界（[`AXIOM_AUDIT.md`](AXIOM_AUDIT.md)）
- **对抗性探针 3/3 通过**：阈值 fail-closed、数据投毒检出（1011× 误差放大）、OOD 守卫
- **结构化科研实体库**：157,859 条（works / authors / concepts / references 等），
  来源 Crossref + arXiv，**零成本、免鉴权**（详见 `data/public_facts.json`）
- **多 Agent 权限隔离**：Planner / Executor / Verifier 三角色，`allow_write=false` +
  审批闸门 + 双流审计（Trace + Audit）

---

## 1.1 关于"真实实验锚点"的诚实区分（2026-10-15 补齐，板块 05 P2-6；2026-10-16 晚扩为三层）

> 项目内存在**三类锚点**，来源、可追溯强度、可外宣口径**完全不同**，对外表述不得混用：
>
> | 类型 | 文件 | 数量 | 性质 | 用途 |
> |---|---|---|---|---|
> | **`real_anchors`**（真实文献原始数据） | `data/real_anchors.json` | **6** | 参数级"从已发表来源派生的实测数据引用"，其中 **2 条来自可公开下载的数据存档**（Zenodo DOI + `file_sha256` 可复算），**4 条来自学术论文图表数据**（有 `paper_doi` / `paper_title` 但无独立数据 DOI）——两者性质不同，见下方诚实说明 | 为场景中的**具体参数**（E. coli Monod 的 K_s / μ_max、酵母乙醇抑制的 K_i / Hill n / OD_max / 99% 抑制浓度）提供**参数级来源**，是"这些数字从哪来"的诚实回答 |
> | **`wet_lab_anchors`**（湿实验锚点） | `frontend_fixed/reports/wet_lab_anchors.json` | **19** | 人工策展、具名人类确认者、可追溯至已发表论文的实测数值，含 `AGREE/NEAR/CONTRADICTED/UNANCHORED` 场景分级 | 作为 GP 代理的**外部真值对照**，是"替代真实实验"叙事的**唯一可信证据层**（但见下文"覆盖锚点"边界说明） |
> | **`synthetic_anchors`**（合成锚点） | `data/synthetic_anchors.json` | **18** | 从场景自身的 `published` 字段**原样抽取**、`anchor_class="synthetic"` 恒定、含 `not_a_validation=true` 标记 | **仅用于算法压测与分诊**——回答"该场景是否带可引用的参数"，**不构成任何真实验证** |
>
> **⚠️ 使用纪律**：
> - `scripts/check_public_numbers.py` 与 CI 诚实口径**只认 `wet_lab_anchors`**，
>   任何对外"锚点数量"数字必须锚定此文件。
> - `synthetic_anchors` **禁止**进入 `wet_lab_anchors.json`，也不作为
>   "覆盖锚点"的补充计数。二者在代码上是硬隔离的两条流水线。
> - `real_anchors` 是**参数级**引用，不等同于 `wet_lab_anchors` 的**场景级**
>   验证；6 条 real_anchors 中仅 **2 条**（E. coli Monod 的 K_s / μ_max）
>   有可下载的数据存档 DOI 与 `file_sha256` 可逐字节复算；另外 4 条
>   （酵母乙醇拟合的 OD_max / K_i / Hill n / 99% 抑制浓度）只有论文的
>   `paper_doi` 与图表派生值，**不具备独立数据存档的可复算性**。
>   对外表述应区分这两档，不得把 6 一律称作"带 DOI 的原始数据"。
> - 当前实测：**0/95 场景有真实实验真值**（`data/trust_surface.json:overall.honesty`）
>   ——即使 19 个 wet_lab 锚点、6 个 real_anchors 存在，它们也仅覆盖少量场景
>   的关键参数，不等于"该场景已被真实实验验证"。
>
> 完整实现见 `engine/synthetic_anchors.py`（分诊）与
> `engine/anchor_independence.py`（19 锚点来源独立性分析）。`real_anchors`
> 的采集记录在 `data/real_anchors.json:provenance`（含采集时间、来源 URL、
> 每条 anchor 的 `provenance` 字段），可原样复现。

---

## 1.2 关于"安全贝叶斯优化"的诚实定位（2026-10-14 更正）

> 本项目的 `engine/optimization/` 模块提供的**不是严格意义上的 safe Bayesian
> Optimization**（safe BO 有明确的可行域约束 `g(x) ≥ 0` 与违反上界监控机制）。
> 本项目的实现更接近 **"constrained recommendation with rejection rationale"**：
> - 用 GP 预测 + 校准区间给出候选点
> - 用 fail-closed 门禁给出**接受/拒绝建议**（而非保证可行）
> - 拒绝时附带上"为什么拒"的可解释理由（超参数/超出历史域/预测方差过大）
>
> 因此对外宣传应使用"约束推荐 + 拒绝理由"这一表述，而非"安全贝叶斯优化"。
> 严格 safe BO 的实现（BoTorch `SafeOpt` / `gpjax.sbo`）不在本项目范围内。

---

## 2. 快速开始

```bash
# 1. 安装（纯 CPU，无需 GPU / 付费 API）
pip install -e .

# 2. 运行端到端 demo（3 点 → 1 结论，<5s）
swarmlabs demo

# 3. 列出可用场景
swarmlabs scenarios --limit 20
```

### 其他命令

```bash
# 查看门禁
swarmlabs gates

# 查看湿实验锚点
swarmlabs anchors

# 环境自检
swarmlabs doctor

# 重建所有产物
swarmlabs rebuild

# 启动本地工作台（Web UI）
swarmlabs workbench
```

### 运维与校验

```bash
# 生成可复现证书（数据决定论签名）
python -m core.trust_layer

# 校验对外数字口径一致性（CI 门禁）
python scripts/build_public_facts.py     # 重建事实源
python scripts/check_public_numbers.py   # 校验 README/前端/文档数字

# 运行飞轮（运维用，非用户入口）
export PYTHONPATH="$(pwd)"
python -m engine.flywheel --domain biology --runs 3
```

---

## 3. 项目结构

```
swarmlabs/
├── core/
│   ├── trust_layer.py             # ★ VeritasGuard 可信执行层（可复现证书 + 独立复算）
│   ├── autonomous_loop.py         # 自主循环（Planner/Executor/Verifier 编排）
│   ├── task_contract.py           # 任务契约（证据收集与验证）
│   └── ...                        # 共 20+ 核心模块
├── engine/
│   ├── flywheel.py                # 飞轮主循环
│   ├── surrogate.py               # GP 代理 + ARD 超参 + 闭式 LOO 守卫
│   ├── conformal.py               # 保形预测（有限样本修正，无作弊）
│   ├── discovery.py               # SINDy/STLSQ 稀疏辨识 + bootstrap CI
│   ├── pinn.py / neural_operator.py  # PINN + DeepONet（纯 numpy，CPU）
│   ├── emukit_adapter.py          # Saltelli Sobol + EI + 贝叶斯优化
│   ├── experiment_proposal.py     # ★ 结论 + 边界 + 下一实验（三件套）
│   ├── microbiology_full.py       # 微生物场景库（含真实 Saltelli Sobol 敏感性）
│   ├── generalization_bench.py    # ★ G1/G2/G3 三级泛化基准（诚实报告失败）
│   └── ...                        # 共 102 个模块
├── scenarios/                     # 学科场景库
├── skills/registry/               # 机读 Skill Registry（JSON Schema）
├── agentteams/                    # 多 Agent 团队编排（权限隔离 + 审计）
├── data/
│   ├── public_facts.json          # ★ 对外数字的单一事实源
│   ├── trust_surface.json         # 可信面（含文献溯源/置信度/rubric/honesty）
│   ├── gate_ledger.json           # 门禁账本（91 PASS / 3 MARGINAL / 1 REFUTED）
│   └── generalization_bench.json  # 泛化基准结果（含失败）
├── docs/                           # 公共产品文档（GOVERNANCE / FOR-REVIEWERS 等）
└── scripts/
    ├── build_public_facts.py      # 事实源生成
    ├── check_public_numbers.py    # 数字口径 CI 门禁
    └── verify_all.py              # 全量验证
```

---

## 4. 核心创新

### 1. 内容决定论的可复现证书
> `core/trust_layer.py` 的签名**剔除时间戳**——同一份数据无论何时签发都得到相同
> SHA256，任何内容变化都导致签名改变。第三方可用 `verify_certificate()` 独立复算。
> （此前签名含 `issued_at`，每次运行必变，只能算"防篡改封装"，不能证明"可复现"。）
>
> **⚠️ 诚实边界（arXiv:2608.20439 后）**：SHA256 仅能证明"内容未变"，
> **不能证明内容可信**。近期跨参数重连攻击（arXiv:2608.20439）已表明
> 单一哈希签名可被绕过。因此本项目的 `provenance_guard` 只作为
> **完整性检查层**，不单独承担"内容真实性"证明。真实性依赖多通道
> （Lean/Z3 形式化 + fail-closed 门禁 + 湿实验锚点 + 独立复算），
> 详见 `docs/trust_layer.md` 与 `AXIOM_AUDIT.md`。

### 2. 实验作为「可验证输出」
> 输出不是预测值，而是 `{conclusion（带 95% 校准区间）, boundary（适用域边界）,
> next_experiment（含 expected_improvement 断言）}` 三件套
> （`engine/experiment_proposal.py`）。`expected_improvement` 本身可被事后证伪。

### 3. 真实的全局敏感性分析
> `sobol_sensitivity()` 用 Saltelli (2010) A/B/AB_i 估计器，**无随机占位**；
> 已用 Ishigami 解析解验证（S1: 0.33/0.46/0.01 vs 理论 0.31/0.44/0.00）。

### 4. 三级泛化基准（含诚实失败）
> `generalization_bench.py` 实测 G1/G2/G3 三层外推。**结果全 0，我们如实公开**——
> 这比一个没有基准的"跨学科"主张更有价值。

### 5. fail-closed + 失效模式公开
> A1~A14 共 14 条已证明的公理与边界，配可运行的最小复现脚本。

### 6. Lean + Z3 + Python 三通道形式化验证
> 本项目的形式化验证采用**多通道集成 + CI 门禁**范式，而非单一定理库。
> 三个通道分工：
> - **Lean 4**（`svp/lean/`）：核心不变量（invariant）与算法安全性质
> - **Z3**（`svp/z3/`）：SMT 可满足性判定的边界与约束验证
> - **Python 参考实现**（`svp/python/`）：数值验证与对拍
>
> **⚠️ 对外口径（2026-10-14 更正）**：过去曾出现"19 个定理"或"147 个定理"
> 等绝对化宣称，均**不采用**——Lean 与 Z3 的判定强度不同（Lean 判定为
> "已验证"、Z3 判定为"当前 SMT 求解器下不可满足"），混称"定理"会夸大。
> 正确口径为："**Lean + Z3 + Python 三通道 + CI 集成**，每个 invariant 都
> 有对应三通道证据与 CI 门禁"。具体证明计数与强度分级见
> `svp/PROOFS_MANIFEST.json` 与 `reports/formal/`。

---

## 5. 关键指标（唯一来源：`data/public_facts.json`）

| 指标 | 数值 | 出处 |
|------|------|------|
| 验证场景 | 95 | `trust_surface.json:overall.scenarios` |
| 门禁账本 | 91 PASS / 3 MARGINAL / 1 REFUTED | `gate_ledger.json:counts` |
| **PASS 分级判定（A-3）** | **5 PASS_FULL / 83 PASS_PARTIAL / 3 PASS_WEAK**（91 个 PASS 场景按支撑证据完整度分级；主导缺口 = 83 个场景无湿实验锚定，而非布尔 PASS） | `data/ablation_plan_report.json:grade_distribution` |
| **参数扰动稳健性（A-4）** | **84/91 PASS 场景在 ±10%/±20% 参数扰动下 verdict 不翻转**（7 个易翻场景已列名） | `data/sensitivity_ablation.json` |
| **第二后端复算（B-1）** | **67 精确一致 / 15 方向一致 / 13 矛盾**（RF+Conformal vs GP 同数据；13 个矛盾场景结论对模型家族敏感，已列名） | `data/second_backend_replication.json` |
| **证据等级审计（B-2）** | **1/18 基准升到 auto-extracted 级**（Zenodo 机读原始数据 + sha256 校验；17 个 1949–1994 经典基准无机器可读原始数据，系统性检索确认）；该基准 31 项 claim **13 项复现**，判定 `PARTIALLY_REPRODUCED`；数据真实性 0.974×噪声地板 | `data/published_data_audit.json` |
| rubric 满通过 | 62 / 95 | `trust_surface.json:overall.rubric_full_pass` |
| coverage 达标 | 93 / 95 | `trust_surface.json:overall.coverage_ok` |
| engine 模块 | 102 | `count(engine/*.py excluding __init__.py)`（真源：`scripts/engine_module_count.py`） |
| 结构化科研实体 | 157,859 | `entity_store.json:counts.TOTAL_ENTITIES` |
| 校准用存储点 | 215 | `sum(trust_surface.rows[].n_store)` |
| **跨学科外推 G3** | **0 / 14** | `g3_aligned_benchmark.json` |
| **真实实验真值** | **0** | `trust_surface.json:overall.honesty` |

> ⚠️ **已弃用数字**（禁止再用于对外材料）：
> - ~~163 个底层模型~~ —— 代码中查无此数（实际 engine 模块 102，2026-10-17 L-12 同步至真源）
> - ~~47,566 条实体~~ —— 已过时（实际 157,859）
> - ~~82 个场景~~ —— 旧口径（实际 95）

---

## 6. 测试与验证

```bash
# 核心算法单测（conformal / SINDy / BO / PINN）
python -m pytest tests/ -v

# 全量验证
python scripts/verify_all.py

# 公开数字口径门禁（不一致则 exit 1）
python scripts/check_public_numbers.py

# 泛化基准（诚实报告失败）
python -m engine.generalization_bench

# 可复现证书自验
python -c "from core.trust_layer import issue_certificate, verify_certificate; \
c=issue_certificate(); print(verify_certificate(c))"
```

---

## 7. 文档

| 文档 | 路径 | 说明 |
|------|------|------|
| **失效模式审计** | `AXIOM_AUDIT.md` | A1~A14 公理与边界 |
| 可信执行层设计 | `docs/trust_layer.md` | VeritasGuard 架构 |
| 平台总览（对外） | `docs/swarmlabs-platform-overview.md` | 功能 / 优势 / 用法 |

---

## 8. 安全与合规

- **核心数值不依赖 LLM**：预测来自解析模型，LLM 仅用于文档与推理辅助
- **证据链溯源**：每项输出可追溯至具体文献（**注意：94/95 为教材级，非 DOI 级**）
- **反事实保护**：关键结论附带敏感度分析
- **越界自动阻断**：REFUTED 场景禁止自主行动
- **MIT 协议开源**

## 9. 贡献

**治理文件**（bus factor 治理，2026-10-15 补齐）：

| 文件 | 作用 |
|---|---|
| [`CODEOWNERS`](CODEOWNERS) | 明确每类产物（引擎 / 门禁 / 账本 / 前端）的负责人与不变量；bus factor=1 是现状，不隐瞒 |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | 贡献流程、fail-closed 规则、拒收清单（"数字必须真实"是唯一准则） |
| [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) | 社区行为规范（Contributor Covenant v2.1） |
| [`SECURITY.md`](SECURITY.md) | 漏洞上报流程（90 天协调披露），明确"REFUTED 是特性不是 bug" |
| [`.github/FUNDING.yml`](.github/FUNDING.yml) | 资金入口占位（pre-revenue 阶段，未真实开放捐赠） |

**贡献规则**：任何新增对外数字必须写入 `data/public_facts.json`
（通过 `scripts/build_public_facts.py`），并让 `check_public_numbers.py` 通过。

任何试图"隐藏 REFUTED / 降低 PASS 门槛 / 删除 provenance 字段"的改动都将在
PR review 阶段被拒——这是本项目相对竞品的核心差异，不是可选特性。

## 10. 许可证

MIT License
