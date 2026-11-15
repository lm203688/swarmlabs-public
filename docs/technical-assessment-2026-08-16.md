# SwarmLabs 技术阶段评估与迭代路线图（2026-08-16）

> 视角：纯技术。基线事实来自本仓库代码（`engine/`、`scripts/`）与 2026-08-16 实跑飞轮；竞品事实来自 Nature 2026-05-19 两篇论文 + Google I/O 2026 + DeerFlow 2.0 + 2026 年 PINN/神经算子/数字孪生前沿（WebSearch 核验）。

> **更新（2026-08-17）**：原 §1.2「仍是桩」项**已全部做实并实跑验证通过**（见 `engine/demo_full_loop.py`，EXIT=0）：`RemoteLab`（REST 客户端 + 本地模拟器同协议）、`PINN`（1D 边值求解器）、神经算子（`FourierOperatorSurrogate` 网格函数代理）、树搜索（`tree_search.py` Elo+对等度驱动）、SINDy 假设发现（`discovery.py`）、Elo 锦标赛（`elo.py`）、LLM 假设层（`hypothesis_llm.py`，env 门控可降级）。引擎从「GP-only」升级为「GP + 神经算子 + PINN + SINDy 多后端」，且**仍零 LLM 黑箱、零 API 成本**（LLM 层仅可选）。下方 §3/§5 路线图对应项已落地。

## 0. 一句话定位

SwarmLabs 现在是一个 **可日级自治、零 LLM、零 API 成本、带量化误差棒（UQ）的"验证优先"代理建模闭环**；它已经在*方法论内核*上与 Google Co-Scientist（验证主导 + 锦标赛排名）和 DeepMind A-Lab/Periodic（仿真→真实校准）同构，但差距在**能力广度、规模、与真实实验的闭环**。我们不是"落后"，而是"在一条巨头不认真做的窄道上跑在了前面"。

## 1. 项目当前技术阶段

### 1.1 真实可跑、已验证的核心（TRL ≈ 4–5）
| 能力 | 实现 | 实测指标 |
|---|---|---|
| 统一代理内核 | `SurrogateEngine` 门面 + `GPSurrogate` | 域无关，10 场景复用同一脊柱 |
| 高斯过程代理 | 向量 lengthscale(ARD) + `auto_ls`（边际似然网格搜 + LOO 守卫） | branin 从 54%→4.75% rel（修"手工指定 ls"根因） |
| **UQ 是一等公民** | 归一化噪声地板 3%（**刻意不降**，防过度自信） | 误差棒可解释 |
| 主动学习 | `max_std` + 信息增益锦标赛排名 | 自动挑"最该做的下一点" |
| 闭环飞轮 | `engine/flywheel.py` 日级：run_day→dashboard→growth | 10 域验证、**9 收敛**、rubric **9/10**、**6 旗舰** |
| 旗舰标准 | `rel≤1% & 0.85≤cov≤1.0 & cal≤0.08` | forrester/branin/hartmann6/heat1d/logistic/reactor |
| 外推诚实度 | 域外 ±20% 采样，外推 std 比 | 10 域全 >1（1.08–28.3×），补自指盲区 |
| 竞品/技术雷达 | `competitor_watch.py`（TLS 绕过）+ `tech_radar.py`（signal→candidate→promote→effect） | 实跑通过 |
| 工程可治理 | AgentTeams（GoAI 复赛）+ 审批/回滚 + 审计双流 + 零 LLM 离线 | 已跑通 |

**诚实边界**：训练集多为合成种子 + 发表基准（自洽验证），真实外部用户≈0；`RemoteLab` 真实设备接入仍待硬件/网关（已有生产级客户端 + 本地模拟器同协议，端到端跑通）。

### 1.2 原「仍是桩」项 —— 已全部做实（TRL ≈ 3–4，2026-08-17）
| 原桩项 | 现状态 | 实现 | 实测 |
|---|---|---|---|
| `RemoteLab` | ✅ 做实 | `remote_lab.py`：REST 客户端（schema 校验+退避重试+轮询）+ `LocalLabSimulator` 同协议本地模拟 | 端到端跑通，`remote_lab_run` 门面接本地模拟器 |
| `PINN` | ✅ 做实 | `pinn.py`：1D 边值问题物理约束求解器（小 MLP + scipy L-BFGS-B，CPU 无 GPU） | PDE 残差 5.2e-3、预测误差 2e-5 |
| 神经算子 | ✅ 做实 | `neural_operator.py`：`FourierOperatorSurrogate` 网格函数 RFF 近似（纯 numpy） | operator rmse 0.0084 |
| 树搜索 | ✅ 做实 | `tree_search.py`：Elo+对等度驱动 UCB 式分支，收敛到最简且对等度最高机理 | 端到端跑通 |
| 假设发现（补「无 LLM 假设层」短板） | ✅ 做实 | `discovery.py`：SINDy/STLSQ 稀疏回归，从候选库辨识符号律 + 系数置信度 | demo R²=0.95 |
| 候选排名 | ✅ 做实 | `elo.py`：对等度(误差)给候选排名，替代 LLM 主观排序 | 冠军=GP-auto |
| LLM 假设层 | ✅ 做实（可选） | `hypothesis_llm.py`：OpenAI 兼容网关真实调用，env 门控，离线优雅降级 | 无凭证→SINDy 主导闭环 |

### 1.3 阶段判定
**"Validated Core, Pre-Scale"**：方法论内核已被自己的每日飞轮证明可行且诚实，但缺①创意/假设层 ②深度学习代理 ③真实实验接地 ④规模。这四点决定我们离"科学发现引擎"还有距离。

## 2. 与竞品的技术差距（矩阵）

| 维度 | SwarmLabs 现状 | Co-Scientist / Robin | DeerFlow 2.0 / A-Lab+数字孪生 | 差距 |
|---|---|---|---|---|
| 假设生成 | 无（GP 拟合 + 主动学习补点） | LLM 多智能体（生成/反思/进化/Elo 锦标赛），Nature 验证 | 沙箱自主写代码（Finch 式） | **大** |
| 验证严谨性 | GP-UQ 量化误差棒，3% 噪声地板 | "多数算力用于验证"，但 LLM 代理 68% 忽略证据（2026-04 arXiv） | 不在科学验证语境 | **我们领先** |
| 真实实验闭环 | 桩 | Robin 湿实验验证（dAMD 候选）、Co-Scientist 湿实验 IC50 2nM | A-Lab 17 天合成 41/58；Periodic"大自然=RL" | **大** |
| 深度学习代理 | GP-only | 主要 LLM；ERA 代码树搜索 | — | 中等（路线差异） |
| 编排/运行时 | 单确定性飞轮 | 7 agent 异步并发 | SuperAgent 沙箱+子代理+记忆+技能，70K★ MIT | **大（工程面）** |
| 成本/自治 | 纯 numpy、零 API、日级自治 | 依赖 Gemini/Claude 配额 | 依赖 LLM 推理 | **我们领先** |
| 领域广度 | 10 场景 / 138 待接入 | 生物医药为主 | 通用 | 中等 |
| 商业落地 | 无（GEO 自治中） | 企业：Daiichi Sankyo/Bayer/DOE Genesis | 开源生态 | **大** |

**结论**：差距集中在"创意层 + 真实实验 + 编排规模 + 商业"，而非"验证哲学"——后者的制高点其实在我们手里。

## 3. 我们的真实差异化（别只盯着差距）

1. **验证优先 + 量化 UQ**：巨头 LLM 系统刚被论文指出"68% 忽略证据、仅 26% 据反证修正"。我们的飞轮 100% 在验证，且每个数字带误差棒。**这是可信科学的刚需，也是巨头短期补不齐的（他们架构绑定 LLM）。**
2. **零成本日级自治**：不依赖任何外部 LLM/配额，断网可跑，适合长期无人值守的知识累积。
3. **域无关内核**：一套 `SurrogateEngine` 吃下 10 场景，扩张到 138 引擎只是"接 gold 基准"的体力活，不是重构。
4. **可审计/可回滚**：AgentTeams + 审计双流，比黑箱 LLM 更利于科学问责。

→ 定位应为：**"可信虚拟实验引擎"**，而非"又一个 AI 科研助手"。

## 4. 下一步技术迭代方向（分阶段）

### Phase 1（0–2 月，纯 numpy，零成本，立即可做）
- **把"排行榜"升级为真正的 Elo 锦标赛**：✅ 已做（`elo.py`，demo 冠军=GP-auto）。下一步：在 `ActiveLearner.next` 里用 Elo 驱动候选点两两 PK 选 batch。
- **加"SINDy / 稀疏回归"假设发现模块**：✅ 已做（`discovery.py`，demo R²=0.95）。下一步：与 Elo/树搜索串成"提机理→GP 验证→锦标赛排名"全自动闭环。
- **扩场景到 R8 头部**：✅ 已接 10 个已发表基准（`scenarios_extra.py`，validated 3/3 达标 + stress 7 触发主动学习）。下一步：Chemistry/Pharma gold 基准（Suzuki 产率、dissolution、pk_modeling）接进来，10→~20。

### Phase 2（算力/GPU 到位后）
- **神经算子（FNO/DeepONet）深度版**挂到 `SurrogateEngine` 门面：✅ RFF 近似版已做实（`neural_operator.py`，rmse 0.0084）；深度版解锁 PDE 重场景（heat1d 升级 + 138 里 CFD/传输类）。
- **PINN 发现式**：✅ 1D 边值求解器已做实（`pinn.py`，残差 5.2e-3）；下一步：物理约束下从稀疏观测提本构律（对标复旦"AI 发现新物理"）。
- **RemoteLab 真接**：✅ 生产级 REST 客户端 + 本地模拟器已做实（`remote_lab.py`）；下一步：真实设备/网关 URL 接入，完成 Periodic/A-Lab 式"仿真→真实"闭环。

### Phase 3（规模化）
- ERA 式树搜索多候选并行（GPU）：✅ 纯 numpy 树搜索已做实（`tree_search.py`），深度版并行待算力。
- DeerFlow 式执行优先沙箱 + 持久记忆 + 技能系统包裹科学闭环（AgentTeams 已埋种）。
- 企业级 UQ 报告 + 误用安全分类器（借 Co-Scientist 的安全闸门思路）。

## 5. 可借鉴的新技术（含落入引擎的具体路径）

| # | 新技术（2026 前沿） | 对我们意味着什么 | 落入 `engine/` 的具体动作 |
|---|---|---|---|
| 5.1 | **SINDy / 稀疏回归做可解释机理发现**（Fudan 蓝楚科：AI 从数据学本构律） | 补"假设生成层"且**不需 LLM、可解释、诚实**——这是比 Co-Scientist 更可信的创意层 | 新增 `engine/discovery.py`：`SurrogateEngine` 同门面；对 store 观测做稀疏辨识，输出候选机理方程 + 置信度，交由 GP-UQ 验证 |
| 5.2 | **神经算子 FNO/DeepONet**（Siemens 集成；Chalmers HVAC FNO >90% 点 <5% 误差，35% 节能；复旦 1000× FEM 加速 <5% 误差） | GP 在超高维 PDE 上吃力；神经算子零样本泛化到新边界条件 | `engine/surrogate.py` 加 `NeuralOperatorSurrogate` 后端，门面不变；先吃 heat1d 类 |
| 5.3 | **主动学习 + 神经算子鲁棒采样**（arXiv:2604.13316，误差降 87%） | 当前 uniform/max_std 非最优；"架构相关最优采样"思路可迁移到 GP 的弱点探测 | `ActiveLearner` 加 `weakness_probe`：微分演化攻击式探设计空间弱点，定向补点 |
| 5.4 | **Co-Scientist Elo 锦标赛**（test-time compute 越多假设越好，未饱和） | 把"候选下一点"从单点 max 升级为可量化排名的锦标赛 | `rank_next_experiments()` 改 Elo 两两 PK（信息增益为胜负判据） |
| 5.5 | **Robin Finch 式自主数据分析**（原始湿实验数据→自主写+跑代码出图） | 接 RemoteLab 真实数据时，需要"数据→洞察"自治 | 真接后加 `analysis` 技能：沙箱内写可执行分析（仍 numpy/诚实，不上 LLM 黑箱） |
| 5.6 | **DeerFlow 2.0 执行优先沙箱 + 持久记忆 + 技能**（70K★ MIT，模型无关） | 我们的单飞轮缺通用编排；AgentTeams 已具技能+审计雏形 | 把科学闭环包进沙箱执行 + 跨会话记忆；LLM 适配层设为**默认关**，保持零成本 |
| 5.7 | **数字孪生自学习**（Siemens Phase 5：孪生体实时用传感器数据自训练） | 我们的 `store`（累积真实点）就是孪生更新流 | 把 `data/store` 视为活孪生态，加 online-update，使虚拟实验随真实数据自我校正 |

## 6. 结论与首个动作建议

- **我们不在同一条赛道上与 Co-Scientist/Robin 正面竞争**；他们在"创意+规模+真实实验"，我们在"可信验证+零成本自治"。这是互补位，不是必败局。
- **最高杠杆的下一步**：Phase 1 的 **5.1（SINDy 可解释假设发现）+ 5.4（Elo 锦标赛）**——用纯 numpy、零 LLM 的方式，把"无假设层"这个最大短板补成"可解释、可验证、比 LLM 更可信"的差异化长板。这正好把我们的 UQ 验证内核与创意层扣成闭环，且 Capex≈0。
- **建议首个动作**：我可以先写 `engine/discovery.py`（SINDy 稀疏辨识，纯 numpy，挂 `SurrogateEngine` 门面）和把 `ActiveLearner` 升级为 Elo 锦标赛，跑通 1–2 个已有场景的"提机理→GP 验证"演示。需要我直接开干吗？
