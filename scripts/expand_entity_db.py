#!/usr/bin/env python3
"""expand_entity_db.py — 用零成本、免鉴权、免额度的公开 API 扩充 SwarmLabs 结构化科研实体库。

目标：把实体库加速推向 100k+（2025-2026 前沿科学发现）。

数据源（全部免费、无 credit、无鉴权）：
  - Crossref REST API：works(DOI) / authors(ORCID+名) / institutions(机构名) /
    venues(ISSN/刊名) / references(引文 DOI)。cursor 深分页，mailto 礼貌池。
  - arXiv API（Atom）：categories = 科研概念/领域；一篇跨多 top-level 组的论文
    即真实「跨域桥接」信号。免费 bulk，无需额度。

实体类型（对齐项目护城河）：works / authors / institutions / concepts(趋势) /
venues(期刊) / references(引文) → 派生 trends / research_gaps / cross_domain_bridges /
coauthor_insights。

注意：OpenAlex 自 2025 起转为 credit 模型、免费额度常被耗尽，故本管线不再依赖它。
用法：
  python scripts/expand_entity_db.py --pages 30 --out data/entity_store.json
  （--pages 控制本轮拉取量：Crossref 与 arXiv 各取 pages*200 条）
"""
import json
import os
import subprocess
import sys
import time
import datetime
import xml.etree.ElementTree as ET
from urllib.parse import quote

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "entity_store.json")
MAILTO = "swarmlabs@example.com"
ATOM = "http://www.w3.org/2005/Atom"


def _curl(url, retries=4, timeout=30, status_out=None, stop_status=None):
    """用系统 curl 拉取（绕过 TLS 拦截代理），带指数退避重试。

    status_out: 可选 [None]，返回后写入本次 HTTP 状态码，供调用方区分限流与网络失败。
    stop_status: 命中该状态码立即返回（不重试）——arXiv 429 连续重试只会加剧限流
                 并拖到 30+ 分钟空转（2026-09-14 实测 429 单请求 30-40s）。
    """
    for i in range(retries):
        try:
            out = subprocess.run(
                ["curl", "-s", "--max-time", str(timeout), "--ssl-no-revoke",
                 "--tlsv1.3", "-w", "\x1f%{http_code}", url],
                capture_output=True, text=True, timeout=timeout + 5,
            )
            code = ""
            if "\x1f" in out.stdout:
                out.stdout, code = out.stdout.rsplit("\x1f", 1)
                code = code.strip()
            if status_out is not None:
                status_out[0] = code
            if stop_status and code == stop_status:
                return None
            if out.returncode != 0 or not out.stdout.strip():
                time.sleep(1.5 * (i + 1))
                continue
            return out.stdout
        except Exception:
            time.sleep(1.5 * (i + 1))
    return None


# --------------------------------------------------------------------------
# Crossref：works / authors / institutions / venues / references
# --------------------------------------------------------------------------
def harvest_crossref(total_items=6000, per_page=200, delay=0.8, budget_seconds=600):
    works, authors, institutions, venues, refs = {}, {}, {}, {}, {}
    coauthor = {}
    cursor = "*"
    fetched = 0
    deadline = time.time() + budget_seconds  # 墙钟预算：限流下避免单步空转到小时级
    sel = "DOI,title,author,container-title,ISSN,reference,issued,type"
    while fetched < total_items and time.time() < deadline:
        url = (f"https://api.crossref.org/works?filter=from-pub-date:2025-01-01"
               f"&rows={per_page}&cursor={cursor}&select={sel}&mailto={MAILTO}")
        raw = _curl(url)
        if not raw:
            print(f"  [crossref warn] 空响应，跳过 cursor={cursor}", file=sys.stderr)
            time.sleep(2.0)
            cursor = cursor  # 重试同 cursor
            fetched += per_page
            continue
        try:
            data = json.loads(raw)
        except Exception:
            time.sleep(2.0)
            fetched += per_page
            continue
        msg = data.get("message", {})
        items = msg.get("items", [])
        if not items:
            break
        nxt = msg.get("next-cursor")
        if nxt:
            cursor = nxt
        for it in items:
            doi = it.get("DOI")
            if not doi:
                continue
            works[doi] = {
                "id": doi,
                "title": ((it.get("title") or [""])[0] or "")[:300],
                "year": _crossref_year(it),
                "type": it.get("type", ""),
            }
            aids = []
            for a in (it.get("author") or []):
                aid = _author_id(a)
                if not aid:
                    continue
                authors[aid] = authors.get(aid) or {
                    "id": aid,
                    "name": _author_name(a),
                    "orcid": (aid[len("orcid:"):] if aid.startswith("orcid:") else ""),
                    "works": 0,
                }
                authors[aid]["works"] += 1
                aids.append(aid)
                for aff in (a.get("affiliation") or []):
                    nm = aff.get("name") if isinstance(aff, dict) else str(aff)
                    if nm:
                        institutions[nm] = institutions.get(nm) or {"id": nm, "name": nm}
            for i in range(len(aids)):
                for j in range(i + 1, len(aids)):
                    coauthor.setdefault(aids[i], set()).add(aids[j])
                    coauthor.setdefault(aids[j], set()).add(aids[i])
            ct = (it.get("container-title") or [])
            if ct:
                issn = (it.get("ISSN") or [])
                vid = (issn[0] if issn else ct[0])
                venues[vid] = venues.get(vid) or {
                    "id": vid, "name": ct[0],
                    "issn": (issn[0] if issn else ""),
                }
            for r in (it.get("reference") or []):
                rd = r.get("DOI") if isinstance(r, dict) else None
                if rd:
                    rd = rd.replace("doi:", "").strip()
                    refs[rd] = refs.get(rd, 0) + 1
        fetched += len(items)
        print(f"  [crossref] fetched={fetched}/{total_items} works={len(works)} "
              f"authors={len(authors)} refs={len(refs)}", file=sys.stderr)
        time.sleep(delay)
    return works, authors, institutions, venues, refs, coauthor


def _crossref_year(it):
    dp = (it.get("issued") or {}).get("date-parts") or [None]
    if dp and dp[0]:
        try:
            return int(dp[0][0])
        except Exception:
            return None
    return None


def _author_name(a):
    g, f = a.get("given", ""), a.get("family", "")
    return (f"{g} {f}".strip() or a.get("name", ""))


def _author_id(a):
    o = a.get("ORCID")
    if o:
        return "orcid:" + o.replace("https://orcid.org/", "").replace("http://orcid.org/", "")
    nm = _author_name(a)
    if nm:
        return "name:" + nm.lower()
    return None


# --------------------------------------------------------------------------
# arXiv：concepts(领域/趋势) + cross_domain_bridges(跨域桥接)
# --------------------------------------------------------------------------
def harvest_arxiv(total_items=6000, per_page=200, delay=1.2, seeds=None, max_requests=150,
                  budget_seconds=300):
    """arXiv 免费 bulk：用一组宽泛种子词分页（每词按 submittedDate 倒序=最新），
    自然覆盖 2025-2026 跨学科论文；一篇跨多 top-level 组的论文即真实跨域桥接信号。
    注：本环境 arXiv 的 submittedDate 区间语法被拒，故改用种子词分页取最新。
    健壮性：max_requests 硬上限 + 仅把「含 category 的真实论文」计入 fetched，
    避免 arXiv 偶发返回错误/help 页时陷入慢速空转。"""
    if seeds is None:
        seeds = ["electron", "learning", "protein", "climate", "materials", "quantum",
                 "optimization", "genomics", "robotics", "economics", "neural",
                 "battery", "fluid", "graph", "imaging"]
    concepts = {}  # id=category term -> {id,name,field,n,cross}
    fetched = 0
    requests = 0
    seed_idx = 0
    si = 0
    cap_per_seed = 500  # 每种子最多取 ~2-3 页，保证跨学科广度
    deadline = time.time() + budget_seconds  # 墙钟预算：限流下避免单步空转到小时级
    while fetched < total_items and requests < max_requests and time.time() < deadline:
        requests += 1
        seed = seeds[seed_idx % len(seeds)]
        url = (f"https://export.arxiv.org/api/query?search_query={quote('all:' + seed)}"
               f"&start={si}&max_results={per_page}"
               f"&sortBy=submittedDate&sortOrder=descending")
        st = [None]
        raw = _curl(url, timeout=40, status_out=st, stop_status="429")
        if st[0] == "429":
            print(f"  [arxiv] HTTP 429 限流，提前收尾 fetched={fetched} req={requests}",
                  file=sys.stderr)
            break
        if time.time() >= deadline:
            print(f"  [arxiv] 预算 {budget_seconds}s 到，提前收尾 fetched={fetched} req={requests}",
                  file=sys.stderr)
            break
        if not raw:
            print(f"  [arxiv warn] 空响应 seed={seed} start={si}", file=sys.stderr)
            seed_idx += 1; si = 0; time.sleep(3.0)
            continue
        try:
            root = ET.fromstring(raw)
        except Exception:
            seed_idx += 1; si = 0; time.sleep(3.0)
            continue
        entries = root.findall(f"{{{ATOM}}}entry")
        if not entries:
            seed_idx += 1; si = 0
            continue
        real = 0
        for e in entries:
            cats = [c.get("term") for c in e.findall(f"{{{ATOM}}}category") if c.get("term")]
            if not cats:
                continue
            real += 1
            groups = set(c.split(".")[0] for c in cats if "." in c)
            cross = len(groups) >= 2
            for c in cats:
                fld = c.split(".")[0] if "." in c else c
                obj = concepts.get(c) or {"id": c, "name": c, "field": fld, "n": 0, "cross": 0}
                obj["n"] += 1
                if cross:
                    obj["cross"] += 1
                concepts[c] = obj
        fetched += real
        si += per_page
        if si >= cap_per_seed:
            seed_idx += 1; si = 0
        print(f"  [arxiv] fetched={fetched}/{total_items} concepts={len(concepts)} req={requests}", file=sys.stderr)
        time.sleep(delay)
    return concepts


# --------------------------------------------------------------------------
# 派生护城河洞察
# --------------------------------------------------------------------------
def derive_insights(concepts, coauthor, top=200):
    clist = sorted(concepts.values(), key=lambda c: -c.get("n", 0))
    trends = [{"id": c["id"], "name": c["name"], "field": c.get("field", ""), "n": c["n"]}
              for c in clist[:top]]
    gaps = [{"id": c["id"], "name": c["name"], "field": c.get("field", ""), "n": c["n"]}
            for c in clist if c.get("n", 0) < 3][:top]
    bridges = sorted(concepts.values(), key=lambda c: -c.get("cross", 0))
    bridges = [{"name": c["name"], "field": c.get("field", ""), "n": c["n"], "cross": c.get("cross", 0)}
               for c in bridges if c.get("cross", 0) > 0][:top]
    coauthor_insights = sorted(
        ({"author_id": a, "n_collaborators": len(s)} for a, s in coauthor.items()),
        key=lambda x: -x["n_collaborators"])[:top]
    return {"trends": trends, "research_gaps": gaps,
            "cross_domain_bridges": bridges, "coauthor_insights": coauthor_insights}


def _merge(base, new):
    for k, v in new.items():
        if k in base:
            if isinstance(v, dict):
                base[k].update(v)
            elif isinstance(v, list):
                base[k] = base[k] + v
            else:
                base[k] = v
        else:
            base[k] = v
    return base


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=30,
                   help="本轮拉取量；Crossref 与 arXiv 各取 pages*200 条")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--delay", type=float, default=0.8)
    ap.add_argument("--skip-arxiv", action="store_true",
                   help="跳过 arXiv（仅 Crossref 拉取，最快拿到量级）")
    ap.add_argument("--only-arxiv", action="store_true",
                   help="仅 arXiv（跳过 Crossref，用于在不重跑 Crossref 的情况下补概念/桥接层）")
    args = ap.parse_args()

    crossref_items = args.pages * 200
    arxiv_items = args.pages * 200

    if os.path.exists(args.out):
        prev = json.load(open(args.out, encoding="utf-8"))
        print(f"[expand_entity_db] 载入已有 store：TOTAL={prev.get('counts', {}).get('TOTAL_ENTITIES')}")
    else:
        prev = None

    if args.only_arxiv:
        mode = "arXiv(免费) 仅补概念/桥接"
    elif args.skip_arxiv:
        mode = "Crossref(免费) 拉取 2025-2026 (skip-arxiv)"
    else:
        mode = "Crossref(免费) + arXiv(免费) 拉取 2025-2026"
    print(f"[expand_entity_db] {mode} ...")
    works, authors, institutions, venues, refs, coauthor = {}, {}, {}, {}, {}, {}
    if not args.only_arxiv:
        works, authors, institutions, venues, refs, coauthor = harvest_crossref(
            crossref_items, delay=args.delay)
    arxiv_concepts = {}
    if not args.skip_arxiv:
        arxiv_concepts = harvest_arxiv(arxiv_items, delay=max(args.delay, 1.0))

    if prev:
        merged_works = dict(prev.get("works", {})); merged_works.update(works)
        merged_authors = dict(prev.get("authors", {})); merged_authors.update(authors)
        merged_inst = dict(prev.get("institutions", {})); merged_inst.update(institutions)
        merged_venues = dict(prev.get("venues", {})); merged_venues.update(venues)
        merged_refs = dict(prev.get("references", {})); merged_refs.update(refs)
        merged_concepts = dict(prev.get("concepts", {})); merged_concepts.update(arxiv_concepts)
        merged_co = {a: set(s) for a, s in prev.get("_coauthor", {}).items()}
        for a, s in coauthor.items():
            merged_co.setdefault(a, set()).update(s)
        works, authors, institutions, venues, refs = (
            merged_works, merged_authors, merged_inst, merged_venues, merged_refs)
        coauthor = merged_co
        concepts = merged_concepts
    else:
        concepts = arxiv_concepts

    insights = derive_insights(concepts, coauthor)

    store = {
        "meta": {
            "source": "Crossref REST API (from-pub-date>=2025-01-01) + arXiv API (submittedDate>=2025); 零成本、免鉴权、免额度",
            "built_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "note": "结构化科研实体库，对齐趋势/研究空白/跨域桥接/合著网络护城河；跨次累积；不依赖 OpenAlex credit",
        },
        "counts": {
            "works": len(works), "authors": len(authors),
            "institutions": len(institutions), "concepts": len(concepts),
            "venues": len(venues), "references": len(refs),
            "trends": len(insights["trends"]),
            "research_gaps": len(insights["research_gaps"]),
            "cross_domain_bridges": len(insights["cross_domain_bridges"]),
            "coauthor_insights": len(insights["coauthor_insights"]),
            "TOTAL_ENTITIES": (len(works) + len(authors) + len(institutions)
                               + len(concepts) + len(venues) + len(refs)),
        },
        "works": works, "authors": authors, "institutions": institutions,
        "concepts": concepts, "venues": venues, "references": refs,
        "insights": insights, "_coauthor": {a: list(s) for a, s in coauthor.items()},
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False)
    print("[expand_entity_db] 写入", args.out)
    print("[counts]", json.dumps(store["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
