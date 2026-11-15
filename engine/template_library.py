"""Template Library — reusable V&V reasoning fingerprints.

借鉴 STELLA (bioRxiv 2025.07.01) 的推理模板库：每次成功的 V&V
评测沉淀为一条"指纹模板"，相似场景可复用，避免从零开始。

SwarmLabs 场景：82 场景 → 目标 100k+ 实体。当实体库扩展后，
相同领域的场景可以复用先前 PASS 的 harness 配置作为先验，
减少全量重算成本。

设计要点：
- 只记录 PASS 模板（MARGINAL/REFUTED 不沉淀，避免污染先验）
- 指纹 = sha1(harness.fingerprint + sorted(gate_ids))
- 模板可序列化，持久化到 data/template_library.json
- 提供 similarity 搜索（gate_ids Jaccard 相似度）
- fail-closed：加载失败返回空库而非崩溃
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Iterable, List, Optional, Tuple


_SCHEMA = "svp.template-library.v1"
Iterable_str = Iterable[str]


@dataclass
class Template:
    """一条成功的 V&V 推理模板。

    Fields:
        id: 稳定指纹（sha1）
        scenario_key: 场景标识（如 'mol_suzuki_yield'）
        domain: 领域标签（如 '化学/催化速率'）
        harness_fingerprint: 关联的 harness 指纹
        gate_ids: 使用的门禁 ID 元组
        verdict: 最终判定（本库只存 PASS）
        score: V&V 分数（0..1）
        created_at: Unix 时间戳
        reuse_count: 被复用次数
        last_used_at: 最近一次被复用的时间戳
        notes: 自由文本备注
    """

    id: str
    scenario_key: str
    harness_fingerprint: str
    gate_ids: Tuple[str, ...]
    verdict: str = "PASS"
    score: float = 1.0
    domain: str = ""
    created_at: int = field(default_factory=lambda: int(time.time()))
    reuse_count: int = 0
    last_used_at: int = 0
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["gate_ids"] = list(self.gate_ids)
        d["schema"] = _SCHEMA
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Template":
        return cls(
            id=d["id"],
            scenario_key=d["scenario_key"],
            harness_fingerprint=d.get("harness_fingerprint", ""),
            gate_ids=tuple(d.get("gate_ids", [])),
            verdict=d.get("verdict", "PASS"),
            score=float(d.get("score", 1.0)),
            domain=d.get("domain", ""),
            created_at=int(d.get("created_at", 0)),
            reuse_count=int(d.get("reuse_count", 0)),
            last_used_at=int(d.get("last_used_at", 0)),
            notes=d.get("notes", ""),
        )


def template_id(harness_fingerprint: str, gate_ids: Iterable_str) -> str:
    """生成稳定模板 ID：sha1(harness_fp + sorted(gate_ids))[:16]。

    Args:
        harness_fingerprint: harness 的稳定指纹
        gate_ids: 门禁 ID 序列

    Returns:
        16 字符 hex 指纹

    Notes:
        Iterable_str 是 Iterable[str] 的别名，见模块顶部定义。
    """
    parts = [harness_fingerprint] + sorted(gate_ids)
    return hashlib.sha1("\n".join(parts).encode("utf-8")).hexdigest()[:16]


class TemplateLibrary:
    """V&V 推理模板库。

    Usage::

        lib = TemplateLibrary()
        lib.record(scenario_key="mol_logistic",
                   harness_fingerprint=h.fingerprint(),
                   gate_ids=h.gate_ids())
        best = lib.find_best(gate_ids=("N1", "N3", "P1"))
        stats = lib.stats()
    """

    def __init__(self, templates: Optional[List[Template]] = None) -> None:
        self._templates: Dict[str, Template] = {}
        if templates:
            for t in templates:
                self._templates[t.id] = t

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------

    def record(
        self,
        scenario_key: str,
        harness_fingerprint: str,
        gate_ids: Iterable_str,
        verdict: str = "PASS",
        score: float = 1.0,
        domain: str = "",
        notes: str = "",
    ) -> Optional[Template]:
        """记录一条模板。**只接受 PASS**；其他 verdict 返回 None。

        设计理由：MARGINAL/REFUTED 是"失败经验"，应由
        `engine/antirepeat.py` 单独记录，不沉淀为可复用先验。
        """
        if verdict != "PASS":
            return None
        if not harness_fingerprint or not gate_ids:
            return None
        gid_tuple = tuple(gate_ids)
        tid = template_id(harness_fingerprint, gid_tuple)
        t = Template(
            id=tid,
            scenario_key=scenario_key,
            harness_fingerprint=harness_fingerprint,
            gate_ids=gid_tuple,
            verdict=verdict,
            score=score,
            domain=domain,
            notes=notes,
        )
        self._templates[tid] = t
        return t

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------

    def find_by_id(self, tid: str) -> Optional[Template]:
        """按 ID 精确查找。"""
        return self._templates.get(tid)

    def find_for_scenario(self, scenario_key: str) -> Optional[Template]:
        """按场景键查找（最近一次记录的模板）。"""
        for t in self._templates.values():
            if t.scenario_key == scenario_key:
                return t
        return None

    def find_best(
        self, gate_ids: Iterable_str, top_k: int = 1
    ) -> List[Template]:
        """按 gate_ids Jaccard 相似度查找 top-k 模板。

        Args:
            gate_ids: 目标门禁 ID 集合
            top_k: 返回前 k 个

        Returns:
            按相似度降序排列的模板列表
        """
        target = set(gate_ids)
        if not target:
            return []
        scored: List[Tuple[float, Template]] = []
        for t in self._templates.values():
            ts = set(t.gate_ids)
            if not ts:
                continue
            union = len(target | ts)
            inter = len(target & ts)
            jaccard = inter / union if union > 0 else 0.0
            if inter > 0:  # 至少共享 1 个 gate
                scored.append((jaccard, t))
        scored.sort(key=lambda x: (-x[0], x[1].id))
        return [t for _, t in scored[:top_k]]

    def mark_reused(self, tid: str) -> bool:
        """标记某模板被复用（reuse_count += 1）。"""
        t = self._templates.get(tid)
        if t is None:
            return False
        t.reuse_count += 1
        t.last_used_at = int(time.time())
        return True

    # ------------------------------------------------------------------
    # 统计
    # ------------------------------------------------------------------

    def stats(self) -> Dict[str, Any]:
        """返回库的统计信息。"""
        total = len(self._templates)
        if total == 0:
            return {
                "schema": _SCHEMA,
                "total": 0,
                "unique_scenarios": 0,
                "unique_domains": 0,
                "avg_score": 0.0,
                "total_reuses": 0,
            }
        scores = [t.score for t in self._templates.values()]
        reuses = [t.reuse_count for t in self._templates.values()]
        scenarios = {t.scenario_key for t in self._templates.values()}
        domains = {t.domain for t in self._templates.values() if t.domain}
        return {
            "schema": _SCHEMA,
            "total": total,
            "unique_scenarios": len(scenarios),
            "unique_domains": len(domains),
            "avg_score": sum(scores) / total,
            "min_score": min(scores),
            "max_score": max(scores),
            "total_reuses": sum(reuses),
            "most_reused": max(reuses),
        }

    def __len__(self) -> int:
        return len(self._templates)

    def __iter__(self):
        return iter(self._templates.values())

    def clear(self) -> int:
        """清空库，返回删除数量。"""
        n = len(self._templates)
        self._templates.clear()
        return n

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """序列化为可 JSON 化的字典。"""
        return {
            "schema": _SCHEMA,
            "generated_at": int(time.time()),
            "total": len(self._templates),
            "templates": [t.to_dict() for t in self._templates.values()],
        }

    def save(self, path: str) -> int:
        """保存到 JSON 文件，返回保存的模板数。"""
        parent = os.path.dirname(os.path.abspath(path))
        os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
        return len(self._templates)

    @classmethod
    def load(cls, path: str) -> "TemplateLibrary":
        """从 JSON 文件加载；文件不存在或损坏时返回空库。"""
        if not os.path.exists(path):
            return cls()
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            templates = [Template.from_dict(t) for t in d.get("templates", [])]
            return cls(templates=templates)
        except (json.JSONDecodeError, KeyError, ValueError, TypeError):
            # fail-closed：损坏的库文件不应当导致启动失败
            return cls()

    @classmethod
    def load_or_empty(cls, path: str) -> "TemplateLibrary":
        """`load` 的语义别名，明确表达"缺文件不报错"的语义。"""
        return cls.load(path)


# ---------------------------------------------------------------------------
# 便捷工厂
# ---------------------------------------------------------------------------


def default_library_path(repo_root: Optional[str] = None) -> str:
    """返回默认的库持久化路径：data/template_library.json。"""
    if repo_root is None:
        # 从当前文件位置推断 repo root
        repo_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..")
        )
    return os.path.join(repo_root, "data", "template_library.json")


def from_harness(harness: Any, scenario_key: str, domain: str = "") -> Optional[Template]:
    """从 Harness 对象构造模板（鸭子类型，不强制 import harness_evolver）。

    Args:
        harness: 有 `.fingerprint()` 和 `.gate_ids()` 方法的对象
        scenario_key: 场景键
        domain: 领域标签
    """
    try:
        fp = harness.fingerprint()
        gids = tuple(harness.gate_ids())
    except (AttributeError, TypeError):
        return None
    return Template(
        id=template_id(fp, gids),
        scenario_key=scenario_key,
        harness_fingerprint=fp,
        gate_ids=gids,
        verdict="PASS",
        score=getattr(harness, "score", 1.0),
        domain=domain,
    )


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------


def _selftest() -> int:
    """纯 stdlib self-test，不依赖外部数据。"""
    passed = 0
    total = 0

    def check(name: str, cond: bool) -> None:
        nonlocal passed, total
        total += 1
        if cond:
            passed += 1
            print(f"  PASS  {name}")
        else:
            print(f"  FAIL  {name}")

    # 1. record + find_by_id
    lib = TemplateLibrary()
    t = lib.record("mol_logistic", "fp_abc123", ("N1", "N3", "P1"), score=0.95)
    check("record returns Template", t is not None)
    check("find_by_id retrieves", lib.find_by_id(t.id) is t)

    # 2. verdict != PASS 不记录
    bad = lib.record("refuted_case", "fp_xyz", ("N1",), verdict="REFUTED")
    check("refuted not recorded", bad is None)

    # 3. find_best 按相似度
    lib2 = TemplateLibrary()
    lib2.record("s1", "fp1", ("N1", "N3", "P1"), score=1.0)
    lib2.record("s2", "fp2", ("N1", "N5"), score=0.9)
    lib2.record("s3", "fp3", ("P1", "N7", "P6"), score=0.85)
    best = lib2.find_best(("N1", "N3", "P1"), top_k=1)
    check("find_best picks exact match", best and best[0].scenario_key == "s1")

    # 4. mark_reused
    lib.mark_reused(t.id)
    check("mark_reused increments", lib.find_by_id(t.id).reuse_count == 1)
    check("mark_reused on missing returns False",
          not lib.mark_reused("nonexistent_id"))

    # 5. stats
    s = lib2.stats()
    check("stats total correct", s["total"] == 3)
    check("stats unique_scenarios", s["unique_scenarios"] == 3)
    check("stats avg_score in [0,1]", 0 <= s["avg_score"] <= 1)

    # 6. save/load round-trip
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json",
                                     delete=False, encoding="utf-8") as f:
        tmp_path = f.name
    try:
        n_saved = lib2.save(tmp_path)
        check("save returns count", n_saved == 3)
        lib3 = TemplateLibrary.load(tmp_path)
        check("load restores count", len(lib3) == 3)
        check("load preserves IDs",
              {t.id for t in lib3} == {t.id for t in lib2})
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    # 7. load missing file -> empty lib
    empty = TemplateLibrary.load("/nonexistent/path.json")
    check("load missing -> empty", len(empty) == 0)
    check("stats of empty", empty.stats()["total"] == 0)

    # 8. from_harness duck-typed
    class FakeHarness:
        def fingerprint(self): return "fp_fake"
        def gate_ids(self): return ["N1", "N3"]
        score = 0.92
    th = from_harness(FakeHarness(), "bio_test", domain="Biology")
    check("from_harness builds Template", th is not None and th.id)
    check("from_harness preserves score", th.score == 0.92)

    # 9. from_harness with broken object
    broken = from_harness(object(), "broken")
    check("from_harness on broken object returns None", broken is None)

    # 10. clear
    n = lib2.clear()
    check("clear returns count", n == 3)
    check("clear empties lib", len(lib2) == 0)

    # 11. template_id stability
    id1 = template_id("fp1", ("N1", "N3", "P1"))
    id2 = template_id("fp1", ("P1", "N1", "N3"))  # 不同顺序
    check("template_id order-independent", id1 == id2)
    id3 = template_id("fp2", ("N1", "N3", "P1"))  # 不同 fingerprint
    check("template_id differs by fp", id1 != id3)

    # 12. default_library_path
    p = default_library_path()
    check("default_library_path exists", p.endswith("template_library.json"))

    print(f"\n{passed}/{total} template library self-tests passed")
    return passed == total


if __name__ == "__main__":
    ok = _selftest()
    import sys
    sys.exit(0 if ok else 1)
