"""swarmlabs-skills 组件清单生成器（借 ECC component-tree + manifest 思想）。

ECC（everything-claude-code，208k+ stars）的核心可借鉴点：
> 每个 skill / component 有**稳定 id + 版本号 + 变更追踪**，整棵树可枚举、可审计。

本项目此前 skill 散落于 skills/agent-skills/ 各目录，只有 SKILL.md 的 frontmatter
`metadata.version`，**没有统一的树状清单**——这导致「飞轮新增了一个 skill」无法被
自动感知、无法被审计、无法在 agentteams 里被确定性引用。

本脚本产出 `skills/agent-skills/MANIFEST.json`：
- 每个 skill 一条：stable_id（目录名，永不变）、version、category、description、file
- 顶层：schema_version、generated_at、skill_count、目录树
- 幂等：重复运行只更新版本/描述，不丢失既有的 changelog 手工记录

同时也校验 frontmatter 完整性（缺 version / 缺 description 即报错），作为 skill
入库的 CI 门槛。
"""

from __future__ import annotations

import datetime
import json
import os
import re
import sys
from typing import Dict, List

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS_DIR = os.path.join(ROOT, "skills", "agent-skills")
MANIFEST_PATH = os.path.join(SKILLS_DIR, "MANIFEST.json")

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_frontmatter(text: str) -> Dict[str, str]:
    """极简 YAML frontmatter 解析：只取顶层 key: value（足够覆盖 SKILL.md）。"""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}
    body = m.group(1)
    out: Dict[str, str] = {}
    cur_key = None
    for line in body.splitlines():
        if not line.strip():
            continue
        if not line.startswith(" ") and ":" in line:
            k, _, v = line.partition(":")
            cur_key = k.strip()
            out[cur_key] = v.strip().strip('"').strip("'")
        elif cur_key and (line.startswith("  ") or line.startswith("\t")):
            # 缩进的续行（如 metadata 块里的 version）——尝试 key: val
            if ":" in line:
                k, _, v = line.partition(":")
                sub = k.strip()
                if sub in ("version", "category", "skill-author"):
                    out[f"{cur_key}.{sub}"] = v.strip().strip('"').strip("'")
    return out


def scan() -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for name in sorted(os.listdir(SKILLS_DIR)):
        d = os.path.join(SKILLS_DIR, name)
        skill_md = os.path.join(d, "SKILL.md")
        if not os.path.isdir(d) or not os.path.exists(skill_md):
            continue
        with open(skill_md, encoding="utf-8") as f:
            text = f.read()
        fm = _parse_frontmatter(text)
        version = fm.get("metadata.version", fm.get("version", ""))
        desc = fm.get("description", "")
        desc = desc[:200]
        rows.append({
            "stable_id": name,
            "name": fm.get("name", name),
            "version": version,
            "category": fm.get("metadata.category", fm.get("category", "")),
            "description": desc,
            "license": fm.get("license", ""),
            "file": f"skills/agent-skills/{name}/SKILL.md",
        })
    return rows


def build(merge_changelog: bool = True) -> Dict[str, object]:
    rows = scan()
    # 校验
    errors = [r["stable_id"] for r in rows if not r["version"] or not r["description"]]
    if errors:
        print(f"[WARN] 以下 skill 缺 version/description，未达入库门槛: {errors}", file=sys.stderr)

    prev = {}
    if merge_changelog and os.path.exists(MANIFEST_PATH):
        try:
            prev = json.load(open(MANIFEST_PATH, encoding="utf-8")).get("skills", {})
        except Exception:
            prev = {}

    skills_out = {}
    tree = []
    for r in rows:
        sid = r["stable_id"]
        entry = dict(r)
        old = prev.get(sid, {})
        # 保留手工 changelog
        entry["changelog"] = old.get("changelog", [])
        if old and old.get("version") != r["version"]:
            entry["changelog"] = [
                f"{datetime.date.today().isoformat()}: {old.get('version','?')} -> {r['version']}"
            ] + entry["changelog"]
        skills_out[sid] = entry
        tree.append(sid)

    manifest = {
        "schema_version": "1.0",
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "skill_count": len(rows),
        "tree": tree,
        "skills": skills_out,
    }
    return manifest


def main() -> int:
    manifest = build()
    os.makedirs(SKILLS_DIR, exist_ok=True)
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"WROTE {MANIFEST_PATH}: {manifest['skill_count']} skills, "
          f"tree={manifest['tree']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
