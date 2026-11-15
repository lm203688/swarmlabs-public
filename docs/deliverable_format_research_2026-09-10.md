# 交付物形态市场调研 ——「成果太简单」是不是真问题？

> 日期：2026-09-10
> 起因：用户提出「最后给用户的实验成果相对费用太简单，没有一份厚厚的报告，竞品难道也这样吗？感觉不划算」
> 方法：一手外部检索 + 本地代码审计，不做印象判断
> 结论：**用户的感觉是对的，而且比他说的更严重。根因不是"做得少"，是"算了不交付"。**

---

## 0. 一句话结论

**业界在交付物形态上有明确共识：付费用户拿到的是一份可归档的文档对象（PDF/DOCX/report），不是一串数值。**

我们在这个细分（代理模型 + 不确定性量化）**不是没有标准可循，而是有硬标准**——ASME V&V 10-2019 与 FDA 计算建模报告指南，两者都明文要求产出文档。

更关键的是：**`engine/surrogate.py:766` 的 `report()` 早就产出了 V&V 报告所需的大部分内容，注释写着"一份可直发的领域报告"——但它是一个从来没有变成文档的 dict。** 线上唯一的 export 能力是 BibTeX。

所以这不是"我们缺能力"，是**缺一层渲染**。

---

## 1. 竞品实际交付什么（一手核实）

### 1.1 Elicit —— 最接近的形态参照

| 项 | 事实 |
|---|---|
| 定位 | AI for scientific research，宣称 5M+ 研究者使用 |
| **核心交付物** | **Automated Research Report**，句子级引用（sentence-level citations） |
| 报告分档 | Fast（~50 sources） / Balanced（~200） / Comprehensive（~500） |
| 系统化工作流 | Systematic Review，已支持 **PRISMA 2020**，可复现可追溯 |
| **导出格式** | **RIS / CSV / BIB / PDF / DOCX**，另有 Zotero 互通、REST API |
| 定价 | Basic $0（每月 2 份报告）→ Plus **$7/月**（4 份）→ Pro **$29/月**（144 份/年）→ Scale **$49/月**（240 份/年）→ Enterprise |

**注意这一点**：Elicit 免费档一个月只允许生成 **2 份**报告，付费价值几乎全部押在"报告份数与规模"上。**他们卖的就是报告本身。**

### 1.2 Google Literature Insights（NotebookLM）—— Google 自己怎么定义"交付"

官方对三家第三方评测中被一致强调的一点：

> "The output formats are the distinctive part. Standard AI literature tools tend to produce text summaries. **Literature Insights produces objects** — reports, slide decks, infographics, audio and video overviews."

Google 把 "Literature Insights" 的产出定义为 **objects（对象/产物）**，明确区别于"text summaries（文本摘要）"。这是**顶级玩家对交付物形态的公开定调**。

### 1.3 SciSpace / Scite —— 结构化而非问答

- **SciSpace**：structured reports = 摘要综合 + **跨研究对比表** + 后续研究建议，可存入 notebook / 导出。
- **Scite**：Smart Citations 把引用分为 supporting / mentioning / **contrasting** 三档，附 confidence score 与原文位置。

**共同点**：都交付"带结构的整合结果"，不是单次问答的一段文字。

### 1.4 Co-Scientist / Gemini for Science —— 前端 Galois 的上限定调

Nature 发表（2026-05），输出形态是 **ranked portfolio of hypotheses，每条附 supporting rationale（支持论据与反驳论据）**。即：**排名 + 论证链**，不是单值。

---

## 2. 我们这个细分有没有标准？有，而且是硬标准

这一条最关键——**"要不要写厚报告"不是口味问题，是有规范的**。

### 2.1 ASME V&V 10-2019（计算固体力学 V&V 标准）

标准目录中**专门设有文档章节**：

```
3  Model Development
   3.5 Sensitivity Analysis
   3.6 Uncertainty Quantification for Simulations
   3.7 Documentation of Model Development Activities
4  Verification
   4.3 Verification Documentation        ← 明要求
5  Validation
   5.1 Validation Experiments
   5.2 Uncertainty Quantification in Experiments
   5.3 Accuracy Assessment (Validation Metrics)
   5.4 Validation Documentation          ← 明要求
```

### 2.2 FDA 医疗器械 CM&S 报告大纲（一手文件 fda.gov/media/87586）

FDA 给出明确的 **Outline of the Report**：

| 章节 | 要求内容 |
|---|---|
| **I. Executive Report Summary** | 使用情境与范围、选计算而非实验的理由、所做分析类型、模型（几何/材料/边界）、软件来源、**支持 (validation activities) 是否充分**、**局限 (limitations)**、结论 |
| II. Background / Introduction | context of use |
| III. Code Verification | SQA、与解析解对比、离散化/求解器参数敏感性、收敛判据 |
| … | *（后续为具体学科 appendix）* |

**两个必须注意的词**：`validation activities`（我们做了，但没汇总）和 `limitations`（**我们诚实边界的核心，必须写进执行摘要，不能藏在附录**）。

### 2.3 ASME「实用验证评估工作流」四要素

一篇 ASME 论文给出的完整验证评估工作流：

1. **Model Accuracy** — 相对实验的精度 + **该精度的不确定度**
2. **Model Acceptability** — 给定用途下接受/拒绝模型的证据
3. **Validation Evaluation** — 模型、实验、验证过程的定量评价
4. **Validation Recommendations** — 后续验证策略与资源配置的建议

> **逐条对照：第 1 项我们算了（rmse/coverage），第 2 项我们有（guard 三态门禁），第 3 项我们有（evaluate/grader），第 4 项我们有（recommend_next_active / BALD）。**
>
> **四要素我们算了三个半，但从来没有把它们写进同一份东西里。**

---

## 3. 本地审计：我们自己已经算了什么

### 3.1 `engine/surrogate.py:766 report()` 实际返回

```python
{
  "key", "category", "n_points",
  "rmse", "rmse_rel",
  "coverage", "coverage_state",   # ok / under / over
  "cal_err",
  "ls_source",
  "next_experiment",              # 推荐的下一个实验点
  "references",                   # 文献
  "published",                    # 发表公式
}
```

**这已经是 V&V 报告的一半内容**，源码注释原话即「生成一份可直发的领域报告」。但它是一个 **dict**，只在内部传递，**没有任何环节把它渲染成文档**。

### 3.2 线上 API 暴露了多少

| 检查项 | 结果 |
|---|---|
| 是否有「生成报告」端点 | **无** |
| 是否有导出 PDF / DOCX / Markdown | **无** |
| 线上唯一的 export 能力 | `v2/export/bibtex`（文献 → BibTeX） |
| `export` 权限开关 | 在套餐定义中，free 档 `export: false`，付费档 `export: true` |

**这是本次审计最扎心的一条**：我们把付费专属的 "export" 权限，实际实现成了**导出参考文献格式**。用户为 Pro 付费换来的 export 权益 ≈ 一个 .bib 文件。

对标 Elicit $7/月 能拿到 PDF/DOCX 报告——**我们当前的付费价值感确实弱，用户的判断有据。**

---

## 4. 差距总结

| 维度 | 业界共识 | SwarmLabs 现状 | 缺口 |
|---|---|---|---|
| 交付形态 | 文档对象（PDF/DOCX/slides） | JSON | **未渲染** |
| 结构 | 遵循标准大纲（ASME / FDA / PRISMA） | 无大纲 | **未对齐** |
| 证据链 | 句子级引用 / supporting-contrasting | 有 references 字段但未组织 | 半成品 |
| 局限性 | 必须在执行摘要明确写出 | 分散在 dashboard | **未进主体** |
| 可复现 | PRISMA 式可追溯 + 参数/seed | 有 seed 但未进交付物 | **未利用的强项** |
| 建议 | Validation Recommendations | `next_experiment` 已有 | 未成章 |

**唯一的例外好消息**：我们的报告是**确定性数值 + 可重跑参数**，Elicit/NotebookLM 的是 LLM 综述（不可复现、存在幻觉引用）。**这是我们能诚实取胜的地方，但目前完全没用上。**

---

## 5. 建议：做一个 V&V 报告生成器（P0）

### 5.1 设计原则（对齐 replace 与诚实边界）

- **不是让 LLM 写综述**，而是把已有计算结果按标准结构组织——否则我们退化成 Elicit 的劣化版，且失去可复现优势。
- **局限性必须进执行摘要**，不能放附录。这是我们的诚实招牌，也是 FDA 的明文要求。
- **带 reproducibility manifest**：scenario key、bounds、seed、GP 超参、噪声地板值、代码版本。让收到报告的人能重跑出同一个数。

### 5.2 报告结构（对齐 FDA CM&S outline + ASME V&V 10）

```
1. Executive Summary        结论 + 精度 + 覆盖率 + 【局限性】
2. Context of Use           本结论适用于哪个工况范围（含 OOD 边界）
3. Method                   代理模型选择、长度尺度来源、核函数、噪声地板
4. Code Verification        与解析/基准的双边对等检验（已有 run_parity_loop）
5. Uncertainty Quantification   LOO R²、RMSE、95% 覆盖率、校准误差
6. Validation Metrics       PASS / MARGINAL / REFUTED，含判定依据
7. Out-of-Disposition Gate  拒绝点数量与理由
8. Recommendations          下一步实验点（next_experiment / BALD）
9. Reproducibility Manifest seed / 参数 / 版本 / 重跑命令
10. References              情景的发表公式与文献
```

### 5.3 依赖盘点：几乎不用新造轮子

| 报告章节 | 现有能力 | 位置 |
|---|---|---|
| 精度/覆盖率/校准 | ✅ 已有 | `surrogate.evaluate()`、`report()` |
| 下一步建议 | ✅ 已有 | `recommend_next_active()`、`recommend_bald()` |
| OOD 门禁 | ✅ 已有 | `engine/guard.py`、`guard_local.py` |
| 双边对等 | ✅ 已有 | `run_parity_loop()`、`mirror_run()` |
| 文献与公式 | ✅ 已有 | scenario `references` / `published` |
| 声明分级 | ✅ 已有 | `paper-claim-validation/scripts/grade_claim.py` |
| **渲染层** | ❌ **缺** | 需新建 |

### 5.4 待决策（需用户拍板）

1. **输出格式优先级**：PDF / HTML / Markdown / DOCX 先做哪个？
   （建议 Markdown → HTML，PDF 可用打印样式；DOCX 最重，最后做）
2. **是否作为付费权益重新定价**：目前 Pro ¥39.9 的 export 只有 bibtex；若报告上线，是把报告纳入现有 Pro 还是单独分档？
3. **是否立刻给 CVE/ASHRAE 之类的第三方审计留 inhibitory 接口**——暂时不需要。

---

## 6. 诚实边界（写这份调研时必须说明）

- Elicit 的定价取自第三方评测站（2026 核实），**官方页面按账期/账户视图会变化**，实际以官方为准。
- Google Literature Insights / Co-Scientist 目前通过 Google Labs 逐步开放，**企业侧走 Google Cloud 销售，定价未公布**——所谓"竞品价格"只对 Elicit/SciSpace 成立。
- **「Co-Scientist 已用湿实验验证」是其论文自述，未经独立审计**（第三方评测已指出这一点）。本报告不引用其作为能力上限的证据，只引用其"排名+论证链"的输出形态。
- ASME V&V 10、FDA 指南是**工程/监管领域的规范**，不自动适用于全部科研场景；选用它们是因为我们做的是 *surrogate + UQ*，这正是其适用域。
