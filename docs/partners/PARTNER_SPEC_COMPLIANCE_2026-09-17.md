# SwarmLabs × RoboParts 接入规范 v1 —— 验收对账（2026-09-17）

> 规范原文：`swarmlabs-api-spec-v1-20260917.md`（RoboParts 侧出具，§8 为十项验收清单）
> 本文件是 SwarmLabs 侧的**逐项对账**。判据不是"我读了一遍代码"，而是
> `python scripts/verify_partner_spec.py` 的实测输出（可复跑、能失败）。

## 0. 先纠正一个前提

RoboParts 侧回传的「两处偏离 spec，等 SwarmLabs 侧修」在**本次实测时已经修好并且在线上了**：

| 回传的偏离 | 实测结果（2026-09-17 17:0x） | 判定 |
|---|---|---|
| ① 无效 key 无 error 区分 | `GET /api/health` 带不存在的 key → **200 + `key_ok:false` + `error:"key_not_found"`** | **已满足** §8.2 |
| ② `X-RateLimit-*` 头缺失 | 三头齐：`X-RateLimit-Limit: 60` / `Remaining` / `Reset` | **已存在** §3 |

所以这两项**不需要再等**。但实测同时发现：第 ② 项虽然"存在"，**内容是错的**（见 §2），
而且线上有一个比这两项严重得多的问题正在发生（见 §1）。

## 1. 真正在坏的东西（已修）

### 1.1 三个 partner 端点在生产上 500

```
GET /api/entities/search?q=neural  -> 500 {"error":"internal_error","message":"failed to parse entities.json"}
GET /api/gaps?domain=robotics      -> 500 同上
GET /api/papers/<id>               -> 500 同上
```

**根因**：Worker 用**单层裸读**取 `entities.json`（`env.ASSETS.fetch` + `res.json()`）。
而 `GET /reports/partner/entities.json` 三次里两次返回 **17996B 的 SPA 兜底页**
（Pages 对未命中路径返回 `index.html`，200 `text/html`）→ `res.json()` 抛错 → 500。

**"为什么这个路径会未命中"** —— 这条推翻了我先前的判断（曾结论为"CF 服务端随机落地残缺"）：

```
远端 main 的树里：
  frontend_fixed/reports/benchmark/index.json        在     <- 早期提交遗留
  frontend_fixed/reports/benchmark/gate_ledger.json  在     <- 早期提交遗留
  frontend_fixed/reports/benchmark/bio_logistic.json 不在
  frontend_fixed/reports/partner/entities.json       不在

而 .github/workflows/{ci-cd,deploy}.yml 会在每次 push 时各跑一次 `pages deploy`，
用的是 actions/checkout 的 **git 树**；.gitignore 里有 `reports/`。
→ 每 push 一次，就用一份**结构性残缺的树**覆盖完整部署。
```

所以「缺件与该次 Source commit 一一对应」这个现象，指向的是 **git 树里有哪些文件**，
不是 CF 的服务端缺陷。而线上看着还能用，是因为 `swarmlabs.tools` 上有
`s-maxage=604800`（7 天）的缓存留着一份旧的好副本 —— **缓存不是病因，是掩盖物**。

**已修**：
- 该读取改走同一函数里那个严格的 `readAsset`（四层取件 + 分类），读失败回
  **503 `service_unavailable`** + `retryable` + `reason` + `tried`，不再回 500。
  "读不到"与"不存在"在响应里必须能分开。
- **CI 部署已停用**（`ci-cd.yml` 的 deploy job / `deploy.yml`），改为跑本文件所述的
  验收清单当守卫。部署唯一入口 = `python scripts/deploy_pages.py`（逐文件字节比对 +
  缺件自动重部署 + 未就绪不采信）。

### 1.2 限流头"存在但在说谎"

| 项 | 实现里的值 | 对外承诺的值 | 说明 |
|---|---|---|---|
| `X-RateLimit-Limit` | 写死 `'60'` | 60 | 但**实际执行**的是 `maxReq = 30` |
| `X-RateLimit-Remaining` | 读**进程内 Map** | — | 而执行限流读的是 **KV**，两者不是同一个计数器 |
| 每分钟阈值 | 30 | 60 | 客户端按 60 做预算，第 31 次被 429 |
| 每日上限 | 无 | 10,000 | 未实现 |
| 突发 20/10s | 无 | 20/10s | 未实现 |
| 429 body | `{error:'Rate limit exceeded', retry_after:60}` | `{error:'rate_limit_exceeded', retry_after_seconds:N}` | code 名与字段名都不符 |

**已修**：`checkRateLimit` 改为一次算出 `{allowed, limit, remaining, reset, retryAfter, reason}`，
**限流判定与响应头只用这一个对象**（消除了"两个计数器"）；阈值按 spec §3 实现
60/min + 10,000/day + 20/10s；429 body 改为规范形状并带 `Retry-After`。

## 2. `verify_partner_spec.py` 修复前基线（实测，7 项失败）

```
[PASS] §8.1  health 无 key -> 200 + key_ok:false
[PASS] §8.2  health 不存在的 key -> 200 + key_ok:false + error:key_not_found
[PASS] §8.3  health 有效 key -> 200 + key_ok:true
[FAIL] §6    health.time 带 +08:00 偏移（不是裸 Z）              -> time='2026-09-17T09:15:41.422Z'
[FAIL] §6    Content-Type 为 application/json; charset=utf-8     -> 'application/json'
[PASS] §3    X-RateLimit-Limit/Remaining/Reset 三头齐
[PASS] §3    X-RateLimit-Limit == 60 且 Remaining 落在 [0,Limit]   （数值对，但见 §1.2 —— 头在说谎）
[PASS] §8.7  x-api-key header 名大小写不敏感
[FAIL] §1    有效 key 回带 X-Partner: <project>                  -> None
[FAIL] §1    401 区分「没带 key」vs「key 没登记」                -> 两条 message 完全相同
[PASS] §8.6  错误响应带 request_id
[PASS] §8.3  entities/search 有效 key -> 200 且有 results          （间歇；见 §8.4）
[PASS] §2.4  /api/gaps 可达（无 5xx）                              （间歇）
[FAIL] §2.5  /api/papers/<id> 可达（无 5xx）                      -> 500 failed to parse entities.json
[PASS] §2.3  /api/bridge 可达（无 5xx）
[PASS] §2.6  /api/trends 可达（无 5xx）
[FAIL] §8.4  同一 URL 10 次调用 body sha256 恒定（幂等）          -> 2 种结果: ['HTTP500', 'dbb6d2fa…']
[PASS] §2.1  health Cache-Control 含 s-maxage=60
[FAIL] §8.9  license.swarmlabs.tools 不再 CONNECT 502            -> DNS 不解析（getaddrinfo failed）
[PASS] self  /v3/verify 对错长度输入回 400（fail-closed 输入校验）

== 13 passed / 7 failed / 2 skipped
```

> §8.4 那一项很值得看：**同一条 URL 连续 10 次，结果在 200 和 500 之间跳**。
> 这就是"间歇失败"的原始形态，也是为什么"人肉 curl 一遍看着都对"会得出错误结论。

## 3. 本轮修复清单（SwarmLabs 侧）

| 项 | 改动 | 位置 |
|---|---|---|
| ① | 三个端点改走严格 `readAsset`；读失败 → 503 而非 500 | `functions/api/[[path]].js` |
| ② | 限流：单计数器 + 60/min·10k/day·20/10s + 规范 429 body | 同上 |
| ③ | `X-RateLimit-*` 取自执行限流的同一个对象 | 同上 |
| ④ | 有效 key 回带 `X-Partner` | 同上 |
| ⑤ | 401 区分「没带 header」与「key 没登记」（同 code，不同 message） | 同上 |
| ⑥ | `health.time` 改带 `+08:00` 显式偏移 | 同上 |
| ⑦ | 补上文档承诺但线上 404 的 `/v3/partner/usage` | 同上 |
| ⑧ | `Access-Control-Expose-Headers` 暴露限流头（否则跨域 JS 读不到） | 同上 |
| ⑨ | 停用 CI 部署，改为跑验收守卫 | `.github/workflows/{ci-cd,deploy}.yml` |
| ⑩ | 验收清单可执行化 | `scripts/verify_partner_spec.py`（新增） |
| ⑪ | 对外接入文档与实现对齐（旧 key、100/min 口径、401 语义、`__partnerUsage`、轮换说明） | `docs/partners/roboparts_onboarding.md` |
| ⑫ | 删除 `deploy.yml` 注释里硬编码的一个 CF API token（凭证入 git） | `.github/workflows/deploy.yml` |

## 4. 仍未满足 / 需要用户侧动作

| 项 | 状态 | 需要什么 |
|---|---|---|
| §8.9 `license.swarmlabs.tools` | **FAIL**：DNS 不解析 | 该子域未创建。规范 §5 第 4 点要求发放走它 —— 若最终不走它，请在 spec 里把它降级为可选项，而不是留一个必然失败的门 |
| 远端 main 里的残缺 `reports/` | 已提交的历史文件（`index.json` / `gate_ledger.json` 等）与生成物的真实集合不一致 | 建议 `git rm --cached` 清掉这批遗留，否则树里永远留着一个"半完整"的假象，将来若恢复 CI 部署会再次踩同一个坑 |
| `deploy.yml` 里那个明文 token | 已从文件删除，但**它曾进入 git 历史，可能仍有效** | 建议在 Cloudflare Dashboard 直接吊销并重建该 token |
| `health.entity_count` | 写死 `157836`，非生成值 | 属于"看着精确其实没在测"的字段；建议改为构建期注入或干脆去掉 |
| `/api/health` 的限流头 | 因 `s-maxage=60`（spec §2.1 要求），最多滞后 60s | 无（已在文档里明说；要精确预算请读数据端点或 `/v3/partner/usage`） |

## 5. 复验方式

```bash
python scripts/verify_partner_spec.py          # 全量（不含限流压测）
python scripts/verify_partner_spec.py --rate   # 加跑 §8.8：等窗口重置后按 0.7s 节奏发 61 包，断言第 61 次 429
```

`--rate` 的测法说明见脚本内注释：必须**先等窗口重置**（否则前面的检查已经用掉配额，
429 会提前出现、看起来像阈值配小了），且必须**限速发包**（否则会先撞 §3 的 20/10s
突发规则，根本测不到每分钟阈值）。
