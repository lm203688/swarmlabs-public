#!/usr/bin/env python3
"""前端 i18n 基线检查：确保公开页面符合国际化规范。

检查项：
1. 英文页面（lang='en'）不得含 CJK 字符
2. 必须有 <meta name='description'>
3. 必须有 <link rel='canonical'>
4. <html lang='...'> 必须存在且合理
5. <title> 必须存在且不超过 120 字符
6. 必须有 <!DOCTYPE html>

返回 exit 0（全部通过）或 exit 1（有违规）。
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend_fixed"
BASELINE = ROOT / "data" / "frontend_i18n_baseline.json"

# All public HTML pages
ALL_PAGES = [
    "index.html", "lab.html", "gpu.html", "login.html",
    "pricing.html", "terms.html", "privacy.html",
    "blog/index.html",
    "blog/geo-research-knowledge-graph.html",
    "blog/honest-failure-rate.html",
    "blog/validation-dashboard.html",
    "mission.html",
]

CJK_RE = re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf]')
TITLE_RE = re.compile(r'<title[^>]*>(.*?)</title>', re.DOTALL)
META_DESC_RE = re.compile(r'<meta\s+[^>]*name=["\']description["\'][^>]*>', re.IGNORECASE)
CANONICAL_RE = re.compile(r'<link\s+[^>]*rel=["\']canonical["\'][^>]*>', re.IGNORECASE)
HTML_LANG_RE = re.compile(r'<html\s+[^>]*lang=["\']([^"\']+)["\']', re.IGNORECASE)
DOCTYPE_RE = re.compile(r'^\s*<!DOCTYPE\s+html\s*>', re.IGNORECASE | re.MULTILINE)

errors = []
warnings = []


def check_page(relpath):
    """Check a single HTML page for i18n compliance."""
    path = FRONTEND / relpath
    if not path.exists():
        warnings.append(f"[WARN] {relpath}: file not found")
        return

    content = path.read_text(encoding="utf-8", errors="replace")

    # Get lang attribute
    lang_match = HTML_LANG_RE.search(content)
    lang = lang_match.group(1).lower() if lang_match else None

    # 1. DOCTYPE
    if not DOCTYPE_RE.search(content):
        errors.append(f"[ERROR] {relpath}: missing <!DOCTYPE html>")

    # 2. lang attribute
    if not lang:
        errors.append(f"[ERROR] {relpath}: missing <html lang='...'>")

    # 3. CJK in English page only
    if lang == 'en':
        cjk_matches = CJK_RE.findall(content)
        if cjk_matches:
            errors.append(f"[ERROR] {relpath}: CJK characters in English page ({len(cjk_matches)} occurrences)")

    # 4. Meta description
    if not META_DESC_RE.search(content):
        errors.append(f"[ERROR] {relpath}: missing <meta name='description'>")

    # 5. Canonical link
    if not CANONICAL_RE.search(content):
        errors.append(f"[ERROR] {relpath}: missing <link rel='canonical'>")

    # 6. Title
    title_match = TITLE_RE.search(content)
    if title_match:
        title = title_match.group(1).strip()
        if not title:
            errors.append(f"[ERROR] {relpath}: empty <title>")
        elif len(title) > 120:
            warnings.append(f"[WARN] {relpath}: title too long ({len(title)} chars, max 120)")
    else:
        errors.append(f"[ERROR] {relpath}: missing <title>")


def main():
    for page in ALL_PAGES:
        check_page(page)

    # Print results
    print(f"前端 i18n 基线检查: {len(ALL_PAGES)} pages")
    print(f"  ERRORS: {len(errors)}")
    print(f"  WARNINGS: {len(warnings)}")

    for e in errors:
        print(f"  {e}")
    for w in warnings:
        print(f"  {w}")

    if errors:
        print(f"\n[BLOCK] {len(errors)} error(s) found — fix before deploy")
        sys.exit(1)
    else:
        print(f"\n[PASS] All i18n checks passed")
        sys.exit(0)


if __name__ == "__main__":
    main()
