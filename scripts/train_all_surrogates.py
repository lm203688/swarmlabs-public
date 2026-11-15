#!/usr/bin/env python
"""Train SmallMLP surrogates for all 95 SwarmLabs scenarios.

Uses the SmallMLP class from paper_validation_loop.py to train 2-layer tanh MLPs
(hidden=24) on each scenario's deterministic LHS training set. Saves .npz files
to data/small_models/ (same location as existing 10 checkpoints).

Usage:
    python scripts/train_all_surrogates.py              # train all missing
    python scripts/train_all_surrogates.py --force       # retrain all 95
    python scripts/train_all_surrogates.py --key mol_suzuki_yield  # single scenario
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from scripts.paper_validation_loop import ALL_SCENARIOS, SMALL_MODEL_THRESHOLD, SmallMLP  # noqa: E402

SMALL_DIR = REPO / "data" / "small_models"
SMALL_DIR.mkdir(parents=True, exist_ok=True)


def _key_seed(key: str) -> int:
    """Deterministic seed from scenario key (same logic as paper_validation_loop)."""
    return int(abs(hash(key)) % (2**31 - 1))


def train_one(scenario: dict, epochs: int = 500, lr: float = 0.02) -> dict:
    """Train a SmallMLP for a single scenario. Returns metrics dict."""
    key = scenario["key"]
    f = scenario["f"]
    bounds = scenario["bounds"]
    dim = len(bounds)

    # Get training set (deterministic LHS, already attached by _attach_datasets)
    X_train = np.array(scenario["train"], dtype=float)
    y_train = np.array([f(list(p)) for p in X_train], dtype=float)

    # Filter to points that produce finite outputs (some scenarios have singularities)
    mask = np.isfinite(y_train)
    X_train, y_train = X_train[mask], y_train[mask]

    # Dynamic threshold: 1D needs ≥20 points, 2D needs ≥40, 6D needs ≥60
    min_points = {1: 20, 2: 40, 6: 60}.get(dim, 30)
    if len(X_train) < min_points:
        return {"key": key, "status": "skip", "reason": f"only {len(X_train)} points (need {min_points})"}

    # Train MLP
    rng = np.random.default_rng(_key_seed(key))
    mlp = SmallMLP(dim, hidden=24, rng=rng)
    mlp.fit(X_train, y_train, epochs=epochs, lr=lr)

    # Evaluate on held-out test set
    X_test = np.array(scenario["test"], dtype=float)
    y_test = np.array([f(list(p)) for p in X_test], dtype=float)
    mask_test = np.isfinite(y_test)
    X_test, y_test = X_test[mask_test], y_test[mask_test]

    pred = mlp.predict(X_test)
    rmse = float(np.sqrt(np.mean((pred - y_test) ** 2)))
    y_range = float(np.std(y_train) if len(y_train) > 1 else 1.0)
    rmse_rel = rmse / (y_range + 1e-9)

    # Save checkpoint
    out_path = SMALL_DIR / f"{key}.npz"
    mlp.save(str(out_path))

    return {
        "key": key,
        "status": "trained",
        "dim": dim,
        "n_train": len(X_train),
        "n_test": len(X_test),
        "rmse": round(rmse, 6),
        "rmse_rel": round(rmse_rel, 4),
        "params": mlp.W1.size + mlp.b1.size + mlp.W2.size + mlp.b2.size,
        "path": str(out_path.relative_to(REPO)),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Retrain all scenarios (not just missing)")
    parser.add_argument("--key", type=str, help="Train only this scenario key")
    parser.add_argument("--epochs", type=int, default=500)
    parser.add_argument("--lr", type=float, default=0.02)
    args = parser.parse_args()

    # Filter scenarios
    if args.key:
        scenarios = [s for s in ALL_SCENARIOS if s["key"] == args.key]
        if not scenarios:
            print(f"ERROR: scenario '{args.key}' not found")
            return 1
    else:
        scenarios = ALL_SCENARIOS

    # Check existing checkpoints
    existing = set()
    for f in os.listdir(SMALL_DIR):
        if f.endswith(".npz"):
            existing.add(os.path.splitext(f)[0])

    to_train = []
    for s in scenarios:
        if args.force or s["key"] not in existing:
            to_train.append(s)

    print(f"Total scenarios: {len(scenarios)}")
    print(f"Already have checkpoints: {len(existing)}")
    print(f"To train: {len(to_train)}")

    if not to_train:
        print("Nothing to do.")
        return 0

    # Train
    results = []
    t0 = time.time()
    for i, s in enumerate(to_train, 1):
        key = s["key"]
        try:
            r = train_one(s, epochs=args.epochs, lr=args.lr)
            results.append(r)
            status = r.get("status", "?")
            if status == "trained":
                print(f"  [{i}/{len(to_train)}] ✓ {key:40s} dim={r['dim']} rmse_rel={r['rmse_rel']:.4f}")
            else:
                print(f"  [{i}/{len(to_train)}] ⊘ {key:40s} {r.get('reason','?')}")
        except Exception as e:
            print(f"  [{i}/{len(to_train)}] ✗ {key:40s} ERROR: {str(e)[:60]}")
            results.append({"key": key, "status": "error", "reason": str(e)[:100]})

    elapsed = time.time() - t0

    # Summary
    trained = [r for r in results if r.get("status") == "trained"]
    skipped = [r for r in results if r.get("status") == "skip"]
    errors = [r for r in results if r.get("status") == "error"]

    print(f"\n=== Training Summary ===")
    print(f"  Trained: {len(trained)}")
    print(f"  Skipped: {len(skipped)}")
    print(f"  Errors:  {len(errors)}")
    print(f"  Time:    {elapsed:.1f}s ({elapsed/len(to_train):.2f}s per model)")

    if trained:
        rmse_rels = [r["rmse_rel"] for r in trained]
        print(f"\n  RMSE_rel stats: min={min(rmse_rels):.4f} max={max(rmse_rels):.4f} mean={np.mean(rmse_rels):.4f}")

    # Save results manifest
    manifest_path = REPO / "data" / "small_models" / "training_manifest.json"
    manifest = {
        "trained_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "total_trained": len(trained),
        "results": results,
    }
    import json
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"\n  Manifest: {manifest_path.relative_to(REPO)}")

    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
