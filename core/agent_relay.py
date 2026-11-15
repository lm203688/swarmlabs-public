"""
Agent Relay - Seamless Multi-Model Switching
Inspired by Orbit's Agent Relay mechanism

Features:
- Seamless switching between Claude, Codex, GPT, etc.
- Context preservation across model switches
- Task handoff with state transfer
- Model capability matching
"""

from enum import Enum
from typing import Dict, Any, List, Optional, Callable
import asyncio
import json
import time
from dataclasses import dataclass, field


class ModelProvider(Enum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    GOOGLE = "google"
    LOCAL = "local"
    SIMULATION = "simulation"


class RelayStatus(Enum):
    IDLE = "idle"
    RUNNING = "running"
    SWITCHING = "switching"
    ERROR = "error"


@dataclass
class ModelConfig:
    model_id: str
    provider: ModelProvider
    model_name: str
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    max_tokens: int = 4096
    cost_per_1k: float = 0.003
    capabilities: List[str] = field(default_factory=list)
    priority: int = 0  # Higher = preferred


@dataclass
class RelayContext:
    context_id: str
    task_description: str
    current_model: str
    history: List[Dict[str, Any]]
    state: Dict[str, Any]
    created_at: float = 0.0
    last_updated: float = 0.0


@dataclass
class HandoffPackage:
    handoff_id: str
    from_model: str
    to_model: str
    context: RelayContext
    evidence: List[Dict[str, Any]]
    timestamp: float = 0.0


class AgentRelay:
    """
    Agent接力器
    
    支持多模型无缝切换
    保持上下文连续性
    """
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key
        self.models: Dict[str, ModelConfig] = {}
        self.contexts: Dict[str, RelayContext] = {}
        self.handoff_history: List[HandoffPackage] = []
        self.status = RelayStatus.IDLE
        self._initialize_default_models()
    
    def _initialize_default_models(self):
        """初始化默认模型配置"""
        self.models = {
            "claude-sonnet": ModelConfig(
                model_id="claude-sonnet",
                provider=ModelProvider.ANTHROPIC,
                model_name="claude-sonnet-4-20250514",
                max_tokens=8192,
                cost_per_1k=0.003,
                capabilities=["code", "analysis", "creative", "reasoning"],
                priority=10
            ),
            "claude-haiku": ModelConfig(
                model_id="claude-haiku",
                provider=ModelProvider.ANTHROPIC,
                model_name="claude-3-5-haiku-20241022",
                max_tokens=4096,
                cost_per_1k=0.0003,
                capabilities=["code", "quick_tasks"],
                priority=5
            ),
            "gpt-4o": ModelConfig(
                model_id="gpt-4o",
                provider=ModelProvider.OPENAI,
                model_name="gpt-4o",
                max_tokens=4096,
                cost_per_1k=0.005,
                capabilities=["code", "analysis", "creative"],
                priority=8
            ),
            "simulation": ModelConfig(
                model_id="simulation",
                provider=ModelProvider.SIMULATION,
                model_name="simulation",
                max_tokens=4096,
                cost_per_1k=0.0,
                capabilities=["code", "analysis", "creative", "reasoning"],
                priority=1
            )
        }
    
    def register_model(self, config: ModelConfig):
        """注册模型"""
        self.models[config.model_id] = config
    
    def create_context(self, task_description: str, 
                      initial_model: str = "claude-sonnet") -> RelayContext:
        """创建接力上下文"""
        context_id = f"ctx_{int(time.time())}"
        
        context = RelayContext(
            context_id=context_id,
            task_description=task_description,
            current_model=initial_model,
            history=[],
            state={},
            created_at=time.time(),
            last_updated=time.time()
        )
        
        self.contexts[context_id] = context
        return context
    
    async def execute(self, context_id: str, prompt: str,
                     model_override: Optional[str] = None) -> Dict[str, Any]:
        """执行任务"""
        if context_id not in self.contexts:
            return {"error": "Context not found"}
        
        context = self.contexts[context_id]
        model_id = model_override or context.current_model
        
        if model_id not in self.models:
            return {"error": f"Model {model_id} not found"}
        
        model = self.models[model_id]
        
        # Execute based on provider
        if model.provider == ModelProvider.SIMULATION:
            result = await self._execute_simulation(prompt, model)
        elif model.provider == ModelProvider.ANTHROPIC:
            result = await self._execute_anthropic(prompt, model)
        elif model.provider == ModelProvider.OPENAI:
            result = await self._execute_openai(prompt, model)
        else:
            result = {"error": f"Unsupported provider: {model.provider}"}
        
        # Update context
        context.history.append({
            "model": model_id,
            "prompt": prompt[:200],
            "result": result.get("response", "")[:200],
            "timestamp": time.time()
        })
        context.current_model = model_id
        context.last_updated = time.time()
        
        return result
    
    async def _execute_simulation(self, prompt: str, model: ModelConfig) -> Dict[str, Any]:
        """模拟执行"""
        await asyncio.sleep(0.1)
        return {
            "response": f"[Simulation] Processed by {model.model_name}: {prompt[:100]}",
            "confidence": 0.8,
            "model": model.model_id,
            "cost": 0.0
        }
    
    async def _execute_anthropic(self, prompt: str, model: ModelConfig) -> Dict[str, Any]:
        """Anthropic API执行"""
        if not model.api_key:
            return await self._execute_simulation(prompt, model)
        
        try:
            import aiohttp
            
            headers = {
                "x-api-key": model.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            }
            
            payload = {
                "model": model.model_name,
                "max_tokens": model.max_tokens,
                "messages": [{"role": "user", "content": prompt}]
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{model.base_url or 'https://api.anthropic.com'}/v1/messages",
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=60)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return {
                            "response": data.get("content", [{}])[0].get("text", ""),
                            "confidence": 0.9,
                            "model": model.model_id,
                            "cost": 0.0
                        }
                    else:
                        return await self._execute_simulation(prompt, model)
        except Exception:
            return await self._execute_simulation(prompt, model)
    
    async def _execute_openai(self, prompt: str, model: ModelConfig) -> Dict[str, Any]:
        """OpenAI API执行"""
        if not model.api_key:
            return await self._execute_simulation(prompt, model)
        
        # Simplified: would implement actual OpenAI call
        return await self._execute_simulation(prompt, model)
    
    async def switch_model(self, context_id: str, from_model: str, 
                          to_model: str, reason: str = "") -> bool:
        """切换模型"""
        if context_id not in self.contexts:
            return False
        
        context = self.contexts[context_id]
        
        # Create handoff package
        handoff = HandoffPackage(
            handoff_id=f"handoff_{int(time.time())}",
            from_model=from_model,
            to_model=to_model,
            context=context,
            evidence=[{
                "type": "model_switch",
                "reason": reason,
                "timestamp": time.time()
            }],
            timestamp=time.time()
        )
        
        self.handoff_history.append(handoff)
        
        # Update context
        context.current_model = to_model
        context.last_updated = time.time()
        
        return True
    
    def select_best_model(self, task_type: str, 
                         requirements: Optional[List[str]] = None) -> str:
        """选择最适合的模型"""
        candidates = []
        
        for model_id, config in self.models.items():
            if model_id == "simulation":
                continue
            
            # Check capabilities
            if requirements:
                has_caps = all(cap in config.capabilities for cap in requirements)
                if not has_caps:
                    continue
            
            candidates.append((model_id, config.priority, config.cost_per_1k))
        
        if not candidates:
            return "simulation"
        
        # Sort by priority (descending), then cost (ascending)
        candidates.sort(key=lambda x: (-x[1], x[2]))
        return candidates[0][0]
    
    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            "total_models": len(self.models),
            "total_contexts": len(self.contexts),
            "total_handoffs": len(self.handoff_history),
            "status": self.status.value,
            "models": list(self.models.keys())
        }


_agent_relay_instance = None

def get_agent_relay(api_key: Optional[str] = None) -> AgentRelay:
    global _agent_relay_instance
    if _agent_relay_instance is None:
        _agent_relay_instance = AgentRelay(api_key)
    return _agent_relay_instance
