"""
Robin Architecture for Multi-Agent Scientific Discovery
Inspired by Robin's specialized multi-agent system
Features:
  - Specialized agent roles (Crow, Falcon, Finch)
  - Coordinated workflow
  - Task routing
  - Agent communication
  - Performance tracking
"""

from enum import Enum
from typing import Dict, Any, List, Optional, Callable
import asyncio
import json
import time
from dataclasses import dataclass, field


class AgentRole(Enum):
    CROW = "crow"  # Literature search (broad)
    FALCON = "falcon"  # Literature search (deep)
    FINCH = "finch"  # Data analysis
    ROBIN = "robin"  # Orchestration
    PHOENIX = "phoenix"  # Chemical synthesis
    OWL = "owl"  # Literature synthesis


class TaskType(Enum):
    LITERATURE_BROAD = "literature_broad"
    LITERATURE_DEEP = "literature_deep"
    DATA_ANALYSIS = "data_analysis"
    HYPOTHESIS_GENERATION = "hypothesis_generation"
    EXPERIMENT_DESIGN = "experiment_design"
    RESULT_INTERPRETATION = "result_interpretation"
    CHEMICAL_SYNTHESIS = "chemical_synthesis"


class AgentStatus(Enum):
    IDLE = "idle"
    BUSY = "busy"
    ERROR = "error"
    OFFLINE = "offline"


@dataclass
class AgentConfig:
    agent_id: str
    role: AgentRole
    name: str
    description: str
    capabilities: List[TaskType]
    model_id: str
    max_concurrent: int = 1
    timeout: float = 300.0


@dataclass
class Task:
    task_id: str
    task_type: TaskType
    description: str
    input_data: Dict[str, Any]
    priority: int = 0
    created_at: float = 0.0
    assigned_to: Optional[str] = None


@dataclass
class TaskResult:
    task_id: str
    agent_id: str
    success: bool
    output: Any
    execution_time: float
    error: Optional[str] = None


@dataclass
class WorkflowStep:
    step_id: str
    task_type: TaskType
    agent_role: AgentRole
    description: str
    dependencies: List[str] = field(default_factory=list)


@dataclass
class Workflow:
    workflow_id: str
    name: str
    description: str
    steps: List[WorkflowStep]
    created_at: float = 0.0


class RobinArchitecture:
    """
    Robin架构
    实现多智能体科学发现系统
    """
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key
        self.agents: Dict[str, AgentConfig] = {}
        self.agent_status: Dict[str, AgentStatus] = {}
        self.task_queue: List[Task] = []
        self.results: Dict[str, TaskResult] = {}
        self.workflows: Dict[str, Workflow] = {}
        self._initialize_agents()
    
    def _initialize_agents(self):
        """初始化智能体"""
        default_agents = [
            AgentConfig(
                agent_id="crow",
                role=AgentRole.CROW,
                name="Crow",
                description="Broad literature search agent",
                capabilities=[TaskType.LITERATURE_BROAD, TaskType.HYPOTHESIS_GENERATION],
                model_id="claude-sonnet-4-20250514"
            ),
            AgentConfig(
                agent_id="falcon",
                role=AgentRole.FALCON,
                name="Falcon",
                description="Deep literature search agent",
                capabilities=[TaskType.LITERATURE_DEEP, TaskType.EXPERIMENT_DESIGN],
                model_id="claude-sonnet-4-20250514"
            ),
            AgentConfig(
                agent_id="finch",
                role=AgentRole.FINCH,
                name="Finch",
                description="Data analysis agent",
                capabilities=[TaskType.DATA_ANALYSIS, TaskType.RESULT_INTERPRETATION],
                model_id="claude-sonnet-4-20250514"
            ),
            AgentConfig(
                agent_id="robin",
                role=AgentRole.ROBIN,
                name="Robin",
                description="Orchestration agent",
                capabilities=[TaskType.HYPOTHESIS_GENERATION, TaskType.EXPERIMENT_DESIGN],
                model_id="claude-sonnet-4-20250514"
            ),
            AgentConfig(
                agent_id="phoenix",
                role=AgentRole.PHOENIX,
                name="Phoenix",
                description="Chemical synthesis agent",
                capabilities=[TaskType.CHEMICAL_SYNTHESIS],
                model_id="claude-sonnet-4-20250514"
            )
        ]
        
        for agent in default_agents:
            self.agents[agent.agent_id] = agent
            self.agent_status[agent.agent_id] = AgentStatus.IDLE
    
    def register_agent(self, config: AgentConfig):
        """注册智能体"""
        self.agents[config.agent_id] = config
        self.agent_status[config.agent_id] = AgentStatus.IDLE
    
    def register_workflow(self, workflow: Workflow):
        """注册工作流"""
        self.workflows[workflow.workflow_id] = workflow
    
    async def execute_task(self, task: Task) -> TaskResult:
        """执行任务"""
        start_time = time.time()
        
        # 查找合适的智能体
        agent_id = self._find_agent_for_task(task)
        
        if not agent_id:
            return TaskResult(
                task_id=task.task_id,
                agent_id="",
                success=False,
                output=None,
                execution_time=time.time() - start_time,
                error="No suitable agent found"
            )
        
        # 更新状态
        self.agent_status[agent_id] = AgentStatus.BUSY
        task.assigned_to = agent_id
        
        try:
            # 执行任务
            output = await self._execute_on_agent(agent_id, task)
            
            result = TaskResult(
                task_id=task.task_id,
                agent_id=agent_id,
                success=True,
                output=output,
                execution_time=time.time() - start_time
            )
        except Exception as e:
            result = TaskResult(
                task_id=task.task_id,
                agent_id=agent_id,
                success=False,
                output=None,
                execution_time=time.time() - start_time,
                error=str(e)
            )
        finally:
            self.agent_status[agent_id] = AgentStatus.IDLE
        
        self.results[task.task_id] = result
        return result
    
    def _find_agent_for_task(self, task: Task) -> Optional[str]:
        """查找适合任务的智能体"""
        for agent_id, config in self.agents.items():
            if task.task_type in config.capabilities:
                if self.agent_status[agent_id] == AgentStatus.IDLE:
                    return agent_id
        return None
    
    async def _execute_on_agent(self, agent_id: str, task: Task) -> Any:
        """在智能体上执行任务"""
        agent = self.agents[agent_id]
        
        # 模拟执行
        await asyncio.sleep(0.1)
        
        return {
            "agent": agent.name,
            "role": agent.role.value,
            "task_type": task.task_type.value,
            "output": f"Task completed by {agent.name}",
            "timestamp": time.time()
        }
    
    async def execute_workflow(self, workflow_id: str, 
                             input_data: Dict[str, Any]) -> Dict[str, Any]:
        """执行工作流"""
        if workflow_id not in self.workflows:
            return {"error": f"Workflow {workflow_id} not found"}
        
        workflow = self.workflows[workflow_id]
        results = {}
        current_data = input_data
        
        # 按依赖顺序执行
        executed_steps = set()
        
        for step in workflow.steps:
            # 检查依赖
            if not all(dep in executed_steps for dep in step.dependencies):
                continue
            
            # 创建任务
            task = Task(
                task_id=f"{workflow_id}_{step.step_id}",
                task_type=step.task_type,
                description=step.description,
                input_data=current_data,
                created_at=time.time()
            )
            
            # 执行任务
            result = await self.execute_task(task)
            results[step.step_id] = result
            
            if result.success:
                current_data = {**current_data, **(result.output or {})}
                executed_steps.add(step.step_id)
        
        return {
            "workflow_id": workflow_id,
            "results": results,
            "final_output": current_data,
            "completed_steps": len(executed_steps),
            "total_steps": len(workflow.steps)
        }
    
    async def coordinate_agents(self, task_description: str, 
                              context: Dict[str, Any]) -> Dict[str, Any]:
        """协调多个智能体"""
        
        # Crow: 广泛文献搜索
        crow_task = Task(
            task_id="crow_search",
            task_type=TaskType.LITERATURE_BROAD,
            description=f"Broad literature search for: {task_description}",
            input_data=context,
            created_at=time.time()
        )
        crow_result = await self.execute_task(crow_task)
        
        # Falcon: 深入文献搜索
        falcon_task = Task(
            task_id="falcon_search",
            task_type=TaskType.LITERATURE_DEEP,
            description=f"Deep literature search for: {task_description}",
            input_data={**context, "literature": crow_result.output if crow_result.success else {}},
            created_at=time.time()
        )
        falcon_result = await self.execute_task(falcon_task)
        
        # Finch: 数据分析
        finch_task = Task(
            task_id="finch_analysis",
            task_type=TaskType.DATA_ANALYSIS,
            description=f"Analyze data for: {task_description}",
            input_data={**context, "literature": falcon_result.output if falcon_result.success else {}},
            created_at=time.time()
        )
        finch_result = await self.execute_task(finch_task)
        
        # Robin: 综合
        robin_task = Task(
            task_id="robin_synthesis",
            task_type=TaskType.HYPOTHESIS_GENERATION,
            description=f"Synthesize findings for: {task_description}",
            input_data={
                **context,
                "crow_output": crow_result.output,
                "falcon_output": falcon_result.output,
                "finch_output": finch_result.output
            },
            created_at=time.time()
        )
        robin_result = await self.execute_task(robin_task)
        
        return {
            "task": task_description,
            "agents_used": ["crow", "falcon", "finch", "robin"],
            "results": {
                "literature_broad": crow_result.output,
                "literature_deep": falcon_result.output,
                "data_analysis": finch_result.output,
                "synthesis": robin_result.output
            },
            "success": all([crow_result.success, falcon_result.success, 
                          finch_result.success, robin_result.success])
        }
    
    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        total_tasks = len(self.results)
        successful_tasks = sum(1 for r in self.results.values() if r.success)
        
        agent_usage = {}
        for result in self.results.values():
            agent_usage[result.agent_id] = agent_usage.get(result.agent_id, 0) + 1
        
        return {
            "total_agents": len(self.agents),
            "total_tasks": total_tasks,
            "successful_tasks": successful_tasks,
            "success_rate": successful_tasks / total_tasks if total_tasks > 0 else 0,
            "agent_usage": agent_usage,
            "workflows": len(self.workflows)
        }


_robin_architecture_instance = None

def get_robin_architecture(api_key: Optional[str] = None) -> RobinArchitecture:
    global _robin_architecture_instance
    if _robin_architecture_instance is None:
        _robin_architecture_instance = RobinArchitecture(api_key)
    return _robin_architecture_instance
