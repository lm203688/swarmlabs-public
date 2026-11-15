"""
Hypothesis Tree Refinement Loop  ——  【研究模块，非生产主链路】

角色定位（与 agentteams/ 的关系）：
- 本模块是 **LLM 驱动的假设树探索**（triple_model + 上下文依赖发现），
  用于「自主发现新假设」的研究性实验，属于 agentteams 之外的独立研究路径。
- **生产主链路 / 竞赛 Demo 以 `agentteams/`（确定性科学计算闭环）为准**；
  本模块不接入 VeritasGuard 的写权限门禁，也不承担对外可信交付。
- 两者架构理念不同：一个用 LLM 驱动探索，一个不用 LLM 做数值结论。
  若需合并，应把本模块的「高置信分支」作为 candidate 喂给 agentteams 的 board，
  而非让 LLM 直接产出可信数值。

Implements a tree-based exploration strategy:
- Root hypothesis branches into multiple child hypotheses
- Each branch is validated independently
- Low-confidence branches are pruned
- High-confidence branches expand further
- Best branch is selected for implementation
- Experience store feeds back into future tree construction
"""

import asyncio
import time
import json
import hashlib
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, asdict, field
from enum import Enum
from pathlib import Path


class NodeStatus(Enum):
    PENDING = "pending"
    EXPANDING = "expanding"
    VALIDATING = "validating"
    PRUNED = "pruned"
    IMPLEMENTED = "implemented"
    FAILED = "failed"


@dataclass
class HypothesisNode:
    id: str
    parent_id: Optional[str]
    hypothesis: str
    rationale: str
    confidence: float
    status: NodeStatus
    depth: int
    children_ids: List[str]
    validation_result: Optional[Dict[str, Any]] = None
    implementation: Optional[Dict[str, Any]] = None
    score: float = 0.0
    created_at: float = field(default_factory=time.time)
    validation_contexts: Dict[str, float] = field(default_factory=dict)
    selectivity: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HypothesisNode":
        if isinstance(data.get("status"), str):
            data["status"] = NodeStatus(data["status"])
        return cls(**data)


class HypothesisTree:
    def __init__(self, root_hypothesis: str, root_rationale: str = ""):
        self.nodes: Dict[str, HypothesisNode] = {}
        self.max_depth = 4
        self.max_children = 3
        self.pruning_threshold = 0.25
        self.expansion_threshold = 0.5

        root = HypothesisNode(
            id=self._make_id(root_hypothesis),
            parent_id=None,
            hypothesis=root_hypothesis,
            rationale=root_rationale,
            confidence=0.5,
            status=NodeStatus.PENDING,
            depth=0,
            children_ids=[]
        )
        self.nodes[root.id] = root
        self.root_id = root.id

    @staticmethod
    def _make_id(text: str) -> str:
        return hashlib.md5(f"{text}{time.time()}".encode()).hexdigest()[:12]

    def add_child(self, parent_id: str, hypothesis: str, rationale: str,
                  confidence: float = 0.5) -> Optional[HypothesisNode]:
        parent = self.nodes.get(parent_id)
        if not parent:
            return None
        if parent.depth >= self.max_depth:
            return None
        if len(parent.children_ids) >= self.max_children:
            return None

        child = HypothesisNode(
            id=self._make_id(hypothesis),
            parent_id=parent_id,
            hypothesis=hypothesis,
            rationale=rationale,
            confidence=confidence,
            status=NodeStatus.PENDING,
            depth=parent.depth + 1,
            children_ids=[]
        )
        parent.children_ids.append(child.id)
        self.nodes[child.id] = child
        return child

    def prune(self, node_id: str):
        node = self.nodes.get(node_id)
        if not node:
            return
        node.status = NodeStatus.PRUNED
        for child_id in node.children_ids:
            self.prune(child_id)

    def get_leaf_nodes(self) -> List[HypothesisNode]:
        return [n for n in self.nodes.values()
                if not n.children_ids and n.status != NodeStatus.PRUNED]

    def get_active_nodes(self) -> List[HypothesisNode]:
        return [n for n in self.nodes.values()
                if n.status in (NodeStatus.PENDING, NodeStatus.EXPANDING, NodeStatus.VALIDATING)]

    def get_best_leaf(self) -> Optional[HypothesisNode]:
        leaves = [n for n in self.nodes.values()
                  if n.status == NodeStatus.IMPLEMENTED and n.score > 0]
        if not leaves:
            return None
        return max(leaves, key=lambda n: n.score)

    def compute_node_score(self, node: HypothesisNode) -> float:
        depth_bonus = 1.0 / (1.0 + node.depth * 0.2)
        conf_score = node.confidence * 0.5

        parent = self.nodes.get(node.parent_id) if node.parent_id else None
        parent_factor = (parent.confidence * 0.2) if parent else 0.1

        validation_bonus = 0.0
        if node.validation_result:
            val_conf = node.validation_result.get("confidence", 0)
            validation_bonus = val_conf * 0.15

        # Selectivity bonus: higher selectivity (works in target context, not in neutral)
        # is rewarded heavily - this is the Gemma-Yale insight
        selectivity_bonus = min(node.selectivity, 1.0) * 0.15

        node.score = conf_score + parent_factor + validation_bonus + depth_bonus * 0.05 + selectivity_bonus
        return node.score

    def to_dict(self) -> Dict[str, Any]:
        return {
            "root_id": self.root_id,
            "nodes": {nid: n.to_dict() for nid, n in self.nodes.items()},
            "stats": {
                "total_nodes": len(self.nodes),
                "active": len(self.get_active_nodes()),
                "pruned": sum(1 for n in self.nodes.values() if n.status == NodeStatus.PRUNED),
                "implemented": sum(1 for n in self.nodes.values() if n.status == NodeStatus.IMPLEMENTED)
            }
        }


class EnhancedAutonomousLoop:
    def __init__(self, api_key: Optional[str] = None, data_dir: str = "data"):
        from core.triple_model_system import TripleModelSystem
        from core.experience_store import ExperienceStore
        from core.tech_radar import TechRadar

        self.triple_model = TripleModelSystem(api_key)
        self.experience_store = ExperienceStore(data_dir)
        self.tech_radar = TechRadar(data_dir)

        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(exist_ok=True)
        self.state_file = self.data_dir / "loop_state.json"

        self.iteration = 0
        self.max_iterations = 50
        self.tree: Optional[HypothesisTree] = None
        self.current_state: Dict[str, Any] = {}
        self.history: List[Dict[str, Any]] = []

        # Dual-context configuration (Gemma-Yale inspired)
        self.validation_contexts = {
            "target": "production environment with active constraints, dependencies, and real-world complexity",
            "neutral": "isolated environment with no constraints, no dependencies, simplified conditions"
        }

    async def run(self, initial_state: Dict[str, Any],
                  max_iterations: int = 50) -> Dict[str, Any]:
        self.max_iterations = max_iterations
        self.current_state = initial_state

        print(f"[START] Hypothesis Tree Loop (max_iter={max_iterations})")
        print("=" * 60)

        root_hyp = await self._generate_root_hypothesis(initial_state)
        self.tree = HypothesisTree(root_hyp, "Root hypothesis from initial state")

        for i in range(max_iterations):
            self.iteration = i
            print(f"\n--- Iteration {i+1}/{max_iterations} ---")

            active = self.tree.get_active_nodes()
            if not active:
                print("  No active nodes. Expanding best leaf...")
                expanded = await self._expand_best_leaf()
                if not expanded:
                    print("  No expandable nodes. Stopping.")
                    break
                active = self.tree.get_active_nodes()

            for node in active[:3]:
                print(f"  [VALIDATE] node={node.id[:8]} depth={node.depth}")
                node.status = NodeStatus.VALIDATING

                val_result = await self._validate_node(node)
                node.validation_result = val_result
                node.confidence = val_result.get("confidence", 0)
                self.tree.compute_node_score(node)

                if node.confidence < self.tree.pruning_threshold:
                    print(f"    [PRUNE] confidence={node.confidence:.2f} < {self.tree.pruning_threshold}")
                    node.status = NodeStatus.PRUNED
                    self.tree.prune(node.id)
                    self._record_experience(node, success=False)
                    continue

                if node.depth < self.tree.max_depth and node.confidence >= self.tree.expansion_threshold:
                    print(f"    [EXPAND] confidence={node.confidence:.2f}")
                    node.status = NodeStatus.EXPANDING
                    children = await self._expand_node(node)
                    print(f"    Created {len(children)} child hypotheses")
                else:
                    print(f"    [IMPLEMENT] confidence={node.confidence:.2f}")
                    node.status = NodeStatus.IMPLEMENTED
                    impl = await self._implement_node(node)
                    node.implementation = impl
                    self._record_experience(node, success=True)

            best = self.tree.get_best_leaf()
            if best:
                print(f"\n  Best leaf: score={best.score:.3f} conf={best.confidence:.2f}")
                if best.score >= 0.8 and best.depth >= 2:
                    print(f"\n[DONE] High-quality solution found at iteration {i+1}")
                    break

            self._save_state()

        print("\n" + "=" * 60)
        print("[COMPLETE] Tree loop finished")
        self._print_tree_summary()

        return self._build_result()

    async def _generate_root_hypothesis(self, state: Dict[str, Any]) -> str:
        experiences = self.experience_store.get_recent_experiences(5)
        exp_summary = ""
        if experiences:
            successful = [e for e in experiences if e.get("success")]
            failed = [e for e in experiences if not e.get("success")]
            exp_summary = (
                f"Past experiences: {len(successful)} successful, {len(failed)} failed. "
                f"Recent patterns: {json.dumps(self.experience_store.patterns, ensure_ascii=False)[:500]}"
            )

        task = {
            "type": "innovative",
            "description": (
                f"Generate a root hypothesis for exploring: {json.dumps(state, ensure_ascii=False)[:300]}\n"
                f"{exp_summary}\n"
                f"Output a single clear hypothesis statement."
            ),
            "context": {"current_state": state, "experiences": experiences},
            "requirements": ["novelty", "feasibility", "high value"]
        }

        result = await self.triple_model.execute_task(task)
        response = result.get("result", {}).get("result", {}).get("response", "")
        return response if response else f"Explore improvements for: {str(state)[:100]}"

    async def _validate_node(self, node: HypothesisNode) -> Dict[str, Any]:
        """Dual-context validation (Gemma-Yale inspired).
        
        Validates hypothesis in two contexts:
        1. Target context: production-like with real constraints
        2. Neutral context: simplified, no constraints
        
        Only hypotheses that score high in target AND low in neutral
        are truly context-dependent (high selectivity).
        """
        # Recall similar past experiences with their conditions
        similar_past = self.experience_store.recall_relevant(node.hypothesis, limit=5)
        past_summary = ""
        if similar_past:
            past_summary = "\nSimilar past experiences:\n"
            for exp, sim in similar_past[:3]:
                status = "success" if exp.success else "failure"
                conds = exp.conditions if exp.conditions else {}
                past_summary += f"  - [{status}] (sim={sim:.2f}) {exp.task_description[:80]}"
                if conds:
                    past_summary += f" conditions={json.dumps(conds, ensure_ascii=False)[:100]}"
                past_summary += "\n"

        # Check conditional contrasts from past
        contrasts = self.experience_store.find_conditional_contrasts("context")
        contrast_summary = ""
        if contrasts:
            contrast_summary = "\nConditional contrasts found:\n"
            for c in contrasts[:3]:
                contrast_summary += (
                    f"  - {c['task_description'][:60]}: "
                    f"{c['condition_a']}={c['condition_a_success']}, "
                    f"{c['condition_b']}={c['condition_b_success']} "
                    f"(selectivity={c['selectivity']:.2f})\n"
                )

        # Validate in TARGET context (with constraints)
        target_task = {
            "type": "basic",
            "description": (
                f"Validate this hypothesis in the TARGET context:\n"
                f"Hypothesis: {node.hypothesis}\n"
                f"Rationale: {node.rationale}\n"
                f"Context: {self.validation_contexts['target']}\n"
                f"{past_summary}\n"
                f"Rate confidence 0-1 that this will work in this specific constrained context."
            ),
            "context": {
                "hypothesis": node.hypothesis,
                "depth": node.depth,
                "validation_mode": "target",
                "constraints": self.validation_contexts["target"],
                "past_similar": [{"desc": e.task_description[:100], "success": e.success, "sim": s,
                                  "conditions": e.conditions}
                                 for e, s in similar_past[:3]]
            }
        }

        target_result = await self.triple_model.execute_task(target_task)
        target_res = target_result.get("result", {}).get("result", {})
        target_confidence = target_res.get("confidence", 0.5)
        if target_res.get("error"):
            target_confidence = 0.1

        # Validate in NEUTRAL context (simplified)
        neutral_task = {
            "type": "basic",
            "description": (
                f"Validate this hypothesis in a NEUTRAL context:\n"
                f"Hypothesis: {node.hypothesis}\n"
                f"Context: {self.validation_contexts['neutral']}\n"
                f"Rate confidence 0-1 that this would work even without real constraints."
            ),
            "context": {
                "hypothesis": node.hypothesis,
                "depth": node.depth,
                "validation_mode": "neutral",
                "constraints": self.validation_contexts["neutral"]
            }
        }

        neutral_result = await self.triple_model.execute_task(neutral_task)
        neutral_res = neutral_result.get("result", {}).get("result", {})
        neutral_confidence = neutral_res.get("confidence", 0.5)
        if neutral_res.get("error"):
            neutral_confidence = 0.1

        # Compute selectivity: how much better in target vs neutral
        # High selectivity = hypothesis is context-dependent (the Gemma insight)
        selectivity = max(0, target_confidence - neutral_confidence)

        # Store validation contexts on node
        node.validation_contexts = {
            "target": target_confidence,
            "neutral": neutral_confidence
        }
        node.selectivity = selectivity

        # Overall confidence is weighted toward target context
        overall_confidence = target_confidence * 0.7 + (1 - neutral_confidence) * 0.3

        print(f"    [CTX] target={target_confidence:.2f} neutral={neutral_confidence:.2f} sel={selectivity:.2f}")

        return {
            "confidence": overall_confidence,
            "target_confidence": target_confidence,
            "neutral_confidence": neutral_confidence,
            "selectivity": selectivity,
            "response": target_res.get("response", ""),
            "error": target_res.get("error", False)
        }

    async def _expand_node(self, node: HypothesisNode) -> List[HypothesisNode]:
        children = []

        task = {
            "type": "innovative",
            "description": (
                f"Given hypothesis: {node.hypothesis}\n"
                f"Generate 2-3 specific sub-hypotheses that refine or extend this idea.\n"
                f"Each should be a concrete, testable statement.\n"
                f"Output as a numbered list."
            ),
            "context": {
                "parent_hypothesis": node.hypothesis,
                "depth": node.depth,
                "node_id": node.id
            }
        }

        result = await self.triple_model.execute_task(task)
        response = result.get("result", {}).get("result", {}).get("response", "")

        sub_hyps = self._parse_sub_hypotheses(response)

        for i, (hyp, rationale) in enumerate(sub_hyps[:self.tree.max_children]):
            child = self.tree.add_child(node.id, hyp, rationale, confidence=0.5)
            if child:
                children.append(child)

        if not children:
            fallback = self.tree.add_child(
                node.id,
                f"Refinement of: {node.hypothesis}",
                "Fallback expansion when parsing failed",
                confidence=0.4
            )
            if fallback:
                children.append(fallback)

        return children

    def _parse_sub_hypotheses(self, text: str) -> List[Tuple[str, str]]:
        results = []
        lines = text.strip().split("\n")
        current_hyp = ""
        current_rationale = ""

        for line in lines:
            line = line.strip()
            if not line:
                continue
            if line[0].isdigit() and ("." in line or ")" in line):
                if current_hyp:
                    results.append((current_hyp, current_rationale))
                parts = line.split(".", 1) if "." in line else line.split(")", 1)
                current_hyp = parts[1].strip() if len(parts) > 1 else line
                current_rationale = ""
            elif current_hyp:
                current_rationale += " " + line

        if current_hyp:
            results.append((current_hyp, current_rationale.strip()))

        return results

    async def _implement_node(self, node: HypothesisNode) -> Dict[str, Any]:
        task = {
            "type": "stable",
            "description": (
                f"Implement production-grade solution for: {node.hypothesis}\n"
                f"Validation confidence: {node.confidence}\n"
                f"Tree depth: {node.depth}"
            ),
            "context": {
                "hypothesis": node.hypothesis,
                "validation": node.validation_result,
                "score": node.score
            }
        }

        result = await self.triple_model.execute_task(task)
        return result.get("result", {}).get("result", {})

    async def _expand_best_leaf(self) -> bool:
        best = self.tree.get_best_leaf()
        if not best:
            active = self.tree.get_active_nodes()
            if active:
                return True
            for node in self.tree.nodes.values():
                if node.status == NodeStatus.PRUNED:
                    continue
                if not node.children_ids and node.depth < self.tree.max_depth:
                    children = await self._expand_node(node)
                    if children:
                        return True
            return False

        if best.depth >= self.tree.max_depth:
            return False

        children = await self._expand_node(best)
        return len(children) > 0

    def _record_experience(self, node: HypothesisNode, success: bool):
        # Build conditions from validation contexts
        conditions = {}
        if node.validation_contexts:
            conditions["context"] = "target" if node.validation_contexts.get("target", 0) > 0.5 else "neutral"
            conditions["target_confidence"] = node.validation_contexts.get("target", 0)
            conditions["neutral_confidence"] = node.validation_contexts.get("neutral", 0)
        
        # Build conditional results (which contexts it works in)
        conditional_results = {}
        if node.validation_contexts:
            conditional_results["works_in_target"] = node.validation_contexts.get("target", 0) >= 0.5
            conditional_results["works_in_neutral"] = node.validation_contexts.get("neutral", 0) >= 0.5
        
        self.experience_store.record(
            task={
                "type": "innovative" if success else "basic",
                "description": node.hypothesis
            },
            model_used="opus_mythos" if success else "gen15",
            result={
                "success": success,
                "confidence": node.confidence,
                "score": node.score,
                "depth": node.depth,
                "selectivity": node.selectivity
            },
            metadata={
                "node_id": node.id,
                "parent_id": node.parent_id,
                "iteration": self.iteration
            },
            conditions=conditions,
            conditional_results=conditional_results
        )

    def _save_state(self):
        state = {
            "iteration": self.iteration,
            "tree": self.tree.to_dict() if self.tree else None,
            "current_state": self.current_state,
            "history": self.history[-10:]
        }
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)

    def _print_tree_summary(self):
        if not self.tree:
            return
        stats = self.tree.to_dict()["stats"]
        print(f"  Total nodes: {stats['total_nodes']}")
        print(f"  Active: {stats['active']}")
        print(f"  Pruned: {stats['pruned']}")
        print(f"  Implemented: {stats['implemented']}")

        best = self.tree.get_best_leaf()
        if best:
            print(f"  Best leaf score: {best.score:.3f}")
            print(f"  Best hypothesis: {best.hypothesis[:100]}")

    def _build_result(self) -> Dict[str, Any]:
        best = self.tree.get_best_leaf() if self.tree else None
        return {
            "status": "completed" if best else "no_solution",
            "best_score": best.score if best else 0,
            "best_hypothesis": best.hypothesis if best else None,
            "tree_stats": self.tree.to_dict()["stats"] if self.tree else {},
            "iterations_used": self.iteration + 1,
            "experience_count": len(self.experience_store.experiences)
        }

    def get_statistics(self) -> Dict[str, Any]:
        if not self.tree:
            return {"total_iterations": 0, "success_rate": 0, "avg_time": 0}
        stats = self.tree.to_dict()["stats"]
        total = stats["total_nodes"]
        implemented = stats["implemented"]
        return {
            "total_iterations": self.iteration + 1,
            "total_nodes": total,
            "implemented": implemented,
            "pruned": stats["pruned"],
            "success_rate": implemented / total if total > 0 else 0,
            "convergence_iteration": self.iteration + 1 if stats["implemented"] > 0 else None
        }

    def save_state(self, filepath: str):
        self._save_state()


_loop_instance = None

def get_autonomous_loop(api_key: Optional[str] = None, data_dir: str = "data") -> EnhancedAutonomousLoop:
    global _loop_instance
    if _loop_instance is None:
        _loop_instance = EnhancedAutonomousLoop(api_key, data_dir)
    return _loop_instance
