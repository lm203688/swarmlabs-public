# Terminal-Bench-Science contribution: `surrogate-anchor-gap-triage`

Status: **task built and self-validated locally. Not yet proposed, not yet
submitted.** Terminal-Bench-Science requires proposal approval *before* a task is
built, so the proposal form must be filed first and the build redone or
re-purposed under that approval.

Nothing here has been published. No repository has been forked, no PR opened, no
external account touched.

---

## 1. What this is

A benchmark task in which an agent must triage twelve surrogate models for use
in place of wet-lab experiments, then allocate a three-run confirmation budget.

The scientific pattern is the one the ecosystem keeps hitting: **candidate
generation is cheap, physical confirmation is not.** Every model in the
catalogue fits its held-out data well. Three are unusable, and only one of
those three is blocked for a reason visible in the numerical columns.

The load-bearing case is `M05`: second-best r², best empirical coverage of any
anchored model, second-lowest calibration error, and contradicted by a published
wet-lab measurement. Its numerical profile is excellent and the verdict is
permanent.

This is the pattern observed at population scale in recent literature — for
example RayGun (Nature 2026, DOI 10.1038/s41586-026-10842-8), which generated
500,000 candidate molecules and confirmed 11 of them, a 2.2×10⁻⁵ confirmation
rate. The task is a single-agent version of that decision, at a scale a terminal
can hold.

## 2. Why it is hard

Four things, in order of how likely they are to break an agent.

1. **The obvious metric is not the gate.** Fit is near-ceiling for all twelve
   models. An agent that ranks on r² or coverage produces a confident, wrong
   answer.
2. **An anchor contradiction overrides fit.** The columns are all present and
   self-labelling. Nothing is hidden — the agent must know that a contradicted
   anchor is disqualifying regardless of in-sample quality.
3. **Coverage error is asymmetric, and both directions appear.** `M08`
   under-covers (0.78 vs 0.95 claimed) and is `BLOCKED`. `M10` over-covers
   (1.00 vs 0.95) and is `APPROVED`. Getting both right requires applying the
   asymmetry in opposite directions on two rows.
4. **The budget rank is not the naive rank.** Four models are pending and three
   confirmations exist. `M12` has the worst fit in the catalogue yet ranks above
   `M11`, because the ordering is by miscalibration, not by fit.

A reviewer's hint file is included at `authoring/hint.md`, graduated gentlest to
most explicit and containing no answer.

## 3. Verifiability

Separate verifier mode, no network in the verifier, access only to two declared
artifacts. Binary reward — all-or-nothing, because approving a contradicted
surrogate is not half right. Seven structural checks run before the two
correctness checks so a malformed submission fails with a readable message, and
diagnostics go to `metrics.json` (which Harbor ignores) so a reviewer can see
exactly which models were mis-triaged.

The expected answer is written flat and independently re-derived from the rules
by a second implementation, so a mis-transcribed threshold cannot silently make
the verifier accept a wrong answer.

## 4. Reproducing the evidence locally

```bash
cd tasks/mathematical-sciences/statistics/surrogate-anchor-gap-triage
python3 authoring/provenance/generate_dataset.py   # regenerate data + truth
python3 authoring/evidence/check_rules.py          # rules vs expected answer
python3 authoring/evidence/local_validation.py     # oracle must score 1.0
```

The third command reconstructs Harbor's container layout under a temporary
directory and points the oracle and verifier at it, so the task is proved
solvable without Docker.

## 5. What must happen before this can be submitted

These are process constraints from the upstream contribution guide, not
judgements about the task.

1. **Proposal form first.** Ideas are filed through the project's task proposal
   form and reviewed — automatically and then by human reviewers — before
   implementation is welcome. A `new task` pull request without an approved
   proposal fails a static check in upstream CI. So the file above is not a
   shortcut to skip the form; it is preparation for it.
2. **Deadline.** The project's v0.2 call closes **2026-10-05**. Most accepted
   tasks went through several review rounds, so a late filing is unlikely to
   make the release.
3. **Canary strings.** Every task file carries the benchmark canary string, so
   published material is detectable and excludable in training pipelines. All
   files in this draft do.
4. **No network in the verifier.** Both phases declare their network policy
   explicitly: the agent phase is public, the verifier phase is not.
5. **Data provenance.** Every artifact a task fetches must come from the
   benchmark repo or the project's own HuggingFace org. This task fetches
   nothing — the catalogue is generated locally and is ~1 KB, so no external
   hosting is needed at all.

## 6. Fields you must set or confirm yourself

These touch your name and your disclosure, so they are drafted rather than
filled in.

- `relevant_experience` — drafted as founder/lead engineer of SwarmLabs. If
  that is not how you want to present yourself, change it. Upstream treats an
  inaccurate account of qualifications as grounds for exclusion.
- `conflicts_of_interest` — drafted to disclose that SwarmLabs is your company
  and that this methodology is your product's. Upstream asks for commercial
  affiliation to be disclosed rather than hidden; a vendor affiliation is
  reportable, concealment is disqualifying. Confirm this is what you want on
  record.
- `author_profile` — left empty. Upstream accepts a Scholar, LinkedIn, or
  personal site.
- `author_organization` — drafted as `SwarmLabs`. If you would rather submit as
  an independent contributor, that is a legitimate choice and the disclosure
  text should change with it.
- `research_advisor` / `referred_by` — left empty.

Upstream also expects contributors to stay reachable until the final release and
to help analyse *why* agents fail on the task. That is a condition of
co-authorship, so weigh it before proposing.

## 7. Honest caveats about the task itself

- The catalogue is synthetic. It is deliberately so — it has no proprietary or
  third-party data, which is what makes it submittable — but a reviewer may ask
  for real-provenance justification. The pattern it encodes is real; the numbers
  are not from a live experiment.
- Twelve rows is small. The task is meant to be a reasoning test, not a data
  task. That is defensible, but it is a design choice a reviewer will notice.
- The classification rules are stated in the instruction only by implication.
  The marking thresholds are withheld on purpose, matching how a real review
  works. A reviewer could judge that the instruction under-specifies; if so, the
  fix is to name the ordering of precedence without publishing the numbers.
- Nothing has been run on Harbor. Local validation proves the oracle scores 1.0
  against its own verifier; it does not prove the Docker build, the artifact
  transfer, or the separate-verifier mount behave. Those only resolve in
  upstream CI.
- `catalog.csv` cannot carry a canary marker: any leading line would be parsed
  as a thirteenth catalogue row and change the task. Its canary is carried by a
  sibling `environment/data/NOTICE.txt`, which also states the bundle's
  provenance. The local contract check treats that as an explicit, narrow
  exception rather than exempting all data files. Confirm upstream's
  `check-canary` accepts this arrangement before relying on it.
