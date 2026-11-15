# SwarmLabs 项目级 API Key 政策

> 版本 2026-09-16 · 适用：通过标准 HTTP API 与 SwarmLabs 对接的外部项目（如机器人项目 `roboparts`）。

## 1. Key 格式

```
sk-swlm-<project>-<16 hex>
```

- `sk-swlm` 固定前缀（SwarmLabs）
- `<project>` 项目标识（如 `rbprts` 占位，确认后改为正式名）
- `<16 hex>` `secrets.token_hex(8)` 生成

当前已签发（演示用）：
```
sk-swlm-rbprts-067998a515b2aaee
```

## 2. 鉴权模型（可选 key + 审计）

- **端点默认仍公开**：GEO 漏斗与免费公开验证不受影响，无 key 也能读 `/v3/gate` `/v3/anchors` `/v3/benchmark`。
- **携带有效 key**：计入配额 + 写入运行时审计账本；响应头带回 `X-Partner` 标识。
- **携带无效 key**：返回 `401 {error:"unauthorized", reason:"invalid_key"}`。
- **缺失 key**：匿名放行（不破坏现有公开访问与 `deploy_pages.py` 复验）。

## 3. 开放端点（scope）

| 端点 | 方法 | 说明 |
|---|---|---|
| `/v3/gate` `/v3/gate/{key}` | GET | 验证闸门账本（62 场景策略 + 阻断清单） |
| `/v3/anchors` `/v3/anchors/{key}` | GET | 湿实验锚定（oracle 是否符合真实生物学） |
| `/v3/benchmark` `/v3/benchmark/{key}` | GET | 公开留出基准集（3110 点 / 62 场景） |
| `/v3/verify` | POST | 任意预测验证（建议也带 key 以便审计） |

**不开放**：引擎训练 API（bus factor=1，不承诺 SLA）。

## 4. 配额

| 项目 | per-minute | per-day |
|---|---|---|
| `roboparts`（默认） | 100 | 10,000 |

超限返回 `401 reason:"rate_min_exceeded"/"rate_day_exceeded"`。

## 5. 审计

- 运行时内存账本 `__partnerUsage`（部署重启后重置；生产应落 KV/D1）。
- 合作方自查：`GET /v3/partner/usage`（需带本项目的 key）。

## 6. 轮换 / 撤销

- 轮换：在 `frontend_fixed/functions/api/[[path]].js` 的 `PROJECT_API_KEYS` 中新增新 key、保留旧 key 宽限期后删除。
- 撤销：从 `PROJECT_API_KEYS` 删除对应条目 → 该 key 立即 `401`。

## 7. 诚实边界（必读）

- 本 key 是**互操作演示凭证**，不是商业授权；正式授权走 `license.swarmlabs.tools`。
- SwarmLabs 当前 62 场景 **5 REFUTED 硬阻断**、**bus factor=1**、**pre-revenue**、**GH stars=0** —— 不承诺 SLA。
- 神经解码 / 软体接触动力学在 SwarmLabs 内**尚无对应场景**，短期靠「对方回传数据 → 扩充锚点」渐进接入。
