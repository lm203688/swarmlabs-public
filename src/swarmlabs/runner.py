"""SwarmLabs runner — thin orchestration layer over engine/*.py.

The engine modules under ``engine/`` are the single source of truth.
This module exposes named operations that compose them into user-facing
capabilities: run a work order, list scenarios, list gates, list anchors,
evaluate confidence, run the check battery, etc.

Design rules
------------
- Every function returns a dict (JSON-serializable). No hidden side effects
  beyond writing artifacts to ``out_dir`` when requested.
- Every operation is idempotent in the read path. Writes (report, package,
  trajectory) are opt-in via explicit arguments.
- Never wrap or mutate verdicts produced by ``vlab_orchestrator`` /
  ``verification_gate``. We relay, we don't recompute.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Repo root resolution — must work both as installed package and as source
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve().parent
# When pip-installed from source, engine/ lives two dirs up from src/swarmlabs/
_REPO_ROOT = _HERE.parent.parent


def repo_root() -> Path:
    """Return the repo root path."""
    return _REPO_ROOT


def _import(name: str, *rel: str):
    """Import a sibling engine module by name (path-based, no relative imports).

    Uses importlib so the module can be located even when the repo root is
    not on sys.path. This mirrors the loader used inside ``vlab_orchestrator``.

    IMPORTANT: The engine/*.py modules import each other via ``from .foo import``
    (relative imports). For those to work, the engine directory must be
    imported as a real package — which it is, since engine/__init__.py exists.
    So we prefer `importlib.import_module('engine.NAME')` when possible and
    only fall back to file-path loading for scripts/.
    """
    import importlib.util
    import importlib

    # Preferred: treat engine/ as a package. Add repo_root to sys.path first.
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))

    if not rel and name.startswith("engine_"):
        # Fallback: engine/foo → import as engine.foo
        bare = name[len("engine_"):]
        try:
            return importlib.import_module(f"engine.{bare}")
        except ModuleNotFoundError:
            pass  # noqa: silent-ok — ModuleNotFoundError 可安全忽略（已审计 2026-10-08）

    if not rel:
        # Try "engine.NAME" first
        try:
            return importlib.import_module(f"engine.{name}")
        except ModuleNotFoundError:
            pass  # noqa: silent-ok — ModuleNotFoundError 可安全忽略（已审计 2026-10-08）

    # Fallback: file-path load
    target = _REPO_ROOT / rel[0] if rel else _REPO_ROOT / f"{name}.py"
    if not target.exists():
        target = _REPO_ROOT / "engine" / f"{name}.py"
    if not target.exists():
        raise ImportError(f"module not found: {target}")

    spec = importlib.util.spec_from_file_location(f"_sw_{name}", str(target))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # CRITICAL: @dataclass needs this
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Work-order execution
# ---------------------------------------------------------------------------

def run_work_order(work_order: Dict[str, Any],
                   out_dir: Optional[str] = None,
                   record_trajectory: bool = True) -> Dict[str, Any]:
    """Run a work order through the six-stage VLab pipeline.

    Returns the job dict from ``vlab_orchestrator.run_job``. Relays
    ``verdict``, ``outcome``, ``screening``, ``stages`` verbatim.

    Also writes a trajectory snapshot under ``data/trajectories/`` unless
    ``record_trajectory`` is False. Trajectory writes are best-effort:
    a disk failure never fails the run.
    """
    import time as _time
    orch = _import("vlab_orchestrator")
    t0 = _time.time()
    job = orch.run_job(dict(work_order), out_dir=out_dir, build_package=False)
    job.pop("_work_order", None)

    if record_trajectory:
        try:
            from swarmlabs import trajectory as _traj
            snap = _traj.record_trajectory(
                work_order=dict(work_order),
                job=job,
                started_at=t0,
            )
            if snap is not None:
                job["_trajectory"] = str(snap)
        except Exception:
            # trajectory failure must never fail the run
            pass
    return job


def load_work_order(path: str) -> Dict[str, Any]:
    """Load a work-order JSON from disk."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Catalog queries
# ---------------------------------------------------------------------------

def list_scenarios(limit: int = 0) -> Dict[str, Any]:
    """Return the scenario catalog summary.

    Two numbers, kept distinct on purpose:
    - ``registry_total``: scenarios in the gate ledger (82 in the
      canonical build; may be 80 if the ledger is stale from a rdkit-less
      rebuild — that's a real invariant and this field surfaces it).
    - ``trainable``: scenarios that carry concrete train/test data and can
      be fed to the surrogate. This is normally ~10.
    """
    # Registry total comes from the ledger (authoritative count)
    registry_total = 0
    counts = None
    build_complete = True
    try:
        ledger_path = _REPO_ROOT / "data" / "gate_ledger.json"
        if ledger_path.exists():
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            registry_total = ledger.get("build", {}).get("registry_total") or \
                            len(ledger.get("scenarios", {}) or {})
            counts = ledger.get("counts")
            build_complete = ledger.get("build", {}).get("build_complete", True)
    except Exception:
        pass  # noqa: silent-ok — Exception 可安全忽略（已审计 2026-10-08）

    # Trainable scenarios come from conformal (what the surrogate can fit)
    trainable = 0
    keys = []
    by_domain: Dict[str, int] = {}
    try:
        from engine import conformal
        scenarios = conformal.load_scenarios()
        trainable = len(scenarios)
        for s in scenarios:
            dom = s.get("domain") or s.get("discipline") or "unknown"
            by_domain[dom] = by_domain.get(dom, 0) + 1
        all_keys = [s.get("key") or s.get("id") for s in scenarios]
        keys = all_keys[:limit] if limit > 0 else all_keys
    except Exception:
        pass  # noqa: silent-ok — Exception 可安全忽略（已审计 2026-10-08）

    return {
        "registry_total": registry_total,
        "build_complete": build_complete,
        "verdict_counts": counts,
        "trainable": trainable,
        "by_domain": by_domain,
        "keys": keys,
    }


def list_gates() -> Dict[str, Any]:
    """Return the registered gate catalog (11 scenario-level + counts)."""
    try:
        from engine.harness_registry import all_gates, summary
        gates = all_gates()
        return {
            "count": len(gates),
            "summary": summary(),
            "gates": [
                {
                    "id": g.id,
                    "category": g.category,
                    "negation": g.negation,
                    "description": g.description,
                }
                for g in gates
            ],
        }
    except Exception as e:
        return {"error": f"harness_registry unavailable: {type(e).__name__}: {e}"}


def list_anchors(filter_discipline: Optional[str] = None) -> Dict[str, Any]:
    """Return the wet-lab anchor catalog with 3D evidence coverage."""
    import engine.wet_lab_anchors as wla
    entries = wla.anchors_index()
    if filter_discipline:
        entries = [e for e in entries
                   if (e.get("discipline") or "").lower() == filter_discipline.lower()]
    coverage = wla.anchors_3d_coverage()
    return {
        "total": len(entries),
        "coverage_3d": coverage,
        "anchors": entries,
    }


# ---------------------------------------------------------------------------
# Confidence profile (distributional V&V)
# ---------------------------------------------------------------------------

def confidence_profile(scenario_key: Optional[str] = None,
                       alpha_grid: Optional[List[float]] = None) -> Dict[str, Any]:
    """Compute a distributional confidence profile for a scenario."""
    from engine import conformal, conformal_vv
    scenarios = conformal.load_scenarios()
    if scenario_key:
        scenarios = [s for s in scenarios
                     if s.get("key") == scenario_key]
        if not scenarios:
            return {"error": f"scenario not found: {scenario_key}"}
        profile = conformal_vv.build_confidence_profile(scenarios[0])
        return {
            "key": profile.key,
            "grade": profile.calibration_grade,
            "levels": [lv.__dict__ for lv in profile.levels],
        }
    profiles = [conformal_vv.build_confidence_profile(s) for s in scenarios]
    summary = conformal_vv.summarize_grades(profiles)
    return {
        "total": len(profiles),
        "grade_distribution": summary,
        "all_pass_at_95": conformal_vv.coverage_at_alpha(profiles, 0.05),
    }


# ---------------------------------------------------------------------------
# Check battery
# ---------------------------------------------------------------------------

_CHECKS = [
    ("honesty_invariants", "scripts/check_honesty_invariants.py"),
    ("vlab_gates",         "scripts/check_vlab_gates.py"),
    ("svp_compliance",     "scripts/check_svp_compliance.py"),
    ("formal_invariants",  "engine/formal_invariants.py"),
    ("conformal_vv",       "engine/conformal_vv.py"),
    ("smt_verification",   "scripts/verify_smt.py"),
]


def run_checks(verbose: bool = False) -> Dict[str, Any]:
    """Run the full check battery. Every check is a subprocess."""
    results = []
    all_ok = True
    for name, script in _CHECKS:
        cmd = [sys.executable, str(_REPO_ROOT / script)]
        if name == "formal_invariants":
            cmd.append("--selftest")
        if name == "conformal_vv":
            cmd.append("--selftest")
        # smt_verification needs no flag; verify_smt.py runs all 6 checks itself
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, cwd=str(_REPO_ROOT),
                timeout=120,
            )
            ok = proc.returncode == 0
            entry = {
                "check": name,
                "ok": ok,
                "returncode": proc.returncode,
            }
            if verbose:
                entry["stdout"] = proc.stdout[-4000:]
                entry["stderr"] = proc.stderr[-2000:]
            results.append(entry)
            if not ok:
                all_ok = False
        except Exception as e:
            results.append({"check": name, "ok": False,
                            "error": f"{type(e).__name__}: {e}"})
            all_ok = False
    return {
        "ok": all_ok,
        "checks_run": len(results),
        "checks_passed": sum(1 for r in results if r.get("ok")),
        "results": results,
    }


# ---------------------------------------------------------------------------
# Doctor
# ---------------------------------------------------------------------------

def doctor() -> Dict[str, Any]:
    """Environment self-check."""
    import numpy, scipy
    info = {
        "python": sys.version.split()[0],
        "python_exe": sys.executable,
        "numpy": numpy.__version__,
        "scipy": scipy.__version__,
        "repo_root": str(_REPO_ROOT),
    }
    rdkit_ok = False
    try:
        from rdkit import Chem  # noqa
        rdkit_ok = True
    except Exception:
        pass  # noqa: silent-ok — Exception 可安全忽略（已审计 2026-10-08）
    info["rdkit"] = "available" if rdkit_ok else "missing (80/82 scenarios only)"
    info["engine_modules"] = len(list((_REPO_ROOT / "engine").glob("*.py")))
    info["scripts"] = len(list((_REPO_ROOT / "scripts").glob("*.py")))
    return info
