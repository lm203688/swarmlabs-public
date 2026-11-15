# SwarmLabs Trust Layer (VeritasGuard)

> 可信执行与可复现证书层 —— 把"每个结论都说得清出处、可被独立复算"做成显式能力。

---

## 1. 为什么需要一层"可信层"

科研 Agent 的最大风险不是"答错"，而是**答得振振有词却无法溯源、无法复现**。
SwarmLabs 把"可信"从口号做成了一个可被评委检查的层：

- 每个计算结果都能回到其 **ground-truth 文献**（不是模型自己编的引用）；
- 每个科学结论都带有 **出处链（代码 / 论文 / 数据集）** 与 **置信度**；
- 每次运行都能产出一份 **可复现证书（Reproducibility Certificate）**，用内容哈希签名，评委可独立重算验证。

对标榜单中 **CodeNotary / LabOps Guard / CyberGuard / SceneGuard / RevGuard** 等项目的共同做法：
> 把"信任"做成一个**独立、可命名、可演示、可审计**的模块，而不是散落在系统里的隐性属性。

---

## 2. 三层能力（均已落地，本层负责统一命名与聚合）

| 能力 | 实现位置 | 作用 |
|------|----------|------|
| 文献溯源可信面 | `data/trust_surface.json`（由 `engine/flywheel.py` 的 `paper_validation_loop` 生成） | 每个学科引擎带 published ground-truth + `ref_papers` + RMSE/校准/覆盖率 + `rubric` 5 项校验 |
| 结论可追溯 | `engine/traceability.py` → `TraceabilityLayer` | 每个 `Finding` 链接 `Provenance`（code/paper/dataset），带 `confidence` 与审计导出 |
| 任务证据契约 | `core/task_contract.py` → `TaskContractManager` | 任务边界 + 证据收集 + 完成验证，fail-closed |

---

## 3. 可复现证书（Reproducibility Certificate）

由新增模块 `core/trust_layer.py` 聚合上述数据生成：

```bash
python -m core.trust_layer
```

证书包含：

- `certificate_id` / `issued_at` / `version`
- `inputs`：本次运行的输入快照（问题、领域、模型版本）
- `artifacts`：关键产物文件 + **sha256**
- `trust_metrics`：领域数、rubric 满通过数、文献溯源数、旗舰数、累积点
- `rubric_detail`：每个引擎 5 项校验明细 + 文献出处
- `evidence_chain_summary`：可追溯发现数、带出处数、低置信度发现
- `signature`：**全体内容的规范化 sha256** —— 任何输入/产物变化都会改变签名

**评委验证方式**：重新运行 `python -m core.trust_layer`，若数据与产物未变，则签名一致；
若任一结论被篡改或文献缺失，签名即变化。这把"可复现/可审计"从一个承诺变成一条可执行的命令。

---

## 4. 与评审维度的对应

| 评审维度 | 对应证据 |
|----------|----------|
| 工程落地与运行验证 | `data/trust_surface.json` 是真实跑出的验证面，非示意值 |
| 安全/合规 | 引用链完整性校验 + 任务契约 fail-closed + 数据血缘 |
| 多 Agent 协同与自主闭环 | 证书由 `flywheel` 每日自主循环产出，无需人工 |
| 可检查性 / 可延续性 | 证书签名 + 出处链，支持独立复算与质疑 |

---

## 5. 下一步（可选增强）

- [ ] 把证书签名写入 `TraceabilityLayer`，每次 `record_finding` 自动附带
- [ ] 增加"反事实"探针：去掉某篇 `ref_paper` 后 rubric 是否仍通过
- [ ] 在 Web Demo 中增加一个 "🔒 可信证书" Tab，实时展示当前会话证书
