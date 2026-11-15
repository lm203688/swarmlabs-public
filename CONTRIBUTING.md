# Contributing to SwarmLabs

Thank you for considering a contribution. This is a single-maintainer project
with an unusually long history — **read the honesty invariants before you
write a line of code.**

## Ground rules (fail-closed by design)

1. **No numbers without a source.** Every count in `data/public_facts.json`
   must trace to a computation or a data file. If you add a scenario, add a
   source; `scripts/check_public_numbers.py` will reject your PR otherwise.
2. **No hiding failures.** REFUTED scenarios stay in the ledger. If your
   change fixes one, its old entry remains in `data/gate_ledger.json` as
   evidence; if your change breaks one, `ledger_is_stale()` will flag it and
   `scripts/check_honesty_invariants.py` will fail CI.
3. **No silent degradation.** If a change skips a scenario (missing optional
   dependency, unsupported platform, etc.), it must be recorded in
   `frontend_fixed/reports/index.json.optional_deps.dropped_molecular_scenarios`
   and reflected in `data/gate_ledger.json.build.complete=false`.
4. **No bypassing the verification gate.** The gate is the product. Do not
   weaken `PASS_R2`, `NOISE_FLOOR`, `KAPPA_CAP`, or `COVERAGE_PASS` to make
   a scenario pass.

If you disagree with any of the above, open an issue first. Do not submit a
PR that violates it and hope we notice.

## Getting started

```bash
# 1. Clone
git clone https://github.com/lm203688/swarmlabs.git
cd swarmlabs

# 2. Install
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 3. Verify the gate is green before you touch anything
python scripts/check_public_numbers.py
python scripts/check_honesty_invariants.py
python scripts/check_scenario_registry.py
python -m pytest tests/test_vlab_contract.py -q

# 4. Run the selftest bundle
python scripts/build_gate_ledger.py --print
python scripts/build_parity_report.py
python -m pytest tests/ -q
```

## Contribution types

### ✅ Accepted without gate-review

- Documentation typos, English polish, i18n fixes.
- Additive benchmark data (new DOI sources, new reference URLs).
- Test additions for existing behavior.
- Documentation of **existing** scenarios and their limitations.

### ⚠️ Requires explicit gate review

- Any change to `engine/verification_gate.py` (thresholds, gate map, policy).
- Any change to `engine/scenario_registry.py` (single source of truth).
- Any change to `data/public_facts.json` (requires CI re-verification).
- Any change to the honesty invariants in `AXIOM_AUDIT.md`.
- New scenarios with `dim > 3` or non-standard oracle functions.
- Any change that alters the count of `PASS / MARGINAL / REFUTED` scenarios.

### ❌ Not accepted

- Attempts to hide, delete, or downgrade REFUTED scenarios.
- Removal of provenance fields (`engine_commit`, `oracle_digest`,
  `ledger_is_stale`).
- Any change that makes the "math-backed verification" claim broader than
  the training-domain interpolation envelope.
- Any change that removes honest limitations from the docs.
- Any request to "just make the number go up."

## Making changes

### Adding a scenario

1. Implement the oracle in `engine/microbiology_full.py`,
   `engine/scenarios_extra.py`, or the appropriate discipline file.
2. Register it in `engine/scenario_registry.py` — **this is the single
   source of truth**; every downstream path (report generator, gate ledger,
   benchmarks, dashboard) reads from this registry.
3. Add a real reference with DOI if the scenario is grounded in a published
   benchmark, or mark it as synthetic and exclude it from `PASS`
   adjudication.
4. Rebuild:
   ```bash
   python scripts/build_reports.py
   python scripts/build_gate_ledger.py
   python scripts/check_public_numbers.py
   ```
5. Update `data/public_facts.json` **via the generator**, never by hand.

### Fixing a bug

1. Write a regression test first, at `tests/`. If the test doesn't fail on
   the current HEAD, it's not a real regression test.
2. Make the fix. Prefer narrow edits; the codebase has grown organically
   over 600+ commits and we value **readability** over cleverness.
3. Run the full gate bundle before opening the PR:
   ```bash
   python scripts/check_public_numbers.py
   python scripts/check_honesty_invariants.py
   python scripts/check_scenario_registry.py
   python scripts/check_anchor_quality.py
   python -m pytest tests/ -q
   ```

## Running the local stack

The deployment target is Cloudflare Pages, but you can develop entirely
locally:

```bash
# Static frontend + API in one process
python scripts/vlab_server.py --port 8765

# Or the CF Pages functions locally
npm install --prefix frontend_fixed
npm --prefix frontend_fixed run dev
```

See `docs/svp-protocol-draft.md` for the formal VLab HTTP contract.

## Code style

- Python 3.11+. Type hints on all new functions.
- **Fail-closed > fail-open.** If you're not sure what should happen,
  return an explicit error rather than a silent default.
- **Comments explain *why*, not *what*.** If the what is unclear, rename.
- No `# noqa` or `# type: ignore` without a written justification in the
  commit message.
- No `except:` without `except (SpecificError, OtherError)`.
- No `pass` in `except:` blocks — if you catch and ignore, name why.

## Review process

There is one maintainer and they are busy. Please:

- Keep PRs **under 200 lines** of changed code. If your change is larger,
  open a design-discussion issue first.
- **Attach the diff of the gate output** in the PR description. The maintainer
  cannot run CI locally for every PR; the diff is the primary review input.
- Do not force-push to a PR branch under review; use additional commits.

## Release process

Releases are driven by `pyproject.toml` version bumps and
`.github/workflows/ci-cd.yml` publishes. Every release candidate must pass:

1. All 4 fail-closed gate scripts.
2. `pytest tests/ -q` — every test.
3. `python scripts/build_reports.py && python scripts/build_gate_ledger.py`.
4. A hand-authored release note explaining what changed and what didn't.

## Credits

SwarmLabs is built on top of:

- [scikit-learn](https://scikit-learn.org/) for baseline models
- [rdkit](https://www.rdkit.org/) for cheminformatics scenarios
- [numpy](https://numpy.org/) / [scipy](https://scipy.org/) for numerical
  kernels
- [Cloudflare Pages](https://pages.cloudflare.com/) for hosting
- The ASME V&V 10-2019 and FDA CM&S report standards

## License

MIT. See [LICENSE](./LICENSE). Contributions are automatically licensed under
the same terms unless you explicitly state otherwise in the PR description.
