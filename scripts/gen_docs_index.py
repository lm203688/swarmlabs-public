#!/usr/bin/env python
"""docs/INDEX.md 生成器（P1-8，2026-10-08）。

问题
----
`docs/` 下有 170+ 篇 Markdown，其中 **106 篇直接堆在根层**，文件名带日期
后缀又不统一（96 篇无日期）。任何人都无法回答三个基本问题：

  1. 从哪篇开始读？（入口缺失）
  2. 哪些是**当前有效**的，哪些已经被取代了？（时效未知）
  3. 哪些文档引用了**已废弃的数字口径**（163 models / 47,566 entities /
     82 scenarios）？（一致性未知）

本脚本把这三个问题变成可生成、可复核的产物：`docs/INDEX.md`。

判据（全部可机械复核，不靠人工记忆）
------------------------------------
* **时效**：用 git 最后提交时间。超过 `STALE_DAYS`（180 天）标记 ⚠️。
  有 `DEPRECATED / 已废弃 / SUPERSEDED` 字样的直接标记 ⛔。
* **口径**：正文里出现已废弃数字（见 `DEPRECATED_PATTERNS`）即标记 🕰，
  并列出命中的片段。这些是「诚实边界」在文档层的具体落点 ——
  公开面写着 95，旧文档还写着 82，读者会以为平台缩水了。
* **分类**：按路径与文件名关键词分到固定主题桶，保证每次生成结果稳定。

输出
----
    docs/INDEX.md   —— 人读的索引（入口 + 主题清单 + 陈旧清单）
    docs/INDEX.json —— 机器读的原始数据（供 CI 校验条目数不缩水）

用法
----
    python scripts/gen_docs_index.py            # 生成
    python scripts/gen_docs_index.py --check    # 只校验（CI 用，不写文件）
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
INDEX_MD = DOCS / "INDEX.md"
INDEX_JSON = DOCS / "INDEX.json"

#: 超过这个天数未提交即视为「可能过时」（只提示，不阻断）。
STALE_DAYS = 180

#: 已废弃的对外数字口径。正文命中即标记 🕰。
#: 与 README §deprecated numbers 保持一致（P0-3 收缩后的唯一权威列表）。
DEPRECATED_PATTERNS: list[tuple[str, str]] = [
    (r"\b163\s*(?:models|模型)", "163 models（旧口径，已废弃）"),
    (r"47[,，]?566", "47,566 entities（旧口径，已废弃）"),
    (r"\b82\s*(?:scenarios|场景)", "82 scenarios（旧口径，应为 95）"),
    (r"\b62\s*(?:scenarios|场景)", "62 scenarios（旧口径，应为 95）"),
    (r"\b72\s*(?:scenarios|场景)", "72 scenarios（旧口径，应为 95）"),
    (r"\b84\s*engine modules", "84 engine modules（旧口径，应为 85）"),
    (r"\bcross[- ]disciplinary general verification layer\b", "跨学科通用验证层（已收缩为生物工艺层）"),
]

#: 主题桶：(显示名, 判定函数)。顺序即 INDEX 里的展示顺序。
#: 判定函数接收「相对 docs 的 posix 路径」，返回 True 表示属于该桶。
#:
#: **顺序即优先级**：具体桶必须排在通用桶（如 `README`）之前，
#: 否则 `competitions/xxx/README.md` 会因为文件名含 README 被误判成入口。
_TOPIC_RULES: list[tuple[str, str]] = [
    # 先按目录归属（最具体）
    ("赛事与交付", r"^competitions/|^goai_|^case_studies/|^milestones/|^artifacts/"),
    ("研究笔记", r"^research/|^analysis/|^insights/|^lab-archive/|^muse-connector/|^sdk/|^agent/"),
    ("商业化与定价", r"^pricing/|^promotion/|^partners/|^competitors/|^competitive/|^strategy/"),
    # 再按文件名关键词
    ("入口与核心目标", r"(CORE_OBJECTIVE|ROADMAP|MISSION|why_swarmlabs|^README\.md$|^docs/INDEX)"),
    ("架构与平台", r"(architecture|platform|engine|module|overview|manifest)"),
    ("可信度与验证", r"(trust|verif|honest|rubric|conformal|falsif|failure|axiom|guard|certificate)"),
    ("泛化与基准", r"(generaliz|benchmark|G1|G2|G3|scenario|sensitivity|sobol)"),
    ("研究笔记", r"(note|analysis|investigat|experiment)"),
    ("审计与评估", r"(audit|assessment|evaluation|evaluate|review|diagnos|scan|走查|评估|审计|核验|bottleneck|feasibility|signal_scan)"),
    ("融资与对外", r"(funding|grant|investor|outreach|pitch|credits|listing|market|ascend|nvidia|huawei|paddle|aigrant|china_bigtech)"),
    ("商业化与定价", r"(pricing|promotion|partner|competit|business|go_to_market|gtm)"),
    ("赛事与交付", r"(competition|goai|case_stud|milestone|deliverab|submission|报名)"),
    ("契约与规范", r"(contract|standard|protocol|schema|jwks|x402|UNIFIED|dataflow)"),
    ("战略与路线", r"(strategy|vision|plan|roadmap|alignment|realign|closure|handover)"),
    ("归档", r"(archive|_old|_legacy|deprecat)"),
]


@dataclass
class DocEntry:
    path: str  # 相对仓库根的 posix 路径
    rel: str  # 相对 docs 的 posix 路径
    title: str
    mtime: str  # ISO 日期（git last commit date，退化到文件 mtime）
    days_old: int
    bytes: int
    topic: str
    flags: list[str] = field(default_factory=list)
    deprecated_hits: list[str] = field(default_factory=list)
    deprecation_marked: bool = False


def _git_last_commit_date(rel_path: str) -> str | None:
    """该文件最后一次 git 提交的日期（ISO）。无 git / 未跟踪返回 None。"""
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%cs", "--", rel_path],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=15,
        )
        s = out.stdout.strip()
        return s or None
    except Exception:  # noqa: BLE001
        return None


def _first_heading(text: str, fallback: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
        if line.startswith("title:"):  # yaml front-matter
            return line.split(":", 1)[1].strip().strip("\"'")
    return fallback


def _topic_for(rel: str) -> str:
    low = rel.lower()
    for name, pat in _TOPIC_RULES:
        if re.search(pat, low, re.IGNORECASE):
            return name
    return "其他"


def _classify(root: Path, path: Path) -> DocEntry:
    rel = path.relative_to(DOCS).as_posix()
    repo_rel = path.relative_to(ROOT).as_posix()
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        text = ""

    iso = _git_last_commit_date(repo_rel)
    if iso:
        try:
            dt = datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)
        except Exception:  # noqa: BLE001
            dt = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    else:
        dt = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    days_old = (datetime.now(timezone.utc) - dt).days

    marked = bool(
        re.search(r"DEPRECATED|已废弃|已过时|OBSOLETE|SUPERSEDED|已被取代", text)
    )
    hits = [label for pat, label in DEPRECATED_PATTERNS if re.search(pat, text)]

    flags: list[str] = []
    if marked:
        flags.append("⛔ 已标记废弃")
    if days_old > STALE_DAYS:
        flags.append(f"⚠️ {days_old} 天未更新")
    if hits:
        flags.append("🕰 引用旧口径")

    return DocEntry(
        path=repo_rel,
        rel=rel,
        title=_first_heading(text, path.stem),
        mtime=dt.date().isoformat(),
        days_old=days_old,
        bytes=path.stat().st_size,
        topic=_topic_for(rel),
        flags=flags,
        deprecated_hits=hits,
        deprecation_marked=marked,
    )


def collect(root: Path = DOCS) -> list[DocEntry]:
    entries = []
    for p in sorted(root.rglob("*.md")):
        if p.name == "INDEX.md":
            continue
        if any(part.startswith(".") for part in p.relative_to(root).parts):
            continue
        entries.append(_classify(root, p))
    return entries


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------
def render(entries: list[DocEntry]) -> str:
    total = len(entries)
    root_level = sum(1 for e in entries if "/" not in e.rel)
    stale = [e for e in entries if any("⚠️" in f for f in e.flags)]
    dep = [e for e in entries if e.deprecation_marked]
    oldnum = [e for e in entries if e.deprecated_hits]

    by_topic: dict[str, list[DocEntry]] = {}
    for e in entries:
        by_topic.setdefault(e.topic, []).append(e)

    lines: list[str] = []
    lines.append("# 文档索引（docs/INDEX.md）")
    lines.append("")
    lines.append(
        "> **本文件由 `scripts/gen_docs_index.py` 自动生成，请勿手改。**"
        " 重新生成：`python scripts/gen_docs_index.py`"
    )
    lines.append("")
    lines.append(f"生成时间：{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("")

    # ---- 概览 ----------------------------------------------------------s
    lines.append("## 概览")
    lines.append("")
    lines.append(f"- 文档总数：**{total}**（其中根层 {root_level} 篇、子目录 {total - root_level} 篇）")
    lines.append(f"- 可能过时（>{STALE_DAYS} 天未更新）：**{len(stale)}** 篇 ⚠️")
    lines.append(f"- 已标记废弃：**{len(dep)}** 篇 ⛔")
    lines.append(f"- 引用已废弃口径：**{len(oldnum)}** 篇 🕰")
    lines.append("")
    lines.append("### 怎么读这个索引")
    lines.append("")
    lines.append("先读「入口与核心目标」桶 —— 那里定义了项目**当前**的定位与诚实边界。")
    lines.append("其余文档按主题分组，每篇标注最后更新时间与陈旧/口径警示。")
    lines.append("标 🕰 的文档与当前公开口径不一致，引用其数字前必须核对 `README.md`。")
    lines.append("")
    lines.append("---")
    lines.append("")

    # ---- 主题清单 -------------------------------------------------------
    # 注意：_TOPIC_RULES 含重复显示名（同一桶有多条规则），必须先按**出现顺序**
    # 去重，否则会渲染出多个同名小节。
    topic_order: list[str] = []
    for name, _ in _TOPIC_RULES:
        if name not in topic_order:
            topic_order.append(name)
    if any(e.topic == "其他" for e in entries):
        topic_order.append("其他")

    for topic in topic_order:
        items = by_topic.get(topic)
        if not items:
            continue
        lines.append(f"## {topic}（{len(items)}）")
        lines.append("")
        lines.append("| 文档 | 标题 | 更新 | 大小 | 标记 |")
        lines.append("|---|---|---|---|---|")
        for e in sorted(items, key=lambda x: (-x.days_old, x.rel)):
            flag = " ".join(e.flags) or "—"
            kb = f"{e.bytes / 1024:.0f} KB"
            lines.append(f"| [`{e.rel}`]({e.rel}) | {e.title} | {e.mtime} | {kb} | {flag} |")
        lines.append("")

    # ---- 陈旧清单 -------------------------------------------------------
    if stale:
        lines.append("---")
        lines.append("")
        lines.append(f"## ⚠️ 待复核：超过 {STALE_DAYS} 天未更新（{len(stale)} 篇）")
        lines.append("")
        lines.append("这些文档不一定是错的，但**其结论未经近期复核**。")
        lines.append("任何对外引用都应先确认它描述的行为仍然成立。")
        lines.append("")
        for e in sorted(stale, key=lambda x: -x.days_old):
            lines.append(f"- `{e.rel}`（{e.days_old} 天，最后更新 {e.mtime}）— {e.title}")
        lines.append("")

    # ---- 口径不符清单 ---------------------------------------------------
    if oldnum:
        lines.append("---")
        lines.append("")
        lines.append(f"## 🕰 引用已废弃口径（{len(oldnum)} 篇）")
        lines.append("")
        lines.append("这些文档里写着**当前公开面已废弃**的数字。它们不是笔误，是历史快照；")
        lines.append("保留是为了可追溯，但**不可作为现状引用**。")
        lines.append("")
        for e in sorted(oldnum, key=lambda x: x.rel):
            hits = "；".join(sorted(set(e.deprecated_hits)))
            lines.append(f"- `{e.rel}` — 命中：{hits}")
        lines.append("")

    # ---- 废弃清单 -------------------------------------------------------
    if dep:
        lines.append("---")
        lines.append("")
        lines.append(f"## ⛔ 已标记废弃（{len(dep)} 篇）")
        lines.append("")
        for e in sorted(dep, key=lambda x: x.rel):
            lines.append(f"- `{e.rel}` — {e.title}")
        lines.append("")

    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成 docs/INDEX.md")
    ap.add_argument("--check", action="store_true", help="只校验，不写文件")
    args = ap.parse_args(argv)

    entries = collect()
    md = render(entries)

    # 条目数护栏：文档数不该突然缩水（防误删 / 防 glob 失效）。
    if len(entries) < 100:
        print(
            f"[FAIL] 只收集到 {len(entries)} 篇文档（预期 >= 100）。"
            f"请检查 docs/ 目录是否被误改或 .gitignore 是否吞掉了内容。",
            file=sys.stderr,
        )
        return 1

    if args.check:
        if not INDEX_MD.exists():
            print("[FAIL] docs/INDEX.md 不存在，请先运行 python scripts/gen_docs_index.py")
            return 1
        old = INDEX_MD.read_text(encoding="utf-8")
        # 忽略「生成时间」行后比较
        strip = lambda s: "\n".join(
            l for l in s.splitlines() if not l.startswith("生成时间：")
        )
        if strip(old) != strip(md):
            print("[FAIL] docs/INDEX.md 已过期，请重新生成：python scripts/gen_docs_index.py")
            return 1
        print(f"[ok] docs/INDEX.md 与源同步（{len(entries)} 篇）")
        return 0

    INDEX_MD.write_text(md, encoding="utf-8")
    INDEX_JSON.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "root": "docs",
                "stale_days": STALE_DAYS,
                "total": len(entries),
                "stale_count": sum(1 for e in entries if any("⚠️" in f for f in e.flags)),
                "deprecated_marked_count": sum(1 for e in entries if e.deprecation_marked),
                "deprecated_number_count": sum(1 for e in entries if e.deprecated_hits),
                "entries": [
                    {
                        "path": e.path,
                        "rel": e.rel,
                        "title": e.title,
                        "mtime": e.mtime,
                        "days_old": e.days_old,
                        "bytes": e.bytes,
                        "topic": e.topic,
                        "flags": e.flags,
                        "deprecated_hits": e.deprecated_hits,
                    }
                    for e in entries
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"[ok] {INDEX_MD}  ({len(entries)} 篇)")
    print(f"[ok] {INDEX_JSON}")
    print(
        f"     陈旧 {sum(1 for e in entries if any('⚠️' in f for f in e.flags))} · "
        f"废弃 {sum(1 for e in entries if e.deprecation_marked)} · "
        f"旧口径 {sum(1 for e in entries if e.deprecated_hits)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
