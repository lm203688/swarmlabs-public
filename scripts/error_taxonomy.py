"""Error taxonomy registry + scan tool (硬骨头 3 基础设施).

设计原则（对齐《地基承重审计与硬骨头报告》§九 · 硬骨头 3）：
- 每处 `except Exception` 必须有 `error_kind` 标记，三档：
    FATAL      — 必须 fail-closed，任何静默都会掩盖正确性风险
    DEGRADABLE — 可降级但须记录（写日志/台账/告警）
    IGNORABLE  — 可静默（仅限纯 I/O 兜底如 "文件不存在则返回默认"）
- 通过 inline 注释注入 `# error_kind:XXX — 简述` 或 `# noqa: error_kind:XXX`
- 门禁脚本 `scripts/check_error_taxonomy.py` 扫描覆盖率；未标注的静默
  `except Exception: pass` 在**门禁模块**（verification_gate / scitrust_eval /
  hive_gate / trust_surface 相关）内一律报错，其它模块 WARN。

用法：
    python scripts/error_taxonomy.py scan                 # 打印报告
    python scripts/error_taxonomy.py scan --json          # JSON 输出
    python scripts/check_error_taxonomy.py                # CI 门禁

标记示例：
    try:
        with open(p) as f: ...
    except FileNotFoundError:
        return None                                        # error_kind:IGNORABLE — 文件不存在，返回默认
    except Exception as e:                                 # error_kind:DEGRADABLE — 台账写入失败可降级
        log_warning("ledger_write_failed", exc=e)
        return default
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent

#: 错误分类（三档，与硬骨头报告口径一致）
ERROR_KINDS = ("FATAL", "DEGRADABLE", "IGNORABLE")

#: 门禁模块：这些模块的静默吞异常必须 fail-closed，未标注报错。
#:
#: 2026-10-16 晚评估报告 §7 · P1 扩展：把"安全关键路径"上的模块加入门禁集合。
#: 判据（三选一即算安全关键）：
#:   (a) 承担硬阻断决策（gate / action / verify 决策）—— 静默吞异常即 fail-open；
#:   (b) 承担溯源指纹或锚点独立性的计算 —— 静默吞异常即"结论来源不明"；
#:   (c) 承担形式化不变量的检查 —— 静默吞异常即"声明式约束被绕过"。
#: 2026-10-16 之前只 11 个模块在集合内，FATAL 站点数为 0 —— 安全关键路径
#: 上没有任何 FATAL 标注本身就是"分类学形式化但未落地"。本次把 5 个
#: 安全关键路径模块纳入集合，并把它们内部对正确性有决定性作用的
#: 静默吞异常打上 FATAL 标注。
GATE_MODULES = frozenset({
    # 硬阻断决策链（gate / action）
    "engine/verification_gate.py",
    "engine/scitrust_eval.py",
    "engine/hive_gate.py",
    "engine/gate.py",
    "engine/guard.py",
    "engine/scen.py",
    # 溯源与锚点独立性
    "engine/trust_surface.py",
    "engine/provenance_binding.py",
    "engine/provenance_guard.py",
    "engine/wet_lab_anchors.py",
    "engine/anchor_independence.py",
    # 形式化不变量
    "engine/formal_invariants.py",
    "engine/spectral_guard.py",
    # 编排层
    "engine/scenario_registry.py",
    "engine/flywheel.py",
    "engine/autonomous_loop.py",
    # 代理内核（R²/覆盖率判定源头）
    "engine/surrogate.py",
})

#: 匹配 `except Exception` / `except BaseException` 的行
_EXCEPT_RE = re.compile(r"\bexcept\s+(?:Exception|BaseException)\b")

#: 匹配 inline error_kind 标记（同一次捕获内可位于 except 行或紧邻 1-3 行）
_KIND_RE = re.compile(
    r"(?:#.*\berror_kind:(FATAL|DEGRADABLE|IGNORABLE)\b"
    r"|noqa:\s*error_kind:(FATAL|DEGRADABLE|IGNORABLE))"
)

#: 匹配既有的旧式标注（silent-ok 已审计、BLE001 抑制 broad-exception）
_LEGACY_MARKS = (
    "silent-ok",       # noqa: silent-ok — Exception 可安全忽略（已审计 ...）
    "BLE001",          # noqa: BLE001  — broad-exception 抑制
    "BLE0",
    "已审计",
)


@dataclass
class Site:
    path: str
    lineno: int
    raw: str                 # 原始 `except ...` 行
    next_lines: List[str]    # 后续 3 行（用于识别 pass / raise / log）
    kind: Optional[str]      # FATAL/DEGRADABLE/IGNORABLE，未标 None
    has_legacy_mark: bool    # 是否已有 silent-ok / BLE001 等
    is_silent: bool          # 是否紧跟 `pass` / 仅 `return` / 仅 `continue`
    is_raise: bool           # 是否 re-raise
    is_log: bool             # 是否含 print/log/logging/warn 等降级记录
    is_gate: bool            # 是否位于门禁模块
    violation: Optional[str] # 违规描述（如 "silent-no-kind-in-gate-module"）


def _classify_next(body_lines: List[str]) -> Tuple[bool, bool, bool]:
    """从 except 块前 3 行推断：silent / raise / log."""
    # 展开非注释非空行
    sig = []
    for ln in body_lines:
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        sig.append(s)
        if len(sig) >= 3:
            break
    text = " ".join(sig)
    is_silent = bool(sig) and sig[0] == "pass"
    is_raise = "raise" in text and not is_silent
    is_log = any(tok in text.lower() for tok in (
        "print(", "log.", "logging.", "warn", "warning", "logger", "append_ledger",
        "console", "emit", "record(",
    ))
    return is_silent, is_raise, is_log


def scan_file(path: Path) -> List[Site]:
    """扫描单个文件，返回所有 except Exception 站点."""
    rel = path.relative_to(ROOT).as_posix()
    is_gate = rel in GATE_MODULES
    sites: List[Site] = []
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()

    i = 0
    while i < len(lines):
        line = lines[i]
        if not _EXCEPT_RE.search(line):
            i += 1
            continue

        # 找 error_kind 标记：本行 + 后续 3 行内
        window = lines[i: min(i + 4, len(lines))]
        joined = "".join(window)
        m = _KIND_RE.search(joined)
        kind = None
        if m:
            kind = next(g for g in m.groups() if g)
        has_legacy = any(mark in joined for mark in _LEGACY_MARKS)

        # 抓 except 块体（下一个非空非注释非缩进 <= except 缩进的行之前）
        body_lines = lines[i + 1: i + 8]
        is_silent, is_raise, is_log = _classify_next(body_lines)

        # 违规判定：门禁模块内 silent 且未标 error_kind
        violation = None
        if is_gate and is_silent and kind is None and not has_legacy:
            violation = "silent-no-kind-in-gate-module"
        elif is_gate and kind is None and not has_legacy and not is_raise and not is_log:
            violation = "no-kind-in-gate-module"

        sites.append(Site(
            path=rel,
            lineno=i + 1,
            raw=line.rstrip(),
            next_lines=[l.rstrip() for l in lines[i + 1: i + 4]],
            kind=kind,
            has_legacy_mark=has_legacy,
            is_silent=is_silent,
            is_raise=is_raise,
            is_log=is_log,
            is_gate=is_gate,
            violation=violation,
        ))
        i += 1

    return sites


def scan_repo(
    root: Optional[Path] = None,
    roots: Optional[List[str]] = None,
) -> List[Site]:
    """扫描仓库，返回所有站点."""
    root = root or ROOT
    roots = roots or ["engine", "scripts"]
    sites: List[Site] = []
    for r in roots:
        for p in (root / r).rglob("*.py"):
            sites.extend(scan_file(p))
    sites.sort(key=lambda s: (s.path, s.lineno))
    return sites


def summarize(sites: List[Site]) -> dict:
    total = len(sites)
    by_kind: Dict[str, int] = {"FATAL": 0, "DEGRADABLE": 0, "IGNORABLE": 0}
    untagged = 0
    legacy_only = 0
    silent = 0
    silent_untagged = 0
    violations = []
    gate_sites = 0
    gate_violations = 0
    per_file: Dict[str, dict] = {}

    for s in sites:
        if s.is_gate:
            gate_sites += 1
        if s.kind:
            by_kind[s.kind] += 1
        elif s.has_legacy_mark:
            legacy_only += 1
        else:
            untagged += 1
        if s.is_silent:
            silent += 1
            if s.kind is None and not s.has_legacy_mark:
                silent_untagged += 1
        if s.violation:
            violations.append(asdict(s))
            if s.is_gate:
                gate_violations += 1

        per_file.setdefault(s.path, {"total": 0, "tagged": 0, "silent": 0, "silent_untagged": 0})
        per_file[s.path]["total"] += 1
        if s.kind or s.has_legacy_mark:
            per_file[s.path]["tagged"] += 1
        if s.is_silent:
            per_file[s.path]["silent"] += 1
        if s.is_silent and s.kind is None and not s.has_legacy_mark:
            per_file[s.path]["silent_untagged"] += 1

    tagged = total - untagged
    coverage = (tagged / total * 100) if total else 100.0

    return {
        "total": total,
        "by_kind": by_kind,
        "tagged": tagged,
        "untagged": untagged,
        "legacy_only": legacy_only,
        "silent": silent,
        "silent_untagged": silent_untagged,
        "gate_sites": gate_sites,
        "gate_violations": gate_violations,
        "coverage_pct": round(coverage, 2),
        "violations": violations,
        "per_file": per_file,
    }


def main(argv: List[str]) -> int:
    if not argv or argv[0] not in ("scan", "--json", "help", "-h"):
        print(__doc__)
        return 0

    as_json = "--json" in argv
    sites = scan_repo()
    s = summarize(sites)

    if as_json:
        print(json.dumps(s, ensure_ascii=False, indent=2))
    else:
        print(f"error taxonomy scan — {s['total']} `except Exception` sites")
        print(f"  total:          {s['total']}")
        print(f"  tagged (new):   {s['by_kind']['FATAL']+s['by_kind']['DEGRADABLE']+s['by_kind']['IGNORABLE']} "
              f"(FATAL={s['by_kind']['FATAL']} "
              f"DEGRADABLE={s['by_kind']['DEGRADABLE']} "
              f"IGNORABLE={s['by_kind']['IGNORABLE']})")
        print(f"  legacy-marked:  {s['legacy_only']}")
        print(f"  untagged:       {s['untagged']}")
        print(f"  silent:         {s['silent']}  (untagged: {s['silent_untagged']})")
        print(f"  gate modules:   {s['gate_sites']}  (violations: {s['gate_violations']})")
        print(f"  coverage:       {s['coverage_pct']}%")
        if s["violations"]:
            print("\nviolations:")
            for v in s["violations"][:40]:
                print(f"  [{v['violation']}] {v['path']}:{v['lineno']}  {v['raw'][:80]}")
            if len(s["violations"]) > 40:
                print(f"  ... {len(s['violations']) - 40} more")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
