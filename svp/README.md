# SwarmLabs Verification Protocol (SVP)

> **OpenAPI for AI4S Verification** — 科学 AI 验证层的开放标准
>
> 版本：v0.5（阶段 3 · ✅ 2026-09-25 已交付）

---

## 什么是 SVP

SVP 是一个**协议**，不是一个产品。它定义了 AI4S 验证层的"标准格式"，让不同项目、不同工具、不同实验室的验证结果**可以互操作**。

**类比**：
- OpenAPI 之于 REST API → SVP 之于 AI4S 验证
- Kubernetes 之于容器 → SVP 之于科学 AI 验证
- HTTP 之于 Web → SVP 之于科学 AI 可信性

## 7 个核心概念（v0.5）

### v0.1 核心（5 个）

| 概念 | 描述 | JSON Schema |
|---|---|---|
| **Scenario** | 可验证的实验场景 | `svp/schema/scenario.json` |
| **Gate** | 可执行的验证判定 | `svp/schema/gate.json` |
| **Anchor** | 三维证据参照值 | `svp/schema/anchor.json` |
| **Harness** | 可版本化的验证配置 | `svp/schema/harness.json` |
| **V&V Report** | 完整的验证报告 | `svp/schema/vv_report.json` |

### v0.5 新增（2 个）

| 概念 | 描述 | JSON Schema |
|---|---|---|
| **Formal Invariant** | 数学不变量（可三条独立通道验证） | `svp/schema/formal_invariant.json` |
| **ConfidenceProfile** | 多层级分布级置信度画像 | `svp/schema/confidence_profile.json` |

## v0.5 里程碑

- ✅ 5 个 JSON Schema（v0.1）
- ✅ 2 个新 Schema（FormalInvariant + ConfidenceProfile）
- ✅ Lean 4 骨架：`svp/lean/Invariants.lean`（含 6 条 Theorem + INV-6 骨架）
- ✅ SMT-LIB v2 独立通道：`svp/smt/invariants.smt2`
- ✅ 6 条业务不变量（INV-1 ~ INV-6，人类可读语义名清单见下表）

### 6 条业务不变量（语义化命名清单）

> **2026-10-14 板块 09 P0 (#552) 修正**：Lean/Python 内部曾以 `inv1..inv5` 短编号命名，
> 对人类不友好（读者需回翻顶部清单才能知 `inv3` 是"三维证据完整性"）。
> 2026-10-14 起在 `svp/lean/Invariants.lean:276-323` 末尾追加了语义化别名
> （`honesty_conversation_*` / `refuted_irreversible_*` / `anchor_3d_*` /
> `error_bound_*` / `coverage_non_regression_*`），并保留原数字名以向后兼容。
> 下表为**跨通道统一的人类可读清单**：

> **2026-10-14 INV-6 新增（#563 语义降级整改）**：INV-1 只能抓"总数变化"，
> 抓不住"总数不变但内容缩水"——某场景 f 被改简单、bounds 被收窄、
> anchors 被删都会让 total 不变但内容静默退化。INV-6 用内容指纹
> (dim + n_train + n_test + published + domain + uncertainty 组合哈希)
> 把每个场景的内容钉死，任何静默修改都会让指纹改变，从而被捕获。
> 版本升级允许指纹变化，但必须走 baseline → candidate 显式审批
> （`content_fingerprint_baseline` 字段），且新版本必须在
> harness_evolver 的 compare_metrics 严格优于判定下才允许接受。

| INV | 语义化名 | 一句话陈述 | Lean def | Python 谓词 |
|---|---|---|---|---|
| INV-1 | **Honesty Conservation**（诚实性守恒） | `pass+marginal+refuted+error = total`，无静默丢弃 | `invariant_honesty_conversation` | `_inv1_honesty` |
| INV-2a | **Refuted Irreversible · DOI**（REFUTED 不可撤销 · DOI 分支） | 每个 REFUTED 场景必须带 DOI | `invariant_refuted_has_doi` | `_inv2_refuted_irreversible` |
| INV-2b | **Refuted Irreversible · Present**（REFUTED 不可撤销 · 存在性分支） | 公开账本至少一条 REFUTED | `invariant_has_refuted` | `_inv2_refuted_irreversible` |
| INV-3 | **Anchor 3D Completeness**（三维证据完整性） | 决策相关锚点必须 experiment_system × version × operating_condition 三维全填 | `invariant_anchor_3d` | `_inv3_anchor_3d` |
| INV-4 | **Error Rate Bound**（错误上界） | `ERROR / total ≤ 0.01` | `invariant_error_bound` | `_inv4_error_bound` |
| INV-5 | **3D Coverage Non-Regression**（三维完整度不劣化） | 版本升级 `anchors_3d_coverage` 单调不减 | `invariant_3d_non_regression` | `_inv5_3d_non_regression` |
| INV-6 | **Content Fingerprint Integrity**（内容指纹不劣化） | 场景内容指纹（组合哈希）不得与 baseline 不一致；baseline 快照中的 key 不得静默消失 | `invariant_fingerprint_present` / `invariant_fingerprint_matches_baseline` / `invariant_fingerprint_set_non_regression` | `_inv6_content_fingerprint` |

三条独立通道互相印证，任一失败即报警：
- Python 语义：`engine/formal_invariants.py::INVARIANTS`
- Lean 4 定义：`svp/lean/Invariants.lean`（骨架 + 通用引理 + 语义化别名）
- SMT-LIB 求解：`svp/smt/invariants.smt2`
- ✅ Distributional V&V：5 层级 alpha 覆盖 + 校准评级 A/B/C/D
- ✅ 合规检查：`python scripts/check_svp_compliance.py` 5/5 全过

## 三条独立验证通道（Formal Invariant）

```
Python 语义 ──→ Lean 4 定义 ──→ SMT-LIB 求解
(可执行)      (形式化)       (独立求解器)
```

任一通道失败即为信号——"验证层的验证层"。

## 快速开始

### 校验一个场景是否符合 SVP

```python
import json
from jsonschema import Draft202012Validator

with open("svp/schema/scenario.json") as f:
    schema = json.load(f)

with open("my_scenario.json") as f:
    scenario = json.load(f)

Draft202012Validator(schema).validate(scenario)
print("✓ Scenario is SVP-compliant")
```

### 校验一个 V&V 报告

```python
with open("svp/schema/vv_report.json") as f:
    schema = json.load(f)

with open("my_vv_report.json") as f:
    report = json.load(f)

Draft202012Validator(schema).validate(report)
print("✓ Report is SVP-compliant")
```

## 生态状态

- ✅ **SwarmLabs** — 原始实现，82 场景基准 + 42 门禁 + 19 anchors
- ⏳ **ScienceBuddy** — 上游产品，harness 自进化（互补关系）
- ⏳ **未来第三方实现** — 待接入

## 版本路线

| 版本 | 状态 | 里程碑 |
|---|---|---|
| v0.1 | ✅ 当前 | 内部使用，5 个 JSON Schema |
| v0.5 | 🔜 阶段 3 | Lean 4 形式化验证 + Conformal 深化 |
| v1.0 | 🔜 阶段 4 | 稳定 API + 第三方实现 |
| v2.0 | 🔜 阶段 5 | 事实标准 + 生态繁荣 |

## 与 OpenAPI 3.0 的关系

| OpenAPI 3.0 | SVP 1.0 |
|---|---|
| 定义 HTTP 请求/响应 | 定义验证输入/输出 |
| 描述端点、参数 | 描述场景、门禁、锚点 |
| 生成 SDK / Mock | 生成验证器 / Mock 场景 |

## 参考文献

- ScienceBuddy (arXiv:2609.17523, 2026-09-15) — harness 自进化范式
- Barbastathis & Roth 2007 — Monod 参数锚定
- Gal & Ghahramani 2017 — Conformal Prediction
- Hartmann & Ernst 2009 — 否定性测试
- RayGun Nature 2026 (DOI 10.1038/s41586-026-10842-8) — 验证覆盖率问题

## 协议完整文档

见 `docs/svp-protocol-draft.md`

## 合规检查

```bash
python scripts/check_svp_compliance.py
```
