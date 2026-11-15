#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_zenodo_package.py — A-5：组装 Zenodo 存档包 + 元数据（2026-10-18）。

背景（第九轮科学性评估报告，A-5）：
    TMLR/ICLR 均鼓励（部分要求）artifacts 存档 + DOI。本脚本把论文复现所需
    的最小产物集组装成一个确定性目录树：

        archives/zenodo-package-<date>/
          .zenodo.json     Zenodo 元数据（上传时自动识别，字段见 zenodo.org 规范）
          MANIFEST.json    每个收录文件的 sha256 + 字节数（防篡改、可独立核对）
          README.md        存档内容说明 + 复现步骤
          code/            engine/ + scripts/ + tests/ + schemas/ + requirements.txt
          papers/          outputs/paper-1（tex+bib+figures+投稿 zip）+ outputs/paper-2
          data/            论文引用的核心数据产物（ curated 白名单，非全量 data/）

刻意排除（体积大且与论文结论无直接关系）：
    data/ml_models（173MB 训练权重）、data/entity_store.json（20MB 实体库）、
    data/blog_index_backups、data/_early_import、data/raw、各类 .bak。

纪律：
    - 白名单显式列出，不递归整个 data/——新增数据文件不会悄悄进存档，
      也不会因漏掉关键文件而静默（缺文件即报错退出）。
    - MANIFEST 记录 sha256：存档包的任何后续漂移都可被第三方检出。
    - DOI 由用户在 Zenodo 上传后获得，脚本**不生成、不伪造** DOI；.zenodo.json
      的 related_identifiers 只引用已存在的 GitHub 仓库。

用法：
    python scripts/build_zenodo_package.py                 # 默认日期=今天
    python scripts/build_zenodo_package.py --date 2026-10-18
    python scripts/build_zenodo_package.py --verify        # 只校验已有存档包
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: 代码收录项（相对仓库根）
CODE_ITEMS = (
    "engine",
    "scripts",
    "tests",
    "schemas",
    "requirements.txt",
    "LICENSE",
    "README.md",
)

#: 论文产物收录项（相对仓库根）
PAPER_ITEMS = (
    "outputs/paper-1-applied-intelligence",
    "outputs/paper-2",
)

#: 数据收录白名单（相对仓库根；缺一即报错——fail-loud，不静默缺文件）
DATA_ITEMS = (
    "data/gate_ledger.json",
    "data/trust_surface.json",
    "data/public_facts.json",
    "data/ablation_plan_report.json",
    "data/sensitivity_ablation.json",
    "data/second_backend_replication.json",
    "data/paper_audit.json",
    "data/paper_audit_report.md",
    "data/published_data_audit.json",
    "data/published_data_audit_report.md",
    "data/extrapolation_stress.json",
    "data/generalization_bench.json",
    "data/g3_aligned_benchmark.json",
    "data/real_anchors.json",
    "data/provenance_attack_reproduction.json",
    "data/conformal_calibration.json",
    "data/adaptive_conformal.json",
    "data/adversarial_probes.json",
    "data/anchor_independence.json",
    "data/ablation_priority.json",
    "benchmarks/extrapolation-bench",
    "frontend_fixed/reports/heldout_index.json",
    "frontend_fixed/reports/wet_lab_anchors.json",
)

#: Zenodo 元数据（.zenodo.json）。DOI 字段留空——上传后由 Zenodo 分配。
ZENODO_META = {
    "title": ("SwarmLabs TDEA: Three-Dimensional Evidence Anchoring with "
              "Fail-Closed Verification Gates for AI4S Virtual Laboratories "
              "(artifacts)"),
    "description": (
        "Artifact archive for the paper \"A Three-Dimensional Evidence Anchoring "
        "Framework with Fail-Closed Verification Gates for AI4S Virtual "
        "Laboratories\" (Applied Intelligence submission) and its companion "
        "negative-results paper. Contains: (1) the verification engine, gate "
        "suite, and test suite (code/); (2) both manuscripts with figures "
        "(papers/); (3) the published data products cited by the papers: the "
        "95-scenario gate ledger, trust surface, ablation plan with graded "
        "verdicts, parameter-perturbation sensitivity audit, extrapolation "
        "stress test, and wet-lab anchor corpus (data/). Every file is "
        "checksummed in MANIFEST.json. Reproduce with: "
        "pip install -r code/requirements.txt && python "
        "scripts/build_gate_ledger.py."
    ),
    "creators": [
        {"name": "Li, Lixing", "affiliation": "Independent Researcher, SwarmLabs"}
    ],
    "keywords": [
        "AI for Science",
        "virtual laboratory",
        "evidence anchoring",
        "verification gates",
        "uncertainty quantification",
        "conformal prediction",
        "negative results",
        "reproducibility",
    ],
    "license": "MIT",
    "upload_type": "software",
    "access_right": "open",
    "communities": [{"identifier": "aisc"}],
    "related_identifiers": [
        {
            "identifier": "https://github.com/lm203688/swarmlabs",
            "relation": "isSupplementedBy",
            "resource_type": "software",
        }
    ],
    "notes": (
        "DOI is assigned by Zenodo upon deposition; this archive intentionally "
        "ships without a pre-registered DOI. All headline numbers in the "
        "manuscripts are reproducible from data/ with the code in code/."
    ),
}


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _copy_item(src: Path, dst: Path) -> int:
    """复制文件或目录（目录递归，跳过 __pycache__/.pyc）。返回文件数。"""
    if src.is_file():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return 1
    n = 0
    for f in sorted(src.rglob("*")):
        if f.is_dir():
            continue
        if any(part in ("__pycache__", ".pytest_cache") for part in f.parts):
            continue
        if f.suffix == ".pyc":
            continue
        rel = f.relative_to(src)
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, target)
        n += 1
    return n


def build(date_str: str) -> Path:
    out_dir = ROOT / "archives" / f"zenodo-package-{date_str}"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    (out_dir / "code").mkdir(parents=True)
    (out_dir / "papers").mkdir(parents=True)
    (out_dir / "data").mkdir(parents=True)

    manifest_files: dict[str, dict] = {}
    stats = {"code": 0, "papers": 0, "data": 0}
    missing: list[str] = []

    def record(bucket: str, root: Path, file: Path) -> None:
        rel = file.relative_to(out_dir).as_posix()
        manifest_files[rel] = {
            "sha256": _sha256(file),
            "bytes": file.stat().st_size,
        }
        stats[bucket] += 1

    # ── code ──
    for item in CODE_ITEMS:
        src = ROOT / item
        if not src.exists():
            missing.append(item)
            continue
        dst = out_dir / "code" / item
        n = _copy_item(src, dst)
        if src.is_file():
            record("code", out_dir, dst)
        else:
            for f in sorted(dst.rglob("*")):
                if f.is_file():
                    record("code", out_dir, f)
        print(f"  code/{item}: {n} 文件")

    # ── papers ──
    for item in PAPER_ITEMS:
        src = ROOT / item
        if not src.exists():
            missing.append(item)
            continue
        dst = out_dir / "papers" / item.split("/", 1)[1]
        for f in sorted(src.rglob("*")):
            if f.is_file():
                target = dst / f.relative_to(src)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, target)
                record("papers", out_dir, target)

    # ── data ──
    for item in DATA_ITEMS:
        src = ROOT / item
        if not src.exists():
            missing.append(item)
            continue
        dst = out_dir / "data" / item.split("/", 1)[1]
        if src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            record("data", out_dir, dst)
        else:
            for f in sorted(src.rglob("*")):
                if f.is_file():
                    target = dst / f.relative_to(src)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, target)
                    record("data", out_dir, target)

    if missing:
        print(f"ERROR: 白名单文件缺失（fail-loud，不静默缺文件）: {missing}",
              file=sys.stderr)
        raise SystemExit(1)

    # ── .zenodo.json ──
    meta = dict(ZENODO_META)
    meta["publication_date"] = date_str
    (out_dir / ".zenodo.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # ── MANIFEST.json（含 .zenodo.json 自身）──
    manifest_files[".zenodo.json"] = {
        "sha256": _sha256(out_dir / ".zenodo.json"),
        "bytes": (out_dir / ".zenodo.json").stat().st_size,
    }
    manifest = {
        "archive": f"zenodo-package-{date_str}",
        "built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_repo": "https://github.com/lm203688/swarmlabs",
        "n_files": len(manifest_files),
        "total_bytes": sum(v["bytes"] for v in manifest_files.values()),
        "files": dict(sorted(manifest_files.items())),
    }
    (out_dir / "MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # ── README.md ──
    readme = f"""# SwarmLabs TDEA — Zenodo Artifact Archive ({date_str})

This archive accompanies:

1. **Paper 1** — *A Three-Dimensional Evidence Anchoring Framework with
   Fail-Closed Verification Gates for AI4S Virtual Laboratories*
   (Applied Intelligence submission; `papers/paper-1-applied-intelligence/`).
2. **Paper 2 (companion)** — the negative-results paper on extrapolation
   failure (`papers/paper-2/`).

## Contents

| Path | What |
|---|---|
| `code/` | Verification engine (`engine/`), build & gate scripts (`scripts/`), test suite (`tests/`), JSON schemas (`schemas/`), `requirements.txt`, MIT `LICENSE` |
| `papers/` | Both manuscripts, BibTeX, figures (300 dpi PNG), and the paper-1 submission zip |
| `data/` | The published data products cited by the papers: gate ledger (95 scenarios), trust surface, ablation plan with graded verdicts, parameter-perturbation sensitivity audit, second-backend replication, the 18-benchmark paper audit suite with per-benchmark evidence grades, the auto-extracted audit of a published paper's machine-readable raw data, extrapolation stress test, wet-lab anchor corpus |
| `MANIFEST.json` | sha256 + byte size of every file in this archive |
| `.zenodo.json` | Zenodo deposition metadata (upload-ready) |

Deliberately **excluded** (large, not needed to verify any claim):
`data/ml_models` (173 MB trained weights), `data/entity_store.json`
(20 MB entity database), blog/SEO artifacts, and daily-run scratch data.

## Reproduce the headline numbers

```bash
pip install -r code/requirements.txt
cd code
python scripts/build_gate_ledger.py        # 91 PASS / 3 MARGINAL / 1 REFUTED
python scripts/check_vlab_gates.py         # 42 fail-closed gates
python scripts/check_anchor_quality.py     # ablation plan + graded verdicts
python scripts/run_sensitivity_ablation.py # parameter-perturbation audit
python scripts/run_published_data_audit.py # auto-extracted audit of a published paper
```

Expected outputs are shipped in `data/`; compare byte-level via `MANIFEST.json`.

## Provenance & honesty

- All headline numbers are reproducible from `data/` with the code in `code/`.
- The single REFUTED case (`mol_suzuki_yield`) is independently caught by three
  distinct gates (see Paper 1, Section 5.1).
- Negative results (73/82 extrapolation REFUTED; parameter-sensitivity flip
  rates) are shipped, not hidden.
- No wet-lab ground truth exists for any of the 95 scenarios; every anchor is
  either literature-sourced or explicitly labelled synthetic
  (`data/wet_lab_anchors.json`, `data/trust_surface.json`).

## License

Code: MIT (`code/LICENSE`). Manuscript text and figures: CC-BY-4.0.
"""
    (out_dir / "README.md").write_text(readme, encoding="utf-8")

    print(f"存档包已组装: {out_dir.relative_to(ROOT)}")
    print(f"  文件数: {manifest['n_files']}  总字节: {manifest['total_bytes']:,}")
    print(f"  code={stats['code']}  papers={stats['papers']}  data={stats['data']}")
    print(f"  MANIFEST.json: {len(manifest_files)} 条 sha256")
    print("  上传步骤见 docs/zenodo-upload-guide.md（DOI 需用户 Zenodo 账号）")
    return out_dir


def verify(out_dir: Path) -> bool:
    """按 MANIFEST.json 逐文件核对 sha256。"""
    manifest = json.loads((out_dir / "MANIFEST.json").read_text(encoding="utf-8"))
    bad = []
    for rel, meta in manifest["files"].items():
        p = out_dir / rel
        if not p.exists():
            bad.append((rel, "missing"))
            continue
        if _sha256(p) != meta["sha256"]:
            bad.append((rel, "sha256 mismatch"))
    if bad:
        print(f"FAIL: {len(bad)} 个文件不符:")
        for rel, why in bad[:20]:
            print(f"  - {rel}: {why}")
        return False
    print(f"PASS: {len(manifest['files'])} 个文件 sha256 全部一致")
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--date", default=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    ap.add_argument("--verify", action="store_true",
                    help="只校验已有存档包（--date 指定）")
    args = ap.parse_args(argv)

    if args.verify:
        out_dir = ROOT / "archives" / f"zenodo-package-{args.date}"
        if not out_dir.exists():
            print(f"ERROR: {out_dir} 不存在", file=sys.stderr)
            return 1
        return 0 if verify(out_dir) else 1

    out_dir = build(args.date)
    return 0 if verify(out_dir) else 1


if __name__ == "__main__":
    raise SystemExit(main())
