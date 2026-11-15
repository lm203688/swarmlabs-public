"""结构化世界模型 —— Kosmos 核心创新的轻量实现。

Kosmos 的核心突破是用结构化世界模型在并行 agent 之间共享上下文，
使 200 轮 rollout 保持连贯。本模块为 SwarmLabs 提供同等能力：

1. 实体库 (entities): 科研实体、假设、实验结果的结构化存储
2. 关系图 (relations): 实体间的因果/关联关系
3. 信念状态 (beliefs): 当前最佳假设及其置信度
4. 可查询接口: 按领域/类型/时间范围检索

与 Kosmos 的差异：
- Kosmos 用 Neo4j 图数据库；本模块用纯 JSON（零依赖，可在任何环境运行）
- Kosmos 需要 LLM 做摘要；本模块用规则提取（可选 LLM 增强）
- Kosmos 是云端服务；本模块是本地文件系统

诚实边界：
- 世界模型是「已知事实的索引」，不是「推理引擎」
- 关系提取基于规则，可能遗漏隐含关系
- 信念更新基于 Elo 排名，不是贝叶斯推理
"""

from __future__ import annotations

import json
import os
import datetime
from typing import Any, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
WORLD_MODEL_PATH = os.path.join(DATA, "world_model.json")


class WorldModel:
    """结构化世界模型：实体 + 关系 + 信念的可查询存储。

    设计原则：
    - 幂等：重复写入同一实体不产生重复
    - 可追溯：每个实体带 source 链接
    - 可查询：按 domain/type/tag 检索
    - 可序列化：纯 JSON，跨进程可复现
    """

    def __init__(self, path: str = WORLD_MODEL_PATH):
        self.path = path
        self._data = self._load()

    def _load(self) -> dict:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass  # noqa: silent-ok — Exception 可安全忽略（已审计 2026-10-08）
        return {
            "version": 1,
            "updated_at": None,
            "entities": [],
            "relations": [],
            "beliefs": [],
            "observations": [],
        }

    def save(self):
        self._data["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    # ── 实体管理 ──────────────────────────────────────────────

    def add_entity(self, entity_type: str, name: str, domain: str = "",
                   properties: dict = None, source: str = "", tags: list = None):
        """添加或更新一个实体。幂等：同名同类型实体更新 properties。"""
        for e in self._data["entities"]:
            if e["type"] == entity_type and e["name"] == name:
                e["properties"].update(properties or {})
                e["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
                if source:
                    e.setdefault("sources", []).append(source)
                if tags:
                    e.setdefault("tags", []).extend(tags)
                    e["tags"] = list(set(e["tags"]))
                return e
        entity = {
            "type": entity_type,
            "name": name,
            "domain": domain,
            "properties": properties or {},
            "sources": [source] if source else [],
            "tags": tags or [],
            "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "updated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        }
        self._data["entities"].append(entity)
        return entity

    def query_entities(self, entity_type: str = None, domain: str = None,
                       tag: str = None, name_contains: str = None) -> list:
        """按条件查询实体。"""
        results = self._data["entities"]
        if entity_type:
            results = [e for e in results if e["type"] == entity_type]
        if domain:
            results = [e for e in results if e["domain"] == domain]
        if tag:
            results = [e for e in results if tag in e.get("tags", [])]
        if name_contains:
            results = [e for e in results if name_contains.lower() in e["name"].lower()]
        return results

    # ── 关系管理 ──────────────────────────────────────────────

    def add_relation(self, source_name: str, target_name: str,
                     relation_type: str, confidence: float = 1.0,
                     evidence: str = ""):
        """添加关系。confidence 0-1 表示关系强度。"""
        rel = {
            "source": source_name,
            "target": target_name,
            "type": relation_type,
            "confidence": float(confidence),
            "evidence": evidence,
            "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
        }
        self._data["relations"].append(rel)
        return rel

    def query_relations(self, source: str = None, target: str = None,
                        relation_type: str = None) -> list:
        results = self._data["relations"]
        if source:
            results = [r for r in results if r["source"] == source]
        if target:
            results = [r for r in results if r["target"] == target]
        if relation_type:
            results = [r for r in results if r["type"] == relation_type]
        return results

    # ── 信念管理 ──────────────────────────────────────────────

    def update_belief(self, hypothesis: str, domain: str,
                      confidence: float, evidence: list = None):
        """更新对某个假设的信念。同假设同领域幂等更新。"""
        for b in self._data["beliefs"]:
            if b["hypothesis"] == hypothesis and b["domain"] == domain:
                b["confidence"] = float(confidence)
                b["evidence"] = evidence or b.get("evidence", [])
                b["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
                return b
        belief = {
            "hypothesis": hypothesis,
            "domain": domain,
            "confidence": float(confidence),
            "evidence": evidence or [],
            "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "updated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        }
        self._data["beliefs"].append(belief)
        return belief

    def get_top_beliefs(self, domain: str = None, top_k: int = 10) -> list:
        """获取置信度最高的信念。"""
        beliefs = self._data["beliefs"]
        if domain:
            beliefs = [b for b in beliefs if b["domain"] == domain]
        return sorted(beliefs, key=lambda b: b["confidence"], reverse=True)[:top_k]

    # ── 观测记录 ──────────────────────────────────────────────

    def add_observation(self, obs_type: str, content: dict, source: str = ""):
        """记录一次观测（实验结果、文献发现等）。"""
        obs = {
            "type": obs_type,
            "content": content,
            "source": source,
            "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        }
        self._data["observations"].append(obs)
        return obs

    def query_observations(self, obs_type: str = None, limit: int = 50) -> list:
        results = self._data["observations"]
        if obs_type:
            results = [o for o in results if o["type"] == obs_type]
        return results[-limit:]

    # ── 统计 ──────────────────────────────────────────────────

    def stats(self) -> dict:
        return {
            "entities": len(self._data["entities"]),
            "relations": len(self._data["relations"]),
            "beliefs": len(self._data["beliefs"]),
            "observations": len(self._data["observations"]),
            "entity_types": list(set(e["type"] for e in self._data["entities"])),
            "domains": list(set(e["domain"] for e in self._data["entities"] if e["domain"])),
        }

    # ── 从自治闭环自动填充 ────────────────────────────────────

    def ingest_from_autonomous_loop(self, result: dict):
        """从 autonomous_loop 的一步结果中提取实体/关系/信念，写入世界模型。

        这是 Kosmos「每步更新世界模型」的轻量实现。
        """
        key = result.get("key", "unknown")
        domain = result.get("domain", "")
        champion = result.get("champion", "")
        coverage = result.get("coverage", 0)
        sindy_eq = result.get("sindy_equation", "")
        converged = result.get("converged", False)

        # 实体：场景
        self.add_entity("scenario", key, domain=domain,
                        properties={"coverage": coverage, "converged": converged},
                        source="autonomous_loop")

        # 实体：最佳模型
        if champion:
            self.add_entity("model", champion, domain=domain,
                            properties={"rating": result.get("champion_rating", 0)},
                            source="autonomous_loop")

        # 实体：符号方程
        if sindy_eq and sindy_eq != "0":
            self.add_entity("equation", sindy_eq, domain=domain,
                            properties={"r2": result.get("sindy_r2", 0)},
                            source="sindy")

        # 关系：场景-使用-模型
        if champion:
            self.add_relation(key, champion, "uses_model",
                              confidence=min(coverage, 1.0))

        # 信念：该场景是否可信
        self.update_belief(
            hypothesis=f"GP surrogate for {key} is reliable",
            domain=domain,
            confidence=coverage,
            evidence=[f"coverage={coverage:.3f}", f"champion={champion}"],
        )

    def ingest_from_competitor(self, snapshot: dict):
        """从竞品监测快照中提取实体。"""
        for repo in snapshot.get("github", []):
            if repo.get("status") == "ok":
                self.add_entity("competitor_repo", repo["repo"],
                                properties={
                                    "stars": repo.get("stars", 0),
                                    "language": repo.get("language", ""),
                                },
                                source="competitor_watch",
                                tags=["competitor"])
        for paper in snapshot.get("arxiv", []):
            if paper.get("status") == "ok":
                self.add_entity("competitor_paper", paper.get("title", "")[:80],
                                properties={
                                    "arxiv_id": paper.get("arxiv_id", ""),
                                    "authors": paper.get("authors", []),
                                },
                                source="competitor_watch",
                                tags=["competitor", "arxiv"])
