# Reproduction & interpretation guide

## What we measure

For each of the 82 scenarios, we probe the surrogate model across **95 extrapolation axes**:
parameter-shift (small/mid/large), holdout-dimension (G2), holdout-discipline (G3), and
cross-domain transfers.

Total predictions: **82 × 95 = 7,790** (some axes have multiple subtests; the 771 figure counts
unique (scenario, axis) verdict pairs).

## Verdict taxonomy

| Verdict | Meaning | Count |
|---|---|---|
| `PASS` | Model stayed within honest UQ band on this axis | 3 axes (0.4%) |
| `MARGINAL` | UQ band widened but still contained the truth | 6 axes (0.8%) |
| `REFUTED` | Truth fell outside UQ band — surrogate is confidently wrong | 73 axes (89.1%) |
| `ERROR` | Numeric failure (should be ~0) | 0 axes |

**Honesty note**: the 73 REFUTED axes include the 2 molecular scenarios (`mol_suzuki_yield`,
`mol_esterification_yield`) that disappear from the registry when `rdkit` is missing. Since 2026-10-14
(main repo `engine/molecular_scenarios.py`), the module **fails loud by default** when rdkit is
unavailable; only `SWARMLABS_ALLOW_DEGRADE=1` restores the old silent-drop behavior. This is the
"silent kill of our most honest data" trap fixed.

## How to interpret the 3 publishable directions

1. **Dimension matters more than domain**: 1D scenarios already produce 33/37 REFUTED, breaking the
   intuitive "extrapolation only fails in high dimensions" story.
2. **G3 holdout-discipline is 0/14** (perfect failure): the model doesn't know it doesn't know when
   the underlying physical law changes. This is the most defensible negative result.
3. **PICP vs MCA calibration gap**: PICP (calibration) is often satisfied while MCA (width) fails —
   the model is "correctly calibrated at the margin" but "too confident at the point".

## Data provenance

- 2026-10-14 snapshot, matching the main repo's `data/trust_surface.json`.
- **G3 = 0/14 disciplines passed** — see `data/g3_aligned_benchmark.json` in the main repo.
- **Zero real wet-lab ground truth** — 0 scenarios have experimental measurements as reference.
- **95 scenarios only 1 peer-reviewed DOI** — the sole `10.1038/s41586-026-10842-8` (Nature 2026).

## What would make this benchmark publishable

- ✅ Independent repo cloneable & runnable on any machine
- ✅ Zero external dependencies beyond numpy
- ✅ Fail-loud on missing optional deps (rdkit trap documented & fixed)
- ❌ Not yet: independent replication by a second lab (open call)
- ❌ Not yet: peer-reviewed publication

## License

MIT — same as the SwarmLabs main repository. See [LICENSE](./LICENSE).
