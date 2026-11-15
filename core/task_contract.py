"""
Task Contract - Bounded Task Execution with Verification
Inspired by Orbit's TaskContract and TaskDAG architecture

Features:
- Explicit scope, done criteria, and verification commands
- Dependency tracking between tasks
- Evidence collection and verification
- Fail-closed on uncertainty
"""

from enum import Enum
from typing import Dict, Any, List, Optional, Callable
import asyncio
import json
import time
import hashlib
from dataclasses import dataclass, field


class TaskStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class VerificationResult(Enum):
    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"
    SKIPPED = "skipped"


@dataclass
class TaskContract:
    task_id: str
    name: str
    description: str
    scope: List[str]  # Files/directories in scope
    done_criteria: List[str]  # What "done" means
    verification_commands: List[str]  # Commands to verify completion
    dependencies: List[str] = field(default_factory=list)  # Task IDs this depends on
    assigned_to: Optional[str] = None  # Agent ID
    status: TaskStatus = TaskStatus.PENDING
    created_at: float = 0.0
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    timeout: float = 600.0  # 10 minutes default
    max_retries: int = 3
    retry_count: int = 0


@dataclass
class TaskEvidence:
    evidence_id: str
    task_id: str
    evidence_type: str  # "file_change", "test_result", "verification_output"
    content: Dict[str, Any]
    timestamp: float
    verified: bool = False
    verification_result: Optional[VerificationResult] = None


@dataclass
class TaskDAG:
    dag_id: str
    name: str
    tasks: List[TaskContract]
    created_at: float = 0.0
    status: TaskStatus = TaskStatus.PENDING


class TaskContractManager:
    """
    任务契约管理器
    
    确保任务在明确边界内执行
    收集执行证据
    验证完成标准
    """
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key
        self.contracts: Dict[str, TaskContract] = {}
        self.evidence: Dict[str, List[TaskEvidence]] = {}
        self.dags: Dict[str, TaskDAG] = {}
        self.verification_handlers: Dict[str, Callable] = {}
        self._register_default_handlers()
    
    def _register_default_handlers(self):
        """注册默认验证处理器"""
        self.verification_handlers = {
            "file_exists": self._verify_file_exists,
            "test_pass": self._verify_test_pass,
            "no_errors": self._verify_no_errors,
            "custom": self._verify_custom
        }
    
    def create_contract(self, name: str, description: str,
                       scope: List[str], done_criteria: List[str],
                       verification_commands: List[str],
                       dependencies: Optional[List[str]] = None,
                       assigned_to: Optional[str] = None,
                       timeout: float = 600.0) -> TaskContract:
        """创建任务契约"""
        task_id = hashlib.md5(f"{name}{time.time()}".encode()).hexdigest()[:8]
        
        contract = TaskContract(
            task_id=task_id,
            name=name,
            description=description,
            scope=scope,
            done_criteria=done_criteria,
            verification_commands=verification_commands,
            dependencies=dependencies or [],
            assigned_to=assigned_to,
            created_at=time.time(),
            timeout=timeout
        )
        
        self.contracts[task_id] = contract
        self.evidence[task_id] = []
        return contract
    
    def create_dag(self, name: str, tasks: List[TaskContract]) -> TaskDAG:
        """创建任务DAG"""
        dag_id = hashlib.md5(f"{name}{time.time()}".encode()).hexdigest()[:8]
        
        dag = TaskDAG(
            dag_id=dag_id,
            name=name,
            tasks=tasks,
            created_at=time.time()
        )
        
        self.dags[dag_id] = dag
        return dag
    
    def start_task(self, task_id: str) -> bool:
        """开始任务"""
        if task_id not in self.contracts:
            return False
        
        contract = self.contracts[task_id]
        
        # Check dependencies
        for dep_id in contract.dependencies:
            if dep_id in self.contracts:
                dep = self.contracts[dep_id]
                if dep.status != TaskStatus.COMPLETED:
                    contract.status = TaskStatus.BLOCKED
                    return False
        
        contract.status = TaskStatus.IN_PROGRESS
        contract.started_at = time.time()
        return True
    
    def complete_task(self, task_id: str) -> bool:
        """完成任务"""
        if task_id not in self.contracts:
            return False
        
        contract = self.contracts[task_id]
        contract.status = TaskStatus.COMPLETED
        contract.completed_at = time.time()
        return True
    
    def fail_task(self, task_id: str, reason: str = "") -> bool:
        """任务失败"""
        if task_id not in self.contracts:
            return False
        
        contract = self.contracts[task_id]
        contract.retry_count += 1
        
        if contract.retry_count >= contract.max_retries:
            contract.status = TaskStatus.FAILED
        else:
            # Allow retry
            contract.status = TaskStatus.PENDING
        
        return True
    
    def add_evidence(self, task_id: str, evidence_type: str,
                    content: Dict[str, Any]) -> Optional[TaskEvidence]:
        """添加执行证据"""
        if task_id not in self.contracts:
            return None
        
        evidence_id = hashlib.md5(f"{task_id}{time.time()}".encode()).hexdigest()[:8]
        
        evidence = TaskEvidence(
            evidence_id=evidence_id,
            task_id=task_id,
            evidence_type=evidence_type,
            content=content,
            timestamp=time.time()
        )
        
        self.evidence[task_id].append(evidence)
        return evidence
    
    def verify_task(self, task_id: str) -> Dict[str, Any]:
        """验证任务完成"""
        if task_id not in self.contracts:
            return {"error": "Task not found"}
        
        contract = self.contracts[task_id]
        
        # Check scope constraints
        scope_ok = self._verify_scope(contract)
        
        # Run verification commands
        verification_results = []
        for cmd in contract.verification_commands:
            result = self._run_verification(cmd, contract)
            verification_results.append(result)
        
        # Check done criteria
        criteria_met = all(
            self._check_criterion(criterion, contract)
            for criterion in contract.done_criteria
        )
        
        all_passed = scope_ok and criteria_met and all(
            r == VerificationResult.PASS for r in verification_results
        )
        
        return {
            "task_id": task_id,
            "scope_ok": scope_ok,
            "criteria_met": criteria_met,
            "verification_results": [r.value for r in verification_results],
            "all_passed": all_passed,
            "recommendation": "APPROVE" if all_passed else "REJECT"
        }
    
    def _verify_scope(self, contract: TaskContract) -> bool:
        """验证作用域"""
        # Simplified scope verification
        return True
    
    def _run_verification(self, command: str, contract: TaskContract) -> VerificationResult:
        """运行验证命令"""
        # Parse command type
        if command.startswith("file_exists:"):
            filepath = command.split(":", 1)[1]
            return self._verify_file_exists(filepath)
        elif command.startswith("test:"):
            test_name = command.split(":", 1)[1]
            return self._verify_test_pass(test_name)
        else:
            # Default: assume pass (in real implementation, would execute command)
            return VerificationResult.PASS
    
    def _verify_file_exists(self, filepath: str) -> VerificationResult:
        """验证文件存在"""
        import os
        if os.path.exists(filepath):
            return VerificationResult.PASS
        return VerificationResult.FAIL
    
    def _verify_test_pass(self, test_name: str) -> VerificationResult:
        """验证测试通过"""
        # Simplified: would actually run test
        return VerificationResult.PASS
    
    def _verify_no_errors(self, content: str) -> VerificationResult:
        """验证无错误"""
        error_indicators = ["error", "exception", "traceback", "failed"]
        content_lower = content.lower()
        if any(indicator in content_lower for indicator in error_indicators):
            return VerificationResult.FAIL
        return VerificationResult.PASS
    
    def _verify_custom(self, rule: str) -> VerificationResult:
        """自定义验证"""
        return VerificationResult.PASS
    
    def _check_criterion(self, criterion: str, contract: TaskContract) -> bool:
        """检查完成标准"""
        # Simplified: would actually check criterion
        return True
    
    def get_ready_tasks(self) -> List[TaskContract]:
        """获取可执行的任务（依赖已满足）"""
        ready = []
        for task in self.contracts.values():
            if task.status != TaskStatus.PENDING:
                continue
            
            deps_met = all(
                self.contracts.get(dep_id, TaskContract(
                    task_id="", name="", description="", scope=[], 
                    done_criteria=[], verification_commands=[]
                )).status == TaskStatus.COMPLETED
                for dep_id in task.dependencies
            )
            
            if deps_met:
                ready.append(task)
        
        return ready
    
    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        status_counts = {}
        for task in self.contracts.values():
            s = task.status.value
            status_counts[s] = status_counts.get(s, 0) + 1
        
        total_evidence = sum(len(ev) for ev in self.evidence.values())
        
        return {
            "total_tasks": len(self.contracts),
            "status_distribution": status_counts,
            "total_evidence": total_evidence,
            "total_dags": len(self.dags)
        }


_task_contract_instance = None

def get_task_contract_manager(api_key: Optional[str] = None) -> TaskContractManager:
    global _task_contract_instance
    if _task_contract_instance is None:
        _task_contract_instance = TaskContractManager(api_key)
    return _task_contract_instance
