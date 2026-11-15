# VLab 服务层：真实调用记录（2026-09-21）

> 本文记录 **VLab HTTP 服务第一次被真实请求打到**的原始结果。
> 它是「验证即服务」从**库**变成**服务**的分界证据。
>
> ⚠️ **必须直说的边界**：这次的调用方是**本仓自己的验证 harness**，不是独立第三方。
> 因此它证明的是「入口存在且语义正确」，**不证明市场需求存在**。
> 市场那一问（`market-position-2026-09-21.md` 第 6 条）仍然悬着 —— 见文末。

---

## 1. 服务形态

| 项 | 值 |
|---|---|
| 实现 | `engine/vlab_server.py`（纯 stdlib，**零依赖**） |
| 启动 | `python scripts/vlab.py serve --port 8790` |
| 绑定 | 默认 `127.0.0.1:8790` |
| 鉴权 | `Authorization: Bearer <token>`；token 取自 `SWARMLABS_VLAB_TOKEN` / `data/vlab/server_token` |
| **拒启条件** | **非回环地址且无 token ⇒ 拒绝启动**（不静默裸奔） |
| 留痕 | 每次调用写 `data/vlab/calls.jsonl`（含被拒的 401/409） |
| OpenAPI | `GET /vlab/openapi.json`，**就地生成**（不落静态文件 ⇒ 不会与实现漂移） |

**为什么不在边缘（CF Pages Functions）**：那些是 JS，跑不了 Python。
VLab 流水线是 Python + stdlib，所以这一层作为**受控进程**运行（本机 / ECS）。
硬把流水线用 JS 重写一遍 = 两套实现 = 必然口径分裂（本项目已多次踩过这个坑）。

**为什么零依赖**：与 `engine-kit/vv_gate_server.py` 同一纪律 ——
**跑不起来的门禁等于没有门禁**。CI 里用 `python -S`（跳过 site-packages）跑，
用来检出任何偷偷引入的第三方依赖。

## 2. 端点

| 方法 | 路径 | 鉴权 | 语义 |
|---|---|---|---|
| GET | `/vlab/health` | 公开 | 存活 + 账本口径 + 诚实声明（含「未知路由 = 404」） |
| GET | `/vlab/schema` | 公开 | 工作单契约 + 可提交样例 |
| GET | `/vlab/disciplines` | 公开 | 专业接入状态（VALIDATED / AWAITING_GOLD） |
| GET | `/vlab/openapi.json` | 公开 | 本文件的机器可读版 |
| GET | `/vlab/jobs` | 公开 | 最近 job 列表 |
| GET | `/vlab/jobs/{id}` | 公开 | 单个 job |
| GET | `/vlab/jobs/{id}/delivery` | 公开 | 交付物原文 |
| POST | `/vlab/intake` | **Bearer** | **只校验不跑** |
| POST | `/vlab/run` | **Bearer** | 跑完整流水线并落盘交付物 |
| POST | `/vlab/verify` | **Bearer** | 离线式复验交付物（篡改检测） |

## 3. 真实调用结果（8 次请求，原始记录）

`ts_utc = 2026-09-21T04:17:43Z`，调用方标识
`swarmlabs-internal-harness/1.0 (CI record; NOT an independent third party)`。

| # | 方法 | 路径 | HTTP | outcome | hard_verdict | 判读 |
|---|---|---|---|---|---|---|
| 1 | GET | `/vlab/health` | 200 | — | — | 存活，账本口径 82/79-2-1 |
| 2 | GET | `/vlab/schema` | 200 | — | — | 契约可机器读取 |
| 3 | POST | `/vlab/intake` | **401** | — | — | **无 token 被拒**（fail closed） |
| 4 | POST | `/vlab/intake` | 200 | — | — | 带 token 通过，`work_order_id=wo-e97f724a406bf888` |
| 5 | POST | `/vlab/run` | 200 | **PROCEED** | **PASS** | 内核场景走通全六段 |
| 6 | POST | `/vlab/run` | 200 | **NEEDS_ONBOARDING** | **null** | **未接入专业：拒绝给出裁决** |
| 7 | GET | `/vlab/jobs` | 200 | — | — | 可回读 |
| 8 | POST | `/vlab/verify` | 200 | — | — | 交付 `DELIVERY_INTACT` |

请求 5 的交付摘要：`95e42905d1f490035de213723aef6ae6fe8d00c6807b31607a6c28af78659d61`
（`data/vlab/jobs/w3-eso-ecoli-cli/delivery.json`，本地可复算）。

请求 5 的六段阶段记录（全 `ok=true`）：
`intake → scope → build → verify → corroborate → deliver`；
`verdict = PASS / gate = PROCEED / cite_ok = true`；
锚定筛选 `ACCEPTED=1, NEEDS_HUMAN=0, REJECTED=0`。

**第 6 条是本次最重要的一条**：`Chemistry` 尚未接入（无 gold 引用），
服务返回 `200 + NEEDS_ONBOARDING + verdict: null + onboarding 清单`。
给出裁决才是失败 —— 接口没有提供「未接入也硬给个结论」这条路。

## 4. 服务层的否定性门禁（30/30）

`scripts/check_vlab_server.py`：真起服务 → 真发请求 → 断言。
29 条里 20 条是**否定性**的。

| 断言 | 要求 |
|---|---|
| N1/N2/N3 | 未知路由、未知 job **必须 404** —— 绝不 200 兜底 |
| N4/N5/N6 | 无 token / 错 token **必须 401**，且带 `WWW-Authenticate` |
| N7/N8 | 坏 JSON / 空 body **必须 400** |
| N9 | 缺 `discipline` **必须 422** |
| N10/N11 | `corroboration.as_input=true` **必须 422 + `ORDER_INVERTED`**（`/vlab/run` 直跑也拒） |
| N12 | `work_order_id` 目录穿越 **必须 422 且不落盘** |
| N13 | 未接入专业 **必须 `verdict: null`** |
| N14 | 改一字段的交付 **必须 409 + `DELIVERY_TAMPERED`** |
| N15 | 不存在的 job 复验 **必须 404**（不是「intact」） |
| N16 | 非回环 + 无 token **必须拒绝启动** |
| P0 | **篡改注入自检**：注入必须真的改变了内容 |

> **P0 是本轮真实踩到的坑**：第一版把 `hard_verdict` 改成 `"PASS"`，
> 而该场景真实值本来就是 `PASS` ⇒ **篡改是个空操作**，测试变成假绿。
> 这类「注入的篡改没真的改变内容」是假绿门禁的隐蔽来源，已固化为独立断言。

## 5. 本地复跑（与 CI 逐字同命令）

```bash
python scripts/check_vlab_gates.py          # 流水线层 24/24
python scripts/check_vlab_server.py         # 服务层   30/30
python -S scripts/check_vlab_server.py      # 真·零依赖（检出偷依赖）
python scripts/vlab.py serve --port 8790 --print-token   # 起服务
```

## 6. 这一条推进了什么，没推进什么

**推进了**：「实验发过来」现在有了真实的网络入口，且这个入口在
未接入专业上**拒绝给结论**、在无凭证上**拒绝服务**、在未知路径上**拒绝 200**。

**没推进**：调用方仍是自己。`market-position-2026-09-21.md` 第 6 条要求的是
**一个真实外部系统调用一次门禁**（非自测、非 mock），用来区分
「需求存在」与「需求不存在」—— 本记录**不能**充当那条证据。

要把它变成真证据，最小动作是把 token 发给一个真实外部使用者
（合作方 / 参赛评委 / 任何一个无关的人），让对方**自己**提交一次工作单，
然后看 `data/vlab/calls.jsonl` 里出现**非本仓**的 `caller`。
在出现那条记录之前，本项目的市场位置仍是「未验证」。
