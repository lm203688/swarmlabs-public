#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 engine-kit 的 vv-gate 同步进 ScienceHub 适配包（并防漂移）。

为什么需要这个脚本
------------------
ScienceHub（磐石）的 Skill 必须是一个**自包含目录**：SKILL.md + scripts/ +
references/，用户在平台里创建技能时上传的就是这个目录，平台容器里**没有**
我们的仓库。

所以客户端脚本必须**复制**一份过去。手动复制会漂移：上游修一个坑、下游还带着
旧的解释，于是同一个门禁对外给出两套行为——这正是本项目反复禁止的"两套口径"。

本脚本把「复制」变成「可校验的同步」：
    python scripts/sync_sciencehub_skill.py            # 同步 + 写 MANIFEST.json
    python scripts/sync_sciencehub_skill.py --check    # 只校验（CI 门禁，不一致 exit 2）

同步范围：**只同步可执行/参考资产**（脚本与语义文档）。`SKILL.md` 与
`references/package-and-provenance.md` 是本包**特有**的（面向平台的中文触发
与评分维度适配），不参与复制，只在 MANIFEST 里登记哈希。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

UPSTREAM = os.path.join("engine-kit", "skills", "vv-gate")
TARGET = os.path.join("packages", "sciencehub-skill", "verify-scientific-claim")

#: (上游相对路径, 包内相对路径) —— 必须逐字节同源
SYNCED = [
    ("scripts/vv_gate.py", "scripts/vv_gate.py"),
    ("references/gate-semantics.md", "references/gate-semantics.md"),
]

#: 包内特有文件（不进上游，只登记）
LOCAL = [
    "SKILL.md",
    "references/package-and-provenance.md",
]

SCHEMA = "swarmlabs-sciencehub-skill/1.0"
MANIFEST = os.path.join(TARGET, "MANIFEST.json")


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 16), b""):
            h.update(c)
    return h.hexdigest()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="verify sync only, do not write (CI gate)")
    args = ap.parse_args(argv)

    src_root = os.path.join(ROOT, UPSTREAM)
    dst_root = os.path.join(ROOT, TARGET)
    if not os.path.isdir(src_root):
        print(f"[ERROR] upstream missing: {src_root}", file=sys.stderr)
        return 1

    rows = []
    drift = []
    for rel_src, rel_dst in SYNCED:
        s = os.path.join(src_root, rel_src)
        d = os.path.join(dst_root, rel_dst)
        if not os.path.exists(s):
            print(f"[ERROR] upstream file missing: {rel_src}", file=sys.stderr)
            return 1
        s_hash = sha256(s)
        d_hash = sha256(d) if os.path.exists(d) else None
        if d_hash != s_hash:
            drift.append(rel_dst)
            if not args.check:
                os.makedirs(os.path.dirname(d), exist_ok=True)
                shutil.copyfile(s, d)
                print(f"  synced  {rel_dst}")
        else:
            print(f"  in-sync {rel_dst}")
        rows.append({
            "target": rel_dst.replace(os.sep, "/"),
            "source": f"{UPSTREAM}/{rel_src}".replace(os.sep, "/"),
            "sha256": s_hash,
            "bytes": os.path.getsize(s),
        })

    local_rows = []
    for rel in LOCAL:
        p = os.path.join(dst_root, rel)
        if not os.path.exists(p):
            print(f"[WARN] package-local file missing: {rel}", file=sys.stderr)
            continue
        local_rows.append({
            "path": rel.replace(os.sep, "/"),
            "sha256": sha256(p),
            "bytes": os.path.getsize(p),
        })

    if args.check:
        if drift:
            print(f"\nSYNC_DRIFT — package differs from upstream: {drift}",
                  file=sys.stderr)
            print("fix: python scripts/sync_sciencehub_skill.py", file=sys.stderr)
            return 2
        print("\nSYNC_OK")
        return 0

    manifest = {
        "schema": SCHEMA,
        "skill": "verify-scientific-claim",
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "platform": "ScienceHub (scienceone.ai) — Agent Skills format",
        "upstream": {"path": UPSTREAM.replace(os.sep, "/"), "spec": "agentskills.io"},
        "synced": rows,
        "package_local": local_rows,
        "drift_policy": ("run `python scripts/sync_sciencehub_skill.py --check` "
                         "in CI; synced files must stay byte-identical to upstream"),
    }
    os.makedirs(dst_root, exist_ok=True)
    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"\nWROTE {os.path.relpath(MANIFEST, ROOT)} "
          f"({len(rows)} synced, {len(local_rows)} package-local)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
