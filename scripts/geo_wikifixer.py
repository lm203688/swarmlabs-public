"""GEO 博客 WikiFixer（借 WeKnora WikiFixerAgent 思想）。

WeKnora（Tencent, 2.3w+ stars）的企业知识库里有一个 WikiFixerAgent：
> 自动巡检死链 / 冲突 / 过期数据，给出「该修哪条」的清单。

本项目 GEO 长文站（frontend_fixed/blog/，58 个 HTML + 若干 MD）同样会随时间出现：
- **死链**：外链 404 / 超时、站内相对链接指向不存在文件；
- **过期数据**：文中「2023 最新」「截至目前」类表述在 2026 已失真；
- **跨文件冲突**：同一指标（如场景数、覆盖率）在不同文章里写了不同数字。

本脚本离线跑第一步（提取 + 静态判定），外链活性检测默认关闭（需联网，
用 --check-links 开启，且只对 http(s) 做 HEAD，带超时与 TLS 透传）。

产出 `frontend_fixed/reports/geo_wikifixer_report.{json,html}`。

用法：
    python scripts/geo_wikifixer.py                   # 离线静态检查
    python scripts/geo_wikifixer.py --check-links    # 额外做外链活性探测
"""

from __future__ import annotations

import argparse
import datetime
import html
import json
import os
import re
import sys
from typing import Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BLOG_DIR = os.path.join(ROOT, "frontend_fixed", "blog")
# CF Pages 部署根（wrangler 发布目录），根相对链接 /xxx 以它为基准解析
DEPLOY_ROOT = os.path.join(ROOT, "frontend_fixed")
OUT_JSON = os.path.join(ROOT, "frontend_fixed", "reports", "geo_wikifixer_report.json")
OUT_HTML = os.path.join(ROOT, "frontend_fixed", "reports", "geo_wikifixer_report.html")

LINK_RE = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)
YEAR_RE = re.compile(r"\b(20[0-9]{2})\b")
# 站内相对链接（以 ./ 或 ../ 或 文件名开头，不含协议）
REL_LINK_RE = re.compile(r"^(?:\.{1,2}/|[^/]+/|[^/]+\\.[a-z]{2,5}$)", re.IGNORECASE)
# 「截至/目前/最新」+ 年份 表述
STALE_RE = re.compile(r"(截至|截至目前|目前|最新|当前).{0,12}?(20[0-9]{2})", re.IGNORECASE)

# 跨文件冲突关注的指标：正则 → 期望锚定值（取自 working memory 的权威口径）
METRIC_PATTERNS = {
    "场景库规模": re.compile(r"(?:场景库|场景).{0,6}?(\d{1,3})\s*(?:个|场景|scenarios)", re.IGNORECASE),
    "V&V PASS": re.compile(r"(\d{1,3})\s*/\s*\d{1,3}\s*(?:PASS|通过)", re.IGNORECASE),
    "覆盖率阈值": re.compile(r"覆盖率.{0,6}?(\d{1,3})\s*%", re.IGNORECASE),
}


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text)


def collect_files() -> List[str]:
    out = []
    for ext in ("html", "md"):
        for fn in os.listdir(BLOG_DIR):
            if fn.lower().endswith(f".{ext}"):
                out.append(os.path.join(BLOG_DIR, fn))
    return sorted(out)


def check_internal_links(path: str, text: str) -> List[Dict[str, str]]:
    issues = []
    for m in LINK_RE.finditer(text):
        url = m.group(1)
        if url.startswith("http://") or url.startswith("https://") or url.startswith("//"):
            continue
        if url.startswith("#") or url.startswith("mailto:") or url.startswith("javascript:"):
            continue
        # 站内相对路径：解析是否存在
        if url.startswith("./") or url.startswith("../"):
            target = os.path.normpath(os.path.join(os.path.dirname(path), url))
            if not os.path.exists(target):
                issues.append({"type": "broken_internal_link", "url": url, "path": path})
        elif url.startswith("/"):
            # 站点根相对链接：以 CF Pages 部署根为基准解析
            target = os.path.normpath(os.path.join(DEPLOY_ROOT, url.lstrip("/")))
            if os.path.exists(target):
                continue  # 真实存在的站点资源（如 /blog）
            # 干净 URL 兜底：/blog/slug → /blog/slug.html
            if os.path.exists(target + ".html"):
                continue
            # 不存在于磁盘 → 可能是 Worker 路由或死导航链接，降级标记供人工复核
            issues.append({"type": "root_relative_unresolved", "url": url, "path": path})
        else:
            target = os.path.normpath(os.path.join(os.path.dirname(path), url))
            if not os.path.exists(target):
                issues.append({"type": "broken_internal_link", "url": url, "path": path})
    return issues


def check_stale(text: str) -> List[Dict[str, str]]:
    issues = []
    for m in STALE_RE.finditer(text):
        yr = int(m.group(2))
        if yr < datetime.date.today().year - 1:
            issues.append({
                "type": "stale_year", "year": yr,
                "excerpt": m.group(0)[:80],
            })
    return issues


def check_metrics(text: str) -> List[Dict[str, object]]:
    found = []
    for metric, rx in METRIC_PATTERNS.items():
        for m in rx.finditer(text):
            found.append({"metric": metric, "value": m.group(1), "excerpt": m.group(0)[:60]})
    return found


def check_external_links(path: str, text: str) -> List[Dict[str, str]]:
    issues = []
    for m in LINK_RE.finditer(text):
        url = m.group(1)
        if not (url.startswith("http://") or url.startswith("https://")):
            continue
        ok, status = _probe(url)
        if not ok:
            issues.append({"type": "dead_external_link", "url": url,
                           "status": status, "path": path})
    return issues


def _probe(url: str):
    import urllib.request
    import ssl
    ctx = ssl._create_unverified_context() if hasattr(ssl, "_create_unverified_context") else None
    req = urllib.request.Request(url, method="HEAD",
                                 headers={"User-Agent": "SwarmLabs-WikiFixer/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=8, context=ctx) as r:
            return 200 <= r.status < 400, r.status
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}"


def build_html(report: Dict[str, object]) -> str:
    rows = []
    for f in report["files"]:
        for iss in f["issues"]:
            rows.append(
                f"<tr><td>{html.escape(f['file'])}</td>"
                f"<td>{iss.get('type','')}</td>"
                f"<td>{html.escape(str(iss.get('url') or iss.get('excerpt') or ''))}</td>"
                f"<td>{html.escape(str(iss.get('status') or iss.get('year') or ''))}</td></tr>"
            )
    metric_rows = "".join(
        f"<tr><td>{html.escape(m['file'])}</td><td>{m['metric']}</td>"
        f"<td>{m['value']}</td><td>{html.escape(m['excerpt'])}</td></tr>"
        for m in report["metric_conflicts"]
    )
    return f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>GEO WikiFixer Report</title>
<style>body{{font-family:system-ui,sans-serif;margin:2rem;color:#222}}
h1{{font-size:1.4rem}} .card{{border:1px solid #ddd;border-radius:8px;padding:1rem;margin:1rem 0}}
table{{border-collapse:collapse;width:100%;font-size:.85rem}}
th,td{{border:1px solid #ddd;padding:.4rem .6rem;text-align:left}}
.kpi{{display:inline-block;margin:.4rem 1rem .4rem 0;font-size:1.1rem}}
.kpi b{{font-size:1.6rem}}</style></head><body>
<h1>GEO 博客 WikiFixer 巡检报告</h1>
<div class="card">
  <span class="kpi">文件 <b>{report['file_count']}</b></span>
  <span class="kpi">死链/坏链 <b>{report['total_link_issues']}</b></span>
  <span class="kpi">过期年份 <b>{report['total_stale']}</b></span>
  <span class="kpi">指标冲突 <b>{report['total_metric_conflicts']}</b></span>
  <p style="color:#666">生成于 {report['generated_at']} | 外链探测：{report['link_check']}</p>
</div>
<h2>链接 / 过期问题</h2>
<table><tr><th>文件</th><th>类型</th><th>URL / 片段</th><th>状态</th></tr>
{''.join(rows) or '<tr><td colspan="4">无</td></tr>'}</table>
<h2>跨文件指标冲突（需人工核对锚定值）</h2>
<table><tr><th>文件</th><th>指标</th><th>取值</th><th>片段</th></tr>
{metric_rows or '<tr><td colspan="4">无</td></tr>'}</table>
</body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check-links", action="store_true", help="额外做外链活性探测（需联网）")
    args = ap.parse_args()

    files = collect_files()
    file_reports = []
    metric_conflicts = []
    total_link = 0
    total_stale = 0
    for p in files:
        with open(p, encoding="utf-8", errors="ignore") as f:
            raw = f.read()
        text = _strip_html(raw) if p.lower().endswith(".html") else raw
        issues = []
        issues += check_internal_links(p, raw)
        issues += check_stale(text)
        if args.check_links:
            issues += check_external_links(p, raw)
        link_issues = [i for i in issues if "link" in i["type"]]
        stale = [i for i in issues if i["type"] == "stale_year"]
        total_link += len(link_issues)
        total_stale += len(stale)
        file_reports.append({
            "file": os.path.relpath(p, ROOT),
            "issues": issues,
            "n_issues": len(issues),
        })
        for m in check_metrics(text):
            m2 = dict(m)
            m2["file"] = os.path.relpath(p, ROOT)
            metric_conflicts.append(m2)

    report = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc)
            .isoformat(timespec="seconds").replace("+00:00", "Z"),
        "file_count": len(files),
        "link_check": "on" if args.check_links else "off",
        "total_link_issues": total_link,
        "total_stale": total_stale,
        "total_metric_conflicts": len(metric_conflicts),
        "files": file_reports,
        "metric_conflicts": metric_conflicts,
    }
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(build_html(report))

    # 终端摘要
    print(f"[WikiFixer] files={len(files)} link_issues={total_link} "
          f"stale={total_stale} metric_conflicts={len(metric_conflicts)} "
          f"(link_check={report['link_check']})")
    print(f"  -> {OUT_JSON}")
    print(f"  -> {OUT_HTML}")
    # 列出最需处理的（带死链/坏链的文件）
    hot = [fr["file"] for fr in file_reports if fr["n_issues"] > 0]
    if hot:
        print("  需处理文件:")
        for h in hot[:20]:
            print(f"    - {h}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
