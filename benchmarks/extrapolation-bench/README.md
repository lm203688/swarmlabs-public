# swarmlabs/extrapolation-bench

**Independent publishable benchmark: honest negative results for surrogate extrapolation.**

> 82 scenarios × 95 extrapolation axes = **771 predictions**, of which **73 axes REFUTED** (76.8%).
> This is not a technical failure to be hidden. This is the honest measurement of where pure-numpy
> surrogate models break under extrapolation.

## What this is

A sub-repository (extracted from SwarmLabs on 2026-10-14) containing the **extrapolation stress test
artifacts** for the 82-scenario corpus that lives inside SwarmLabs.

- **Purpose**: make our honesty boundary externally verifiable, not just a claim inside the main repo.
- **Audience**: benchmark consumers who want to know how much they can trust a GP/conformal hybrid
  outside its training domain.
- **License**: MIT (matches main repo).
- **Zero-cost**: pure numpy, no GPU, no paid API, deterministic.

## Design decisions (aligned with SwarmLabs core goals)

| Decision | Rationale |
|---|---|
| Independent sub-repo | External verifiability. Anyone can `git clone` and reproduce the 73/95 negative result without pulling in SwarmLabs' modeling code. |
| Zero new dependencies | Preserves "pure numpy / zero GPU / content-deterministic" constraint inherited from the main project. |
| Publishable negative result | Aligns with SwarmLabs' #1 honesty principle: "public failure is our moat". 73/95 REFUTED is the most citable asset in the corpus. |

## Layout

```
benchmarks/extrapolation-bench/
├── README.md                     ← this file
├── BENCHMARK.json                ← the actual 73/95 snapshot
├── METRICS.md                    ← how to reproduce & interpret
├── SCENARIOS.tsv                 ← scenario-level verdicts (82 rows)
├── AXES.tsv                      ← per-axis results (771 rows)
└── LICENSE
```

## Reproduction

```bash
# From the SwarmLabs main repo:
python -S scripts/generate_extrapolation_bench.py
# Regenerates BENCHMARK.json + SCENARIOS.tsv + AXES.tsv into this directory.
# Requires: python 3.11+, numpy. No GPU, no internet, no paid API.
```

## What this benchmark is NOT

- ❌ **NOT** a general-purpose BO benchmark (see MLE-bench, MABench).
- ❌ **NOT** a claim that "extrapolation is impossible" — see the 3 publishable directions in
  `reports/upgrade/extrapolation_stress_73_of_95_narrative.md` in the main repo.
- ❌ **NOT** a claim of SOTA. We publish numbers honestly, including the failure rates.

## Related

- `reports/upgrade/extrapolation_stress_73_of_95_narrative.md` in the main repo — full narrative
- `data/g3_aligned_benchmark.json` in the main repo — G3 (holdout discipline) alignment results
- `data/generalization_bench.json` in the main repo — G1/G2/G3 aggregate stats
- arXiv:2608.20439 — cross-parameter reconnection attack, referenced by `data/public_facts.json`
  in the main repo
