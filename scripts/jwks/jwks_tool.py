#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SwarmLabs JWKS 端点：Ed25519 密钥管理 + 签名/验证工具。

让第三方 agent 能通过标准 `.well-known/jwks.json` 端点验证 SwarmLabs
发布的 `.well-known/mcp.json` / `.well-known/agent-card.json` 的
`signature` 字段是否由 SwarmLabs 官方私钥签发。

依赖：PyNaCl（`pip install pynacl`）—— 已在 venv 中。

CLI：
    python scripts/jwks/generate_keys.py                     # 生成新密钥对
    python scripts/jwks/generate_keys.py --kid <key-id>       # 指定 key ID
    python scripts/jwks/generate_keys.py --rotate             # 保留旧密钥，追加新密钥
    python scripts/jwks/sign_manifest.py <path-to-manifest>   # 生成签名（base64）
    python scripts/jwks/sign_manifest.py <path> --apply       # 直接把 signature 写入 manifest
    python scripts/jwks/verify_manifest.py <path-to-manifest> # 用 jwks.json 验签
    python scripts/jwks/selftest.py                           # 端到端自检

产物：
    .well-known/jwks.json                                     # 公开端点（CF Pages 托管）
    .well-known/keys/private-key-<kid>.pem                    # 私钥（本地，不入 git）
    .well-known/keys/public-key-<kid>.pem                     # 公钥（备份）

安全注意：
1. 私钥**绝不入 git**。用 .gitignore 排除 `.well-known/keys/`。
2. jwks.json 只包含公钥，可以放心公开托管。
3. 密钥轮换：`--rotate` 会保留旧 keyId（标记 revoked_at），追加新 keyId。
   旧 keyId 在 `revoked_at` 设置后不再用于新签名，但 jwks.json 保留
   历史以便验证历史 manifest。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
WELL_KNOWN = REPO / ".well-known"
KEYS_DIR = WELL_KNOWN / "keys"
JWKS_PATH = WELL_KNOWN / "jwks.json"

PRIVATE_KEY_SUFFIX = ".key"
PUBLIC_KEY_SUFFIX = ".pub"


# ─────────────────────────────────────────────────────────────
# 密钥生成
# ─────────────────────────────────────────────────────────────
def _load_signing_key():
    try:
        from nacl.signing import SigningKey, VerifyKey
        return SigningKey, VerifyKey
    except ImportError as e:
        raise SystemExit(
            "缺少 PyNaCl。运行: pip install pynacl"
        ) from e


def generate_keypair(kid: str, rotate: bool = False) -> dict:
    """生成 Ed25519 密钥对并写入 .well-known/keys/ 与 jwks.json。

    rotate=True 时保留现有 jwks.json，追加新 key；否则覆盖。
    """
    SigningKey, VerifyKey = _load_signing_key()
    sk = SigningKey.generate()
    vk = sk.verify_key

    KEYS_DIR.mkdir(parents=True, exist_ok=True)
    priv_path = KEYS_DIR / f"{kid}{PRIVATE_KEY_SUFFIX}"
    pub_path = KEYS_DIR / f"{kid}{PUBLIC_KEY_SUFFIX}"
    priv_path.write_bytes(sk.encode())
    pub_path.write_bytes(vk.encode())

    # 构造 JWK
    jwk = {
        "kty": "OKP",
        "crv": "Ed25519",
        "kid": kid,
        "alg": "EdDSA",
        "use": "sig",
        "x": base64.urlsafe_b64encode(vk.encode()).rstrip(b"=").decode("ascii"),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        "revoked_at": None,
    }

    # 更新 jwks.json
    if rotate and JWKS_PATH.exists():
        existing = json.loads(JWKS_PATH.read_text(encoding="utf-8"))
        # 新密钥加入
        existing["keys"].append(jwk)
    else:
        existing = {"keys": [jwk]}

    JWKS_PATH.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")

    return {
        "kid": kid,
        "private_key_path": str(priv_path),
        "public_key_path": str(pub_path),
        "jwks_path": str(JWKS_PATH),
        "x_b64url": jwk["x"],
    }


# ─────────────────────────────────────────────────────────────
# 签名
# ─────────────────────────────────────────────────────────────
def _canonical_bytes(manifest: dict) -> bytes:
    """按 JSON 标准字段顺序生成规范化字节序列。

    排除易变字段（如 `signature` 本身）后按 key 排序序列化。
    """
    m = {k: v for k, v in manifest.items() if k != "signature"}
    return json.dumps(m, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def sign_manifest(manifest_path: Path, key_path: Path) -> str:
    """签名 manifest，返回 base64 编码的签名。"""
    SigningKey, _ = _load_signing_key()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    sk = SigningKey(key_path.read_bytes())
    canonical = _canonical_bytes(manifest)
    signature = sk.sign(canonical).signature
    return base64.b64encode(signature).decode("ascii")


def apply_signature(manifest_path: Path, key_path: Path,
                    algorithm: str = "Ed25519", kid: str = "",
                    signature: str | None = None) -> None:
    """把签名写入 manifest 的 signature 字段。"""
    if signature is None:
        signature = sign_manifest(manifest_path, key_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["signature"] = {
        "algorithm": algorithm,
        "keyId": kid,
        "payload": "canonical-utf8-json",
        "signature": signature,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")


# ─────────────────────────────────────────────────────────────
# 验证
# ─────────────────────────────────────────────────────────────
def verify_manifest(manifest_path: Path, jwks_path: Path = JWKS_PATH) -> dict:
    """用 jwks.json 中的公钥验证 manifest 签名。

    返回 {"valid": bool, "kid": str, "error": str | None}
    """
    _, VerifyKey = _load_signing_key()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    jwks = json.loads(jwks_path.read_text(encoding="utf-8"))

    sig_block = manifest.get("signature")
    if not sig_block or not isinstance(sig_block, dict):
        return {"valid": False, "kid": None, "error": "missing signature block"}

    kid = sig_block.get("keyId")
    sig_str = sig_block.get("signature")

    # 占位符检查：先于 base64 解码，避免对非 ASCII 字符串调用 b64decode 抛异常
    if not sig_str or "[占位]" in str(sig_str) or "待发布" in str(sig_str):
        return {"valid": False, "kid": kid, "error": "placeholder signature"}

    x_b64url = None
    for jwk in jwks.get("keys", []):
        if jwk.get("kid") == kid:
            if jwk.get("revoked_at"):
                return {"valid": False, "kid": kid,
                        "error": f"key {kid} revoked at {jwk['revoked_at']}"}
            x_b64url = jwk.get("x")
            break
    if not x_b64url:
        return {"valid": False, "kid": kid, "error": f"key {kid} not found in jwks.json"}

    # base64url → bytes
    padding = "=" * ((4 - len(x_b64url) % 4) % 4)
    x_bytes = base64.urlsafe_b64decode(x_b64url + padding)

    try:
        signature_bytes = base64.b64decode(sig_str)
    except Exception as e:
        return {"valid": False, "kid": kid, "error": f"invalid signature encoding: {e}"}

    canonical = _canonical_bytes(manifest)

    try:
        vk = VerifyKey(x_bytes)
        vk.verify(canonical, signature_bytes)
        return {"valid": True, "kid": kid, "error": None}
    except Exception as e:
        return {"valid": False, "kid": kid, "error": str(e)}


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────
def cmd_generate_keys(args):
    kid = args.kid or f"swarmlabs-tools-{time.strftime('%Y')}--{base64.b64encode(os.urandom(4)).decode()[:6]}"
    result = generate_keypair(kid=kid, rotate=args.rotate)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def cmd_sign(args):
    manifest = Path(args.manifest)
    key = Path(args.key)
    if args.apply:
        # 从 manifest 里读 keyId，若没有则从 key 文件名推导
        manifest_obj = json.loads(manifest.read_text(encoding="utf-8"))
        kid = manifest_obj.get("signature", {}).get("keyId") or key.stem.replace(".key", "")
        apply_signature(manifest, key, kid=kid)
        print(json.dumps({"applied": True, "manifest": str(manifest), "kid": kid}, indent=2))
    else:
        sig = sign_manifest(manifest, key)
        print(json.dumps({"signature_b64": sig, "length": len(sig)}, indent=2))
    return 0


def cmd_verify(args):
    manifest = Path(args.manifest)
    result = verify_manifest(manifest)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["valid"] else 1


def cmd_selftest(_args=None):
    """端到端自检：生成密钥 → 签名 → 验证 → 验证失败路径。"""
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    try:
        # 用一个临时 kid + 临时 jwks.json
        SigningKey, _ = _load_signing_key()
        sk = SigningKey.generate()
        vk = sk.verify_key
        kid = "selftest-" + base64.b64encode(os.urandom(4)).decode()[:6]
        x_b64url = base64.urlsafe_b64encode(vk.encode()).rstrip(b"=").decode("ascii")

        # 临时 jwks
        jwks = {"keys": [{"kty": "OKP", "crv": "Ed25519", "kid": kid,
                          "alg": "EdDSA", "use": "sig", "x": x_b64url,
                          "created_at": "2026-09-22T00:00:00+08:00", "revoked_at": None}]}
        jwks_path = tmp / "jwks.json"
        jwks_path.write_text(json.dumps(jwks, indent=2), encoding="utf-8")

        # 临时 key
        key_path = tmp / "test.key"
        key_path.write_bytes(sk.encode())

        # 临时 manifest
        manifest_path = tmp / "manifest.json"
        manifest_path.write_text(json.dumps({
            "manifestVersion": "1.0", "name": "test", "version": "1.0.0"
        }, indent=2), encoding="utf-8")

        # 签名
        sig = sign_manifest(manifest_path, key_path)
        apply_signature(manifest_path, key_path, kid=kid)

        # 验证
        r1 = verify_manifest(manifest_path, jwks_path)
        assert r1["valid"], f"签名后应验证通过，实际 {r1}"

        # 篡改 → 应失败
        bad = json.loads(manifest_path.read_text(encoding="utf-8"))
        bad["version"] = "2.0.0"
        manifest_path.write_text(json.dumps(bad, indent=2), encoding="utf-8")
        r2 = verify_manifest(manifest_path, jwks_path)
        assert not r2["valid"], f"篡改后应验证失败，实际 {r2}"

        # 占位符签名 → 应失败
        bad["signature"] = {"algorithm": "Ed25519", "keyId": kid,
                            "payload": "canonical-utf8-json", "signature": "[占位]"}
        manifest_path.write_text(json.dumps(bad, indent=2), encoding="utf-8")
        r3 = verify_manifest(manifest_path, jwks_path)
        assert not r3["valid"] and "placeholder" in (r3["error"] or ""), f"占位符应失败，实际 {r3}"

        print("PASS: selftest all checks passed")
        return 0
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="SwarmLabs JWKS 管理工具")
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate-keys", help="生成 Ed25519 密钥对并更新 jwks.json")
    g.add_argument("--kid", help="Key ID（默认自动生成）")
    g.add_argument("--rotate", action="store_true", help="追加新密钥，不覆盖旧密钥")
    g.set_defaults(func=cmd_generate_keys)

    s = sub.add_parser("sign", help="签名 manifest")
    s.add_argument("manifest", help="manifest 路径")
    s.add_argument("--key", required=True, help="私钥路径 (.key)")
    s.add_argument("--apply", action="store_true", help="直接把 signature 写入 manifest")
    s.set_defaults(func=cmd_sign)

    v = sub.add_parser("verify", help="用 jwks.json 验证 manifest")
    v.add_argument("manifest", help="manifest 路径")
    v.add_argument("--jwks", default=str(JWKS_PATH), help="jwks.json 路径")
    v.set_defaults(func=cmd_verify_with_jwks)

    st = sub.add_parser("selftest", help="端到端自检")
    st.set_defaults(func=cmd_selftest)

    args = ap.parse_args(argv)
    return args.func(args)


def cmd_verify_with_jwks(args):
    result = verify_manifest(Path(args.manifest), Path(args.jwks))
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
