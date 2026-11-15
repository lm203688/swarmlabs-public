# SwarmLabs → RoboParts 外部数据源接入规范 v1

**发给**：SwarmLabs 侧（负责 `https://swarmlabs.tools` API 后端与 key 发放）
**来自**：RoboParts（`https://roboparts.cc`，项目独立性硬约束——只走 API，零源文件共享）
**日期**：2026-09-17
**版本**：v1（最小可用面）

---

## 0. 背景：为什么这份 spec 现在写

上一版 key `sk-swlm-rbprts-067998a515b2aaee` 已发放，但 RoboParts 侧实测穷举调用全部 401：
- **Header 变体 8 种**（`x-api-key` / `X-Api-Key` / `X-API-KEY` / `Authorization: Bearer` / `Authorization: <plain>` / `api-key` / `key` / `token`）
- **Query 变体 5 种**（`?x-api-key=` / `?api_key=` / `?api-key=` / `?key=` / `?token=`）
- **路径变体 13 个**（`/api/stats`、`/api/v1/stats`、`/v1/stats`、`/api/knowledge-graph`、`/api/entities`、`/api/trends`、`/api/bridge`、`/api/papers`、`/api/v1/entities`、`/api/v1/knowledge-graph`、`/api/labs`、`/api/lab`、`/api/insights`）

响应体完全一致：`{"error":"Authentication required","hint":"Provide x-api-key header. Get your key at swarmlabs.tools/login"}`。

诊断：
1. **可能 A**——key 只写进发放日志，没进 KV / secret store，认证代码永远查不到；
2. **可能 B**——auth 中间件读的实际 header 名与 401 提示文本不符（提示是模板硬编码的误导性内容）；
3. **可能 C**——key 认了但项目白名单未放行 `roboparts`。

顺带：**`https://license.swarmlabs.tools`** Cloudflare Tunnel 未接通（CONNECT 502），若 key 发放流程走该子域，需一并修好。

请**先修上述三点**，再按下面 v1 规范补齐端点。

---

## 1. 认证（Authentication）

**唯一方式**：请求头 `x-api-key: <KEY>`（大小写不敏感）。

- ❌ 不支持 `Authorization: Bearer`、`api-key`、`key`、`token` 任何别名——避免多套机制并存导致误判。
- ❌ 不支持 query string 传 key——避免被日志、referer、CDN cache 泄露。
- ✅ 401 响应体必须**明确区分「key 不存在」和「key 存在但无权限」**：
  ```json
  {"error":"key_not_found","hint":"key sk-swlm-*** not registered"}
  ```
  ```json
  {"error":"permission_denied","project":"roboparts","reason":"endpoint_not_allowed","allowed":["/api/health","/api/entities/search"]}
  ```
  现在两码同回一个 hint，是排障盲区。

---

## 2. 需要提供的端点（v1，6 个）

### 2.1 `GET /api/health` —— 无鉴权、可公开
RoboParts 侧用这个做**存活探测 + key 有效性探针**（key 放 header 里，服务端只回 `key_ok:true/false`，不回 key 明文）。

**请求**
```
GET https://swarmlabs.tools/api/health
x-api-key: <KEY>
```

**响应 200**
```json
{
  "status": "ok",
  "time": "2026-09-17T09:24:00+08:00",
  "version": "v1",
  "key_ok": true,
  "project": "roboparts",
  "entity_count": 47566
}
```

**约束**
- 无 key 也应 200，只是 `key_ok:false`、`project:null`（存活探针不该被 401 挡住）；
- 缓存 `Cache-Control: s-maxage=60`；
- p99 响应 < 500ms。

### 2.2 `GET /api/entities/search` —— 核心查询接口
RoboParts 侧用它做**跨域情报桥接**：把 RoboParts 机械兼容的关键词丢过去，看 SwarmLabs 知识图谱里有没有对应的趋势/空白/桥接实体。

**请求**
```
GET https://swarmlabs.tools/api/entities/search?q=<keyword>&kind=<filter>&limit=<n>
x-api-key: <KEY>
```

**参数**
- `q`（必填，1–100 字符，URL-encoded）：关键词，走标题/摘要/标签的复合检索
- `kind`（可选，枚举）：`paper` | `method` | `dataset` | `trend` | `gap` | `bridge` | `insight`；缺省=不限
- `limit`（可选，1–50，默认 20）
- `since`（可选，ISO-8601 日期，只返回该日期后新增/更新的实体）

**响应 200**
```json
{
  "query": "neuromorphic controller",
  "total": 42,
  "returned": 20,
  "results": [
    {
      "id": "TREND-00342",
      "kind": "trend",
      "title": "Spike-based neuromorphic control for legged locomotion",
      "summary": "...",
      "confidence": 0.87,
      "sources": ["https://arxiv.org/abs/xxxx.xxxxx", "..."],
      "updated_at": "2026-09-12T00:00:00+08:00",
      "bridges_to": ["GAP-0118", "METHOD-00902"]
    }
  ]
}
```

**约束**
- 结果按 confidence 降序、updated_at 次序；
- `bridges_to` 必须只放**真实存在于知识图谱**的 ID（否则 RoboParts 侧无法跟进）；
- 空结果返回 `total:0, results:[]`（不要 404）。

### 2.3 `GET /api/bridge` —— 跨域桥接（RoboParts 最需要的）
RoboParts 的机械兼容 ↔ SwarmLabs 的微生物/材料/神经科学，这条桥接接口是核心消费场景。

**请求**
```
GET https://swarmlabs.tools/api/bridge?from=<domain_a>&to=<domain_b>&limit=<n>
x-api-key: <KEY>
```

**参数**
- `from`（必填）：RoboParts 侧的领域标签，例如 `robotic-actuator`、`gripper-interface`
- `to`（可选）：目标领域，例如 `material-bio`、`neuro-compute`；缺省=跨所有域
- `limit`（1–30，默认 10）

**响应 200**
```json
{
  "from": "robotic-actuator",
  "to": "material-bio",
  "bridges": [
    {
      "id": "BRIDGE-00421",
      "from_entity": "TREND-00342",
      "to_entity": "METHOD-00902",
      "relation": "complementary",
      "signal_strength": 0.74,
      "signal_source": "co-author-network | topic-model | citation-bridge",
      "created_at": "2026-09-10T00:00:00+08:00"
    }
  ]
}
```

### 2.4 `GET /api/gaps` —— 研究空白（RoboParts 情报层消费）
RoboParts 侧需要按领域拉空白列表，做「RoboParts 零件 × 前沿空白」的情报 enrichment。

**请求**
```
GET https://swarmlabs.tools/api/gaps?domain=<tag>&status=open&limit=<n>
x-api-key: <KEY>
```

**响应 200**
```json
{
  "domain": "neuro-compute",
  "gaps": [
    {
      "id": "GAP-0118",
      "title": "...",
      "description": "...",
      "open_since": "2026-05-01",
      "attempted_by": 3,
      "signal_strength": 0.62
    }
  ]
}
```

### 2.5 `GET /api/papers` —— 文献详情（可选，若你已有实体存储）
按 ID 或 arxiv 号回单条实体完整记录。RoboParts 侧用作**引用溯源**。

**请求**
```
GET https://swarmlabs.tools/api/papers/<id_or_arxiv_id>
x-api-key: <KEY>
```

**响应 200**：完整实体 JSON，字段与 2.2 的 `results[i]` 对齐（外加全文摘要、作者列表、领域标签）。

### 2.6 `GET /api/trends` —— 趋势聚合（RoboParts 情报层可选消费）
按领域标签或时间窗口聚合趋势条目。

**请求**
```
GET https://swarmlabs.tools/api/trends?domain=<tag>&since=<date>&limit=<n>
x-api-key: <KEY>
```

**响应 200**：趋势列表，每条含 `id, title, momentum (float), entity_count, top_bridges`。

---

## 3. 限流（Rate Limits）

| 维度 | 阈值 | 说明 |
|---|---|---|
| 每分钟 | 60 请求 | 覆盖 RoboParts 部署周期性的情报刷新（`scripts/build_derived_features.py` 每次 ~5 请求） |
| 每日 | 10,000 请求 | 单 key 上限，覆盖 neurorobotics 情报层每日全量拉取 |
| 突发 | 20 请求/10s | 防误用（脚本循环）|

**响应头必须包含**：
```
X-RateLimit-Limit: 60
X-RateLimit-Remaining: 55
X-RateLimit-Reset: 1729142400   # Unix timestamp
```

**超限响应 429**：
```json
{"error":"rate_limit_exceeded","retry_after_seconds":32}
```
配合 `Retry-After` header。

---

## 4. 错误码（Error Codes）

统一响应体：
```json
{"error":"<code>","message":"<human-readable>","request_id":"<uuid>"}
```

| HTTP | code | 含义 |
|---|---|---|
| 400 | `bad_request` | 参数缺失或格式错 |
| 401 | `key_not_found` | key 不存在于后端存储 |
| 401 | `key_revoked` | key 已存在但已吊销 |
| 403 | `permission_denied` | key 有效但无权访问该端点/项目 |
| 404 | `not_found` | 端点不存在或实体 ID 不存在 |
| 429 | `rate_limit_exceeded` | 限流触发 |
| 500 | `internal_error` | 服务端异常，`request_id` 用于对账 |
| 503 | `service_unavailable` | 依赖服务不可用（DB/KV down） |

**硬约束**：`request_id` 必须每次都有——RoboParts 侧的排障脚本会用它做日志对账。

---

## 5. Key 发放（Key Issuance）

请确保发放流程满足：

1. **写入后立即可用**——发放脚本执行完，`GET /api/health` 应立刻返回 `key_ok:true`（不是异步同步、不是等 KV 缓存刷新）；
2. **明确的项目绑定**——`project=roboparts`，不要默认放行所有端点（未来可按端点权限精细化）；
3. **明确的吊销通道**——至少支持「按 key 吊销」和「按 project 吊销」两种粒度；
4. **`license.swarmlabs.tools` Tunnel**——若发放走该子域，`https://license.swarmlabs.tools` 必须先修好（当前 CONNECT 502）；
5. **格式**：保持 `sk-swlm-rbprts-<16 hex>` 这种格式即可（RoboParts 侧不做格式校验，只透传）。

---

## 6. 响应契约（Response Contract）

RoboParts 侧的接入代码依赖以下稳定契约：

- **Content-Type**：`application/json; charset=utf-8`
- **字符编码**：UTF-8（中文/emoji 不转义）
- **时区**：所有时间戳用 `+08:00` 显式偏移（RoboParts 侧的 `.env` 与部署环境都在 CST）
- **`Cache-Control`**：`s-maxage=3600`（1 小时，减少 RoboParts 侧的重复拉取）
- **幂等性**：GET 请求必须幂等，重复调用同一 URL 必须返回完全一致的 body（RoboParts 侧会用 body sha256 做新鲜度锚定，防止漂移快照病）
- **字段命名**：snake_case（与 RoboParts 侧的 JSON 契约一致）
- **空结果**：`{ "results": [] }` 而非 `null` 或 `404`

---

## 7. RoboParts 侧已就绪的接入点

修好后，RoboParts 侧**无需改代码**即可开始消费：

- `.env.local` 已写入 `SWARMLABS_API_KEY` 与 `SWARMLABS_BASE_URL`（已在 `.gitignore` 白名单外，不会入库）；
- 首个消费者规划：`scripts/build_neurorobotics.py` 增一段 enrichment——拉 `/api/bridge?from=robotic-actuator&to=neuro-compute` 与 `/api/gaps?domain=neuro-compute`，把 SwarmLabs 的空中标注进 `api/neurorobotics.json` 的 `gap_map`（走 L1.95 独立闸门验证新鲜度）。
- 独立铁律保障：RoboParts 侧**只通过 HTTPS 访问 SwarmLabs 的公开端点**，不共享源码、不克隆仓库、不读 SwarmLabs 的 KV/DB。

---

## 8. 验收清单（SwarmLabs 侧修完自查）

- [ ] `GET /api/health` 无 key → 200 + `key_ok:false`；带有效 key → `key_ok:true`
- [ ] `GET /api/health` 带不存在的 key → 200 + `key_ok:false` + `error:key_not_found`（不是 401）
- [ ] `GET /api/entities/search?q=...` 带有效 key → 200 + 结果列表
- [ ] 同一 URL 重复调用 10 次，body sha256 恒定（幂等）
- [ ] 429 触发时 header 有 `Retry-After` + body 有 `retry_after_seconds`
- [ ] 所有错误响应含 `request_id`
- [ ] `x-api-key` header 大小写不敏感
- [ ] 每分钟 61 次调用触发 429（不是每分钟 62、不是 59）
- [ ] `https://license.swarmlabs.tools` CONNECT 不再 502
- [ ] 401 hint 文本与实际读的 header 名一致（现在 hint 与实现不符是排障盲区）

---

**联络**：新 key 生成后请回复给厉兴（QQ 1786240725175），我会立刻做端到端验证并在 RoboParts 侧接入第一个消费者。
