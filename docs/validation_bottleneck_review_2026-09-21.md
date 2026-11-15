# 验证瓶颈方向全面梳理 + 开源可借鉴清单（2026-09-21）

> 背景：围绕 2026-09-20 确定的新方向——**RayGun 验证瓶颈**（50 万候选仅 11 湿实验 = 2.2×10⁻⁵ 覆盖率）全面梳理 SwarmLabs 现状，并扫描开源平台可借鉴的工具/方法论。
> 所有数字均为本次对 `engine/` 与 `frontend_fixed/reports/` 的实跑结果，非记忆引用。

---

## 零、执行摘要

1. **模型保真度已不是瓶颈，证据维度才是。** 六维 ablation 评分里，fidelity/coverage/benchmark 三轴已封顶（≈1.0），但 anchor=0.300、replication=0.412、sensitivity=0.050，把整体均值拉到 0.62。这正是 RayGun「生成便宜、证据贵」在 SwarmLabs 内部的微观映射。
2. **开源生态一致证明「生成强、验证弱」是行业共性。** Karpathy autoresearch 让 AI 一夜跑 100 个实验；Biomni 15k+ 科学家在用，但都明确「不做更深的科学裁决」。SwarmLabs 的「可信验证层」定位正好落在行业最弱、最被评审苛求的环节——这是护城河，也是自家还没做完的功课。
3. **9 个候选里 5 个明确可借鉴**，按 P0/P1/P2 排布，全部不违反自治红线（详见第四节）。

---

## 一、当前验证瓶颈方向现状盘点（实跑口径）

### 1.1 六维 ablation 评分（来自 `verification_gate.ablation_plan_batch`，n=80 场景）

| 维度 | 均值 | 满分(=1.0)占比 | 判读 |
|---|---|---|---|
| fidelity（R² 保真度） | **0.994** | 79/80 | 代理模型拟合已到天花板 |
| coverage（UQ 覆盖率） | **0.973** | 74/80 | 不确定度校准已到天花板 |
| benchmark（解析解/基准） | **1.000** | 80/80 | 全覆盖 |
| **anchor（湿实验锚定）** | **0.300** | **0/80** | 全部停在 0.3，无一有锚定字段 |
| **replication（独立复算）** | **0.412** | 33/80 | **47/80 零独立复算** |
| **sensitivity（灵敏度）** | **0.050** | 4/80 | **76/80 零灵敏度分析** |
| **六维整体均值** | **0.6215** | — | 被后三轴拉低 |

**根因一句话**：保真度/覆盖率/基准三轴已封顶，瓶颈完全集中在「证据生产」三轴（锚定/复算/灵敏度）。这与 RayGun 的宏观叙事同构——生成/拟合是便宜的，证据是贵的。

### 1.2 账本与锚点资产（`gate_ledger.json` + `wet_lab_anchors.json`）

- **V&V gate ledger：80 个场景**（78 PASS + 2 MARGINAL）。场景注册表口径为 82，详见下方口径告警。
- **锚点来源独立性分析**（`anchor_independence.py`）：19 个锚点 / 声明 36 个来源 / 去重后 19 个独立来源家族 / **独立性比率 0.5278**（< 0.8 警告线，6 个共享聚类）。即「19 个独立锚点」的说法准确（去重后仍是 19），但每个锚点平均只摊到 0.53 个独立来源，同源重复显著。
- **合成锚点预验证**（`synthetic_anchors.py`）：18 条（9 CLEAN / 9 NEEDS_REVIEW），60 个场景无 `published` 字段，2 个非 PASS。这是分诊工具，不进入 `wet_lab_anchors.json`，纪律已写进代码。
- **VLab 批量收敛检测**（`vlab_batch.py`）：四类判据已落地（DUPLICATE_SUBMISSION / NON_RETRYABLE / STALLED / OSCILLATING），fail-closed 语义，可复算。

### 1.3 ⚠️ 口径不一致（必须修正，影响对外可信度）

- v2 回信（已发）与工作记忆均写「**82 个已验证场景**」，但 gate_ledger 实测 **80**（78 PASS+2 MARGINAL）；场景注册表为 82。
- **建议**：对外统一为双层口径——「the validated corpus: 80 scenarios live in the V&V gate, 82 in the registry」；或在回信/网站中干脆不写硬数字，用「the validated corpus」带过。
- 邮件已发出无法改；**后续所有通信与网站口径必须统一到 80/82 双层表述**，否则评审若数出来对不上，会直接动摇「数据诚实」这条立身之本。

---

## 二、战略锚点回顾：RayGun 验证覆盖率

- RayGun（Nature 2026，DOI **10.1038/s41586-026-10842-8**）：500,000 候选分子 → 11 次湿实验 = **2.2×10⁻⁵** 验证覆盖率。
- **引用纪律（已固化）**：凡提 RayGun 必须带分母 + DOI，不得只说「极低覆盖率」。
- **对内含义**：SwarmLabs 的「验证闸门」卖点 = 把 2.2×10⁻⁵ 这种证据荒拉起来。但自家内部 anchor 维度 0/80 满分，说明「验证层」这件事自己也还没做完——**这不是要遮的丑，而是下阶段主线的精确位置**。承认它、正面解决它，比掩盖更有说服力。

---

## 三、开源平台扫描（9 个候选，分组）

### A 组：方法论 / 架构范式（可直接借鉴）

| # | 项目 | 来源 | 许可 | 关键事实 |
|---|---|---|---|---|
| 1 | **Karpathy autoresearch** | github.com/karpathy/autoresearch | MIT | 630 行脚本，3 文件 ratchet（prepare.py 不可变 / train.py 可改 / program.md 人类写策略）；固定 5 分钟预算；单标量 `val_bpb`；git 作为研究记忆；63k+★。一夜 ~100 实验。 |
| 2 | **K-Dense-AI/scientific-agent-skills** | github.com/K-Dense-AI/scientific-agent-skills | MIT | 163 个程序性知识技能 / 16 域 / 100+ 数据库连接器；遵循开放 Agent Skills 标准；**渐进式暴露**（常驻仅 7.1% 窗口）；42k+★。 |
| 3 | **VAILabs** | arXiv 2507.06271 | 开源 | 模块化虚拟实验工作流（Modules/Plugins/Cores），XML 描述；human-in-the-loop；混合数字-物理循环；内置日志保证可复现。 |
| 4 | **Terminal-Bench-Science** | github.com/harbor-framework/terminal-bench-science | Apache-2.0 | 70 个专家策展任务（生命/物理/地球/数学/工程）；Harbor 框架；需 Modal/Daytona 云环境；带 oracle + canary。 |
| 5 | **CenterForOpenScience/llm-benchmarking**（ReplicatorBench） | github.com/CenterForOpenScience/llm-benchmarking | 开源 | 复制(replication)/鲁棒性(robustness)/研究设计(study-design)三维评测方法论。 |

### B 组：竞品 / 相邻（借鉴叙事与定位，不宜直接搬代码）

| # | 项目 | 关键事实 | 与 SwarmLabs 关系 |
|---|---|---|---|
| 6 | **Stanford Biomni**（Science 2026） | 通用生物医学 agent，15k+ 科学家，150 工具 / 59 数据库；但论文明确「不做更深的科学判断」。 | **相邻竞品**。Biomni 不做的「可信裁决」正是 SwarmLabs 的验证闸门要做的——差异化锚点。 |
| 7 | **ProtoPilot** | 自演化生物协议多智能体（类 autoresearch 但 bio 专用）。 | 与 #1 同源思路，bio 特化。 |
| 8 | **ABC-Bench** | 生物安全 agent 基准（风险评分）。 | 与 SwarmLabs 的 5 个 REFUTED 硬阻断纪律可互参。 |
| 9 | **IBSim / TokaLab / MatteriX** | 数字孪生 / 化学仿真平台。 | SwarmLabs 自有 surrogate 引擎已覆盖此能力，低优先。 |

---

## 四、可借鉴清单（优先级 + 成本/收益/红线）

> 红线判据（已固化）：① 不引商业 LLM 密钥；② AI 不代发邮件；③ 不为赶 grant 重构；④ 动作准入唯一判据 =「是否让虚拟实验在**无真实实验、无网络、无设备**下更可替代」。

| 候选 | 借鉴点 | 移植成本 | 收益 | 是否违反红线 | 优先级 |
|---|---|---|---|---|---|
| **#2 K-Dense skills 标准 + 渐进式暴露** | 把 `swarmlabs-skills` 切到 Agent Skills 标准；写渐进式暴露 loader（常驻仅名称+短描述，按需解冻 SKILL.md）。 | 低（已有 #301 基础） | 高：增强既有技能线，呼应评审「程序性知识治理」趋势 | **干净** | **P0** |
| **#4 Terminal-Bench 贡献任务** | 起草 1–2 个任务展示「given surrogate + ledger, find the anchor gap / judge trustworthiness」，提 PR。 | 低 | 中高：开放基准社区能见度 + 可信度 + 呼应「held-out benchmark」要求 | **干净**（仅 benchmark 跑云，生产仍本地） | **P0** |
| **#1 autoresearch ratchet（门禁化）** | 借「固定预算 + git lineage + 二元 keep/revert」循环，但每次 keep 必过 `verification_gate`（fail-closed）。 | 中 | 高：直接打「证据生产」瓶颈，让更多场景以更低人力通过门禁 | **关键条件**：需 LLM agent —— 用本地 Ollama(ornith) 则干净；接商业 API 即违例 | **P1** |
| **#5 ReplicatorBench 复制/鲁棒性维度** | 用其「独立复算 + 鲁棒性」思路，给已复算场景补「不同 surrogate 后端一致」记录，抬 replication 维度（0.41→更高）。 | 中 | 高：直接补 47/80 零复算缺口 | **干净** | **P1** |
| **#3 VAILabs 架构对账** | 用其 7 模块 taxonomy（Initialiser/DataProcessing/Modeling/Decision/Environment/UserInteraction/Output）校验 `vlab_contract` 完整性。 | 低（文档/审计） | 中：确认自身架构与 SOTA 对齐 | **干净** | **P2** |
| **#6 Biomni 叙事** | 引用「collaborator not decision-maker」+「fully documented and auditable」作为定位话术；强调 Biomni 不做、SwarmLabs 做的「可信裁决」。 | 零（引用+话术） | 中：竞品定位清晰度 | **干净** | **P2（引用）** |
| **#7/#8/#9 ProtoPilot/ABC-Bench/IBSim** | 观察为主；ABC-Bench 风险评分可互参 REFUTED 纪律；IBSim 等不搬（自有 surrogate 已覆盖）。 | — | 低 | 需评估（接外部仿真/云可能触红线） | **观察** |

---

## 五、落地建议（供拍板）

**立即（本周，P0）**
1. 把 `swarmlabs-skills` 切到 Agent Skills 标准 + 写渐进式暴露 loader（借 #2）。
2. 起草 1 个 Terminal-Bench 任务（锚点缺口判别），提 PR（借 #4）。

**本冲刺（P1）**
3. 用本地 Ollama 跑 autoresearch 式 ratchet，但每个 keep 过 `verification_gate`——把「自主提议 surrogate 改进」关进 fail-closed 闸门（借 #1，红线条件满足）。
4. 用 ReplicatorBench 思路给 33 个已复算场景补「独立后端复算」记录，系统性抬 replication 维度（借 #5）。

**不做的**
- 不引商业 LLM 密钥（autoresearch ratchet 必须本地化，否则违例）。
- 不为赶 Catalyst 重构（v2 回信已收紧，保持）。
- 不搬 IBSim/TokaLab/MatteriX 这类领域仿真（自有 surrogate 已覆盖）。
- Biomni 是相邻竞品，不是要抄的对象——它的弱项（不裁决）恰好是 SwarmLabs 的强项。

**红线校验小结**：P0/P2 全部干净；P1 的 autoresearch ratchet 只有在「本地 Ollama 驱动」时才干净，若图省事接商业 API 即触发红线——必须本地化。

---

## 六、对 Catalyst 叙事的影响

- **数据给力**：评审要的三件套「held-out benchmark + 量化 UQ + human checkpoint」，SwarmLabs 已有 coverage 0.95 + 门禁 fail-closed + human sign-off（vlab 五态机）。
- **软肋要正面说**：anchor 维度 0/80 满分是客观弱点。正确表述是「we are extending the anchor corpus and the replication/sensitivity dimensions」，**不要掩盖**。评审若发现掩盖，比承认代价大得多。
- **已发 v2 回信已收紧**（删 demo 邀约、不虚构进度），保持即可，无需再改。

---

## 附：本次实跑命令与产物

- `engine/synthetic_anchors.py` → `data/synthetic_anchors.json`（18 条）
- `engine/anchor_independence.py` → `data/anchor_independence.json`（ratio 0.5278）
- `verification_gate.ablation_plan_batch` → 六维均值 0.6215（见第一节表）
- 关键文件：`engine/verification_gate.py`、`engine/synthetic_anchors.py`、`engine/anchor_independence.py`、`engine/vlab_batch.py`、`frontend_fixed/reports/gate_ledger.json`、`frontend_fixed/reports/wet_lab_anchors.json`
