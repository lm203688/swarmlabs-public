"""
Model Registry for SwarmLabs

Tracks surrogate models, their versions, checkpoints, and validation metrics.
Serves as the single source of truth for which model powers which scenario.

Each model entry has:
- id: unique identifier (usually scenario name)
- version: semantic version string
- checkpoint: path to model artifact (usually in data/models/)
- trained_on: date + training set size
- metrics: dict of validation metrics (rmse, coverage, calibration_error, picp)
- evidence_grade: 'validated' | 'experimental' | 'prototype'
- paper_backed: bool, True if this model version is cited in a published paper
- calibration_seed: seed used for conformal calibration
- calibration_set_size: number of calibration points
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any


DATA_DIR = Path(__file__).resolve().parent.parent / "data"
REGISTRY_PATH = DATA_DIR / "model_registry.json"


@dataclass
class ModelMetrics:
    """Validation metrics for a surrogate model."""
    rmse_abs: float | None = None
    rmse_rel: float | None = None
    coverage: float | None = None  # empirical coverage on test set
    picp: float | None = None  # prediction interval coverage probability
    calibration_error: float | None = None  # |coverage - 0.95|
    calibration_width: float | None = None  # average interval width
    calibration_width_relative: float | None = None


@dataclass
class ModelEntry:
    """A single entry in the model registry."""
    id: str
    version: str
    scenario: str
    domain: str  # discipline, e.g. 'bio'
    category: str
    checkpoint_path: str | None = None
    trained_on: str = ""
    training_set_size: int = 0
    calibration_seed: int = 7311  # matches project default
    calibration_set_size: int = 0
    metrics: ModelMetrics = field(default_factory=ModelMetrics)
    evidence_grade: str = "experimental"  # validated | experimental | prototype
    paper_backed: bool = False
    paper_refs: list[str] = field(default_factory=list)
    uq_method: str = "acp_acp"  # conformal method used
    kernel: str = "rbf"
    length_scale: str = "auto"
    noise_floor: float = 0.03
    notes: str = ""
    registered_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"))


def _default_registry() -> dict[str, Any]:
    return {
        "_meta": {
            "version": "1.0",
            "generated": "2026-10-23",
            "purpose": "Single source of truth for surrogate models backing SwarmLabs scenarios. "
                       "Distinguishes validated (paper-backed) from experimental models.",
            "default_seed": 7311,
            "default_noise_floor": 0.03,
            "target_coverage": 0.95,
            "min_picp": 0.90,
        },
        "models": [],
        "stats": {},
    }


def load_registry(path: Path | None = None) -> dict[str, Any]:
    """Load registry from JSON; create default if missing."""
    p = path or REGISTRY_PATH
    if p.exists():
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    return _default_registry()


def save_registry(data: dict[str, Any], path: Path | None = None) -> None:
    p = path or REGISTRY_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def upsert_model(entry: ModelEntry, path: Path | None = None) -> None:
    """Insert or update a model entry by id+version."""
    data = load_registry(path)
    models = data["models"]
    key = (entry.id, entry.version)
    for i, m in enumerate(models):
        if (m["id"], m["version"]) == key:
            models[i] = asdict(entry)
            break
    else:
        models.append(asdict(entry))
    data["stats"] = _compute_stats(data["models"])
    save_registry(data, path)


def get_model(model_id: str, version: str | None = None, path: Path | None = None) -> ModelEntry | None:
    """Retrieve a specific model, or the latest version if not specified."""
    data = load_registry(path)
    matches = [m for m in data["models"] if m["id"] == model_id]
    if not matches:
        return None
    if version:
        matches = [m for m in matches if m["version"] == version]
        if not matches:
            return None
        m = matches[0]
    else:
        m = sorted(matches, key=lambda x: x["version"], reverse=True)[0]
    return ModelEntry(**{**m, "metrics": ModelMetrics(**m.get("metrics", {}))})


def list_models(domain: str | None = None, evidence_grade: str | None = None, path: Path | None = None) -> list[dict]:
    """List models with optional filters."""
    data = load_registry(path)
    models = data["models"]
    if domain:
        models = [m for m in models if m.get("domain") == domain]
    if evidence_grade:
        models = [m for m in models if m.get("evidence_grade") == evidence_grade]
    return models


def _compute_stats(models: list[dict]) -> dict:
    by_grade = {}
    by_domain = {}
    for m in models:
        g = m.get("evidence_grade", "experimental")
        d = m.get("domain", "unknown")
        by_grade[g] = by_grade.get(g, 0) + 1
        by_domain[d] = by_domain.get(d, 0) + 1
    pics = [m.get("metrics", {}).get("picp") for m in models if m.get("metrics", {}).get("picp") is not None]
    return {
        "total_models": len(models),
        "by_evidence_grade": by_grade,
        "by_domain": by_domain,
        "mean_picp": round(sum(pics) / len(pics), 4) if pics else None,
        "min_picp": round(min(pics), 4) if pics else None,
    }


# ---------------------------------------------------------------------------
# Bootstrap: seed the registry from scenario_registry.json (95 validated + 163 experimental)
# ---------------------------------------------------------------------------

def _seed_from_scenario_registry() -> list[ModelEntry]:
    """Bootstrap from engine.scenario_registry (single source of truth).

    Uses engine.scenario_registry.all_scenarios() which returns dict keyed by scenario id.
    95 scenarios -> evidence_grade='validated' (paper-backed surrogate).
    Engines listed in ENGINE_STATS but not in scenario_registry -> 'experimental'.
    Only electrodialysis retains evidence_grade='validated' per P0-3 (me=3.21 measured).
    """
    entries = []
    try:
        from engine.scenario_registry import all_scenarios
        scenarios_map = all_scenarios()
    except Exception as e:
        print(f"[model_registry] scenario_registry unavailable: {e}", file=__import__('sys').stderr)
        return []
    for key, s in scenarios_map.items():
        name = s.get("name") or s.get("id") or key
        if not name:
            continue
        grade = "experimental"  # default: P0-3 fix removed placeholder claims
        if name == "electrodialysis":
            grade = "validated"  # only engine with measured mean_error=3.21
        entry = ModelEntry(
            id=name,
            version="0.1.0",
            scenario=name,
            domain=s.get("domain", "unknown"),
            category=s.get("category", "unknown"),
            checkpoint_path=f"data/models/{name}_v0.1.0.npz",
            trained_on="2026-10-23",
            training_set_size=int(s.get("n_samples") or s.get("n_train") or 0),
            calibration_seed=7311,
            calibration_set_size=int(s.get("n_calibration") or 100),
            metrics=ModelMetrics(
                rmse_rel=s.get("rmse_rel"),
                coverage=s.get("coverage"),
                picp=s.get("picp"),
                calibration_error=s.get("calibration_error"),
            ),
            evidence_grade=grade,
            paper_backed=(grade == "validated"),
            paper_refs=["paper-2-ieee-tcyber"] if grade == "validated" else [],
            uq_method=s.get("uq_method", "acp_acp"),
            kernel=s.get("kernel", "rbf"),
            length_scale=s.get("length_scale", "auto"),
            notes=s.get("notes", ""),
        )
        entries.append(entry)
    return entries


def _seed_legacy_json_only() -> list[ModelEntry]:
    """Legacy JSON-based seeding (kept for backward compat with scenario_registry.json)."""
    sr_path = DATA_DIR / "scenario_registry.json"
    if not sr_path.exists():
        return []
    with open(sr_path, "r", encoding="utf-8") as f:
        sr = json.load(f)
    entries = []
    scenarios = sr.get("scenarios", []) if isinstance(sr, dict) else sr
    for s in scenarios:
        name = s.get("name") or s.get("id")
        if not name:
            continue
        grade = s.get("evidence_grade", "experimental")
        # Only 1 validated: electrochemistry / electrodialysis with mean_error 3.21
        # Others default experimental per P0-3 fix
        if name == "electrodialysis":
            grade = "validated"
        entry = ModelEntry(
            id=name,
            version="0.1.0",
            scenario=name,
            domain=s.get("domain", "unknown"),
            category=s.get("category", "unknown"),
            checkpoint_path=f"data/models/{name}_v0.1.0.npz",
            trained_on="2026-10-23",
            training_set_size=s.get("n_samples", 0),
            calibration_seed=7311,
            calibration_set_size=s.get("n_calibration", 100),
            metrics=ModelMetrics(
                rmse_rel=s.get("rmse_rel"),
                coverage=s.get("coverage"),
                picp=s.get("picp"),
                calibration_error=s.get("calibration_error"),
            ),
            evidence_grade=grade,
            paper_backed=(grade == "validated"),
            paper_refs=["paper-2-ieee-tcyber"] if grade == "validated" else [],
            uq_method=s.get("uq_method", "acp_acp"),
            kernel=s.get("kernel", "rbf"),
            length_scale=s.get("length_scale", "auto"),
            notes=s.get("notes", ""),
        )
        entries.append(entry)
    return entries


def bootstrap(path: Path | None = None, force: bool = False) -> int:
    """Seed the registry if empty (or force rebuild). Returns count added."""
    p = path or REGISTRY_PATH
    if p.exists() and not force:
        data = load_registry(p)
        if data.get("models"):
            return 0  # already seeded
    entries = _seed_from_scenario_registry()
    data = _default_registry()
    data["models"] = [asdict(e) for e in entries]
    data["stats"] = _compute_stats(data["models"])
    save_registry(data, p)
    return len(entries)


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--bootstrap", action="store_true", help="Seed from scenario_registry if empty")
    ap.add_argument("--force", action="store_true", help="Force rebuild")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--domain")
    ap.add_argument("--grade", choices=["validated", "experimental", "prototype"])
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    if args.bootstrap:
        n = bootstrap(force=args.force)
        print(f"Seeded {n} models")
    if args.stats:
        data = load_registry()
        print(json.dumps(data.get("stats", {}), indent=2))
    if args.list or not any([args.bootstrap, args.stats, args.json]):
        models = list_models(domain=args.domain, evidence_grade=args.grade)
        if args.json:
            print(json.dumps(models, indent=2))
        else:
            print(f"{'ID':40s} {'VER':8s} {'GRADE':12s} {'PICP':8s} {'RMSE_REL':10s}")
            print("-" * 80)
            for m in models[:50]:
                p = m.get("metrics", {}).get("picp")
                r = m.get("metrics", {}).get("rmse_rel")
                print(f"{m['id'][:40]:40s} {m['version']:8s} {m.get('evidence_grade','?'):12s} "
                      f"{(f'{p:.4f}' if p else 'n/a'):8s} {(f'{r:.4f}' if r else 'n/a'):10s}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
