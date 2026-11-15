#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Paper submission zip freshness check (L-07, 2026-10-17).

背景（2026-10-17 查缺补漏报告 L-07）：
- paper-1-applied-intelligence 的投稿 zip 停在 22 天前（2026-09-24），
  而 paper.tex 在 2026-10-16 有过重大修订（73/82 三分母、L411 不变量断言、
  文献 18→33）。zip 内仅 17KB vs 当前 45KB tex，如果按 zip 投稿 = 投了旧版。
- 病根是「真源不唯一」：tex 是内容真源，但 zip 是投稿真源，两者之间
  没有强制同步机制。

本脚本的作用：
- 每次 CI 跑一次，检查 zip 的 mtime ≥ tex 的 mtime（zip 至少不比 tex 旧）；
- 检查 zip 内 paper.tex 的 sha256 == 磁盘 paper.tex 的 sha256（防"重打包但
  打包的不是最新 tex"这种更隐蔽的漂移）；
- 检查 _MANIFEST.txt 里记录的 paper.tex_bytes / paper.tex_sha256 与现状一致。

用法：
    python scripts/check_paper_zip_freshness.py              # 默认检查 paper-1
    python scripts/check_paper_zip_freshness.py --paper-2    # paper-2 若有 zip
    python scripts/check_paper_zip_freshness.py --rebuild    # 发现漂移即重打（本地用）

失败即 exit 1（CI fail-closed）；本地 `--rebuild` 才允许静默重建。
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import os
import pathlib
import re
import sys
import zipfile


PAPER_DIRS = {
    "paper-1": ("outputs/paper-1-applied-intelligence", "applied_intelligence_submission.zip"),
    # paper-2 已于 2026-10-24 改投 IEEE T-Cyber（journal 形态），
    # 投稿 zip 真源是 ieee-t-cyber/ieee_tc_submission.zip；
    # 旧的 negative_results_submission.zip 命名已废弃（历史 _archive）。
    "paper-2": ("outputs/paper-2/ieee-t-cyber", "ieee_tc_submission.zip"),
}


def _sha256(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


#: 投稿固定文件（不含图）
_BASE_FILES = ["paper.tex", "references.bib", "cover_letter.txt", "highlights.txt"]


def _includegraphics_targets(tex: str) -> list[str]:
    """从 paper.tex 提取 \\includegraphics 引用的相对路径（去保序）。

    A-1（2026-10-18）：paper.tex 插入 3 张图后，zip 若不含 figures/ 目录，
    编辑部编译直接失败——zip 是投稿真源，必须 fail-closed 校验图存在。
    """
    targets: list[str] = []
    for m in re.finditer(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}", tex):
        t = m.group(1).strip()
        if t and t not in targets:
            targets.append(t)
    return targets


def _submission_files(subdir: pathlib.Path) -> list[str]:
    """投稿文件清单 = 基础文件 + paper.tex 实际引用的图。"""
    files = list(_BASE_FILES)
    tex_path = subdir / "paper.tex"
    if tex_path.exists():
        for t in _includegraphics_targets(tex_path.read_text(encoding="utf-8")):
            if (subdir / t).exists() and t not in files:
                files.append(t)
    return files


def _build_zip(subdir: pathlib.Path, zip_name: str, files: list[str]) -> int:
    """Rebuild the submission zip with a fresh manifest."""
    out = subdir / zip_name
    tex_path = subdir / "paper.tex"
    tex_bytes = tex_path.read_bytes() if tex_path.exists() else b""
    tex_sha = hashlib.sha256(tex_bytes).hexdigest() if tex_bytes else ""
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in files:
            p = subdir / f
            if p.exists():
                z.write(p, arcname=f)
        note = (
            f"SUBMISSION_MANIFEST\n"
            f"built_at_utc: {datetime.datetime.now(datetime.timezone.utc).isoformat()}\n"
            f"paper.tex_sha256: {tex_sha}\n"
            f"paper.tex_bytes: {len(tex_bytes)}\n"
            f"files: {', '.join(files)}\n"
        )
        z.writestr("_MANIFEST.txt", note)
    return os.path.getsize(out)


def check_one(paper_key: str, rebuild: bool = False) -> tuple[bool, list[str]]:
    """Check one paper's submission zip. Returns (ok, error_msgs)."""
    subdir_str, zip_name = PAPER_DIRS[paper_key]
    subdir = pathlib.Path(subdir_str)
    zip_path = subdir / zip_name
    errors: list[str] = []

    if not zip_path.exists():
        if rebuild:
            files = _submission_files(subdir)
            size = _build_zip(subdir, zip_name, files)
            return True, [f"{paper_key}: rebuilt ({size} bytes)"]
        errors.append(f"missing: {zip_path}")
        return False, errors

    if not subdir.exists():
        errors.append(f"missing dir: {subdir}")
        return False, errors

    tex_path = subdir / "paper.tex"
    if not tex_path.exists():
        errors.append(f"missing paper.tex: {tex_path}")
        return False, errors

    # 1) zip mtime >= tex mtime（允许同秒；不允许 zip 比 tex 旧）
    zip_mtime = os.path.getmtime(zip_path)
    tex_mtime = os.path.getmtime(tex_path)
    if zip_mtime < tex_mtime - 1:  # 允许 1s 抖动
        if rebuild:
            size = _build_zip(
                subdir,
                zip_name,
                _submission_files(subdir),
            )
            return True, [f"{paper_key}: REBUILT (zip was older than tex; {size} bytes)"]
        errors.append(
            f"zip mtime {datetime.datetime.fromtimestamp(zip_mtime)}"
            f" < paper.tex mtime {datetime.datetime.fromtimestamp(tex_mtime)}"
        )

    # 2) zip 内 paper.tex 的 sha256 == 磁盘 sha256
    disk_sha = _sha256(tex_path)
    try:
        with zipfile.ZipFile(zip_path) as z:
            names = z.namelist()
            if "paper.tex" not in names:
                if rebuild:
                    size = _build_zip(
                        subdir,
                        zip_name,
                        _submission_files(subdir),
                    )
                    return True, [f"{paper_key}: REBUILT (zip missing paper.tex; {size} bytes)"]
                errors.append("zip 内没有 paper.tex")
                return False, errors
            in_zip_bytes = z.read("paper.tex")
            in_zip_sha = hashlib.sha256(in_zip_bytes).hexdigest()
            # _MANIFEST.txt 校验
            if "_MANIFEST.txt" in names:
                manifest = z.read("_MANIFEST.txt").decode("utf-8", errors="replace")
            else:
                manifest = ""
    except zipfile.BadZipFile as e:
        errors.append(f"zip 已损坏：{e}")
        return False, errors

    if in_zip_sha != disk_sha:
        if rebuild:
            size = _build_zip(
                subdir,
                zip_name,
                _submission_files(subdir),
            )
            return True, [f"{paper_key}: REBUILT (sha256 mismatch; {size} bytes)"]
        errors.append(
            f"zip 内 paper.tex sha256 {in_zip_sha[:16]}… != 磁盘 {disk_sha[:16]}…（zip 内容是旧的）"
        )

    # 3) manifest 里的 sha 与磁盘一致（若 manifest 存在）
    if manifest:
        found_sha_line = False
        for line in manifest.splitlines():
            if line.startswith("paper.tex_sha256:"):
                found_sha_line = True
                declared = line.split(":", 1)[1].strip()
                if declared != disk_sha:
                    errors.append(
                        f"_MANIFEST.txt 记录的 sha256 {declared[:16]}… 与磁盘 {disk_sha[:16]}… 不一致"
                    )
                break
        if not found_sha_line:
            errors.append("_MANIFEST.txt 缺少 paper.tex_sha256 行（manifest 不完整）")

    # 4) A-1（2026-10-18）：zip 内 paper.tex 的 \\includegraphics 目标必须都在 zip 里。
    #    缺图 = 编辑部编译直接失败，而 mtime/sha 检查都看不出——必须 fail-closed。
    try:
        zip_tex = zipfile.ZipFile(zip_path).read("paper.tex").decode("utf-8", errors="replace")
        zip_names = set(zipfile.ZipFile(zip_path).namelist())
    except (zipfile.BadZipFile, KeyError):
        zip_tex, zip_names = "", set()
    for t in _includegraphics_targets(zip_tex):
        if t not in zip_names:
            if rebuild:
                size = _build_zip(
                    subdir,
                    zip_name,
                    _submission_files(subdir),
                )
                return True, [f"{paper_key}: REBUILT (zip missing figure {t}; {size} bytes)"]
            errors.append(f"zip 缺少 \\includegraphics 目标 {t}（编辑部编译会失败）")

    return len(errors) == 0, errors


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser()
    ap.add_argument("--paper-1", action="store_true", help="仅检查 paper-1")
    ap.add_argument("--paper-2", action="store_true", help="仅检查 paper-2")
    ap.add_argument("--rebuild", action="store_true", help="本地用：发现漂移即重打")
    args = ap.parse_args(argv)

    targets = ["paper-1", "paper-2"]
    if args.paper_1:
        targets = ["paper-1"]
    elif args.paper_2:
        targets = ["paper-2"]

    all_ok = True
    print("Paper submission zip freshness check (L-07)")
    print("=" * 60)
    for key in targets:
        ok, msgs = check_one(key, rebuild=args.rebuild)
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {key}")
        for m in msgs:
            print(f"    - {m}")
        if not ok:
            all_ok = False

    print()
    print("=" * 60)
    print(f"result: {'ALL PASS' if all_ok else 'FAILED'}")
    print("=" * 60)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
