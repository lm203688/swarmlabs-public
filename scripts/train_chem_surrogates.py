"""从已恢复的数据集重建化学代理模型（确定性、可复跑）。

背景
----
`data/ml_models/` 下的 .pkl 曾一度只存在于 backups/（脱离仓库）。数据集已恢复入库，
模型本体不入库（见 .gitignore：`data/ml_models/**/*.pkl`），改为用本脚本从数据集重建，
保证「模型可复现」而不是「模型靠二进制活着」。

数据来源（均已入库）
--------------------
* QM9 全集     data/reference_benchmarks/quantum_chemistry/qm9_full_130831.json
* QM9 2万子集  data/reference_benchmarks/quantum_chemistry/qm9_slim.json
* Tox21 标签   data/reference_benchmarks/toxicology/tox21_labeled.json

产出
----
* data/ml_models/qm9_{dipole,homo,lumo,gap,alpha,zpve}_regen.pkl   (RF)
* data/ml_models/qm9_{target}_regen_gbr.pkl                        (GBR)
* data/ml_models/tox21_regen_classifiers.pkl                       (12 逻辑回归基线)
* data/ml_models/regen_report.json                                 (逐目标指标)

设计约束
--------
* 只依赖 numpy + scikit-learn（不引入新框架）。
* 固定 random_state=4242（与仓库既有种子约定一致），保证跨进程可复现。
* 不覆盖原 .pkl，产物统一带 `_regen` 后缀 —— 便于与原模型对照，不做静默替换。
"""
from __future__ import annotations

import argparse
import json
import os
import pickle
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QCC = os.path.join(ROOT, "data", "reference_benchmarks", "quantum_chemistry")
TOX = os.path.join(ROOT, "data", "reference_benchmarks", "toxicology")
OUT = os.path.join(ROOT, "data", "ml_models")

QM9_FULL = os.path.join(QCC, "qm9_full_130831.json")
TOX21 = os.path.join(TOX, "tox21_labeled.json")

SEED = 4242
# QM9 全集列名 -> 语义目标名
QM9_TARGETS = {
    "mu": ("dipole", "D"),
    "ho": ("homo", "eV"),
    "lu": ("lumo", "eV"),
    "gp": ("gap", "eV"),
    "al": ("alpha", "Bohr^3"),
    "zp": ("zpve", "Hartree"),
}


def _load_qm9(limit: int | None = None):
    with open(QM9_FULL, encoding="utf-8") as f:
        d = json.load(f)
    cols = d["columns"]
    rows = d["rows"]
    if limit:
        rows = rows[:limit]
    X = np.asarray([[r[cols.index(c)] for c in ("n", "C", "H", "N", "O", "F")] for r in rows], dtype=float)
    y = {}
    for col, (name, unit) in QM9_TARGETS.items():
        y[name] = np.asarray([r[cols.index(col)] for r in rows], dtype=float)
    return X, y


def train_qm9(limit: int | None, seed: int = SEED) -> dict:
    from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
    from sklearn.metrics import mean_absolute_error, mean_squared_error
    from sklearn.model_selection import train_test_split

    X, y = _load_qm9(limit)
    report = {"n_molecules": int(X.shape[0]), "n_features": int(X.shape[1]), "targets": {}}
    os.makedirs(OUT, exist_ok=True)

    for name, yy in y.items():
        Xtr, Xte, ytr, yte = train_test_split(X, yy, test_size=0.2, random_state=seed)
        rf = RandomForestRegressor(n_estimators=100, max_depth=15, random_state=seed, n_jobs=-1)
        rf.fit(Xtr, ytr)
        pred = rf.predict(Xte)
        mae = float(mean_absolute_error(yte, pred))
        rmse = float(np.sqrt(mean_squared_error(yte, pred)))
        # 相对误差必须写清分母，否则不同版本的「rel_err」无法对照：
        #   rel_vs_std  —— 以测试集标准差为尺度（对近零均值目标稳健，本脚本默认口径）
        #   rel_vs_mean —— 以测试集均值为尺度（历史 qm9_full_meta.json 用的是这个，
        #                  分母为近零时会爆成几百 %，故仅作对照保留）
        scale_std = float(np.std(yte)) or 1.0
        rel = mae / scale_std
        mean_abs = abs(float(np.mean(yte))) or 1.0
        rel_vs_mean = mae / mean_abs
        with open(os.path.join(OUT, f"qm9_{name}_regen.pkl"), "wb") as f:
            pickle.dump({"model": rf, "target": name, "features": ["n", "C", "H", "N", "O", "F"]}, f)

        gbr = GradientBoostingRegressor(random_state=seed)
        gbr.fit(Xtr, ytr)
        gpred = gbr.predict(Xte)
        gmae = float(mean_absolute_error(yte, gpred))
        with open(os.path.join(OUT, f"qm9_{name}_regen_gbr.pkl"), "wb") as f:
            pickle.dump({"model": gbr, "target": name, "features": ["n", "C", "H", "N", "O", "F"]}, f)

        report["targets"][name] = {
            "unit": QM9_TARGETS[[k for k, v in QM9_TARGETS.items() if v[0] == name][0]][1],
            "rf_mae": round(mae, 6),
            "rf_rmse": round(rmse, 6),
            "rf_rel_err_vs_std": round(rel, 6),
            "rf_rel_err_vs_mean": round(rel_vs_mean, 6),
            "gbr_mae": round(gmae, 6),
        }
        print(f"  {name:8s} RF MAE={mae:.4f}  rel(std)={rel*100:.2f}%  rel(mean)={rel_vs_mean*100:.1f}%  GBR MAE={gmae:.4f}")
    return report


def train_tox21(seed: int = SEED) -> dict:
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score
    from sklearn.model_selection import train_test_split

    with open(TOX21, encoding="utf-8") as f:
        d = json.load(f)
    labels, descs = d["labels"], d["descriptors"]
    rows = d["rows"]
    X = np.asarray([[float(r[k]) for k in descs] for r in rows], dtype=float)

    report = {"n_molecules": len(rows), "n_labels": len(labels), "assays": {}}
    models = {}
    for a in labels:
        yv = np.asarray([1 if r[a] == "1" else 0 for r in rows if r[a] in ("0", "1")], dtype=int)
        Xv = np.asarray([[float(r[k]) for k in descs] for r in rows if r[a] in ("0", "1")], dtype=float)
        if len(np.unique(yv)) < 2 or len(yv) < 200:
            report["assays"][a] = {"skipped": "insufficient labels", "n": int(len(yv))}
            continue
        Xtr, Xte, ytr, yte = train_test_split(Xv, yv, test_size=0.2, random_state=seed, stratify=yv)
        clf = LogisticRegression(max_iter=2000, class_weight="balanced", random_state=seed)
        clf.fit(Xtr, ytr)
        auc = float(roc_auc_score(yte, clf.predict_proba(Xte)[:, 1]))
        models[a] = clf
        report["assays"][a] = {"n_labeled": int(len(yv)), "n_active": int(yv.sum()), "roc_auc": round(auc, 4)}
        print(f"  {a:16s} n={len(yv):5d} act={int(yv.sum()):4d} AUC={auc:.3f}")

    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "tox21_regen_classifiers.pkl"), "wb") as f:
        pickle.dump({"models": models, "descriptors": descs}, f)
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="从已恢复的数据集重建化学代理模型")
    ap.add_argument("--limit", type=int, default=None, help="QM9 只用前 N 行（默认全集 130831）")
    ap.add_argument("--skip-qm9", action="store_true")
    ap.add_argument("--skip-tox21", action="store_true")
    ap.add_argument("--out", default=None, help="报告输出路径（默认 data/ml_models/regen_report.json）")
    args = ap.parse_args(argv)

    report: dict = {"seed": SEED, "source_files": {"qm9_full": os.path.relpath(QM9_FULL, ROOT),
                                                   "tox21": os.path.relpath(TOX21, ROOT)}}
    if not args.skip_qm9:
        if not os.path.exists(QM9_FULL):
            print(f"[FAIL] 缺少 {QM9_FULL}", file=sys.stderr)
            return 2
        print(f"[QM9] 训练（limit={args.limit or '全集'}）")
        report["qm9"] = train_qm9(args.limit)
    if not args.skip_tox21:
        if not os.path.exists(TOX21):
            print(f"[FAIL] 缺少 {TOX21}", file=sys.stderr)
            return 2
        print("[Tox21] 训练")
        report["tox21"] = train_tox21()

    out = args.out or os.path.join(OUT, "regen_report.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print(f"[OK] 报告 -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
