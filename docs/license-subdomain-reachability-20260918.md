# license/api 子域不可达：诊断与结论（2026-09-18）

## 一句话结论

`license.swarmlabs.tools` 与 `api.swarmlabs.tools` **不是「国内被墙」，而是 CNAME 指向了
一个全球都不存在的目标**（`error code: 1042`）。修法不是改 DNS 代理开关，而是
**给两个 Worker 重新建立入口（Worker Custom Domain）**，需要一枚带 Workers 权限的 token。

---

## 1. 起因

记忆与多份文档长期把此事记为「灰云 CNAME → workers.dev，国内超时不可达（网络硬约束）」，
并列为 P0。2026-09-18 选择「方案 A：改为橙云（proxied=true）」，执行后实测证伪，遂深挖。

## 2. 证据链（全部为本机一手实测）

### 2.1 改动前基线（同一台机器、同一时刻）

| 端点 | 结果 |
|---|---|
| `https://swarmlabs.tools/api/v2/payment/plans` | **http=200**，0.77s，IPv6 2606:4700:…（橙云） |
| `https://license.swarmlabs.tools/` | http=000，**20.0s 超时**（灰云） |
| `https://api.swarmlabs.tools/` | http=000，**20.0s 超时**（灰云） |

干净的对照：同为 CNAME 到 CF 平台域名，**橙云可达、灰云不可达**。

### 2.2 方案 A 实测（橙云）→ 522

改 `proxied=true` 后（DNS 已生效，阿里 DoH 确认解析到 CF anycast `104.21.x`）：

```
--resolve license.swarmlabs.tools:443:104.21.89.118
HTTP/1.1 522 <none>
CF-RAY: a3cf643efaa9be3d-AMS
```

`api.swarmlabs.tools` 同样 522（`CF-RAY …-LHR`）。

**根因**：CF 边缘不允许把橙云回源指向自己的 `*.workers.dev`（同网回环），源站永不可达。
→ **方案 A 技术不成立，已回滚**（DNS 逐字段还原为 `proxied=false, ttl=300`）。

### 2.3 关键对照组：证明测量方法有效

```
swarmlabs-dnh.pages.dev            → 301  ✅（CF 平台子域在 80 端口正常重定向）
demo-video.swarmlabs-dnh.pages.dev → 301  ✅
```

→ HTTP/80 无 SNI 探测是**有效**的，404/1042 不是测量假象。

### 2.4 决定性证据：目标本身就是死的

```
genetech-license.61960005.workers.dev    → HTTP 404  body: error code: 1042
genetech-api-guard.61960005.workers.dev  → HTTP 404  body: error code: 1042
oraclemind-manifold-proxy.61960005.workers.dev
                                         → HTTP 404  body: {"message":"The requested route
                                           '/' does not exist. … refer to app.ts"}
```

第三条是**同账户**的另一个 Worker，它返回的是 **Worker 自己的 JSON**——
说明账户的 `workers.dev` 子域健康、且该 Worker 有入口。

**注意——此处的 worker 名是错的，见 §8（09-19 更正）。** 源码里许可证 Worker 的真实
`name` 是 **`unified-license`**（见 `知识引擎14站/unified-license/wrangler.toml`），
而 CNAME 指向的 `genetech-license.61960005.workers.dev` 是一个**从未存在过**的主机名
（故 1042）。

**修正后的推论**：CNAME 指向了**陈旧的错误主机名**，而非"worker 被整体删除"。
09-19 用真实 worker 名复探（见 §8），证明两个 Worker **都已部署并绑定**到各自
`*.workers.dev` 主机名（返回 404 而非 1042）。因此真实可用性：workers.dev 海外可达、
但 `*.workers.dev` **国内 TCP 层被墙**——与根域橙云可达形成对照。修法不变：
**绑 Worker Custom Domain（自动橙云 + 证书）**，但 service 名须用 `unified-license`。

## 3. 通行方案逐条实测结论

| 方案 | 结论 | 依据 |
|---|---|---|
| A 橙云代理到 workers.dev | ❌ 522 | §2.2，CF 禁止回源自身 workers.dev |
| B Worker Route（zone 级） | ⛔ 无权限 | `POST /zones/{z}/workers/routes` → `Authentication error` |
| C Worker Custom Domain | ⛔ 无权限 | `GET /accounts/{a}/workers/domains` → `Authentication error` |
| D Pages Functions 代理到 workers.dev | ❌ 撞同一死链 | §2.4，目标 1042；探针未部署即作废，文件已清理 |

本机 token（`cfut_…` in `~/.workbuddy/secrets/swarmlabs_cf.json`）实测权限边界：

- ✅ Zone Read / **DNS Edit**（能建改 DNS 记录）
- ✅ **Pages 完整权限**（可列项目、可见自定义域）
- ❌ Workers Scripts / Routes / Domains（全部 `Authentication error`）

## 4. 唯一正解与所需动作

**方案 E：Worker Custom Domain**（CF 官方推荐，自动创建橙云 DNS + 证书，不走回源）：

```
POST /accounts/{account_id}/workers/domains
{
  "hostname": "license.swarmlabs.tools",
  "service": "unified-license",          ← 真实 worker 名（非 CNAME 里的 genetech-license）
  "environment": "production",
  "zone_id": "3d4ae45b030c41c27bc96279e774a763"
}
```

`api.swarmlabs.tools` → `genetech-api-guard` 同理。

**前置条件（用户侧，硬阻塞）**：一枚带 Workers 权限的 API token。
在 https://dash.cloudflare.com/profile/api-tokens 创建 Custom token，
权限至少包含：

- `Account` → `Workers Scripts` → **Edit**（读脚本 + 部署 + 管 routes/domains 都归此组）
- 建议同时给 `Account` → `Workers KV Storage` → Edit（仅当需重部署且 KV 缺失时用到）

账户限定：`61960005@qq.com`（Account ID `8162aa3b2241c132e43a81f526d7f758`）。

**执行前置清理**：绑定 Custom Domain 时 CF 要求该 hostname 无冲突记录，
需先删除现有两条指向死目标的 CNAME（本文件未执行，因其为独立变更）。

> ⚠️ **09-19 实测**：用户提供的第二枚 token `cfut_mYJegFrA8…` 经 `token/verify` 有效，
> 但实测权限边界与旧 token **完全相同**——Zone + DNS + Pages 全可用，
> **Workers Scripts / Domains 仍无权限**：
> - `GET /accounts/{a}/workers/scripts/{name}` → `No access to the specified resource`
> - `POST /accounts/{a}/workers/domains` → `10405 Method not allowed for this authentication scheme`
> 故该 token **仍无法执行方案 E**。须按上方权限重新生成。

## 5. 本次已完成 / 未做

**已完成**
- 方案 A 实测证伪（522 硬证据）
- DNS 回滚，逐字段还原（`proxied=false, ttl=300`），备份见
  `.workbuddy/backups/dns_cname_backup_20260918.json`
- 两个子域的引用面盘点（详见 §6）
- 权限边界实测、五条方案逐条判定
- 探针文件已删除，未产生任何生产变更

**未做（需授权或需 token）**
- Custom Domain 绑定（缺 Workers 权限 token）
- 删除两条死链 CNAME（独立变更，待与绑定一并做）

## 6. 引用面盘点（决定「修它的收益是什么」）

- `api.swarmlabs.tools` —— **全仓代码零引用**（仅本次备份文件提及）
- `license.swarmlabs.tools` —— **代码零引用**，仅被 docs/spec 引用，且被多处记为
  **必然失败的门禁**：
  - `docs/partners/PARTNER_SPEC_COMPLIANCE_2026-09-17.md`：`[FAIL] §8.9 … DNS 不解析`
  - `docs/pricing/premium-tiers.md`：`license.swarmlabs.tools | DNS 不存在`
  - `scripts/verify_partner_spec.py`：`§8.9` 检查项

  ⇒ 修好后这些门禁才能转绿；否则应把 §8.9 降级为可选项，而不是留一个必然失败的检查。

**承载能力（决定「值不值得修」）**：`genetech-license`（源码 `知识引擎14站/unified-license/`）
是完整支付/许可证中心：`/api/license/{status,validate,redeem}`、
`/api/hupijiao/{order,create-order,callback}`、`/api/admin/issue`。
其中 callback 从请求 origin 推导（`worker.js:659`），CORS 白名单未命中时**回退 `*`**——
两条特性都意味着：一旦入口恢复，无需改代码即可在任意 hostname 下工作。

注：swarmlabs 自身支付**不受影响**——根域 `frontend_fixed/functions/api/[[path]].js`
内嵌独立的虎皮椒实现，且橙云可达、国内正常。受影响的只有 genetech 14 站的许可证中心。

## 8. 09-19 二次核查：Worker 其实已部署（关键翻转）

用户再次要求"做掉"并给出新 token `cfut_mYJegFrA8…`。重新核查时改用**源码真实
worker 名**探测，发现此前判断再度偏差：

### 8.1 真实 worker 名 vs CNAME 里的名

| 项 | 值 |
|---|---|
| 许可证 Worker 源码 `name` | **`unified-license`**（`知识引擎14站/unified-license/wrangler.toml`，`account_id=8162aa3b…`） |
| CNAME `license.swarmlabs.tools` 指向 | `genetech-license.61960005.workers.dev`（**陈旧错误名，该 worker 从未存在**） |
| api-guard Worker `name` | `genetech-api-guard`（与 CNAME 指向**一致**，正确） |

### 8.2 用真实名复探（HTTP/80 经 CF 边缘，对照组法）

```
unified-license.61960005.workers.dev     → HTTP 404 (空 body)   ← 已绑定/已部署（非 1042）
genetech-api-guard.61960005.workers.dev  → HTTP 404 (空 body)   ← 已绑定/已部署（非 1042）
genetech-license.61960005.workers.dev    → HTTP 404 body:1042   ← 该名 worker 不存在
```

**关键区分**：`1042` = 该主机名**无任何 Worker 绑定**；`404`（无 1042 body）= Worker
**已绑定并执行**，只是请求路径无路由。因此两个 Worker **都已部署**，此前"入口被整体
移除"的判断不成立——真正的问题是 **CNAME 写错了一个主机名**（`genetech-license` 而非
`unified-license`）。

### 8.3 含义

- **无需重部署**：绑 Custom Domain 到正确 service 名即可立刻生效。
- api-guard 源码 `wrangler.toml` 的 KV id 仍是占位符 `REPLACE_WITH_YOUR_KV_NAMESPACE_ID`，
  但其 `*.workers.dev` 已返回 404（绑定态），说明**线上部署版用的是真实 KV id**
  （out-of-band 注入，未提交回源码）。若日后需从源码重部署，须先补真实 KV id。
- 绑定后务必用真实端点（`/api/license/status`、`/health`）复验；若仍 404，说明线上
  部署版内容陈旧，需 `Workers Scripts:Edit` 重部署——届时同样受本 token 缺权限制约。

## 7. 三次自我纠正（留痕，避免下次重犯）

1. 初判「国内被墙 = 网络硬约束」→ 错。根域同为 CF 平台域名却可达，说明是代理开关问题。
2. 改橙云后判「海外原本可用、被我打成 522」→ 半错。海外同样不可用（1042），改动
   并未造成可用性回归，但**结论仍应是回滚**（522 比 1042 更难诊断且无意义）。
3. 设计「Pages Functions 代理到 workers.dev」→ 未部署即在 §2.4 证据下作废。

**方法论**：三态纪律——「够不到裁决者」≠「裁决说否」。本次正是靠对照组
（`pages.dev` 301 / `oraclemind` Worker 自身 JSON）把「连不上」与「不存在」分开。

---

## 9. 四度校正（09-19 下午）：**§8.2 的 404 结论不成立**

### 9.1 错在哪 —— 探测的 vantage point 被污染

§8.2 那组「404 = 已部署」的判据，是**从本机**用 `--resolve <host>:80:<CF IP>` 发
**明文 HTTP** 探到的。而本机常驻一枚 TLS 拦截代理，它会按 SNI/Host 干预请求——
在**没有 TLS 可拦截**的 80 端口，它返回的是自己的 404。于是我把
**代理的 404** 读成了「Worker 已绑定」。

> **教训（比结论重要）**：`404` 与 `1042` 的区分只有在**同一 vantage point**下才成立。
> 判断境外可达性，本机测量一律作废——必须走 CF 边缘自身或第三方通道。

### 9.2 重做实验：从 CF 自己的边缘取证

在 `swarmlabs` Pages 项目上部署一枚探针 Function 到**预览分支**（`egress-probe`，
零生产影响），由它发起 fetch。回波来自 CF 边缘 `CF-RAY: …-SJC`（圣何塞），
完全绕开本机网络：

| 目标 | 结果 |
|---|---|
| `unified-license.61960005.workers.dev/`、`/health`、`/api/license/status`、`/api/license/validate`、`/api/hupijiao/order` | **`error code: 1042`**（全部路径） |
| `genetech-api-guard.61960005.workers.dev/`、`/health` | **`error code: 1042`** |
| `genetech-license.61960005.workers.dev/` | **`error code: 1042`** |
| **对照组** `oraclemind-manifold-proxy.61960005.workers.dev/` | **404 + Worker 自身 JSON** ✅ |

⇒ 账户的 workers.dev 子域是健康的（对照组证明方法有效），**是这三个主机名没有
任何 Worker 绑定**。`unified-license` 并不比 `genetech-license` 更好——**两个都不存在**。
§8.2「正确名应为 unified-license」的结论**作废**。

结合源码 `unified-license/wrangler.toml` 写着 `workers_dev = true`，最合理的解释是：
**这两个 Worker 根本没有部署**（或部署后被删除）。

### 9.3 顺带证伪：实验 C1（橙云 + 正确主机名）

§6 那个 522 是对着**已死目标**测的，实验是混淆的。本次做干净对照：同时修正
主机名（`genetech-license` → `unified-license`）**并**开橙云，经 CF anycast IP 实测：

```
license.swarmlabs.tools  → 522   (0.54s)
api.swarmlabs.tools      → 522   (0.61s)
```

⇒ 「CF 禁止橙云回源自身 workers.dev」**成立**。已回滚，与基线**逐字段一致**
（快照 `.workbuddy/backups/dns_cname_before_C1.json`）。

### 9.4 顺带证伪：实验 C2（Pages Functions 代理）

先测权限：`POST /accounts/{a}/pages/projects/swarmlabs/domains` 用非法域名做探针，
返回 `8000015 This domain is reserved and unavailable`（**域名校验错，不是 auth 错**）
⇒ 现有 token **具备 Pages 自定义域写权限**。这条路径本可完全自服务，但
9.2 已证明**代理目标本身是死链**（1042），故不成立。探针与预览部署已删除
（本地目录 + `DELETE /pages/projects/swarmlabs/deployments/c2db5181?force=true`），
生产 23 个部署未受影响。

### 9.5 最终结论（当前定论）

| 路径 | 判定 |
|---|---|
| CNAME 改主机名（灰云） | ❌ 四个候选主机名全 1042 |
| CNAME + 橙云 | ❌ 522（回源禁令） |
| Pages Functions 代理 | ❌ 目标 1042，且前提不成立 |
| Worker Route | ⛔ 无权限（Authentication error） |
| **Worker Custom Domain + 重部署** | ✅ **唯一正解**，需 `Account:Workers Scripts:Edit` |

**执行前提（都必须 Workers 权限）**：
1. 核实脚本是否存在（`GET /accounts/{a}/workers/scripts`）
2. 不存在 → 从源码重部署：
   - `unified-license`：先 `wrangler secret put ADMIN_SECRET / HUPIJIAO_APP_ID / HUPIJIAO_APP_SECRET`（**本机无这些凭据，需用户提供**）
   - `genetech-api-guard`：KV id 仍是占位符，需补真实 namespace id
3. `POST /accounts/{a}/workers/domains` 绑 `license.swarmlabs.tools` / `api.swarmlabs.tools`
4. 绑前先删 zone 里那两条指向死目标的 CNAME（此步本机 token 可做）

### 9.6 自我纠正记录（累计四次）

1. 「国内被墙 = 网络硬约束」→ 错（根域同平台却可达）。
2. 「海外原本可用、被我打成 522」→ 半错（海外同样不可用）。
3. 「Pages 代理 workers.dev」→ 在 §2.4 证据下作废。
4. **「404 = 已部署，正确名是 unified-license」→ 错（本机 HTTP/80 探针被 TLS 代理污染）。
   09-19 由 CF 边缘 SJC 探针推翻：三个主机名全 1042。**

> 四次里有三次源于**同一个病根**：拿被污染的测量当证据。此后凡涉境外可达性，
> 一律以 CF 边缘/第三方通道为准，本机结果仅作参考。

---

## 十、已解决（2026-09-23 16:47，token 到位后一次通过）

> 上述「全球 0% 可达」与「unified-license 未部署」的结论**均已过期**。用户提供了
> `Edit Cloudflare Workers` 模板 token（存 `~/.workbuddy/secrets/cf_workers_token.json`），
> 实查后真实状态与 09-19 的推断不同，且修复比预想简单——**无需重部署**。

### 10.1 实查修正（09-19 推断 vs 09-23 实查）

| 09-19 推断 | 09-23 实查 |
|---|---|
| `unified-license` 根本没部署 | 名字确实不存在，但**同一套代码以 `genetech-license` 之名在 08-10 已部署**（34KB worker.js + hupijiao bundle 逐节 diff 与本地 Aug-14 源码功能一致），`ADMIN_SECRET` / `HUPIJIAO_APP_ID` / `HUPIJIAO_APP_SECRET` / KV `UNIFIED_LICENSES` **四项绑定齐全** |
| workers.dev 1042 = 无绑定 | 绑定一直在，只是 `workers_dev` 开关为 **false**（042 的真实根因） |
| license.swarmlabs.tools 无 Worker | **有**：09-20 起 zone route 把它指给了 `swarm-labs-gateway`（14 站 partner 数据网关，占错了位置，对无 key 请求一律 401） |
| 需删两条死 CNAME | CNAME 实际指向 `genetech-license.61960005.workers.dev`，非死目标 |

### 10.2 实际做的三步（全部走 API，可复算）

1. `POST .../workers/scripts/genetech-license/subdomain {"enabled":true}` — 恢复 wrangler.toml 声明的 workers_dev 入口（本机不可达 workers.dev，未做本机验证，不据此下结论）。
2. `DELETE` zone routes `license.swarmlabs.tools` / `license.swarmlabs.tools/*`（原 → swarm-labs-gateway）+ `POST` 新 route `license.swarmlabs.tools/* → genetech-license`。CNAME 保持不动——「橙云 CNAME + zone route」正是 api.swarmlabs.tools 已验证可行的同一链路。
3. 顺带给被摘下的 `swarm-labs-gateway` 开启 workers.dev，恢复其文档声明的部署目标（`swarm-labs-gateway.61960005.workers.dev`），不让 14 站 partner 网关变孤儿。

### 10.3 线上复验（本机，目标域名非 workers.dev，可信）

| 探测 | 结果 |
|---|---|
| `GET /health` | **200** `{"service":"genetech-unified-license","status":"ok","version":"2.0"}` —— 即 DEPLOY-SECRETS.md 的验收判据 |
| `GET /` | 同上（健康 JSON） |
| `POST /api/license/validate`（dummy GUX_） | 200 `{"valid":false,"error":"license_not_found"}` —— 真实业务逻辑 |
| `POST /api/hupijiao/create-order`（空 body） | **400** `invalid_plan` —— 支付端点可达且在做参数校验（刻意未发真实订单，不碰虎皮椒生产） |
| `OPTIONS` 预检（github.io origin） | 204 + 正确 CORS 头（未命中白名单回退 `*`，符合 worker.js 设计） |

### 10.4 仍未闭合的口子（非域名问题，留待决策）

- **swarmlabs `/v3/*` 的 `x-api-key` 与 GUX_ 体系未打通**：`[[path]].js` 用静态 `PROJECT_API_KEYS` 名单校验（401 文案仍是 "Contact the swarmlabs team"）。现在有 4 套密钥并存：`slb_`（partner 网关）/ `gtk_`（Pro 站点 key）/ `GUX_`（统一许可证）/ `PROJECT_API_KEYS`（静态 partner 名单）。`/v3` 要接受自助发放的 key，需用户拍板接哪套（建议 GUX_ 兑换出的 gtk_，在 `[[path]].js` 加一路远程校验或把 `PROJECT_API_KEYS` 改为 KV 动态名单）。
- swarmlabs 前端**零引用** license 子域（购买页在 14 站 GitHub Pages license.html；本地源 `.dev.vars` 内有虎皮椒凭据，勿入库）。
- 09-23 二次记录「license 实测 401 = fail-closed 生效」**口径作废**：401 是占错的 swarm-labs-gateway 在拒绝请求，不是许可证服务的 fail-closed。

### 10.5 追加发现（同日 17:30）：免费版 KV 全账户写配额已耗尽 → 两个 P0

路由修复后做端到端下单实测，`create-order` 稳定 `error code: 1101`（Worker 未捕获异常）。
用诊断部署（5 级 staging 探针 + 调用点 try/catch）定位到真因：

```
Error: KV put() limit exceeded for the day.   (CF 10048: account free usage limit)
```

**免费版 KV 全账户 1,000 写/天**（UTC 0 点重置），且直接 KV API 写 UNIFIED_LICENSES 与
主站注册写 SWARMLABS_KV_PROD 同样被拒——账户级共享，无命名空间豁免。

由此挖出两个此前无门禁能发现的真实缺陷：

| # | 缺陷 | 证据 | 处置 |
|---|---|---|---|
| P0-1 | **swarmlabs 主站注册假成功**：`kvSaveUser` 的 `catch { /* 静默降级 */ }` 吞掉写失败 → 返回 200「注册成功」，但 `user:<email>` 未落库 → 任何其他 isolate 登录 401 | 实测注册 `kv-quota-probe@swarmlabs.tools` 成功 → 立即登录 401 → 直接读 KV 该 key **不存在**（10009 not found） | 未修（见下） |
| P0-2 | **api-guard 每请求烧 1 次 KV 写**：免费层 + LLM 免费层限流均为 `PRO_KV.put(free:<ip>:<min>)`，api.swarmlabs.tools 自 09-20 上线后爬虫流量持续消耗配额，是每日 1,000 写的主要燃烧源 | apiguard.js line 127/148 | **已修**：限流改内存 Map（per-isolate），已部署并验证 health/root 200 |
| 修复 | license worker 的限流同样是每请求 1 次 KV 写 + 待支付订单写入**裸奔**（无 try/catch，配额拒绝即 1101） | 诊断栈 `KV put() limit exceeded at handleHupijiaoCreateOrder` | **已修**：限流改内存 + 订单写加守卫 + 回调支持从虎皮椒 `attach` 重建订单 + 签发失败返回非 success 触发官方 6 次重试；已部署并**端到端实测下单成功**（真实虎皮椒订单 `GUXMUDW1N6Y172TJ4K`，¥9.90 starter，返回真实微信二维码；刻意未支付） |

两处修复已同步回 14 站仓 ESM 源码（`unified-license/worker.js`、`api-guard/worker.js`，
语法校验通过），防止下次 wrangler deploy 回滚。

**当前剩余风险**：账户写配额今日已耗尽（UTC 明日 0 点重置 ≈ 北京时间 08:00），
重置前主站注册/登录仍会丢用户；api-guard 修复后消耗大幅下降，但若主站注册量上来，
1,000 写/天仍可能不够——**根治要么给 [[path]].js 的注册/登录做 fail-loud/重试，
要么上 Workers Paid（$5/月，KV 1M 写/天）**，属花钱决策，留给用户拍板。
测试订单 `GUXMUDW1N6Y172TJ4K` 未支付会自动过期，无资金影响。
