#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""llms.txt v2 规范 CI 一致性检查（板块 10 P1，2026-10-15）。

背景
----
2026-10-14 板块 10 P0 修好了 llms.txt v1→v2（Jeremy Howard 2026-08-10 提案
的 Markdown 规范）：把 16 行注释式 `# xxx` + `url -> desc` 键值对换成
79 行 v2 Markdown 结构。但修完那天之后没有 CI 检查，任何人重新生成都会
**静默回退**到 v1 格式——这是同类问题在 `paper_validation` 三版本混乱里
已经踩过的坑（板块 11 已修）。

本检查做 5 件事：
1. 存在性：`frontend_fixed/llms.txt` 必须存在；
2. 顶部标题：第一行必须是 `# SwarmLabs`；
3. 引用块定位：第 3 行必须以 `> AI4S` 开头（项目定位）；
4. 分组标题：必须含 7 个 v2 规范要求的 `##` 分组；
5. 尾部元数据：必须含 `Last updated:` / `Locale:` / `Spec:`；
6. 反回归：绝不出现 v1 格式特征（`url -> desc` / 纯注释式）；
7. 一致性：`geo_engine.build_llms_txt()` 生成的内容与磁盘一致（防静默漂移）。

用法
----
    python scripts/check_llms_txt_ci.py            # 打印 + 非零退出=失败
    python scripts/check_llms_txt_ci.py --quiet    # 只退出码，无输出

CI 契约：非零退出即失败。
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LLMS_PATH = os.path.join(ROOT, "frontend_fixed", "llms.txt")

#: v2 规范要求的最小分组标题（缺一即失败）
REQUIRED_SECTIONS = (
    "## Documentation",
    "## Honest Boundaries",
    "## V&V Gate & Trust",
    "## Generalization Benchmarks",
    "## Scenarios & Knowledge",
    "## GEO Authority Content",
    "## Product & Community",
    "## What We Explicitly Do NOT Claim",
)

#: v1 格式的反回归特征（出现即失败）
V1_SIGNATURES = (
    r"^[A-Za-z0-9\-_/]+\s*->\s*",  # url -> desc 键值对
    r"^#\s*https?://",              # 纯注释式 URL
    r"^#[^\n]*\bhttps?://\S+\s*$",  # 注释+URL 单行
)


def _read() -> str:
    with open(LLMS_PATH, encoding="utf-8") as f:
        return f.read()


def _lines(text: str) -> list[str]:
    return text.splitlines()


def check_exists() -> tuple[bool, str]:
    if not os.path.exists(LLMS_PATH):
        return False, f"llms.txt 缺失: {LLMS_PATH}"
    return True, "存在"


def check_top_structure(text: str) -> tuple[bool, list[str]]:
    """顶部：# SwarmLabs 标题 + > AI4S 引用块定位。"""
    fails: list[str] = []
    lines = _lines(text)
    if not lines or not lines[0].startswith("# SwarmLabs"):
        fails.append(f"首行不是 '# SwarmLabs'，实际：{lines[0] if lines else '<empty>'!r}")
    # 找到引用块定位（第 3 行或紧随后的空行）
    quote_hit = any(l.startswith("> AI4S") for l in lines[:6])
    if not quote_hit:
        fails.append("前 6 行内缺 `> AI4S` 项目定位引用块")
    return (not fails), fails


def check_sections(text: str) -> tuple[bool, list[str]]:
    """v2 规范要求的分组标题必须齐全。"""
    fails: list[str] = []
    for sec in REQUIRED_SECTIONS:
        if sec not in text:
            fails.append(f"缺分组标题: {sec!r}")
    return (not fails), fails


def check_footer(text: str) -> tuple[bool, list[str]]:
    """尾部元数据：Last updated / Locale / Spec。"""
    fails: list[str] = []
    for key in ("Last updated:", "Locale:", "Spec:"):
        if key not in text:
            fails.append(f"缺尾部元数据: {key}")
    return (not fails), fails


def check_no_v1_regression(text: str) -> tuple[bool, list[str]]:
    """反回归：不得出现 v1 注释式特征。"""
    fails: list[str] = []
    lines = _lines(text)
    for i, line in enumerate(lines, 1):
        for pat in V1_SIGNATURES:
            if re.match(pat, line):
                fails.append(f"第 {i} 行含 v1 格式特征: {line[:80]!r}")
    return (not fails), fails


def check_line_count(text: str) -> tuple[bool, str]:
    """v2 完整产物应在 60–200 行之间（<60 是 v1 或残缺，>200 是膨胀）。"""
    n = len(_lines(text))
    if n < 60:
        return False, f"行数过少: {n} 行（v1 是 16 行，v2 应 ≥60）"
    if n > 200:
        return False, f"行数过多: {n} 行（可能重复或膨胀）"
    return True, f"{n} 行（在 v2 合理区间）"


def check_consistency_with_builder() -> tuple[bool, str]:
    """与 `geo_engine.build_llms_txt()` 生成内容一致（防漂移）。"""
    try:
        sys.path.insert(0, ROOT)
        from scripts.geo_engine import build_llms_txt  # noqa
    except Exception as e:
        return False, f"导入 build_llms_txt 失败: {e}"
    # build_llms_txt 会写盘——先备份再比
    disk_before = _read()
    try:
        path = build_llms_txt()
        fresh = _read()
    except Exception as e:
        return False, f"调用 build_llms_txt 失败: {e}"
    finally:
        # 回滚到磁盘原状（避免 CI 检查本身改变产物）。
        # 仅在 build_llms_txt() 成功（path 已绑定）时回滚；build 抛异常时
        # path 未绑定，走 except 分支返回，不能再引用 path（否则
        # UnboundLocalError 掩盖真实失败原因）。
        if "path" in locals():
            with open(path, "w", encoding="utf-8") as f:
                f.write(disk_before)

    # 2026-11-16：`Last updated` 是**元数据日期**，`build_llms_txt()` 用
    # `datetime.date.today()` 生成（跨天必然更新），但磁盘版本是上次提交时的
    # 日期——严格逐字节比较会在跨天后**必然**红，把「日期元数据更新」误判成
    # 「静默漂移」。真正的防漂移目标是**内容与结构**（分组/链接/主张），日期
    # 是已知必然的元数据，不是漂移信号。
    # 因此：比较前归一化 `Last updated: <date>` 的日期部分，只比非日期内容。
    # 结构/内容任何真实漂移（回退 v1、删分组、改主张）仍会被逐行严格捕获。
    _DATE_LINE_RE = re.compile(r"^Last updated:\s*\d{4}-\d{2}-\d{2}\s*$")

    def _norm(text: str) -> list[str]:
        return [
            "Last updated: <DATE>" if _DATE_LINE_RE.match(line) else line
            for line in text.splitlines()
        ]

    a = _norm(disk_before)
    b = _norm(fresh)
    if a == b:
        return True, "与 build_llms_txt() 一致（日期元数据已归一化）"
    # 报 diff 摘要（前 3 行不同）
    diffs = [
        (i + 1, a[i] if i < len(a) else "", b[i] if i < len(b) else "")
        for i in range(max(len(a), len(b)))
        if i >= len(a) or i >= len(b) or a[i] != b[i]
    ]
    summary = "; ".join(f"L{i}: 磁盘={a!r} vs 生成={b!r}" for i, a, b in diffs[:3])
    return False, f"磁盘 llms.txt 与 build_llms_txt() 输出不一致（{len(diffs)} 行差）: {summary}"


def check_no_hardcoded_dates(text: str) -> tuple[bool, str]:
    """Last updated 日期不得比今天更早超过 30 天（防陈旧）。"""
    m = re.search(r"Last updated:\s*(\d{4}-\d{2}-\d{2})", text)
    if not m:
        return False, "Last updated 日期缺失或格式错误"
    from datetime import date, datetime
    d = datetime.strptime(m.group(1), "%Y-%m-%d").date()
    days_ago = (date.today() - d).days
    if days_ago > 30:
        return False, f"Last updated 太旧：{d}（{days_ago} 天前）"
    return True, f"Last updated={d}（{days_ago} 天前）"


def run_all(strict: bool = False) -> tuple[bool, list[tuple[str, bool, str]]]:
    """跑全部检查，返回 (总体通过, [(检查名, 通过, 详情)])。

    strict=False: matches_builder 只作 warn（默认；磁盘 llms.txt 允许
                 v2 手工优化，与 build_llms_txt() 生成版有设计差异）。
    strict=True:  matches_builder 与所有其它检查一致，不一致即失败
                 （CI 用，强制磁盘 = build_llms_txt() 输出）。
    """
    checks: list[tuple[str, bool, str]] = []

    ok, msg = check_exists()
    checks.append(("exists", ok, msg))
    if not ok:
        return False, checks

    text = _read()

    ok, fails = check_top_structure(text)
    checks.append(("top_structure", ok, "; ".join(fails) or "标题+引用块 OK"))

    ok, fails = check_sections(text)
    checks.append(("sections", ok, "; ".join(fails) or f"全部 {len(REQUIRED_SECTIONS)} 分组齐全"))

    ok, fails = check_footer(text)
    checks.append(("footer_meta", ok, "; ".join(fails) or "Last updated/Locale/Spec OK"))

    ok, fails = check_no_v1_regression(text)
    checks.append(("no_v1_regression", ok, "; ".join(fails) or "无 v1 注释式特征"))

    ok, msg = check_line_count(text)
    checks.append(("line_count", ok, msg))

    ok, msg = check_consistency_with_builder()
    if ok:
        checks.append(("matches_builder", True, "与 build_llms_txt() 一致"))
    elif strict:
        # strict 模式：磁盘必须与 build_llms_txt() 完全一致
        checks.append(("matches_builder", False, msg))
    else:
        # 2026-10-17 L-13：软模式 WARN→FAIL。
        # 之前设计是「软模式允许手工优化，只有 --strict 才 fail」，但脚本本身
        # 自述是「防静默漂移」—— 37 行差 = 静默漂移。既然本脚本已接入 CI
        # （见 .github/workflows/ci-cd.yml compliance-gate），WARN 语义就等于
        # 无声放行；改成默认 fail 与「漂移即失败」的意图一致。
        # 保留 SWARMLABS_LLMS_ALLOW_SOFT_WARN=1 逃生舱用于本地调试。
        if os.environ.get("SWARMLABS_LLMS_ALLOW_SOFT_WARN") == "1":
            checks.append(("matches_builder(WARN)", True, f"[warn] {msg}"))
        else:
            checks.append(("matches_builder", False, msg))

    ok, msg = check_no_hardcoded_dates(text)
    checks.append(("fresh_date", ok, msg))

    return all(c[1] for c in checks), checks


def main() -> int:
    ap = argparse.ArgumentParser(description="llms.txt v2 CI 一致性检查")
    ap.add_argument("--quiet", action="store_true", help="只输出失败项")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出（CI 友好）")
    ap.add_argument("--strict", action="store_true",
                    help="强制磁盘与 build_llms_txt() 一致（默认软检查）")
    args = ap.parse_args()

    overall, checks = run_all(strict=args.strict)

    if args.json:
        import json
        print(json.dumps({
            "overall": overall,
            "llms_path": LLMS_PATH,
            "llms_sha256": hashlib.sha256(_read().encode()).hexdigest() if os.path.exists(LLMS_PATH) else None,
            "checks": [{"name": n, "passed": ok, "detail": d} for n, ok, d in checks],
        }, ensure_ascii=False, indent=2))
    else:
        for name, ok, detail in checks:
            marker = "PASS" if ok else "FAIL"
            if not (args.quiet and ok):
                print(f"[{marker}] {name}: {detail}")
        print(f"\noverall: {'PASS' if overall else 'FAIL'}")

    return 0 if overall else 1


if __name__ == "__main__":
    raise SystemExit(main())
