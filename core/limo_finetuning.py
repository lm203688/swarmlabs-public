"""
LIMO Fine-tuning for Low-Data Regimes
Inspired by LIMO (Less Is More for Alignment) approach
Features:
  - Efficient fine-tuning with minimal data
  - Quality over quantity principle
  - Task-specific adaptation
  - Performance monitoring
  - Resource optimization
"""

from enum import Enum
from typing import Dict, Any, List, Optional
import asyncio
import json
import time
from dataclasses import dataclass, field


class FineTuningStrategy(Enum):
    FULL = "full"  # Full fine-tuning
    LORA = "lora"  # Low-rank adaptation
    PROMPT_TUNING = "prompt_tuning"  # Prompt tuning
    ADAPTER = "adapter"  # Adapter layers
    LIMO = "limo"  # Less is more for alignment


class DataQuality(Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    CURATED = "curated"


class TrainingStatus(Enum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"


@dataclass
class TrainingExample:
    example_id: str
    input_text: str
    output_text: str
    quality: DataQuality
    metadata: Dict[str, Any] = field(default_factory=dict)
    weight: float = 1.0


@dataclass
class Dataset:
    dataset_id: str
    name: str
    examples: List[TrainingExample]
    quality_distribution: Dict[DataQuality, int] = field(default_factory=dict)
    created_at: float = 0.0


@dataclass
class FineTuningConfig:
    config_id: str
    strategy: FineTuningStrategy
    base_model: str
    learning_rate: float = 1e-4
    batch_size: int = 8
    epochs: int = 3
    warmup_steps: int = 100
    max_seq_length: int = 2048
    lora_rank: int = 16
    lora_alpha: int = 32
    quality_threshold: float = 0.7


@dataclass
class TrainingMetrics:
    epoch: int
    loss: float
    accuracy: float
    learning_rate: float
    step: int
    timestamp: float


@dataclass
class FineTuningResult:
    config_id: str
    dataset_id: str
    status: TrainingStatus
    metrics: List[TrainingMetrics]
    final_loss: float
    final_accuracy: float
    training_time: float
    model_path: Optional[str] = None
    warnings: List[str] = field(default_factory=list)


class LIMOFineTuning:
    """
    LIMO微调系统
    实现低数据量下的高效微调
    """
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key
        self.configs: Dict[str, FineTuningConfig] = {}
        self.datasets: Dict[str, Dataset] = {}
        self.results: Dict[str, FineTuningResult] = {}
    
    def create_dataset(self, dataset_id: str, name: str, 
                      examples: List[TrainingExample]) -> Dataset:
        """创建数据集"""
        quality_dist = {}
        for ex in examples:
            quality_dist[ex.quality] = quality_dist.get(ex.quality, 0) + 1
        
        dataset = Dataset(
            dataset_id=dataset_id,
            name=name,
            examples=examples,
            quality_distribution=quality_dist,
            created_at=time.time()
        )
        
        self.datasets[dataset_id] = dataset
        return dataset
    
    def create_config(self, config_id: str, strategy: FineTuningStrategy,
                     base_model: str, **kwargs) -> FineTuningConfig:
        """创建微调配置"""
        config = FineTuningConfig(
            config_id=config_id,
            strategy=strategy,
            base_model=base_model,
            learning_rate=kwargs.get("learning_rate", 1e-4),
            batch_size=kwargs.get("batch_size", 8),
            epochs=kwargs.get("epochs", 3),
            warmup_steps=kwargs.get("warmup_steps", 100),
            max_seq_length=kwargs.get("max_seq_length", 2048),
            lora_rank=kwargs.get("lora_rank", 16),
            lora_alpha=kwargs.get("lora_alpha", 32),
            quality_threshold=kwargs.get("quality_threshold", 0.7)
        )
        
        self.configs[config_id] = config
        return config
    
    async def prepare_dataset(self, dataset_id: str, 
                            config: FineTuningConfig) -> Dataset:
        """准备数据集（LIMO策略）"""
        if dataset_id not in self.datasets:
            raise ValueError(f"Dataset {dataset_id} not found")
        
        dataset = self.datasets[dataset_id]
        
        # LIMO策略：质量优先
        if config.strategy == FineTuningStrategy.LIMO:
            # 筛选高质量样本
            high_quality = [
                ex for ex in dataset.examples 
                if ex.quality in [DataQuality.HIGH, DataQuality.CURATED]
            ]
            
            if len(high_quality) < 10:
                # 如果高质量样本不足，添加中等质量样本
                medium_quality = [
                    ex for ex in dataset.examples 
                    if ex.quality == DataQuality.MEDIUM
                ]
                high_quality.extend(medium_quality[:10 - len(high_quality)])
            
            # 创建新数据集
            prepared_dataset = Dataset(
                dataset_id=f"{dataset_id}_prepared",
                name=f"{dataset.name} (LIMO prepared)",
                examples=high_quality,
                quality_distribution={ex.quality: 1 for ex in high_quality},
                created_at=time.time()
            )
            
            self.datasets[prepared_dataset.dataset_id] = prepared_dataset
            return prepared_dataset
        
        return dataset
    
    async def train(self, config_id: str, dataset_id: str) -> FineTuningResult:
        """训练模型"""
        if config_id not in self.configs:
            raise ValueError(f"Config {config_id} not found")
        
        if dataset_id not in self.datasets:
            raise ValueError(f"Dataset {dataset_id} not found")
        
        config = self.configs[config_id]
        dataset = self.datasets[dataset_id]
        
        start_time = time.time()
        metrics = []
        
        # 模拟训练过程
        for epoch in range(config.epochs):
            await asyncio.sleep(0.1)
            
            # 模拟指标
            loss = 1.0 / (epoch + 1)
            accuracy = 0.5 + (epoch * 0.15)
            
            metric = TrainingMetrics(
                epoch=epoch,
                loss=loss,
                accuracy=accuracy,
                learning_rate=config.learning_rate / (epoch + 1),
                step=epoch * 100,
                timestamp=time.time()
            )
            metrics.append(metric)
        
        training_time = time.time() - start_time
        
        result = FineTuningResult(
            config_id=config_id,
            dataset_id=dataset_id,
            status=TrainingStatus.COMPLETED,
            metrics=metrics,
            final_loss=metrics[-1].loss if metrics else 0,
            final_accuracy=metrics[-1].accuracy if metrics else 0,
            training_time=training_time,
            model_path=f"models/{config_id}_{dataset_id}",
            warnings=self._generate_warnings(dataset, config, metrics)
        )
        
        self.results[f"{config_id}_{dataset_id}"] = result
        return result
    
    def _generate_warnings(self, dataset: Dataset, config: FineTuningConfig,
                         metrics: List[TrainingMetrics]) -> List[str]:
        """生成警告"""
        warnings = []
        
        if len(dataset.examples) < 100:
            warnings.append("Small dataset size may limit performance")
        
        if config.strategy == FineTuningStrategy.LIMO:
            high_quality_count = dataset.quality_distribution.get(DataQuality.HIGH, 0)
            if high_quality_count < 10:
                warnings.append("Limited high-quality examples for LIMO")
        
        if metrics and metrics[-1].accuracy < 0.6:
            warnings.append("Low final accuracy achieved")
        
        return warnings
    
    async def evaluate(self, config_id: str, dataset_id: str, 
                      test_examples: List[TrainingExample]) -> Dict[str, Any]:
        """评估模型"""
        if f"{config_id}_{dataset_id}" not in self.results:
            raise ValueError("Model not trained yet")
        
        result = self.results[f"{config_id}_{dataset_id}"]
        
        # 模拟评估
        await asyncio.sleep(0.1)
        
        evaluation = {
            "config_id": config_id,
            "dataset_id": dataset_id,
            "test_examples": len(test_examples),
            "metrics": {
                "accuracy": result.final_accuracy,
                "loss": result.final_loss,
                "perplexity": 2 ** result.final_loss
            },
            "latency_ms": 50,
            "throughput": 20
        }
        
        return evaluation
    
    async def compare_strategies(self, dataset_id: str, 
                               base_model: str) -> Dict[str, Any]:
        """比较不同策略"""
        strategies = [
            FineTuningStrategy.LIMO,
            FineTuningStrategy.LORA,
            FineTuningStrategy.PROMPT_TUNING
        ]
        
        results = {}
        
        for strategy in strategies:
            config = self.create_config(
                config_id=f"compare_{strategy.value}",
                strategy=strategy,
                base_model=base_model
            )
            
            prepared_dataset = await self.prepare_dataset(dataset_id, config)
            result = await self.train(config.config_id, prepared_dataset.dataset_id)
            
            results[strategy.value] = {
                "final_accuracy": result.final_accuracy,
                "final_loss": result.final_loss,
                "training_time": result.training_time,
                "warnings": result.warnings
            }
        
        # 找出最佳策略
        best_strategy = max(results.items(), 
                          key=lambda x: x[1]["final_accuracy"])
        
        return {
            "results": results,
            "best_strategy": best_strategy[0],
            "best_accuracy": best_strategy[1]["final_accuracy"]
        }
    
    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            "total_configs": len(self.configs),
            "total_datasets": len(self.datasets),
            "total_trainings": len(self.results),
            "total_examples": sum(len(d.examples) for d in self.datasets.values()),
            "average_accuracy": (
                sum(r.final_accuracy for r in self.results.values()) / 
                len(self.results) if self.results else 0
            )
        }


_limo_finetuning_instance = None

def get_limo_finetuning(api_key: Optional[str] = None) -> LIMOFineTuning:
    global _limo_finetuning_instance
    if _limo_finetuning_instance is None:
        _limo_finetuning_instance = LIMOFineTuning(api_key)
    return _limo_finetuning_instance
