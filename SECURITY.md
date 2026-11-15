# Security Policy

## Supported Versions

| Version | Supported | Notes |
|---|---|---|
| `main` (HEAD) | ✅ | Rolling production; daily deploys via `.github/workflows/deploy.yml` |
| `v3` API prefix | ✅ | Public HTTP surface, no auth required for read endpoints |
| `v2` API prefix | ✅ | Legacy auth via `stk_` bearer or `x-api-key` |
| Anything older | ❌ | Not served; if you're on it, upgrade immediately |

## Reporting a Vulnerability

**Please do not open a public GitHub issue for security bugs.** Public disclosure
before a fix exists enables automated scanners to weaponize the bug.

1. **Email** the project owner at **contact@swarmlabs.tools** with
   `Subject: [SECURITY] <short description>`.
2. Include a **minimal reproduction** if possible — a scenario key, an HTTP
   request, or a Python snippet that triggers the issue.
3. If the bug involves an authentication or authorization bypass, do **not**
   include any working API key or bearer token in your report.

### Disclosure timeline

We follow a **90-day coordinated disclosure** policy:

| Day | Event |
|---|---|
| 0 | Vulnerability accepted; fix work begins |
| ≤ 7 | Fix or workaround merged to `main` (best effort) |
| ≤ 90 | Coordinated public advisory + release with fix |

For **critical** vulnerabilities (auth bypass, arbitrary code execution, or
a way to make `PASS` verdicts claimable without evidence), we compress the
timeline to **30 days** and coordinate with your organization's security
team as needed.

### Known-safe-by-design boundaries

The following behaviors are **intentional** and not vulnerabilities:

- `POST /v2/run` is rate-limited per-isolate; a `429` under load is expected.
- The verification gate **blocks autonomous action** on low-confidence
  scenarios. This is the product, not a bug. See
  `docs/competitor_swarmlabs_positioning_2026-09-12.md` § 5.2.
- Scenarios with `verdict=REFUTED` remain in the public ledger. Removing them
  would break `ledger_is_stale()` — see
  `data/gate_ledger.json.provenance.note`.
- The `math-backed verification` claim is scoped to **published held-out
  test sets only**. Predictions outside the training domain return
  `OOD_REJECT` and must not be interpreted as verified.

## Audit scope

- **In scope**: `engine/`, `frontend_fixed/functions/`, `mcp/`, `core/`.
- **Out of scope**: Third-party libraries we depend on (`scipy`, `numpy`,
  `rdkit`, `baybe` when installed). Report those to their upstream projects.
- **Not accepted**: Denial-of-service attacks that require sustained traffic
  above the rate limits documented in `/v3/partner/usage`.

## Automated checks we run

Every commit to `main` runs through a series of fail-closed gates:

- `scripts/check_public_numbers.py` — catches stale or untraceable public
  numbers (scenarios count, PASS/MARGINAL/REFUTED counts, DOI counts).
- `scripts/check_honesty_invariants.py` — enforces the honesty invariants
  in `AXIOM_AUDIT.md`.
- `scripts/check_scenario_registry.py` — asserts the registry single-source
  of truth, including build-completeness.
- `scripts/check_anchor_quality.py` — rejects synthetic-looking anchor data.
- `tests/test_vlab_contract.py` — V&V contract tests.

If you find a bypass in any of these gates, we consider it **high severity**.
The gates are the primary defense against **silent drift** between the
advertised numbers and the actual computation; bypassing them undermines
every downstream claim in the project.

## Data handling

We do not currently collect personal data beyond what is required to
authenticate API calls (`x-api-key` and account email). Anchor data in
`data/real_anchors.json` is exclusively from **publicly accessible Zenodo,
Figshare, and preprint sources** with DOI provenance. We do not accept anchor
data submitted through GitHub Issues — see `SECURITY.md` reporting flow
for authenticated contributions.
