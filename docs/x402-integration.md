# SwarmLabs x402 支付通道实施方案（方案 A）

**日期**：2026-09-22
**背景**：AIsa（aisa.one）已产品化 AgentPay Guard（quote-first / per-request 三层限额 /
cost 无法封顶就不执行），x402 稳定币支付已成 agent 生态标配。本文件描述**方案 A**
的落地实现——**虎皮椒保留为国内主通道，x402 作为海外 agent 按需调用通道**。

## 现状

| 维度 | 当前状态 |
|---|---|
| 支付通道 | 虎皮椒（微信/支付宝），license.swarmlabs.tools 已就绪 |
| 定价 | Pro ¥39.9 / 终身 ¥199 |
| 后端 API | swarmlabs.tools/api/v3，OpenAI 兼容推理 + 47,566+ 结构化实体 |
| A2A/MCP 元数据 | `.well-known/agent-card.json` + `.well-known/mcp.json` 已发布 |
| x402 支持 | **本轮已加**：`scripts/x402/x402_facilitator.py`（12 项 selftest + 26 项单元测试全绿） |

## 三档方案对比（保留决策记录）

| 方案 | 改动 | 推荐 |
|---|---|---|
| A | 只加 x402 支付后端，虎皮椒保留主通道 | ✅ 本轮实现 |
| B | x402 主通道 + 虎皮椒兜底 | ❌ 中国大陆用户几乎无法使用 |
| C | 暂缓观察 AIsa 生态 | ❌ 失去早期布局 |

**为什么选 A**：
1. 中国大陆用户占 SwarmLabs 目标市场主体，x402 转化有限，虎皮椒必须保留
2. x402 让海外 agent 能通过稳定币按需调用（无信用卡/无跨境支付问题）
3. 与 AIsa 生态对接，为上架 OpenClaw/ClawHub 做准备
4. 定价模型不变，仅加一条支付后端，最小侵入

## 一、方案 A 的完整实现

### 1.1 定价对齐（¥ → USDC）

| Tier | 虎皮椒 ¥ | USDC | 有效期 | 汇率容差 |
|---|---|---|---|---|
| trial | ¥0.00 | $0.00 | 24 小时 | — |
| **pro** | **¥39.90** | **$5.50** | 720 小时（30 天） | $5.23 – $5.78 |
| **lifetime** | **¥199.00** | **$28.00** | 永久 | $26.60 – $29.40 |

汇率使用固定值（USD_PER_CNY = 0.14），生产环境替换为动态汇率 + 5% 容差带。

### 1.2 文件布局

```
scripts/x402/x402_facilitator.py      # x402 核心实现（343 行）
tests/test_x402.py                     # 26 项单元测试
docs/x402-integration.md               # 本文件
```

### 1.3 架构（对齐 x402 协议 v1）

```
client ──GET /api/v3/... ──► server
server ──verify api_key──► [401 if missing]
server ──if no license───► HTTP 402 {
  protocol: "x402/1",
  scheme: "exact",
  amount: "5.50",
  currency: "USDC",
  pay_to: "0xswarmlabs-tools-2026-primary",
  facilitator: "https://facilitator.x402.org/v1/verify",
  network: "base",
  resources: ["/api/v3/**"],
  expires_at: <unix_ts>,
  alternative_channels: [
    {name: "虎皮椒", url: "https://license.swarmlabs.tools", cny: "39.90"}
  ]
}
client ──pay via facilitator──► facilitator
facilitator ──signed receipt──► client
client ──GET /api/v3/... ──► server (with x-signature header)
server ──verify receipt──► [200 or 402]
```

**关键不变量**：
- 服务器不接触私钥（facilitator 持私钥签名收款）
- 服务器不接触 USDC 余额（facilitator 托管）
- 客户端只需一次 API key + 一次 x402 支付即可长期访问

### 1.4 三种 facilitator 实现（可插拔）

| 实现 | 用途 | 上线条件 |
|---|---|---|
| `SimulatedFacilitator` | 本地模拟，不联网 | 已上线（默认） |
| `CircleFacilitator` | Circle 官方 API | 需 Circle API 凭证，生产部署时启用 |
| `NoneFacilitator` | 关闭 x402 通道 | 用于紧急切断 |

切换方式：在 API 服务启动时注入即可，无需改代码。

```python
from scripts.x402.x402_facilitator import SimulatedFacilitator, X402Middleware
mw = X402Middleware(
    facilitator=SimulatedFacilitator(initial_balances={"0xtest": "1000"}),
    protected_paths=["/api/v3/**"],
    licensed_keys=set_of_paid_api_keys,
)
```

## 二、集成指南

### 2.1 服务器端（FastAPI 示例）

```python
from fastapi import Request, HTTPException
from scripts.x402.x402_facilitator import (
    SimulatedFacilitator, X402Middleware, X402PaymentRequest,
)

facilitator = SimulatedFacilitator()
mw = X402Middleware(
    facilitator=facilitator,
    protected_paths=["/api/v3/**"],
    licensed_keys=load_licensed_keys_from_db(),
)

@app.get("/api/v3/microbiology")
async def microbiology(request: Request, api_key: str = Header()):
    if mw.should_charge("/api/v3/microbiology", api_key):
        # 无 license，返回 402
        resp = mw.payment_required_response(tier="pro", caller_id=api_key)
        raise HTTPException(status_code=402, detail=resp["body"])
    # 已 license，正常处理
    return await do_microbiology_work(api_key)
```

### 2.2 客户端（agent 侧）

```python
from scripts.x402.x402_facilitator import X402PaymentRequest, sign_payment_request
import requests

response = requests.get("https://swarmlabs.tools/api/v3/microbiology",
                       headers={"Authorization": "***"})
if response.status_code == 402:
    pr = response.json()  # X402PaymentRequired body
    # 1. 向 facilitator 支付
    facilitator_response = requests.post(pr["facilitator"], json={
        "amount": pr["amount"],
        "currency": pr["currency"],
        "payTo": pr["pay_to"],
        "payer": my_wallet_address,
    })
    # 2. 拿到收据，带着 x-signature 重试
    sig = sign_payment_request(receipt_request, facilitator_secret)
    response2 = requests.get("https://swarmlabs.tools/api/v3/microbiology",
                            headers={"Authorization": "***", "X402-Signature": sig})
```

## 三、安全模型

### 3.1 关键安全边界

1. **签名不泄漏私钥**：客户端签名用 HMAC-SHA256，需要 facilitator secret；
   facilitator 只暴露 verify 接口，不返回私钥。
2. **时间戳防重放**：所有支付请求必须携带 timestamp，30 分钟过期。
3. **金额容差**：±5% 汇率容差带（$26.60–$29.40 for $28 lifetime），避免汇率波动频繁过期。
4. **收据校验**：服务器侧不信任客户端传来的 receipt，必须走 facilitator 复核。

### 3.2 简化点（生产环境需替换）

| 当前实现 | 生产替换 |
|---|---|
| HMAC-SHA256 签名 | Ed25519 签名（用 PyNaCl） |
| SimulatedFacilitator | Circle 官方 facilitator API |
| 固定汇率 0.14 | CoinGecko / x402 exchange API 动态汇率 |
| 内存 licensed_keys 集合 | Redis / Postgres 持久化 license 表 |

### 3.3 未做的安全控制

- **无 rate limiting**：应限制单 API key 的 x402 请求频率
- **无审计日志**：应记录所有 402 响应与支付结算
- **无退款流程**：USDC 支付不可逆，需明确退款政策

## 四、测试

- `scripts/x402/x402_facilitator.py selftest`：12 项端到端自检（全绿）
- `tests/test_x402.py`：26 项单元测试（全绿）
  - 定价表完整性：6 项
  - generate_payment_required：4 项
  - 签名/验证/篡改/过期：4 项
  - SimulatedFacilitator：5 项
  - NoneFacilitator：2 项
  - Middleware：4 项
  - 容差带：1 项

## 五、生产部署步骤

1. **本地测试**：`python scripts/x402/x402_facilitator.py selftest`（应输出 PASS）
2. **CI 集成**：把 `tests/test_x402.py` 加入 `python -m unittest` 测试集
3. **申请 Circle facilitator API**：访问 [circle.xyz/x402](https://www.circle.xyz/x402)，
   获取 API key + 收款地址，替换 `SWARMLABS_PAY_TO`
4. **注入 facilitator**：在 API 服务启动时选择 `CircleFacilitator`（生产）或
   `SimulatedFacilitator`（开发）
5. **监控**：跟踪 x402 支付成功率、平均结算时间、汇率波动

## 六、与 AIsa 生态对接

- **协议对齐**：本实现严格遵循 x402 协议 v1（HTTP 402 + facilitator 三方模式）
- **上架准备**：SwarmLabs 可上架 AIsa OpenClaw/ClawHub（等 AIsa 开放 skill 目录）
- **兼容性**：AIsa 的 AgentPay Guard 使用相同的 x402 协议，可互通

## 七、相关文件

- `scripts/x402/x402_facilitator.py`：核心实现
- `tests/test_x402.py`：单元测试
- `docs/x402-integration.md`：本文件
- `docs/swarmlabs-x402-plan.md`：三档方案对比（历史决策记录）
- `scripts/jwks/jwks_tool.py`：签名验证工具（x402 客户端可用同一套 JWKS）

## 八、下一步

- [ ] 申请 Circle x402 facilitator API 凭证
- [ ] 部署 Circle facilitator 后端，替换 SimulatedFacilitator
- [ ] 接入 CoinGecko 动态汇率
- [ ] license.swarmlabs.tools 加 x402 支付选项（与虎皮椒并列）
- [ ] 上架 AIsa OpenClaw（等目录开放）
- [ ] 加入 rate limiting + 审计日志
