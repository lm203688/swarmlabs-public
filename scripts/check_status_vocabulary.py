#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""SwarmLabs 一致词汇表校验器。

背景
----
PROJECT_ELEVATION_REPORT_2026-10-26.md §3.3「所有状态用同一语言」：
    可建议 / 需补数据 / 仅模拟 / 越界拒答 / 等待审批 / 已证伪

AI 助手在对外输出中**只能**使用这 6 个受控状态词，不得自造近义变体。
此前项目代码中散落的「建议」「数据不足」「只是模拟」「超出范围」「未批准」「错误的」
等变体导致用户难以判断真实决策边界。此脚本把这些变体全部锁定，任何 UI/API 输出
命中变体即报红。

设计原则
--------
1. **Schema 驱动**：`schemas/status_vocabulary.schema.json` 定义 6 个受控 code 与字段。
2. **单一真源**：`data/status_vocabulary.json` 是唯一权威词汇表；本脚本不硬编码词表。
3. **扫描范围可控**：`--target-dir` 允许只扫前端 / 文档 / agent-card，避免误伤 archives/。
4. **B1 边界对齐**：REFUTED / OUT_OF_SCOPE / PENDING_APPROVAL 的
   `allow_ai_generated=false`，脚本在扫描 LLM 生成路径时也会抓出。
5. **自测 + CI 双模式**：`--selftest` 用内建 fixture 验证检测器；`--ci` 输出机器可读结果。

判据
----
- **命中规则 1（canonical）**：文本中出现 canonical_form 的 6 个 zh 词 → 合规。
- **命中规则 2（forbidden_variant）**：文本中出现任一 forbidden_variant → 违规。
- 若同一字符串同时含 canonical 和 variant（如「可建议：数据不足」），
  按违规处理（有歧义即报警）。
- `archives/`、`reports/`、`.git/` 默认不扫描（历史资料）。

用法
----
    python scripts/check_status_vocabulary.py                     # 扫全项目
    python scripts/check_status_vocabulary.py --selftest          # 自测
    python scripts/check_status_vocabulary.py --ci                # CI 门禁
    python scripts/check_status_vocabulary.py --target-dir frontend_fixed
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
VOCAB_PATH = REPO / "data" / "status_vocabulary.json"
SCHEMA_PATH = REPO / "schemas" / "status_vocabulary.schema.json"

# 扫描级别定义
# HARD = 违规即 fail（面向用户的 UI / API 响应，必须严格）
# SOFT = 只报告不 fail（内部实现 / 自然语言描述 / 分类标签）
HARD_PREFIXES = (
    "frontend_fixed/",              # 前端 UI 面向最终用户
    "frontend_fixed/functions/api/",  # API 响应字段
    ".well-known/",                 # agent-card 等对外
)
# 白名单豁免（词汇表定义文件、词汇表展示页、内部 probe label 文件等）
EXEMPT_PATHS = {
    "data/status_vocabulary.json",       # 词汇表定义本身
    "schemas/status_vocabulary.schema.json",
    "scripts/check_status_vocabulary.py", # 本脚本
    "scripts/check_status_badge_sync.py", # badge JS↔JSON 同步守护
    "frontend_fixed/vocabulary.html",     # §3.3 词汇表展示页：forbidden_variants 作为元数据列出
    "frontend_fixed/js/status-badge.js",  # badge 渲染器：不生成状态词，只按 code 上色
    "frontend_fixed/llms.txt",            # LLM 索引页：举例说明 6 个受控词与门禁作用
}

# 默认扫描范围（相对 REPO）
DEFAULT_TARGETS = ["frontend_fixed", "docs", "data", "schemas", "engine", ".well-known"]
# 永远排除
EXCLUDED_DIRS = {".git", "archives", "reports", "__pycache__", ".workbuddy", ".venv", "node_modules", ".pytest_cache"}
EXCLUDED_SUFFIXES = {".pyc", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".gz", ".tar"}


# ============ 数据加载 ============

@dataclass
class VocabEntry:
    code: str
    zh: str
    en: str
    severity: str
    allow_ai_generated: bool
    requires_evidence: bool
    forbidden_variants: list[str] = field(default_factory=list)


def load_vocabulary() -> tuple[list[VocabEntry], str, dict[str, Any]]:
    """从 data/status_vocabulary.json 加载词汇表。"""
    raw = json.loads(VOCAB_PATH.read_text(encoding="utf-8"))
    entries = []
    for e in raw["vocabulary"]:
        entries.append(VocabEntry(
            code=e["code"],
            zh=e["zh"],
            en=e["en"],
            severity=e["severity"],
            allow_ai_generated=e["allow_ai_generated"],
            requires_evidence=e["requires_evidence"],
            forbidden_variants=e.get("forbidden_variants", []),
        ))
    return entries, raw.get("canonical_form", ""), raw


# ============ 校验器 ============

def build_variant_index(vocab: list[VocabEntry]) -> dict[str, str]:
    """variant → owning code 索引。变体可能被多 code 共享（罕见），取最后一次覆盖。"""
    index: dict[str, str] = {}
    for e in vocab:
        for v in e.forbidden_variants:
            # 只把长度 ≥ 2 的变体加入索引（避免过短误伤）
            if len(v) >= 2:
                index[v] = e.code
    return index


def _canonical_spans(text: str, vocab: list[VocabEntry]) -> list[tuple[int, int]]:
    """返回所有 canonical 词在 text 中出现的位置区间 [(start, end), ...]。"""
    spans: list[tuple[int, int]] = []
    for e in vocab:
        term = e.zh
        if not term:
            continue
        for m in re.finditer(re.escape(term), text):
            spans.append((m.start(), m.end()))
    return spans


def _inside_canonical(pos: int, end: int, spans: list[tuple[int, int]]) -> bool:
    """判断 [pos, end) 是否完全落在某个 canonical 区间内。"""
    for s, e in spans:
        if pos >= s and end <= e:
            return True
    return False


def scan_text(text: str, vocab: list[VocabEntry]) -> tuple[list[dict[str, Any]], bool]:
    """扫描一段文本，返回 (violations, has_canonical)。

    violations 元素：{variant, code, position}

    判据（关键）：
    - 若 variant 完全落在某个 canonical 词内部 → 不算违规（如「可建议」中的「建议」）。
    - 若 variant 跨越 canonical 边界 → 也不算违规（如「可建议执行」，"建议" 起点在 "可建议" 内）。
    - 其余情况为违规。
    """
    variant_index = build_variant_index(vocab)
    canonical_terms = [e.zh for e in vocab]
    has_canonical = any(term in text for term in canonical_terms)
    spans = _canonical_spans(text, vocab)

    violations: list[dict[str, Any]] = []
    for variant, code in variant_index.items():
        for m in re.finditer(re.escape(variant), text):
            v_start, v_end = m.start(), m.end()
            # 跳过完全落在 canonical 内的 variant
            if _inside_canonical(v_start, v_end, spans):
                continue
            violations.append({
                "variant": variant,
                "code": code,
                "position": v_start,
                "surrounded_by_canonical": has_canonical,
            })
    return violations, has_canonical


def _classify_level(rel_path: str) -> str:
    """判断文件是 HARD 还是 SOFT 门禁级别。"""
    if rel_path in EXEMPT_PATHS:
        return "exempt"
    for prefix in HARD_PREFIXES:
        if rel_path.startswith(prefix):
            return "hard"
    return "soft"


def _to_posix(rel_path: Path | str) -> str:
    """统一转 posix 分隔符，避免 Windows 反斜杠破坏前缀匹配。"""
    if isinstance(rel_path, Path):
        return rel_path.as_posix()
    return rel_path.replace("\\", "/")


def scan_file(path: Path, vocab: list[VocabEntry]) -> dict[str, Any]:
    """扫描单个文件。返回 {path, level, violations, has_canonical, line_hits}。"""
    rel = _to_posix(path.relative_to(REPO))
    level = _classify_level(rel)
    if level == "exempt":
        return {"path": rel, "level": "exempt", "violations": [], "has_canonical": False,
                "line_hits": [], "exempt_reason": "in EXEMPT_PATHS"}
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            text = path.read_text(encoding="utf-8-sig")
        except Exception:
            return {"path": rel, "level": level, "skipped": "encoding_error"}
    except Exception as e:
        return {"path": rel, "level": level, "skipped": f"read_error: {e}"}

    violations, has_canonical = scan_text(text, vocab)
    line_hits: list[dict[str, Any]] = []
    if violations:
        lines = text.splitlines()
        # 只保留前 20 条 line_hits（防刷屏）
        seen = set()
        for v in violations:
            pos = v["position"]
            # 计算行号
            line_no = text[:pos].count("\n") + 1
            line_text = lines[line_no - 1] if line_no - 1 < len(lines) else ""
            key = (line_no, v["variant"])
            if key not in seen and len(line_hits) < 20:
                seen.add(key)
                line_hits.append({
                    "line": line_no,
                    "variant": v["variant"],
                    "code": v["code"],
                    "text_excerpt": line_text.strip()[:120],
                })
    return {
        "path": rel,
        "level": level,
        "violations": violations,
        "has_canonical": has_canonical,
        "line_hits": line_hits,
    }


def iter_scan_files(target_dirs: list[str]) -> list[Path]:
    """按 target_dirs 展开成文件列表，排除 EXCLUDED_DIRS。"""
    out: list[Path] = []
    for d in target_dirs:
        root = REPO / d
        if not root.exists():
            continue
        if root.is_file():
            out.append(root)
            continue
        for p in root.rglob("*"):
            if p.is_dir():
                continue
            # 排除目录
            rel_parts = p.relative_to(REPO).parts
            if any(part in EXCLUDED_DIRS for part in rel_parts):
                continue
            # 排除后缀
            if p.suffix in EXCLUDED_SUFFIXES:
                continue
            out.append(p)
    return out


# ============ 主检查 ============

def run_check(target_dirs: list[str] | None = None) -> dict[str, Any]:
    """扫全项目，返回完整报告（分级统计 hard/soft）。"""
    if target_dirs is None:
        target_dirs = DEFAULT_TARGETS
    vocab, canonical_form, raw = load_vocabulary()
    files = iter_scan_files(target_dirs)

    file_reports: list[dict[str, Any]] = []
    hard_violations = 0
    soft_violations = 0
    hard_files_with_violations = 0
    soft_files_with_violations = 0
    files_with_canonical = 0
    exempt_files = 0

    for p in files:
        r = scan_file(p, vocab)
        file_reports.append(r)
        lvl = r.get("level", "soft")
        if lvl == "exempt":
            exempt_files += 1
        elif r.get("violations"):
            if lvl == "hard":
                hard_violations += len(r["violations"])
                hard_files_with_violations += 1
            else:
                soft_violations += len(r["violations"])
                soft_files_with_violations += 1
        if r.get("has_canonical"):
            files_with_canonical += 1

    # 汇总变体命中（分 level）
    variant_counts_hard: dict[str, int] = {}
    variant_counts_soft: dict[str, int] = {}
    for r in file_reports:
        lvl = r.get("level", "soft")
        for v in r.get("violations", []):
            if lvl == "hard":
                variant_counts_hard[v["variant"]] = variant_counts_hard.get(v["variant"], 0) + 1
            elif lvl == "soft":
                variant_counts_soft[v["variant"]] = variant_counts_soft.get(v["variant"], 0) + 1

    # HARD 违规才导致 FAIL；SOFT 只是 advisory
    status = "FAIL" if hard_violations > 0 else "PASS"
    return {
        "status": status,
        "vocab_version": raw.get("schema_version", "?"),
        "canonical_form": canonical_form,
        "scanned_files": len(files),
        "exempt_files": exempt_files,
        "files_with_canonical": files_with_canonical,
        "hard_violations": hard_violations,
        "soft_violations": soft_violations,
        "hard_files_with_violations": hard_files_with_violations,
        "soft_files_with_violations": soft_files_with_violations,
        "total_violations": hard_violations + soft_violations,
        "variant_counts_hard": variant_counts_hard,
        "variant_counts_soft": variant_counts_soft,
        "hard_offenders": sorted(
            [r for r in file_reports if r.get("level") == "hard" and r.get("line_hits")],
            key=lambda r: -len(r["violations"]),
        )[:20],
        "soft_offenders_sample": sorted(
            [r for r in file_reports if r.get("level") == "soft" and r.get("line_hits")],
            key=lambda r: -len(r["violations"]),
        )[:10],
        "files": [r for r in file_reports if r.get("line_hits") or r.get("skipped")],
    }


# ============ Self-test ============

def _selftest() -> dict[str, Any]:
    """用内建 fixture 验证扫描器判据正确。"""
    vocab, _, _ = load_vocabulary()
    cases = [
        # (name, text, expect_violations)
        ("canonical 可建议 alone", "本方案状态：可建议。", 0),
        ("canonical 6 词全出现", "可建议|需补数据|仅模拟|越界拒答|等待审批|已证伪", 0),
        ("forbidden 可以执行 变体", "本方案可以执行。", 1),
        ("forbidden 数据不足 变体", "当前数据不足，无法判断", 1),
        ("forbidden 只是模拟 变体", "以上结果只是模拟数据", 1),
        ("forbidden 超出范围 变体", "该请求超出范围", 1),
        ("forbidden 未批准 变体", "该操作等审批中。", 1),
        ("forbidden 已否定 变体", "该结论已否定", 1),
        ("mixed: canonical + variant 同现", "本方案可建议，但因数据不足需补数据", 1),
        ("clean neutral text", "今日天气晴朗，适合实验", 0),
        ("code REFUTED as English tag", "status: REFUTED", 0),  # 只扫中文变体，不扫英文 code
    ]

    results = []
    for name, text, expect in cases:
        violations, has_canon = scan_text(text, vocab)
        ok = len(violations) == expect
        results.append({
            "name": name,
            "text": text,
            "expect": expect,
            "got": len(violations),
            "variants_found": [v["variant"] for v in violations],
            "has_canonical": has_canon,
            "ok": ok,
        })
    passed = sum(1 for r in results if r["ok"])
    return {
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "cases": results,
    }


# ============ CI mode ============

def ci_output(report: dict[str, Any]) -> None:
    """CI 模式：机器可读输出（分级）。"""
    print(f"STATUS: {report['status']}")
    print(f"VOCAB_VERSION: {report['vocab_version']}")
    print(f"SCANNED_FILES: {report['scanned_files']}")
    print(f"EXEMPT_FILES: {report['exempt_files']}")
    print(f"HARD_VIOLATIONS: {report['hard_violations']}")
    print(f"SOFT_VIOLATIONS: {report['soft_violations']} (advisory only)")
    print(f"HARD_FILES_WITH_VIOLATIONS: {report['hard_files_with_violations']}")
    if report["variant_counts_hard"]:
        print("HARD_VARIANT_HITS:")
        for v, c in sorted(report["variant_counts_hard"].items(), key=lambda x: -x[1]):
            print(f"  - {v!r}: {c}")
    if report["hard_offenders"]:
        print("HARD_OFFENDERS:")
        for f in report["hard_offenders"][:10]:
            print(f"  - {f['path']}: {len(f['violations'])} hits")
    if report["variant_counts_soft"]:
        print("SOFT_VARIANT_HITS (top 10):")
        for v, c in sorted(report["variant_counts_soft"].items(), key=lambda x: -x[1])[:10]:
            print(f"  - {v!r}: {c}")


def pretty_output(report: dict[str, Any]) -> None:
    """人类可读输出（分级）。"""
    print(f"=== Status Vocabulary Check ===")
    print(f"Status: {report['status']}  (only HARD violations fail the gate)")
    print(f"Vocab version: {report['vocab_version']}")
    print(f"Canonical form: {report['canonical_form']}")
    print(f"Scanned files: {report['scanned_files']}  (exempt: {report['exempt_files']})")
    print(f"Files with canonical term: {report['files_with_canonical']}")
    print(f"HARD violations: {report['hard_violations']} across {report['hard_files_with_violations']} files")
    print(f"SOFT violations: {report['soft_violations']} across {report['soft_files_with_violations']} files (advisory)")

    if report["hard_offenders"]:
        print("\n── HARD offenders (must fix) ──")
        for f in report["hard_offenders"][:10]:
            print(f"\n  {f['path']} ({len(f['violations'])} hits):")
            for hit in f["line_hits"][:5]:
                print(f"    L{hit['line']} [{hit['code']}]: {hit['variant']!r}")
                print(f"      └ {hit['text_excerpt']}")

    if report["variant_counts_hard"]:
        print("\nHARD variant hit counts:")
        for v, c in sorted(report["variant_counts_hard"].items(), key=lambda x: -x[1]):
            print(f"  {v!r}: {c}")

    if report["soft_offenders_sample"]:
        print("\n── SOFT offenders sample (advisory, natural language / labels) ──")
        for f in report["soft_offenders_sample"][:5]:
            print(f"\n  {f['path']} ({len(f['violations'])} hits):")
            for hit in f["line_hits"][:3]:
                print(f"    L{hit['line']} [{hit['code']}]: {hit['variant']!r}")
                print(f"      └ {hit['text_excerpt']}")

    if report["status"] == "FAIL":
        print("\n⚠️  HARD 违规：请将 frontend_fixed/** 中的变体替换为 canonical 词。见 data/status_vocabulary.json。")
    else:
        print("\n✅ HARD 层无违规。SOFT 层的变体属于自然语言/分类标签，仅记录不阻断。")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--selftest", action="store_true", help="用内建 fixture 自测")
    ap.add_argument("--ci", action="store_true", help="CI 门禁模式（机器可读）")
    ap.add_argument("--target-dir", nargs="*", default=None,
                    help=f"扫描目录（默认：{' '.join(DEFAULT_TARGETS)}）")
    ap.add_argument("--json", action="store_true", help="完整 JSON 输出")
    args = ap.parse_args(argv)

    if args.selftest:
        r = _selftest()
        print(f"Self-test: {r['passed']}/{r['total']} passed, {r['failed']} failed")
        for c in r["cases"]:
            mark = "✓" if c["ok"] else "✗"
            print(f"  [{mark}] {c['name']}: expect={c['expect']} got={c['got']}")
            if not c["ok"] and c["variants_found"]:
                print(f"       variants: {c['variants_found']}")
        return 0 if r["failed"] == 0 else 1

    report = run_check(args.target_dir)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif args.ci:
        ci_output(report)
    else:
        pretty_output(report)
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
