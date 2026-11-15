"""SwarmLabs one-shot rebuild.

Runs the full artifact pipeline in the correct order:

    1. build_reports.py         -> regenerate scenario reports / V&V JSON
    2. patch_trust_surface_honesty.py -> inject the honesty block (mandatory
       after every rebuild, otherwise honesty block goes stale)
    3. build_gate_ledger.py     -> regenerate data/gate_ledger.json (build_complete)
    4. build_vv_package.py      -> regenerate the V&V tarball + index

Each step is a subprocess with a hard timeout. Any non-zero exit aborts the
pipeline (fail-closed) — no partial rebuild silently ships a broken ledger.

Also verifies the outcome: registry_total must be >= the previous ledger's
total (or --allow-shrink to override; that's the honest way to publish a
deliberately-reduced build).
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


STEPS: List[Dict[str, Any]] = [
    {
        "id": "build_reports",
        "script": "scripts/build_reports.py",
        "args": [],
        "timeout": 600,
        "desc": "regenerate scenario reports + trust surface JSON",
    },
    {
        "id": "patch_honesty",
        "script": "scripts/patch_trust_surface_honesty.py",
        "args": [],
        "timeout": 60,
        "desc": "re-inject the honesty block (mandatory after rebuild)",
    },
    {
        "id": "build_gate_ledger",
        "script": "scripts/build_gate_ledger.py",
        "args": [],
        "timeout": 120,
        "desc": "rebuild data/gate_ledger.json (registry_total source)",
    },
    {
        "id": "build_vv_package",
        "script": "scripts/build_vv_package.py",
        "args": [],
        "timeout": 180,
        "desc": "rebuild the V&V package + manifest",
    },
]


def repo_root() -> Path:
    here = Path(__file__).resolve().parent
    return here.parent.parent


def _read_ledger_total(root: Path) -> Optional[int]:
    p = root / "data" / "gate_ledger.json"
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return int(d.get("build", {}).get("registry_total")
                   or len(d.get("scenarios", {}) or {}))
    except Exception:
        return None


def run(rebuild_root: Optional[str] = None,
        allow_shrink: bool = False,
        dry_run: bool = False,
        verbose: bool = False) -> Dict[str, Any]:
    """Execute the rebuild pipeline.

    Returns a dict with:
      - steps: list of per-step result dicts (id, ok, rc, elapsed, ...)
      - ok: bool (all steps green)
      - before_total / after_total: ledger registry_total before/after
      - allow_shrink: was the shrink gate honored?
      - outcome: 'ok' | 'aborted_step:<id>' | 'aborted_shrink'
    """
    root = Path(rebuild_root) if rebuild_root else repo_root()
    python = sys.executable

    before_total = _read_ledger_total(root)

    results: List[Dict[str, Any]] = []
    ok_all = True
    outcome = "ok"

    for step in STEPS:
        entry: Dict[str, Any] = {
            "id": step["id"],
            "desc": step["desc"],
            "script": step["script"],
        }
        if dry_run:
            entry.update({"dry_run": True, "ok": True})
            results.append(entry)
            continue

        script_path = root / step["script"]
        if not script_path.exists():
            entry.update({"ok": False, "error": f"script not found: {script_path}"})
            results.append(entry)
            ok_all = False
            outcome = f"aborted_step:{step['id']}"
            break

        cmd = [python, str(script_path)] + step["args"]
        if verbose:
            print(f"[{step['id']}] {' '.join(cmd)}", file=sys.stderr)

        t0 = time.time()
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=str(root),
                timeout=step["timeout"],
            )
            entry["rc"] = proc.returncode
            entry["elapsed"] = round(time.time() - t0, 2)
            if verbose:
                if proc.stdout:
                    print(proc.stdout[-2000:], file=sys.stderr)
                if proc.stderr:
                    print(proc.stderr[-2000:], file=sys.stderr)
        except subprocess.TimeoutExpired:
            entry["ok"] = False
            entry["error"] = f"timeout after {step['timeout']}s"
            entry["elapsed"] = round(time.time() - t0, 2)
            results.append(entry)
            ok_all = False
            outcome = f"aborted_step:{step['id']}"
            break
        except Exception as e:
            entry["ok"] = False
            entry["error"] = f"{type(e).__name__}: {e}"
            entry["elapsed"] = round(time.time() - t0, 2)
            results.append(entry)
            ok_all = False
            outcome = f"aborted_step:{step['id']}"
            break

        entry["ok"] = proc.returncode == 0
        results.append(entry)

        if not entry["ok"]:
            ok_all = False
            entry["stdout_tail"] = (proc.stdout or "")[-2000:]
            entry["stderr_tail"] = (proc.stderr or "")[-2000:]
            outcome = f"aborted_step:{step['id']}"
            break

    after_total = _read_ledger_total(root)

    # Shrink gate: refuse to publish a rebuild that silently lost scenarios
    # unless the user explicitly allowed it. This is the 09-23 rdkit regression
    # caught in stone — a rebuild that drops mol_suzuki_yield from REFUTED
    # would silently publish a "we never failed" ledger.
    shrink_ok = True
    if before_total is not None and after_total is not None:
        if after_total < before_total and not allow_shrink:
            shrink_ok = False
            ok_all = False
            outcome = "aborted_shrink"

    return {
        "ok": ok_all,
        "outcome": outcome,
        "before_total": before_total,
        "after_total": after_total,
        "shrink_ok": shrink_ok,
        "allow_shrink": allow_shrink,
        "steps": results,
        "elapsed_total": round(sum(r.get("elapsed", 0) for r in results), 2),
    }
