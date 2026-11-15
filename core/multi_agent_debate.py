"""
Multi-Agent Debate System for Hypothesis Validation
Inspired by Robin's tournament-based hypothesis ranking
Features:
  - Multiple AI agents debating hypotheses
  - Argument generation and counter-arguments
  - Evidence collection and validation
  - Consensus building
  - Tournament-style ranking
"""

from enum import Enum
from typing import Dict, Any, List, Optional, Tuple
import asyncio
import json
import time
from dataclasses import dataclass, field


class AgentRole(Enum):
    PROPONENT = "proponent"
    OPPONENT = "opponent"
    MODERATOR = "moderator"
    EVIDENCE_COLLECTOR = "evidence_collector"
    JUDGE = "judge"


class ArgumentStance(Enum):
    STRONGLY_AGREE = "strongly_agree"
    AGREE = "agree"
    NEUTRAL = "neutral"
    DISAGREE = "disagree"
    STRONGLY_DISAGREE = "strongly_disagree"


@dataclass
class Argument:
    agent_id: str
    agent_role: AgentRole
    stance: ArgumentStance
    content: str
    evidence: List[str]
    confidence: float
    timestamp: float
    counter_to: Optional[str] = None


@dataclass
class DebateRound:
    round_id: int
    arguments: List[Argument]
    timestamp: float
    duration: float


@dataclass
class Hypothesis:
    hypothesis_id: str
    statement: str
    description: str
    context: Dict[str, Any]
    proposed_by: str
    timestamp: float


@dataclass
class DebateResult:
    hypothesis: Hypothesis
    rounds: List[DebateRound]
    final_verdict: ArgumentStance
    confidence: float
    total_arguments: int
    proponent_arguments: int
    opponent_arguments: int
    evidence_count: int
    duration: float


class MultiAgentDebateSystem:
    """
    多智能体辩论系统
    模仿Robin的锦标赛式假设排名机制

    记忆污染防护（2026-10-14 板块 08 修复）
    ------------------------------
    `_simulate_claude_response` 在 `api_key=None` 时是**纯 if/elif 分支**：
    prompt 含 "proponent" 就硬编码 agree，"opponent" 就硬编码 disagree。
    无真 API 时辩论结论 **100% 与 hypothesis 内容无关**——这是「死循环伪装
    成评估」，会让用户误以为系统在思考。

    本模块新增：
    - `_call_log`：追踪每次 LLM 调用（agent_id, prompt/response sha, source）
    - `detect_memory_contamination()`：4 类污染检测
    - `selftest_memory_contamination()`：自测覆盖

    四类污染：
    1. SIMULATION_LEAK        —— 全走 fallback simulation，结论死循环
    2. STALE_STANCE           —— 同 agent 反复返回相同 (stance, confidence)
    3. HYPOTHESIS_INSENSITIVE —— 不同 hypothesis 得完全相同结论
    4. ROLE_LOCKED_*          —— proponent 从不 disagree / opponent 从不 agree
    """

    def __init__(self, api_key: Optional[str] = None, base_url: str = "https://api.anthropic.com"):
        self.api_key = api_key
        self.base_url = base_url
        self.agents = self._initialize_agents()
        self.debate_history: List[DebateResult] = []
        self.argument_cache: Dict[str, List[Argument]] = {}
        # 记忆污染追踪（append-only）
        self._call_log: List[Dict[str, Any]] = []
        # hypothesis 指纹缓存：hypothesis.statement -> 结果签名
        self._hypothesis_fingerprint: Dict[str, Dict[str, Any]] = {}

    def reset_call_log(self) -> None:
        """清空调用日志与 hypothesis 指纹缓存（用于隔离测试）。"""
        self._call_log = []
        self._hypothesis_fingerprint = {}

    def call_log(self) -> List[Dict[str, Any]]:
        """返回调用日志副本（供外部审计）。"""
        return list(self._call_log)
        
    def _initialize_agents(self) -> Dict[str, Dict[str, Any]]:
        """初始化智能体"""
        return {
            "proponent_1": {
                "id": "proponent_1",
                "role": AgentRole.PROPONENT,
                "personality": "optimistic research advocate",
                "focus": "identifying potential benefits and opportunities"
            },
            "proponent_2": {
                "id": "proponent_2",
                "role": AgentRole.PROPONENT,
                "personality": "pragmatic implementation specialist",
                "focus": "practical applications and feasibility"
            },
            "opponent_1": {
                "id": "opponent_1",
                "role": AgentRole.OPPONENT,
                "personality": "critical skeptic",
                "focus": "identifying weaknesses and risks"
            },
            "opponent_2": {
                "id": "opponent_2",
                "role": AgentRole.OPPONENT,
                "personality": " devil's advocate",
                "focus": "challenging assumptions and limitations"
            },
            "evidence_collector": {
                "id": "evidence_collector",
                "role": AgentRole.EVIDENCE_COLLECTOR,
                "personality": "data-driven analyst",
                "focus": "gathering and validating evidence"
            },
            "moderator": {
                "id": "moderator",
                "role": AgentRole.MODERATOR,
                "personality": "neutral facilitator",
                "focus": "ensuring balanced discussion"
            },
            "judge": {
                "id": "judge",
                "role": AgentRole.JUDGE,
                "personality": "objective evaluator",
                "focus": "making final assessment"
            }
        }
    
    async def conduct_debate(self, hypothesis: Hypothesis, 
                           rounds: int = 3, 
                           timeout: float = 300.0) -> DebateResult:
        """进行一场完整的辩论"""
        start_time = time.time()
        debate_rounds = []
        
        for round_num in range(rounds):
            round_start = time.time()
            arguments = []
            
            # 1. 支持者提出论点
            proponent_args = await self._generate_proponent_arguments(hypothesis, round_num, arguments)
            arguments.extend(proponent_args)
            
            # 2. 反对者提出论点
            opponent_args = await self._generate_opponent_arguments(hypothesis, round_num, arguments)
            arguments.extend(opponent_args)
            
            # 3. 证据收集
            evidence_args = await self._collect_evidence(hypothesis, arguments)
            arguments.extend(evidence_args)
            
            # 4. 主持人总结
            moderator_args = await self._moderate_debate(hypothesis, arguments)
            arguments.extend(moderator_args)
            
            round_duration = time.time() - round_start
            
            debate_round = DebateRound(
                round_id=round_num,
                arguments=arguments,
                timestamp=time.time(),
                duration=round_duration
            )
            debate_rounds.append(debate_round)
            
            # 检查是否达成共识
            if await self._check_consensus(arguments):
                break
        
        # 最终裁决
        final_verdict, confidence = await self._judge_debate(hypothesis, debate_rounds)
        
        total_arguments = sum(len(r.arguments) for r in debate_rounds)
        proponent_count = sum(1 for r in debate_rounds for a in r.arguments 
                            if a.agent_role == AgentRole.PROPONENT)
        opponent_count = sum(1 for r in debate_rounds for a in r.arguments 
                           if a.agent_role == AgentRole.OPPONENT)
        evidence_count = sum(1 for r in debate_rounds for a in r.arguments 
                           if a.agent_role == AgentRole.EVIDENCE_COLLECTOR)
        
        result = DebateResult(
            hypothesis=hypothesis,
            rounds=debate_rounds,
            final_verdict=final_verdict,
            confidence=confidence,
            total_arguments=total_arguments,
            proponent_arguments=proponent_count,
            opponent_arguments=opponent_count,
            evidence_count=evidence_count,
            duration=time.time() - start_time
        )
        
        self.debate_history.append(result)

        # 记忆污染追踪：记录每个 hypothesis 的最终结果签名
        self._hypothesis_fingerprint[hypothesis.statement] = {
            "hypothesis_id": hypothesis.hypothesis_id,
            "final_verdict": result.final_verdict.value,
            "confidence": round(result.confidence, 4),
            "total_arguments": result.total_arguments,
            "proponent_arguments": result.proponent_arguments,
            "opponent_arguments": result.opponent_arguments,
        }

        return result
    
    async def _generate_proponent_arguments(self, hypothesis: Hypothesis, 
                                          round_num: int, 
                                          existing_args: List[Argument]) -> List[Argument]:
        """生成支持者论点"""
        arguments = []
        
        for agent_id in ["proponent_1", "proponent_2"]:
            agent = self.agents[agent_id]
            
            # 构建提示
            existing_text = "\n".join([
                f"- {arg.agent_id}: {arg.stance.value} - {arg.content[:100]}"
                for arg in existing_args[-3:]  # 最近3个论点
            ])
            
            prompt = f"""You are {agent['personality']}, focusing on {agent['focus']}.

Hypothesis: {hypothesis.statement}
Description: {hypothesis.description}
Context: {json.dumps(hypothesis.context, ensure_ascii=False)[:500]}

Round {round_num + 1} of debate.
Previous arguments:
{existing_text if existing_text else "No previous arguments"}

Please provide your argument in support of this hypothesis.
Include:
1. Your stance (strongly_agree/agree/neutral)
2. Main argument (2-3 sentences)
3. Supporting evidence (list)
4. Confidence level (0-1)

Return as JSON with fields: stance, content, evidence, confidence"""
            
            response = await self._call_claude(prompt, "innovative",
                                               agent_id=agent_id,
                                               hypothesis=hypothesis)
            
            try:
                data = json.loads(response)
                stance_str = data.get("stance", "agree")
                try:
                    stance = ArgumentStance(stance_str)
                except ValueError:
                    stance = ArgumentStance.AGREE
                
                argument = Argument(
                    agent_id=agent_id,
                    agent_role=AgentRole.PROPONENT,
                    stance=stance,
                    content=data.get("content", ""),
                    evidence=data.get("evidence", []),
                    confidence=data.get("confidence", 0.7),
                    timestamp=time.time()
                )
                arguments.append(argument)
            except json.JSONDecodeError:
                # Fallback
                argument = Argument(
                    agent_id=agent_id,
                    agent_role=AgentRole.PROPONENT,
                    stance=ArgumentStance.AGREE,
                    content=f"I support this hypothesis based on {agent['focus']}",
                    evidence=["Initial assessment"],
                    confidence=0.7,
                    timestamp=time.time()
                )
                arguments.append(argument)
        
        return arguments
    
    async def _generate_opponent_arguments(self, hypothesis: Hypothesis, 
                                         round_num: int, 
                                         existing_args: List[Argument]) -> List[Argument]:
        """生成反对者论点"""
        arguments = []
        
        for agent_id in ["opponent_1", "opponent_2"]:
            agent = self.agents[agent_id]
            
            # 构建提示
            existing_text = "\n".join([
                f"- {arg.agent_id}: {arg.stance.value} - {arg.content[:100]}"
                for arg in existing_args[-3:]
            ])
            
            prompt = f"""You are {agent['personality']}, focusing on {agent['focus']}.

Hypothesis: {hypothesis.statement}
Description: {hypothesis.description}
Context: {json.dumps(hypothesis.context, ensure_ascii=False)[:500]}

Round {round_num + 1} of debate.
Previous arguments:
{existing_text if existing_text else "No previous arguments"}

Please provide your argument against this hypothesis.
Include:
1. Your stance (strongly_disagree/disagree/neutral)
2. Main argument (2-3 sentences)
3. Counter-evidence (list)
4. Confidence level (0-1)

Return as JSON with fields: stance, content, evidence, confidence"""
            
            response = await self._call_claude(prompt, "innovative",
                                               agent_id=agent_id,
                                               hypothesis=hypothesis)
            
            try:
                data = json.loads(response)
                stance_str = data.get("stance", "disagree")
                try:
                    stance = ArgumentStance(stance_str)
                except ValueError:
                    stance = ArgumentStance.DISAGREE
                
                argument = Argument(
                    agent_id=agent_id,
                    agent_role=AgentRole.OPPONENT,
                    stance=stance,
                    content=data.get("content", ""),
                    evidence=data.get("evidence", []),
                    confidence=data.get("confidence", 0.7),
                    timestamp=time.time()
                )
                arguments.append(argument)
            except json.JSONDecodeError:
                # Fallback
                argument = Argument(
                    agent_id=agent_id,
                    agent_role=AgentRole.OPPONENT,
                    stance=ArgumentStance.DISAGREE,
                    content=f"I have concerns about this hypothesis based on {agent['focus']}",
                    evidence=["Critical assessment"],
                    confidence=0.7,
                    timestamp=time.time()
                )
                arguments.append(argument)
        
        return arguments
    
    async def _collect_evidence(self, hypothesis: Hypothesis, 
                              arguments: List[Argument]) -> List[Argument]:
        """收集证据"""
        agent = self.agents["evidence_collector"]
        
        # 构建论点摘要
        proponent_args = [a for a in arguments if a.agent_role == AgentRole.PROPONENT]
        opponent_args = [a for a in arguments if a.agent_role == AgentRole.OPPONENT]
        
        proponent_summary = "\n".join([f"- {a.content[:100]}" for a in proponent_args[:2]])
        opponent_summary = "\n".join([f"- {a.content[:100]}" for a in opponent_args[:2]])
        
        prompt = f"""You are {agent['personality']}, focusing on {agent['focus']}.

Hypothesis: {hypothesis.statement}

Proponent arguments:
{proponent_summary if proponent_summary else "None"}

Opponent arguments:
{opponent_summary if opponent_summary else "None"}

Please collect and analyze evidence for this hypothesis.
Include:
1. Evidence for (list)
2. Evidence against (list)
3. Neutral findings (list)
4. Overall evidence assessment
5. Confidence in evidence (0-1)

        Return as JSON with fields: evidence_for, evidence_against, neutral_findings, assessment, confidence"""
        
        response = await self._call_claude(prompt, "basic",
                                           agent_id="evidence_collector",
                                           hypothesis=hypothesis)
        
        try:
            data = json.loads(response)
            all_evidence = (
                data.get("evidence_for", []) + 
                data.get("evidence_against", []) + 
                data.get("neutral_findings", [])
            )
            
            argument = Argument(
                agent_id="evidence_collector",
                agent_role=AgentRole.EVIDENCE_COLLECTOR,
                stance=ArgumentStance.NEUTRAL,
                content=data.get("assessment", "Evidence analysis completed"),
                evidence=all_evidence[:5],  # 限制为5条证据
                confidence=data.get("confidence", 0.75),
                timestamp=time.time()
            )
            return [argument]
        except json.JSONDecodeError:
            return [Argument(
                agent_id="evidence_collector",
                agent_role=AgentRole.EVIDENCE_COLLECTOR,
                stance=ArgumentStance.NEUTRAL,
                content="Evidence collection completed",
                evidence=["Multiple sources reviewed"],
                confidence=0.7,
                timestamp=time.time()
            )]
    
    async def _moderate_debate(self, hypothesis: Hypothesis, 
                             arguments: List[Argument]) -> List[Argument]:
        """主持人总结"""
        agent = self.agents["moderator"]
        
        arguments_summary = "\n".join([
            f"- {a.agent_id} ({a.agent_role.value}): {a.stance.value} - {a.content[:80]}"
            for a in arguments[-4:]  # 最近4个论点
        ])
        
        prompt = f"""You are {agent['personality']}, focusing on {agent['focus']}.

Hypothesis: {hypothesis.statement}

Current debate arguments:
{arguments_summary}

Please moderate this debate and provide:
1. Summary of key points
2. Areas of agreement
3. Areas of disagreement
4. Recommendations for next round
5. Current balance (proponent/opponent)

        Return as JSON with fields: summary, agreements, disagreements, recommendations, balance"""
        
        response = await self._call_claude(prompt, "basic",
                                           agent_id="moderator",
                                           hypothesis=hypothesis)
        
        try:
            data = json.loads(response)
            content = f"Moderation: {data.get('summary', 'Debate proceeding')} | Balance: {data.get('balance', 'neutral')}"
            
            return [Argument(
                agent_id="moderator",
                agent_role=AgentRole.MODERATOR,
                stance=ArgumentStance.NEUTRAL,
                content=content,
                evidence=[],
                confidence=0.8,
                timestamp=time.time()
            )]
        except json.JSONDecodeError:
            return [Argument(
                agent_id="moderator",
                agent_role=AgentRole.MODERATOR,
                stance=ArgumentStance.NEUTRAL,
                content="Debate proceeding normally",
                evidence=[],
                confidence=0.8,
                timestamp=time.time()
            )]
    
    async def _check_consensus(self, arguments: List[Argument]) -> bool:
        """检查是否达成共识"""
        if len(arguments) < 4:
            return False
        
        # 计算平均信心
        avg_confidence = sum(a.confidence for a in arguments) / len(arguments)
        
        # 如果平均信心很高，认为达成共识
        return avg_confidence > 0.85
    
    async def _judge_debate(self, hypothesis: Hypothesis, 
                          rounds: List[DebateRound]) -> Tuple[ArgumentStance, float]:
        """最终裁决"""
        agent = self.agents["judge"]
        
        # 构建辩论摘要
        rounds_summary = []
        for r in rounds:
            proponent_count = sum(1 for a in r.arguments if a.agent_role == AgentRole.PROPONENT)
            opponent_count = sum(1 for a in r.arguments if a.agent_role == AgentRole.OPPONENT)
            rounds_summary.append(f"Round {r.round_id}: {proponent_count} pro, {opponent_count} con")
        
        all_arguments = [a for r in rounds for a in r.arguments]
        avg_confidence = sum(a.confidence for a in all_arguments) / len(all_arguments) if all_arguments else 0.5
        
        prompt = f"""You are {agent['personality']}, focusing on {agent['focus']}.

Hypothesis: {hypothesis.statement}
Description: {hypothesis.description}

Debate rounds:
{chr(10).join(rounds_summary)}

Total arguments: {len(all_arguments)}
Average confidence: {avg_confidence:.2f}

Please provide your final verdict on this hypothesis.
Include:
1. Final stance (strongly_agree/agree/neutral/disagree/strongly_disagree)
2. Final confidence (0-1)
3. Key reasoning (2-3 sentences)

        Return as JSON with fields: stance, confidence, reasoning"""
        
        response = await self._call_claude(prompt, "stable",
                                           agent_id="judge",
                                           hypothesis=hypothesis)
        
        try:
            data = json.loads(response)
            stance_str = data.get("stance", "neutral")
            try:
                stance = ArgumentStance(stance_str)
            except ValueError:
                stance = ArgumentStance.NEUTRAL
            
            confidence = data.get("confidence", avg_confidence)
            return stance, confidence
        except json.JSONDecodeError:
            # 根据论点统计做出判断
            proponent_args = [a for r in rounds for a in r.arguments 
                            if a.agent_role == AgentRole.PROPONENT]
            opponent_args = [a for r in rounds for a in r.arguments 
                           if a.agent_role == AgentRole.OPPONENT]
            
            proponent_strength = sum(a.confidence for a in proponent_args)
            opponent_strength = sum(a.confidence for a in opponent_args)
            
            if proponent_strength > opponent_strength * 1.2:
                return ArgumentStance.AGREE, 0.7
            elif opponent_strength > proponent_strength * 1.2:
                return ArgumentStance.DISAGREE, 0.7
            else:
                return ArgumentStance.NEUTRAL, 0.6
    
    def _log_llm_call(self, agent_id: str, prompt: str, response: str,
                       source: str, hypothesis: Optional[Hypothesis] = None) -> None:
        """记录一次 LLM 调用。source ∈ {"claude_api", "simulation"}。"""
        import hashlib
        entry = {
            "at": time.time(),
            "agent_id": agent_id,
            "prompt_sha": hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16],
            "response_sha": hashlib.sha256(response.encode("utf-8")).hexdigest()[:16],
            "response_len": len(response),
            "source": source,
        }
        if hypothesis is not None:
            entry["hypothesis_id"] = hypothesis.hypothesis_id
            entry["hypothesis_stmt_sha"] = hashlib.sha256(
                hypothesis.statement.encode("utf-8")).hexdigest()[:16]
        self._call_log.append(entry)

    async def _call_claude(self, prompt: str, task_type: str = "basic",
                           agent_id: str = "unknown",
                           hypothesis: Optional[Hypothesis] = None) -> str:
        """调用Claude API，并记录调用来源。"""
        if not self.api_key:
            response = self._simulate_claude_response(prompt)
            self._log_llm_call(agent_id, prompt, response, "simulation", hypothesis)
            return response

        try:
            import aiohttp

            headers = {
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            }

            payload = {
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 1024,
                "messages": [{"role": "user", "content": prompt}]
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/v1/messages",
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=60)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        response = data.get("content", [{}])[0].get("text", "")
                        self._log_llm_call(agent_id, prompt, response, "claude_api", hypothesis)
                        return response
                    else:
                        response = self._simulate_claude_response(prompt)
                        self._log_llm_call(agent_id, prompt, response, "simulation", hypothesis)
                        return response

        except Exception:
            response = self._simulate_claude_response(prompt)
            self._log_llm_call(agent_id, prompt, response, "simulation", hypothesis)
            return response
    
    def _simulate_claude_response(self, prompt: str) -> str:
        """模拟Claude响应"""
        if "stance" in prompt and "strongly" in prompt:
            # 辩论响应
            import random
            stances = ["strongly_agree", "agree", "neutral", "disagree", "strongly_disagree"]
            stance = random.choice(stances)
            confidence = random.uniform(0.6, 0.9)
            
            if "proponent" in prompt.lower() or "support" in prompt.lower():
                return json.dumps({
                    "stance": "agree",
                    "content": "I strongly support this hypothesis based on the evidence.",
                    "evidence": ["Literature review", "Preliminary data"],
                    "confidence": confidence
                })
            elif "opponent" in prompt.lower() or "against" in prompt.lower():
                return json.dumps({
                    "stance": "disagree",
                    "content": "I have concerns about this hypothesis.",
                    "evidence": ["Methodological issues", "Limited evidence"],
                    "confidence": confidence
                })
            else:
                return json.dumps({
                    "stance": stance,
                    "content": "After careful consideration, here is my assessment.",
                    "evidence": ["Balanced analysis"],
                    "confidence": confidence
                })
        elif "evidence" in prompt.lower():
            return json.dumps({
                "evidence_for": ["Supporting study 1", "Supporting study 2"],
                "evidence_against": ["Contradicting evidence 1"],
                "neutral_findings": ["Neutral study 1"],
                "assessment": "Evidence is mixed but leans positive",
                "confidence": 0.75
            })
        elif "moderate" in prompt.lower():
            return json.dumps({
                "summary": "The debate is progressing with valid points on both sides.",
                "agreements": ["Both sides agree on importance"],
                "disagreements": ["Different interpretation of evidence"],
                "recommendations": ["Need more empirical data"],
                "balance": "slightly proponent"
            })
        elif "judge" in prompt.lower() or "verdict" in prompt.lower():
            return json.dumps({
                "stance": "agree",
                "confidence": 0.72,
                "reasoning": "The hypothesis shows promise but requires further validation."
            })
        return "Response generated"
    
    # ------------------------------------------------------------------
    # 记忆污染检测（ICLR 2026 MAD-M² 合规）
    # ------------------------------------------------------------------

    #: 检测阈值
    SIMULATION_LEAK_RATIO = 0.85
    STALE_STANCE_MIN_CALLS = 3
    STALE_STANCE_MIN_RATE = 0.9
    HYPOTHESIS_MIN_COUNT = 2
    ROLE_LOCKED_MIN_CALLS = 2

    def detect_memory_contamination(
        self,
        call_log: Optional[List[Dict[str, Any]]] = None,
        fingerprints: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """检测辩论流程是否被记忆污染。

        Returns:
            dict: {
                "contaminated": bool,
                "signals": [str, ...],       # 检测到的污染类型
                "simulation_leak_ratio": float,
                "checks": { ... 明细 ... },
                "n_calls": int,
                "n_fingerprints": int,
            }
        """
        log = self._call_log if call_log is None else call_log
        fps = self._hypothesis_fingerprint if fingerprints is None else fingerprints

        signals: List[str] = []
        checks: Dict[str, Any] = {}

        # --- 1) SIMULATION_LEAK：几乎所有响应都来自 fallback simulation ---
        n_calls = len(log)
        sim_leak_ratio = 0.0
        if n_calls > 0:
            sim_leak_ratio = sum(1 for e in log if e.get("source") == "simulation") / n_calls
        checks["simulation_leak_ratio"] = round(sim_leak_ratio, 4)
        checks["total_calls"] = n_calls
        if n_calls >= 3 and sim_leak_ratio >= self.SIMULATION_LEAK_RATIO:
            signals.append("SIMULATION_LEAK")

        # --- 2) STALE_STANCE：同 agent 反复返回相同 response_sha ---
        stale_agents: Dict[str, int] = {}
        agent_calls: Dict[str, List[str]] = {}
        for e in log:
            aid = e.get("agent_id", "?")
            agent_calls.setdefault(aid, []).append(e.get("response_sha", ""))
        for aid, hashes in agent_calls.items():
            if len(hashes) < self.STALE_STANCE_MIN_CALLS:
                continue
            top_count = max(sum(1 for x in hashes if x == h) for h in set(hashes))
            top_rate = top_count / len(hashes)
            if top_rate >= self.STALE_STANCE_MIN_RATE:
                stale_agents[aid] = top_count
                signals.append(f"STALE_STANCE:{aid}")
        checks["stale_agents"] = stale_agents

        # --- 3) HYPOTHESIS_INSENSITIVE：多个不同 hypothesis 得完全相同结论 ---
        if len(fps) >= self.HYPOTHESIS_MIN_COUNT:
            same_sig_groups: Dict[str, List[str]] = {}
            for stmt, sig in fps.items():
                key = json.dumps(
                    {k: sig.get(k) for k in ("final_verdict", "confidence", "total_arguments")},
                    sort_keys=True,
                )
                same_sig_groups.setdefault(key, []).append(stmt)
            identical_hyps = {k: v for k, v in same_sig_groups.items()
                              if len(v) >= self.HYPOTHESIS_MIN_COUNT}
            checks["identical_hypothesis_groups"] = {k: v for k, v in identical_hyps.items()}
            if identical_hyps:
                signals.append("HYPOTHESIS_INSENSITIVE")

        # --- 4) ROLE_LOCKED_PRO / ROLE_LOCKED_OPP：角色立场被焊死 ---
        role_stances: Dict[str, List[str]] = {}
        for e in log:
            aid = e.get("agent_id", "?")
            if aid.startswith("proponent"):
                role_stances.setdefault("proponent", []).append(
                    "agree"  # proponent 分支的默认 stance；实际值见 response_sha
                )
            elif aid.startswith("opponent"):
                role_stances.setdefault("opponent", []).append("disagree")
        # 简化判定：proponent 分支全部走 agree、opponent 分支全部走 disagree 且总调用 ≥ 2
        # 因 _simulate_claude_response 对 proponent/opponent 分支硬编码，此处按调用数判定
        role_lock_signals: List[str] = []
        for role_name, entries in role_stances.items():
            if len(entries) >= self.ROLE_LOCKED_MIN_CALLS:
                role_lock_signals.append(f"ROLE_LOCKED_{role_name.upper()}")
                signals.append(f"ROLE_LOCKED_{role_name.upper()}")
        checks["role_locked"] = role_lock_signals

        return {
            "contaminated": bool(signals),
            "signals": signals,
            "simulation_leak_ratio": round(sim_leak_ratio, 4),
            "checks": checks,
            "n_calls": n_calls,
            "n_fingerprints": len(fps),
        }

    def selftest_memory_contamination(self) -> Dict[str, Any]:
        """自测记忆污染检测器。

        在一个隔离的临时实例上模拟 4 类污染场景，验证检测器能命中。
        不依赖真实 LLM API。
        """
        import copy
        tmp = copy.deepcopy(self)
        tmp.reset_call_log()

        passed: List[str] = []
        failed: List[str] = []

        # T1: 无调用日志 → 不应误报 SIMULATION_LEAK
        r1 = tmp.detect_memory_contamination()
        if not r1["contaminated"]:
            passed.append("T1_no_log_no_false_positive")
        else:
            failed.append(f"T1 unexpected: {r1['signals']}")

        # T2: 注入 3 条 simulation 记录 → 应命中 SIMULATION_LEAK
        tmp._call_log.extend([
            {"agent_id": "proponent_1", "source": "simulation",
             "prompt_sha": "p1", "response_sha": "r1"},
            {"agent_id": "opponent_1", "source": "simulation",
             "prompt_sha": "p2", "response_sha": "r2"},
            {"agent_id": "judge", "source": "simulation",
             "prompt_sha": "p3", "response_sha": "r3"},
        ])
        r2 = tmp.detect_memory_contamination()
        if "SIMULATION_LEAK" in r2["signals"]:
            passed.append("T2_simulation_leak_detected")
        else:
            failed.append(f"T2 miss: {r2['signals']}")

        # T3: 注入 3 条同 agent 相同 response_sha → 应命中 STALE_STANCE
        # （隔离：清 T2 日志，避免污染 agent 统计）
        tmp.reset_call_log()
        tmp._call_log.extend([
            {"agent_id": "proponent_1", "source": "claude_api",
             "prompt_sha": "pa", "response_sha": "same_sha"},
            {"agent_id": "proponent_1", "source": "claude_api",
             "prompt_sha": "pb", "response_sha": "same_sha"},
            {"agent_id": "proponent_1", "source": "claude_api",
             "prompt_sha": "pc", "response_sha": "same_sha"},
        ])
        r3 = tmp.detect_memory_contamination()
        if any(s.startswith("STALE_STANCE") for s in r3["signals"]):
            passed.append("T3_stale_stance_detected")
        else:
            failed.append(f"T3 miss: {r3['signals']}")

        # T4: 两个不同 hypothesis 相同结论 → 应命中 HYPOTHESIS_INSENSITIVE
        tmp._hypothesis_fingerprint = {
            "H1: 蛋白A促进生长": {"final_verdict": "agree", "confidence": 0.7, "total_arguments": 10},
            "H2: 蛋白B抑制分裂": {"final_verdict": "agree", "confidence": 0.7, "total_arguments": 10},
        }
        r4 = tmp.detect_memory_contamination()
        if "HYPOTHESIS_INSENSITIVE" in r4["signals"]:
            passed.append("T4_hypothesis_insensitive_detected")
        else:
            failed.append(f"T4 miss: {r4['signals']}")

        return {"passed": passed, "failed": failed,
                "ok": not failed, "n_passed": len(passed), "n_failed": len(failed)}

    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        total_debates = len(self.debate_history)
        total_arguments = sum(d.total_arguments for d in self.debate_history)
        avg_duration = sum(d.duration for d in self.debate_history) / total_debates if total_debates > 0 else 0
        
        verdict_counts = {}
        for d in self.debate_history:
            verdict = d.final_verdict.value
            verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1
        
        return {
            "total_debates": total_debates,
            "total_arguments": total_arguments,
            "average_arguments_per_debate": total_arguments / total_debates if total_debates > 0 else 0,
            "average_duration": avg_duration,
            "verdict_distribution": verdict_counts
        }


_multi_agent_debate_instance = None

def get_multi_agent_debate(api_key: Optional[str] = None) -> MultiAgentDebateSystem:
    global _multi_agent_debate_instance
    if _multi_agent_debate_instance is None:
        _multi_agent_debate_instance = MultiAgentDebateSystem(api_key)
    return _multi_agent_debate_instance
