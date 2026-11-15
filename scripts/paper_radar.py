#!/usr/bin/env python3
"""Paper Radar — arXiv 信号 × Jev Noul 分级雷达 (SwarmLabs Tech Radar P3 增件)。

模式来源: awesome-jev 生态的 Paper Radar (实测 501 篇 $0.0196);
架构对齐: Jev Harness 白皮书 Step「置信度×后果分级路由」+「紧凑状态包」。

流水线: 新论文 (arXiv API 或 --offline) → 每篇 × 每条兴趣一次 Noul → 代码内套阈值
        → 按 P 排序输出 JSON + Markdown 报告 → 供 Tech Radar signal→candidate 环节消费。

用法:
  python paper_radar.py                          # 在线抓 arXiv (需网络可达 export.arxiv.org)
  python paper_radar.py --max-papers 20          # 限篇数控成本
  python paper_radar.py --offline papers.json    # 离线模式 ( [{"title","summary"}...] )
  python paper_radar.py --threshold 0.8          # 告警阈值 (默认 0.8)

成本: 每篇 × 每兴趣 1 次 Noul, 输入 = 标题+摘要截断 (~600 token/次)。
$42/1M 输入 token → 30 篇 × 4 兴趣 ≈ 72K token ≈ $0.003。输出免费。

依赖: 零第三方 (urllib)。Jev 调用 fail-closed: 非 200 一律记 error, 不静默丢参。
"""
import argparse
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

API = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
CRED_PATH = os.path.join(os.path.expanduser("~"), ".config", "typesafe", "credentials.json")
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "paper_radar")

# 兴趣问题: 与 Tech Radar SIGNAL_QUERIES 对齐 (agent 科学发现 / 代理模型+贝叶斯优化 /
# 主动学习+实验编排) + 数据库扩展主线 (2025-2026 前沿科学发现)。
INTERESTS = [
    {"id": "agent_discovery",
     "q": "Does this paper present a significant advance in LLM-agent driven scientific discovery (autonomous experimentation, agent infra, multi-agent research loops)?"},
    {"id": "surrogate_bayesopt",
     "q": "Does this paper present a significant advance in surrogate modeling or Bayesian optimization for scientific/engineering workloads?"},
    {"id": "active_learning_lab",
     "q": "Does this paper present a significant advance in active learning or orchestration of automated experimental campaigns (self-driving labs)?"},
    {"id": "frontier_discovery",
     "q": "Does this paper report a notable 2025-2026 frontier scientific discovery (biology, materials, physics) with reusable structured findings?"},
]

ARXIV_QUERIES = [
    'abs:"agent"+AND+abs:"scientific+discovery"',
    'abs:"surrogate+model"+AND+abs:"bayesian+optimization"',
    'abs:"active+learning"+AND+abs:"experimental+campaign"',
]


def load_key():
    k = os.environ.get("TYPESAFE_API_KEY")
    if k:
        return k.strip()
    if os.path.exists(CRED_PATH):
        return json.load(open(CRED_PATH, encoding="utf-8"))["api_key"].strip()
    sys.exit("[!] no API key: set TYPESAFE_API_KEY or create %s" % CRED_PATH)


def jev_noul(state, instruction, key, timeout=45):
    """单次 Noul。fail-closed: 非 200 抛异常, 由调用方记 error, 绝不静默吞。"""
    body = json.dumps({
        "model": MODEL,
        "state": state,
        "questions": {"rel": {"type": "noul", "instructions": instruction}},
    }).encode("utf-8")
    req = urllib.request.Request(API, data=body, method="POST", headers={
        "Authorization": "Bearer " + key,
        "Content-Type": "application/json",
    })
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8"))
    dt = time.time() - t0
    # 解析: 取 answers.rel; 字段缺失即 fail-closed
    ans = data.get("answers", {}).get("rel")
    if not isinstance(ans, dict) or "noul" not in ans:
        raise ValueError("missing answers.rel.noul: %s" % json.dumps(data)[:160])
    usage = data.get("usage", {})
    return {"p": float(ans["noul"]), "conf": ans.get("confidence"),
            "in_tokens": usage.get("input_tokens", 0), "ms": round(dt * 1000)}


def fetch_arxiv(max_papers):
    """arXiv API 抓取 (TLS 绕过 + 301 跟随), 与 tech_radar._http_get 同款策略。"""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    papers, seen = [], set()
    for q in ARXIV_QUERIES:
        url = ("https://export.arxiv.org/api/query?search_query=" +
               urllib.parse.quote(q, safe="") +
               "&max_results=%d&sortBy=submittedDate&sortOrder=descending" % max_papers)
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "swarmlabs-paper-radar/1.0"})
                with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
                    body = r.read().decode("utf-8", "replace")
                break
            except urllib.error.HTTPError as e:
                loc = e.headers.get("Location")
                if e.code in (301, 302, 303, 307, 308) and loc:
                    url = loc
                    continue
                body = None
                break
            except Exception:
                body = None
                time.sleep(2)
        else:
            body = None
        if not body:
            continue
        for part in body.split("<entry>")[1:]:
            try:
                title = part.split("<title>")[1].split("</title>")[0].strip().replace("\n", " ")
                summary = part.split("<summary>")[1].split("</summary>")[0].strip().replace("\n", " ")
                pid = part.split("<id>")[1].split("</id>")[0].strip()
            except IndexError:
                continue
            if title and pid not in seen:
                seen.add(pid)
                papers.append({"id": pid, "title": title, "summary": summary[:1200]})
    return papers[:max_papers]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-papers", type=int, default=30)
    ap.add_argument("--threshold", type=float, default=0.8)
    ap.add_argument("--offline", help="离线论文 JSON 文件 ([{'title','summary'}...])")
    ap.add_argument("--limit-tokens", type=int, default=200000, help="输入 token 预算护栏")
    args = ap.parse_args()

    # 1. 取论文
    if args.offline:
        raw = json.load(open(args.offline, encoding="utf-8"))
        papers = [{"id": "offline-%d" % i, "title": p["title"], "summary": p.get("summary", "")}
                  for i, p in enumerate(raw)]
        papers = papers[:args.max_papers]
        src = "offline:" + os.path.basename(args.offline)
    else:
        papers = fetch_arxiv(args.max_papers)
        src = "arxiv"
    if not papers:
        sys.exit("[!] no papers fetched (网络不可达时用 --offline)")
    print("[radar] %d papers from %s" % (len(papers), src), flush=True)

    # 2. 逐篇 × 逐兴趣 Noul (紧凑状态包: 标题+摘要, 不带对话历史)
    key = load_key()
    results, errors = [], []
    spent_tokens = 0
    t0 = time.time()
    for i, p in enumerate(papers):
        state = json.dumps({"title": p["title"], "abstract": p["summary"][:900]}, ensure_ascii=False)
        scores = {}
        for it in INTERESTS:
            if spent_tokens >= args.limit_tokens:
                errors.append({"paper": p["id"], "interest": it["id"], "err": "token budget exhausted"})
                continue
            try:
                r = jev_noul(state, it["q"], key)
                scores[it["id"]] = {"p": round(r["p"], 4), "conf": r["conf"]}
                spent_tokens += r["in_tokens"]
            except Exception as e:
                errors.append({"paper": p["id"], "interest": it["id"],
                               "err": str(e)[:140]})  # fail-closed: 显式记录, 不伪造分数
        if scores:
            results.append({"id": p["id"], "title": p["title"],
                            "scores": scores,
                            "max_p": max(s["p"] for s in scores.values())})
        if (i + 1) % 5 == 0:
            print("[radar] %d/%d scored, %.0fs, ~%d in-tokens" % (i + 1, len(papers), time.time() - t0, spent_tokens), flush=True)

    # 3. 阈值过滤 + 报告 (阈值在代码里, 不在模型里 — 白皮书原则)
    alerts = sorted([r for r in results if r["max_p"] >= args.threshold],
                    key=lambda r: -r["max_p"])
    cost_usd = spent_tokens / 1_000_000 * 0.042
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": src, "n_papers": len(papers), "threshold": args.threshold,
        "n_alerts": len(alerts), "n_errors": len(errors),
        "in_tokens": spent_tokens, "est_cost_usd": round(cost_usd, 5),
        "interests": [i["id"] for i in INTERESTS],
        "alerts": alerts, "all": sorted(results, key=lambda r: -r["max_p"]),
        "errors": errors,
    }

    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    json_path = os.path.join(OUT_DIR, "radar_%s.json" % stamp)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1, ensure_ascii=False)

    md = ["# Paper Radar — %s" % report["generated_at"][:19],
          "",
          "来源: %s | 论文 %d 篇 | 阈值 %.2f | 命中 %d | 错误 %d | ~%d in-tokens ≈ $%.5f" % (
              src, len(papers), args.threshold, len(alerts), len(errors), spent_tokens, cost_usd),
          ""]
    if alerts:
        md.append("| P(max) | 标题 | 命中兴趣 |")
        md.append("|---|---|---|")
        for a in alerts:
            hits = ", ".join("%s=%.2f" % (k, v["p"]) for k, v in a["scores"].items() if v["p"] >= args.threshold)
            md.append("| %.3f | %s | %s |" % (a["max_p"], a["title"][:90], hits))
    else:
        md.append("(无超阈值论文)")
    if errors:
        md.append("\n## 错误 (fail-closed, 未伪造分数)\n")
        for e in errors[:10]:
            md.append("- %s / %s: %s" % (e["paper"], e["interest"], e["err"]))
    md_path = os.path.join(OUT_DIR, "radar_%s.md" % stamp)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")

    print(json.dumps({"json": json_path, "md": md_path, "alerts": len(alerts),
                      "errors": len(errors), "in_tokens": spent_tokens,
                      "est_cost_usd": round(cost_usd, 5)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
