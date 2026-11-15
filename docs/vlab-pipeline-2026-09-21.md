# 综合虚拟实验室（VLab）—— 编排层落地记录

> 日期：2026-09-21 ｜ 上游评估：`docs/feasibility-virtual-lab-2026-09-21.md`
> 判据来源：`docs/goal_realignment_2026-09-09.md` §1 单一判据
> 入口：`python scripts/vlab.py`
> 门禁：`python scripts/check_vlab_gates.py`（24 条，含 22 条否定性）

---

## 0. 这一轮做了什么

评估文档 §7 列出四块要建的东西。本轮把其中**三块**落地为可执行代码，
第四块（锚定数据的**自动**抓取）按评估结论**刻意不做全自动**——只做了
"防垃圾进"的筛选层，理由见 §5。

| # | 要建的东西 | 本轮状态 | 产物 |
|---|---|---|---|
| 1 | Intake / Scope / Job 编排层 | ✅ 落地 | `engine/vlab_contract.py`、`engine/vlab_orchestrator.py` |
| 2 | 专业接入脚手架 | ✅ 落地 | `engine/discipline_registry.py` |
| 3 | 锚定数据层硬化（防垃圾进） | ✅ 落地（**不做全自动抓取**） | `engine/anchor_intake.py` |
| 4 | 对外交付契约 | ✅ 落地 | `engine/vlab_delivery.py` |

---

## 1. 六段流水线

```
intake → scope → build → verify → corroborate → deliver
```

| 阶段 | 做什么 | 关键约束 |
|---|---|---|
| `intake` | 校验工作单 | 非法即拒，**不进 build/verify** |
| `scope` | 专业路由 + 落成 scenario spec | 未接入专业 → `NEEDS_ONBOARDING`，**不给裁决** |
| `build` | 搭虚拟实验 | 只吃白名单输入（`BUILD_INPUT_KEYS`），**结构上拿不到真实观测** |
| `verify` | 从门禁单一真源搬裁决 | 账本无该场景 / 账本过期 → fail-closed 拒绝沿用旧结论 |
| `corroborate` | 真实数据**事后**作证 | 只增信，`affects_verdict=false`，不改判 |
| `deliver` | 交付契约 + 摘要 | 改一字段即 `DELIVERY_TAMPERED` |

跑完必调 `assert_order_integrity(job)`：**阶段单调 + build/真实数据隔离 +
裁决在 corroborate 前后逐位不变**，任一不成立即抛错。这不是文档承诺，
是运行时不变式。

---

## 2. 红线如何从"承诺"变成"不可表达"

评估文档 §1 判定：真实数据作**前置输入**（读法 B）会让产品退化成
"编排真实实验"，出局。

本轮把它做进**数据结构**：

```jsonc
"corroboration": {
  "as_input": false,        // ← 写 true 直接拒收（错误码 ORDER_INVERTED）
  "points": [ /* 真实观测点 */ ]
}
```

- `validate_work_order()` 在 `as_input: true` 时返回 `ok=false`，错误码
  `ORDER_INVERTED`；工作单根本进不了 build 阶段。
- `build` 阶段的输入走**白名单**（`credential / discipline / goal /
  design_space / method / scenario_key / seed / claimed_refs`）——
  `observations`、`real_points`、`y` 这类键不在表内，出现即抛
  `OrderInvertedError`。
- 再加一条**数据隔离**检查：哪怕换了键名，只要某个 build 输入与任一作证点
  **逐字节相同**，也抛错。

也就是说，"偷偷把真实数据喂给虚拟实验"这件事在这里有**三条独立的拦截**，
而不是一句叮嘱。

---

## 3. 专业接入脚手架

`engine/domains.py` 的 9 大类 taxonomy 是单一真源；本模块只加**准入条件**。

一个专业允许被标 `VALIDATED` 的必要条件（两条同时满足）：

1. **至少一条 gold 可解析**——要么是仓库内产物指针（`path#json.key`，
   校验时真的打开文件、真的走到那个键），要么是含年份/DOI 的文献引用；
   且必须带单位与数值/区间。
2. **内核场景真在门禁账本里**——不是一个占位字符串。

当前状态（`python scripts/vlab.py disciplines`）：

| 专业 | 状态 | gold | 内核场景 |
|---|---|---|---|
| Microbiology | **VALIDATED** | 1/1（指针指向 `wet_lab_anchors.json`） | `micro_ecoli_glucose` |
| Chemistry / Environment / Energy / Materials / Separation / Analysis / Pharma / Electrochemistry / NeuroRobotics | AWAITING_GOLD | 0 | — |

**9 个专业的 gold 是空着的，不是编造的。** 这与项目既有纪律一致：
`wet_lab_anchors` 的 19 个锚点是人工策展的结果，为凑数铺满"文献区间"等于
造假。空着是诚实，填假的是灾难。

接入清单由 `onboarding_checklist()` 给出（6 步，每步带产出物与验收判据），
`register()` **在校验不通过时拒绝写盘**——一份"写进去但没准入"的档案比
没有更糟，它会让人以为该专业已接入。

---

## 4. 判定三档与"无自动接纳路径"

锚定筛选（`anchor_intake`）只判断"这条参照数据能不能被当作锚定 chip"，
**不判断科学对错**。判据顺序（前层不过不看后层）：

| 序 | 检查 | 不通过 |
|---|---|---|
| 1 | 数值有限 | `REJECTED` |
| 2 | 引用含年份或 DOI | `REJECTED`（`unverifiable_citation`） |
| 3 | 单位可解析 | `REJECTED`（`unknown_unit`） |
| 4 | 与目标量**同量纲** | `REJECTED`（`dimension_mismatch`） |
| 5 | 在声明的合理区间内 | `NEEDS_HUMAN` |
| 6 | 有具名确认人 | `NEEDS_HUMAN` |
| 7 | 全过 | `ACCEPTED` |

**不存在自动 ACCEPT 路径**：第 6 条决定"机械全过也必须有个人署名"。

---

## 5. 为什么不做全自动抓取

评估文档 §6 把**垃圾进风险**列为第一号诚实风险。本轮的选择是：

- **做**：机械可查的三层筛选（可核验性 / 量纲 / 合理区间）+ 人工署名门槛。
- **不做**：全自动检索并采纳文献数值。理由不是"技术做不到"，而是——
  一个"验证服务"若自动采纳未经验证的参照数据，它的可信度就建立在自己
  没验证的东西上（自指坍塌）。

文献检索可以作为**候选生成器**（产出 `ReferenceCandidate` 列表），但必须
经本层筛选 + 人工确认。这一条已写进交付物的诚实边界。

---

## 6. 交付契约

`delivery.json` 的关键内容与"改一字段即失效"的规范化摘要：

- 裁决字段**直接搬自** `engine/verification_gate.py`（阈值不重算、不发明）；
- `corroboration.affects_verdict = false` —— 作证不影响裁决，写进交付物本身；
- `honest_boundaries` —— 交付层边界 + 复用 `vv_package` 的边界（单一来源）；
- `verification.instructions` —— 第三方**离线**复验的可执行步骤；
- `engine.all_digest` —— 裁决钉死在哪一版引擎上（**权威**：文件内容的换行规范化摘要）。

> **`engine.git_commit` 是 advisory，不可当权威**：本仓推送走 Contents/Git-Data API，
> 该 API **不推进本地 HEAD**，所以这个字段取自本地 `.git HEAD`，**可能指向一个并不包含当前引擎状态的提交**。
> 已加 `git_commit_source` / `git_commit_binding` 两个字段把这一点写进产物本身（不删字段，因为删了就丢了线索）。
> 第三方要复算，请用 `all_digest`：在目标 ref 上取 blob → `\r\n`→`\n` → sha256 → 按文件名排序聚合，
> 与产物里的 `all_digest` 比对。**本地自测不能替代这一步**（见下 §7 缺陷 10）。

三种结果：

| 状态 | 含义 |
|---|---|
| `DELIVERY_INTACT` | 内容与摘要一致 |
| `DELIVERY_TAMPERED` | 被改过（exit 2） |
| `DELIVERY_UNREADABLE` | 读不到 |

**边界要分清**：`verify_delivery()` 校验的是**文件没被动过**，不是
"判定是对的"。这两件事必须分开说，否则就是拿完整性冒充正确性。

---

## 7. 本轮实测暴露并修复的真实缺陷

这些不是设计推演出来的，是**跑起来才发现的**。逐条记录，因为它们比
架构图更能说明这套东西在哪儿脆：

| # | 缺陷 | 后果（若不修） | 修复 |
|---|---|---|---|
| 1 | 契约把观测值叫 `y`，锚定层读 `value`，未桥接 | **每一条真实提交都被判 `bad_value` 拒收**——看起来在工作、实际全拒 | `ReferenceCandidate.from_dict` 显式读 `value` 或 `y` |
| 2 | fail-closed 三个分支的 verdict **键集不一致** | 消费方得写 `if 'cite_ok' in v` 这种脆弱代码 | 统一出口 `_fail()`，键集固定 |
| 3 | 单位表 `mm`/`nm` **重复注册**到两个量纲（后写覆盖） | 静默的单位错误——正是"自信地错"的典型来源 | 删重复键；歧义单位按长度解，摩尔浓度必须写全 `mmol/l` |
| 4 | 单位表注释写"不注册 mm"但代码注册了 | 注释与行为相反，下一个人会照注释理解 | 同步注释与代码 |
| 5 | `assert_order_integrity` 取 `stage["verdict"]`（实际字段是 `hard_verdict`） | 不变式**永远误报**，会被当成噪音而关掉 | 改取 `hard_verdict` |
| 6 | 筛选报告丢失原始观测点（`x`/`y` 未回显） | 客户看到"你有一条被拒"却不知是哪条 | `ReferenceCandidate` 保留原始字典并回显 |
| 7 | i18n 指标 `cjk_in_english_page` 写成 `if cjk:` | 统计**所有**页面 → 往中文页补中文被报成"i18n 债回归"（假回归） | 限定 `lang.startswith("en")`，与 `lang_mismatch` 判据一致 |
| 8 | `_sha256_file` 直接哈希原始字节 | 仓库存 LF、Windows 工作区 CRLF ⇒ **同一仓库在不同平台算出不同指纹**，「账本新鲜度」门禁不可能跨平台同时为真 | 换行统一为 LF 后再哈希 |
| 9 | 账本用默认文本模式写盘 | Windows 上写成 CRLF ⇒ 每次本机重建产生**全文件 diff**，且下游字节哈希平台相关 | `newline="\n"` + 收尾换行 |
| 10 | `engine.git_commit` 取本地 HEAD | API 推送不推进本地 HEAD ⇒ 该指针**可能指向不含当前引擎的提交**（可追溯性谎报） | 加 `git_commit_source` / `git_commit_binding` 明示 advisory；权威改用 `all_digest` |
| 11 | 判「CI 会不会绿」用本地自测 | 本地自测**结构性地**回答不了「远端字节 vs 本地字节」这个问题（本轮三次误判均源于此） | 远端取 blob 用同一套规范化复算 digest，与远端账本比对 |

第 8 条是最有价值的一条：它解释了一个此前无人问过的现象——为什么
"指纹可复算"这句话在跨平台上其实不成立。

第 11 条是**方法上的**：本轮三次判断真因（multifidelity 未推 / 账本未同批 / 换行规范化）
都靠"远端复算"定位，且**没有一次是猜测**。若只能本地自测，这三次都会给出错误结论。

---

## 8. 一个必须纠正的自我误判

此前我报告"用**不装 numpy 的托管基 python** 模拟第三方零依赖复验"。
**这个说法是错的**：本机托管基 python 装着 numpy 2.5.2 / scipy。用 `-S`
（跳过 site-packages）才是真正干净的解释器：

```
python -S scripts/check_vlab_gates.py       # 24/24
python -S scripts/build_vv_package.py selfcheck   # STDLIB_ONLY_OK
```

已用 `-S` 重新验证：VLab 各模块与 `vv_package` 在 numpy 确实不存在的
环境下都跑通。CI 的 `vlab-gate` 刻意**不装 numpy**，因此它是真正的
干净解释器检验。

---

## 9. CI 门禁

新增 job `vlab-gate`（零依赖，刻意不装 numpy）：

| 步骤 | 断言 |
|---|---|
| 否定性自检 | 22 条"应当被拒"的输入确实被拒（含红线 `ORDER_INVERTED`） |
| 专业注册表 | 不存在"被标 VALIDATED 却没有任何可解析 gold"的专业 |
| 样例工作单 | 内核场景出裁决；未接入专业**拒绝**给裁决 |
| 交付物 | 离线复验 `DELIVERY_INTACT` |
| **服务层** | **真起 HTTP 服务 + 真发请求**：未知路由 404 / 无 token 401 / 倒置 422 / 未接入不给裁决 / 篡改 409 / 非回环无 token 拒启（30 条，20 条否定性） |
| **零依赖复跑** | `python -S` 跑服务层与流水线门禁（检出任何偷引入的第三方依赖） |

与既有门禁的分工：

- `ledger-fresh-gate` 守**结论时效**（引擎一改即强制重验）；
- `public-numbers-gate` 守**数字口径**；
- `vlab-gate` 守**流程纪律**（顺序不可倒置、无自动接纳、未接入不给裁决）+ **接口纪律**
  （未知路由不得 200 兜底、无凭证不得服务、未接入不得给结论）。

三者都是 fail-closed，但管的是不同的腐化路径。

---

## 10. 服务层（HTTP 入口）

流水线能跑但**不是一个服务**。商业模式是「把一个需要验证的实验发过来」，
发过来需要网络入口。`engine/vlab_server.py` 只做三件事：**鉴权、路由、落盘** ——
流水线逻辑一行都不复制，全部委托 `run_job()`（单一真源）。

| 方法 | 路径 | 鉴权 | 语义 |
|---|---|---|---|
| GET | `/vlab/health` | 公开 | 存活 + 账本口径 + 诚实声明 |
| GET | `/vlab/schema` | 公开 | 契约 + 可提交样例 |
| GET | `/vlab/disciplines` | 公开 | 专业接入状态 |
| GET | `/vlab/openapi.json` | 公开 | **就地生成**（不落静态文件 ⇒ 不会与实现漂移） |
| GET | `/vlab/jobs` ｜ `/vlab/jobs/{id}` ｜ `/vlab/jobs/{id}/delivery` | 公开 | 回读 |
| POST | `/vlab/intake` | Bearer | 只校验不跑 |
| POST | `/vlab/run` | Bearer | 跑流水线并落盘交付物 |
| POST | `/vlab/verify` | Bearer | 离线式复验（篡改检测） |

**三条不可让步的失败语义**：

1. **未知路由必须 404**，绝不 200 兜底 —— "200 即成功"是最贵的假绿；
2. **未接入专业不得给出裁决**：`200 + NEEDS_ONBOARDING + verdict: null` + 接入清单；
3. **非回环地址绑定且无 token ⇒ 拒绝启动**（fail closed，不静默裸奔）。

其他工程决定：

- **不部署在边缘**。CF Pages Functions 是 JS，跑不了 Python；把流水线用 JS
  重写一遍就是两套实现，必然口径分裂（本项目已多次踩过）。故作为**受控进程**运行。
- **零依赖**：CI 用 `python -S` 跑，用来检出偷依赖。
- **调用留痕**：`data/vlab/calls.jsonl` 记 `caller` / `request_id` / 状态码 / 裁决 ——
  这是"有没有人真的来用"的唯一凭据。

**真实调用记录**见 `docs/vlab-call-record-2026-09-21.md`（8 次请求，
含 401 拒绝、`PROCEED/PASS`、`NEEDS_ONBOARDING/verdict:null`）。
⚠️ 该记录的调用方是**本仓自己的 harness**，证明「入口存在且语义正确」，
**不证明市场需求存在**。

---

## 11. 使用方式

```bash
python scripts/vlab.py schema                    # 契约说明
python scripts/vlab.py disciplines               # 专业接入状态
python scripts/vlab.py checklist Chemistry       # 接入清单
python scripts/vlab.py intake  <work-order.json> # 只校验
python scripts/vlab.py run     <work-order.json> --out .vlab-ci/kernel
python scripts/vlab.py verify  <delivery.json>   # 离线复验
python scripts/vlab.py selftest                  # 否定性自检（流水线层 24/24）
python scripts/vlab.py serve [--port 8790]       # 起 HTTP 服务
python scripts/vlab.py selftest-server           # 服务层否定性自检（30/30）
python -S scripts/check_vlab_server.py           # 真·零依赖复跑
```

退出码：`0` 成功 ｜ `1` 请求非法 ｜ `2` 交付被改动 ｜ `3` 裁决为
"不得自动推进"（**是业务结果，不是错误**）｜ `4` 环境错误。

样例：`data/vlab/work-orders/w3-microbio-ecoli.json`（走内核场景，含四种
筛选结果的演示）、`data/vlab/work-orders/w3-chemistry-onboarding.json`
（未接入专业）。

---

## 12. 已知边界与未做的事

- **prediction-vs-observation parity 未计算**。它需要数值引擎（numpy），
  本层是 stdlib 层，只做数据筛选与记录，**不编造 parity 数字**；交付物里
  明确标注 `parity.computed = false` 及原因。
- **真实外部消费者仍为 0**。`docs/market-position-2026-09-21.md` 的
  第 6 条（让一个真实外部系统调用一次门禁）**仍未推进**——它仍是唯一
  能证伪"这个位置有没有市场"的动作，而且不是靠再多写代码能替代的。
  **补充（服务层落地后）**：入口现在已经存在且可被外部调用
  （`docs/vlab-call-record-2026-09-21.md`），但第一次调用方仍是本仓自己的
  harness。要变成真证据，最小动作是**把 token 发给一个真实外部使用者**，
  然后看 `data/vlab/calls.jsonl` 里出现**非本仓**的 `caller`。
- **服务层无速率限制、无配额、无多租户隔离**。单 token、单进程、单机；
  它现在的定位是「能跑通且不撒谎」，不是「能对外营业」。
- **`/vlab/run` 是同步阻塞的**。一次工作单跑完才返回；没有作业队列。
  对当前 82 场景的内核路径够用，成为瓶颈之前先不引入队列（YAGNI）。
- **多专业扩展的实际成本未测**。脚手架给出了流程与判据，但"接第二个专业
  要几天"这个问题，只有真做一次才知道。9 个专业仍全部 `AWAITING_GOLD`。
- 本层不判断科学对错，只判断"能不能验证"。这条边界写在交付物里，
  不写在附录里。

---

> **一句话**：本轮把"真实数据只能事后作证"从**文档承诺**变成了
> **schema 层不可表达性 + 三条独立拦截 + 22 条否定性测试**；
> 把"加一个专业"从散文变成了**有准入条件的复用流程**；
> 把"能跑"变成了**有网络入口的服务**（30 条接口门禁，20 条否定性）；
> 并顺手修掉了 11 个跑起来才暴露的缺陷（其中 1 个解释了跨平台指纹为何不可复算，
> 1 个是"篡改注入没真的改变内容"的假绿）。
> 仍然没做、也做不了的是第 6 条：让一个**真实外部**系统调用一次。
