"""
Experience Store with real cross-run learning.

Provides:
- BM25 语义检索打分（2026-10-14 升级，替换原 TF-IDF + cosine similarity）
- Pattern extraction that feeds into decision-making
- Relevance-ranked recall based on task similarity
- Success/failure pattern analysis for model selection
- Dual-context conditional contrast detection (Gemma-Yale inspired)
- Condition-aware experience recording and recall

检索层升级说明（2026-10-14）
---------------------------
旧：TF-IDF 手工实现 + cosine similarity。缺点：
  - TF 是 tf/total，长度惩罚缺失；对短文本 query 和长描述匹配偏差大
  - cosine 假设向量空间几何，对稀疏词项集信号损失严重
  - 中文短语分词粒度过粗导致关键词稀疏（现存 6 条经验 keywords=[]）

新：BM25（Okapi，Robertson & Zaragoza 2009），BM25+ IDF 变体
  - 显式文档长度惩罚（k1=1.5, b=0.75 工业标准超参）
  - IDF 加 +1 平滑，避免负值
  - 分数无上界，ExperienceStore 内部做 max-normalize 到 [0, 1] 兼容
    recency_bonus / quality_score 等融合权重

依赖：仅 core/bm25.py（纯 numpy + math），无外部新依赖
可复现性：BM25 完全确定性，相同 (docs, query) 恒等分数，无模型版本漂移
"""

import json
import time
import math
import hashlib
import re
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, asdict, field
from pathlib import Path
from collections import Counter

import numpy as np

from core.bm25 import BM25, normalize_max


@dataclass
class Experience:
    id: str
    task_type: str
    model_used: str
    task_description: str
    result: Dict[str, Any]
    success: bool
    timestamp: float
    metadata: Dict[str, Any] = field(default_factory=dict)
    keywords: List[str] = field(default_factory=list)
    conditions: Dict[str, Any] = field(default_factory=dict)
    conditional_results: Dict[str, bool] = field(default_factory=dict)
    quality_score: float = 0.5
    use_count: int = 0
    last_used: float = 0.0
    success_streak: int = 0
    compound_value: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Experience':
        keywords = data.pop("keywords", [])
        conditions = data.pop("conditions", {})
        conditional_results = data.pop("conditional_results", {})
        quality_score = data.pop("quality_score", 0.5)
        use_count = data.pop("use_count", 0)
        last_used = data.pop("last_used", 0.0)
        success_streak = data.pop("success_streak", 0)
        compound_value = data.pop("compound_value", 0.0)
        return cls(**data, keywords=keywords, conditions=conditions,
                   conditional_results=conditional_results,
                   quality_score=quality_score, use_count=use_count,
                   last_used=last_used, success_streak=success_streak,
                   compound_value=compound_value)


def _tokenize(text: str) -> List[str]:
    text = text.lower()
    text = re.sub(r'[^\w\s]', ' ', text)
    tokens = text.split()
    stop = {'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been',
            'being', 'have', 'has', 'had', 'do', 'does', 'did', 'will',
            'would', 'could', 'should', 'may', 'might', 'shall', 'can',
            'to', 'of', 'in', 'for', 'on', 'with', 'at', 'by', 'from',
            'and', 'or', 'not', 'if', 'this', 'that', 'it', 'as', 'but'}
    return [t for t in tokens if len(t) > 2 and t not in stop]


class ExperienceStore:
    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(exist_ok=True)

        self.experiences_file = self.data_dir / "experiences.json"
        self.patterns_file = self.data_dir / "patterns.json"
        # Legacy file (kept for backward compatibility; new writes go to bm25_file)
        self.idf_file = self.data_dir / "experience_idf.json"
        self.bm25_file = self.data_dir / "experience_bm25.json"

        self.experiences: List[Experience] = []
        self.patterns: Dict[str, Any] = {}

        # BM25 index (replaces old self.idf / self.doc_count)
        self.bm25 = BM25()
        # Legacy public attributes (kept as read-only views so any external code
        # that pokes at `store.idf` / `store.doc_count` still works).
        self.idf: Dict[str, float] = {}
        self.doc_count: int = 0

        self._load_data()

    def _load_data(self):
        try:
            if self.experiences_file.exists() and self.experiences_file.stat().st_size > 0:
                with open(self.experiences_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self.experiences = [Experience.from_dict(exp) for exp in data.get("experiences", [])]
        except (json.JSONDecodeError, Exception):
            self.experiences = []

        try:
            if self.patterns_file.exists() and self.patterns_file.stat().st_size > 0:
                with open(self.patterns_file, 'r', encoding='utf-8') as f:
                    self.patterns = json.load(f)
                # Ensure all patterns have required fields
                for task_type, p in self.patterns.items():
                    if "model_success_rates" not in p:
                        p["model_success_rates"] = {}
                    if "common_keywords" not in p:
                        p["common_keywords"] = {}
                    if "recent_trend" not in p:
                        p["recent_trend"] = []
        except (json.JSONDecodeError, Exception):
            self.patterns = {}

        # Prefer new BM25 state; fall back to legacy idf file shape.
        # In both cases we _rebuild_index() from current experiences anyway,
        # so the loaded state is only a soft cache.
        try:
            if self.bm25_file.exists() and self.bm25_file.stat().st_size > 0:
                with open(self.bm25_file, 'r', encoding='utf-8') as f:
                    state = json.load(f)
                self.bm25 = BM25.from_dict(state)
        except (json.JSONDecodeError, ValueError, KeyError, Exception):
            self.bm25 = BM25()

        self._rebuild_index()

    def _save_data(self):
        with open(self.experiences_file, 'w', encoding='utf-8') as f:
            json.dump({
                "experiences": [exp.to_dict() for exp in self.experiences],
                "last_updated": time.time()
            }, f, indent=2, ensure_ascii=False)

        with open(self.patterns_file, 'w', encoding='utf-8') as f:
            json.dump(self.patterns, f, indent=2, ensure_ascii=False)

        with open(self.bm25_file, 'w', encoding='utf-8') as f:
            json.dump(self.bm25.to_dict(), f, indent=2, ensure_ascii=False)

        # Legacy file kept for downstream tools that may still read it.
        # Contains a compact summary — NOT the full BM25 postings.
        with open(self.idf_file, 'w', encoding='utf-8') as f:
            json.dump({
                "schema": "legacy-tfidf-shape",
                "backend": "bm25",
                "idf": {t: round(self.bm25.idf(t), 6) for t in self.bm25.df},
                "doc_count": self.bm25.N,
                "k1": self.bm25.k1,
                "b": self.bm25.b,
                "note": "idf column values are BM25+ IDF for compatibility; actual retrieval uses BM25 postings in experience_bm25.json"
            }, f, indent=2, ensure_ascii=False)

    def _rebuild_index(self):
        """Rebuild the BM25 index from current experiences.

        Tokenization strategy:
          - Prefer pre-computed `exp.keywords` (backward compatible).
          - Fall back to `_tokenize(exp.task_description)` when keywords is empty
            (fixes the historical "6 Chinese experiences with keywords=[]" bug).
        """
        docs: List[List[str]] = []
        for exp in self.experiences:
            if exp.keywords:
                docs.append(list(exp.keywords))
            else:
                docs.append(_tokenize(exp.task_description))

        self.bm25.fit(docs)
        # Keep legacy public attributes in sync so external readers don't crash.
        self.doc_count = self.bm25.N
        self.idf = {t: self.bm25.idf(t) for t in self.bm25.df}

    def _doc_tokens(self, exp: Experience) -> List[str]:
        """Token list for one experience (mirrors _rebuild_index logic)."""
        if exp.keywords:
            return list(exp.keywords)
        return _tokenize(exp.task_description)

    def _score_bm25(self, query_tokens: List[str]) -> np.ndarray:
        """
        Return normalized BM25 scores for all self.experiences, shape=(N,).
        Scores are max-normalized to [0, 1] so downstream fusion weights
        (recency_bonus, quality_score) retain their original semantics.
        """
        if not self.bm25.docs:
            return np.zeros(0, dtype=np.float64)
        raw = self.bm25.score_all(query_tokens)
        return normalize_max(raw)

    def record(self, task: Dict[str, Any], model_used: str, result: Dict[str, Any],
               metadata: Optional[Dict[str, Any]] = None,
               conditions: Optional[Dict[str, Any]] = None,
               conditional_results: Optional[Dict[str, bool]] = None) -> str:
        exp_id = hashlib.md5(
            f"{task.get('description', '')}{time.time()}".encode()
        ).hexdigest()

        description = task.get("description", "")
        keywords = _tokenize(description)

        experience = Experience(
            id=exp_id,
            task_type=task.get("type", "basic"),
            model_used=model_used,
            task_description=description,
            result=result,
            success=result.get("success", False),
            timestamp=time.time(),
            metadata=metadata or {},
            keywords=keywords,
            conditions=conditions or {},
            conditional_results=conditional_results or {}
        )
        
        # Compute initial quality score
        experience.quality_score = self.compute_quality_score(experience)
        experience.compound_value = experience.quality_score

        self.experiences.append(experience)
        self._rebuild_index()
        self._extract_patterns(experience)
        self._save_data()

        return exp_id

    def recall_relevant(self, query: str, limit: int = 5,
                        success_only: bool = False) -> List[Tuple[Experience, float]]:
        query_tokens = _tokenize(query)
        sim_scores = self._score_bm25(query_tokens)  # normalized [0,1], shape=(N,)

        scored: List[Tuple[Experience, float]] = []
        for i, exp in enumerate(self.experiences):
            if success_only and not exp.success:
                continue
            sim = float(sim_scores[i]) if i < len(sim_scores) else 0.0

            age_hours = (time.time() - exp.timestamp) / 3600
            recency_bonus = 1.0 / (1.0 + age_hours * 0.01)
            sim *= recency_bonus

            scored.append((exp, sim))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:limit]

    def get_best_model_for_task(self, task_type: str, description: str = "") -> str:
        relevant = self.recall_relevant(
            description if description else task_type,
            limit=20,
            success_only=True
        )

        if not relevant:
            return "gen15"

        model_scores: Dict[str, float] = {}
        model_counts: Dict[str, int] = {}

        for exp, sim in relevant:
            if exp.task_type == task_type:
                model = exp.model_used
                model_scores[model] = model_scores.get(model, 0) + sim
                model_counts[model] = model_counts.get(model, 0) + 1

        if not model_scores:
            for exp, sim in relevant:
                model = exp.model_used
                model_scores[model] = model_scores.get(model, 0) + sim * 0.5
                model_counts[model] = model_counts.get(model, 0) + 1

        best_model = max(model_scores, key=lambda m: model_scores[m] / max(model_counts[m], 1))
        return best_model

    def get_success_rate_by_model(self, task_type: str) -> Dict[str, float]:
        rates: Dict[str, Dict[str, int]] = {}
        for exp in self.experiences:
            if exp.task_type == task_type:
                m = exp.model_used
                if m not in rates:
                    rates[m] = {"success": 0, "total": 0}
                rates[m]["total"] += 1
                if exp.success:
                    rates[m]["success"] += 1

        return {
            m: v["success"] / v["total"] if v["total"] > 0 else 0.0
            for m, v in rates.items()
        }

    def query_by_type(self, task_type: str, limit: int = 10) -> List[Experience]:
        filtered = [exp for exp in self.experiences if exp.task_type == task_type]
        return sorted(filtered, key=lambda x: x.timestamp, reverse=True)[:limit]

    def query_by_success(self, success: bool = True, limit: int = 10) -> List[Experience]:
        filtered = [exp for exp in self.experiences if exp.success == success]
        return sorted(filtered, key=lambda x: x.timestamp, reverse=True)[:limit]

    def get_recent_experiences(self, limit: int = 5) -> List[Dict[str, Any]]:
        recent = sorted(self.experiences, key=lambda x: x.timestamp, reverse=True)[:limit]
        return [exp.to_dict() for exp in recent]

    def _extract_patterns(self, experience: Experience):
        task_type = experience.task_type
        model = experience.model_used or "unknown"

        if task_type not in self.patterns:
            self.patterns[task_type] = {
                "total": 0, "success": 0, "failure": 0,
                "avg_time": 0.0,
                "model_success_rates": {},
                "common_keywords": {},
                "recent_trend": []
            }

        p = self.patterns[task_type]
        p["total"] += 1

        if experience.success:
            p["success"] += 1
        else:
            p["failure"] += 1

        exec_time = experience.result.get("execution_time", 0)
        p["avg_time"] = (p["avg_time"] * (p["total"] - 1) + exec_time) / p["total"]

        if model not in p["model_success_rates"]:
            p["model_success_rates"][model] = {"success": 0, "total": 0}
        p["model_success_rates"][model]["total"] += 1
        if experience.success:
            p["model_success_rates"][model]["success"] += 1

        for kw in experience.keywords:
            p["common_keywords"][kw] = p["common_keywords"].get(kw, 0) + 1

        p["recent_trend"].append({
            "success": experience.success,
            "model": model,
            "time": experience.timestamp
        })
        p["recent_trend"] = p["recent_trend"][-50:]

        p["common_keywords"] = dict(
            sorted(p["common_keywords"].items(), key=lambda x: x[1], reverse=True)[:30]
        )

    def get_pattern(self, task_type: str) -> Optional[Dict[str, Any]]:
        return self.patterns.get(task_type)

    def find_conditional_contrasts(self, condition_key: str = "context") -> List[Dict[str, Any]]:
        """Find experiences where the same task succeeded in one condition but failed in another.
        Inspired by Gemma-Yale dual-context screening.
        
        Returns list of contrasts with condition_a (succeeded) and condition_b (failed).
        """
        contrasts: List[Dict[str, Any]] = []
        
        for i, exp_a in enumerate(self.experiences):
            if not exp_a.success:
                continue
            if condition_key not in exp_a.conditions:
                continue
                
            for exp_b in self.experiences[i+1:]:
                if exp_b.success:
                    continue
                if condition_key not in exp_b.conditions:
                    continue
                
                # Check if same task type and similar description
                if exp_a.task_type != exp_b.task_type:
                    continue
                
                tokens_a = set(exp_a.keywords)
                tokens_b = set(exp_b.keywords)
                overlap = len(tokens_a & tokens_b) / max(len(tokens_a | tokens_b), 1)
                
                if overlap < 0.3:
                    continue
                
                contrasts.append({
                    "task_description": exp_a.task_description,
                    "condition_a": exp_a.conditions[condition_key],
                    "condition_a_success": True,
                    "condition_a_confidence": exp_a.result.get("confidence", 0),
                    "condition_b": exp_b.conditions[condition_key],
                    "condition_b_success": False,
                    "condition_b_confidence": exp_b.result.get("confidence", 0),
                    "selectivity": (exp_a.result.get("confidence", 0) -
                                    exp_b.result.get("confidence", 0)),
                    "experience_a": exp_a.id,
                    "experience_b": exp_b.id
                })
        
        contrasts.sort(key=lambda c: c["selectivity"], reverse=True)
        return contrasts

    def recall_conditional(self, query: str, condition: str = "",
                           success_in_condition: bool = True,
                           limit: int = 5) -> List[Tuple[Experience, float]]:
        """Recall experiences filtered by condition.
        Similar to Gemma's dual-context approach: find what works in a specific context.
        """
        query_tokens = _tokenize(query)
        sim_scores = self._score_bm25(query_tokens)

        scored: List[Tuple[Experience, float]] = []
        for i, exp in enumerate(self.experiences):
            if condition:
                if condition not in exp.conditions:
                    continue
                if success_in_condition and not exp.success:
                    continue
                if not success_in_condition and exp.success:
                    continue

            sim = float(sim_scores[i]) if i < len(sim_scores) else 0.0

            age_hours = (time.time() - exp.timestamp) / 3600
            recency_bonus = 1.0 / (1.0 + age_hours * 0.01)
            sim *= recency_bonus

            scored.append((exp, sim))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:limit]

    def get_selective_models(self, task_type: str,
                             condition_key: str = "context") -> Dict[str, Dict[str, float]]:
        """Get model success rates split by condition.
        Identifies which models work best in which contexts.
        """
        model_cond_stats: Dict[str, Dict[str, Dict[str, int]]] = {}
        
        for exp in self.experiences:
            if exp.task_type != task_type:
                continue
            m = exp.model_used
            cond = exp.conditions.get(condition_key, "unknown")
            
            if m not in model_cond_stats:
                model_cond_stats[m] = {}
            if cond not in model_cond_stats[m]:
                model_cond_stats[m][cond] = {"success": 0, "total": 0}
            
            model_cond_stats[m][cond]["total"] += 1
            if exp.success:
                model_cond_stats[m][cond]["success"] += 1
        
        rates: Dict[str, Dict[str, float]] = {}
        for m, conds in model_cond_stats.items():
            rates[m] = {}
            for cond, stats in conds.items():
                rates[m][cond] = stats["success"] / stats["total"] if stats["total"] > 0 else 0.0
        
        return rates

    # ============================================================
    # Phase 1: Quality Scoring & Compound Interest (参考智能体蜂群)
    # ============================================================
    
    def compute_quality_score(self, experience: Experience) -> float:
        """计算经验质量分 (0.0 ~ 1.0)
        
        Factors:
        - Success rate of similar experiences
        - Recency (newer = higher)
        - Use count (more used = more validated)
        - Success streak (consecutive successes)
        """
        base_score = 0.5 if experience.success else 0.2
        
        # Recency bonus: exponential decay over 7 days
        age_hours = (time.time() - experience.timestamp) / 3600
        recency = math.exp(-age_hours / (7 * 24))
        
        # Use count bonus: logarithmic growth
        use_bonus = min(0.3, math.log1p(experience.use_count) * 0.1)
        
        # Success streak bonus
        streak_bonus = min(0.2, experience.success_streak * 0.05)
        
        quality = base_score * 0.4 + recency * 0.2 + use_bonus * 0.2 + streak_bonus * 0.2
        return min(1.0, max(0.0, quality))
    
    def update_quality_scores(self):
        """批量更新所有经验的质量分"""
        for exp in self.experiences:
            exp.quality_score = self.compute_quality_score(exp)
        self._save_data()
    
    def get_compound_value(self, experience: Experience) -> float:
        """计算经验复利值
        
        复利公式: V = base * (1 + rate) ^ periods
        - base: 质量分
        - rate: 使用频率系数
        - periods: 成功次数
        """
        if experience.use_count == 0:
            return experience.quality_score
        
        base = experience.quality_score
        rate = 0.1  # 10% per successful reuse
        periods = experience.success_streak
        
        compound = base * ((1 + rate) ** periods)
        return min(2.0, compound)  # Cap at 2x
    
    def mark_used(self, experience_id: str, success: bool):
        """标记经验被使用，更新复利值"""
        for exp in self.experiences:
            if exp.id == experience_id:
                exp.use_count += 1
                exp.last_used = time.time()
                if success:
                    exp.success_streak += 1
                else:
                    exp.success_streak = 0
                exp.compound_value = self.get_compound_value(exp)
                exp.quality_score = self.compute_quality_score(exp)
                break
        self._save_data()
    
    def recall_by_quality(self, query: str, limit: int = 5) -> List[Tuple[Experience, float]]:
        """按质量分+复利值排序召回"""
        query_tokens = _tokenize(query)
        sim_scores = self._score_bm25(query_tokens)

        scored: List[Tuple[Experience, float]] = []
        for i, exp in enumerate(self.experiences):
            sim = float(sim_scores[i]) if i < len(sim_scores) else 0.0

            # Combine similarity with quality and compound value
            final_score = sim * 0.4 + exp.quality_score * 0.3 + min(1.0, exp.compound_value / 2.0) * 0.3
            scored.append((exp, final_score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:limit]

    # ============================================================
    # Phase 1: Auto Skill Generation (参考AutoForge)
    # ============================================================
    
    def detect_skill_candidates(self, min_successes: int = 3) -> List[Dict[str, Any]]:
        """检测可自动创建Skill的模式
        
        当某个修复/任务模式成功3次以上，自动建议创建可复用Skill
        """
        candidates = []
        
        # Group by task_type + similar keywords
        pattern_groups: Dict[str, List[Experience]] = {}
        for exp in self.experiences:
            if not exp.success:
                continue
            key = exp.task_type
            if key not in pattern_groups:
                pattern_groups[key] = []
            pattern_groups[key].append(exp)
        
        for task_type, exps in pattern_groups.items():
            if len(exps) < min_successes:
                continue
            
            # Check for consistent model usage
            model_counts: Dict[str, int] = {}
            for exp in exps:
                model_counts[exp.model_used] = model_counts.get(exp.model_used, 0) + 1
            
            best_model = max(model_counts, key=model_counts.get)
            avg_quality = sum(e.quality_score for e in exps) / len(exps)
            
            # Extract common keywords
            all_keywords: Counter = Counter()
            for exp in exps:
                all_keywords.update(exp.keywords)
            top_keywords = [kw for kw, _ in all_keywords.most_common(5)]
            
            candidates.append({
                "task_type": task_type,
                "success_count": len(exps),
                "best_model": best_model,
                "avg_quality": avg_quality,
                "top_keywords": top_keywords,
                "suggested_skill_name": f"auto_{task_type}_skill",
                "confidence": min(1.0, len(exps) / 10)  # More successes = higher confidence
            })
        
        return sorted(candidates, key=lambda x: x["confidence"], reverse=True)

    # ============================================================
    # Phase 1: Confidence Gate (参考AutoForge 三级门控)
    # ============================================================
    
    def get_confidence_gate(self, experience: Experience) -> str:
        """三级置信度门控决策
        
        Returns:
            "AUTO_APPLY": quality >= 0.8, 自动应用
            "PENDING_REVIEW": 0.5 <= quality < 0.8, 需人工审批
            "REJECTED": quality < 0.5, 拒绝
        """
        if experience.quality_score >= 0.8:
            return "AUTO_APPLY"
        elif experience.quality_score >= 0.5:
            return "PENDING_REVIEW"
        else:
            return "REJECTED"

    def get_statistics(self) -> Dict[str, Any]:
        total = len(self.experiences)
        successful = sum(1 for exp in self.experiences if exp.success)

        return {
            "total_experiences": total,
            "successful_experiences": successful,
            "success_rate": successful / total if total > 0 else 0,
            "patterns": self.patterns,
            "task_type_distribution": self._get_task_distribution(),
            "idf_terms": len(self.idf)
        }

    def _get_task_distribution(self) -> Dict[str, int]:
        distribution: Dict[str, int] = {}
        for exp in self.experiences:
            distribution[exp.task_type] = distribution.get(exp.task_type, 0) + 1
        return distribution

    def clear_old_experiences(self, days: int = 30):
        cutoff_time = time.time() - (days * 24 * 3600)
        self.experiences = [exp for exp in self.experiences if exp.timestamp > cutoff_time]
        self._rebuild_index()
        self._save_data()


_experience_store_instance = None

def get_experience_store(data_dir: str = "data") -> ExperienceStore:
    global _experience_store_instance
    if _experience_store_instance is None:
        _experience_store_instance = ExperienceStore(data_dir)
    return _experience_store_instance
