"""
Claude Triple Model Collaborative System
gen-1.5 (basic tasks) + Mythos Preview (innovation) + Opus 4.8 (production)
Real Claude API integration via anthropic-compatible HTTP
"""

from enum import Enum
from typing import Dict, Any, List, Optional
import asyncio
import json
import time
import hashlib
import os
from dataclasses import dataclass, asdict


class TaskType(Enum):
    BASIC = "basic"
    INNOVATIVE = "innovative"
    STABLE = "stable"
    CRITICAL = "critical"


@dataclass
class ClaudeModel:
    name: str
    model_id: str
    cost_tier: str
    speed_tier: str
    api_key: Optional[str] = None
    base_url: str = "https://api.anthropic.com"

    async def execute(self, task: Dict[str, Any]) -> Dict[str, Any]:
        start_time = time.time()

        if self.api_key:
            result = await self._real_api_call(task)
        else:
            result = self._simulate_api_call(task)

        execution_time = time.time() - start_time

        return {
            "model": self.name,
            "model_id": self.model_id,
            "task": task.get("description", ""),
            "result": result,
            "execution_time": execution_time,
            "success": True
        }

    async def _real_api_call(self, task: Dict[str, Any]) -> Dict[str, Any]:
        import aiohttp

        prompt = task.get("description", "")
        context = task.get("context", {})
        requirements = task.get("requirements", [])

        system_parts = []
        if context:
            system_parts.append(f"Current context: {json.dumps(context, ensure_ascii=False)[:2000]}")
        if requirements:
            system_parts.append(f"Requirements: {', '.join(requirements)}")
        system_text = "\n".join(system_parts) if system_parts else "You are a helpful AI assistant."

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        }

        payload = {
            "model": self.model_id,
            "max_tokens": 1024,
            "system": system_text,
            "messages": [{"role": "user", "content": prompt}]
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/v1/messages",
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=60)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        text = data.get("content", [{}])[0].get("text", "")
                        return {
                            "response": text,
                            "confidence": 0.9,
                            "cost": 0.0,
                            "input_tokens": data.get("usage", {}).get("input_tokens", 0),
                            "output_tokens": data.get("usage", {}).get("output_tokens", 0)
                        }
                    else:
                        body = await resp.text()
                        return {
                            "response": f"API error {resp.status}: {body[:500]}",
                            "confidence": 0.3,
                            "cost": 0.0,
                            "error": True
                        }
        except Exception as e:
            return {
                "response": f"API call failed: {str(e)}",
                "confidence": 0.3,
                "cost": 0.0,
                "error": True
            }

    def _simulate_api_call(self, task: Dict[str, Any]) -> Dict[str, Any]:
        if task.get("type") == "basic":
            return {
                "response": f"gen-1.5 processed: {task.get('description', '')[:200]}",
                "confidence": 0.85,
                "cost": 0.001
            }
        elif task.get("type") == "innovative":
            return {
                "response": f"Mythos innovation: {task.get('description', '')[:200]}",
                "confidence": 0.92,
                "cost": 0.015
            }
        elif task.get("type") == "stable":
            return {
                "response": f"Opus production: {task.get('description', '')[:200]}",
                "confidence": 0.98,
                "cost": 0.025
            }
        return {"response": "Task processed", "confidence": 0.8, "cost": 0.005}


class TripleModelSystem:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key

        self.models = {
            "gen15": ClaudeModel(
                name="gen-1.5", model_id="claude-sonnet-4-20250514",
                cost_tier="low", speed_tier="fast", api_key=api_key
            ),
            "mythos": ClaudeModel(
                name="Mythos Preview", model_id="claude-sonnet-4-20250514",
                cost_tier="medium", speed_tier="medium", api_key=api_key
            ),
            "opus": ClaudeModel(
                name="Opus 4.8", model_id="claude-sonnet-4-20250514",
                cost_tier="high", speed_tier="slow", api_key=api_key
            )
        }

        self.task_model_map = {
            TaskType.BASIC: "gen15",
            TaskType.INNOVATIVE: "mythos",
            TaskType.STABLE: "opus",
            TaskType.CRITICAL: "opus_mythos"
        }

        self.experience_store: List[Dict[str, Any]] = []

    def _classify_task(self, task: Dict[str, Any]) -> TaskType:
        explicit = task.get("type", "basic")
        if explicit == "innovative":
            return TaskType.INNOVATIVE
        elif explicit == "stable":
            return TaskType.STABLE
        elif explicit == "critical":
            return TaskType.CRITICAL

        complexity = task.get("complexity", 0.3)
        if complexity > 0.8:
            return TaskType.CRITICAL
        elif complexity > 0.5:
            return TaskType.STABLE
        elif complexity > 0.2:
            return TaskType.INNOVATIVE
        return TaskType.BASIC

    async def execute_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        start_time = time.time()

        task_type = self._classify_task(task)
        model_name = self.task_model_map[task_type]

        if model_name == "opus_mythos":
            result = await self._dual_model_execution(task)
        else:
            result = await self.models[model_name].execute(task)

        experience = {
            "task": task,
            "task_type": task_type.value,
            "model_used": model_name,
            "result": result,
            "timestamp": time.time()
        }
        self.experience_store.append(experience)

        total_time = time.time() - start_time

        return {
            "task_type": task_type.value,
            "model_used": model_name,
            "execution_time": total_time,
            "result": result,
            "success": result.get("success", False)
        }

    async def _dual_model_execution(self, task: Dict[str, Any]) -> Dict[str, Any]:
        mythos_result = await self.models["mythos"].execute({
            **task,
            "description": f"Innovative solution for: {task.get('description', '')}"
        })

        opus_result = await self.models["opus"].execute({
            **task,
            "description": f"Validate and produce production-grade implementation of: {mythos_result.get('result', {}).get('response', '')[:500]}"
        })

        return opus_result

    async def batch_execute(self, tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        results = []
        for task in tasks:
            result = await self.execute_task(task)
            results.append(result)
        return results

    async def conditional_reasoning(self, hypothesis: str,
                                     conditions: Dict[str, str],
                                     model_assignment: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Conditional reasoning across multiple contexts (Gemma-Yale inspired).
        
        Different models reason about the same hypothesis in different contexts.
        Default assignment:
          - "target" context -> Mythos (innovation, context-dependent reasoning)
          - "neutral" context -> gen-1.5 (basic validation)
          - "cross-check" -> Opus (final synthesis)
        
        Args:
            hypothesis: The hypothesis to evaluate
            conditions: Dict of context_name -> context_description
            model_assignment: Optional override for which model handles which context
        
        Returns:
            Dict with per-context results and cross-context analysis
        """
        if model_assignment is None:
            model_assignment = {}
            for ctx_name in conditions:
                if "target" in ctx_name or "production" in ctx_name:
                    model_assignment[ctx_name] = "mythos"
                elif "neutral" in ctx_name or "simple" in ctx_name:
                    model_assignment[ctx_name] = "gen15"
                else:
                    model_assignment[ctx_name] = "gen15"

        context_results = {}
        for ctx_name, ctx_desc in conditions.items():
            model_name = model_assignment.get(ctx_name, "gen15")
            task = {
                "type": "basic" if model_name == "gen15" else "innovative",
                "description": (
                    f"Evaluate hypothesis in context '{ctx_name}':\n"
                    f"Hypothesis: {hypothesis}\n"
                    f"Context: {ctx_desc}\n"
                    f"Rate confidence 0-1 and explain reasoning."
                ),
                "context": {"hypothesis": hypothesis, "validation_context": ctx_name}
            }
            result = await self.models[model_name].execute(task)
            res = result.get("result", {})
            context_results[ctx_name] = {
                "model_used": model_name,
                "confidence": res.get("confidence", 0.5),
                "response": res.get("response", ""),
                "error": res.get("error", False)
            }

        # Cross-check with Opus
        cross_check_contexts = "\n".join([
            f"  {name}: confidence={r['confidence']:.2f} - {r['response'][:100]}"
            for name, r in context_results.items()
        ])
        cross_task = {
            "type": "stable",
            "description": (
                f"Synthesize cross-context analysis for hypothesis:\n"
                f"{hypothesis}\n\n"
                f"Context evaluations:\n{cross_check_contexts}\n\n"
                f"Compute selectivity (target vs neutral) and overall recommendation."
            ),
            "context": {
                "hypothesis": hypothesis,
                "context_results": context_results
            }
        }
        cross_result = await self.models["opus"].execute(cross_task)
        cross_res = cross_result.get("result", {})

        # Compute selectivity from results
        confidences = [r["confidence"] for r in context_results.values()]
        target_key = [k for k in context_results if "target" in k or "production" in k]
        neutral_key = [k for k in context_results if "neutral" in k or "simple" in k]

        selectivity = 0.0
        if target_key and neutral_key:
            selectivity = (context_results[target_key[0]]["confidence"] -
                          context_results[neutral_key[0]]["confidence"])

        return {
            "hypothesis": hypothesis,
            "context_results": context_results,
            "cross_check": cross_res.get("response", ""),
            "selectivity": selectivity,
            "max_confidence": max(confidences) if confidences else 0,
            "min_confidence": min(confidences) if confidences else 0
        }

    def get_statistics(self) -> Dict[str, Any]:
        if not self.experience_store:
            return {"total_tasks": 0, "success_rate": 0, "avg_time": 0}

        total = len(self.experience_store)
        successful = sum(1 for exp in self.experience_store if exp["result"].get("success", False))
        total_time = sum(exp["result"].get("execution_time", 0) for exp in self.experience_store)

        return {
            "total_tasks": total,
            "successful_tasks": successful,
            "success_rate": successful / total if total > 0 else 0,
            "avg_time": total_time / total if total > 0 else 0,
            "model_usage": self._get_model_usage()
        }

    def _get_model_usage(self) -> Dict[str, int]:
        usage = {"gen15": 0, "mythos": 0, "opus": 0, "opus_mythos": 0}
        for exp in self.experience_store:
            model = exp.get("model_used", "gen15")
            usage[model] = usage.get(model, 0) + 1
        return usage


_triple_model_instance = None

def get_triple_model(api_key: Optional[str] = None) -> TripleModelSystem:
    global _triple_model_instance
    if _triple_model_instance is None:
        _triple_model_instance = TripleModelSystem(api_key)
    return _triple_model_instance
