#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for mirror-run skill. Mirrors the guard/UA regression coverage of
virtual-experiment-design but exercises the bridge path."""
import json
import subprocess
import sys

PY = "C:/Users/xing/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
SCRIPT = "skills/agent-skills/mirror-run/scripts/mirror_run.py"

# A scenario that IS live on the current v3 API (pre-52-deploy).
LIVE_SCENARIO = "microbio_monod"


def run(args):
    cmd = [PY, SCRIPT] + args
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    return p


def test_predict_and_guard_twin():
    """Virtual GP + Guard gating + twin, no real endpoint."""
    p = run([
        "--scenario", LIVE_SCENARIO,
        "--train-x-json", "[[0.1],[0.5],[1.0],[1.5]]",
        "--train-y-json", "[0.16,0.55,0.66,0.68]",
        "--query-x-json", "[[0.3],[0.8]]",
        "--json",
    ])
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert out["scenario"] == LIVE_SCENARIO
    assert len(out["virtual_mean"]) == 2
    assert len(out["virtual_std"]) == 2
    # Guard field is `status` (regression: not `verdict`)
    assert "status" in out["guard"], "guard.status missing (verdict bug regressed?)"
    assert out["mode"].startswith("virtual-twin")  # no --real-endpoint
    # twin values exist and len matches query
    assert len(out["real_or_twin"]) == 2


def test_emit_mhs_manifest():
    p = run([
        "--scenario", LIVE_SCENARIO,
        "--train-x-json", "[[0.1],[0.5]]",
        "--train-y-json", "[0.16,0.55]",
        "--query-x-json", "[[0.3]]",
        "--emit-mhs", "--json",
    ])
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert "mhs_manifest" in out
    m = out["mhs_manifest"]
    assert m["virtual_instrument"] == "swarmlabs.v3"
    assert "guard" in m["real_adapter"]
    assert m["real_adapter"]["guard"] in ("pass", "controlled", "reject", "unknown")


def test_shape_normalization():
    """Flat list input must be normalized to matrix."""
    p = run([
        "--scenario", LIVE_SCENARIO,
        "--train-x-json", "[0.1,0.5,1.0]",
        "--train-y-json", "[0.16,0.55,0.66]",
        "--query-x-json", "[0.3,0.8]",
        "--json",
    ])
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert len(out["virtual_mean"]) == 2


if __name__ == "__main__":
    tests = [test_predict_and_guard_twin, test_emit_mhs_manifest, test_shape_normalization]
    failed = 0
    for t in tests:
        try:
            t()
            print("PASS", t.__name__)
        except Exception as e:
            failed += 1
            print("FAIL", t.__name__, "->", e)
    print("\n%d/%d passed" % (len(tests) - failed, len(tests)))
    sys.exit(1 if failed else 0)
