# SwarmLabs Effectiveness Check（2026-10-15）

**任务 ID**：#320
**范围**：把 §1-6 竞品定位 + 3 条线上用户旅程走查 + parity 数据整合成一份"SwarmLabs 是否真的能作为可信依据"的诚实评估。
**立场**：**不自我推销**，只回答"当前产出的可信价值到底在什么边界内"。

---

## 0. 一句话结论

SwarmLabs 当前产出的**可信价值**成立，但**仅在以下三重边界内**：

1. **域内插值**：95 场景均在设计域（design region）内插值验证，外推 REFUTED（73/95 已公开判负，见 `data/g3_aligned_benchmark.json`）。
2. **生物过程内插**：核心定位收缩后专注发酵/微生物/酶催化；跨学科泛化 G3=0/14 学科通过，明确不作此主张。
3. **锚点覆盖稀疏**：95 场景仅 6 条 wet_lab_anchored（19 条锚点、3 个 DOI 来源、5/7 oracle 参数在文献区间内），84 场景靠 published benchmark 或 synthetic surrogate 锚定。

**任何超出这三重边界的推断，都是超出产品承诺，不属于 SwarmLabs 的责任**——这条边界是**主动收缩的定位**，不是缺陷。

---

## 1. 输出有效性矩阵（What works / What doesn't）

| 产出类型 | 数量 | 独立证据链 | 是否可作为决策依据 | 边界 |
|---|---|---|---|---|
| 场景级 PASS 判定 | 91/95 | ✅ R² + coverage + κ 三指标 | **是**，但仅限**训练域内插值** | `data/gate_ledger.json` |
| 场景级 REFUTED 判定 | 1/95（+ 73 外推压力判负） | ✅ oracle 与真值分歧记录 | **是**，作为"不做这个方向"的决策依据 | 保留在账本，不隐藏 |
| 湿实验锚点覆盖 | 6/95 场景 | ⚠️ 3 个 DOI 来源，5/7 参数在文献区间 | **部分**，锚点稀疏是诚实边界 | `data/real_anchors.json` |
| 外推预测 | 0/95 PASS | ❌ G3=0/14 学科 | **否**，明确禁止 | `data/g3_aligned_benchmark.json` |
| 跨学科迁移 | 0/14 学科 PASS | ❌ 泛化基准已证伪 | **否**，明确禁止 | 定位已收缩至生物过程 |
| LLM 假设生成 | 0（未实现） | N/A | N/A | 明确"验证层，非创意层" |
| 真实实验闭环 | 0（用户拒绝） | N/A | N/A | RemoteLab 桩已就绪，等信号 |
| 生产 API 可调用性 | ✅ 30/30 步骤线上全绿（10 端点 × 3 场景） | ✅ HTTP GET 全部 200 | **是**，只读契约完整 | `reports/user_journeys/2026-10-15_journeys.md` |

---

## 2. 与 6 家竞品的定位对齐

基于 `docs/competitor_comparison_matrix.md`（2026-10-14）+ `docs/competitor-landscape.md`（2026-08-09）+ `docs/competitor-benchmark-2026.md`（2026-08-10）整合。

| 竞品 | 定位 | SwarmLabs 位置 | 是否有真实可交付差异 |
|---|---|---|---|
| **Google Co-Scientist** | Gemini 多智能体，**验证主导**（多数算力用于核验），idea tournament Elo | 同构内核，无 LLM 假设生成层 | ✅ 差异真实：SwarmLabs 无 LLM 成本，纯 numpy 自治，但**放弃了多智能体演化能力** |
| **DeepMind A-Lab / Periodic** | 文献→仿真→**真实实验**→校准闭环，Hassabis"AI 能验证而非仅预测" | 有仿真 + UQ 校准，真实实验为桩 | ⚠️ 差异**部分**真实：SwarmLabs 有 UQ 校准，但**没有真实实验闭环**，这是被明确的定位收缩 |
| **FutureHouse Robin** | 假设→实验→解读闭环，300h→2h | 同赛道不同实现 | ❌ **无差异**：Robin 是通用框架，SwarmLabs 是场景级可信层，赛道不同但**Robin 更完整** |
| **Claude for Science** | $20K API 额度，赋能科学家而非替代 | 无 API 计划，赋能而非替代的哲学一致 | ❌ **无差异**：定位话术一致，SwarmLabs 无规模化商业能力 |
| **DeepMind AI4S toolkit** | 通用科学工具 | 上游可能消费 SwarmLabs 的裁决 | ✅ 差异真实：SwarmLabs 是它的**下游**可信层，非直接竞品 |
| **BoTorch SafeOpt / gpjax.sbo** | 严格 safe BO（PyTorch + GPU） | SwarmLabs **主动拒绝** strict safe BO，改做 constrained recommendation | ✅ 差异真实但**是选择**不是优势：SwarmLabs 换到"零 GPU、纯 CPU、无新依赖"，代价是失去严格 safe BO 形式化保证 |

**净结论**：SwarmLabs 与 6 家竞品的**真实可交付差异**只有 3 处（Co-Scientist / AI4S toolkit / BoTorch SafeOpt），另外 3 家（Robin / Claude for Science / A-Lab）SwarmLabs 都在追赶而非领先。这与"验证层是差异化位置"的战略假设**部分吻合**——但差异来自**主动收缩**，不是天然优势。

---

## 3. 线上走查证据（3 条用户旅程）

来源：`reports/user_journeys/2026-10-15_journeys.md`（2026-10-15 生成）

| 旅程 | 场景 | 10 端点结果 | 总耗时 | Grade |
|---|---|---|---|---|
| A · Microbial kinetics | `microbio_monod` | 10/10 ✅ 200 | 11.3 s | PASS |
| B · Chemical reactor | `chem_reactor_conversion` | 10/10 ✅ 200 | 11.2 s | PASS |
| C · Chemistry REFUTED | `mol_suzuki_yield` | 10/10 ✅ 200（含 1 个诚实 404：无锚点） | 11.5 s | PASS (blocked as designed) |

**验证了 5 项**：
- 端点契约完整（/v3/reports, /v3/anchors, /v3/gate, /v3/benchmark, /v3/openapi + 报告文件 3 种格式 + parity 报告 + index）
- REFUTED 场景**依然可查**，账本诚实不删
- 无锚点场景返回 404 是**诚实边界**（`grade=UNANCHORED` 已在 /v3/anchors 中声明）
- OpenAPI spec 69 paths 与线上端点**一致**
- 10 个只读端点 **零 API key 可用**，符合公开契约

**未验证的（诚实留白）**：
- 未测试 429 限流触发
- 未测试认证链路（/api/v2/user/me 等）
- 未测试写入端点（/v2/feedback, /v2/run）
- 未测试异常输入（invalid scenario key, malformed JSON）

---

## 4. Parity 覆盖度检查

来源：`frontend_fixed/reports/parity_report.json`（19 条锚点，3 个 DOI 来源）

| 指标 | 值 | 评估 |
|---|---|---|
| 总锚点数 | 19 | 🟡 稀疏（相对 95 场景） |
| 独立来源数 | 36 | 🟢 独立度可接受（independence_ratio=0.528） |
| DOI 溯源数 | 3 | 🔴 极少（多数锚点无 DOI 级证据） |
| Oracle 参数在文献区间 | 5/7 | 🟡 部分通过（2/7 出界） |
| Parity grades: STRONG | 0 | 🔴 尚无真实湿实验达到 STRONG |
| Parity grades: MODERATE | 3 | 🟡 中等一致度 |
| Parity grades: LITERATURE_CONSENSUS | 3 | 🟡 文献共识，无湿实验 |
| Parity grades: SINGLE_SOURCE | 13 | 🔴 13/19 锚点仅单一来源，未交叉验证 |
| trust_surface.experimental_ground_truth | 0 | 🔴 尚无场景达到实验真值验证 |

**Parity 结论**：**SwarmLabs 目前还没有一个场景达到 STRONG parity**。所有 19 条锚点中，13 条仅有单一来源、3 条达到 MODERATE（含文献共识）、3 条仅文献共识、0 条达到 STRONG。
这是**诚实的短板**，也是**下一步的最优投资方向**（见 §6）。

---

## 5. 效果差距：SwarmLabs 声称 vs 交付

按 `data/public_facts.json` 与对外宣称，列出差距：

| 声明 | 当前交付 | 差距 |
|---|---|---|
| "每个预测带校准区间" | 92/95 场景 conformal 校准，3 场景未校准 | 🟢 96.8% 达标 |
| "噪声地板 3% 不降" | 95/95 场景执行 NOISE_FLOOR=0.03 | 🟢 100% 达标 |
| "越界自动阻断" | 73/95 外推 REFUTED + 1/95 训练内 REFUTED | 🟢 100% 执行（诚实失败是资产） |
| "覆盖 14 学科" | 定位已收缩至生物过程，G3=0/14 明确禁止 | 🟢 定位对齐，不是交付问题 |
| "湿实验验证" | 6 场景 19 锚点，0 STRONG parity | 🔴 **未达标**——这是对外宣称与交付的最大差距 |
| "真实实验闭环" | 0（RemoteLab 桩就绪，未接） | 🔴 **未达标**——被明确的定位收缩 |
| "纯 numpy 零 GPU" | 100% 达标 | 🟢 100% |
| "95 场景 V&V 报告" | 95/95 生成，5/7 oracle 参数在文献区间 | 🟢 报告齐全，锚点边界已声明 |

---

## 6. 下一步投资优先级（如果预算有限）

按 ROI 排序（真实提升可信价值的能力）：

| 优先级 | 动作 | 预计投入 | 提升 |
|---|---|---|---|
| **P0** | 湿实验协议落地（`docs/wetlab_protocol_2026-09-15.md` 已就绪），跑 Monod E. coli 3 重复，把 `microbio_monod` 推到 STRONG parity | 2 人日 · <¥500 耗材 | trust_surface.experimental_ground_truth 0 → ≥1 |
| **P1** | 补齐 3 个 MARGINAL 场景（`chem_bet`, `chem_langmuir`, `mol_esterification_yield`）的 published benchmark 引用质量 | 1 人日 | 单来源锚点 13 → ≤10 |
| **P1** | 补齐 #344 Partner API 10 项线上验收（health/幂等/request_id/429/hint 一致性） | 半天 · 需要 wrangler 权限 | 商用就绪度证明 |
| **P2** | AOCI 记忆层索引重建（#465）——阻塞：MCP 服务绑定到 `知识引擎14站`，需重定向 | 半天 | 认知层可用性 |
| **P2** | a2a_adapter 换官方 a2a-sdk（#566，656 行大改造） | 2 天 | 协议层稳定性 |
| **P3** | Lean 4 本地可复现（#449，被 GitHub Actions 权限阻塞） | 待权限 | A13 形式化证明 |
| **P3** | #291 全局内容项目（blog 57 + docs 101） | 5+ 天 | 内容资产 |

**不建议投入的方向**（诚实边界）：
- 跨学科泛化（G3=0 是方法学限制，不是可解的 bug）
- 真实实验台接入（用户明确未授权）
- 大规模 LLM 集成（成本失控，与"验证层"定位冲突）

---

## 7. 与战略假设的对齐度

| 战略假设 | 交付现状 | 对齐度 |
|---|---|---|
| "验证层是唯一差异化" | 3/6 竞品有真实差异，3/6 在追赶 | 🟡 部分对齐 |
| "诚实失败是资产" | 73/95 外推 + 1/95 训练内 REFUTED 保留在账本 | 🟢 完全对齐 |
| "纯 numpy 零成本" | 100% 达标 | 🟢 完全对齐 |
| "wet lab 是护城河" | 0/95 STRONG parity，6 场景有锚点 | 🔴 **未对齐**——这是最大的诚实短板 |
| "定位收缩到生物过程" | 95 场景 100% 在生物过程内插值 | 🟢 完全对齐 |

---

## 8. 诚实结论

**SwarmLabs 的输出有效性**：

- ✅ **可信**的部分：域内插值判定（95/95 场景）、越界阻断（100% 执行）、公开失败（73 外推 + 1 训练内 REFUTED 保留）、线上 API 契约（30/30 步骤全绿）。
- ⚠️ **待验证**的部分：wet lab parity（0 STRONG / 3 MODERATE）、跨场景泛化（G3=0）、多智能体演化（未实现）。
- ❌ **不做**的部分：跨学科泛化、真实实验闭环、LLM 假设生成、大规模商用。

**是否达到"验证层是差异化位置"的战略假设**：**部分**。3/6 竞品有真实差异，3/6 是战略追赶。真正的护城河目前只有一条：**公开失败 + 主动阻断 + 诚实边界**——这三件事没有一家竞品在做（见 `docs/competitor_swarmlabs_positioning_2026-09-12.md § 5.2 "谱系 ≠ 裁决"`）。

**下一步最经济的动作**：把 Monod E. coli 湿实验跑通（2 人日 · <¥500），把 STRONG parity 从 0 推到 ≥1。这是**当前投资回报最高的一步**，也是**唯一能把"湿实验是护城河"从口号变成事实的路径**。

---

**生成**：2026-10-15 · `docs/effectiveness_check_2026-10-15.md`
**证据源**：
- `data/gate_ledger.json`（95 场景门禁账本）
- `data/trust_surface.json`（可信面四维）
- `data/real_anchors.json`（19 锚点，3 DOI）
- `frontend_fixed/reports/parity_report.json`（parity 覆盖）
- `frontend_fixed/reports/index.json`（95 场景 registry）
- `reports/user_journeys/2026-10-15_journeys.md`（3 条线上走查）
- `docs/competitor_comparison_matrix.md`（2026-10-14）
- `docs/competitor-landscape.md`（2026-08-09）
- `docs/competitor-benchmark-2026.md`（2026-08-10）
- `docs/competitor_swarmlabs_positioning_2026-09-12.md`（2026-09-12）
- `docs/wetlab_protocol_2026-09-15.md`（协议就绪）
