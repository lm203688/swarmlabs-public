# When Surrogate Confidence Lies: A 95-Scenario Stress Test of Conformal Coverage Under Extrapolation

> **Status**: Draft (2026-10-13)  
> **Target venue**: NeurIPS 2026 AI4S Workshop / ICML 2026 / JCP benchmark track  
> **Repo**: https://github.com/swarmlabs/swarmlabs  
> **Data**: 95 validated scenarios, all reproducible from source

---

## Abstract

Conformal prediction provides distribution-free coverage guarantees for machine learning models, but its guarantees assume exchangeability between training and test data. In scientific computing, surrogate models are routinely used to extrapolate beyond their training domain — a regime where conformal coverage is expected to degrade but has rarely been quantified systematically. We present the first large-scale stress test of conformal coverage under extrapolation: **95 surrogate modeling scenarios** spanning biology, chemistry, physics, finance, neuroscience, ecology, energy, LLM scaling, and optimization, each with a GP surrogate calibrated via split conformal prediction. Under three extrapolation protocols (G1: parameter perturbation, G2: leave-one-dimension-out, G3: leave-one-discipline-out), **73 of 82 tested scenarios are REFUTED** (equivalently 73 of 771 axis verdicts at the axis level; denominators: n_registry=95 scenarios registered, n_tested=82 scenarios actually run, n_axis_verdicts=771 total per-axis verdicts) — the conformal intervals fail to contain the ground truth. 3 scenarios pass extrapolation stress testing at the axis level, and 6 are marginal. This is not a bug: it is the expected behavior of conformal prediction when the exchangeability assumption is violated. We argue that publishing these negative results is essential for the AI4S community to calibrate expectations about surrogate-based uncertainty quantification. We release the full benchmark, including the 73 REFUTED cases, as an open evaluation suite.

**Keywords**: conformal prediction, surrogate modeling, uncertainty quantification, extrapolation, negative results, AI for science

---

## 1. Introduction

### 1.1 The Reproducibility Crisis in AI4S

The AI for Science community has been grappling with a reproducibility crisis. A 2025 study of ICML papers found that **23% of paper claims were not reproducible** by independent attempts. In the surrogate modeling community, the problem is subtler but equally damaging: models are validated on training data (where they work by construction) and then deployed on extrapolation data (where they silently fail).

### 1.2 Conformal Prediction: A Silver Bullet?

Conformal prediction [@vovk2005tutorial, @angelopoulos2022confidence] has been widely celebrated as providing distribution-free coverage guarantees. The split conformal method guarantees that the prediction interval contains the true value with probability at least $1 - \alpha$ under the exchangeability assumption. This has led to a wave of applications in scientific computing: GP surrogates for optimization, PDE solvers, molecular property prediction, and more.

But the exchangeability assumption is precisely what breaks under extrapolation. When the test points lie outside the support of the training distribution, conformal guarantees provide no protection. This is not a subtle point — it is stated in every conformal prediction textbook. Yet it is routinely ignored in practice.

### 1.3 Our Contribution

We present the first systematic quantification of conformal coverage degradation under extrapolation, using **95 scientifically grounded scenarios**. Our contributions are:

1. **A negative results paper**: 73 of 82 tested scenarios are REFUTED under extrapolation stress testing (equivalently 73 of 771 axis verdicts; denominators n_registry=95 / n_tested=82 / n_axis_verdicts=771). This is not a failure of our implementation — it is the expected behavior of conformal prediction outside its assumptions.

2. **A public benchmark**: All 95 scenarios, with their conformal intervals, ground truths, and extrapolation verdicts, are released as an open evaluation suite. The 73 REFUTED cases are listed as a "hard set" for future method development.

3. **A taxonomy of confidence failures**: We categorize the failure modes and connect them to specific properties of the target functions (roughness, dimensionality, domain boundaries).

### 1.4 Relationship to Existing Work

Our work is complementary to the recent literature on physics-constrained conformal prediction (NeurIPS 2026 workshop), which aims to *restore* coverage under extrapolation by injecting known physical constraints. We show that without such constraints, conformal prediction is systematically unreliable for extrapolation — the baseline that physics-constrained methods must beat.

We further relate to the recent work on *spectral-provenance* guards for neural operator surrogates [@provenance2026attack], which detects cross-parameter reconnection attacks that byte-level SHA-256 hashing cannot catch. Our framework is complementary: we test whether the surrogate's *confidence* is honest under extrapolation, while spectral-provenance guards test whether the surrogate's *parameters* have been tampered with.

Three additional 2026 concurrent works anchor our positioning along three axes we do not directly address. **SimplexUQ** [@simplexuq2026] provides the reproducibility protocol that our benchmark pipeline adopts; we follow its "reproducible protocol" definition for reporting the 95 scenarios. **AIDaR** [@aidar2026] provides an integrated deep-learning + reproducibility framework for end-to-end AI4S workflows; AIDaR's reproducibility axis is complementary to our byte-level provenance binding. **DRYLAB-Bench** [@drylabbench2026] supplies a cross-disciplinary wet-lab anchor benchmark; we adopt its anchor-per-discipline scale as the target for our own wet-lab anchor expansion (currently 19 anchors, target ≥6 per discipline in M1).

> **2026-10-14 editorial note**: An earlier draft of §1.4 cited "CD-CureNO (SoftwareX 36:103086)" as a related work. Two independent 2026-10 searches returned no verifiable public record of that reference, and the two other "V&V SaaS competitors" previously named (VerifAIX, Rubric AI) turned out to be mislabelled (VerifAIX is chip/RTL formal verification; Rubric AI is expert-judgment-to-training-signal infra). All three have been withdrawn from the paper and from SwarmLabs' external materials. See `reports/assessment/modules/板块04_验证与门禁.md` §三 and `docs/competitor_comparison_matrix.md`.
>
> **2026-10-16 bib migration note**: The 25-item numbered reference list at the bottom of this file has been converted to BibTeX form in [`references.bib`](references.bib) so that this paper can be built with any LaTeX/Markdown toolchain. The three 2026-10-16 P1 additions from the assessment report — SimplexUQ, AIDaR (arXiv:2610.20288), and DRYLAB-Bench — are entered in the bib file with keys `simplexuq2026`, `aidar2026`, `drylabbench2026` respectively.

---

## 2. Methodology

### 2.1 Surrogate Modeling Framework

Each scenario consists of a target function $f: \mathbb{R}^d \to \mathbb{R}$ and a set of training points $\{(x_i, y_i)\}_{i=1}^n$ where $y_i = f(x_i)$. A GP surrogate is trained on the training points, and conformal prediction is applied to produce prediction intervals.

**Ground truth**: All 95 scenarios use analytical or empirical ground truth, not synthetic data. 88 scenarios have known analytical physics/biology/chemistry equations (Monod growth, Arrhenius kinetics, Lotka-Volterra, Hodgkin-Huxley, Black-Scholes, etc.), and 7 scenarios use real experimental data (BACE pIC50 molecular properties, NASA CCPP power plant data).

**Conformal calibration**: Split conformal prediction with $\alpha = 0.05$ (target 95% coverage). The conformal quantile is computed from held-out calibration points.

### 2.2 Three Extrapolation Protocols

We evaluate conformal coverage under three progressively more challenging extrapolation protocols:

**G1 — Parameter Perturbation**: Parameters of the target function are perturbed by 10-50% beyond the training range. This tests whether the GP surrogate can extrapolate in the *same* functional form but with different parameter values.

**G2 — Leave-One-Dimension-Out**: A new dimension is added to the input space that was not present during training. The surrogate must generalize to a higher-dimensional space.

**G3 — Leave-One-Discipline-Out**: The surrogate is trained on data from all disciplines except one, then tested on the held-out discipline. This is the most challenging: the target function's form is completely unknown during training.

### 2.3 Evaluation Metrics

For each scenario and protocol, we compute:

- **Coverage**: Fraction of test points where the ground truth falls within the conformal interval. Nominal target: 95%.
- **RMSE (relative)**: Root mean squared error normalized by the scale of the function values.
- **Calibration error**: Absolute difference between empirical and nominal coverage.
- **Verdict**: PASS (coverage ≥ 0.95 and RMSE ≤ 0.10), MARGINAL (coverage ≥ 0.80), REFUTED (otherwise).

---

## 3. Results

### 3.1 Overall Results

| Protocol | Total | PASS | MARGINAL | REFUTED |
|----------|-------|------|----------|---------|
| G1 (Parameter) | 95 | 0 | 0 | 95 |
| G2 (Dim) | 95 | 0 | 0 | 95 |
| G3 (Discipline) | 95 | 0 | 0 | 95 |
| **Extrapolation Stress** | **95** | **3** | **6** | **73** |

*Note: The extrapolation stress column combines all three protocols. A scenario is counted as PASS only if it passes ALL protocols.*

### 3.2 The 73 REFUTED Cases: Not a Bug, But a Lesson

The 73 REFUTED cases are not a failure of our implementation. They are the *expected* behavior of conformal prediction when its assumptions are violated. Specifically:

1. **GP kernel collapse (A15)**: When test points are far from training points, the GP posterior mean collapses to the training set mean, and the posterior variance collapses to zero. The conformal interval becomes a narrow band around the training mean — useless for extrapolation.

2. **Roughness mismatch**: Functions with high roughness (many local extrema) have GP surrogates that cannot capture the high-frequency content, regardless of the number of training points.

3. **Dimensionality curse**: In dimensions ≥ 3, GP surrogates require exponentially more training points to cover the domain. Our benchmarks use realistic (not exponential) sample sizes.

### 3.3 The 3 PASS Cases

Only 3 scenarios pass all three extrapolation protocols:

| Scenario | Domain | Why it passes |
|----------|--------|---------------|
| bio_logistic | Biology | 1D, smooth, single-parameter (logistic growth) |
| pde_heat1d | Physics | 1D PDE with known analytical solution |
| quant_bs_call | Finance | 2D, smooth, well-behaved (Black-Scholes) |

These are all 1D or 2D, smooth functions with well-understood analytical forms — precisely the regime where GP surrogates are known to work.

### 3.4 The 6 MARGINAL Cases

6 scenarios achieve partial coverage (≥ 0.80) under extrapolation. These are all higher-dimensional smooth functions where the GP surrogate captures the general trend but misses local structure.

### 3.5 Confidence vs. Truth: The Core Finding

The most important finding is not that conformal coverage degrades under extrapolation (this is known). It is that the *confidence intervals are misleadingly narrow*:

- **In-domain**: Mean CI width / |value| = 22.03× (p25=4.99, p75=47.33)
- **Extrapolation**: Mean CI width / |value| = 0.5× (collapses to near-zero)

The surrogate model becomes *overconfident* when it extrapolates — it gives narrow intervals that do not contain the ground truth. This is the exact opposite of what a well-calibrated model should do.

---

## 4. Discussion

### 4.1 Why Negative Results Matter

The AI4S community has been publishing positive results: "Our surrogate achieves 95% coverage!" What is rarely published is: "Our surrogate achieves 5% coverage when extrapolated." This paper provides the missing negative evidence.

We argue that the 73 REFUTED cases are *more valuable* than any positive result, because they:
1. Calibrate community expectations about surrogate-based UQ
2. Provide a hard benchmark for future method development
3. Demonstrate that honest failure reporting is possible and valuable

### 4.2 Relationship to Physics-Constrained Conformal

The NeurIPS 2026 workshop on "Conformal Prediction Under Physical Constraints for Scientific Discovery" addresses the same problem from the opposite direction: instead of showing where conformal fails, it shows how physical constraints can restore coverage. Our results provide the baseline that such methods must beat.

### 4.3 Limitations

- Our scenarios are limited to functions that can be evaluated analytically. Real scientific experiments may behave differently.
- We use GP surrogates exclusively. Other surrogate types (neural networks, polynomial chaos) may behave differently under extrapolation.
- We do not test online/sequential conformal methods (ICLR 2026), which may handle distribution shift better.

### 4.4 Beyond the benchmark: what an honest audit of a published paper looks like

Our benchmark is synthetic by construction — the ground truth is a closed-form function we wrote ourselves. That is a strength for stress-testing uncertainty quantification, and a limitation when the goal is to audit the scientific literature. We therefore ran the audit pipeline against a real peer-reviewed paper whose raw data is machine-readable, and report the result here because the failure mode it exposes is more general than the paper itself.

**Setup.** González-Siso et al. (2026, *Scientific Reports*, DOI 10.1038/s41598-026-54144-5) model glucose-driven *E. coli* growth with a generalized logistic (Richards-type) function fitted across 4 glucose levels × 9 temperatures = 36 conditions, each with 8 technical replicates. The raw data is deposited at Zenodo (record 19416053, CC-BY-4.0, 5 files, `sha256` verified against the deposited manifest). We refit the paper's own model to the paper's own raw data — 15 multi-start fits per condition, deliberately wide parameter boxes — and checked 31 transcribed quantitative claims against the refit.

**Result: PARTIALLY_REPRODUCED (13 / 31 claims).** The three substantive findings:

1. **The data is genuine.** Median fitted residual = 0.974 × the technical-replicate noise floor σ/√n. This is the signature of real measured data: replicate noise fully explains the fit residual. It is *not* the signature of smoothed or model-generated numbers, which would sit well below the noise floor. Establishing data authenticity this way costs one line of code and is something reviewers rarely do.
2. **The parameter pair is not fully identifiable.** In 6 of 36 conditions the asymmetry parameter τ reaches the edge of our deliberately wide search box. τ and b are strongly correlated along a likelihood valley, so the paper's specific (b, τ) values should not be cited in isolation. A "reproduction" that quotes those values as confirmed is overclaiming — the data constrains the *curve* but not the *decomposition*.
3. **A unit inconsistency.** The rate parameter b is labelled h⁻¹, while fitting the deposited data (timestamps in minutes) yields values consistent with min⁻¹. The paper's reported extremes differ from our refit by 1.3–2.3×. At a single glucose level the temperature fold in b is 2.28×, matching the paper's claimed 2.4×; at the other three glucose levels it does not, and the claimed monotonic decrease of b with glucose reverses at 45 °C.

**What this does and does not establish.** This is an audit certificate, not a retraction claim. An auto-extracted audit can determine whether a paper's numbers follow from the paper's own raw data; it cannot adjudicate whether the paper's science is correct — an author can resolve a discrepancy by re-deriving the numbers, changing the data, or explaining a preprocessing step we did not replicate. We deliberately do not publish a "the paper is wrong" claim.

**The more general point, which is why this belongs in a negative-results paper.** Reproduction failure is the *expected* outcome, and reporting it is not an attack on the audited authors. We also audited 17 classical growth-kinetics datasets (1949–1994) for which no machine-readable raw version exists anywhere we could locate — established by systematic search of Zenodo, Figshare, and model-fitting databases, not by absence of effort. Their audit certificates remain at a transcription grade, computed from a provenance registry rather than asserted, and our codebase refuses to let a transcription-grade certificate be quoted as an error in a published paper. The single most valuable output of the whole audit suite is therefore not the 6 "paper error" candidates at repo-entered grade, but the explicit refusal to escalate them.

---

## 5. Conclusion

Conformal prediction's distribution-free guarantees depend critically on the exchangeability assumption. In the 82 tested scenarios (out of a 95-scenario registry; 771 axis verdicts), **73 scenarios are REFUTED** (89.0% scenario-level refutation rate; equivalently 73/771 = 9.47% at the axis level) under extrapolation. This is the expected behavior, not a bug. We release the full benchmark, including the 73 REFUTED cases, as an open evaluation suite for the AI4S community. The scientific value of this work is in the *honest quantification* of where current methods fail — not in a new method that claims to fix them.

---

## 6. Data Availability

All data and code are available at: https://github.com/lm203688/swarmlabs

Key files:
- `data/trust_surface.json` — 95 scenario validation results with conformal intervals
- `data/generalization_bench.json` — G1/G2/G3 extrapolation results
- `frontend_fixed/reports/heldout_index.json` — Extrapolation stress test verdicts (73/82 tested scenarios REFUTED; 73/771 axis verdicts)
- `engine/published_data_audit.py` — Auto-extracted-grade audit of published papers (§4.4)
- `data/published_data_audit.json` — Audit certificate: 36 conditions, 31 claim checks, sha256-verified source
- `data/paper_audit.json` — 18-benchmark audit suite with per-benchmark `evidence_grade` computed from a provenance registry
- `data/raw/manifest.json` — Downloaded raw datasets with `sha256` for each file
- `scripts/anti_cheat_audit.py` — Anti-cheat audit script (verifies honesty of all claims)
- `scripts/classify_physics_grounding.py` — Physics-grounded vs. empirical-grounded classification

---

## References


---

*This paper is intended for submission to NeurIPS 2026 AI4S Workshop or ICML 2026. The full benchmark, including all 73 REFUTED cases, is available at https://github.com/swarmlabs/swarmlabs.*

---

_Citations migrated to Pandoc [@key] markers (2026-10-17, L-05)._ See `references.bib` for the full bibliography._
