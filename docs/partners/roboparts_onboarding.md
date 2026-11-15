# RoboParts × SwarmLabs 互操作接入指南

> 签发日期：2026-09-15 · 凭证类型：互操作演示 key（非商业授权）
> 适用：机器人项目（占位标识 `rbprts` / 正式 `roboparts`）通过标准 HTTP API 调用 SwarmLabs 的验证闸门、湿实验锚定、公开基准数据。

## 1. 凭证（请妥善保管，勿提交到 git）

```
Key:       sk-swlm-rbprts-a1b2c3d4e5f6a7b8
Project:   roboparts
Base URL:  https://swarmlabs.tools
```

- 携带有效 key：计入配额 + 响应头回带 `X-Partner: roboparts` + `X-RateLimit-Limit/Remaining/Reset`。
- 携带**无效** key：
  - `/api/health` → **200** + `key_ok:false` + `error:"key_not_found"`（存活探针不该被 401 挡住，见 spec §8 第 2 项）；
  - 其余 partner 端点 → **401** `{error:"key_not_found"}`，且 message 区分「没带 header」与「header 里的 key 没登记」。
- **缺失** key：`/api/health` 与 `v3/*` 匿名放行（公开端点仍可无 key 读取，不影响 GEO 漏斗与免费验证）；其余 partner 端点 → 401。

## 2. 开放端点（scope）

| 端点 | 方法 | 说明 | 需 key |
|---|---|---|---|
| `/v3/gate` | GET | 验证闸门账本（62 场景策略 + 5 REFUTED 硬阻断清单） | 可选 |
| `/v3/gate/{key}` | GET | 单个场景的闸门判定（PASS/MARG/REFUTED + 阈值） | 可选 |
| `/v3/anchors` | GET | 湿实验锚定（oracle 是否符合真实生物学；10 可锚定场景） | 可选 |
| `/v3/anchors/{key}` | GET | 单个场景的锚定结论（AGREE/NEAR/CONTRADICTED/UNANCHORED） | 可选 |
| `/v3/benchmark` | GET | 公开留出基准集（3110 点 / 62 场景） | 可选 |
| `/v3/benchmark/{key}` | GET | 单个场景的留出基准数据 | 可选 |
| `/v3/partner/usage` | GET | **本项目**配额/用量自查（必须带本 key） | **必须** |

> 注：`/v3/verify`（POST 任意预测验证）为公开端点，本 key 的 scope 未显式列入，调用前请先带 key 试 `partner/usage` 确认；正式验证接入走 `license.swarmlabs.tools`。
> **不开放**：引擎训练 API（bus factor=1，不承诺 SLA）。

## 3. 配额

以 RoboParts 侧 spec §3 为准（**SwarmLabs 侧的实现就是照这个口径做的**）：

| 项目 | 阈值 | 说明 |
|---|---|---|
| 每分钟 | 60 请求 | 固定 60s 窗口 |
| 每日 | 10,000 请求 | 单来源 |
| 突发 | 20 请求/10s | 防脚本循环误用 |

超限返回 **429**（不是 401）：

```json
{"error":"rate_limit_exceeded","reason":"rate_min_exceeded","retry_after_seconds":32,"request_id":"..."}
```

并带 `Retry-After` 头。`reason` 取值 `rate_min_exceeded` / `rate_day_exceeded` / `burst_10s_exceeded`，便于定位是哪一档被触发。

> **两个容易踩的点，写在这里免得再次漂移：**
> 1. `X-RateLimit-Limit` 的值（60）与**真正执行限流的阈值必须是同一个数**。曾经实现里 Limit 写死 60 而实际卡 30，头是齐的但在说谎 —— 客户端按 60 做预算，第 31 次被 429。
> 2. `/api/health` 按 spec §2.1 带 `Cache-Control: s-maxage=60`，因此它上面那三个限流头**最多可能滞后 60 秒**。要做精确的限流预算，请读数据端点（`/api/entities/search` 等，未被缓存）上的头，或直接查 `/v3/partner/usage`。

## 4. 调用示例

### 4.1 只读闸门账本
```bash
curl -H "x-api-key: sk-swlm-rbprts-a1b2c3d4e5f6a7b8" \
     https://swarmlabs.tools/v3/gate
```

### 4.2 单场景闸门判定（如 microbio_monod）
```bash
curl -H "x-api-key: sk-swlm-rbprts-a1b2c3d4e5f6a7b8" \
     https://swarmlabs.tools/v3/gate/microbio_monod
```

### 4.3 湿实验锚定（验证 oracle 是否符合真实生物学）
```bash
curl -H "x-api-key: sk-swlm-rbprts-a1b2c3d4e5f6a7b8" \
     https://swarmlabs.tools/v3/anchors
```

### 4.4 公开留出基准集
```bash
curl -H "x-api-key: sk-swlm-rbprts-a1b2c3d4e5f6a7b8" \
     https://swarmlabs.tools/v3/benchmark
```

### 4.5 配额自查（必须带 key）
```bash
curl -H "x-api-key: sk-swlm-rbprts-a1b2c3d4e5f6a7b8" \
     https://swarmlabs.tools/v3/partner/usage
# 返回（真实形状，2026-09-17 实测）：
# {"project":"roboparts","scope":[...],
#  "rate_per_min":60,"rate_per_day":10000,"burst_10s":20,
#  "used_min":3,"used_day":3,"remaining_min":57,"reset":1789636015,
#  "scope_basis":"per-source-IP (this deployment has no per-key counter yet)",
#  "time":"2026-09-17T17:16:00.000+08:00"}
```

### 4.6 Python 封装
```python
import requests

BASE = "https://swarmlabs.tools"
KEY  = "sk-swlm-rbprts-a1b2c3d4e5f6a7b8"
H    = {"x-api-key": KEY}

def swarm_get(path):
    r = requests.get(f"{BASE}{path}", headers=H, timeout=20)
    r.raise_for_status()
    return r.json()

gate     = swarm_get("/v3/gate")          # 全量闸门账本
anchors  = swarm_get("/v3/anchors")       # 湿实验锚定
bench    = swarm_get("/v3/benchmark")     # 公开基准
usage    = swarm_get("/v3/partner/usage") # 本 key 用量
```

## 5. 典型用法（机器人项目侧）

1. **上线前合规闸门**：在 RoboParts 投放任何"仿真/预测"结论前，调用 `/v3/gate/{scenario}` 确认是否落入 REFUTED 硬阻断；落入则前端标注"未经验证"。
2. **生物学可信度锚定**：调用 `/v3/anchors/{scenario}` 读取 AGREE/NEAR/CONTRADICTED，把"CONTRADICTED"场景从对外宣称中剔除。
3. **对照基准自测**：拉取 `/v3/benchmark/{scenario}` 的留出点，用 RoboParts 自研模型跑一遍，与 SwarmLabs GP+UQ 结果做横向对比（独立验证，非照搬）。

## 6. 诚实边界（必读）

- 本 key 是**互操作演示凭证**，不是商业授权；正式授权走 `license.swarmlabs.tools`。
- SwarmLabs 当前 **62 场景 / 5 REFUTED 硬阻断**、**bus factor=1**、**pre-revenue**、**GH stars=0** —— **不承诺 SLA**。
- 神经解码 / 软体接触动力学在 SwarmLabs 内**尚无对应场景**，短期靠「RoboParts 回传数据 → 扩充锚点」渐进接入。
- **配额计量是按来源 IP 的，不是按 key 的**（KV 里只有 `ratelimit:<ip>` / `ratelimit_day:<ip>`）。`/v3/partner/usage` 会用 `scope_basis` 明说这一点。RoboParts 目前从单一出口 IP 调用，两者等价；一旦变成多 IP，这个字段会先把差异暴露出来，而不是悄悄算错。
- 数据端点对**读失败**回 `503 service_unavailable`（带 `reason` / `tried` / `retryable`），不回 500。"读不到"不等于"不存在"，也不等于"数据源坏了" —— 这两件事在响应里必须能分开。

## 7. 轮换 / 撤销

- 轮换：在 Cloudflare Pages 项目的环境变量 `PROJECT_API_KEYS`（逗号分隔）里加新 key，保留旧 key 宽限期后再删掉。
  > 注意：`frontend_fixed/functions/api/[[path]].js` 里那个 `'sk-swlm-rbprts-…'` 只是**代码内兜底默认值**，改它等于把凭证写进被 git 跟踪的源码。生产请走 Pages 环境变量。
- 撤销：从 `PROJECT_API_KEYS` 删掉对应条目 → 该 key 立即 401。
- 部署（**唯一入口**）：`python scripts/deploy_pages.py` —— 它会逐文件字节比对部署完整性，缺件自动重部署，仍不完整则非零退出。
  **不要裸跑 `wrangler pages deploy`，也不要让 CI 部署**：CI 的树里没有 git-ignored 的 `reports/` 生成物，部署出来的是残缺站点（实测 62/348），详见 `.github/workflows/deploy.yml` 头部注释。
- 任何变更须推送 `lm203688/swarmlabs` main 并通过 `scripts/verify_main_artifacts.py` 复验；接口契约用 `python scripts/verify_partner_spec.py` 按 spec §8 清单验收。
