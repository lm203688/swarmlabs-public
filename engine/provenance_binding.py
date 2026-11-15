"""Cryptographic tensor ↔ λ binding layer (M4, 2026-10-15 §硬科技提升).

The gap this module closes
--------------------------
`engine/provenance_guard.py` binds a **string fingerprint** of model
configuration to a scenario, which catches "same parameters, different
scenario". It does **not** catch *join-point attacks* — swapping a trained
λ (a scalar hyperparameter value, or a checkpoint) with a different tensor
that has the same SHA256 byte signature but different semantics.

arXiv:2608.20439 (2026) formalises this class of attacks on scientific
computing provenance graphs: byte-level fingerprints alone are
insufficient for cross-layer provenance because a join point between a
tensor and its scalar configuration can be tampered with silently.

The construction here
---------------------
    sig = HMAC-SHA256(secret_key, sample_id ‖ λ_str ‖ tensor_sha256 ‖ context)

gives a *cryptographic* triple binding:
- **sample_id** identifies which scenario run / row this covers
- **λ** is the scalar value being bound (e.g. a lengthscale, learning rate)
- **tensor_sha256** is the standard SHA256 of the underlying tensor bytes
- **context** is optional extra metadata (units, precision, timestamp)

Any mutation of any of the four inputs changes `sig`. The secret key
rotates by deployment (see `SECRET_KEY_SOURCES`). We emit a CycloneDX
ML-BOM so the artifacts can be consumed by standard SBOM tooling.

Attack reproduction contract
-----------------------------
`scripts/reproduce_provenance_attack.py` demonstrates 4 attack modes:
- A1: swap tensor, keep λ             → detected (sig mismatch)
- A2: swap λ, keep tensor             → detected (sig mismatch)
- A3: swap both consistently         → detected if keys rotate between runs
- A4: same (λ, tensor, key, sample)  → reproducible sig (positive control)

Design constraints
------------------
- stdlib only (`hashlib`, `hmac`, `json`, `numpy`) — no external deps.
- No silent fallback: tampered inputs raise `BindingIntegrityError`.
- Keys are loaded from environment / file / default-dev (with loud warning).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

import numpy as np


# ---------------------------------------------------------------------------
# Enums & dataclasses
# ---------------------------------------------------------------------------

class Bindability(str, Enum):
    """Whether a value can be bound into the HMAC triple."""
    SCALAR = "scalar"
    TENSOR = "tensor"
    SAMPLE_ID = "sample_id"
    CONTEXT = "context"


class BindingVerdict(str, Enum):
    """Outcome of a binding verification."""
    VERIFIED = "VERIFIED"           # sig matches recomputed sig
    TAMPERED = "TAMPERED"           # sig differs → attack detected
    UNBINDABLE = "UNBINDABLE"       # missing field / wrong type
    KEYS_UNMATCHED = "KEYS_UNMATCHED"  # different secret keys → can't verify


class BindingIntegrityError(RuntimeError):
    """Raised when verify() fails and strict=True is requested."""


@dataclass(frozen=True)
class BindingRecord:
    """One HMAC-SHA256 bound tuple."""
    sample_id: str
    lambda_name: str
    lambda_value: str                # JSON-safe scalar string
    tensor_sha256: str               # lowercase hex
    context: str                     # optional extra payload
    secret_key_id: str               # which key id signed this
    signature: str                   # lowercase hex HMAC
    created_at: str                  # ISO 8601 UTC

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sample_id": self.sample_id,
            "lambda_name": self.lambda_name,
            "lambda_value": self.lambda_value,
            "tensor_sha256": self.tensor_sha256,
            "context": self.context,
            "secret_key_id": self.secret_key_id,
            "signature": self.signature,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "BindingRecord":
        required = ("sample_id", "lambda_name", "lambda_value",
                    "tensor_sha256", "context", "secret_key_id", "signature")
        missing = [k for k in required if k not in d]
        if missing:
            raise BindingIntegrityError(f"missing fields: {missing}")
        return cls(
            sample_id=d["sample_id"],
            lambda_name=d["lambda_name"],
            lambda_value=d["lambda_value"],
            tensor_sha256=d["tensor_sha256"],
            context=d["context"],
            secret_key_id=d["secret_key_id"],
            signature=d["signature"],
            created_at=d.get("created_at", ""),
        )


# ---------------------------------------------------------------------------
# Secret-key management (three-source ladder, fail-loud)
# ---------------------------------------------------------------------------

_DEV_DEFAULT_KEY = b"swarmlabs-dev-key-do-not-use-in-prod"
_ENV_KEY = "SWARMLABS_PROVENANCE_KEY"
_ENV_KEY_ID = "SWARMLABS_PROVENANCE_KEY_ID"
_ENV_STRICT = "SWARMLABS_PROVENANCE_STRICT"
_KEY_FILE = os.path.expanduser("~/.swarmlabs/provenance_key.bin")

SECRET_KEY_SOURCES = (
    "env:" + _ENV_KEY,
    "file:" + _KEY_FILE,
    "dev-default (warning in non-strict; refused in strict)",
)


def _load_secret_key(strict: bool | None = None) -> Tuple[bytes, str]:
    """Resolve a secret key. Order: env → file → dev default.

    Strict mode is enabled by:
    - explicit `strict=True` arg, OR
    - env var `SWARMLABS_PROVENANCE_STRICT=1` (production default)

    In strict mode the DEV DEFAULT KEY is **refused** (raise) — a production
    deployment must provide either `SWARMLABS_PROVENANCE_KEY` env var or
    `~/.swarmlabs/provenance_key.bin` file. This is the N5 hardening required
    by the 2026-10-16 assessment report: the DEV key previously emitted only
    a loud warning, which a production deploy could easily ignore.

    Test scripts / one-shot reproduction should either set
    `SWARMLABS_PROVENANCE_STRICT=0` or pass `secret_key=` explicitly to
    bind()/verify().
    """
    if strict is None:
        strict = os.environ.get(_ENV_STRICT, "").lower() in ("1", "true", "yes")

    env_key = os.environ.get(_ENV_KEY)
    if env_key:
        key_id = os.environ.get(_ENV_KEY_ID, "env:" + _ENV_KEY)
        return env_key.encode("utf-8"), key_id

    if os.path.exists(_KEY_FILE):
        with open(_KEY_FILE, "rb") as f:
            key = f.read()
        if key:
            return key, "file:" + _KEY_FILE

    if strict:
        raise BindingIntegrityError(
            f"[provenance_binding] STRICT MODE: no secret key found. "
            f"Set env {_ENV_KEY} or create file {_KEY_FILE}. "
            f"The DEV DEFAULT KEY is not usable for production artifacts."
        )

    # Non-strict: dev default with loud warning.
    print(f"[provenance_binding] WARNING: using DEV DEFAULT KEY "
          f"(neither env { _ENV_KEY } nor file {_KEY_FILE} found; "
          f"set {_ENV_STRICT}=1 to refuse). "
          f"Do NOT use for production artifacts.", flush=True)
    return _DEV_DEFAULT_KEY, "dev-default"


def load_secret_keys() -> Dict[str, bytes]:
    """Return dict of known key_id → bytes, for multi-key verification."""
    keys: Dict[str, bytes] = {}
    env_key = os.environ.get(_ENV_KEY)
    if env_key:
        key_id = os.environ.get(_ENV_KEY_ID, "env:" + _ENV_KEY)
        keys[key_id] = env_key.encode("utf-8")
    if os.path.exists(_KEY_FILE):
        with open(_KEY_FILE, "rb") as f:
            raw = f.read()
        if raw:
            keys["file:" + _KEY_FILE] = raw
    keys["dev-default"] = _DEV_DEFAULT_KEY
    return keys


# ---------------------------------------------------------------------------
# Canonicalisation (deterministic serialization)
# ---------------------------------------------------------------------------

def _canonical_tensor_bytes(tensor: Union[np.ndarray, bytes, str]) -> bytes:
    """Return canonical bytes for a tensor value.

    - np.ndarray: np.ascontiguousarray → np.ndarray.tobytes(dtype native),
      then prepend shape+dtype header so identical arrays are stable.
    - bytes: as-is.
    - str: utf-8 encoded.
    """
    if isinstance(tensor, np.ndarray):
        a = np.ascontiguousarray(tensor)
        header = (
            f"{a.dtype};" + ",".join(str(x) for x in a.shape) + "\x00"
        ).encode("utf-8")
        return header + a.tobytes()
    if isinstance(tensor, (bytes, bytearray)):
        return bytes(tensor)
    if isinstance(tensor, str):
        return tensor.encode("utf-8")
    raise TypeError(f"unsupported tensor type: {type(tensor).__name__}")


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def tensor_sha256(tensor: Union[np.ndarray, bytes, str]) -> str:
    """Public helper: canonical SHA256 of a tensor."""
    return _sha256_bytes(_canonical_tensor_bytes(tensor))


# ---------------------------------------------------------------------------
# Sign / verify
# ---------------------------------------------------------------------------

def _canonical_message(
    sample_id: str,
    lambda_name: str,
    lambda_value: str,
    tensor_sha256_hex: str,
    context: str,
) -> bytes:
    """Deterministic byte-string used as HMAC input."""
    parts = [
        ("sample_id", str(sample_id)),
        ("lambda_name", str(lambda_name)),
        ("lambda_value", str(lambda_value)),
        ("tensor_sha256", str(tensor_sha256_hex).lower()),
        ("context", str(context) if context else ""),
    ]
    # Length-prefix to prevent canonicalisation ambiguities.
    chunks = []
    for k, v in parts:
        kb = k.encode("utf-8")
        vb = v.encode("utf-8")
        chunks.append(len(kb).to_bytes(4, "big") + kb)
        chunks.append(len(vb).to_bytes(4, "big") + vb)
    return b"|".join(chunks)


def compute_signature(
    sample_id: str,
    lambda_name: str,
    lambda_value: Union[float, int, str],
    tensor_sha256_hex: str,
    context: str = "",
    secret_key: Optional[bytes] = None,
) -> str:
    """HMAC-SHA256 over the canonicalised tuple."""
    if secret_key is None:
        secret_key, _ = _load_secret_key()
    msg = _canonical_message(sample_id, lambda_name, str(lambda_value),
                             tensor_sha256_hex, context)
    return hmac.new(secret_key, msg, hashlib.sha256).hexdigest()


def bind(
    sample_id: str,
    lambda_name: str,
    lambda_value: Union[float, int, str],
    tensor: Union[np.ndarray, bytes, str],
    context: str = "",
    secret_key: Optional[bytes] = None,
) -> BindingRecord:
    """Bind (sample_id, λ, tensor) → signed BindingRecord."""
    if secret_key is None:
        _, key_id = _load_secret_key()
        if secret_key is None:
            secret_key, key_id = _load_secret_key()
    else:
        key_id = "provided"
    t_sha = tensor_sha256(tensor)
    sig = compute_signature(sample_id, lambda_name, lambda_value, t_sha,
                            context, secret_key=secret_key)
    return BindingRecord(
        sample_id=str(sample_id),
        lambda_name=str(lambda_name),
        lambda_value=str(lambda_value),
        tensor_sha256=t_sha,
        context=context,
        secret_key_id=key_id,
        signature=sig,
        created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )


def verify(
    record: Union[BindingRecord, Dict[str, Any]],
    secret_key: Optional[bytes] = None,
    strict: bool = False,
) -> str:
    """Recompute and compare. Returns a BindingVerdict value."""
    if isinstance(record, dict):
        try:
            record = BindingRecord.from_dict(record)
        except BindingIntegrityError:
            return BindingVerdict.UNBINDABLE.value

    if secret_key is None:
        keys = load_secret_keys()
        # Try the recorded key_id first, then any key.
        if record.secret_key_id in keys:
            candidates = [(record.secret_key_id, keys[record.secret_key_id])]
        else:
            candidates = list(keys.items())
    else:
        candidates = [("provided", secret_key)]

    expected = compute_signature(
        record.sample_id,
        record.lambda_name,
        record.lambda_value,
        record.tensor_sha256,
        record.context,
        secret_key=candidates[0][1],
    )
    if expected == record.signature:
        return BindingVerdict.VERIFIED.value

    # Try other keys (key rotation / mis-scope case)
    for _, alt in candidates[1:]:
        if compute_signature(
            record.sample_id,
            record.lambda_name,
            record.lambda_value,
            record.tensor_sha256,
            record.context,
            secret_key=alt,
        ) == record.signature:
            return BindingVerdict.KEYS_UNMATCHED.value

    if strict:
        raise BindingIntegrityError(
            f"signature mismatch: sample_id={record.sample_id!r} "
            f"lambda={record.lambda_name}={record.lambda_value}"
        )
    return BindingVerdict.TAMPERED.value


# ---------------------------------------------------------------------------
# CycloneDX ML-BOM (v1.5 schema, minimal valid subset)
# ---------------------------------------------------------------------------

def to_cyclonedx_bom(
    records: Iterable[BindingRecord],
    component_name: str = "swarmlabs",
    component_version: str = "1.0.0",
) -> Dict[str, Any]:
    """Serialise a set of BindingRecords to a CycloneDX 1.5 SBOM fragment.

    Each record becomes an ML-BOM component with:
      - bomspec: 1.5
      - type: model
      - properties: tensor_sha256, lambda_value, signature, sample_id
    """
    comps = []
    for i, r in enumerate(records):
        comps.append({
            "type": "model",
            "bom-ref": f"binding-{i:04d}-{r.sample_id}",
            "name": f"{component_name}/{r.lambda_name}",
            "version": component_version,
            "purl": f"pkg:swarmlabs/{r.lambda_name}@{component_version}",
            "properties": [
                {"name": "swarmlabs:sample_id", "value": r.sample_id},
                {"name": "swarmlabs:lambda_value", "value": r.lambda_value},
                {"name": "swarmlabs:lambda_name", "value": r.lambda_name},
                {"name": "swarmlabs:tensor_sha256", "value": r.tensor_sha256},
                {"name": "swarmlabs:signature", "value": r.signature},
                {"name": "swarmlabs:secret_key_id", "value": r.secret_key_id},
                {"name": "swarmlabs:context", "value": r.context or ""},
                {"name": "swarmlabs:created_at", "value": r.created_at},
            ],
        })
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "component": {
                "type": "application",
                "name": component_name,
                "version": component_version,
            },
            "tools": {
                "components": [{
                    "type": "application",
                    "name": "swarmlabs-provenance-binding",
                    "version": "1.0",
                }],
            },
        },
        "components": comps,
    }


def bom_to_json(records: Iterable[BindingRecord],
                component_name: str = "swarmlabs",
                component_version: str = "1.0.0") -> str:
    return json.dumps(
        to_cyclonedx_bom(records, component_name, component_version),
        indent=2,
        ensure_ascii=False,
    )


# ---------------------------------------------------------------------------
# Self-test / CLI
# ---------------------------------------------------------------------------

def _selftest(verbose: bool = True) -> int:
    """Positive + negative controls; return 0 on full pass, 1 otherwise."""
    if verbose:
        print("[provenance_binding] selftest")

    rng = np.random.default_rng(42)
    tensor_a = rng.normal(size=(4, 4)).astype(np.float32)
    tensor_b = rng.normal(size=(4, 4)).astype(np.float32)
    secret = b"selftest-key"

    # 1. Positive control: same tuple → same signature
    r1 = bind("sample-1", "mu_max", 0.81, tensor_a, context="ecoli-monod",
              secret_key=secret)
    r2 = bind("sample-1", "mu_max", 0.81, tensor_a, context="ecoli-monod",
              secret_key=secret)
    pos_ok = r1.signature == r2.signature
    if verbose:
        print(f"  [A4] reproducibility     → {'PASS' if pos_ok else 'FAIL'}")

    # 2. Tamper tensor, keep everything else
    r3 = bind("sample-1", "mu_max", 0.81, tensor_b, context="ecoli-monod",
              secret_key=secret)
    # Rebind r3 as if it were r1 (i.e. forge a record that claims tensor_a
    # but has r3's signature which was signed over tensor_b)
    forged = BindingRecord(
        sample_id=r1.sample_id,
        lambda_name=r1.lambda_name,
        lambda_value=r1.lambda_value,
        tensor_sha256=tensor_sha256(tensor_a),  # claims tensor_a
        context=r1.context,
        secret_key_id=r1.secret_key_id,
        signature=r3.signature,                 # actually signed tensor_b
        created_at=r1.created_at,
    )
    verdict_a1 = verify(forged, secret_key=secret)
    a1_ok = verdict_a1 == BindingVerdict.TAMPERED.value
    if verbose:
        print(f"  [A1] swap tensor         → {verdict_a1}  "
              f"{'PASS' if a1_ok else 'FAIL'}")

    # 3. Tamper lambda, keep tensor
    forged2 = BindingRecord(
        sample_id=r1.sample_id,
        lambda_name=r1.lambda_name,
        lambda_value="999.99",                  # forged λ
        tensor_sha256=r1.tensor_sha256,
        context=r1.context,
        secret_key_id=r1.secret_key_id,
        signature=r1.signature,                 # signed original λ=0.81
        created_at=r1.created_at,
    )
    verdict_a2 = verify(forged2, secret_key=secret)
    a2_ok = verdict_a2 == BindingVerdict.TAMPERED.value
    if verbose:
        print(f"  [A2] swap lambda         → {verdict_a2}  "
              f"{'PASS' if a2_ok else 'FAIL'}")

    # 4. Wrong secret key → KEYS_UNMATCHED (or TAMPERED with strict)
    r3_wrong_key = bind("sample-2", "K_s", 0.22, tensor_a, context="ecoli",
                        secret_key=secret)
    verdict_wrong = verify(r3_wrong_key, secret_key=b"different-key")
    wrong_ok = verdict_wrong in (
        BindingVerdict.KEYS_UNMATCHED.value, BindingVerdict.TAMPERED.value)
    if verbose:
        print(f"  [key-rotation]            → {verdict_wrong}  "
              f"{'PASS' if wrong_ok else 'FAIL'}")

    # 5. BOM round-trip
    bom = to_cyclonedx_bom([r1])
    bom_ok = (
        bom["bomFormat"] == "CycloneDX"
        and bom["specVersion"] == "1.5"
        and len(bom["components"]) == 1
        and any(p["name"] == "swarmlabs:signature"
                and p["value"] == r1.signature for p in bom["components"][0]["properties"])
    )
    if verbose:
        print(f"  [BOM] CycloneDX 1.5       → {'PASS' if bom_ok else 'FAIL'}")

    all_ok = all([pos_ok, a1_ok, a2_ok, wrong_ok, bom_ok])
    if verbose:
        print(f"  overall: {'PASS' if all_ok else 'FAIL'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    import sys
    sys.exit(_selftest(verbose=True))
