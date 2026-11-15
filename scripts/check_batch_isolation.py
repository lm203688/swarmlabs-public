#!/usr/bin/env python3
"""check_batch_isolation.py — Verify batch_id isolation in train/val/test splits.

Source: PROJECT_ELEVATION_REPORT_2026-10-26 §3.1「训练、校准、测试必须按 batch_id 隔离，
不能随机拆时间点，否则会发生严重信息泄漏」。

Batch isolation invariant:
  For any split assignment dict {sample_id → "train" | "val" | "test"},
  no two samples from the same batch_id may appear in different splits.

Violations are hard-blocked. Also checks:
  - No sample_id appears in >1 split
  - Every sample has a batch_id (missing batch_id → reject)

Usage:
    python scripts/check_batch_isolation.py --selftest
    python scripts/check_batch_isolation.py --ci
    python scripts/check_batch_isolation.py --json
    python scripts/check_batch_isolation.py --split my_split.json --batches my_batches.json

Data files are optional — the selftest generates synthetic cases (including
positive + negative) so the check is always exercised.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


SPLITS = ("train", "val", "test")


class BatchIsolationError(Exception):
    pass


def validate_split(batch_id_of_sample: dict[str, str],
                   split_of_sample: dict[str, str]) -> tuple[bool, list[str]]:
    """Return (isolated, violations).

    batch_id_of_sample: {sample_id: batch_id}
    split_of_sample:    {sample_id: "train" | "val" | "test"}
    """
    violations: list[str] = []

    # 1. Every sample must have a batch_id
    for sid in split_of_sample:
        if sid not in batch_id_of_sample or not batch_id_of_sample[sid]:
            violations.append(f"sample {sid!r} has missing/empty batch_id")

    # 2. Every sample_id must appear in exactly one split
    for sid in split_of_sample:
        if sid not in SPLITS:
            pass  # will be flagged below via unknown split
        unknown = split_of_sample[sid]
        if unknown not in SPLITS:
            violations.append(f"sample {sid!r}: unknown split {unknown!r}")

    # 3. Same batch_id must not appear in multiple splits
    batch_to_splits: dict[str, set[str]] = defaultdict(set)
    for sid, split in split_of_sample.items():
        bid = batch_id_of_sample.get(sid)
        if bid:
            batch_to_splits[bid].add(split)

    for bid, splits in batch_to_splits.items():
        if len(splits) > 1:
            violations.append(
                f"batch {bid!r} appears in multiple splits: {sorted(splits)}"
            )

    # 4. Duplicate sample_id across splits (should be impossible if input is
    #    a valid dict, but guard against malformed input).
    sid_to_splits: dict[str, set[str]] = defaultdict(set)
    for sid, split in split_of_sample.items():
        sid_to_splits[sid].add(split)
    for sid, splits in sid_to_splits.items():
        if len(splits) > 1:
            violations.append(f"sample {sid!r} appears in multiple splits: {sorted(splits)}")

    return len(violations) == 0, violations


def stratified_batch_split(
    samples: list[dict[str, Any]],
    val_ratio: float = 0.15,
    test_ratio: float = 0.15,
    seed: int = 42,
) -> dict[str, list[str]]:
    """Random-shuffle-within-batch grouping split.

    Samples are grouped by batch_id, then batches are shuffled and assigned
    to splits so that no batch is fragmented across splits.
    """
    by_batch: dict[str, list[str]] = defaultdict(list)
    for s in samples:
        by_batch[s["batch_id"]].append(s["sample_id"])
    batch_ids = list(by_batch.keys())
    rng = random.Random(seed)
    rng.shuffle(batch_ids)

    n_batches = len(batch_ids)
    n_val = max(1, round(n_batches * val_ratio))
    n_test = max(1, round(n_batches * test_ratio))
    if n_val + n_test >= n_batches:
        raise BatchIsolationError(
            f"need >= {n_val + n_test} batches for val+test, only have {n_batches}"
        )

    val_batches = batch_ids[:n_val]
    test_batches = batch_ids[n_val:n_val + n_test]
    train_batches = batch_ids[n_val + n_test:]

    splits: dict[str, list[str]] = {"train": [], "val": [], "test": []}
    for bid, sids in by_batch.items():
        if bid in val_batches:
            splits["val"].extend(sids)
        elif bid in test_batches:
            splits["test"].extend(sids)
        else:
            splits["train"].extend(sids)

    return splits


def _generate_selftest_cases() -> list[dict[str, Any]]:
    """Return list of test cases: positive (should pass) + negative (should fail)."""
    return [
        {
            "name": "positive: 3 batches × 4 samples, clean split",
            "batch_id_of_sample": {
                f"A{i}": "b1" for i in range(4)
            } | {
                f"B{i}": "b2" for i in range(4)
            } | {
                f"C{i}": "b3" for i in range(4)
            } | {
                f"D{i}": "b4" for i in range(4)
            },
            "split_of_sample": {
                **{f"A{i}": "train" for i in range(4)},
                **{f"B{i}": "train" for i in range(4)},
                **{f"C{i}": "val" for i in range(4)},
                **{f"D{i}": "test" for i in range(4)},
            },
            "expect_isolated": True,
        },
        {
            "name": "negative: same batch fragmented across train+test (time leakage)",
            "batch_id_of_sample": {
                f"A{i}": "b1" for i in range(4)
            } | {
                f"B{i}": "b2" for i in range(4)
            },
            "split_of_sample": {
                **{f"A{i}": "train" for i in range(2)},
                **{f"A{i}": "test" for i in range(2, 4)},
                **{f"B{i}": "val" for i in range(4)},
            },
            "expect_isolated": False,
        },
        {
            "name": "negative: same batch in train+val (calibration leakage)",
            "batch_id_of_sample": {
                "A0": "b1", "A1": "b1", "A2": "b1", "A3": "b1",
                "B0": "b2", "B1": "b2",
            },
            "split_of_sample": {
                "A0": "train", "A1": "train", "A2": "val", "A3": "test",
                "B0": "val", "B1": "val",
            },
            "expect_isolated": False,
        },
        {
            "name": "negative: missing batch_id",
            "batch_id_of_sample": {
                "A0": "b1", "A1": "b1", "B0": "",
            },
            "split_of_sample": {
                "A0": "train", "A1": "val", "B0": "test",
            },
            "expect_isolated": False,
        },
        {
            "name": "negative: unknown split label",
            "batch_id_of_sample": {"A0": "b1"},
            "split_of_sample": {"A0": "holdout"},
            "expect_isolated": False,
        },
    ]


def run_selftest() -> dict[str, Any]:
    cases = _generate_selftest_cases()
    results = []
    passed = 0
    failed = 0
    for c in cases:
        isolated, violations = validate_split(
            c["batch_id_of_sample"], c["split_of_sample"]
        )
        expected = c["expect_isolated"]
        ok = (isolated == expected)
        if ok:
            passed += 1
        else:
            failed += 1
        results.append({
            "name": c["name"],
            "expect_isolated": expected,
            "got_isolated": isolated,
            "violations": violations,
            "ok": ok,
        })

    # Also test stratified_batch_split end-to-end
    samples = [
        {"sample_id": f"A{i}", "batch_id": f"b{i // 5}"} for i in range(60)
    ]  # 12 batches × 5 samples
    splits = stratified_batch_split(samples, val_ratio=0.2, test_ratio=0.2, seed=42)
    batch_id_map = {s["sample_id"]: s["batch_id"] for s in samples}
    split_map = {sid: split_name for split_name, sids in splits.items() for sid in sids}
    e2e_isolated, e2e_violations = validate_split(batch_id_map, split_map)
    results.append({
        "name": "e2e: stratified_batch_split with 60 samples × 12 batches is isolated",
        "expect_isolated": True,
        "got_isolated": e2e_isolated,
        "violations": e2e_violations,
        "ok": e2e_isolated,
    })
    if e2e_isolated:
        passed += 1
    else:
        failed += 1

    return {
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "results": results,
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--ci", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--split", type=str)
    ap.add_argument("--batches", type=str)
    args = ap.parse_args(argv)

    if args.selftest:
        r = run_selftest()
        if args.json:
            print(json.dumps(r, indent=2, ensure_ascii=False))
        else:
            print("=" * 68)
            print("batch_id 隔离自测")
            print("=" * 68)
            for res in r["results"]:
                mark = "✅" if res["ok"] else "❌"
                print(f"  {mark} {res['name']}")
                if res["violations"]:
                    for v in res["violations"]:
                        print(f"     - {v}")
            print(f"\n  {r['passed']}/{r['total']} passed")
        return 0 if r["failed"] == 0 else 1

    if args.split and args.batches:
        batches = json.loads(Path(args.batches).read_text(encoding="utf-8"))
        split = json.loads(Path(args.split).read_text(encoding="utf-8"))
        isolated, violations = validate_split(batches, split)
        if args.json:
            print(json.dumps({"isolated": isolated, "violations": violations}, indent=2))
        else:
            status = "PASS" if isolated else "FAIL"
            print(f"[{status}] batch isolation: {len(violations)} violation(s)")
            for v in violations:
                print(f"  - {v}")
        return 0 if isolated else 1

    if args.ci:
        r = run_selftest()
        print(f"batch_id isolation selftest: {r['passed']}/{r['total']} passed")
        if r["failed"]:
            print(f"[FAIL] {r['failed']} case(s) failed", file=sys.stderr)
            return 1
        print("[CI PASS]")
        return 0

    print("Use --selftest, --ci, or --split/--batches")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
