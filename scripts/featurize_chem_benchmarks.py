#!/usr/bin/env python3
"""离线 RDKit 特征化：对 4 份真实化学基准跑 SMILES -> Morgan 2048 + 结构描述符。

输出到 data/reference_benchmarks/cheminformatics/featurized/（静态 JSON，进 CF Pages）。
关键约束：RDKit 仅在本地离线使用，绝不部署到 V8 Worker（CF Pages 无 Python 运行时）。
特征化后的 JSON 是纯数据，可由 SwarmLabs GP 引擎在 JS 侧做对照（B+RDKit 方案 P1）。

用法：
  python scripts/featurize_chem_benchmarks.py
依赖：rdkit（本地 venv）
"""
from __future__ import annotations
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "reference_benchmarks" / "cheminformatics"
OUT = SRC / "featurized"
OUT.mkdir(parents=True, exist_ok=True)

TARGETS = {
    "chembl_lipophilicity_slim.json": "exp_logD",
    "bace_slim.json": "exp_pIC50",
    "freesolv_slim.json": "exp_hydration_free_energy_kcal_mol",
    "esol_delaney_slim.json": "exp_logS",
}

FP_BITS = 2048
DESC_NAMES = [
    "MolWt", "MolLogP", "TPSA", "NumHDonors", "NumHAcceptors",
    "NumRotatableBonds", "NumAromaticRings", "RingCount",
    "FractionCSP3", "NumHeteroatoms", "HeavyAtomCount",
]


def featurize():
    from rdkit import Chem
    from rdkit.Chem import AllChem, Descriptors
    from rdkit import RDLogger
    RDLogger.DisableLog("rdApp.*")

    summary = {}
    for fname, target in TARGETS.items():
        path = SRC / fname
        rows = json.loads(path.read_text(encoding="utf-8"))
        out_rows = []
        skipped = 0
        t0 = time.time()
        for r in rows:
            smi = r.get("smiles")
            y = r.get(target)
            mol = Chem.MolFromSmiles(smi) if smi else None
            if mol is None:
                skipped += 1
                continue
            fp = AllChem.GetMorganFingerprintAsBitVect(mol, radius=2, nBits=FP_BITS)
            bits = [int(b) for b in fp.GetOnBits()]
            desc = {d: float(getattr(Descriptors, d)(mol)) for d in DESC_NAMES}
            out_rows.append({
                "smiles": smi,
                "target": target,
                "y": y,
                "morgan2048_indices": bits,
                "descriptors": desc,
            })
        out = {
            "source": fname,
            "target": target,
            "fp_bits": FP_BITS,
            "n_total": len(rows),
            "n_featurized": len(out_rows),
            "n_skipped": skipped,
            "descriptor_names": DESC_NAMES,
            "generated_at": time.strftime("%Y-%m-%d"),
            "rows": out_rows,
        }
        opath = OUT / fname.replace("_slim.json", "_featurized.json")
        opath.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
        sz = opath.stat().st_size / 1024
        summary[fname] = {"featurized": len(out_rows), "skipped": skipped, "kb": round(sz, 1)}
        print(f"{fname}: {len(out_rows)} featurized, {skipped} skipped, {sz:.1f} KB ({time.time()-t0:.1f}s)")
    (OUT / "_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print("DONE", json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    try:
        featurize()
    except ImportError as e:
        print("IMPORT_ERROR:", e)
        sys.exit(2)
