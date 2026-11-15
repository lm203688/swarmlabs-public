#!/usr/bin/env python3
"""check_architecture_boundaries.py — Verify SwarmLabs' four architectural boundaries.

Source: PROJECT_ELEVATION_REPORT_2026-10-26 §4「必须守住的四条边界」

Four hard boundaries:
  B1. LLM 与科学裁决分离 — LLM can suggest but must not directly modify verdict,
       coverage, ground-truth, or approval status.
  B2. 科学控制面与运营飞轮分离 — growth/marketing task failures must not affect
       science state; science verification failure MUST block corresponding delivery.
  B3. 通信标准与数据标准分离 — MCP/A2A handle agent comms; SiLA2/OPC UA handle
       device I/O; AnIML/Allotrope/ISA handle experiment semantics & data exchange.
  B4. 运行 trace 与科研谱系分离 — OpenTelemetry traces for ops debugging;
       RO-Crate/W3C PROV for long-term reproducibility & cross-system exchange.

Implementation: static AST + grep checks on engine/, frontend_fixed/, scripts/.

Usage:
    python scripts/check_architecture_boundaries.py              # run all 4 boundaries
    python scripts/check_architecture_boundaries.py --json       # machine-readable output
    python scripts/check_architecture_boundaries.py --ci         # exit non-zero on any FAIL
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent

# ============ Boundary B1: LLM vs Science verdict separation ============
# LLM-facing modules are those that actually CALL an LLM SDK.
# The boundary: LLM output must not directly write to science verdict sinks.
# Analysis/consumer scripts (that READ verdicts) are NOT LLM-facing.

# True LLM SDK imports — presence of any of these makes a file "LLM-facing".
B1_LLM_SDK_IMPORTS = [
    r'^\s*import\s+openai\b',
    r'^\s*from\s+openai\b',
    r'^\s*import\s+anthropic\b',
    r'^\s*from\s+anthropic\b',
    r'^\s*import\s+google\.generativeai\b',
    r'^\s*from\s+google\.generativeai\b',
    r'^\s*import\s+cohere\b',
    r'^\s*from\s+cohere\b',
    r'^\s*import\s+litellm\b',
    r'^\s*from\s+litellm\b',
    # Local wrapper modules that encapsulate LLM calls
    r'^\s*from\s+engine\.llm\b',
    r'^\s*from\s+engine\.copilot\b',
    r'^\s*import\s+engine\.llm\b',
]
B1_SCAN_ROOTS = [
    REPO / "engine",
    REPO / "scripts",
    REPO / "frontend_fixed" / "functions",
]

# Sinks the LLM path must not directly write to.
B1_SCIENCE_SINKS = [
    "hard_verdict",
    "verdict",
    "coverage",
    "ground_truth",
    "anchor_status",
    "approve_status",
    "approval_state",
]

# Files where the regex will trip on legitimate analysis/writer code paths.
# These are consumers/aggregators of science state, not LLM output paths.
B1_ALLOWED_FILES = {
    # Report builders consume verdicts to write them to HTML/JSON — no LLM involved.
    "scripts/build_reports.py",
    "scripts/judge_bench.py",
    "scripts/scenario_library_density.py",
    # Retired scripts (kept for history, no active LLM usage).
    "scripts/_retired/build_ablation_priority.py",
    "scripts/_retired/completion_check.py",
    # The checker itself — scans for these strings.
    "scripts/check_architecture_boundaries.py",
}


def _is_llm_facing(src: str) -> bool:
    """Return True only if the file actually imports an LLM SDK."""
    return any(re.search(pat, src, re.MULTILINE) for pat in B1_LLM_SDK_IMPORTS)


def check_b1() -> dict[str, Any]:
    """B1: LLM-facing files must not write to verdict sinks."""
    llm_files: list[tuple[Path, str]] = []
    scanned = 0
    for root in B1_SCAN_ROOTS:
        if not root.exists():
            continue
        for f in root.rglob("*.py"):
            try:
                src = f.read_text(encoding="utf-8")
            except Exception:
                continue
            scanned += 1
            if not _is_llm_facing(src):
                continue
            rel = f.relative_to(REPO).as_posix()
            if rel in B1_ALLOWED_FILES:
                continue
            llm_files.append((f, src))

    violations: list[str] = []
    for f, src in llm_files:
        rel = f.relative_to(REPO).as_posix()
        for sink in B1_SCIENCE_SINKS:
            write_patterns = [
                rf'\.{re.escape(sink)}\s*=[^=]',
                rf'setattr\s*\(\s*[^,]+,\s*["\']{re.escape(sink)}["\']',
            ]
            for pat in write_patterns:
                if re.search(pat, src):
                    violations.append(f"{rel}: writes to verdict sink `{sink}`")
                    break

    return {
        "boundary": "B1",
        "name": "LLM 与科学裁决分离",
        "status": "PASS" if not violations else "FAIL",
        "scanned_files": scanned,
        "llm_facing_files": len(llm_files),
        "violations": violations,
        "allowed_files": sorted(B1_ALLOWED_FILES),
    }


# ============ Boundary B2: Science control plane vs Operations flywheel ============
# Growth/ops modules must not import or call science gates.
B2_OPS_PATHS = [
    REPO / "frontend_fixed" / "functions",
    REPO / "scripts" / "run_daily.py",
    REPO / "scripts" / "unified_ops.py",
    REPO / "engine" / "flywheel.py",
]
B2_SCIENCE_MODULE_KEYWORDS = [
    "from engine.verification_gate",
    "from engine.gate_ledger",
    "from engine.conformal",
    "import verification_gate",
    "import gate_ledger",
]
B2_FORBIDDEN_SCIENCE_CALLS = [
    # These are the science gate call sites — ops modules must not call them directly.
    "calibrated_verdict",       # the actual verdict function
    "compute_coverage",         # coverage computation
    "gate_decision",            # gate blocking decision
]


def check_b2() -> dict[str, Any]:
    """B2: Ops/growth modules must not call science gate functions directly."""
    ops_files: list[Path] = []
    for p in B2_OPS_PATHS:
        if p.is_file():
            ops_files.append(p)
        elif p.is_dir():
            ops_files.extend(p.rglob("*.py"))

    violations: list[str] = []
    scanned = 0
    for f in ops_files:
        try:
            src = f.read_text(encoding="utf-8")
        except Exception:
            continue
        scanned += 1
        rel = f.relative_to(REPO).as_posix()
        for kw in B2_SCIENCE_MODULE_KEYWORDS:
            if kw in src:
                # Ops modules are supposed to talk to science via the API layer,
                # not by importing verification_gate directly.
                # Allow if the file is inside frontend_fixed/functions (that's the
                # API layer which is allowed to import science gates to serve /v3/*).
                if "frontend_fixed/functions" in rel:
                    continue
                violations.append(f"{rel}: directly imports {kw!r}")
        for call in B2_FORBIDDEN_SCIENCE_CALLS:
            if re.search(rf'\b{re.escape(call)}\s*\(', src):
                violations.append(f"{rel}: directly calls {call}()")

    return {
        "boundary": "B2",
        "name": "科学控制面与运营飞轮分离",
        "status": "PASS" if not violations else "FAIL",
        "scanned_files": scanned,
        "violations": violations,
        "note": "frontend_fixed/functions/ is exempted as the API boundary layer",
    }


# ============ Boundary B3: Comms standards vs Data standards ============
# Comms: MCP, A2A — for agent-to-agent communication
# Data: SiLA2, OPC UA, AnIML, Allotrope, ISA 88/106
# Check: MCP server must NOT define experiment data schemas;
#        schema files must NOT define transport protocols.
B3_COMMS_KEYWORDS = ["mcp", "a2a", "agent-card", "server_spec"]
B3_DATA_KEYWORDS = ["sila", "opc", "uwa", "anml", "anilm", "allotrope", "isa 88", "isa88", "isa 106", "isa106"]
B3_SCHEMA_DIRS = [REPO / "schemas"]


def check_b3() -> dict[str, Any]:
    """B3: Schema files must not define transport protocols; comms files must not define data schemas."""
    violations: list[str] = []
    scanned = 0

    # Check schemas/ directory — should NOT contain transport protocol definitions
    for schema_file in B3_SCHEMA_DIRS[0].glob("*.json"):
        scanned += 1
        try:
            content = json.loads(schema_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        content_str = json.dumps(content).lower()
        # Schema files should define data contracts, not comms protocols.
        # Flag if they define endpoints, transports, or RPC protocols.
        comms_markers = [
            "rpc", "websocket", "grpc", "mcp-server", "mcp_server", "a2a-protocol",
            "transport", "http-endpoint", "api-path",
        ]
        for marker in comms_markers:
            if marker in content_str:
                # Allow generic terms that overlap (like "transport" in data movement)
                if marker in ("transport",) and "transport_layer" not in content_str:
                    continue
                violations.append(
                    f"schemas/{schema_file.name}: schema file references comms marker {marker!r}"
                )

    # Check comms-facing files — should NOT define experiment data schemas
    comms_files = list((REPO / ".well-known").glob("*.json")) if (REPO / ".well-known").exists() else []
    for cf in comms_files:
        scanned += 1
        try:
            content = cf.read_text(encoding="utf-8").lower()
        except Exception:
            continue
        # Comms files should reference data contracts by name/path, not redefine them.
        # Flag if they define $schema with data-contract properties.
        if '"$schema"' in content and any(kw in content for kw in ("batch_record", "experiment_intent", "evidence_manifest")):
            violations.append(f"{cf.relative_to(REPO)}: comms file redefines data schema")

    return {
        "boundary": "B3",
        "name": "通信标准与数据标准分离",
        "status": "PASS" if not violations else "FAIL",
        "scanned_files": scanned,
        "violations": violations,
    }


# ============ Boundary B4: Runtime trace vs Science provenance ============
# OpenTelemetry is for ops tracing (spans, errors, tokens).
# RO-Crate/W3C PROV is for science provenance (wasDerivedFrom, etc.).
# Check: PROV/RO-Crate references must not be mixed with trace/span/opentelemetry in
# the same module (they are separate concerns).
B4_PROV_KEYWORDS = ["prov", "w3c prov", "ro-crate", "ro_crate", "wasderivedfrom", "wasgeneratedby", "wasassociatedwith"]
B4_TRACE_KEYWORDS = ["opentelemetry", "otel", "tracing", "span_id", "trace_id", "span(", "tracer("]
B4_ALLOWED_MIX_PATHS = [
    # These files legitimately touch both: the export_repro_package.py wraps both.
    "scripts/export_repro_package.py",
]


def check_b4() -> dict[str, Any]:
    """B4: OpenTelemetry trace files and RO-Crate/PROV provenance files must be in separate modules."""
    violations: list[str] = []
    scanned = 0

    # Scan engine/ and scripts/ for files that mix concerns
    for root in [REPO / "engine", REPO / "scripts"]:
        for f in root.rglob("*.py"):
            try:
                src = f.read_text(encoding="utf-8")
            except Exception:
                continue
            scanned += 1
            rel = f.relative_to(REPO).as_posix()
            if rel in B4_ALLOWED_MIX_PATHS:
                continue

            lower = src.lower()
            has_prov = any(kw in lower for kw in B4_PROV_KEYWORDS)
            has_trace = any(kw in lower for kw in B4_TRACE_KEYWORDS)

            if has_prov and has_trace:
                # Check if they are in clearly separate functions/sections
                # (heuristic: if imports are in different blocks, treat as separated)
                prov_imports = re.findall(r'^(?:import|from)\s+[^#\n]*prov[^#\n]*$',
                                          src, re.MULTILINE | re.IGNORECASE)
                trace_imports = re.findall(r'^(?:import|from)\s+[^#\n]*(?:otel|opentelemetry|tracing|trace)[^#\n]*$',
                                            src, re.MULTILINE | re.IGNORECASE)
                # If both import sets are present, it's a mixed concern
                if prov_imports and trace_imports:
                    violations.append(
                        f"{rel}: mixes PROV imports ({len(prov_imports)}) and trace imports ({len(trace_imports)})"
                    )
                # Otherwise, likely keyword-only (e.g., 'prov' as variable), skip

    return {
        "boundary": "B4",
        "name": "运行 trace 与科研谱系分离",
        "status": "PASS" if not violations else "FAIL",
        "scanned_files": scanned,
        "violations": violations,
        "allowed_mix_paths": B4_ALLOWED_MIX_PATHS,
    }


# ============ Runner ============

def run_all() -> dict[str, Any]:
    return {
        "as_of": "2026-10-26",
        "source": "PROJECT_ELEVATION_REPORT_2026-10-26 §4「必须守住的四条边界」",
        "boundaries": [
            check_b1(),
            check_b2(),
            check_b3(),
            check_b4(),
        ],
    }


def main(argv: list[str]) -> int:
    as_json = "--json" in argv
    ci_mode = "--ci" in argv

    result = run_all()
    if as_json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print("=" * 72)
        print("SwarmLabs 四条架构边界检查")
        print(f"as_of: {result['as_of']}   source: {result['source']}")
        print("=" * 72)
        for b in result["boundaries"]:
            status = "✅ PASS" if b["status"] == "PASS" else "❌ FAIL"
            print(f"\n{status}  {b['boundary']}: {b['name']}")
            print(f"  scanned: {b['scanned_files']} files")
            if b.get("violations"):
                for v in b["violations"]:
                    print(f"  - {v}")
            if b.get("note"):
                print(f"  note: {b['note']}")

    if ci_mode:
        fails = sum(1 for b in result["boundaries"] if b["status"] != "PASS")
        if fails:
            print(f"\n[FAIL] {fails}/4 boundaries failed", file=sys.stderr)
            return 1
        print("\n[CI PASS] All 4 boundaries satisfied.", file=sys.stderr)
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
