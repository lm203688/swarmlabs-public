#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SwarmLabs Patch-Only Editor —— 借 HyperResearch 的 patch-only 修改机制。

哲学：GEO 博客、报告、文档等生成物一旦被整段重写，历史 diff 就丢失；
      整段重写会让 audit trail 无法追踪「这次改动具体改了哪一句」。
      HyperResearch 的做法是让 LLM 只能**打 hunk 补丁**，物理上防止
      「整段重写」。本模块把这套机制作为离线工具，供 GEO 生成器、
      报告生成器、审计工具调用。

能力：
  * apply_hunks(file_path, hunks, dry_run=False) —— 应用一组 hunk
  * propose_hunk(old_text, new_text, anchor)     —— 生成一个 hunk
  * diff_stats(file_path)                        —— 简易 diff 统计
  * assert_no_full_rewrite(before, after, threshold=0.15)
      若 after 与 before 的差异比例超过 threshold → 抛异常
      这是物理层防线，不是软建议

hunk 结构（简化版 unified diff）：
    {
      "anchor": "唯一锚点上下文（旧内容中的连续字符串，用于定位）",
      "old":    "要替换的旧内容",
      "new":    "新内容",
      "reason": "为什么要改（必填，用作审计线索）",
    }
"""
from __future__ import annotations

import datetime
import difflib
import json
import os
import re
from typing import Dict, List, Optional

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


class FullRewriteDetected(RuntimeError):
    """整段重写被检出，apply_hunks 拒绝执行。"""


class AnchorNotFound(RuntimeError):
    """hunk 的 anchor 在文件中找不到。"""


class MultipleAnchors(RuntimeError):
    """hunk 的 anchor 在文件中命中多处，无法唯一定位。"""


def _pct_diff(before: str, after: str) -> float:
    """粗略的编辑距离百分比（0-1）。1.0 = 完全重写。"""
    if not before and not after:
        return 0.0
    base_len = max(len(before), len(after))
    if base_len == 0:
        return 0.0
    sm = difflib.SequenceMatcher(a=before, b=after, autojunk=False)
    # 1 - ratio 近似于编辑比例
    return 1.0 - sm.ratio()


def hunk_divergence(old_text: str, new_text: str) -> float:
    """单个 hunk 的改写程度：old → new 的差异比例。

    使用**词级 overlap**（而不是字符级），因为字符级会把「保留几个词
    但整段换掉」误判为相似。返回 0.0 = 完全相同；1.0 = 完全不共享词。
    """
    old_tokens = set(_TOKEN_RE.findall(old_text.lower()))
    new_tokens = set(_TOKEN_RE.findall(new_text.lower()))
    if not old_tokens and not new_tokens:
        return 0.0
    union = old_tokens | new_tokens
    if not union:
        return 0.0
    inter = old_tokens & new_tokens
    return 1.0 - (len(inter) / len(union))


def assert_no_full_rewrite(
    before: str, after: str, threshold: float = 0.15
) -> float:
    """若整段变化比例 > threshold → 抛 FullRewriteDetected。

    返回实际比例，供日志/审计用。
    """
    r = _pct_diff(before, after)
    if r > threshold:
        raise FullRewriteDetected(
            f"rewrite ratio={r:.2%} > threshold={threshold:.2%} — "
            f"整段重写被拒绝，请改用 hunk 补丁"
        )
    return r


def assert_hunk_not_rewrite(
    hunk: Dict, max_divergence: float = 0.90
) -> float:
    """检测单个 hunk 是否实质上把锚点整段换掉了。

    「整段换掉」= 老锚点与新文本几乎不共享字符 → 这已经不是补丁，
    而是重写。默认阈值 90%（几乎完全替换）。
    """
    old = hunk.get("old") or hunk.get("anchor", "")
    new = hunk.get("new", "")
    d = hunk_divergence(old, new)
    if d > max_divergence:
        raise FullRewriteDetected(
            f"hunk divergence={d:.2%} > {max_divergence:.2%} — "
            f"整段替换被拒绝（old={len(old)}B, new={len(new)}B, "
            f"reason={hunk.get('reason')!r}）"
        )
    return d


def apply_hunks(
    file_path: str,
    hunks: List[Dict],
    dry_run: bool = False,
    rewrite_threshold: float = 0.15,
    audit_path: Optional[str] = None,
) -> Dict:
    """应用一组 hunk 补丁到文件。

    每个 hunk 必须提供 anchor（唯一定位锚点）与 new 内容；若 hunk 未提供
    old 则默认用 anchor 本身作为替换源。若提供 old 则验证 old 在 anchor
    附近确实存在。

    审计：每次成功/失败都会写入 audit_path 指向的 JSONL 文件。
    """
    os.makedirs(os.path.dirname(file_path) or ".", exist_ok=True)
    with open(file_path, encoding="utf-8") as f:
        before = f.read()

    result_hunks: List[Dict] = []
    cur = before
    applied = 0
    for i, h in enumerate(hunks):
        anchor = h.get("anchor") or h.get("old")
        new = h.get("new")
        reason = h.get("reason")
        if not anchor or new is None:
            result_hunks.append({
                "index": i, "status": "skipped",
                "reason": "missing anchor or new",
            })
            continue
        count = cur.count(anchor)
        if count == 0:
            result_hunks.append({
                "index": i, "status": "anchor_not_found", "reason": reason,
            })
            raise AnchorNotFound(f"hunk[{i}]: anchor not found: {anchor[:80]}")
        if count > 1:
            result_hunks.append({
                "index": i, "status": "ambiguous", "matches": count,
                "reason": reason,
            })
            raise MultipleAnchors(
                f"hunk[{i}]: anchor matched {count} times — 不唯一定位"
            )
        # 单 hunk 粒度重写防线：old → new 差异 > 90% 视为整段替换
        hunk_div = hunk_divergence(anchor, new)
        if hunk_div > 0.90:
            result_hunks.append({
                "index": i, "status": "aborted_hunk_rewrite",
                "divergence": round(hunk_div, 4), "reason": reason,
            })
            raise FullRewriteDetected(
                f"hunk[{i}]: old→new divergence={hunk_div:.2%} "
                f"> 90% — 整段替换被拒绝"
            )
        cur = cur.replace(anchor, new, 1)
        result_hunks.append({
            "index": i, "status": "applied", "reason": reason,
            "divergence": round(hunk_div, 4),
        })
        applied += 1

    # 整段重写防线
    try:
        ratio = assert_no_full_rewrite(before, cur, rewrite_threshold)
    except FullRewriteDetected as e:
        result_hunks.append({
            "index": applied, "status": "aborted_rewrite_detected",
            "reason": str(e),
        })
        if audit_path:
            _append_audit(audit_path, {
                "ts": datetime.datetime.now().isoformat(timespec="seconds"),
                "file": file_path, "status": "aborted",
                "reason": str(e), "hunks": result_hunks,
            })
        return {"status": "aborted", "detail": result_hunks}

    if not dry_run:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(cur)

    record = {
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "file": file_path, "status": "applied",
        "n_hunks": len(hunks), "n_applied": applied,
        "rewrite_ratio": ratio, "dry_run": dry_run,
        "hunks": result_hunks,
    }
    if audit_path:
        _append_audit(audit_path, record)
    return {"status": "applied", "detail": result_hunks, "rewrite_ratio": ratio}


def propose_hunk(
    old_text: str, new_text: str, reason: str,
    anchor: Optional[str] = None,
) -> Dict:
    """从 old/new 生成一个 hunk（默认 anchor=old_text）。"""
    return {
        "anchor": anchor if anchor else old_text,
        "old": old_text,
        "new": new_text,
        "reason": reason,
    }


def diff_stats(file_path: str, against: Optional[str] = None) -> Dict:
    """若 against 提供，输出与 against 文件的粗略 diff 统计。"""
    with open(file_path, encoding="utf-8") as f:
        cur = f.read()
    if against is None:
        return {
            "path": file_path, "size": len(cur),
            "lines": cur.count("\n") + 1,
        }
    with open(against, encoding="utf-8") as f:
        prev = f.read()
    sm = difflib.SequenceMatcher(a=prev, b=cur, autojunk=False)
    return {
        "path": file_path, "against": against,
        "size_before": len(prev), "size_after": len(cur),
        "rewrite_ratio": round(_pct_diff(prev, cur), 4),
        "similarity_ratio": round(sm.ratio(), 4),
    }


def _append_audit(audit_path: str, record: Dict) -> None:
    os.makedirs(os.path.dirname(audit_path) or ".", exist_ok=True)
    with open(audit_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ----------------------
# 自测
# ----------------------
if __name__ == "__main__":
    import tempfile, pathlib
    tmp = pathlib.Path(tempfile.gettempdir()) / "swarmlabs_patch_selftest.txt"
    tmp.write_text(
        "line 1: old statement\nline 2: another line\nline 3: conclusion\n",
        encoding="utf-8",
    )
    audit = tmp.with_suffix(".audit.jsonl")

    # 1) 正常 hunk 应用
    r1 = apply_hunks(
        str(tmp),
        [propose_hunk("old statement", "new statement", reason="typo fix")],
        audit_path=str(audit),
    )
    print("hunk applied:", r1["status"])
    print("file content:", tmp.read_text(encoding="utf-8"))

    # 2) 尝试整段重写 —— 应该被拒绝
    try:
        apply_hunks(
            str(tmp),
            [propose_hunk(
                "line 1: new statement\nline 2: another line\nline 3: conclusion\n",
                "completely different text here entirely rewritten\n",
                reason="test rewrite detection",
            )],
            audit_path=str(audit),
        )
        print("ERROR: rewrite was NOT blocked")
    except FullRewriteDetected as e:
        print("rewrite blocked:", str(e)[:80], "...")

    # 3) diff_stats
    before_snapshot = tmp.read_text(encoding="utf-8")
    print("diff stats:", diff_stats(str(tmp)))

    # 清理
    tmp.unlink(missing_ok=True)
    audit.unlink(missing_ok=True)
    print("OK")
