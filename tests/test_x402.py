"""
x402_facilitator 单元测试。

覆盖：
- 定价表完整性（pro/lifetime/trial 三档）
- generate_payment_required：生成合法 402 响应
- 客户端签名 + 服务端验证：合法通过 / 篡改失败 / 时间戳过期失败
- SimulatedFacilitator：余额管理 / 结算扣款 / 收据生成
- NoneFacilitator：关闭通道返回 x402_disabled
- X402Middleware：should_charge 判断（licensed 与未 licensed 两路）
- 金额容差：±5% 汇率容差
"""
import importlib.util
import json
import os
import sys
import time
import unittest
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
X402_PY = REPO / "scripts" / "x402" / "x402_facilitator.py"


def _load():
    name = "x402_facilitator_isolated"
    spec = importlib.util.spec_from_file_location(name, X402_PY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class TestPricing(unittest.TestCase):
    def setUp(self):
        self.m = _load()

    def test_three_tiers_exist(self):
        self.assertIn("pro", self.m.LICENSE_PRICING)
        self.assertIn("lifetime", self.m.LICENSE_PRICING)
        self.assertIn("trial", self.m.LICENSE_PRICING)

    def test_pro_pricing(self):
        p = self.m.LICENSE_PRICING["pro"]
        self.assertEqual(p["cny"], Decimal("39.90"))
        self.assertEqual(p["usdc"], Decimal("5.50"))
        self.assertEqual(p["duration_hours"], 720)

    def test_lifetime_pricing(self):
        p = self.m.LICENSE_PRICING["lifetime"]
        self.assertEqual(p["cny"], Decimal("199.00"))
        self.assertEqual(p["usdc"], Decimal("28.00"))
        self.assertEqual(p["duration_hours"], -1)  # 永久

    def test_trial_pricing(self):
        p = self.m.LICENSE_PRICING["trial"]
        self.assertEqual(p["usdc"], Decimal("0.00"))
        self.assertEqual(p["duration_hours"], 24)

    def test_price_to_usdc_conversion(self):
        # ¥39.90 * 0.14 ≈ $5.59，但用固定表定价 $5.50
        converted = self.m.price_to_usdc(Decimal("39.90"))
        # 容差内
        self.assertTrue(
            abs(converted - Decimal("5.50")) < Decimal("0.15"),
            f"转换结果 {converted} 应在 5.50 ± 0.15 内",
        )

    def test_tier_field_completeness(self):
        for tier, p in self.m.LICENSE_PRICING.items():
            self.assertIn("cny", p)
            self.assertIn("usd", p)
            self.assertIn("usdc", p)
            self.assertIn("duration_hours", p)
            self.assertIn("label", p)


class TestPaymentRequired(unittest.TestCase):
    def setUp(self):
        self.m = _load()

    def test_generate_payment_required_pro(self):
        pr, pr_id = self.m.generate_payment_required(tier="pro", caller_id="test-agent")
        self.assertEqual(pr.amount, "5.50")
        self.assertEqual(pr.currency, "USDC")
        self.assertEqual(pr.network, "base")
        self.assertIn("pay_to", pr.to_dict())
        self.assertIn("facilitator", pr.to_dict())
        self.assertTrue(pr_id.startswith("req-"))

    def test_generate_payment_required_lifetime(self):
        pr, _ = self.m.generate_payment_required(tier="lifetime", caller_id="test")
        self.assertEqual(pr.amount, "28.00")

    def test_unknown_tier_raises(self):
        with self.assertRaises(ValueError):
            self.m.generate_payment_required(tier="nonexistent", caller_id="x")

    def test_http_response_shape(self):
        pr, _ = self.m.generate_payment_required(tier="pro", caller_id="test")
        resp = pr.to_http_response()
        self.assertEqual(resp["status"], 402)
        self.assertIn("headers", resp)
        self.assertIn("body", resp)
        self.assertEqual(resp["headers"]["X-Payment-Required"], "true")
        self.assertEqual(resp["body"]["amount"], "5.50")


class TestSigning(unittest.TestCase):
    def setUp(self):
        self.m = _load()
        self.secret = "test-secret"

    def test_sign_and_verify_roundtrip(self):
        req = self.m.X402PaymentRequest(
            payment_required_id="req-123",
            wallet_address="0xwallet",
            nonce="nonce-abc",
            payload_hash="",
            signature="",
            tier="pro",
            amount="5.50",
        )
        sig = self.m.sign_payment_request(req, self.secret)
        self.assertTrue(len(sig) == 64)  # SHA256 hex

        req.signature = sig
        v = self.m.verify_payment_request(req, self.secret)
        self.assertTrue(v["valid"])

    def test_tampered_amount_fails(self):
        req = self.m.X402PaymentRequest(
            payment_required_id="req-123",
            wallet_address="0xwallet",
            nonce="nonce-abc",
            payload_hash="", signature="",
            tier="pro", amount="5.50",
        )
        sig = self.m.sign_payment_request(req, self.secret)
        req.signature = sig

        # 篡改金额
        req.amount = "0.01"
        v = self.m.verify_payment_request(req, self.secret)
        self.assertFalse(v["valid"])
        self.assertEqual(v["error"], "signature_mismatch")

    def test_wrong_secret_fails(self):
        req = self.m.X402PaymentRequest(
            payment_required_id="req-123",
            wallet_address="0xwallet",
            nonce="nonce-abc",
            payload_hash="", signature="",
            tier="pro", amount="5.50",
        )
        sig = self.m.sign_payment_request(req, self.secret)
        req.signature = sig
        v = self.m.verify_payment_request(req, "wrong-secret")
        self.assertFalse(v["valid"])

    def test_expired_timestamp_fails(self):
        req = self.m.X402PaymentRequest(
            payment_required_id="req-123",
            wallet_address="0xwallet",
            nonce="nonce-abc",
            payload_hash="", signature="",
            tier="pro", amount="5.50",
        )
        sig = self.m.sign_payment_request(req, self.secret)
        req.signature = sig
        # 篡改时间戳（30 分钟前）
        req.timestamp = int(time.time()) - 2000
        v = self.m.verify_payment_request(req, self.secret)
        self.assertFalse(v["valid"])
        self.assertEqual(v["error"], "timestamp_out_of_range")


class TestSimulatedFacilitator(unittest.TestCase):
    def setUp(self):
        self.m = _load()

    def test_initial_balance(self):
        f = self.m.SimulatedFacilitator(initial_balances={"0xw1": "10.00"})
        bal = f.check_balance("0xw1")
        self.assertEqual(bal["usdc_balance"], "10.00")
        self.assertTrue(bal["has_sufficient"])

    def test_empty_balance(self):
        f = self.m.SimulatedFacilitator()
        bal = f.check_balance("0xunknown")
        self.assertEqual(bal["usdc_balance"], "0")
        self.assertFalse(bal["has_sufficient"])

    def test_settle_success(self):
        f = self.m.SimulatedFacilitator(initial_balances={"0xw1": "10.00"})
        req = self.m.X402PaymentRequest(
            payment_required_id="req-1", wallet_address="0xw1",
            nonce="n1", payload_hash="", signature="",
            tier="pro", amount="5.50",
        )
        req.signature = self.m.sign_payment_request(req, f.secret)
        r = f.settle_payment(req)
        self.assertTrue(r["settled"])
        self.assertIsNotNone(r["tx_hash"])
        self.assertEqual(r["receipt"]["amount"], "5.50")
        self.assertEqual(r["receipt"]["payer"], "0xw1")
        # 余额减少
        bal = f.check_balance("0xw1")
        self.assertEqual(bal["usdc_balance"], "4.50")

    def test_settle_insufficient_funds(self):
        f = self.m.SimulatedFacilitator(initial_balances={"0xw1": "1.00"})
        req = self.m.X402PaymentRequest(
            payment_required_id="req-1", wallet_address="0xw1",
            nonce="n1", payload_hash="", signature="",
            tier="pro", amount="5.50",
        )
        req.signature = self.m.sign_payment_request(req, f.secret)
        r = f.settle_payment(req)
        self.assertFalse(r["settled"])
        self.assertEqual(r["error"], "insufficient_balance")

    def test_settle_bad_signature(self):
        f = self.m.SimulatedFacilitator(initial_balances={"0xw1": "10.00"})
        req = self.m.X402PaymentRequest(
            payment_required_id="req-1", wallet_address="0xw1",
            nonce="n1", payload_hash="", signature="bogus",
            tier="pro", amount="5.50",
        )
        r = f.settle_payment(req)
        self.assertFalse(r["settled"])
        self.assertEqual(r["error"], "signature_mismatch")


class TestNoneFacilitator(unittest.TestCase):
    def setUp(self):
        self.m = _load()

    def test_settle_returns_disabled(self):
        nf = self.m.NoneFacilitator()
        req = self.m.X402PaymentRequest(
            payment_required_id="req-1", wallet_address="0xw1",
            nonce="n1", payload_hash="", signature="",
            tier="pro", amount="5.50",
        )
        r = nf.settle_payment(req)
        self.assertFalse(r["settled"])
        self.assertEqual(r["error"], "x402_disabled")

    def test_check_balance_returns_zero(self):
        nf = self.m.NoneFacilitator()
        bal = nf.check_balance("0xw1")
        self.assertEqual(bal["usdc_balance"], "0")
        self.assertFalse(bal["has_sufficient"])


class TestMiddleware(unittest.TestCase):
    def setUp(self):
        self.m = _load()
        self.facilitator = self.m.SimulatedFacilitator(initial_balances={"0xw1": "10.00"})

    def test_should_charge_unlicensed(self):
        mw = self.m.X402Middleware(
            facilitator=self.facilitator,
            protected_paths=["/api/v3/**"],
        )
        self.assertTrue(mw.should_charge("/api/v3/microbiology", "some-key"))
        self.assertFalse(mw.should_charge("/api/v1/public", "some-key"))

    def test_should_not_charge_licensed(self):
        mw = self.m.X402Middleware(
            facilitator=self.facilitator,
            protected_paths=["/api/v3/**"],
            licensed_keys={"some-key"},
        )
        self.assertFalse(mw.should_charge("/api/v3/microbiology", "some-key"))

    def test_payment_required_response(self):
        mw = self.m.X402Middleware(
            facilitator=self.facilitator,
            protected_paths=["/api/v3/**"],
        )
        resp = mw.payment_required_response(tier="pro", caller_id="caller-1")
        self.assertEqual(resp["status"], 402)
        self.assertEqual(resp["body"]["amount"], "5.50")

    def test_wildcard_match(self):
        mw = self.m.X402Middleware(
            facilitator=self.facilitator,
            protected_paths=["/api/v3/**"],
        )
        # /api/v3 下的所有路径都应匹配
        for path in ["/api/v3/microbiology", "/api/v3/entities/foo", "/api/v3/x"]:
            self.assertTrue(mw.should_charge(path, "k"))
        # 其他路径不匹配
        for path in ["/api/v1/x", "/docs", "/"]:
            self.assertFalse(mw.should_charge(path, "k"))


class TestToleranceBand(unittest.TestCase):
    def setUp(self):
        self.m = _load()

    def test_tolerance_band_for_lifetime(self):
        p = self.m.LICENSE_PRICING["lifetime"]
        tol_low = (p["usdc"] - self.m.PRICING_TOLERANCE * p["usdc"]).quantize(Decimal("0.01"))
        tol_high = (p["usdc"] + self.m.PRICING_TOLERANCE * p["usdc"]).quantize(Decimal("0.01"))
        self.assertLess(tol_low, p["usdc"])
        self.assertGreater(tol_high, p["usdc"])
        # 具体值
        self.assertEqual(tol_low, Decimal("26.60"))
        self.assertEqual(tol_high, Decimal("29.40"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
