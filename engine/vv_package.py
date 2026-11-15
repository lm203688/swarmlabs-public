#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V&V 归档包 —— 把「一次判定」变成一个可移交、可离线复验的证据包。

为什么需要这一层
----------------
本项目此前的产物是**散落的**：账本在 `frontend_fixed/reports/gate_ledger.json`，
第二证据链在 `wet_lab_anchors.json`，留出集在 `benchmark/<key>.json`，报告在
`reports/{html,json,docx}/`，阈值在 `engine/verification_gate.py` 的源码里。

散落的后果是：**判定结论无法被单独移交**。你能把一个 R² 数字发给第三方，
但你不能把「这个数字是在哪版引擎、对哪条留出集、按哪份阈值、由哪条证据链
裁出来的」一起交给对方。于是第三方唯一能做的动作是信任你——而那正是本项目
声称要消除的东西。

对比参照（2026-09 竞争格局）
---------------------------
中科闻歌 SciencePro 于 2026-09-17 发布，其 `Projects` 能力明确为
「以课题为单位保存研究进度、文献资料和工具配置，将文件资料、Skills、外部
工具连接及相关参数配置统一归档」。我们此前没有这一层：有零件，没有容器。
本模块就是那个容器，但**多加三件它没有的东西**：

  1. **阈值来源可追溯** —— `policy_source` 指向唯一真源，而不是"平台认为"；
  2. **版本钉死** —— engine / oracle 源码指纹进 manifest，任何改动都会让
     「沿用旧结论」变成可检测的事实；
  3. **离线复验** —— `verify_package()` 只依赖标准库读包内文件，第三方无需
     我们的仓库、账号、网络即可判断包是否被改过。

设计红线（不得为了"好看"而放宽）
--------------------------------
- **默认不含留出集真值**。留出集是评分依据；把 y 放进可移交的包里等于泄漏
  评分标准（同时是下一次"自证"的作弊面）。默认只放 `y_sha256`（承诺）与
  x 的摘要，需要内部完整归档时显式开 `include_ground_truth=True`，
  且 manifest 会标注 `contains_ground_truth: true`。
- **缺件记为 absent，不补默认值**。没有锚点就是没有锚点，写成
  `"no_wet_lab_anchor"` 而不是跳过该字段。
- **不重算判定**。本模块搬运引擎已签发的 verdict/gate，不自行发明阈值。
  唯一的例外是 `verify_package()` 的完整性校验——那校验的是**文件没被动过**，
  不是「判定是对的」。

依赖：**纯标准库**。不 import `engine/__init__.py`（它会拉起 numpy 依赖链），
改为按文件路径直接加载 `verification_gate.py`，使本模块可在零依赖环境构建与复验。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import time
import zipfile
from typing import Dict, List, Optional

# ---------------------------------------------------------------- 路径与常量

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS = os.path.join(ROOT, "frontend_fixed", "reports")
LEDGER = os.path.join(REPORTS, "gate_ledger.json")
ANCHORS = os.path.join(REPORTS, "wet_lab_anchors.json")
BENCHMARK_DIR = os.path.join(REPORTS, "benchmark")
HELDOUT_INDEX = os.path.join(REPORTS, "heldout_index.json")

SCHEMA = "swarmlabs-vv-package/1.0"
REPO_URL = "https://github.com/lm203688/swarmlabs"
GATE_MODULE = os.path.join(ROOT, "engine", "verification_gate.py")

#: 包内不参与 sha256 清单的两个文件（自指问题：manifest 无法包含自己的哈希）
NON_CONTENT_FILES = {"manifest.json", "CHECKS.txt"}

HONEST_BOUNDARIES = [
    "The gate adjudicates a prediction against a held-out surrogate/oracle and "
    "cross-checks literature ranges. It does not validate the underlying physics.",
    "PASS means 'faithful to the oracle'. If the oracle is wrong, PASS is still "
    "wrong. The second evidence chain (wet-lab anchors) exists to catch exactly "
    "that, and it reports CONTRADICTED / UNANCHORED for real scenarios.",
    "Most scenarios have no wet-lab counterpart; those are recorded as "
    "UNANCHORED, not as passing.",
    "Held-out ground truth is deliberately NOT included by default. The package "
    "carries a sha256 commitment to it instead, so a third party can verify which "
    "split was used without being handed the answer key.",
    "A block count of 0 is the honest number on the current baseline. A gate that "
    "has never fired is a gate whose teeth you have not yet seen.",
]


# ------------------------------------------------------- 无包导入的模块加载

def _load_gate_module():
    """按**文件路径**加载 `engine/verification_gate.py`。

    不能用 `import engine.verification_gate`：那会先执行 `engine/__init__.py`，
    把 numpy 依赖链拉进一个本该零依赖的归档工具。`verification_gate.py` 自身
    只 import 标准库，所以直接加载是安全的，也让本模块能在无 numpy 环境跑。
    """
    spec = importlib.util.spec_from_file_location("_swarmlabs_gate", GATE_MODULE)
    if spec is None or spec.loader is None:      # pragma: no cover - 环境异常
        raise RuntimeError(f"cannot load gate module at {GATE_MODULE}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _policy_bundle() -> Dict:
    """阈值 / 策略 / 指纹 —— 全部从权威模块搬运，本模块不发明任何数字。"""
    g = _load_gate_module()
    return {
        "policy": g.policy(),
        "thresholds": g.thresholds(),
        "policy_source": "engine/verification_gate.py::thresholds()",
        "engine": g.engine_fingerprint(),
        "oracle": g.oracle_digest(),
    }


# ------------------------------------------------------------------ 小工具

def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _canonical_sha256(obj) -> str:
    """对 JSON 对象算**规范化** sha256（键排序、无多余空白）。

    用于对留出集真值做承诺：同一份数据在不同平台/换行/键序下哈希相同，
    这样第三方才能拿自己那份数据复算出同一个承诺值。
    """
    s = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return _sha256_bytes(s.encode("utf-8"))


def _read_json(path: str):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=False)
        f.write("\n")


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _walk_files(base: str) -> List[str]:
    out: List[str] = []
    for dirpath, _dirnames, filenames in os.walk(base):
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            out.append(os.path.relpath(full, base).replace(os.sep, "/"))
    return sorted(out)


# ------------------------------------------------------------ 逐场景证据装配

def _scenario_payload(key: str, entry: dict,
                      include_ground_truth: bool) -> Dict:
    """单场景的归档条目：判定 + 留出集承诺（+ 可选真值）。

    刻意**不**复制 `rationale` 之外的任何推导过程——推导在报告里，
    包只负责把"结论 + 可复核它的最小事实"捆在一起。
    """
    unc = entry.get("uncertainty") or {}
    payload: Dict = {
        "scenario_key": key,
        "verdict": entry.get("verdict"),
        "gate": entry.get("gate"),
        "domain": entry.get("domain"),
        "published_reference": entry.get("published"),
        "rationale": entry.get("rationale"),
        "dim": entry.get("dim"),
        "n_train": entry.get("n_train"),
        "n_test": entry.get("n_test"),
        "uncertainty": {
            "r2": unc.get("r2"),
            "coverage": unc.get("coverage"),
            "calibration_scale_kappa": unc.get("calibration_scale_kappa"),
            "noise_floor": unc.get("noise_floor"),
            "nominal_coverage": unc.get("nominal_coverage"),
            "rmse_rel": unc.get("rmse_rel"),
        },
        "replication_passed": entry.get("replication_passed"),
        "report_refs": entry.get("report"),
    }
    # 用外层包提供的单一真源复核该条目（不自算阈值，只做一致性标注）
    b = _policy_bundle()
    th = b["thresholds"]
    r2 = unc.get("r2")
    cov = unc.get("coverage")
    payload["threshold_check"] = {
        "policy_source": b["policy_source"],
        "pass_requires": b["policy"].get("pass"),
        "per_axis": {
            "r2_ge_pass_r2": (None if r2 is None else bool(r2 >= th["pass_r2"])),
            "coverage_ge_coverage_pass": (
                None if cov is None else bool(cov >= th["coverage_pass"])),
        },
        "note": ("consistency annotation only — the verdict is the engine's, "
                 "this package does not re-adjudicate"),
    }

    # 留出集：只放承诺，不放答案
    hb = os.path.join(BENCHMARK_DIR, f"{key}.json")
    if os.path.exists(hb):
        h = _read_json(hb)
        meta = {
            "available": True,
            "n_test": h.get("n_test"),
            "test_seed": h.get("test_seed"),
            "dim": h.get("dim"),
            "bounds": h.get("bounds"),
            "ground_truth_convention": h.get("ground_truth"),
            "x_sha256": _canonical_sha256(h.get("X")),
            "y_sha256": _canonical_sha256(h.get("y")),
            "x_head": (h.get("X") or [])[:3],
        }
        if include_ground_truth:
            meta["y"] = h.get("y")
            meta["X"] = h.get("X")
        payload["heldout"] = meta
    else:
        payload["heldout"] = {"available": False,
                              "reason": "no published held-out file for this key"}

    # 第二证据链（湿实验锚点）——缺就是缺，不跳字段
    anch = _anchors_for(key)
    payload["wet_lab_anchor"] = anch
    return payload


_ANCHOR_CACHE: Optional[dict] = None


def _anchors_doc() -> dict:
    global _ANCHOR_CACHE
    if _ANCHOR_CACHE is None:
        try:
            _ANCHOR_CACHE = _read_json(ANCHORS)
        except Exception:
            _ANCHOR_CACHE = {}
    return _ANCHOR_CACHE


def _anchors_for(key: str) -> Dict:
    """从第二证据链产物里取该场景的锚定结果。

    产物历史上既可能是 `{"scenarios": {key: entry}}`（映射）也可能是
    `{"scenarios": [entry, ...]}`（列表）——两种都接受，因为一个归档工具
    不该因为上游产物换了容器类型就悄悄把"有锚点"读成"没锚点"。
    """
    doc = _anchors_doc()
    scenarios = doc.get("scenarios")
    entry = None
    if isinstance(scenarios, dict):
        entry = scenarios.get(key)
    elif isinstance(scenarios, list):
        for e in scenarios:
            if isinstance(e, dict) and e.get("scenario_key") == key:
                entry = e
                break
    if not entry:
        return {
            "grade": "UNANCHORED",
            "gate": "NO_WET_LAB_ANCHOR",
            "reason": "scenario has no wet-lab counterpart in the anchor chain",
        }
    return entry


def _anchors_index_payload() -> Dict:
    doc = _anchors_doc()
    return {
        "generated_at_utc": doc.get("generated_at_utc"),
        "purpose": doc.get("purpose"),
        "summary": doc.get("summary"),
        "anchors": doc.get("anchors"),
        "note": doc.get("note"),
    }


# --------------------------------------------------------------- 构建主流程

README_TEMPLATE = """# V&V Package — {package_id}

Generated {created_utc} by SwarmLabs. Schema `{schema}`.

This is an **archival evidence package**, not a score sheet. It exists so that a
third party can hold the same facts the vendor held when a verdict was issued —
without having to trust the vendor's summary.

## What is in here

| Path | What |
|---|---|
| `manifest.json` | machine-readable index: policy, source fingerprints, per-file sha256 |
| `policy.json` | the thresholds, exported from their single source of truth |
| `standard.json` | the V&V standard this report outline follows |
| `scenarios/<key>.json` | per-scenario verdict, gate, uncertainty, held-out commitment |
| `evidence/wet_lab_anchors.json` | the **second, independent** evidence chain |
| `CHECKS.txt` | human-readable checksum list |

## What is deliberately NOT in here

**The held-out ground truth.** The package carries `y_sha256` — a commitment —
instead of `y`. That lets you verify *which* split was used and that it was not
edited afterwards, without being handed the answer key. (Internal archival use
can opt in with `--include-ground-truth`, which flags the package.)

## How to re-verify

```bash
# 1. integrity: did anything in this package change after it was built?
python scripts/build_vv_package.py verify <this-dir>
# exit 0 = PACKAGE_INTACT, exit 2 = PACKAGE_TAMPERED

# 2. adjudication: does a *new* prediction survive held-out truth?
python skills/vv-gate/scripts/vv_gate.py verify {first_key} --pred preds.json
# exit 0 PROCEED | 2 BLOCK | 3 HUMAN CHECK | 4 unreachable (no verdict)
```

Step 1 needs nothing but Python 3.8+. Step 2 needs network access to the gate.

## Scope of this package

- scenarios: {n_scenarios}
- verdicts: {counts}
- blocked (autonomous action halted): {n_blocked}
- engine git commit: `{git_commit}`
- engine all_digest: `{all_digest}`
- oracle digest: `{oracle_digest}`

## Honest boundaries

{boundaries}
"""


def build_package(out_dir: str, keys: Optional[List[str]] = None,
                  include_ground_truth: bool = False,
                  include_reports: bool = False) -> Dict:
    """构建一个 V&V 归档包，返回 manifest 字典。

    keys=None → 归档账本里的全部场景。out_dir 会被清空后重建（幂等）。
    """
    if not os.path.exists(LEDGER):
        raise FileNotFoundError(
            f"gate ledger missing at {LEDGER} — run scripts/build_gate_ledger.py first")

    ledger = _read_json(LEDGER)
    scenarios = ledger.get("scenarios") or {}
    if keys is None:
        keys = sorted(scenarios)
    else:
        missing = [k for k in keys if k not in scenarios]
        if missing:
            raise KeyError(f"scenario(s) not in ledger: {missing}")

    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_dir)

    bundle = _policy_bundle()

    # ---- 逐场景 ----
    for k in keys:
        _write_json(os.path.join(out_dir, "scenarios", f"{k}.json"),
                    _scenario_payload(k, scenarios[k], include_ground_truth))
    _write_json(os.path.join(out_dir, "evidence", "wet_lab_anchors.json"),
                _anchors_index_payload())

    # ---- 策略 / 标准 ----
    _write_json(os.path.join(out_dir, "policy.json"), {
        "thresholds": bundle["thresholds"],
        "policy": bundle["policy"],
        "policy_source": bundle["policy_source"],
    })
    _write_json(os.path.join(out_dir, "standard.json"), {
        "standard": ledger.get("standard"),
        "ledger_name": ledger.get("name"),
        "ledger_version": ledger.get("version"),
        "ledger_generated_at_utc": ledger.get("generated_at_utc"),
        "heldout_suite": _heldout_suite_meta(),
    })

    # ---- 报告（可选内联；引用永远保留）----
    report_refs: Dict[str, Dict] = {}
    if include_reports:
        for k in keys:
            ref = scenarios[k].get("report") or {}
            kept: Dict[str, str] = {}
            for fmt, rel in (ref.items() if isinstance(ref, dict) else []):
                src = _resolve_report(rel)
                if not src:
                    continue
                fn = f"{k}.{fmt if fmt != 'json' else 'json'}"
                dst = os.path.join(out_dir, "reports", fn)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copyfile(src, dst)
                kept[fmt] = f"reports/{fn}"
            if kept:
                report_refs[k] = kept
    _write_json(os.path.join(out_dir, "evidence", "report_refs.json"),
                {"included": bool(include_reports), "refs": report_refs})

    # ---- 摘要：README 与 manifest 共用，先算好，避免两次遍历 ----
    grade_counts: Dict[str, int] = {}
    for k in keys:
        v = scenarios[k].get("verdict") or "UNKNOWN"
        grade_counts[v] = grade_counts.get(v, 0) + 1
    blocked = [k for k in keys
               if scenarios[k].get("gate") == "BLOCK_AUTONOMOUS_ACTION"]

    package_id = "vvpkg-" + _sha256_bytes(
        ("|".join(keys) + (ledger.get("generated_at_utc") or "")).encode())[:12]
    created_utc = _utc_now()

    # ---- README：必须在扫描**之前**写。
    #      它是包的一部分（第三方先读它）。若在扫描后写，verify 会把它判成
    #      「manifest 未登记的额外文件」——一个真实存在过的假告警。
    with open(os.path.join(out_dir, "README.md"), "w", encoding="utf-8") as f:
        f.write(README_TEMPLATE.format(
            package_id=package_id,
            created_utc=created_utc,
            schema=SCHEMA,
            n_scenarios=len(keys),
            counts=json.dumps(grade_counts, ensure_ascii=False),
            n_blocked=len(blocked),
            git_commit=(bundle["engine"] or {}).get("git_commit") or "(unknown)",
            all_digest=((bundle["engine"] or {}).get("all_digest") or "")[:16],
            oracle_digest=((bundle["oracle"] or {}).get("digest") or "")[:16],
            first_key=keys[0] if keys else "<key>",
            boundaries="\n".join(f"- {b}" for b in HONEST_BOUNDARIES),
        ))

    # ---- 内容文件清单。manifest.json / CHECKS.txt 无法自指，故不在其中 ----
    content_files = [p for p in _walk_files(out_dir) if p not in NON_CONTENT_FILES]
    files_map = {p: _sha256_file(os.path.join(out_dir, p)) for p in content_files}

    # ---- CHECKS.txt：派生自 files_map，供人肉眼比对 ----
    with open(os.path.join(out_dir, "CHECKS.txt"), "w", encoding="utf-8") as f:
        f.write(f"# sha256 manifest for {package_id}\n")
        f.write(f"# generated {created_utc} — verify with: "
                f"python scripts/build_vv_package.py verify <dir>\n")
        for p in sorted(files_map):
            f.write(f"{files_map[p]}  {p}\n")

    # ---- manifest ----
    manifest = {
        "schema": SCHEMA,
        "package_id": package_id,
        "created_utc": created_utc,
        "generator": {
            "name": "SwarmLabs V&V package builder",
            "repo": REPO_URL,
            "module": "engine/vv_package.py",
        },
        "standard": {"published": ledger.get("standard")},
        "policy": bundle["policy"],
        "policy_source": bundle["policy_source"],
        "provenance": {
            "engine": bundle["engine"],
            "oracle": bundle["oracle"],
            "ledger_generated_at_utc": ledger.get("generated_at_utc"),
            "ledger_version": ledger.get("version"),
            "report_index_sha256": (ledger.get("provenance") or {}).get(
                "report_index_sha256"),
            # 账本自报的构建完整性（scripts/build_gate_ledger.py 写入）。
            # 第三方拿到的若是「某个残缺环境构建出来的账本」切出的包，必须能从
            # 包里直接看出来：否则一次 rdkit 缺失就能让唯一的 REFUTED 场景
            # 从归档证据里消失，而归档包看起来完全正常。
            "ledger_build": ledger.get("build"),
        },
        # 便于机器判读的顶层布尔：这个包所覆盖的场景集是否等于注册表全集。
        # False 时不要把它当作完整发布物——先按 manifest.provenance.ledger_build
        # 的 dropped_scenarios 补齐再重建。
        "build_complete": bool((ledger.get("build") or {}).get("complete", True)),
        "scope": {"n_scenarios": len(keys), "keys": keys},
        "summary": {
            "counts": grade_counts,
            "n_blocked": len(blocked),
            "blocked": blocked,
            "n_human_check_required": ledger.get("n_human_check_required"),
        },
        "contains_ground_truth": bool(include_ground_truth),
        "contains_reports": bool(include_reports),
        "integrity": {
            "algorithm": "sha256",
            "n_files": len(files_map),
            "files": files_map,
        },
        "how_to_reverify": [
            "python scripts/build_vv_package.py verify <dir>   # integrity (offline)",
            (f"python skills/vv-gate/scripts/vv_gate.py verify {keys[0]} "
             "--pred preds.json   # adjudication (network)") if keys else "",
        ],
        "honest_boundaries": HONEST_BOUNDARIES,
    }
    _write_json(os.path.join(out_dir, "manifest.json"), manifest)

    return manifest


def _resolve_report(rel) -> Optional[str]:
    """报告引用（`/reports/html/x.html`）→ 真实文件路径，找不到返回 None。"""
    if not isinstance(rel, str) or not rel:
        return None
    rel = rel.lstrip("/")
    for cand in (os.path.join(ROOT, "frontend_fixed", rel),
                 os.path.join(ROOT, rel)):
        if os.path.exists(cand):
            return cand
    return None


def _heldout_suite_meta() -> Dict:
    try:
        d = _read_json(HELDOUT_INDEX)
    except Exception:
        return {"available": False}
    return {
        "available": True,
        "name": d.get("name"),
        "version": d.get("version"),
        "note": d.get("note"),
        "sha256": _sha256_file(HELDOUT_INDEX),
    }


# --------------------------------------------------------------- 离线复验

def verify_package(pkg_dir: str) -> Dict:
    """离线完整性复验：**只读包内文件 + 标准库**。

    检查三件事：
      1. manifest 列出的每个文件都存在且 sha256 一致；
      2. 包内没有 manifest 未列出的额外内容文件（防止事后被塞东西）；
      3. manifest 自洽（文件数、场景数与 scope 一致）。

    注意：这里校验的是「文件没被动过」，**不是**「判定是对的」。
    判定正确性由 vv-gate 对着留出集重跑（需要网络）。
    """
    mp = os.path.join(pkg_dir, "manifest.json")
    if not os.path.exists(mp):
        return {"intact": False, "reason": "manifest.json missing", "pkg_dir": pkg_dir}
    try:
        man = _read_json(mp)
    except Exception as e:
        return {"intact": False, "reason": f"manifest unparseable: {e}",
                "pkg_dir": pkg_dir}

    listing = man.get("integrity") or {}
    expected: Dict[str, str] = listing.get("files") or {}

    missing: List[str] = []
    mismatched: List[str] = []
    for rel, want in expected.items():
        full = os.path.join(pkg_dir, rel)
        if not os.path.exists(full):
            missing.append(rel)
            continue
        got = _sha256_file(full)
        if got != want:
            mismatched.append(rel)

    actual = set(_walk_files(pkg_dir))
    unlisted = sorted(actual - set(expected) - NON_CONTENT_FILES)

    n_expected_files = len(expected)
    n_scope = int(((man.get("scope") or {}).get("n_scenarios")) or 0)
    n_scn_files = len([p for p in actual if p.startswith("scenarios/")])
    scope_ok = (n_scope == n_scn_files)
    count_ok = (listing.get("n_files") == n_expected_files)

    intact = not missing and not mismatched and not unlisted and scope_ok and count_ok
    return {
        "intact": intact,
        "verdict": "PACKAGE_INTACT" if intact else "PACKAGE_TAMPERED",
        "pkg_dir": pkg_dir,
        "package_id": man.get("package_id"),
        "created_utc": man.get("created_utc"),
        "checks": {
            "n_files_expected": n_expected_files,
            "n_files_listed": listing.get("n_files"),
            "files_missing": missing,
            "files_mismatched": mismatched,
            "files_unlisted": unlisted,
            "n_scenarios_in_scope": n_scope,
            "n_scenario_files": n_scn_files,
            "scope_matches_files": scope_ok,
            "count_field_matches": count_ok,
        },
        "note": ("integrity only — this says the files were not changed after "
                 "build, not that the verdict is correct"),
    }


def write_zip(pkg_dir: str, zip_path: Optional[str] = None) -> str:
    """把包打成 zip（单文件移交）。返回 zip 路径。"""
    pkg_dir = os.path.abspath(pkg_dir)
    if zip_path is None:
        zip_path = pkg_dir.rstrip(os.sep) + ".zip"
    base = os.path.basename(pkg_dir.rstrip(os.sep))
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in _walk_files(pkg_dir):
            z.write(os.path.join(pkg_dir, rel), f"{base}/{rel}")
    return zip_path


if __name__ == "__main__":     # pragma: no cover - 人工速查
    import pprint
    led = _read_json(LEDGER)
    ks = sorted((led.get("scenarios") or {}).keys())[:1] or []
    out = os.path.join(ROOT, ".workbuddy", "vvpkg-selftest")
    m = build_package(out, keys=ks or None)
    pprint.pprint({"manifest_summary": m["summary"], "n_files": m["integrity"]["n_files"]})
    pprint.pprint(verify_package(out))
