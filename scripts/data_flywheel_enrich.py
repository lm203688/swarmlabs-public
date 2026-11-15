"""数据飞轮 enrichment（猫眼 P1）—— 把公开趋势/研究空白情报补进 47k 实体库。

边界（重要，不可逾越）：
  - 仅用**公开免费页**（基金查询、热点趋势等）；不破付费墙、不登录、不抓私有数据。
  - 遵守目标站点的 robots / ToS；本脚本不内置任何绕过访问控制的手段。
  - 本文件是**管线能力**实现 + 演示样例；生产接入需自行合规 fetch 公开页。

管线：
  TrendSource.fetch()        → 原始公开条目（列表 of dict）
  extract_insights(entries)  → 归一化为 insights 实体（research_gaps/trends/cross_domain_bridges）
  merge_into_insights(store, new, apply=False) → 去重合并；默认只产出 delta 不改动大库

用法：
  python scripts/data_flywheel_enrich.py            # 干跑：产出 delta + 统计，不改大库
  python scripts/data_flywheel_enrich.py --apply   # 实际合并进 data/entity_store.json（先备份）
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORE = os.path.join(ROOT, "data", "entity_store.json")
SAMPLE = os.path.join(ROOT, "data", "maoyan_public_sample.json")
DELTA = os.path.join(ROOT, "data", "research_gap_enrichments.json")

TYPE_TO_KEY = {
    "research_gap": "research_gaps",
    "trend": "trends",
    "cross_domain_bridge": "cross_domain_bridges",
}


def slug(s: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in s.lower())[:48]


# --------------------------------------------------------------------------
# 数据源（公开、合规边界内）
# --------------------------------------------------------------------------
class TrendSource:
    name = "abstract"
    tos_note = ""

    def fetch(self):
        raise NotImplementedError


class MaoyanPublicSource(TrendSource):
    name = "maoyan_public"
    tos_note = ("仅监测猫眼课题宝公开免费页（基金查询/热点趋势）；不破付费墙、不登录、不抓私有数据；"
                "遵守其 robots/ToS。生产 fetch 需自行合规接入公开页。")

    def __init__(self, seed_path: str = SAMPLE):
        self.seed_path = seed_path

    def fetch(self):
        """返回原始公开条目列表。生产环境替换为合规抓取；此处读本地演示种子。"""
        with open(self.seed_path, "r", encoding="utf-8") as f:
            doc = json.load(f)
        return doc.get("entries", [])


# --------------------------------------------------------------------------
# 归一化 + 合并
# --------------------------------------------------------------------------
def extract_insights(entries, source_name: str = "maoyan_public"):
    """把原始条目归一化为 insights 实体。signal 越大 → 置信度/权重越高。"""
    now = datetime.now(timezone.utc).isoformat()
    out = {k: [] for k in TYPE_TO_KEY.values()}
    for e in entries:
        t = e.get("type")
        if t not in TYPE_TO_KEY:
            continue
        title = e.get("title", "").strip()
        if not title:
            continue
        domain = e.get("domain", "")
        signal = int(e.get("signal", 1))
        ent = {
            "id": f"{source_name}:{t}:{slug(title)}",
            "name": title,
            "field": domain,
            "n": signal,
            "source": source_name,
            "retrieved_at": now,
            "confidence": round(min(0.95, 0.5 + 0.05 * signal), 2),
        }
        out[TYPE_TO_KEY[t]].append(ent)
    return out


def merge_into_insights(store, new_entities, apply: bool = False):
    """去重合并到 store['insights']。返回统计。"""
    ins = store.setdefault("insights", {})
    stats = {"added": 0, "skipped_dup": 0, "by_type": {}}
    for key, ents in new_entities.items():
        bucket = ins.setdefault(key, [])
        seen = {e.get("id") for e in bucket if isinstance(e, dict)}
        for ent in ents:
            if ent["id"] in seen:
                stats["skipped_dup"] += 1
                continue
            bucket.append(ent)
            seen.add(ent["id"])
            stats["added"] += 1
            stats["by_type"][key] = stats["by_type"].get(key, 0) + 1
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="实际合并进 entity_store.json（先备份）")
    ap.add_argument("--seed", default=SAMPLE, help="公开趋势种子文件路径")
    args = ap.parse_args()

    src = MaoyanPublicSource(seed_path=args.seed)
    print(f"[source] {src.name}  ToS: {src.tos_note[:40]}...")
    entries = src.fetch()
    print(f"[fetch] 原始条目 {len(entries)} 条")

    new_ents = extract_insights(entries, source_name=src.name)
    tot = sum(len(v) for v in new_ents.values())
    print(f"[extract] 归一化实体 {tot} 条: " +
          ", ".join(f"{k}={len(v)}" for k, v in new_ents.items()))

    store = json.load(open(STORE, "r", encoding="utf-8"))
    stats = merge_into_insights(store, new_ents, apply=args.apply)
    print(f"[merge] 新增 {stats['added']} / 跳过重复 {stats['skipped_dup']}  "
          f"/ 按类型 {stats['by_type']}")

    if args.apply:
        bak = STORE + ".bak"
        import shutil
        shutil.copy2(STORE, bak)
        json.dump(store, open(STORE, "w", encoding="utf-8"), ensure_ascii=False)
        print(f"[apply] 已写回 {STORE}（备份 {bak}）；" +
              f"insights 当前 counts: " +
              ", ".join(f"{k}={len(store['insights'].get(k, []))}" for k in TYPE_TO_KEY.values()))
    else:
        # 干跑：只写 delta，不改大库
        delta = {"_note": "数据飞轮 enrichment 增量（干跑未并入大库；用 --apply 并入）",
                 "source": src.name, "generated_at": datetime.now(timezone.utc).isoformat(),
                 "stats": stats, "entities": new_ents}
        json.dump(delta, open(DELTA, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"[dry-run] 未改动大库；增量已写 {DELTA}")


if __name__ == "__main__":
    main()
