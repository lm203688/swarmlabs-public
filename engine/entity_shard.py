"""
entity_shard.py — 实体存储分片模块（hash-bucket 分片）

解决：entity_store.json 19.5MB 单文件，加载慢、CF Workers 内存受限。

实际数据结构（entity_store.json）：
  works:    {openalex_work_id: {id, title, year}} — 21,115 条，无 domain 字段
  concepts: {openalex_concept_id: {id, name, field, n}} — 16,074 条
  references: {work_id: citation_count} — 64,855 条
  insights: {trends/research_gaps/cross_domain_bridges/coauthor_network_insights: [...]}

分片策略（按类型 + hash-bucket）：
  entity_shards/works_00.json  ~ entity_shards/works_49.json  （50 桶，每桶 ~400 条）
  entity_shards/concepts.json  （16K 概念）
  entity_shards/insights.json  （洞察）
  entity_shards/references.json（引用计数）
  entity_shards/_manifest.json
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

__all__ = [
    "DEFAULT_STORE",
    "SHARD_DIR",
    "NUM_WORK_BUCKETS",
    "load_full",
    "load_works_by_bucket",
    "load_concepts",
    "load_insights",
    "get_bucket_list",
    "split_store",
    "shard_summary",
    "search_all",
    "get_total_counts",
]

DEFAULT_STORE = Path(__file__).resolve().parent.parent / "data" / "entity_store.json"
SHARD_DIR = Path(__file__).resolve().parent.parent / "data" / "entity_shards"
NUM_WORK_BUCKETS = 50


# ── 核心 API ─────────────────────────────────────────────────────────

def load_full() -> dict:
    """加载完整 entity_store.json（兼容旧接口）。"""
    path = DEFAULT_STORE
    if not path.exists():
        return {"insights": {}, "works": {}}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_works_by_bucket(bucket: int) -> list[dict]:
    """按需加载 works 的指定 hash 桶（0 ~ NUM_WORK_BUCKETS-1）。"""
    shard_dir = _ensure_shards()
    fname = f"works_{bucket:02d}.json"
    fp = shard_dir / fname
    if fp.exists():
        with open(fp, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def load_concepts() -> list[dict]:
    """加载所有概念。"""
    shard_dir = _ensure_shards()
    fp = shard_dir / "concepts.json"
    if fp.exists():
        with open(fp, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def load_insights() -> dict:
    """加载洞察数据。"""
    shard_dir = _ensure_shards()
    fp = shard_dir / "insights.json"
    if fp.exists():
        with open(fp, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def get_bucket_list() -> list[dict]:
    """获取分片文件列表及统计。"""
    shard_dir = _ensure_shards()
    buckets = []
    for fp in sorted(shard_dir.glob("*.json")):
        if fp.name == "_manifest.json":
            continue
        size = fp.stat().st_size
        try:
            with open(fp, "r", encoding="utf-8") as f:
                data = json.load(f)
            count = len(data) if isinstance(data, list) else len(data.get("items", []))
        except Exception:
            count = 0
        buckets.append({"file": fp.name, "count": count, "size_kb": round(size / 1024, 1)})
    return buckets


def split_store(source: Path | None = None, n_buckets: int = NUM_WORK_BUCKETS) -> dict:
    """
    将 entity_store.json 分片为 data/entity_shards/。

    策略：
      - works 按 id hash 分入 n_buckets 个桶
      - concepts / insights / references 各存一个文件

    返回 manifest。
    """
    src = source or DEFAULT_STORE
    shard_dir = SHARD_DIR
    shard_dir.mkdir(parents=True, exist_ok=True)

    # 清理旧分片
    for fp in shard_dir.glob("*.json"):
        fp.unlink()

    if not src.exists():
        return {"error": f"源文件不存在: {src}"}

    with open(src, "r", encoding="utf-8") as f:
        store = json.load(f)

    works = store.get("works", {})
    concepts = store.get("concepts", {})
    insights = store.get("insights", {})
    references = store.get("references", {})
    authors = store.get("authors", {})
    institutions = store.get("institutions", {})

    # works → hash-bucket 分片
    buckets: dict[int, list] = {i: [] for i in range(n_buckets)}
    if isinstance(works, dict):
        for wid, entity in works.items():
            bid = int(hashlib.md5(str(wid).encode()).hexdigest(), 16) % n_buckets
            buckets[bid].append({"id": wid, **(entity if isinstance(entity, dict) else {"data": entity})})

    for bid, items in buckets.items():
        fp = shard_dir / f"works_{bid:02d}.json"
        with open(fp, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False)

    # concepts → 单文件
    if isinstance(concepts, dict):
        c_list = [{"id": cid, **v} for cid, v in concepts.items() if isinstance(v, dict)]
    elif isinstance(concepts, list):
        c_list = concepts
    else:
        c_list = []
    with open(shard_dir / "concepts.json", "w", encoding="utf-8") as f:
        json.dump(c_list, f, ensure_ascii=False)

    # insights → 单文件
    with open(shard_dir / "insights.json", "w", encoding="utf-8") as f:
        json.dump(insights, f, ensure_ascii=False)

    # references → 单文件
    with open(shard_dir / "references.json", "w", encoding="utf-8") as f:
        json.dump(references if isinstance(references, dict) else {}, f, ensure_ascii=False)

    # authors / institutions → 单文件
    with open(shard_dir / "authors.json", "w", encoding="utf-8") as f:
        json.dump(authors if isinstance(authors, dict) else {}, f, ensure_ascii=False)
    with open(shard_dir / "institutions.json", "w", encoding="utf-8") as f:
        json.dump(institutions if isinstance(institutions, dict) else {}, f, ensure_ascii=False)

    # manifest
    manifest = {
        "source": str(src),
        "total_works": sum(len(v) for v in buckets.values()),
        "total_concepts": len(c_list),
        "total_references": len(references) if isinstance(references, dict) else 0,
        "total_insights": len(insights) if isinstance(insights, dict) else 0,
        "work_buckets": n_buckets,
        "generated_at": __import__("datetime").datetime.now().isoformat(),
    }
    with open(shard_dir / "_manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    return manifest


def shard_summary() -> dict:
    """获取分片摘要（仅读 manifest）。"""
    manifest_path = SHARD_DIR / "_manifest.json"
    if manifest_path.exists():
        with open(manifest_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"error": "未分片"}


def get_total_counts() -> dict:
    """获取各类型实体计数（从分片统计）。"""
    shard_dir = _ensure_shards()
    work_count = 0
    for fp in shard_dir.glob("works_*.json"):
        try:
            with open(fp, "r", encoding="utf-8") as f:
                work_count += len(json.load(f))
        except Exception:
            pass  # noqa: silent-ok — Exception 可安全忽略（已审计 2026-10-08）
    concepts_count = 0
    fp = shard_dir / "concepts.json"
    if fp.exists():
        with open(fp, "r", encoding="utf-8") as f:
            concepts_count = len(json.load(f))
    return {"works": work_count, "concepts": concepts_count}


def search_all(query: str, limit: int = 50) -> list[dict]:
    """
    在所有分片中搜索关键词。
    返回 [{'source': 'works_03', 'id': '...', 'title': '...', ...}, ...]
    """
    shard_dir = _ensure_shards()
    results = []
    query_lower = query.lower()

    for fp in shard_dir.glob("*.json"):
        if fp.name == "_manifest.json":
            continue
        try:
            with open(fp, "r", encoding="utf-8") as f:
                data = json.load(f)
            items = data if isinstance(data, list) else [data]
            for item in items:
                if isinstance(item, dict):
                    text = json.dumps(item, ensure_ascii=False).lower()
                    if query_lower in text:
                        results.append({"source": fp.stem, **item})
                        if len(results) >= limit:
                            return results
        except Exception:
            continue
    return results


# ── 内部 ─────────────────────────────────────────────────────────────

def _ensure_shards() -> Path:
    SHARD_DIR.mkdir(parents=True, exist_ok=True)
    if not (SHARD_DIR / "_manifest.json").exists():
        split_store()
    return SHARD_DIR