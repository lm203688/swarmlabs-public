"""
Lab-in-the-Loop Integration System
Inspired by Robin's lab-in-the-loop framework for experimental validation
Features:
  - Experimental protocol generation
  - Hardware abstraction
  - Result interpretation
  - Iterative refinement
  - Safety guardrails
"""

from enum import Enum
from typing import Dict, Any, List, Optional
import asyncio
import json
import time
from dataclasses import dataclass, field


class ExperimentType(Enum):
    IN_VITRO = "in_vitro"
    IN_VIVO = "in_vivo"
    COMPUTATIONAL = "computational"
    HIGH_THROUGHPUT = "high_throughput"
    SEQUENCING = "sequencing"


class HardwareType(Enum):
    PLATE_READER = "plate_reader"
    FLOW_CYTOMETER = "flow_cytometer"
    MICROSCOPE = "microscope"
    SEQUENCER = "sequencer"
    ROBOT = "robot"
    CUSTOM = "custom"


class ExperimentStatus(Enum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"


@dataclass
class ExperimentalProtocol:
    protocol_id: str
    title: str
    description: str
    experiment_type: ExperimentType
    hardware_requirements: List[HardwareType]
    steps: List[Dict[str, Any]]
    safety_notes: List[str]
    estimated_duration: float
    estimated_cost: float
    proposed_by: str = "ai_system"


@dataclass
class ExperimentalResult:
    experiment_id: str
    protocol_id: str
    status: ExperimentStatus
    raw_data: Dict[str, Any]
    processed_data: Dict[str, Any]
    interpretation: str
    confidence: float
    issues: List[str]
    timestamp: float


@dataclass
class RefinementIteration:
    iteration_id: int
    hypothesis: str
    protocol: ExperimentalProtocol
    result: Optional[ExperimentalResult]
    feedback: str
    timestamp: float


class LabInTheLoop:
    """
    实验室集成系统
    实现实验验证的自动化循环
    """
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key
        self.protocols: Dict[str, ExperimentalProtocol] = {}
        self.results: Dict[str, ExperimentalResult] = {}
        self.iterations: List[RefinementIteration] = []
        self.safety_guardrails = self._initialize_safety_guardrails()
        
    def _initialize_safety_guardrails(self) -> Dict[str, Any]:
        """初始化安全护栏"""
        return {
            "max_iterations": 10,
            "required_approvals": ["safety_review", "scientific_merit"],
            "prohibited_substances": ["toxic化学品", "高致病性病原体"],
            "max_risk_level": "moderate",
            "human Oversight": True
        }
    
    async def propose_experiment(self, hypothesis: str, 
                               context: Dict[str, Any]) -> ExperimentalProtocol:
        """提出实验方案"""
        
        # 生成实验方案
        protocol = ExperimentalProtocol(
            protocol_id=f"proto_{int(time.time())}",
            title=f"Validation experiment for: {hypothesis[:50]}",
            description=f"Experimental validation of: {hypothesis}",
            experiment_type=self._determine_experiment_type(context),
            hardware_requirements=self._determine_hardware_requirements(context),
            steps=self._generate_experiment_steps(hypothesis, context),
            safety_notes=self._generate_safety_notes(hypothesis, context),
            estimated_duration=self._estimate_duration(context),
            estimated_cost=self._estimate_cost(context)
        )
        
        self.protocols[protocol.protocol_id] = protocol
        return protocol
    
    def _determine_experiment_type(self, context: Dict[str, Any]) -> ExperimentType:
        """确定实验类型"""
        if "cell_culture" in context:
            return ExperimentType.IN_VITRO
        elif "animal_model" in context:
            return ExperimentType.IN_VIVO
        elif "computational" in context:
            return ExperimentType.COMPUTATIONAL
        return ExperimentType.IN_VITRO
    
    def _determine_hardware_requirements(self, context: Dict[str, Any]) -> List[HardwareType]:
        """确定硬件需求"""
        requirements = []
        
        if "fluorescence" in context:
            requirements.append(HardwareType.MICROSCOPE)
        if "cell_counting" in context:
            requirements.append(HardwareType.FLOW_CYTOMETER)
        if "absorbance" in context:
            requirements.append(HardwareType.PLATE_READER)
        if "high_throughput" in context:
            requirements.append(HardwareType.ROBOT)
        
        if not requirements:
            requirements.append(HardwareType.PLATE_READER)
        
        return requirements
    
    def _generate_experiment_steps(self, hypothesis: str, 
                                 context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """生成实验步骤"""
        steps = [
            {"step": 1, "description": "Prepare reagents and materials", "duration": 30},
            {"step": 2, "description": "Set up experimental conditions", "duration": 15},
            {"step": 3, "description": "Conduct experiment", "duration": 60},
            {"step": 4, "description": "Collect data", "duration": 15},
            {"step": 5, "description": "Clean up and store samples", "duration": 15}
        ]
        return steps
    
    def _generate_safety_notes(self, hypothesis: str, 
                             context: Dict[str, Any]) -> List[str]:
        """生成安全注意事项"""
        notes = [
            "Wear appropriate PPE",
            "Follow institutional safety protocols",
            "Dispose of waste properly"
        ]
        
        if "chemical" in context:
            notes.append("Handle chemicals in fume hood")
        if "biological" in context:
            notes.append("Use biosafety cabinet for biological materials")
        
        return notes
    
    def _estimate_duration(self, context: Dict[str, Any]) -> float:
        """估计持续时间（小时）"""
        return 2.0
    
    def _estimate_cost(self, context: Dict[str, Any]) -> float:
        """估计成本（美元）"""
        return 500.0
    
    async def validate_protocol(self, protocol: ExperimentalProtocol) -> Dict[str, Any]:
        """验证实验方案"""
        
        validation = {
            "protocol_id": protocol.protocol_id,
            "safety_check": self._check_safety(protocol),
            "feasibility_check": self._check_feasibility(protocol),
            "cost_check": self._check_cost(protocol),
            "overall_status": "approved"
        }
        
        # 检查安全护栏
        if not validation["safety_check"]["passed"]:
            validation["overall_status"] = "rejected"
        elif not validation["feasibility_check"]["passed"]:
            validation["overall_status"] = "needs_revision"
        
        return validation
    
    def _check_safety(self, protocol: ExperimentalProtocol) -> Dict[str, Any]:
        """安全检查"""
        issues = []
        
        # 检查禁止物质
        for note in protocol.safety_notes:
            for prohibited in self.safety_guardrails["prohibited_substances"]:
                if prohibited in note.lower():
                    issues.append(f"Safety concern: {prohibited}")
        
        return {
            "passed": len(issues) == 0,
            "issues": issues
        }
    
    def _check_feasibility(self, protocol: ExperimentalProtocol) -> Dict[str, Any]:
        """可行性检查"""
        issues = []
        
        # 检查硬件需求
        available_hardware = [HardwareType.PLATE_READER, HardwareType.FLOW_CYTOMETER]
        for hw in protocol.hardware_requirements:
            if hw not in available_hardware:
                issues.append(f"Hardware not available: {hw.value}")
        
        return {
            "passed": len(issues) == 0,
            "issues": issues
        }
    
    def _check_cost(self, protocol: ExperimentalProtocol) -> Dict[str, Any]:
        """成本检查"""
        budget_limit = 10000
        return {
            "passed": protocol.estimated_cost <= budget_limit,
            "estimated_cost": protocol.estimated_cost,
            "budget_limit": budget_limit
        }
    
    async def conduct_experiment(self, protocol: ExperimentalProtocol) -> ExperimentalResult:
        """进行实验（模拟）"""
        await asyncio.sleep(0.1)
        
        # 模拟实验结果
        raw_data = {
            "measurements": [1.0, 1.2, 0.9, 1.1, 1.0],
            "controls": [1.0, 1.0, 1.0],
            "timestamp": time.time()
        }
        
        processed_data = {
            "mean": sum(raw_data["measurements"]) / len(raw_data["measurements"]),
            "std": 0.1,
            "fold_change": 1.1,
            "p_value": 0.05
        }
        
        result = ExperimentalResult(
            experiment_id=f"exp_{int(time.time())}",
            protocol_id=protocol.protocol_id,
            status=ExperimentStatus.COMPLETED,
            raw_data=raw_data,
            processed_data=processed_data,
            interpretation="Experiment completed successfully with positive results",
            confidence=0.8,
            issues=[],
            timestamp=time.time()
        )
        
        self.results[result.experiment_id] = result
        return result
    
    async def interpret_results(self, result: ExperimentalResult, 
                              hypothesis: str) -> Dict[str, Any]:
        """解释实验结果"""
        
        interpretation = {
            "hypothesis": hypothesis,
            "experiment_id": result.experiment_id,
            "supports_hypothesis": result.processed_data.get("fold_change", 1.0) > 1.0,
            "confidence": result.confidence,
            "key_findings": [
                f"Fold change: {result.processed_data.get('fold_change', 0):.2f}",
                f"P-value: {result.processed_data.get('p_value', 1.0):.3f}"
            ],
            "recommendations": self._generate_recommendations(result)
        }
        
        return interpretation
    
    def _generate_recommendations(self, result: ExperimentalResult) -> List[str]:
        """生成建议"""
        recommendations = []
        
        if result.processed_data.get("p_value", 1.0) > 0.05:
            recommendations.append("Consider increasing sample size for better statistical power")
        
        if result.processed_data.get("fold_change", 1.0) < 1.5:
            recommendations.append("Effect size is modest, consider optimizing conditions")
        
        recommendations.append("Repeat experiment to confirm results")
        
        return recommendations
    
    async def refine_hypothesis(self, original_hypothesis: str, 
                              result: ExperimentalResult) -> str:
        """基于结果优化假设"""
        
        # Calculate if hypothesis is supported based on processed data
        fold_change = result.processed_data.get("fold_change", 1.0)
        p_value = result.processed_data.get("p_value", 1.0)
        supports_hypothesis = fold_change > 1.0 and p_value < 0.05
        
        if supports_hypothesis:
            return f"Enhanced: {original_hypothesis} (supported by experimental evidence)"
        else:
            return f"Revised: {original_hypothesis} (requires modification based on results)"
    
    async def iterate(self, hypothesis: str, context: Dict[str, Any], 
                    max_iterations: int = 3) -> List[RefinementIteration]:
        """执行迭代循环"""
        
        iterations = []
        current_hypothesis = hypothesis
        
        for i in range(max_iterations):
            # 提出实验
            protocol = await self.propose_experiment(current_hypothesis, context)
            
            # 验证方案
            validation = await self.validate_protocol(protocol)
            
            if validation["overall_status"] != "approved":
                break
            
            # 进行实验
            result = await self.conduct_experiment(protocol)
            
            # 解释结果
            interpretation = await self.interpret_results(result, current_hypothesis)
            
            # 记录迭代
            iteration = RefinementIteration(
                iteration_id=i,
                hypothesis=current_hypothesis,
                protocol=protocol,
                result=result,
                feedback=interpretation.get("recommendations", ["Continue"])[0],
                timestamp=time.time()
            )
            iterations.append(iteration)
            
            # 优化假设
            current_hypothesis = await self.refine_hypothesis(current_hypothesis, result)
        
        self.iterations.extend(iterations)
        return iterations
    
    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            "total_protocols": len(self.protocols),
            "total_experiments": len(self.results),
            "total_iterations": len(self.iterations),
            "completed_experiments": sum(1 for r in self.results.values() if r.status == ExperimentStatus.COMPLETED),
            "success_rate": sum(1 for r in self.results.values() if r.status == ExperimentStatus.COMPLETED) / len(self.results) if self.results else 0
        }


_lab_in_loop_instance = None

def get_lab_in_loop(api_key: Optional[str] = None) -> LabInTheLoop:
    global _lab_in_loop_instance
    if _lab_in_loop_instance is None:
        _lab_in_loop_instance = LabInTheLoop(api_key)
    return _lab_in_loop_instance
