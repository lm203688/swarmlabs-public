#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""锚点来源独立性分析器 —— 同源聚类 + 有效锚点数计算。

动机（2026-09-21，借自 HyperResearch 的"同源聚类"实践）
---------------------------------------------------------
我方 wet_lab_anchors 有 19 个锚点，但来源声明中存在**同源重复**：
  1. cerevisiae_glucose_mu_max 与 cerevisiae_glucose_Ks **共用全部 4 个来源**
     ⇒ 声称 8 个独立来源，实际只有 4 个。
  2. 13 个工艺论文数据锚点全部引用同一份 swarmlabs_real_paper_data.json
     ⇒ 声称 13 个来源，实际只有 1 个汇编。
  3. Bader et al. / BioProcessTools 被 E. coli Ks 和 μmax 两个锚点同时引用。

后果：如果对外宣称"19 个独立锚点"，实际独立来源远少于此数。
这是一个**方法论弱点**——对"可信验证层"的定位是致命的。

本模块提供：
  - 来源家族聚类（按作者+年份+期刊分组）
  - 有效来源计数（去重后的独立来源数）
  - 跨锚点依赖图（哪些锚点共享来源）
  - 独立性比率（有效/声称）
  - 可执行报告（JSON 输出）

设计约束：纯 stdlib，零外部依赖。
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.parse
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------------------
# 来源解析
# ---------------------------------------------------------------------------

#: DOI 正则（标准形式 10.xxxx/yyyy）
_RX_DOI = re.compile(r"(?<![\w./-])(10\.\d{4,9}/[^\s,;)\]}\u3001\u3002\uff0c\uff1b]+)")

#: PMC ID（如 PMC5733555）
_RX_PMC = re.compile(r"PMC(\d{6,9})", re.I)

#: 期刊引用模式："Journal Volume:Pages"
# 匹配 "AEM 72:1164", "IECR 42:4175", "MMBR 62:646-666" 等
_RX_JOURNAL_REF = re.compile(
    r"[A-Z][A-Za-z\.&\-\s]{1,40}?\s+"   # 期刊名（1-40 字符，可含缩写和点）
    r"(\d{1,4})"                          # 卷号
    r"[:\u2013\u2014]"                    # 分隔符（: – —）
    r"(\d{1,6}(?:\-\d{1,6})?)",          # 页码
    re.I,
)

#: 作者+年份模式："Author et al. YEAR" 或 "Author YEAR"
_RX_AUTHOR_YEAR = re.compile(
    r"([A-Z][a-zA-Z\u00c0-\u017f][a-zA-Z\u00c0-\u017f\-\.]{1,30})"  # 作者姓氏
    r"(\s+et\s+al\.?)?"                                                      # et al.
    r"\s+(\d{4})",                                                             # 年份
    re.I,
)

#: 书籍/手册引用："Year 'Title'" 或 "Year Title"
_RX_BOOK = re.compile(
    r"(\d{4})\s+['\u2018\u201c\"]?"    # 年份 + 可能的引号
    r"([A-Z][A-Za-z\u00c0-\u017f\s\-\.,'’“”]{5,80})"  # 书名
    r"['\u2019\u201d\"]?",
    re.I,
)

#: 数据汇编标识："swarmlabs_real_paper_data.json: domain"
_RX_DATA_COMPILATION = re.compile(
    r"(swarmlabs_real_paper_data\.json)[::]?\s*\(?\s*(\w+)",
    re.I,
)

#: 工具/汇编引用："BioProcessTools ..." 或 "Fellows YEAR ..."
_RX_COMPILATION = re.compile(
    r"(BioProcessTools|Fellows\s+\d{4}|Pirt\s+\d{4}|"
    r"GermEcological|PMC\d{6,9})",
    re.I,
)


@dataclass
class SourceMeta:
    """解析后的来源元数据。"""
    raw: str
    # 识别到的标识符
    doi: Optional[str] = None
    pmc_id: Optional[str] = None
    journal_ref: Optional[str] = None   # "AEM 72:1164"
    author: Optional[str] = None        # "Nanchen"
    year: Optional[int] = None
    compilation_id: Optional[str] = None  # "swarmlabs_real_paper_data.json"
    # 聚类用的稳定 key
    family_key: str = ""

    def compute_family_key(self) -> str:
        """计算来源家族 key，用于聚类。"""
        if self.doi:
            return f"doi:{self.doi.lower()}"
        if self.pmc_id:
            return f"pmc:{self.pmc_id}"
        if self.compilation_id:
            return f"comp:{self.compilation_id}"
        if self.author and self.year and self.journal_ref:
            return f"ref:{self.author.lower()}:{self.year}:{self.journal_ref.split()[0].lower()}"
        if self.author and self.year:
            return f"auth:{self.author.lower()}:{self.year}"
        # 无法识别 → 用原文前 50 字符
        return f"raw:{self.raw[:50].strip()}"


def parse_source(raw: str) -> SourceMeta:
    """从来源字符串提取元数据。"""
    meta = SourceMeta(raw=raw)
    # DOI
    m = _RX_DOI.search(raw)
    if m:
        meta.doi = m.group(1).rstrip(".,;")
    # PMC
    m = _RX_PMC.search(raw)
    if m:
        meta.pmc_id = m.group(1)
    # 期刊引用
    m = _RX_JOURNAL_REF.search(raw)
    if m:
        meta.journal_ref = m.group(0).strip()
    # 作者+年份
    m = _RX_AUTHOR_YEAR.search(raw)
    if m:
        meta.author = m.group(1).strip()
        meta.year = int(m.group(3))
    # 数据汇编
    m = _RX_DATA_COMPILATION.search(raw)
    if m:
        meta.compilation_id = m.group(1)
    meta.family_key = meta.compute_family_key()
    return meta


# ---------------------------------------------------------------------------
# 聚类与独立性分析
# ---------------------------------------------------------------------------

@dataclass
class SourceCluster:
    """来源聚类：同一出版物的所有来源条目。"""
    family_key: str
    members: list[str] = field(default_factory=list)   # 来源原始字符串
    anchor_keys: list[str] = field(default_factory=list)  # 引用该来源的锚点
    identified_by: str = ""  # "doi" / "pmc" / "journal_ref" / "author_year" / "compilation" / "raw_text"
    exemplar: str = ""  # 代表来源字符串


@dataclass
class AnchorIndependence:
    """单个锚点的独立性分析。"""
    anchor_key: str
    claimed_sources: int       # 声明的来源数
    unique_families: int       # 去重后的来源家族数
    shared_families: list[str] = field(default_factory=list)  # 被其他锚点共用的来源
    exclusive_families: list[str] = field(default_factory=list)  # 仅被本锚点引用的来源
    independence_ratio: float = 1.0  # unique_families / claimed_sources


@dataclass
class IndependenceReport:
    """完整的独立性分析报告。"""
    total_anchors: int
    total_claims: int          # 所有锚点声明的来源总数
    total_unique_families: int  # 去重后的独立来源家族数
    effective_anchor_sources: int  # 去重后的有效来源总数
    anchor_details: list[AnchorIndependence]
    shared_source_clusters: list[dict]  # 被多个锚点共用的来源聚类
    independence_ratio: float  # total_unique_families / total_claims
    retraction_checked: bool = False
    retraction_results: list[dict] = field(default_factory=list)


def analyze(anchors: list[dict]) -> IndependenceReport:
    """对锚点列表执行独立性分析。"""
    # 建立 family_key → [锚点引用] 的索引
    family_anchors: dict[str, list[str]] = defaultdict(list)
    family_sources: dict[str, list[str]] = defaultdict(list)
    family_meta: dict[str, SourceMeta] = {}

    for a in anchors:
        ak = a["key"]
        for src in a.get("sources", []):
            meta = parse_source(src)
            fk = meta.family_key
            family_anchors[fk].append(ak)
            family_sources[fk].append(src)
            family_meta[fk] = meta

    # 建立锚点 → family 的反向索引
    anchor_families: dict[str, set[str]] = defaultdict(set)
    for fk, aks in family_anchors.items():
        for ak in aks:
            anchor_families[ak].add(fk)

    # 逐锚点分析
    anchor_details: list[AnchorIndependence] = []
    total_claims = 0
    for a in anchors:
        ak = a["key"]
        claimed = len(a.get("sources", []))
        total_claims += claimed
        fams = anchor_families.get(ak, set())
        shared = sorted(fk for fk in fams if len(family_anchors[fk]) > 1)
        exclusive = sorted(fk for fk in fams if len(family_anchors[fk]) == 1)
        ai = AnchorIndependence(
            anchor_key=ak,
            claimed_sources=claimed,
            unique_families=len(fams),
            shared_families=shared,
            exclusive_families=exclusive,
            independence_ratio=round(len(fams) / claimed, 4) if claimed > 0 else 1.0,
        )
        anchor_details.append(ai)

    # 收集共享聚类（被2+锚点引用的来源）
    shared_clusters = []
    for fk, aks in sorted(family_anchors.items()):
        if len(aks) > 1:
            meta = family_meta[fk]
            identified_by = (
                "doi" if meta.doi else
                "pmc" if meta.pmc_id else
                "compilation" if meta.compilation_id else
                "journal_ref" if meta.journal_ref else
                "author_year" if meta.author and meta.year else
                "raw_text"
            )
            shared_clusters.append({
                "family_key": fk,
                "anchor_keys": sorted(set(aks)),
                "n_anchors": len(set(aks)),
                "identified_by": identified_by,
                "exemplar": meta.raw[:120],
            })

    total_unique = len(family_anchors)
    report = IndependenceReport(
        total_anchors=len(anchors),
        total_claims=total_claims,
        total_unique_families=total_unique,
        effective_anchor_sources=total_unique,
        anchor_details=anchor_details,
        shared_source_clusters=shared_clusters,
        independence_ratio=round(total_unique / total_claims, 4) if total_claims > 0 else 1.0,
    )
    return report


# ---------------------------------------------------------------------------
# 序列化
# ---------------------------------------------------------------------------

def report_to_json(report: IndependenceReport) -> dict:
    """转为可序列化的 JSON 字典。"""
    return {
        "generated_at_utc": _utc_now(),
        "total_anchors": report.total_anchors,
        "total_claimed_sources": report.total_claims,
        "total_unique_source_families": report.total_unique_families,
        "independence_ratio": report.independence_ratio,
        "independence_ratio_note": (
            "unique_families / claimed_sources — 值越低说明同源重复越多。"
            "1.0 = 每个来源仅被一个锚点引用（完全独立）；"
            "0.5 = 一半来源被多个锚点共用。"
        ),
        "anchor_details": [
            {
                "anchor_key": ad.anchor_key,
                "claimed_sources": ad.claimed_sources,
                "unique_families": ad.unique_families,
                "independence_ratio": ad.independence_ratio,
                "shared_with": ad.shared_families,
                "exclusive": ad.exclusive_families,
            }
            for ad in report.anchor_details
        ],
        "shared_source_clusters": report.shared_source_clusters,
        "shared_source_clusters_note": (
            "以下来源被 2+ 锚点同时引用——它们不算独立证据。"
            "如果 3 个锚点引用同一篇论文，那是 1 个锚点不是 3 个。"
        ),
        "retraction_checked": report.retraction_checked,
        "retraction_results": report.retraction_results,
        "method_note": (
            "聚类依据：DOI > PMC ID > 数据汇编标识 > 期刊引用（作者+年份+期刊）> "
            "作者+年份 > 原文前 50 字符。聚类后按 family_key 去重，计算有效来源数。"
        ),
    }


def _utc_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# DOI 提取与撤稿核查（借 HyperResearch 的 retraction-checker）
# ---------------------------------------------------------------------------

#: 已知撤稿 DOI 本地缓存（离线可用，不依赖网络）
#: 格式: {"<doi_lowercase>": {"status": "retracted", "note": "..."}}
_RETRACTION_CACHE_PATH = os.path.join(ROOT, "data", "retraction_cache.json")

#: Crossref API 超时（秒）
_CROSSREF_TIMEOUT = 10


def extract_all_dois(anchors: list[dict]) -> list[dict]:
    """从所有锚点的 sources 中提取 DOI。

    返回 [{anchor_key, doi, source_raw}] 列表。
    如果来源中没有显式 DOI，返回空列表。
    """
    out: list[dict] = []
    for a in anchors:
        for src in a.get("sources", []):
            meta = parse_source(src)
            if meta.doi:
                out.append({
                    "anchor_key": a["key"],
                    "doi": meta.doi,
                    "source_raw": src[:120],
                })
    return out


def _load_retraction_cache() -> dict:
    """加载本地撤稿缓存。"""
    try:
        with open(_RETRACTION_CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        # error_kind:IGNORABLE — 撤稿缓存只是网络结果的本地备忘录；缺失时
        # 退回"无缓存"，caller 仍会走网络路径。不影响判定正确性。
        return {}


def _save_retraction_cache(cache: dict) -> None:
    """保存撤稿缓存。"""
    os.makedirs(os.path.dirname(_RETRACTION_CACHE_PATH), exist_ok=True)
    with open(_RETRACTION_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
        f.write("\n")


def _crossref_retraction_status(doi: str) -> Optional[dict]:
    """通过 Crossref API 查询 DOI 的撤稿状态。

    返回 {"status": "retracted"|"ok"|"unknown", "note": str} 或 None（网络不可用）。
    """
    import urllib.request
    import urllib.error

    url = f"https://api.crossref.org/works/{urllib.parse.quote(doi)}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "SwarmLabs/1.0 (anchor independence checker)",
    })
    try:
        with urllib.request.urlopen(req, timeout=_CROSSREF_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        msg = data.get("message", {})
        # Crossref 撤稿检测：
        # 1) 如果当前记录本身是 retraction-notice，则它"撤"了某篇
        # 2) 如果当前记录有 "is-retracted-by" relationship，则它被撤了
        # 3) 如果 subtype 是 retraction，则被撤
        subtype = msg.get("subtype", "")
        if subtype == "retraction" or "retracted" in str(msg.get("type", "")).lower():
            return {"status": "retracted", "note": f"Crossref subtype={subtype}"}
        # 检查 relationships
        for rel in msg.get("relationship", {}).get("is-retracted-by", []):
            target = rel.get("id", "")
            return {"status": "retracted", "note": f"retracted by DOI {target}"}
        # 检查 license
        for lic in msg.get("license", []):
            if lic.get("URL", "").startswith("http") and "retract" in lic.get("URL", "").lower():
                return {"status": "retracted", "note": f"retraction license URL: {lic['URL']}"}
        return {"status": "ok", "note": "no retraction detected via Crossref"}
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, json.JSONDecodeError) as e:
        return {"status": "unknown", "note": f"Crossref query failed: {type(e).__name__}: {e}"}
    except Exception as e:
        # error_kind:DEGRADABLE — 撤稿状态查询失败降级为 "unknown"，由 caller
        # 决定是否 fail-closed。锚点独立性是**辅助证据链**，不是主门禁，因此
        # 不 FATAL；但也不静默——"unknown" 会让读者知道"这条锚点的撤稿
        # 状态查不到"，而不是被误当作"未撤稿"。
        return {"status": "unknown", "note": f"unexpected error: {e}"}


def check_retraction_status(anchors: list[dict],
                            use_network: bool = True) -> list[dict]:
    """检查所有锚点来源的撤稿状态。

    策略：
      1. 提取所有 DOI
      2. 先查本地缓存
      3. 缓存未命中且 use_network=True → 查 Crossref API → 写入缓存
      4. 无 DOI 的来源 → 跳过（标记为 no_doi）

    返回 [{doi, anchor_key, status, note}] 列表。
    """
    dois = extract_all_dois(anchors)
    if not dois:
        return []

    cache = _load_retraction_cache()
    results: list[dict] = []
    dirty = False

    for item in dois:
        doi_lower = item["doi"].lower().strip()
        if doi_lower in cache:
            entry = cache[doi_lower]
            results.append({
                "doi": item["doi"],
                "anchor_key": item["anchor_key"],
                "status": entry.get("status", "unknown"),
                "note": entry.get("note", "cached"),
                "source": "cache",
            })
            continue

        status = None
        if use_network:
            status = _crossref_retraction_status(item["doi"])

        if status is None:
            status = {"status": "unknown", "note": "network unavailable"}

        cache[doi_lower] = status
        dirty = True
        results.append({
            "doi": item["doi"],
            "anchor_key": item["anchor_key"],
            "status": status["status"],
            "note": status["note"],
            "source": "crossref" if use_network else "unavailable",
        })

    if dirty:
        _save_retraction_cache(cache)

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    """从 wet_lab_anchors.json 运行独立性分析并输出报告。

    用法：
        python engine/anchor_independence.py              # 仅独立性分析
        python engine/anchor_independence.py --retraction  # 含撤稿核查
    """
    # 找到锚点数据
    anchors_path = os.path.join(ROOT, "frontend_fixed", "reports", "wet_lab_anchors.json")
    if not os.path.exists(anchors_path):
        print(f"ERROR: {anchors_path} not found", file=sys.stderr)
        return 1

    with open(anchors_path, encoding="utf-8") as f:
        data = json.load(f)

    anchors = data.get("anchors", [])
    if not anchors:
        print("ERROR: no anchors found in wet_lab_anchors.json", file=sys.stderr)
        return 1

    report = analyze(anchors)

    # 撤稿核查（可选）
    check_retraction = "--retraction" in sys.argv
    if check_retraction:
        ret_results = check_retraction_status(anchors, use_network=True)
        report.retraction_checked = True
        report.retraction_results = ret_results

    result = report_to_json(report)

    # 输出到 stdout
    print(json.dumps(result, ensure_ascii=False, indent=2))

    # 同时写入 data/anchor_independence.json
    out_path = os.path.join(ROOT, "data", "anchor_independence.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
        f.write("\n")

    # 摘要
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"锚点来源独立性报告", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)
    print(f"  锚点总数:           {report.total_anchors}", file=sys.stderr)
    print(f"  声明来源总数:       {report.total_claims}", file=sys.stderr)
    print(f"  去重后独立来源家族: {report.total_unique_families}", file=sys.stderr)
    print(f"  独立性比率:         {report.independence_ratio:.4f}", file=sys.stderr)
    print(f"  共享聚类数:         {len(report.shared_source_clusters)}", file=sys.stderr)
    if report.retraction_checked:
        n_retracted = sum(1 for r in report.retraction_results if r.get("status") == "retracted")
        n_ok = sum(1 for r in report.retraction_results if r.get("status") == "ok")
        n_unknown = sum(1 for r in report.retraction_results if r.get("status") == "unknown")
        print(f"  撤稿核查:           {len(report.retraction_results)} 条 DOI 已查", file=sys.stderr)
        print(f"    OK={n_ok}  RETRACTED={n_retracted}  UNKNOWN={n_unknown}", file=sys.stderr)
        if n_retracted > 0:
            print(f"\n  *** WARNING: 发现 {n_retracted} 条已撤稿来源！***", file=sys.stderr)
            for r in report.retraction_results:
                if r.get("status") == "retracted":
                    print(f"    DOI {r['doi']} ({r['anchor_key']}): {r.get('note','')}", file=sys.stderr)
    print(f"  报告已写入:         data/anchor_independence.json", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)

    # 独立性比率 < 0.8 视为警告
    if report.independence_ratio < 0.8:
        print(f"\nWARNING: 独立性比率 {report.independence_ratio:.2f} < 0.80", file=sys.stderr)
        print("  大量来源被多个锚点共用，对外宣称的锚点数应如实标注为去重后数值。", file=sys.stderr)
        return 2  # 警告退出码

    # 已撤稿来源 → 严重警告
    if report.retraction_checked:
        n_retracted = sum(1 for r in report.retraction_results if r.get("status") == "retracted")
        if n_retracted > 0:
            print(f"\nCRITICAL: {n_retracted} 条已撤稿来源仍在锚点中", file=sys.stderr)
            print("  撤稿来源不能作为独立证据，必须移除或替换。", file=sys.stderr)
            return 3  # 严重警告退出码

    return 0


if __name__ == "__main__":
    sys.exit(main())
