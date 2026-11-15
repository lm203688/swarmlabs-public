"""
JWKS 工具单元测试。

设计要点：
1. 不污染真实 .well-known/jwks.json —— 用 tempfile 隔离；
2. 不 import engine 包（避免 numpy 依赖）；
3. 用 importlib.util 按文件路径加载 jwks_tool.py。

覆盖：
- generate_keypair：创建 jwks.json、写入密钥文件、rotate 追加
- sign_manifest + apply_signature：签名流程
- verify_manifest：验证通过 / 篡改失败 / 占位符失败 / key 不在 jwks 中失败
- 密钥轮换：旧密钥 revoked 后不能再验证
"""
import base64
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
JWKS_TOOL = REPO / "scripts" / "jwks" / "jwks_tool.py"


def _load():
    """按路径加载 jwks_tool，绕开 engine/__init__.py。"""
    name = "jwks_tool_isolated"
    spec = importlib.util.spec_from_file_location(name, JWKS_TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class TestJWKS(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.m = _load()
        # 让模块级路径常量指向临时目录
        self.m.WELL_KNOWN = self.tmpdir / ".well-known"
        self.m.KEYS_DIR = self.m.WELL_KNOWN / "keys"
        self.m.JWKS_PATH = self.m.WELL_KNOWN / "jwks.json"

    def tearDown(self):
        if self.tmpdir.exists():
            shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_generate_keypair_creates_jwks(self):
        result = self.m.generate_keypair(kid="test-key-1", rotate=False)
        self.assertEqual(result["kid"], "test-key-1")
        self.assertTrue(self.m.JWKS_PATH.exists())
        jwks = json.loads(self.m.JWKS_PATH.read_text(encoding="utf-8"))
        self.assertEqual(len(jwks["keys"]), 1)
        self.assertEqual(jwks["keys"][0]["kid"], "test-key-1")
        self.assertEqual(jwks["keys"][0]["kty"], "OKP")
        self.assertEqual(jwks["keys"][0]["crv"], "Ed25519")
        self.assertIn("x", jwks["keys"][0])
        # 私钥文件已生成
        priv_path = self.m.KEYS_DIR / "test-key-1.key"
        self.assertTrue(priv_path.exists())
        self.assertEqual(len(priv_path.read_bytes()), 32)  # Ed25519 seed is 32 bytes

    def test_generate_keypair_rotate_appends(self):
        self.m.generate_keypair(kid="key-v1", rotate=False)
        self.m.generate_keypair(kid="key-v2", rotate=True)
        jwks = json.loads(self.m.JWKS_PATH.read_text(encoding="utf-8"))
        kids = [k["kid"] for k in jwks["keys"]]
        self.assertEqual(len(kids), 2)
        self.assertIn("key-v1", kids)
        self.assertIn("key-v2", kids)

    def test_sign_and_verify_roundtrip(self):
        # 生成密钥
        result = self.m.generate_keypair(kid="roundtrip-key")
        # 创建 manifest
        manifest_path = self.tmpdir / "manifest.json"
        manifest_path.write_text(json.dumps({
            "manifestVersion": "1.0", "name": "test-manifest", "version": "1.0.0",
        }, indent=2), encoding="utf-8")
        key_path = self.m.KEYS_DIR / "roundtrip-key.key"

        # 签名
        self.m.apply_signature(manifest_path, key_path, kid="roundtrip-key")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertIn("signature", manifest)
        self.assertEqual(manifest["signature"]["keyId"], "roundtrip-key")

        # 验证
        result = self.m.verify_manifest(manifest_path, self.m.JWKS_PATH)
        self.assertTrue(result["valid"], f"应验证通过，实际 {result}")
        self.assertEqual(result["kid"], "roundtrip-key")

    def test_verify_detects_tampering(self):
        # 先生成有效的签名
        self.m.generate_keypair(kid="tamper-key")
        manifest_path = self.tmpdir / "manifest.json"
        manifest_path.write_text(json.dumps({
            "manifestVersion": "1.0", "name": "tamper-test", "version": "1.0.0",
        }), encoding="utf-8")
        key_path = self.m.KEYS_DIR / "tamper-key.key"
        self.m.apply_signature(manifest_path, key_path, kid="tamper-key")

        # 篡改 manifest
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["version"] = "9.9.9"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        result = self.m.verify_manifest(manifest_path, self.m.JWKS_PATH)
        self.assertFalse(result["valid"])

    def test_verify_rejects_placeholder(self):
        self.m.generate_keypair(kid="ph-key")
        manifest_path = self.tmpdir / "manifest.json"
        manifest_path.write_text(json.dumps({
            "manifestVersion": "1.0", "name": "ph-test",
            "signature": {"algorithm": "Ed25519", "keyId": "ph-key",
                          "payload": "canonical-utf8-json",
                          "signature": "[待发布签名密钥后填充]"},
        }), encoding="utf-8")
        result = self.m.verify_manifest(manifest_path, self.m.JWKS_PATH)
        self.assertFalse(result["valid"])
        self.assertIn("placeholder", result["error"])

    def test_verify_rejects_unknown_kid(self):
        # jwks 里有一个 key，manifest 用的是不同的 keyId
        self.m.generate_keypair(kid="real-key")
        manifest_path = self.tmpdir / "manifest.json"
        manifest_path.write_text(json.dumps({
            "manifestVersion": "1.0", "name": "unknown-kid",
            "signature": {"algorithm": "Ed25519", "keyId": "not-in-jwks",
                          "payload": "canonical-utf8-json",
                          "signature": "aW52YWxpZA=="},
        }), encoding="utf-8")
        result = self.m.verify_manifest(manifest_path, self.m.JWKS_PATH)
        self.assertFalse(result["valid"])
        self.assertIn("not found", result["error"])

    def test_revoked_key_rejected(self):
        self.m.generate_keypair(kid="revoked-key")
        manifest_path = self.tmpdir / "manifest.json"
        manifest_path.write_text(json.dumps({
            "manifestVersion": "1.0", "name": "revoke-test", "version": "1.0.0",
        }), encoding="utf-8")
        key_path = self.m.KEYS_DIR / "revoked-key.key"
        self.m.apply_signature(manifest_path, key_path, kid="revoked-key")

        # 标记为 revoked
        jwks = json.loads(self.m.JWKS_PATH.read_text(encoding="utf-8"))
        jwks["keys"][0]["revoked_at"] = "2026-09-22T00:00:00+08:00"
        self.m.JWKS_PATH.write_text(json.dumps(jwks, indent=2), encoding="utf-8")

        result = self.m.verify_manifest(manifest_path, self.m.JWKS_PATH)
        self.assertFalse(result["valid"])
        self.assertIn("revoked", result["error"])

    def test_missing_signature_block(self):
        manifest_path = self.tmpdir / "manifest.json"
        manifest_path.write_text(json.dumps({"name": "no-sig"}), encoding="utf-8")
        # 需要一个 jwks 文件存在
        self.m.generate_keypair(kid="dummy")
        result = self.m.verify_manifest(manifest_path, self.m.JWKS_PATH)
        self.assertFalse(result["valid"])
        self.assertEqual(result["error"], "missing signature block")

    def test_canonical_bytes_stable(self):
        # 相同内容 → 相同 bytes；不同字段顺序 → 相同 bytes（规范化）
        a = {"a": 1, "b": 2}
        b = {"b": 2, "a": 1}
        self.assertEqual(self.m._canonical_bytes(a), self.m._canonical_bytes(b))

    def test_canonical_excludes_signature(self):
        m1 = {"name": "x", "version": "1.0.0"}
        m2 = {"name": "x", "version": "1.0.0",
              "signature": {"algorithm": "Ed25519", "signature": "abc"}}
        self.assertEqual(self.m._canonical_bytes(m1), self.m._canonical_bytes(m2))


if __name__ == "__main__":
    unittest.main(verbosity=2)
