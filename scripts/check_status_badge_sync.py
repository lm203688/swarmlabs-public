#!/usr/bin/env python3
"""check_status_badge_sync.py — Assert frontend JS vocabulary matches data JSON.

The badge renderer (frontend_fixed/js/status-badge.js) contains a hard-coded copy
of the 6 codes because it must render offline without a fetch. That duplication
can drift silently. This script asserts field-by-field equivalence so a mismatch
fails CI.

Checks (all must pass):
  1. Same 6 codes (order-preserving)
  2. Same zh / en labels
  3. Same severity (info/warn/block)
  4. Same allow_ai_generated boolean
  5. Same requires_evidence boolean
  6. Forbidden variants from the HTML template match JSON (via regex extraction)

Usage:
  python scripts/check_status_badge_sync.py            # human output
  python scripts/check_status_badge_sync.py --ci       # machine-readable
  python scripts/check_status_badge_sync.py --json     # full JSON report

Exit codes: 0 = PASS, 1 = FAIL, 2 = internal error
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
JSON_PATH = REPO / "data" / "status_vocabulary.json"
JS_PATH = REPO / "frontend_fixed" / "js" / "status-badge.js"
HTML_PATH = REPO / "frontend_fixed" / "vocabulary.html"

EXPECTED_CODES = [
    "ADVISABLE",
    "NEEDS_DATA",
    "SIMULATION_ONLY",
    "OUT_OF_SCOPE",
    "PENDING_APPROVAL",
    "REFUTED",
]


def load_json() -> dict[str, Any]:
    if not JSON_PATH.exists():
        raise SystemExit(f"[FATAL] missing {JSON_PATH}")
    return json.loads(JSON_PATH.read_text(encoding="utf-8"))


def extract_js_vocab() -> list[dict[str, Any]]:
    """Parse the VOCAB array out of status-badge.js.

    We use a simple regex on the array literal — no JS runtime required. The
    JS file is expected to keep one entry per code with the exact key names
    used by the JSON schema (code/zh/en/severity/allow_ai_generated/requires_evidence).
    """
    if not JS_PATH.exists():
        raise SystemExit(f"[FATAL] missing {JS_PATH}")
    src = JS_PATH.read_text(encoding="utf-8")

    # Locate the VOCAB = [ ... ]; block (greedy to matching ];)
    m = re.search(r"var\s+VOCAB\s*=\s*\[(.*?)\];", src, re.DOTALL)
    if not m:
        raise SystemExit(f"[FATAL] VOCAB array not found in {JS_PATH}")
    block = m.group(1)

    # Split by top-level { ... } — entries are separated by commas at depth 0
    entries: list[str] = []
    depth = 0
    start = -1
    for i, ch in enumerate(block):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                entries.append(block[start : i + 1])
                start = -1
    if len(entries) != 6:
        raise SystemExit(
            f"[FATAL] expected 6 VOCAB entries in JS, found {len(entries)}"
        )

    vocab: list[dict[str, Any]] = []
    for e in entries:
        entry: dict[str, Any] = {}
        # code: 'X'
        entry["code"] = _first_str(e, "code")
        entry["zh"] = _first_str(e, "zh")
        entry["en"] = _first_str(e, "en")
        entry["severity"] = _first_str(e, "severity")
        entry["allow_ai_generated"] = _first_bool(e, "allow_ai_generated")
        entry["requires_evidence"] = _first_bool(e, "requires_evidence")
        vocab.append(entry)
    return vocab


def _first_str(entry: str, key: str) -> str | None:
    m = re.search(rf"{key}\s*:\s*'([^']*)'", entry)
    if not m:
        m = re.search(rf'{key}\s*:\s*"([^"]*)"', entry)
    return m.group(1) if m else None


def _first_bool(entry: str, key: str) -> bool | None:
    m = re.search(rf"{key}\s*:\s*(true|false)", entry)
    if not m:
        return None
    return m.group(1) == "true"


def extract_html_forbidden() -> dict[str, list[str]]:
    """Extract FORBIDDEN map from vocabulary.html for the entry_forbidden_html function."""
    if not HTML_PATH.exists():
        return {}
    src = HTML_PATH.read_text(encoding="utf-8")
    m = re.search(r"var\s+FORBIDDEN\s*=\s*\{(.*?)\};", src, re.DOTALL)
    if not m:
        return {}
    block = m.group(1)
    result: dict[str, list[str]] = {}
    # Each entry: 'CODE': ['a', 'b', 'c'],
    for m2 in re.finditer(r"'([A-Z_]+)'\s*:\s*\[([^\]]+)\]", block):
        code = m2.group(1)
        items_raw = m2.group(2)
        items = re.findall(r"'([^']*)'", items_raw)
        result[code] = items
    return result


def compare(
    js_vocab: list[dict[str, Any]],
    json_vocab: list[dict[str, Any]],
    html_forbidden: dict[str, list[str]],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []

    js_codes = [e["code"] for e in js_vocab]
    json_codes = [e["code"] for e in json_vocab]

    if js_codes != EXPECTED_CODES:
        issues.append({
            "kind": "js_codes_order",
            "expected": EXPECTED_CODES,
            "actual": js_codes,
        })
    if json_codes != EXPECTED_CODES:
        issues.append({
            "kind": "json_codes_order",
            "expected": EXPECTED_CODES,
            "actual": json_codes,
        })

    by_code_js = {e["code"]: e for e in js_vocab}
    by_code_json = {e["code"]: e for e in json_vocab}

    for code in EXPECTED_CODES:
        j = by_code_js.get(code)
        x = by_code_json.get(code)
        if not j:
            issues.append({"kind": "js_missing_code", "code": code})
            continue
        if not x:
            issues.append({"kind": "json_missing_code", "code": code})
            continue

        for field in ("zh", "en", "severity", "allow_ai_generated", "requires_evidence"):
            if j.get(field) != x.get(field):
                issues.append({
                    "kind": "field_mismatch",
                    "code": code,
                    "field": field,
                    "js": j.get(field),
                    "json": x.get(field),
                })

        # Forbidden variants (HTML mirror)
        json_forbidden = x.get("forbidden_variants", [])
        html_forbidden_list = html_forbidden.get(code, [])
        if json_forbidden != html_forbidden_list:
            issues.append({
                "kind": "forbidden_variants_mismatch",
                "code": code,
                "json": json_forbidden,
                "html": html_forbidden_list,
            })

    return issues


def run_check() -> dict[str, Any]:
    js = load_js_vocab()
    json_doc = load_json()
    json_vocab = json_doc.get("vocabulary", [])
    html_forbidden = extract_html_forbidden()
    issues = compare(js, json_vocab, html_forbidden)
    return {
        "status": "PASS" if not issues else "FAIL",
        "codes_checked": EXPECTED_CODES,
        "js_vocab_count": len(js),
        "json_vocab_count": len(json_vocab),
        "html_forbidden_codes": sorted(html_forbidden.keys()),
        "issues": issues,
    }


def ci_output(report: dict[str, Any]) -> None:
    print(f"STATUS: {report['status']}")
    print(f"CODES: {','.join(report['codes_checked'])}")
    print(f"JS_COUNT: {report['js_vocab_count']}")
    print(f"JSON_COUNT: {report['json_vocab_count']}")
    if report["issues"]:
        print(f"ISSUES: {len(report['issues'])}")
        for i in report["issues"]:
            print(f"  - {i['kind']}: {i}")


def pretty_output(report: dict[str, Any]) -> None:
    print("=== Status Badge Sync Check ===")
    print(f"Status: {report['status']}")
    print(f"Codes: {', '.join(report['codes_checked'])}")
    print(f"JS entries: {report['js_vocab_count']}")
    print(f"JSON entries: {report['json_vocab_count']}")
    if report["issues"]:
        print(f"\n{len(report['issues'])} issues:")
        for i in report["issues"]:
            print(f"  · {i['kind']}:")
            for k, v in i.items():
                if k != "kind":
                    print(f"      {k}: {v}")
        print("\n⚠️  Sync the JS VOCAB array in frontend_fixed/js/status-badge.js "
              "with data/status_vocabulary.json, and the FORBIDDEN map in "
              "frontend_fixed/vocabulary.html.")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ci", action="store_true", help="machine-readable output")
    ap.add_argument("--json", action="store_true", help="emit full JSON report")
    args = ap.parse_args(argv)

    try:
        report = run_check()
    except SystemExit as e:
        print(str(e), file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif args.ci:
        ci_output(report)
    else:
        pretty_output(report)

    return 0 if report["status"] == "PASS" else 1


def load_js_vocab() -> list[dict[str, Any]]:
    return extract_js_vocab()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
