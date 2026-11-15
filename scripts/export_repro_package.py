#!/usr/bin/env python3
"""一键复现包导出（PR-10 / PROJECT_ELEVATION_REPORT §10）。

把一次实验的完整证据链打包成 .tar.gz 交付给第三方，第三方在不读源码、
不信任宣传数字的前提下，仅凭包内 SHA-256 与种子即可独立复现同一结论。

包内容：
- batch_records.jsonl  —— BatchRecord 规范化的 JSON Lines（来自 import_batch_csv）
- split.json           —— 按 batch_id 隔离的切分方案（来自 split_by_batch_id）
- intent.json          —— ExperimentIntent（来自 build_experiment_card）
- experiment_card.json —— 完整实验卡（含三档 baseline、gate_results、negative_controls）
- manifest.json        —— EvidenceManifest（来自 build_experiment_card）
- env.lock.json        —— Python/依赖/随机种子锁定
- SHA256SUMS           —— 上述文件的 SHA-256 校验和

用法：
    python scripts/export_repro_package.py \
        --card outputs/pr08/experiment_card.json \
        --outdir outputs/repro-packages/ \
        [--batch-records path/to/records.jsonl] \
        [--split path/to/split.json] \
        [--intent path/to/intent.json] \
        [--manifest path/to/manifest.json]

退出码：
    0  —— 成功，包已写入 <outdir>/repro-package-<ts>.tar.gz
    2  —— 参数或输入文件错误
    3  —— 打包阶段失败（SHA-256 不一致等）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import sys
import tarfile
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ============================================================
# 常量（与 SwarmLabs 铁律对齐）
# ============================================================

OFFICIAL_SEED = 991          # 官方发布种子
TRAIN_SEED = 4242            # 训练种子
CALIBRATION_SEED = 7311      # 校准种子

PACKAGE_SCHEMA_VERSION = "1.0.0"

# 期望的包内文件（缺一即 exit 3）
REQUIRED_PKG_FILES = [
    "experiment_card.json",
    "intent.json",
    "manifest.json",
    "split.json",
    "batch_records.jsonl",
    "env.lock.json",
    "SHA256SUMS",
]


# ============================================================
# 工具函数
# ============================================================


def sha256_of_file(path: Path) -> str:
    """计算文件的 SHA-256 hex 摘要。"""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_of_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_json(path: Path, label: str) -> Any:
    if not path.exists():
        raise SystemExit(f"[ERROR] {label}: 文件不存在 -> {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as e:
        raise SystemExit(f"[ERROR] {label}: JSON 解析失败 -> {path} ({e})")


def load_jsonl(path: Path, label: str) -> List[Dict[str, Any]]:
    if not path.exists():
        raise SystemExit(f"[ERROR] {label}: 文件不存在 -> {path}")
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise SystemExit(
                    f"[ERROR] {label}: 第 {i} 行 JSON 解析失败 -> {path} ({e})"
                )
    return rows


def write_json(path: Path, data: Any, indent: int = 2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=indent, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ============================================================
# RO-Crate / W3C PROV metadata 生成（PR-11 / REPORT §5）
# ============================================================

# RO-Crate profile 声明：使用标准 profile，无需自建 schema
RO_CRATE_CONTEXT = "https://w3id.org/ro/crate/1.1/context"
PROV_CONTEXT = "https://w3id.org/prov-o#blank-node-20261027"


def _build_ro_crate(
    staging: Path,
    card_data: Dict[str, Any],
    intent_data: Dict[str, Any],
    manifest_data: Dict[str, Any],
) -> Dict[str, Any]:
    """构造 RO-Crate 1.1 compliant JSON-LD metadata。

    包含：
    - Dataset（整个 package 作为 root resource）
    - SoftwareSourceCode（SwarmLabs repo 指针）
    - Algorithm（experiment_card.intent.method 中的方法标识）
    - Plan（ExperimentIntent）
    - Entity（intent.json / manifest.json / batch_records.jsonl / split.json /
            env.lock.json / experiment_card.json / package.json / SHA256SUMS）
    - PROV 关系：wasDerivedFrom / wasAssociatedWith / wasGeneratedBy /
               hadPrimarySource / startedAtTime / endedAtTime

    设计取舍（REPORT §5）：轻量 JSON-LD，不重建大型语义平台。
    """
    now = utc_now_iso()
    card_id = str(card_data.get("experiment_id") or card_data.get("id") or "unknown")
    intent_id = str(intent_data.get("intent_id") or intent_data.get("id") or card_id)
    manifest_id = str(manifest_data.get("manifest_id") or manifest_data.get("id") or card_id)

    # 从 intent 提取 scenario/discipline（可选字段，缺失则省略）
    discipline = intent_data.get("discipline") or card_data.get("discipline") or ""
    scenario_name = (
        intent_data.get("scenario_name")
        or intent_data.get("scenario")
        or card_data.get("scenario_name")
        or card_data.get("scenario")
        or ""
    )
    method = intent_data.get("method") or card_data.get("method") or ""

    graph: List[Dict[str, Any]] = []

    # 1) root dataset
    graph.append({
        "@id": "./",
        "@type": ["Dataset", "ro:Dataset", "prov:Entity"],
        "ro:hasContentEntity": [f"./{n}" for n in [
            "experiment_card.json",
            "intent.json",
            "manifest.json",
            "split.json",
            "batch_records.jsonl",
            "env.lock.json",
            "package.json",
            "SHA256SUMS",
        ]],
        "dct:title": f"SwarmLabs reproduction package: {card_id}",
        "dct:description": (
            "SwarmLabs evidence package. Given env.lock.json seeds + "
            "batch_records.jsonl + split.json, results must reproduce "
            "bit-for-bit (rmse_rel / coverage / gate thresholds)."
        ),
        "dct:creator": {
            "@id": "./SwarmLabs",
            "@type": ["dct:Agent"],
            "dct:title": "SwarmLabs",
            "ro:isIdentifiedBy": "https://swarmlabs.tools",
        },
        "dct:dateCreated": now,
        "dct:license": "https://opensource.org/licenses/MIT",
        "dct:provenance": "./ProvenanceStatement",
    })

    # 2) swarm-labs source code reference
    git_head = "<UNKNOWN>"
    try:
        import subprocess
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
            cwd=Path(__file__).resolve().parents[1],
        )
        if r.returncode == 0:
            git_head = r.stdout.strip()
    except Exception:
        pass
    graph.append({
        "@id": "./SwarmLabs",
        "@type": ["prov:Activity", "SoftwareSourceCode"],
        "prov:wasAssociatedWith": "./SwarmLabs-Builder",
        "dct:title": f"swarmlabs git HEAD {git_head[:12]}",
        "prov:startedAtTime": now,
    })

    # 3) builder script (Software)
    graph.append({
        "@id": "./SwarmLabs-Builder",
        "@type": ["prov:Software"],
        "dct:title": "scripts/export_repro_package.py",
        "dct:description": "SwarmLabs reproduction-package exporter",
    })

    # 4) Algorithm
    algo_id = method or "SwarmLabs-Method"
    graph.append({
        "@id": f"./{algo_id}",
        "@type": ["Algorithm", "prov:Activity"],
        "dct:title": algo_id,
        "dct:description": f"Method used by experiment {card_id}",
        "prov:wasAssociatedWith": "./SwarmLabs-Builder",
    })

    # 5) Plan (ExperimentIntent)
    graph.append({
        "@id": f"./{intent_id}",
        "@type": ["prov:Plan", "ExperimentIntent"],
        "dct:title": f"ExperimentIntent {intent_id}",
        "dct:description": intent_data.get("description") or intent_data.get("purpose") or "",
        "prov:hadPlan": f"./{intent_id}",
        "prov:hadPrimarySource": "./intent.json",
        "ro:hasContentEntity": "./intent.json",
        "prov:wasAssociatedWith": f"./{algo_id}",
    })

    # 6) entities —— 每个包内文件映射为 Entity
    entity_meta = {
        "experiment_card.json": (
            f"ExperimentCard for {card_id}",
            "The primary experiment summary with baselines, gates, and negative controls.",
        ),
        "manifest.json": (
            f"EvidenceManifest {manifest_id}",
            "Evidence inventory (data, models, calibrations, approvals).",
        ),
        "intent.json": (
            f"ExperimentIntent {intent_id}",
            "What the experiment intends to test.",
        ),
        "split.json": (
            f"Batch-split for {card_id}",
            "Batch-id-isolated train/calibration/test split.",
        ),
        "batch_records.jsonl": (
            f"BatchRecords for {card_id}",
            "Normalized BatchRecord JSON Lines (schema v1).",
        ),
        "env.lock.json": (
            f"Environment lock for {card_id}",
            "Python/deps/seeds lock for bit-for-bit reproduction.",
        ),
        "package.json": (
            f"Package meta for {card_id}",
            "Package-level schema/version/verification hints.",
        ),
        "SHA256SUMS": (
            f"SHA-256 checksums for {card_id}",
            "SHA-256 hex digest index for all package files.",
        ),
    }
    for fname, (title, desc) in entity_meta.items():
        sha256 = ""
        fp = staging / fname
        if fp.exists():
            sha256 = sha256_of_file(fp)
        graph.append({
            "@id": f"./{fname}",
            "@type": ["prov:Entity"],
            "dct:title": title,
            "dct:description": desc,
            "dct:hash": (
                {"algorithm": "sha256", "value": sha256} if sha256 else None
            ),
            "prov:wasGeneratedBy": "./SwarmLabs",
            "prov:wasAssociatedWith": "./SwarmLabs-Builder",
            "prov:wasDerivedFrom": f"./{algo_id}",
            "dct:isPartOf": "./",
        })

    # 7) PROV statement (aggregate provenance summary)
    graph.append({
        "@id": "./ProvenanceStatement",
        "@type": ["prov:Entity", "prov:Bundle"],
        "dct:title": f"Provenance bundle for {card_id}",
        "dct:description": (
            "Aggregate PROV statements tying together SwarmLabs source, "
            "builder script, algorithm, intent, and produced entities."
        ),
        "prov:hadPrimarySource": "./SwarmLabs",
        "prov:startedAtTime": now,
        "prov:endedAtTime": now,
    })

    return {
        "@context": {
            "ro": "https://w3id.org/ro/",
            "ro-Dataset": "ro:Dataset",
            "prov": "http://www.w3.org/ns/prov-o#",
            "dct": "http://purl.org/dc/terms/",
            "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
            "xsd": "http://www.w3.org/2001/XMLSchema#",
            "SoftwareSourceCode": "https://w3id.org/ro/terms/SoftwareSourceCode",
            "Algorithm": "https://w3id.org/ro/terms/Algorithm",
            "ExperimentIntent": "https://swarmlabs.tools/vocab/ExperimentIntent",
        },
        "@graph": graph,
    }


# ============================================================
# 从 experiment_card 推断各产物路径（用户可覆盖）
# ============================================================


def infer_paths_from_card(
    card_path: Path,
    batch_records: Optional[Path],
    split: Optional[Path],
    intent: Optional[Path],
    manifest: Optional[Path],
) -> Dict[str, Path]:
    """尝试从 card 目录推断其它产物的路径；用户显式传入的路径优先。"""
    card_dir = card_path.parent

    def pick(explicit: Optional[Path], fallback: Path) -> Path:
        if explicit is not None:
            return explicit
        return fallback

    def pick_batch_records(explicit: Optional[Path]) -> Path:
        if explicit is not None:
            return explicit
        jsonl = card_dir / "batch_records.jsonl"
        if jsonl.exists():
            return jsonl
        js = card_dir / "batch_records.json"
        if js.exists():
            return js
        # 再回退到 data/real 下的 CSV 或 jsonl
        csv = card_dir / "batch_records.csv"
        return jsonl if jsonl.exists() else js

    return {
        "card": card_path,
        "batch_records": pick_batch_records(batch_records),
        "split": pick(split, card_dir / "split.json"),
        "intent": pick(intent, card_dir / "intent.json"),
        "manifest": pick(manifest, card_dir / "manifest.json"),
    }


# ============================================================
# 环境锁
# ============================================================


def build_env_lock(extra_git_head: Optional[str] = None) -> Dict[str, Any]:
    """构造环境锁文件。"""
    import importlib.metadata

    # 关键依赖列表（SwarmLabs 项目硬约束）
    key_deps = [
        "numpy",
        "scipy",
        "jsonschema",
        "pandas",
        "scikit-learn",
        "rdkit",
    ]
    deps: Dict[str, str] = {}
    for pkg in key_deps:
        try:
            deps[pkg] = importlib.metadata.version(pkg)
        except importlib.metadata.PackageNotFoundError:
            deps[pkg] = "<NOT_INSTALLED>"

    # git HEAD（如果能读到）
    git_head = extra_git_head
    if not git_head:
        try:
            import subprocess

            r = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                timeout=5,
                cwd=Path(__file__).resolve().parents[1],
            )
            if r.returncode == 0:
                git_head = r.stdout.strip()
        except Exception:
            pass

    return {
        "schema_version": "1.0.0",
        "generated_at_utc": utc_now_iso(),
        "python": {
            "version": platform.python_version(),
            "executable": sys.executable,
            "implementation": platform.python_implementation(),
        },
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "dependencies": deps,
        "swarmlabs_git_head": git_head or "<UNKNOWN>",
        "seeds": {
            "official": OFFICIAL_SEED,
            "train": TRAIN_SEED,
            "calibration": CALIBRATION_SEED,
            "note": (
                "所有 SwarmLabs 侧推理与门控都以上述种子为真源；"
                "第三方复现必须固定这三颗种子。"
            ),
        },
        "repro_invariant": (
            "给定同一份 batch_records.jsonl + split.json + 上述三颗种子 + "
            "同一份 numpy/scipy/scikit-learn 版本，SwarmLabs 输出的 rmse_rel / "
            "coverage / 门控阈值必须与 experiment_card.json 完全一致（bit-for-bit）。"
        ),
    }


# ============================================================
# 组装复现包
# ============================================================


def assemble_package(
    src: Dict[str, Path],
    outdir: Path,
    notes: Optional[str] = None,
) -> Tuple[Path, Path, Dict[str, str]]:
    """
    组装一个复现包。

    Returns:
        (tar_path, staging_dir, sha256_index)
    """
    outdir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    tar_name = f"repro-package-{ts}.tar.gz"
    tar_path = outdir / tar_name

    with tempfile.TemporaryDirectory(prefix="repro_pkg_") as td:
        staging = Path(td) / "repro-package"
        staging.mkdir()

        # 1. experiment_card.json
        card_data = load_json(src["card"], "experiment_card")
        write_json(staging / "experiment_card.json", card_data)

        # 2. intent.json —— 支持独立文件，或从 card.intent 抽取
        if src["intent"].exists():
            intent_data = load_json(src["intent"], "intent")
        else:
            intent_data = card_data.get("intent")
            if intent_data is None:
                raise SystemExit(
                    f"[ERROR] 找不到 intent：{src['intent']} 不存在，且 card.intent 为空"
                )
        write_json(staging / "intent.json", intent_data)

        # 3. manifest.json —— 支持独立文件，或从 card.manifest 抽取
        if src["manifest"].exists():
            manifest_data = load_json(src["manifest"], "manifest")
        else:
            manifest_data = card_data.get("manifest")
            if manifest_data is None:
                raise SystemExit(
                    f"[ERROR] 找不到 manifest：{src['manifest']} 不存在，且 card.manifest 为空"
                )
        write_json(staging / "manifest.json", manifest_data)

        # 4. split.json
        split_data = load_json(src["split"], "split")
        write_json(staging / "split.json", split_data)

        # 5. batch_records.jsonl（支持 .jsonl 与 .json 两种输入）
        br_path = src["batch_records"]
        if br_path.suffix == ".jsonl":
            rows = load_jsonl(br_path, "batch_records")
        elif br_path.suffix in (".json",):
            data = load_json(br_path, "batch_records")
            rows = data if isinstance(data, list) else data.get("records", [])
        else:
            raise SystemExit(
                f"[ERROR] batch_records 只支持 .jsonl 或 .json -> {br_path}"
            )
        if not rows:
            raise SystemExit("[ERROR] batch_records 为空，无法打包")
        write_jsonl(staging / "batch_records.jsonl", rows)

        # 6. env.lock.json
        env_lock = build_env_lock()
        if notes:
            env_lock["notes"] = notes
        write_json(staging / "env.lock.json", env_lock)

        # 7. 包级 manifest.json —— 顶层元数据（与 EvidenceManifest 区分命名）
        # 放到包根，让第三方第一眼看到
        pkg_meta = {
            "package_schema_version": PACKAGE_SCHEMA_VERSION,
            "generated_at_utc": utc_now_iso(),
            "produced_by": "SwarmLabs scripts/export_repro_package.py",
            "files_expected": REQUIRED_PKG_FILES,
            "how_to_verify": (
                "cd <pkg>/ && sha256sum -c SHA256SUMS && python -c \"import jsonschema, json; "
                "s=json.load(open('../schemas/evidence_manifest.schema.json')); "
                "d=json.load(open('manifest.json')); "
                "jsonschema.validate(d, s)\" && echo REPRODUCIBLE"
            ),
        }
        write_json(staging / "package.json", pkg_meta)

        # 7.5 RO-Crate metadata（PROJECT_ELEVATION_REPORT §5：RO-Crate/PROV 现在采纳）
        # 轻量 JSON-LD：@context + @graph + PROV 关系（wasDerivedFrom/wasAssociatedWith/wasGeneratedBy）
        ro_crate = _build_ro_crate(staging, card_data, intent_data, manifest_data)
        write_json(staging / "ro-crate-metadata.json", ro_crate)

        # 8. SHA256SUMS（对所有产物计算校验和，包括 package.json 和 ro-crate-metadata.json）
        sha_index: Dict[str, str] = {}
        for name in sorted(staging.iterdir()):
            if name.is_file() and name.name != "SHA256SUMS":
                sha_index[name.name] = sha256_of_file(name)
        sums_path = staging / "SHA256SUMS"
        # 强制 LF 换行：Windows 默认 text 模式会写入 \r\n，导致 sha256sum -c 报错
        with sums_path.open("w", encoding="utf-8", newline="\n") as f:
            for name in sorted(sha_index):
                f.write(f"{sha_index[name]}  {name}\n")

        # 9. 打包
        with tarfile.open(tar_path, "w:gz", compresslevel=9) as tar:
            tar.add(staging, arcname="repro-package")

    return tar_path, staging, sha_index


# ============================================================
# 交付前自检
# ============================================================


def self_check(tar_path: Path, sha_index: Dict[str, str]) -> List[str]:
    """
    解包 tar.gz 并核对每个文件的 SHA-256 是否与打包时记录的一致。

    Returns:
        错误列表（空列表 = 全通过）
    """
    errors: List[str] = []
    with tarfile.open(tar_path, "r:gz") as tar:
        # 检查所有必需文件都在
        names = tar.getnames()
        base = names[0].rstrip("/") if names else ""
        for req in REQUIRED_PKG_FILES:
            if not any(n.endswith("/" + req) or n == req for n in names):
                errors.append(f"MISSING: {req}")
        if not any(n.endswith("/package.json") for n in names):
            errors.append("MISSING: package.json")

        # 对每个非 SHA256SUMS 文件核对 sha256
        for member in tar.getmembers():
            if not member.isfile():
                continue
            fname = member.name.rsplit("/", 1)[-1]
            if fname == "SHA256SUMS":
                continue
            expected = sha_index.get(fname)
            if expected is None:
                continue
            fobj = tar.extractfile(member)
            if fobj is None:
                continue
            data = fobj.read()
            actual = hashlib.sha256(data).hexdigest()
            if actual != expected:
                errors.append(
                    f"MISMATCH: {fname} expected={expected[:12]} actual={actual[:12]}"
                )
    return errors


# ============================================================
# CLI
# ============================================================


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="SwarmLabs 一键复现包导出（PR-10）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--card", required=True, type=Path,
        help="experiment_card.json 的路径（必需，其它路径默认在其同目录推断）"
    )
    p.add_argument(
        "--outdir", type=Path, default=Path("outputs/repro-packages"),
        help="输出目录（默认 outputs/repro-packages/）"
    )
    p.add_argument("--batch-records", type=Path, default=None,
                   help="BatchRecord JSONL/JSON 路径（默认 <card 目录>/batch_records.jsonl）")
    p.add_argument("--split", type=Path, default=None,
                   help="split.json 路径（默认 <card 目录>/split.json）")
    p.add_argument("--intent", type=Path, default=None,
                   help="intent.json 路径（默认 <card 目录>/intent.json）")
    p.add_argument("--manifest", type=Path, default=None,
                   help="manifest.json 路径（默认 <card 目录>/manifest.json）")
    p.add_argument("--notes", type=str, default=None,
                   help="写入 env.lock.json 的备注（可选）")
    p.add_argument("--no-selfcheck", action="store_true",
                   help="跳过交付前 SHA-256 自检（不推荐）")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)

    # 解析路径
    src = infer_paths_from_card(
        card_path=args.card,
        batch_records=args.batch_records,
        split=args.split,
        intent=args.intent,
        manifest=args.manifest,
    )

    # 预检：intent/manifest 允许从 card 里抽取（不视为硬缺失）
    #       card/batch_records/split 是硬必需
    hard_required = ["card", "batch_records", "split"]
    missing_hard = [str(src[k]) for k in hard_required if not src[k].exists()]
    if missing_hard:
        print("[FATAL] 以下硬必需源文件缺失：")
        for m in missing_hard:
            print(f"  - {m}")
        return 2

    missing_soft = [str(src[k]) for k in ("intent", "manifest") if not src[k].exists()]
    if missing_soft:
        print("[INFO] 以下文件缺失，将从 experiment_card.json 中抽取：")
        for m in missing_soft:
            print(f"  - {m}")

    # 打印输入清单
    print("=" * 60)
    print("SwarmLabs 一键复现包导出 (PR-10)")
    print("=" * 60)
    print(f"输入：")
    for k, v in src.items():
        print(f"  - {k:15s}  {v}")
    print(f"输出目录：{args.outdir}")
    print()

    # 组装
    tar_path, staging, sha_index = assemble_package(
        src=src, outdir=args.outdir, notes=args.notes
    )

    print(f"[OK] 打包完成：{tar_path}")
    print(f"[OK] 包内文件：")
    for name in sorted(sha_index):
        print(f"      {sha_index[name][:16]}  {name}")
    pkg_size = tar_path.stat().st_size
    print(f"[OK] tar.gz 大小：{pkg_size/1024:.1f} KB")

    # 自检
    if args.no_selfcheck:
        print("[SKIP] --no-selfcheck 已指定，跳过 SHA-256 校验")
    else:
        errors = self_check(tar_path, sha_index)
        if errors:
            print(f"[FAIL] 自检失败（{len(errors)} 项）：")
            for e in errors:
                print(f"  - {e}")
            return 3
        print(f"[OK] SHA-256 自检通过（{len(sha_index)} 个文件全部一致）")

    # 打印交付命令
    print()
    print("=" * 60)
    print("交付命令（拷贝给第三方）：")
    print("=" * 60)
    print(f"  # 1) 下载并解包")
    print(f"  tar -xzf {tar_path.name}")
    print(f"  cd repro-package")
    print(f"  # 2) 校验 SHA-256")
    print(f"  sha256sum -c SHA256SUMS")
    print(f"  # 3) 校验 EvidenceManifest 契约")
    print(f"  python -c 'import jsonschema, json; s=json.load(open(\"../schemas/evidence_manifest.schema.json\")); d=json.load(open(\"manifest.json\")); jsonschema.validate(d, s); print(\"OK\")'")
    print(f"  # 4) 复现：给定 env.lock.json 里的种子与依赖版本，结论必须 bit-for-bit 一致")

    return 0


if __name__ == "__main__":
    sys.exit(main())
