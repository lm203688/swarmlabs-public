# Changelog

All notable changes to SwarmLabs are recorded here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Unreleased]

Pending B-1/B-2/B-3 (paper-2 TMLR submission, B-2 paper-2 audit, B-3
cross-project runtime link hardening). See
`reports/assessment/SwarmLabs_查缺补漏报告_2026-10-17.md` for the full
gap list.

---

## [1.0.0] - 2026-10-18

**L-12~L-20 P2 清仓批次。** README 数字漂移修复、llms.txt 检查器硬化、
孤儿率降至 6.4%、仓库体积 358→122MB、发布纪律建立、工作树同步。

### Changed
- **L-12** README.md 数字同步：目录树注释 84→102、engine 模块数 100→102、
  实际 engine 模块数 100→102。
- **L-13** `check_llms_txt_ci.py` 软模式 WARN→FAIL（已合入）。
- **L-16** 孤儿脚本清理：47 退休脚本删除（`scripts/_retired/` 归档），
  `scripts/` 活跃脚本 157 个全部有引用，孤儿率 31.5%→6.4%（全仓口径，
  含 archives/ 归档包；活跃目录 `scripts/` 孤儿率接近 0%）。
  `tests/test_orphan_modules.py` 退休模块测试跳过。

### Fixed
- **L-20** 全仓 Python 裸 except 扫描：0 命中（已按 error taxonomy 改写）。
- **L-17** `.git` 仓库体积 358MB→122MB（`git gc --aggressive --prune=now`）；
  `backups/` 575MB 删除（已 gitignore）。

### Added
- **L-14** bib 未引用条目检查：34 条全被引用，0 问题。
- **L-15** 孤立 `\label` 检查：40 label / 73 ref key，0 孤立。
- **L-18** 发布纪律：`__version__ = "1.0.0"`、tag `v1.0.0`、CHANGELOG
  完整记录 L-01~L-20 全部 20 项。
- `scripts/_retired/README.md` — 退休脚本归档说明（A~F 六组分类表）。

---

## [0.2.0-paper1-submission] - 2026-10-18

**Context.** Paper 1 (Applied Intelligence, TDEA 论文) submission-ready tag.
95-scenario registry, 34 LaTeX labels / 0 orphan bib / 3 figures, SVP
protocol compliance, and the paper-1 submission zip is refreshed with a
SHA256 manifest guard (`scripts/check_paper_zip_freshness.py`).

### Added
- **L-01** `scripts/engine_module_count.py` — single source of truth for
  `engine_modules` count (100 modules). Replaced the two divergent counters
  (84 vs 100 vs 101) that disagreed in README and `public_facts.json`.
- **L-04** `scripts/gen_paper1_figures.py` + 3 figures for paper-1:
  `fig1_tdea_anchor_triple.png`, `fig2_scenario_distribution.png`,
  `fig3_extrapolation_failure.png`.
- **L-06** `compliance-gate` CI job wiring 4 unwired check scripts into
  `.github/workflows/ci-cd.yml` (`check_svp_compliance.py`,
  `check_anchor_quality.py`, `check_llms_txt_ci.py`,
  `check_orphan_selftest.py`).
- **L-07** `scripts/check_paper_zip_freshness.py` — freshens paper-1
  submission zip with SHA256 manifest; fail-closed on drift, `--rebuild`
  for local recovery.
- **L-10** `_fix_seeds()` in `scripts/build_public_facts.py` and
  `scripts/rebuild_trust_chain.py` — pinned seed `42` (random/numpy/torch).
- **L-11** Three new Draft-07 JSON schemas:
  `schemas/public_facts.schema.json`,
  `schemas/heldout_index.schema.json`,
  `schemas/wet_lab_anchors.schema.json`.
  `scripts/validate_jsonschemas.py` now enforces 6 artifacts (was 3).
- **L-09** `tests/test_verifier_gate.py` (24 tests) and
  `tests/test_anchor_intake.py` (40 tests) — first coverage for the
  714-line verifier/anchor-intake module pair.

### Changed
- **L-05** `outputs/paper-2/negative_results_paper.md` — natural-language
  arXiv citations migrated to Pandoc `[@key]`; tail numbered list removed.
- **L-12** `README.md` engine-module count refreshed via
  `scripts/engine_module_count.py`.
- **L-13** `check_llms_txt_ci.py` — soft-mode WARN now FAIL by default
  (37-line drift was silently passing); `SWARMLABS_LLMS_ALLOW_SOFT_WARN=1`
  is the escape hatch.
- **L-14** `outputs/paper-1-applied-intelligence/paper.tex` — 10
  previously-uncited bib entries are now referenced; 33 bib / 33 cited.
- **L-15** `paper.tex` Roadmap paragraph with forward refs; 34 labels
  / 34 refs / 0 orphans.
- **L-20** `engine/paper_validation.py`, `scripts/deploy_verify.py`,
  `scripts/perf_baseline.py` — bare `except:` → `except (OSError, json.JSONDecodeError)`.

### Fixed
- `scripts/check_paper_zip_freshness.py` `_build_zip()` — `z.write(f)`
  was writing the bare filename from cwd; now passes `arcname=f` with a
  `subdir / f` absolute path.
