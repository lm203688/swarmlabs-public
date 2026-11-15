#!/usr/bin/env python
"""Reproduction of arXiv:2608.20439 join-point attacks on SwarmLabs provenance.

背景
----
arXiv:2608.20439 (2026) 指出：科学计算溯源图上，"字节层 SHA256 指纹"（如我们
现有的 provenance_guard）能抓到「同参数、不同场景」，但抓不到 join-point
攻击——即张量与 λ 之间的绑定被静默替换，两侧字节指纹都合法，唯独绑定关系
被伪造。

M4（板块 05 P0）用 HMAC-SHA256 三元绑定 (sample_id, λ, tensor_sha256)
+ 可轮换密钥解决此问题。本脚本用**真实攻击场景**验证：

攻击模式（对齐 §M4 要求"负面控制先"）：
    A1  swap_tensor    : 换掉张量、保留 λ 和签名        → 应该被检测
    A2  swap_lambda    : 换掉 λ、保留张量和签名        → 应该被检测
    A3  rotate_key     : 用不同密钥签同一三元           → 应该被检测
    A4  positive       : 同三元同密钥                    → 应该可复现（正对照）

用法：
    python scripts/reproduce_provenance_attack.py
    python scripts/reproduce_provenance_attack.py --write-bom out.sbm

产物：
    data/provenance_attack_reproduction.json   报告
    [可选] 命令行指定的 .sbom                  CycloneDX 1.5 ML-BOM 文件

设计约束：
- **无降级**：任何攻击检测失败 → exit 1（阻断 CI）。
- 只用 stdlib + numpy（对齐 §M4 零外部依赖要求）。
- 密钥只用于本脚本内验证，不落 git；真实部署用 SWARMLABS_PROVENANCE_KEY。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List

import numpy as np

os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

DATA = os.path.join(ROOT, "data")

from engine.provenance_binding import (  # noqa: E402
    BindingRecord,
    BindingVerdict,
    bind,
    bom_to_json,
    compute_signature,
    tensor_sha256,
    verify,
)


# ─────────────────────────────────────────────────────────────────────────
# 攻击复现场景
# ─────────────────────────────────────────────────────────────────────────

def _make_scenario(seed: int = 42) -> Dict[str, Any]:
    """构造一个仿真的科学实验场景（E. coli Monod 生长）。"""
    rng = np.random.default_rng(seed)
    # 训练集张量（模拟 36 个浓度条件下的生长速率）
    tensor_growth_rate = rng.normal(0.002, 0.0005, size=(36, 4)).astype(np.float32)
    tensor_growth_rate_b = rng.normal(0.002, 0.0005, size=(36, 4)).astype(np.float32)
    return {
        "sample_id": "ecoli-monod-2026-10-15-001",
        "lambda_name": "mu_max",
        "lambda_value": 0.002667,          # 1/min（M1 提取的真实值）
        "context": "T=29°C;n_conditions=36;source=zenodo:19416053",
        "tensor_growth_rate": tensor_growth_rate,
        "tensor_growth_rate_b": tensor_growth_rate_b,
    }


def attack_A1_swap_tensor(scen: Dict[str, Any], secret: bytes) -> Dict[str, Any]:
    """A1: 攻击者把 tensor_b 的哈希填到记录里，签名却是 tensor_a 签的。"""
    r_legit = bind(scen["sample_id"], scen["lambda_name"],
                    scen["lambda_value"], scen["tensor_growth_rate"],
                    context=scen["context"], secret_key=secret)
    # 攻击者：伪造记录，声称 tensor_a 但签名取自 tensor_b
    forged = BindingRecord(
        sample_id=r_legit.sample_id,
        lambda_name=r_legit.lambda_name,
        lambda_value=r_legit.lambda_value,
        tensor_sha256=tensor_sha256(scen["tensor_growth_rate"]),  # 声称 A
        context=r_legit.context,
        secret_key_id=r_legit.secret_key_id,
        signature=compute_signature(
            r_legit.sample_id, r_legit.lambda_name,
            r_legit.lambda_value,
            tensor_sha256(scen["tensor_growth_rate_b"]),  # 实际签 B
            r_legit.context, secret_key=secret,
        ),
        created_at=r_legit.created_at,
    )
    verdict = verify(forged, secret_key=secret)
    return {
        "attack_id": "A1_swap_tensor",
        "description": "same λ, swap tensor (attacker forges record to claim tensor_a but signs tensor_b)",
        "expected_verdict": BindingVerdict.TAMPERED.value,
        "actual_verdict": verdict,
        "detected": verdict == BindingVerdict.TAMPERED.value,
        "legit_signature": r_legit.signature[:16] + "...",
        "forged_signature": forged.signature[:16] + "...",
        "legit_tensor_sha256": r_legit.tensor_sha256[:16] + "...",
        "other_tensor_sha256": tensor_sha256(scen["tensor_growth_rate_b"])[:16] + "...",
    }


def attack_A2_swap_lambda(scen: Dict[str, Any], secret: bytes) -> Dict[str, Any]:
    """A2: 攻击者把 λ 从 0.002667 改成 999.99，签名保留原值。"""
    r_legit = bind(scen["sample_id"], scen["lambda_name"],
                    scen["lambda_value"], scen["tensor_growth_rate"],
                    context=scen["context"], secret_key=secret)
    forged = BindingRecord(
        sample_id=r_legit.sample_id,
        lambda_name=r_legit.lambda_name,
        lambda_value="999.99",              # 攻击者改 λ
        tensor_sha256=r_legit.tensor_sha256,
        context=r_legit.context,
        secret_key_id=r_legit.secret_key_id,
        signature=r_legit.signature,        # 保留原签名
        created_at=r_legit.created_at,
    )
    verdict = verify(forged, secret_key=secret)
    return {
        "attack_id": "A2_swap_lambda",
        "description": "same tensor, swap λ (attacker rewrites scalar, keeps signature)",
        "expected_verdict": BindingVerdict.TAMPERED.value,
        "actual_verdict": verdict,
        "detected": verdict == BindingVerdict.TAMPERED.value,
        "legit_lambda": r_legit.lambda_value,
        "forged_lambda": forged.lambda_value,
    }


def attack_A3_rotate_key(scen: Dict[str, Any], secret: bytes,
                          attacker_secret: bytes) -> Dict[str, Any]:
    """A3: 攻击者用不同密钥签同一三元（模拟密钥轮换/未同步）。"""
    r_attacker = bind(scen["sample_id"], scen["lambda_name"],
                       scen["lambda_value"], scen["tensor_growth_rate"],
                       context=scen["context"], secret_key=attacker_secret)
    verdict = verify(r_attacker, secret_key=secret)  # 用原密钥验
    # 应该 FAIL（TAMPERED 或 KEYS_UNMATCHED，本脚本 secret 单一所以是 TAMPERED）
    return {
        "attack_id": "A3_rotate_key",
        "description": "same triple, attacker uses different key (unauthorized key rotation)",
        "expected_verdict": BindingVerdict.TAMPERED.value,
        "actual_verdict": verdict,
        "detected": verdict in (BindingVerdict.TAMPERED.value,
                                BindingVerdict.KEYS_UNMATCHED.value),
        "victim_secret_key_id": "victim-prod-key",
        "attacker_secret_key_id": "attacker-compromised-key",
    }


def control_A4_positive(scen: Dict[str, Any], secret: bytes) -> Dict[str, Any]:
    """A4 正对照：同三元同密钥 → 两次签名完全一致。"""
    r1 = bind(scen["sample_id"], scen["lambda_name"],
               scen["lambda_value"], scen["tensor_growth_rate"],
               context=scen["context"], secret_key=secret)
    r2 = bind(scen["sample_id"], scen["lambda_name"],
               scen["lambda_value"], scen["tensor_growth_rate"],
               context=scen["context"], secret_key=secret)
    return {
        "control_id": "A4_positive",
        "description": "same triple, same key, repeated binding → deterministic signature",
        "sig1": r1.signature,
        "sig2": r2.signature,
        "reproducible": r1.signature == r2.signature,
        "verify_roundtrip": verify(r1, secret_key=secret) == BindingVerdict.VERIFIED.value,
    }


# ─────────────────────────────────────────────────────────────────────────
# 阴性对照（Negative Control）—— 评估报告 §硬科技提升要求
# ─────────────────────────────────────────────────────────────────────────

def _legacy_sha256_fingerprint_only(tensor: "np.ndarray") -> str:
    """旧方案的指纹：只哈希张量字节，完全忽略 (sample_id, λ, context)。

    这是 `engine/provenance_guard.py` 的等价物：只把 tensor 值本身当指纹。
    对于 A2 (swap_λ) 攻击，两侧张量字节完全相同 → 指纹相同 → 旧方案判 CLEAN。
    """
    import hashlib
    b = np.ascontiguousarray(tensor, dtype=np.float64).tobytes()
    return hashlib.sha256(b).hexdigest()


def negative_control_A2_swap_lambda(scen: Dict[str, Any]) -> Dict[str, Any]:
    """阴性对照：显式记录「旧 SHA256-only 方案」漏检 A2 的证据。

    这是评估报告 §硬科技提升的强制要求——之前只有正向控制（新 HMAC 抓到
    A1/A2/A3），没有记录「旧方案漏检同一 A2」这个对照证据。

    对照逻辑：
    - legit record：(sample_id, λ=0.002667, tensor)
    - A2 forged record：(sample_id, λ=999.99, tensor)  ← 张量字节完全相同
    - 旧方案指纹 = SHA256(tensor bytes only) → 两侧相同 → 判 CLEAN (漏检!)
    - 新方案 = HMAC(secret, sample_id, λ, tensor_sha256, context) → 因为 λ 变了，
      HMAC 变了 → 判 TAMPERED (抓到)
    """
    legit_tensor_sha256 = _legacy_sha256_fingerprint_only(scen["tensor_growth_rate"])

    # A2 forged record：改 λ 但保留张量
    forged_record = {
        "sample_id": scen["sample_id"],
        "lambda_name": scen["lambda_name"],
        "lambda_value": "999.99",            # 攻击者改 λ
        "tensor_sha256": tensor_sha256(scen["tensor_growth_rate"]),
        "context": scen["context"],
    }
    forged_tensor_sha256 = _legacy_sha256_fingerprint_only(scen["tensor_growth_rate"])

    legacy_catch = (legit_tensor_sha256 != forged_tensor_sha256)  # 旧方案能否抓到
    legacy_verdict = "CLEAN" if not legacy_catch else "TAMPERED"

    # 新方案对同一 A2 攻击的判定
    from engine.provenance_binding import BindingRecord, verify, compute_signature
    r_legit = bind(scen["sample_id"], scen["lambda_name"],
                    scen["lambda_value"], scen["tensor_growth_rate"],
                    context=scen["context"], secret_key=b"reproduction-victim-prod-key")
    forged = BindingRecord(
        sample_id=r_legit.sample_id,
        lambda_name=r_legit.lambda_name,
        lambda_value="999.99",
        tensor_sha256=r_legit.tensor_sha256,
        context=r_legit.context,
        secret_key_id=r_legit.secret_key_id,
        signature=r_legit.signature,
        created_at=r_legit.created_at,
    )
    new_verdict = verify(forged, secret_key=b"reproduction-victim-prod-key")

    return {
        "control_id": "A2_negctl_legacy_sha256",
        "purpose": (
            "记录旧 SHA256-only 方案漏检 A2_swap_lambda 的直接证据，"
            "让新 HMAC 方案的价值可审计。评估报告 §硬科技提升明确要求此对照。"
        ),
        "attack": "A2_swap_lambda (same tensor bytes, different λ)",
        "legacy_scheme": {
            "scheme": "SHA256-of-tensor-bytes-only (provenance_guard.py 等价)",
            "legit_fingerprint": legit_tensor_sha256,
            "forged_fingerprint": forged_tensor_sha256,
            "fingerprint_changed": bool(legit_tensor_sha256 != forged_tensor_sha256),
            "verdict": legacy_verdict,
            "attacked_catches": bool(legacy_catch),
        },
        "new_scheme": {
            "scheme": "HMAC-SHA256(secret, sample_id ‖ λ ‖ tensor_sha256 ‖ context)",
            "legit_signature": r_legit.signature,
            "forged_signature_retained": forged.signature,
            "verdict": new_verdict,
            "attack_catches": (new_verdict == BindingVerdict.TAMPERED.value),
        },
        "finding": (
            "旧 SHA256-only 方案：A2_swap_lambda 攻击下两侧指纹字节相同 → "
            "判 CLEAN（漏检，attack_succeeded）。新 HMAC 方案把 λ 纳入签名 → "
            "判 TAMPERED（抓到，attack_blocked）。这就是我们部署 HMAC 而非纯 SHA256 的动机。"
        ),
        "attack_succeeded_under_legacy": (legacy_verdict == "CLEAN"),
        "attack_blocked_under_new": (new_verdict == BindingVerdict.TAMPERED.value),
    }


# ─────────────────────────────────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-bom", type=str, default=None,
                        help="optional: write CycloneDX 1.5 ML-BOM to this path")
    parser.add_argument("--secret", type=str,
                        default="reproduction-victim-prod-key",
                        help="victim's secret key (default is a fixed reproduction string)")
    parser.add_argument("--attacker-secret", type=str,
                        default="attacker-compromised-key",
                        help="attacker's secret key for A3 rotation scenario")
    args = parser.parse_args()

    victim_secret = args.secret.encode("utf-8")
    attacker_secret = args.attacker_secret.encode("utf-8")

    print("=" * 72)
    print("arXiv:2608.20439 join-point attack reproduction (M4 §硬科技提升)")
    print("=" * 72)
    print(f"victim secret   : {args.secret!r}")
    print(f"attacker secret : {args.attacker_secret!r}")
    print()

    scen = _make_scenario(seed=42)
    t0 = time.time()

    attacks: List[Dict[str, Any]] = []
    attacks.append(attack_A1_swap_tensor(scen, victim_secret))
    attacks.append(attack_A2_swap_lambda(scen, victim_secret))
    attacks.append(attack_A3_rotate_key(scen, victim_secret, attacker_secret))
    control = control_A4_positive(scen, victim_secret)
    negctl = negative_control_A2_swap_lambda(scen)

    elapsed = time.time() - t0

    # 打印
    for a in attacks:
        tag = "DETECTED" if a.get("detected") else "MISSED"
        color = "✓" if a.get("detected") else "✗"
        print(f"  {color} [{a['attack_id']:<16}] → {a['actual_verdict']}  ({tag})")
    print(f"  {'✓' if control['reproducible'] else '✗'} [{control['control_id']:<16}] "
          f"→ reproducible={control['reproducible']}, "
          f"verify_roundtrip={control['verify_roundtrip']}")

    # 阴性对照打印（评估报告要求）
    print()
    print(f"  [阴性对照] legacy SHA256-only:  verdict={negctl['legacy_scheme']['verdict']}  "
          f"attack_succeeded_under_legacy={negctl['attack_succeeded_under_legacy']}  ← 漏检证据")
    print(f"  [阴性对照] new HMAC binding   :  verdict={negctl['new_scheme']['verdict']}  "
          f"attack_blocked_under_new={negctl['attack_blocked_under_new']}  ← 抓到证据")

    # 汇总（含阴性对照）
    n_detected = sum(1 for a in attacks if a.get("detected"))
    n_total = len(attacks)
    negctl_ok = negctl["attack_succeeded_under_legacy"] and negctl["attack_blocked_under_new"]
    all_ok = (
        n_detected == n_total
        and control["reproducible"] and control["verify_roundtrip"]
        and negctl_ok
    )

    print()
    print(f"汇总: {n_detected}/{n_total} attacks detected "
          f"+ negative_control ok={negctl_ok} "
          f"→ {'PASS' if all_ok else 'FAIL'}  总耗时 {elapsed:.2f}s")

    # 写报告
    report = {
        "version": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "paper_ref": "arXiv:2608.20439",
        "report_ref": "reports/assessment/SwarmLabs_硬科技提升执行方案_2026-10-15.md §M4",
        "module": "engine/provenance_binding.py",
        "scenario": {
            "sample_id": scen["sample_id"],
            "lambda_name": scen["lambda_name"],
            "lambda_value": scen["lambda_value"],
            "context": scen["context"],
            "tensor_shape": list(scen["tensor_growth_rate"].shape),
            "tensor_dtype": str(scen["tensor_growth_rate"].dtype),
        },
        "attacks": attacks,
        "positive_control": control,
        "negative_control": negctl,
        "summary": {
            "attacks_detected": n_detected,
            "attacks_total": n_total,
            "positive_control_reproducible": control["reproducible"],
            "positive_control_roundtrip": control["verify_roundtrip"],
            "negative_control_legacy_missed": negctl["attack_succeeded_under_legacy"],
            "negative_control_new_caught": negctl["attack_blocked_under_new"],
            "all_ok": all_ok,
            "elapsed_sec": round(elapsed, 2),
        },
    }
    path = os.path.join(DATA, "provenance_attack_reproduction.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n报告已写入: {path}")

    # 可选：写 CycloneDX ML-BOM
    if args.write_bom:
        r_legit = bind(scen["sample_id"], scen["lambda_name"],
                        scen["lambda_value"], scen["tensor_growth_rate"],
                        context=scen["context"], secret_key=victim_secret)
        bom = bom_to_json([r_legit], component_name="swarmlabs",
                          component_version="1.0.0")
        with open(args.write_bom, "w", encoding="utf-8") as f:
            f.write(bom)
        print(f"CycloneDX 1.5 ML-BOM 已写入: {args.write_bom}")

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
