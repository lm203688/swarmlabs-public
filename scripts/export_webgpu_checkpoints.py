#!/usr/bin/env python
"""Export data/small_models/*.npz → frontend_fixed/models/*.json for browser-side WebGPU inference.

The npz format stores a 2-layer MLP surrogate:
    h = ReLU(W1 @ x + b1)   # W1: (H, D), b1: (H,)
    y = W2 @ h + b2          # W2: (1, H), b2: (1,)
where D is the input dimension (1, 2, or 6 depending on scenario).

Each JSON is a compact, human-readable checkpoint the WebGPU compute shader can load
and feed directly into a WGSL storage buffer. Every value is stored as float32 to
match GPU memory layout and avoid float64→float32 roundtrip on the client.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import struct
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
SRC_DIR = REPO / "data" / "small_models"
OUT_DIR = REPO / "frontend_fixed" / "models"

# Human-readable metadata for each model — surfaced in gpu.html's scenario panel.
# Models not listed here get auto-generated descriptions from their key prefix.
MODEL_DESCRIPTIONS = {
    "surrogate_forrester": {
        "domain": "benchmark",
        "description": "Forrester 1D (f(x) = sin(5πx) + 12πx³ + 51π²x⁴ + 600πx), the classical Gaussian process benchmark.",
    },
    "surrogate_branin": {
        "domain": "optimization",
        "description": "Branin 2D (non-convex multimodal optimizer benchmark with three global minima).",
    },
    "surrogate_hartmann6": {
        "domain": "optimization",
        "description": "Hartmann 6D (high-dimensional multimodal function used in evolutionary optimization literature).",
    },
    "bio_logistic": {
        "domain": "biology",
        "description": "Logistic population growth (dP/dt = r·P·(1−P/K)) as a learned regression surrogate.",
    },
    "chem_langmuir": {
        "domain": "chemistry",
        "description": "Langmuir adsorption isotherm (θ = KP / (1 + KP)) fitted with an MLP surrogate.",
    },
    "chem_reactor_conversion": {
        "domain": "chemistry",
        "description": "Continuous stirred-tank reactor conversion (CSTR mass balance) as a 2-input surrogate.",
    },
    "llm_scaling": {
        "domain": "machine_learning",
        "description": "LLM scaling-law regression (log(loss) ≈ A·N^−α + B) — Chinchilla-style scaling fit.",
    },
    "microbio_inhibition": {
        "domain": "biology",
        "description": "Microbial growth inhibition (bacterial lag + exponential phase) as a 1D regression surrogate.",
    },
    "microbio_monod": {
        "domain": "biology",
        "description": "Monod microbial kinetics (μ = μmax·S/(Ks+S)) with substrate-limited regime.",
    },
    "pde_heat1d": {
        "domain": "physics",
        "description": "1D heat equation (∂u/∂t = κ·∂²u/∂x²) regression surrogate over a discrete time step.",
    },
}

# Domain inference from scenario key prefix (fallback for unlisted models)
DOMAIN_BY_PREFIX = {
    "bio_": "biology", "micro_": "biology", "microbio_": "biology",
    "chem_": "chemistry", "cheminfo_": "chemistry",
    "opt_": "optimization", "surrogate_": "benchmark",
    "llm_": "machine_learning", "pde_": "physics", "fin_": "finance",
    "eco_": "ecology", "neuro_": "neuroscience", "energy_": "energy",
    "mol_": "chemistry", "heat_": "physics", "ode_": "dynamics",
    "resp_": "statistics", "quant_": "finance",
}


def infer_domain(name: str) -> str:
    """Infer domain from scenario key prefix."""
    for prefix, domain in DOMAIN_BY_PREFIX.items():
        if name.startswith(prefix):
            return domain
    return "general"


def infer_description(name: str, input_dim: int) -> str:
    """Generate description from scenario key if not in MODEL_DESCRIPTIONS."""
    if name in MODEL_DESCRIPTIONS:
        return MODEL_DESCRIPTIONS[name]["description"]
    # Parse key: prefix_rest_dim
    parts = name.split("_")
    domain = infer_domain(name)
    # Capitalize domain
    domain_label = domain.replace("_", " ").title()
    return f"{name.replace('_', ' ').title()} — {domain_label} {input_dim}D surrogate model."


def f32_array(arr: np.ndarray) -> list[float]:
    """Serialize ndarray as float32 list (GPU-ready)."""
    return [float(v) for v in arr.astype(np.float32).ravel()]


def export_one(npz_path: Path) -> dict:
    name = npz_path.stem
    data = np.load(npz_path)
    W1, b1 = data["W1"], data["b1"]
    W2, b2 = data["W2"], data["b2"]

    hidden, input_dim = int(W1.shape[0]), int(W1.shape[1])
    meta = MODEL_DESCRIPTIONS.get(name, {})

    # Infer domain and description if not in MODEL_DESCRIPTIONS
    domain = meta.get("domain", infer_domain(name))
    description = meta.get("description", infer_description(name, input_dim))

    # Sanity check dimensions
    assert b1.shape[0] == hidden, f"{name}: b1 shape {b1.shape} != hidden {hidden}"
    assert W2.shape[1] == hidden, f"{name}: W2 shape {W2.shape} != (1, {hidden})"
    assert W2.shape[0] == 1 and b2.shape[0] == 1, f"{name}: output layer not scalar"

    checkpoint = {
        "format": "swarmlabs-mlp-v1",
        "name": name,
        "description": description,
        "domain": domain,
        "architecture": {
            "layers": ["linear", "tanh", "linear"],  # SmallMLP uses tanh, not ReLU
            "input_dim": input_dim,
            "hidden_dim": hidden,
            "output_dim": 1,
            "total_params": int(W1.size + b1.size + W2.size + b2.size),
        },
        "weights": {
            "W1": f32_array(W1),        # flat row-major [hidden * input_dim]
            "b1": f32_array(b1),        # [hidden]
            "W2": f32_array(W2),        # [hidden]
            "b2": f32_array(b2),        # [1]
        },
        "weights_shape": {
            "W1": list(W1.shape),       # (hidden, input_dim)
            "b1": list(b1.shape),
            "W2": list(W2.shape),
            "b2": list(b2.shape),
        },
        "stats": {
            "W1_range": [float(W1.min()), float(W1.max())],
            "W2_range": [float(W2.min()), float(W2.max())],
            "b1_range": [float(b1.min()), float(b1.max())],
            "b2_value": float(b2[0]),
        },
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_file": str(npz_path.relative_to(REPO)),
    }
    return checkpoint


def pack_f32_bytes(checkpoint: dict) -> bytes:
    """Compact binary pack of weights (avoids JSON parse overhead in browser)."""
    parts = []
    for key in ("W1", "b1", "W2", "b2"):
        parts.extend(struct.pack("<" + "f" * len(checkpoint["weights"][key]),
                                  *checkpoint["weights"][key]))
    return b"".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--src", default=str(SRC_DIR), help="Source npz directory")
    parser.add_argument("--out", default=str(OUT_DIR), help="Output JSON directory")
    parser.add_argument("--include-bin", action="store_true",
                        help="Also write a compact .bin (float32 packed) alongside each JSON")
    args = parser.parse_args()

    src = Path(args.src)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    npz_files = sorted(glob.glob(str(src / "*.npz")))
    if not npz_files:
        print(f"ERROR: no .npz files found in {src}", file=sys.stderr)
        return 1

    manifest = {
        "format": "swarmlabs-checkpoint-manifest-v1",
        "count": 0,
        "models": [],
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    for npz_path_str in npz_files:
        npz_path = Path(npz_path_str)
        checkpoint = export_one(npz_path)
        json_path = out / f"{checkpoint['name']}.json"
        json_path.write_text(json.dumps(checkpoint, separators=(",", ":")))

        entry = {
            "name": checkpoint["name"],
            "domain": checkpoint["domain"],
            "input_dim": checkpoint["architecture"]["input_dim"],
            "hidden_dim": checkpoint["architecture"]["hidden_dim"],
            "params": checkpoint["architecture"]["total_params"],
            "file": f"models/{checkpoint['name']}.json",
            "bytes": json_path.stat().st_size,
        }
        manifest["models"].append(entry)

        print(f"  ✓ {checkpoint['name']:32s} "
              f"in={entry['input_dim']}d hidden={entry['hidden_dim']} "
              f"params={entry['params']:4d}  {entry['bytes']:6d}B")

    manifest["count"] = len(manifest["models"])
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))

    print(f"\nExported {manifest['count']} checkpoints → {out}")
    print(f"  Manifest: {(out / 'manifest.json').relative_to(REPO)}")
    total_kb = sum(m["bytes"] for m in manifest["models"]) / 1024
    print(f"  Total JSON size: {total_kb:.1f} KB ({manifest['count']} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
