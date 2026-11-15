"""
Hybrid Pipeline for Method Routing
Inspired by Robin's approach of combining different analysis methods
Features:
  - Dynamic method selection
  - Pipeline composition
  - Result fusion
  - Quality assessment
  - Fallback mechanisms
"""

from enum import Enum
from typing import Dict, Any, List, Optional, Callable, Any
import asyncio
import json
import time
from dataclasses import dataclass, field


class PipelineStage(Enum):
    DATA_COLLECTION = "data_collection"
    PREPROCESSING = "preprocessing"
    ANALYSIS = "analysis"
    VALIDATION = "validation"
    SYNTHESIS = "synthesis"
    REPORTING = "reporting"


class MethodType(Enum):
    LLM_BASED = "llb_based"
    STATISTICAL = "statistical"
    MACHINE_LEARNING = "machine_learning"
    HYBRID = "hybrid"
    DOMAIN_SPECIFIC = "domain_specific"


class QualityLevel(Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


@dataclass
class PipelineStep:
    step_id: str
    stage: PipelineStage
    method_type: MethodType
    name: str
    description: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    dependencies: List[str] = field(default_factory=list)
    timeout: float = 300.0
    retry_count: int = 3


@dataclass
class StepResult:
    step_id: str
    success: bool
    output: Any
    quality: QualityLevel
    execution_time: float
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineConfig:
    pipeline_id: str
    name: str
    description: str
    steps: List[PipelineStep]
    max_concurrent: int = 3
    timeout: float = 3600.0
    quality_threshold: float = 0.7


@dataclass
class PipelineResult:
    pipeline_id: str
    success: bool
    step_results: Dict[str, StepResult]
    final_output: Any
    total_execution_time: float
    quality_score: float
    warnings: List[str]


class HybridPipeline:
    """
    混合管道
    动态选择和组合不同的分析方法
    """
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key
        self.pipelines: Dict[str, PipelineConfig] = {}
        self.step_executors: Dict[str, Callable] = {}
        self.execution_history: List[PipelineResult] = []
        self._register_default_executors()
    
    def _register_default_executors(self):
        """注册默认执行器"""
        self.step_executors = {
            "literature_search": self._execute_literature_search,
            "data_retrieval": self._execute_data_retrieval,
            "hypothesis_generation": self._execute_hypothesis_generation,
            "hypothesis_validation": self._execute_hypothesis_validation,
            "data_analysis": self._execute_data_analysis,
            "result_synthesis": self._execute_result_synthesis,
            "report_generation": self._execute_report_generation
        }
    
    def register_pipeline(self, config: PipelineConfig):
        """注册管道配置"""
        self.pipelines[config.pipeline_id] = config
    
    def register_executor(self, step_name: str, executor: Callable):
        """注册步骤执行器"""
        self.step_executors[step_name] = executor
    
    async def execute_pipeline(self, pipeline_id: str, 
                             input_data: Dict[str, Any]) -> PipelineResult:
        """执行管道"""
        start_time = time.time()
        
        if pipeline_id not in self.pipelines:
            return PipelineResult(
                pipeline_id=pipeline_id,
                success=False,
                step_results={},
                final_output=None,
                total_execution_time=0,
                quality_score=0,
                warnings=[f"Pipeline {pipeline_id} not found"]
            )
        
        config = self.pipelines[pipeline_id]
        step_results = {}
        current_data = input_data
        
        # 拓扑排序执行步骤
        execution_order = self._topological_sort(config.steps)
        
        for step in execution_order:
            # 检查依赖
            if not self._check_dependencies(step, step_results):
                step_results[step.step_id] = StepResult(
                    step_id=step.step_id,
                    success=False,
                    output=None,
                    quality=QualityLevel.UNKNOWN,
                    execution_time=0,
                    error="Dependencies not met"
                )
                continue
            
            # 执行步骤
            result = await self._execute_step(step, current_data)
            step_results[step.step_id] = result
            
            # 更新数据流
            if result.success:
                current_data = self._merge_results(current_data, result.output)
            else:
                # 处理失败
                if step.retry_count > 0:
                    # 重试
                    result = await self._execute_step_with_retry(step, current_data, step.retry_count)
                    step_results[step.step_id] = result
                    if result.success:
                        current_data = self._merge_results(current_data, result.output)
        
        # 计算总体质量
        quality_score = self._calculate_overall_quality(step_results)
        
        total_time = time.time() - start_time
        
        result = PipelineResult(
            pipeline_id=pipeline_id,
            success=all(r.success for r in step_results.values()),
            step_results=step_results,
            final_output=current_data,
            total_execution_time=total_time,
            quality_score=quality_score,
            warnings=self._generate_warnings(step_results, quality_score)
        )
        
        self.execution_history.append(result)
        return result
    
    def _topological_sort(self, steps: List[PipelineStep]) -> List[PipelineStep]:
        """拓扑排序"""
        step_map = {s.step_id: s for s in steps}
        in_degree = {s.step_id: 0 for s in steps}
        adj = {s.step_id: [] for s in steps}
        
        for step in steps:
            for dep in step.dependencies:
                if dep in step_map:
                    adj[dep].append(step.step_id)
                    in_degree[step.step_id] += 1
        
        queue = [sid for sid, deg in in_degree.items() if deg == 0]
        sorted_steps = []
        
        while queue:
            sid = queue.pop(0)
            sorted_steps.append(step_map[sid])
            
            for neighbor in adj[sid]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)
        
        return sorted_steps
    
    def _check_dependencies(self, step: PipelineStep, 
                          results: Dict[str, StepResult]) -> bool:
        """检查依赖"""
        for dep in step.dependencies:
            if dep not in results or not results[dep].success:
                return False
        return True
    
    async def _execute_step(self, step: PipelineStep, 
                          input_data: Dict[str, Any]) -> StepResult:
        """执行单个步骤"""
        start_time = time.time()
        
        executor = self.step_executors.get(step.name)
        if not executor:
            return StepResult(
                step_id=step.step_id,
                success=False,
                output=None,
                quality=QualityLevel.UNKNOWN,
                execution_time=time.time() - start_time,
                error=f"No executor for step: {step.name}"
            )
        
        try:
            output = await asyncio.wait_for(
                executor(input_data, step.parameters),
                timeout=step.timeout
            )
            
            quality = self._assess_output_quality(output)
            
            return StepResult(
                step_id=step.step_id,
                success=True,
                output=output,
                quality=quality,
                execution_time=time.time() - start_time
            )
        except asyncio.TimeoutError:
            return StepResult(
                step_id=step.step_id,
                success=False,
                output=None,
                quality=QualityLevel.UNKNOWN,
                execution_time=time.time() - start_time,
                error=f"Step timed out after {step.timeout}s"
            )
        except Exception as e:
            return StepResult(
                step_id=step.step_id,
                success=False,
                output=None,
                quality=QualityLevel.UNKNOWN,
                execution_time=time.time() - start_time,
                error=str(e)
            )
    
    async def _execute_step_with_retry(self, step: PipelineStep, 
                                     input_data: Dict[str, Any],
                                     retries: int) -> StepResult:
        """带重试的步骤执行"""
        for attempt in range(retries):
            result = await self._execute_step(step, input_data)
            if result.success:
                return result
            await asyncio.sleep(1 * (attempt + 1))  # 指数退避
        
        return result  # 返回最后一次失败结果
    
    def _merge_results(self, current_data: Dict[str, Any], 
                      new_output: Any) -> Dict[str, Any]:
        """合并结果"""
        if isinstance(new_output, dict):
            return {**current_data, **new_output}
        return current_data
    
    def _assess_output_quality(self, output: Any) -> QualityLevel:
        """评估输出质量"""
        if output is None:
            return QualityLevel.UNKNOWN
        if isinstance(output, dict):
            if len(output) > 5:
                return QualityLevel.HIGH
            elif len(output) > 0:
                return QualityLevel.MEDIUM
        return QualityLevel.MEDIUM
    
    def _calculate_overall_quality(self, results: Dict[str, StepResult]) -> float:
        """计算总体质量"""
        if not results:
            return 0.0
        
        quality_scores = {
            QualityLevel.HIGH: 1.0,
            QualityLevel.MEDIUM: 0.7,
            QualityLevel.LOW: 0.4,
            QualityLevel.UNKNOWN: 0.0
        }
        
        scores = [quality_scores.get(r.quality, 0) for r in results.values()]
        return sum(scores) / len(scores) if scores else 0.0
    
    def _generate_warnings(self, results: Dict[str, StepResult], 
                         quality_score: float) -> List[str]:
        """生成警告"""
        warnings = []
        
        if quality_score < 0.7:
            warnings.append(f"Overall quality score is low: {quality_score:.2f}")
        
        failed_steps = [sid for sid, r in results.items() if not r.success]
        if failed_steps:
            warnings.append(f"Failed steps: {', '.join(failed_steps)}")
        
        slow_steps = [sid for sid, r in results.items() if r.execution_time > 60]
        if slow_steps:
            warnings.append(f"Slow steps detected: {', '.join(slow_steps)}")
        
        return warnings
    
    # 默认执行器实现
    async def _execute_literature_search(self, input_data: Dict[str, Any], 
                                       params: Dict[str, Any]) -> Dict[str, Any]:
        """文献检索执行器"""
        await asyncio.sleep(0.1)
        return {
            "literature_results": [
                {"title": "Paper 1", "relevance": 0.9},
                {"title": "Paper 2", "relevance": 0.8}
            ],
            "search_quality": "high"
        }
    
    async def _execute_data_retrieval(self, input_data: Dict[str, Any], 
                                    params: Dict[str, Any]) -> Dict[str, Any]:
        """数据检索执行器"""
        await asyncio.sleep(0.1)
        return {
            "datasets": [
                {"id": "DS1", "type": "transcriptomics", "samples": 100},
                {"id": "DS2", "type": "proteomics", "samples": 50}
            ],
            "retrieval_quality": "medium"
        }
    
    async def _execute_hypothesis_generation(self, input_data: Dict[str, Any], 
                                           params: Dict[str, Any]) -> Dict[str, Any]:
        """假设生成执行器"""
        await asyncio.sleep(0.1)
        return {
            "hypotheses": [
                {"statement": "Hypothesis 1", "confidence": 0.8},
                {"statement": "Hypothesis 2", "confidence": 0.7}
            ],
            "generation_quality": "high"
        }
    
    async def _execute_hypothesis_validation(self, input_data: Dict[str, Any], 
                                           params: Dict[str, Any]) -> Dict[str, Any]:
        """假设验证执行器"""
        await asyncio.sleep(0.1)
        return {
            "validated_hypotheses": [
                {"statement": "Hypothesis 1", "validation_score": 0.85}
            ],
            "validation_quality": "high"
        }
    
    async def _execute_data_analysis(self, input_data: Dict[str, Any], 
                                   params: Dict[str, Any]) -> Dict[str, Any]:
        """数据分析执行器"""
        await asyncio.sleep(0.1)
        return {
            "analysis_results": {
                "significant_genes": 15,
                "pathways": ["Pathway 1", "Pathway 2"]
            },
            "analysis_quality": "high"
        }
    
    async def _execute_result_synthesis(self, input_data: Dict[str, Any], 
                                      params: Dict[str, Any]) -> Dict[str, Any]:
        """结果综合执行器"""
        await asyncio.sleep(0.1)
        return {
            "synthesis": {
                "key_findings": ["Finding 1", "Finding 2"],
                "confidence": 0.8
            },
            "synthesis_quality": "high"
        }
    
    async def _execute_report_generation(self, input_data: Dict[str, Any], 
                                       params: Dict[str, Any]) -> Dict[str, Any]:
        """报告生成执行器"""
        await asyncio.sleep(0.1)
        return {
            "report": {
                "title": "Analysis Report",
                "sections": ["Introduction", "Methods", "Results", "Discussion"]
            },
            "report_quality": "high"
        }
    
    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            "total_pipelines": len(self.pipelines),
            "total_executions": len(self.execution_history),
            "average_quality": sum(r.quality_score for r in self.execution_history) / len(self.execution_history) if self.execution_history else 0,
            "success_rate": sum(1 for r in self.execution_history if r.success) / len(self.execution_history) if self.execution_history else 0
        }


_hybrid_pipeline_instance = None

def get_hybrid_pipeline(api_key: Optional[str] = None) -> HybridPipeline:
    global _hybrid_pipeline_instance
    if _hybrid_pipeline_instance is None:
        _hybrid_pipeline_instance = HybridPipeline(api_key)
    return _hybrid_pipeline_instance
