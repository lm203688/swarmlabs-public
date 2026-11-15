"""OSS landscape scan for SwarmLabs capability gaps.

Pulls REAL metadata from the GitHub REST API for a curated candidate list
grouped by capability gap. No numbers are invented: every field comes from
api.github.com at run time. If a repo 404s it is recorded as NOT_FOUND.

Usage:
    python scripts/oss_scan.py            # scan + write data/oss_scan.json
    python scripts/oss_scan.py --quiet    # scan only, no print table
    python scripts/oss_scan.py --add owner/repo:gap   # add a candidate
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "oss_scan.json")
SEC = os.path.expanduser("~/.workbuddy/secrets/github.json")

# gap -> candidates. Grouping is the analysis unit: a repo only matters if it
# closes one of OUR named gaps.
GAPS = {
    "uq_calibration": [          # 我们 UQ 一等公民，但只有 GP 后验方差，缺分布无关校准
        "scikit-learn-contrib/MAPIE",
        "uncertainty-toolbox/uncertainty-toolbox",
        "awslabs/fortuna",
        "SURGroup/UQpy",
        "aangelopoulos/conformal-prediction",
    ],
    "decision_model": [          # Jev 类 System One 判定/路由（对应 hive_gate 广播闸门）
        "typesafe-ai/jev",       # 闭源对照（预期 NOT_FOUND / 无公开仓）
        "ikermoel/open-alternative-jev",
        "RedHatAI/laya",         # 项目已用（本地 421M）
        "kev-decision/kev",      # Qwen3.5 系，0.8B/4B/9B
        "von-decision/von",      # ModernBERT ~395M，非自回归
        "semif-ai/semif",        # 原名 OpenJev，从开放 LLM 读选项概率
        "rizzo-flow/rizzo-flow", # Spark-X2.5 + llama.cpp
    ],
    "active_learning_bo": [      # 我们自研 GP+UCB，对标/吸收成熟实现
        "pytorch/botorch",
        "facebook/Ax",
        "emdgroup/baybe",
        "optuna/optuna",
        "automl/SMAC3",
        "HerbAIx/scikit-activemodel",
    ],
    "symbolic_discovery": [      # 自治闭环已用 SINDy，对标符号回归
        "dynamicslab/pysindy",
        "astroautomata/PySR",
    ],
    "surrogate_pde": [           # 我们只有 pde_heat1d，PINN 是补强方向
        "lululxvi/deepxde",
    ],
    "bio_metabolic": [           # 52 微生物场景的代谢层
        "opencobra/cobrapy",
        "biosustain/cameo",
    ],
    "science_agent": [           # 科学发现 Agent 工作流
        "SakanaAI/AI-Scientist",
        "ur-whitelab/chemcrow-public",
        "Future-House/aviary",
        "SamuelSchmidgall/AgentLaboratory",
    ],
    "paper_grounding": [         # GC-01 审计需要回到原文核对
        "Future-House/paper-qa",
        "allenai/scifact",
    ],
    "repro_evidence": [          # 机器可查证据/可复现性（Lean 的邻接思路）
        "showyourwork/showyourwork",
        "swissdatasciencecenter/renku",
    ],
    "agent_skill_ecosystem": [   # 分发渠道：Skills / MCP
        "anthropics/skills",
        "modelcontextprotocol/servers",
        "K-Dense-AI/scientific-agent-skills",
    ],
    "surrogate_arch": [          # 代理模型架构：神经算子 / 深度核 GP（对 G1/G2/G3 泛化）
        "neuraloperator/neuraloperator",
        "camlab-ethz/poseidon",
        "cornellius-gp/gpytorch",
        "GPflow/GPflow",
        "google/jax-cfd",
    ],
    "calibration_metrics": [     # 校准评估与重校准（netcal 是标准库）
        "fKalogeropoulos/netcal",
        "gpleiss/temperature_scaling",
    ],
    "sensitivity_analysis": [    # 全局敏感性（项目 microbiology_full 自研 Sobol，应对标 SALib）
        "SALib/SALib",
    ],
    "process_simulation": [      # 机理层：SBML/ODE 过程仿真（给微生物场景补机理 oracle）
        "sys-bio/tellurium",
        "copasi/COPASI",
        "sbmlteam/libsbml",
    ],
    "data_versioning": [         # 可复现：数据/实验版本化（补"证据链"工程底座）
        "iterative/dvc",
        "mlflow/mlflow",
        "snakemake/snakemake",
    ],
    "property_testing": [        # 形式化/契约：把 fail-closed 门禁升级为可证明不变量
        "HypothesisWorks/hypothesis",
        "pschanely/CrossHair",
        "life4/deal",
    ],
    "real_data_benchmark": [     # 真实数据基准：直接补"真实锚点=0"生死线
        "pdebench/PDEBench",
        "polymathic-ai/the_well",
        "brandondamos/Design-Bench",
    ],
    "experiment_design": [       # 实验设计 DOE（补实验建议闭环）
        "titusjan/pyDOE2",
        "scikit-optimize/scikit-optimize",
        "dme65/pySOT",
    ],
    "api_framework": [           # 对外形态：把 stdlib http.server 升级为生产级 API
        "fastapi/fastapi",
        "encode/uvicorn",
        "pydantic/pydantic",
    ],
    "provenance_standards": [    # 溯源标准：把教材级引用升级为可机器校验的证据
        "ResearchObject/ro-crate-py",
        "trungdong/prov",
        "inveniosoftware/datacite",
    ],
    "bio_design": [              # 生物设计：蛋白/酶（扩展"酶催化"场景）
        "RosettaCommons/RFdiffusion",
        "dauparas/ProteinMPNN",
    ],
    "active_learning_lib": [     # 主动学习库（对标自研 GP+UCB 采集）
        "modAL-python/modAL",
        "scikit-activemodel/scikit-activemodel",
    ],
}

# 我们已知的参照（不必重复学，但用于定位）
BASELINES = [
    "bytedance/deer-flow",
    "Future-House/robin",
    "lm203688/swarmlabs-engine-kit",
]


def gh_search(query: str, token: str | None, limit: int = 8):
    """Search API: discover repos we did not already know about."""
    url = ("https://api.github.com/search/repositories?q="
           + urllib.parse.quote(query)
           + "&sort=stars&order=desc&per_page=" + str(limit))
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "swarmlabs-oss-scan")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.load(r)
    except Exception as e:
        return {"_error": f"{type(e).__name__}: {e}"}
    return d.get("items", [])


DISCOVER_QUERIES = {
    "discover_uq_surrogate": "uncertainty quantification surrogate model pushed:>2026-01-01",
    "discover_active_learning": "active learning bayesian optimization pushed:>2026-01-01",
    "discover_self_driving_lab": "self-driving laboratory OR experiment design agent pushed:>2026-01-01",
    "discover_repro_audit": "reproducibility OR research audit LLM agent pushed:>2026-01-01",
    "discover_mcp_science": "MCP server scientific computing pushed:>2026-01-01",
    "discover_decision_model": "system one decision model calibrated classifier pushed:>2026-01-01",
    "discover_hybrid_model": "hybrid physics-informed machine learning process model pushed:>2026-01-01",
    "discover_fermentation_ml": "fermentation bioprocess soft sensor machine learning pushed:>2026-01-01",
    "discover_conformal_science": "conformal prediction uncertainty scientific machine learning pushed:>2026-01-01",
}


def load_token() -> str | None:
    tok = os.environ.get("GITHUB_TOKEN")
    if tok:
        return tok
    try:
        with open(SEC, encoding="utf-8") as f:
            d = json.load(f)
        return d.get("GITHUB_TOKEN")
    except Exception:
        return None


def fetch(repo: str, token: str | None):
    url = f"https://api.github.com/repos/{repo}"
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "swarmlabs-oss-scan")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return {"_error": f"HTTP {e.code}"}
    except Exception as e:  # network / TLS
        return {"_error": f"{type(e).__name__}: {e}"}


def slim(d: dict) -> dict:
    if "_error" in d:
        return {"status": d["_error"]}
    return {
        "status": "ok",
        "full_name": d.get("full_name"),
        "stars": d.get("stargazers_count"),
        "forks": d.get("forks_count"),
        "open_issues": d.get("open_issues_count"),
        "license": (d.get("license") or {}).get("spdx_id"),
        "language": d.get("language"),
        "pushed_at": d.get("pushed_at"),
        "created_at": d.get("created_at"),
        "topics": d.get("topics", [])[:10],
        "desc": (d.get("description") or "")[:220],
        "archived": d.get("archived", False),
        "url": d.get("html_url"),
    }


def main() -> int:
    quiet = "--quiet" in sys.argv
    token = load_token()
    if not token:
        print("[warn] no GitHub token -> unauthenticated (60 req/h, will fail fast)")

    out = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": "api.github.com (live)",
        "gaps": {},
        "baselines": {},
    }

    n_ok = n_err = 0
    for gap, repos in GAPS.items():
        out["gaps"][gap] = {}
        for r in repos:
            rec = slim(fetch(r, token))
            out["gaps"][gap][r] = rec
            if rec.get("status") == "ok":
                n_ok += 1
            else:
                n_err += 1
            if not quiet:
                if rec.get("status") == "ok":
                    print(f"  {gap:24s} {r:48s} ★{rec['stars']:>7,}  "
                          f"{str(rec['license']):12s} pushed={str(rec['pushed_at'])[:10]}")
                else:
                    print(f"  {gap:24s} {r:48s} !! {rec['status']}")
            time.sleep(0.35)  # be polite

    for r in BASELINES:
        out["baselines"][r] = slim(fetch(r, token))
        time.sleep(0.35)

    # ---- 主动发现：搜索近一年活跃的新项目（避免只扫我已知的名单）----
    out["discovered"] = {}
    for tag, q in DISCOVER_QUERIES.items():
        items = gh_search(q, token, limit=8)
        if isinstance(items, dict) and "_error" in items:
            out["discovered"][tag] = {"query": q, "error": items["_error"]}
            if not quiet:
                print(f"  {tag:26s} !! {items['_error']}")
            continue
        recs = [{
            "full_name": it.get("full_name"),
            "stars": it.get("stargazers_count"),
            "license": (it.get("license") or {}).get("spdx_id"),
            "pushed_at": it.get("pushed_at"),
            "desc": (it.get("description") or "")[:200],
            "url": it.get("html_url"),
        } for it in items]
        out["discovered"][tag] = {"query": q, "items": recs}
        if not quiet:
            print(f"--- {tag} ---")
            for r in recs:
                print(f"    {str(r['full_name'])[:44]:44s} ★{r['stars']:>7,} pushed={str(r['pushed_at'])[:10]}")
        time.sleep(2.0)  # search API: 30 req/min authenticated

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    if not quiet:
        print(f"\n[ok] {n_ok} repos fetched, {n_err} failed -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
