# Why SwarmLabs · 诚实差异化与合规对标

> 本文档基于 `2026-09-09` 代码快照，刻意**只写已落地的真实能力**，并显式标注差距。
> 不重复「163 模型 / 10 域全量验证 / 活跃参赛」等已被核实为夸大或已过时的表述。

## 1. 真实存在的差异化能力（已验证）

| 能力 | 状态 | 证据 |
|------|------|------|
| **微生物发酵动力学深度场景** | ✅ 已落地 | `engine/microbiology_full.py` 实现 **52 个** GP-UQ 场景，覆盖 Monod / Andrews / Contois / Haldane / Pirt / Luedeking-Piret / Tessier / Baranyi / Fed-batch / Chemostat / Thiele / Antibiotic / Thermal 共 13 种动力学（见 `data/microbiology_scenarios.json`） |
| **可信执行层 VeritasGuard** | ✅ 已落地 | `core/trust_layer.py`：7 项 rubric（含 `sharpness_ok`、`function_nonconstant` 防退化）+ SHA256 数字签名 + 文献溯源 + 可复现证书 |
| **确定性可验证数值（非 LLM 算结果）** | ✅ 已落地 | 核心预测来自解析物理/数学模型（Forrester/Branin/Hartmann6/Heat1D/Logistic/Monod 等），LLM 不参与数值结论 |
| **多 Agent 权限隔离** | ✅ 已落地 | `agentteams/`：Planner（只读规划）→ Executor（执行无写权限）→ Verifier（唯一写权限 + 人工审批）；`contracts.py` 静态声明 + `Context.invoke_skill` 运行时校验 |
| **结构化科研实体数据护城河** | ✅ 已落地 | 47,566+ 条实体（趋势/研究空白/跨域桥接/合著网络），每日从 OpenAlex 增量扩充 |
| **主动学习 / 实验锦标赛** | ✅ 已落地 | `engine/surrogate.py`：GP 后验方差排名驱动候选采样 |

## 2. 诚实差距（不要宣称已具备）

| 差距 | 事实 | 影响 |
|------|------|------|
| **制药端点未真正落地** | 报告称「PBPK/PopPK/f2/IVIVC 端点存在」，但 `engine/` 中**无对应 Python 验证引擎**；仅在 `frontend_fixed/.build-check/index.js` 有前端 JS 启发式（溶解/f2/Korsmeyer-Peppas）。属未验证启发式，非 GP-UQ 内核 | 不能宣称「BE 生物等效性端到端」已可用 |
| **可信面满通过率有限** | `data/trust_surface.json`：62 场景，**25/62（40%）** 七项 rubric 全通过；`rmse_rel_ok` 仅 26/62 通过 | 对外应如实呈现，而非「全量通过」 |
| **全部场景为 simulated** | 62/62 均为已发表模型/公式的 GP 代理，**0 个**有独立实验复测（experimental ground-truth） | 应标注 `evidence_type=simulated`，不暗示实验验证 |
| **文献溯源质量有限** | `gold_source_traced` 62/62 通过，但**0 行含真实 DOI**，引用多为教材/网络资源 | `gold_source_traced` 仅表示「有文献字符串」，不等于 DOI 级溯源 |
| **无 CI/CD** | `tests/` 存在但无 GitHub Actions / pre-commit | 主仓 `github.com/lm203688/swarmlabs` 当前 404，无法推送，CI 暂不可落地 |
| **GOAI 2026 已终止** | 2026-08-28 确认未进复赛、终止跟踪 | 任何「参赛赛道」表述均已过时 |

## 3. 合规对标映射（ASME / 21 CFR / ICH）

| 标准 | 要求 | SwarmLabs 对应 | 差距 |
|------|------|----------------|------|
| **ASME V&V 10**（计算模型验证与确认） | 模型验证 + 确认 + 不确定性量化 | `core/trust_layer.py` 7 项 rubric + `run_parity_loop` 奇偶校验 + conformal UQ | 缺独立第三方 V&V 报告； rubric 阈值未外部审计 |
| **21 CFR Part 11**（电子记录/电子签名） | 审计追踪 + 电子签名 + 不可篡改 | SHA256 可复现证书 + `agentteams/runs/trace.jsonl` 操作追踪 + Verifier 人工审批门 | 缺正式 e-signature 工作流与访问控制日志 |
| **ICH Q8/Q9/Q10**（质量源于设计 / 风险管理） | 设计空间 + 质量风险管理 + 持续改进 | GP 覆盖区间（UQ）+ 主动学习闭环 + 反重复记忆（Anti-Repeat） | 缺制药级 QRM 文档模板与变更控制 |
| **ICH Q2(R1)**（分析方法验证） | 专属性/线性/范围/精密度/准确度 | GP 代理的 `rmse_rel` + `coverage` + `cal_err` 可类比精密度/准确度 | 未映射到标准术语；缺回收率/检测限指标 |

> 结论：SwarmLabs 在**方法论层面**已对齐上述标准的「精神」（可复现、可审计、带 UQ），但**尚未取得任何正式合规认证**，且制药端点本身未落地。对外沟通应表述为「对齐 ASME/21 CFR/ICH 原则」，而非「符合认证」。

## 4. 给评委 / 投资人的一句话定位

> SwarmLabs 用**纯解析模型 + GP 不确定性量化 + 权限隔离的多 Agent 闭环**，把「虚拟实验替代真实实验」做成可复现、可审计的科研基础设施；当前已在**微生物发酵动力学**这一垂直领域做到 52 个深度场景，但制药广域端点与正式合规认证仍是路线图项，可信面满通过率为 40%（诚实披露）。
