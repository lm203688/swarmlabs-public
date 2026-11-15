"""
Cost Breaker - Three-Level Cost Circuit Breaker
Inspired by AutoForge's cost control mechanism

Features:
- Daily budget limit with automatic downgrade
- Emergency brake at 2x budget
- Cost tracking per operation
- Automatic model downgrade when budget exceeded
"""

from enum import Enum
from typing import Dict, Any, List, Optional
import time
import json
from dataclasses import dataclass, field
from pathlib import Path


class CostLevel(Enum):
    NORMAL = "normal"
    WARNING = "warning"      # 80% budget used
    DOWNGRADE = "downgrade"  # 100% budget used
    EMERGENCY = "emergency"  # 200% budget used


class ModelTier(Enum):
    TIER1 = "tier1"  # High cost, high quality (Opus)
    TIER2 = "tier2"  # Medium cost, medium quality (Sonnet)
    TIER3 = "tier3"  # Low cost, low quality (Haiku)


@dataclass
class CostRecord:
    operation: str
    model: str
    cost: float
    timestamp: float
    tokens_used: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


class CostBreaker:
    """
    三级成本熔断器
    
    Level 1: Normal (< 80% budget)
    Level 2: Warning (80-100% budget) - log warning
    Level 3: Downgrade (> 100% budget) - switch to cheaper model
    Level 4: Emergency (> 200% budget) - stop all operations
    """
    
    def __init__(self, daily_budget: float = 5.0, data_dir: str = "data"):
        self.daily_budget = daily_budget
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(exist_ok=True)
        
        self.cost_file = self.data_dir / "cost_records.json"
        self.records: List[CostRecord] = []
        self.current_level = CostLevel.NORMAL
        
        # Model tier mapping
        self.model_tiers = {
            "opus": ModelTier.TIER1,
            "mythos": ModelTier.TIER1,
            "gen15": ModelTier.TIER2,
            "haiku": ModelTier.TIER3,
            "claude-sonnet-4-20250514": ModelTier.TIER2,
            "claude-3-5-haiku-20241022": ModelTier.TIER3,
        }
        
        # Cost per 1K tokens by tier
        self.tier_costs = {
            ModelTier.TIER1: 0.015,
            ModelTier.TIER2: 0.003,
            ModelTier.TIER3: 0.0003,
        }
        
        self._load_data()
    
    def _load_data(self):
        try:
            if self.cost_file.exists() and self.cost_file.stat().st_size > 0:
                with open(self.cost_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self.records = [
                    CostRecord(**r) for r in data.get("records", [])
                ]
        except (json.JSONDecodeError, Exception):
            self.records = []
    
    def _save_data(self):
        with open(self.cost_file, 'w', encoding='utf-8') as f:
            json.dump({
                "records": [
                    {
                        "operation": r.operation,
                        "model": r.model,
                        "cost": r.cost,
                        "timestamp": r.timestamp,
                        "tokens_used": r.tokens_used,
                        "metadata": r.metadata
                    }
                    for r in self.records
                ],
                "last_updated": time.time()
            }, f, indent=2, ensure_ascii=False)
    
    def get_today_cost(self) -> float:
        """获取今日总成本"""
        today_start = time.time() - (time.time() % 86400)
        return sum(
            r.cost for r in self.records
            if r.timestamp >= today_start
        )
    
    def get_cost_level(self) -> CostLevel:
        """获取当前成本等级"""
        today_cost = self.get_today_cost()
        ratio = today_cost / self.daily_budget
        
        if ratio >= 2.0:
            return CostLevel.EMERGENCY
        elif ratio >= 1.0:
            return CostLevel.DOWNGRADE
        elif ratio >= 0.8:
            return CostLevel.WARNING
        else:
            return CostLevel.NORMAL
    
    def can_execute(self, estimated_cost: float = 0.0) -> bool:
        """检查是否可以执行操作"""
        level = self.get_cost_level()
        
        if level == CostLevel.EMERGENCY:
            return False
        
        if level == CostLevel.DOWNGRADE:
            # Only allow low-cost operations
            return estimated_cost < 0.001
        
        return True
    
    def get_recommended_model(self, requested_model: str) -> str:
        """根据成本等级推荐模型"""
        level = self.get_cost_level()
        
        if level == CostLevel.EMERGENCY:
            # Emergency: only allow free/very cheap models
            return "simulation"
        
        if level == CostLevel.DOWNGRADE:
            # Downgrade: switch to cheapest available
            return "haiku"
        
        if level == CostLevel.WARNING:
            # Warning: downgrade one tier
            requested_tier = self.model_tiers.get(requested_model, ModelTier.TIER2)
            if requested_tier == ModelTier.TIER1:
                return "gen15"  # Downgrade to tier 2
            elif requested_tier == ModelTier.TIER2:
                return "haiku"  # Downgrade to tier 3
        
        return requested_model
    
    def record_cost(self, operation: str, model: str, cost: float,
                    tokens_used: int = 0, metadata: Optional[Dict[str, Any]] = None):
        """记录成本"""
        record = CostRecord(
            operation=operation,
            model=model,
            cost=cost,
            timestamp=time.time(),
            tokens_used=tokens_used,
            metadata=metadata or {}
        )
        self.records.append(record)
        
        # Update level
        old_level = self.current_level
        self.current_level = self.get_cost_level()
        
        # Log level change
        if old_level != self.current_level:
            print(f"[COST] Level changed: {old_level.value} -> {self.current_level.value}")
            print(f"[COST] Today cost: ${self.get_today_cost():.4f} / ${self.daily_budget:.2f}")
        
        self._save_data()
    
    def estimate_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """估算操作成本"""
        tier = self.model_tiers.get(model, ModelTier.TIER2)
        cost_per_1k = self.tier_costs[tier]
        
        total_tokens = input_tokens + output_tokens
        return (total_tokens / 1000) * cost_per_1k
    
    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        today_cost = self.get_today_cost()
        level = self.get_cost_level()
        
        # Cost by model
        cost_by_model: Dict[str, float] = {}
        for r in self.records:
            cost_by_model[r.model] = cost_by_model.get(r.model, 0) + r.cost
        
        # Cost by operation type
        cost_by_op: Dict[str, float] = {}
        for r in self.records:
            cost_by_op[r.operation] = cost_by_op.get(r.operation, 0) + r.cost
        
        return {
            "today_cost": today_cost,
            "daily_budget": self.daily_budget,
            "budget_used_pct": (today_cost / self.daily_budget * 100) if self.daily_budget > 0 else 0,
            "current_level": level.value,
            "cost_by_model": cost_by_model,
            "cost_by_operation": cost_by_op,
            "total_records": len(self.records)
        }


_cost_breaker_instance = None

def get_cost_breaker(daily_budget: float = 5.0, data_dir: str = "data") -> CostBreaker:
    global _cost_breaker_instance
    if _cost_breaker_instance is None:
        _cost_breaker_instance = CostBreaker(daily_budget, data_dir)
    return _cost_breaker_instance
