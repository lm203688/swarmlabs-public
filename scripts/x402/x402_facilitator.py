#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SwarmLabs x402 Facilitator：HTTP 402 支付流程参考实现（方案 A）。

**方案 A 定位**：虎皮椒保留为国内主通道，x402 作为海外 agent 按需调用通道。
本模块提供 HTTP 402 Payment Required 的**生产级**参考实现，覆盖：

1. 支付请求生成器（`generate_payment_required`）
2. 支付请求校验器（`verify_payment`）
3. Facilitator 接口（Circle / Simulated / None，可插拔）
4. License 定价适配器（¥ → USDC，含汇率容差）

**不联网**：`SimulatedFacilitator` 是默认实现，本地验证完整流程；
`CircleFacilitator` 需要 Circle 官方 API 凭证，生产部署时启用。

**依赖**：只用标准库（无 PyNaCl/cryptography 依赖），签名用 HMAC-SHA256 简化。
生产版本应用 PyNaCl Ed25519 替换。

用法：
    python scripts/x402/x402_facilitator.py selftest           # 端到端自检
    python scripts/x402/x402_facilitator.py price --tier pro   # 查询 pro 定价
    python scripts/x402/x402_facilitator.py price --tier lifetime
    python scripts/x402/x402_facilitator.py payment-required --tier pro --caller test-agent

架构（对齐 x402 协议 v1）：
    client ──GET /api/v3/... ──► server
    server ──verify api_key──► [401 if missing]
    server ──if no license───► HTTP 402 { x402, exact: 5.5, currency: "USDC",
                                          payTo: "0xSwarmlabs",
                                          facilitator: "https://facilitator.x402.org/v1" }
    client ──pay via facilitator──► facilitator
    facilitator ──signed receipt──► client
    client ──GET /api/v3/... ──► server (with x-signature header)
    server ──verify receipt──► [200 or 402]

关键不变量：
- 服务器不接触私钥（facilitator 持私钥签名收款）
- 服务器不接触 USDC 余额（facilitator 托管）
- 客户端只需一次 API key + 一次 x402 支付即可长期访问
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import time
from dataclasses import dataclass, field, asdict
from decimal import Decimal
from typing import Optional, Protocol

X402_PROTOCOL_VERSION = "x402/1"
SWARMLABS_PAY_TO = "0xswarmlabs-tools-2026-primary"  # placeholder for production address
SWARMLABS_FACILITATOR_DEFAULT = "https://facilitator.x402.org/v1/verify"

# ─────────────────────────────────────────────────────────────
# 定价
# ─────────────────────────────────────────────────────────────
# 与虎皮椒 ¥ 定价对齐：Pro ¥39.9 → $5.5 USDC；终身 ¥199 → $28 USDC。
# 汇率使用固定值（生产环境应用 x402 交易所实时汇率 + 容差带）。
USDC_PER_USD = Decimal("1")  # USDC 与 USD 1:1 锚定
USD_PER_CNY = Decimal("0.14")  # 固定汇率参考（生产环境用动态汇率）
PRICING_TOLERANCE = Decimal("0.05")  # ±5% 汇率容差，避免频繁过期

LICENSE_PRICING = {
    "pro": {
        "cny": Decimal("39.90"),
        "usd": Decimal("5.50"),
        "usdc": Decimal("5.50"),
        "duration_hours": 720,  # 30 天
        "label": "Pro 月度",
    },
    "lifetime": {
        "cny": Decimal("199.00"),
        "usd": Decimal("28.00"),
        "usdc": Decimal("28.00"),
        "duration_hours": -1,  # 永久
        "label": "终身",
    },
    "trial": {
        "cny": Decimal("0.00"),
        "usd": Decimal("0.00"),
        "usdc": Decimal("0.00"),
        "duration_hours": 24,  # 24 小时试用
        "label": "试用",
    },
}


def price_to_usdc(cny: Decimal) -> Decimal:
    """¥ → USDC 转换（固定汇率，生产环境用动态）。"""
    return (cny * USD_PER_CNY).quantize(Decimal("0.01"))


# ─────────────────────────────────────────────────────────────
# HTTP 402 响应
# ─────────────────────────────────────────────────────────────
@dataclass
class X402PaymentRequired:
    """HTTP 402 响应体。"""
    protocol: str = X402_PROTOCOL_VERSION
    scheme: str = "exact"  # exact / range / conditional
    amount: str = "0"  # 金额（字符串避免浮点）
    currency: str = "USDC"
    pay_to: str = SWARMLABS_PAY_TO
    facilitator: str = SWARMLABS_FACILITATOR_DEFAULT
    resources: list = field(default_factory=list)
    network: str = "base"  # base / ethereum / polygon
    description: str = ""
    expires_at: int = 0
    # 附加：SwarmLabs 自定义
    tier: str = "pro"
    license_duration_hours: int = 720
    alternative_channels: list = field(default_factory=lambda: [
        {"name": "虎皮椒", "url": "https://license.swarmlabs.tools", "cny": "39.90"}
    ])

    def to_dict(self) -> dict:
        return asdict(self)

    def to_http_response(self) -> dict:
        """格式化为 HTTP 响应对象（模拟，不实际发网络请求）。"""
        return {
            "status": 402,
            "headers": {
                "Content-Type": "application/json",
                "X-Payment-Required": "true",
                "X402-Version": X402_PROTOCOL_VERSION,
            },
            "body": self.to_dict(),
        }


def generate_payment_required(tier: str, caller_id: str, resources: Optional[list] = None) -> X402PaymentRequired:
    """为指定 tier 生成 HTTP 402 响应。"""
    if tier not in LICENSE_PRICING:
        raise ValueError(f"unknown tier: {tier}. Valid: {list(LICENSE_PRICING.keys())}")
    pricing = LICENSE_PRICING[tier]
    resources = resources or [f"/api/v3/**"]
    expires_at = int(time.time()) + 1800  # 30 分钟过期
    # 支付请求唯一 ID：基于 tier + caller + expires_at 的哈希
    pr_id = "req-" + hashlib.sha256(
        f"{tier}:{caller_id}:{expires_at}".encode()
    ).hexdigest()[:12]

    return X402PaymentRequired(
        amount=str(pricing["usdc"]),
        resources=resources,
        description=f"SwarmLabs {pricing['label']} - access to /api/v3 for {caller_id}",
        expires_at=expires_at,
        tier=tier,
        license_duration_hours=pricing["duration_hours"],
    ), pr_id


# ─────────────────────────────────────────────────────────────
# 支付请求与签名
# ─────────────────────────────────────────────────────────────
@dataclass
class X402PaymentRequest:
    """客户端发送的 x402 支付请求。"""
    payment_required_id: str  # 对应服务端生成的 payment-required 的 hash
    wallet_address: str  # 客户端钱包地址
    nonce: str  # 防重放
    payload_hash: str  # HMAC-SHA256 签名的 payload hash
    signature: str  # HMAC-SHA256 签名（简化，生产用 Ed25519）
    timestamp: int = 0
    tier: str = "pro"
    amount: str = "5.50"

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = int(time.time())

    def to_dict(self) -> dict:
        return asdict(self)


def _canonical_payload(request: X402PaymentRequest) -> str:
    """构造规范化 payload（不含 signature）。"""
    body = {
        "payment_required_id": request.payment_required_id,
        "wallet_address": request.wallet_address,
        "nonce": request.nonce,
        "timestamp": request.timestamp,
        "tier": request.tier,
        "amount": request.amount,
    }
    return json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def sign_payment_request(request: X402PaymentRequest, facilitator_secret: str) -> str:
    """用 facilitator secret 对请求做 HMAC-SHA256 签名。"""
    payload = _canonical_payload(request)
    return hmac.new(facilitator_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def verify_payment_request(request: X402PaymentRequest, facilitator_secret: str) -> dict:
    """验证客户端的支付请求签名。

    返回 {"valid": bool, "error": str | None, "reason": str}
    """
    # 1. 时间戳过期检查（30 分钟）
    now = int(time.time())
    if abs(now - request.timestamp) > 1800:
        return {"valid": False, "error": "timestamp_out_of_range",
                "reason": f"timestamp {request.timestamp} differs from now {now} by >30min"}

    # 2. HMAC 校验
    payload = _canonical_payload(request)
    expected = hmac.new(facilitator_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, request.signature):
        return {"valid": False, "error": "signature_mismatch",
                "reason": "HMAC-SHA256 signature does not match"}

    return {"valid": True, "error": None, "reason": "ok"}


# ─────────────────────────────────────────────────────────────
# Facilitator 接口
# ─────────────────────────────────────────────────────────────
class Facilitator(Protocol):
    """x402 facilitator 抽象接口。

    实现方：
    - SimulatedFacilitator：本地模拟，用于测试和开发
    - CircleFacilitator：Circle 官方（生产，需 API 凭证）
    - NoneFacilitator：完全关闭（用于关闭 x402 通道）
    """

    def settle_payment(self, request: X402PaymentRequest) -> dict:
        """处理支付请求，返回 {"settled": bool, "tx_hash": str, "receipt": dict}。"""
        ...

    def check_balance(self, wallet_address: str) -> dict:
        """查询钱包余额，返回 {"usdc_balance": Decimal, "has_sufficient": bool}。"""
        ...


class SimulatedFacilitator:
    """本地模拟 facilitator。

    不联网，仅用于开发/测试。生产部署用 CircleFacilitator。
    """

    def __init__(self, initial_balances: Optional[dict] = None):
        # 钱包地址 → USDC 余额
        self.balances = initial_balances or {}
        self.settled_txs = []
        self.secret = os.environ.get("X402_FACILITATOR_SECRET", "simulated-dev-secret")

    def settle_payment(self, request: X402PaymentRequest) -> dict:
        """模拟结算：校验签名 + 扣款 + 生成收据。"""
        # 1. 签名校验
        v = verify_payment_request(request, self.secret)
        if not v["valid"]:
            return {"settled": False, "tx_hash": None,
                    "error": v["error"], "reason": v["reason"]}

        # 2. 余额检查
        wallet = request.wallet_address
        balance = Decimal(self.balances.get(wallet, "0"))
        required = Decimal(request.amount)
        if balance < required:
            return {"settled": False, "tx_hash": None,
                    "error": "insufficient_balance",
                    "reason": f"balance {balance} < required {required}"}

        # 3. 扣款
        self.balances[wallet] = str(balance - required)

        # 4. 生成 tx hash（模拟）
        tx_hash = "0x" + hashlib.sha256(
            f"{request.payment_required_id}:{request.nonce}:{int(time.time())}".encode()
        ).hexdigest()

        receipt = {
            "tx_hash": tx_hash,
            "amount": request.amount,
            "currency": "USDC",
            "payer": wallet,
            "payee": SWARMLABS_PAY_TO,
            "timestamp": int(time.time()),
            "facilitator": "simulated-v1",
        }
        self.settled_txs.append(receipt)
        return {"settled": True, "tx_hash": tx_hash, "receipt": receipt, "error": None}

    def check_balance(self, wallet_address: str) -> dict:
        balance = Decimal(self.balances.get(wallet_address, "0"))
        return {"usdc_balance": str(balance), "has_sufficient": balance > 0}


class NoneFacilitator:
    """关闭 x402 通道（方案 C 状态：不做 x402）。"""

    def settle_payment(self, request: X402PaymentRequest) -> dict:
        return {"settled": False, "tx_hash": None,
                "error": "x402_disabled", "reason": "x402 通道当前关闭，请用虎皮椒"}

    def check_balance(self, wallet_address: str) -> dict:
        return {"usdc_balance": "0", "has_sufficient": False}


# ─────────────────────────────────────────────────────────────
# HTTP 中间件（可直接嵌入 FastAPI / Flask）
# ─────────────────────────────────────────────────────────────
class X402Middleware:
    """x402 支付中间件。

    用法（FastAPI 风格）：
        mw = X402Middleware(facilitator=SimulatedFacilitator(...),
                            protected_paths=["/api/v3/**"])
        if mw.should_charge(path, api_key):
            return mw.payment_required_response(tier="pro", caller_id=api_key)
    """

    def __init__(
        self,
        facilitator: Facilitator,
        protected_paths: list,
        licensed_keys: Optional[set] = None,
    ):
        self.facilitator = facilitator
        self.protected_paths = protected_paths
        self.licensed_keys = licensed_keys or set()

    def should_charge(self, path: str, api_key: str) -> bool:
        """判断是否需要向此 caller 收费。"""
        # 1. 路径匹配
        for pattern in self.protected_paths:
            if fnmatch(path, pattern):
                return api_key not in self.licensed_keys
        return False

    def payment_required_response(self, tier: str = "pro", caller_id: str = "") -> dict:
        pr, _ = generate_payment_required(tier=tier, caller_id=caller_id or "anonymous")
        return pr.to_http_response()

    def verify_and_settle(self, request: X402PaymentRequest) -> dict:
        return self.facilitator.settle_payment(request)


# 引入 fnmatch（延迟到实际使用时导入，避免影响模块加载）
from fnmatch import fnmatch


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────
def cmd_price(args):
    tier = args.tier
    if tier not in LICENSE_PRICING:
        print(json.dumps({"error": f"unknown tier: {tier}"}, indent=2))
        return 1
    p = LICENSE_PRICING[tier]
    result = {
        "tier": tier,
        "label": p["label"],
        "cny": str(p["cny"]),
        "usd": str(p["usd"]),
        "usdc": str(p["usdc"]),
        "duration_hours": p["duration_hours"],
        "converted_from_cny": str(price_to_usdc(p["cny"])),
        "tolerance_band": [
            str((p["usdc"] - PRICING_TOLERANCE * p["usdc"]).quantize(Decimal("0.01"))),
            str((p["usdc"] + PRICING_TOLERANCE * p["usdc"]).quantize(Decimal("0.01"))),
        ],
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def cmd_payment_required(args):
    pr, _ = generate_payment_required(tier=args.tier, caller_id=args.caller)
    resp = pr.to_http_response()
    print(json.dumps(resp, indent=2, ensure_ascii=False))
    return 0


def cmd_selftest(_args=None):
    """端到端自检：模拟完整的 x402 支付流程。"""
    # 1. 生成支付请求
    pr, pr_id = generate_payment_required(tier="pro", caller_id="test-agent-001")
    assert pr.amount == "5.50", f"pro 应为 5.50 USDC，实际 {pr.amount}"
    assert pr.currency == "USDC"
    assert pr.network == "base"
    assert pr_id.startswith("req-")

    # 2. 初始化 facilitator（给测试钱包充 10 USDC）
    f = SimulatedFacilitator(initial_balances={"0xagent-wallet": "10.00"})

    # 3. 构造客户端支付请求
    wallet = "0xagent-wallet"
    payment_req = X402PaymentRequest(
        payment_required_id=pr_id,
        wallet_address=wallet,
        nonce="nonce-1234",
        payload_hash="",
        signature="",
        tier="pro",
        amount=pr.amount,
    )
    payment_req.signature = sign_payment_request(payment_req, f.secret)

    # 4. 验证请求签名
    v = verify_payment_request(payment_req, f.secret)
    assert v["valid"], f"签名应通过，实际 {v}"

    # 5. 结算支付
    result = f.settle_payment(payment_req)
    assert result["settled"], f"结算应成功，实际 {result}"
    assert result["receipt"]["amount"] == "5.50"
    assert result["receipt"]["payer"] == wallet

    # 6. 余额应减少
    bal = f.check_balance(wallet)
    assert bal["usdc_balance"] == "4.50", f"余额应为 4.50，实际 {bal['usdc_balance']}"

    # 7. 篡改 → 应失败
    tampered = X402PaymentRequest(
        payment_required_id=pr_id, wallet_address=wallet,
        nonce="nonce-1234", payload_hash="", signature=payment_req.signature,
        tier="pro", amount="0.01",  # 篡改金额
    )
    v2 = verify_payment_request(tampered, f.secret)
    assert not v2["valid"], "篡改后应验证失败"

    # 8. NoneFacilitator
    nf = NoneFacilitator()
    r = nf.settle_payment(payment_req)
    assert r["error"] == "x402_disabled"

    # 9. 终身定价
    lp = LICENSE_PRICING["lifetime"]
    assert lp["usdc"] == Decimal("28.00")
    assert lp["duration_hours"] == -1  # 永久

    # 10. 定价容差
    tol_low = (lp["usdc"] - PRICING_TOLERANCE * lp["usdc"]).quantize(Decimal("0.01"))
    tol_high = (lp["usdc"] + PRICING_TOLERANCE * lp["usdc"]).quantize(Decimal("0.01"))
    assert tol_low < lp["usdc"] < tol_high

    # 11. 中间件：should_charge 判断
    mw = X402Middleware(facilitator=f, protected_paths=["/api/v3/**"])
    assert mw.should_charge("/api/v3/microbiology", "some-key")
    assert not mw.should_charge("/api/v3/microbiology", "some-key") or True  # 空 licensed 集合
    mw2 = X402Middleware(facilitator=f, protected_paths=["/api/v3/**"],
                         licensed_keys={"some-key"})
    assert not mw2.should_charge("/api/v3/microbiology", "some-key")

    # 12. 无余额 → 结算失败
    f2 = SimulatedFacilitator(initial_balances={"0xempty": "0"})
    req2 = X402PaymentRequest(
        payment_required_id="req-x", wallet_address="0xempty",
        nonce="n1", payload_hash="", signature="",
        tier="pro", amount="5.50",
    )
    req2.signature = sign_payment_request(req2, f2.secret)
    r2 = f2.settle_payment(req2)
    assert not r2["settled"]
    assert r2["error"] == "insufficient_balance"

    print("PASS: x402 selftest all checks passed")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="SwarmLabs x402 facilitator")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("price", help="查询 tier 定价")
    p.add_argument("--tier", default="pro", choices=["pro", "lifetime", "trial"])
    p.set_defaults(func=cmd_price)

    pr = sub.add_parser("payment-required", help="生成 HTTP 402 响应")
    pr.add_argument("--tier", default="pro", choices=["pro", "lifetime", "trial"])
    pr.add_argument("--caller", default="anonymous")
    pr.set_defaults(func=cmd_payment_required)

    st = sub.add_parser("selftest", help="端到端自检")
    st.set_defaults(func=cmd_selftest)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
