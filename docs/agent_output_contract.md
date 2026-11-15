# Agent 输出契约（证据 · 置信度 · 反事实）

> 本契约规定 SwarmLabs 任一 Agent 输出给下游或用户时的**标准结构**，目的是把"可检查、可质疑、可延续"做成机器可校验，而不是评委的主观印象。

---

## 1. 标准输出结构

每个 Agent 输出必须是一个 JSON，包含以下字段（按必要性分级）：

```jsonc
{
  "answer": "...",              // 核心结论 / 结果（必填）
  "domain": "...",              // 所属学科 / 场景（必填）
  "evidence": [                 // 证据链（必填，源自 traceability.Provenance）
    { "source_type": "paper", "reference": "arXiv:2001.08361", "detail": "Section 3" },
    { "source_type": "code", "reference": "engine/surrogate.py", "detail": "L120-L185" }
  ],
  "confidence": {               // 置信度（必填，源自 Finding.confidence / trust_surface rubric）
    "value": 0.87,              // 0–1
    "basis": "rubric_full_pass=5/5, gold_source_traced=true"
  },
  "counterfactual": {           // 反事实 / 敏感度（可选但强烈推荐）
    "description": "若移除 Kaplan 2020 引用，结论是否会改变？",
    "result": "不变，另有 Smith 2022 与 Nijkamp 2023 双支撑"
  },
  "next_step": "...",           // 可延续性（可选）：下一步怎么验证 / 复现
  "certificate": "..."          // 指向 Trust Layer 生成的可复现证书（可选）
}
```

---

## 2. 字段定义与校验规则

### 2.1 `evidence`（证据链）

- 至少包含 **1 条** `paper` 或 `code` 类出处（否则判定为「无溯源，不可采信」）。
- `source_type` 取值集合：`paper` | `code` | `dataset` | `model` | `observation`。
- 出处必须是**可定位的**（DOI/arXiv id、文件路径+行号、数据集版本），不接受"根据文献…"这类模糊归因。
- 校验：存在 `reference` 非空且 `source_type` ∈ 合法集合。

### 2.2 `confidence`（置信度）

- `value` 为 0–1 浮点数；**必须由可计算依据支撑**（例如 rubric 通过数、coverage 覆盖率、交叉验证一致性）。
- `basis` 说明置信度如何得来（引用 `trust_surface.json` 中对应行的 rubric / coverage 数值）。
- 强约束：**不得凭空给高分**。`value > 0.9` 时必须有 `gold_source_traced=true` 且 `rubric_full_pass` 满分。

### 2.3 `counterfactual`（反事实 / 敏感度）

- 至少对一个**关键证据源**做一次「若移除它，结论是否改变」的分析。
- 用途：暴露结论的脆弱点，让评委和下游可以挑战；符合"开放探索"评审里"可检查性与可延续性"的要求。

### 2.4 `next_step`（可延续性）

- 给出下一步验证路径：如"在 OpenAlex 拉取该领域的 Top 10 新论文复核"、"跑 `python -m core.trust_layer` 复算证书"。

### 2.5 `certificate`（可复现证书引用）

- 指向由 `core/trust_layer.py` 生成的证书 `certificate_id` 或文件路径。
- 评委可通过该证书独立复算，确认结论未被篡改。

---

## 3. 违反契约时的降级策略

- 缺 `evidence`：输出仍可呈现，但必须附加 `⚠️ 无溯源` 标记，且 `confidence.value` 强制 ≤ 0.3。
- 缺 `confidence.basis`：`confidence.value` 强制置为 `null`，不可凭空给分。
- `counterfactual` 缺失：不阻断，但标记 `verify_depth: low`。

---

## 4. 与评审维度的对应

| 评审维度 | 契约字段 |
|----------|----------|
| 可检查性 / 可延续性 | `evidence`、`next_step`、`certificate` |
| 安全/合规（引用真实性） | `evidence` + `confidence.basis` |
| 多 Agent 协同与自主闭环 | 契约为跨 Agent 数据交换的硬接口 |
| 探索过程与科学/研究信号 | `counterfactual`、`confidence` |
