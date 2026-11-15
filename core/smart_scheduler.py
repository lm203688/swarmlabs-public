"""
智能定时任务调度器
基于 Triple Model 的自适应调度与故障恢复
"""

import asyncio
import time
import json
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass, asdict
from pathlib import Path
from enum import Enum
from datetime import datetime, timedelta


class TaskPriority(Enum):
    """任务优先级"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class TaskStatus(Enum):
    """任务状态"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"


@dataclass
class ScheduledTask:
    """定时任务"""
    id: str
    name: str
    task_type: str
    priority: TaskPriority
    schedule: str  # cron表达式或间隔秒数
    last_run: Optional[float]
    next_run: float
    status: TaskStatus
    retry_count: int
    max_retries: int
    metadata: Dict[str, Any]
    
    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["priority"] = self.priority.value
        data["status"] = self.status.value
        return data


class SmartScheduler:
    """智能定时任务调度器"""
    
    def __init__(self, api_key: Optional[str] = None, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(exist_ok=True)
        
        self.tasks_file = self.data_dir / "scheduled_tasks.json"
        self.metrics_file = self.data_dir / "scheduler_metrics.json"
        
        # 导入三模型系统
        from core.triple_model_system import TripleModelSystem
        self.triple_model = TripleModelSystem(api_key)
        
        self.tasks: List[ScheduledTask] = []
        self.metrics: Dict[str, Any] = {}
        self.running = False
        
        self._load_data()
    
    def _load_data(self):
        """加载数据"""
        try:
            if self.tasks_file.exists() and self.tasks_file.stat().st_size > 0:
                with open(self.tasks_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for task in data.get("tasks", []):
                        if isinstance(task.get("priority"), str):
                            task["priority"] = TaskPriority(task["priority"])
                        if isinstance(task.get("status"), str):
                            task["status"] = TaskStatus(task["status"])
                    self.tasks = [ScheduledTask(**task) for task in data.get("tasks", [])]
        except (json.JSONDecodeError, Exception):
            self.tasks = []
        
        try:
            if self.metrics_file.exists() and self.metrics_file.stat().st_size > 0:
                with open(self.metrics_file, 'r', encoding='utf-8') as f:
                    self.metrics = json.load(f)
        except (json.JSONDecodeError, Exception):
            self.metrics = {}
    
    def _save_data(self):
        """保存数据"""
        with open(self.tasks_file, 'w', encoding='utf-8') as f:
            json.dump({
                "tasks": [task.to_dict() for task in self.tasks],
                "last_updated": time.time()
            }, f, indent=2, ensure_ascii=False)
        
        with open(self.metrics_file, 'w', encoding='utf-8') as f:
            json.dump(self.metrics, f, indent=2, ensure_ascii=False)
    
    def add_task(self, name: str, task_type: str, schedule: str,
                 priority: Optional[TaskPriority] = None,
                 max_retries: int = 3, metadata: Optional[Dict[str, Any]] = None) -> str:
        """添加定时任务"""
        import hashlib
        
        # Handle string priority by converting to enum
        if priority is None:
            priority = TaskPriority.MEDIUM
        elif isinstance(priority, str):
            priority_map = {
                "low": TaskPriority.LOW,
                "medium": TaskPriority.MEDIUM,
                "high": TaskPriority.HIGH,
                "critical": TaskPriority.CRITICAL
            }
            priority = priority_map.get(priority.lower(), TaskPriority.MEDIUM)
        
        task_id = hashlib.md5(f"{name}{time.time()}".encode()).hexdigest()
        
        # 计算下次运行时间
        next_run = self._calculate_next_run(schedule)
        
        task = ScheduledTask(
            id=task_id,
            name=name,
            task_type=task_type,
            priority=priority,
            schedule=schedule,
            last_run=None,
            next_run=next_run,
            status=TaskStatus.PENDING,
            retry_count=0,
            max_retries=max_retries,
            metadata=metadata or {}
        )
        
        self.tasks.append(task)
        self._save_data()
        
        return task_id
    
    def _calculate_next_run(self, schedule: str) -> float:
        """计算下次运行时间"""
        # 简单实现：支持秒数间隔
        try:
            interval = int(schedule)
            return time.time() + interval
        except ValueError:
            # 默认1小时后运行
            return time.time() + 3600
    
    def get_task(self, task_id: str) -> Optional[ScheduledTask]:
        """获取任务"""
        for task in self.tasks:
            if task.id == task_id:
                return task
        return None
    
    def remove_task(self, task_id: str) -> bool:
        """移除任务"""
        for i, task in enumerate(self.tasks):
            if task.id == task_id:
                self.tasks.pop(i)
                self._save_data()
                return True
        return False
    
    def get_pending_tasks(self) -> List[ScheduledTask]:
        """获取待执行任务"""
        current_time = time.time()
        return [task for task in self.tasks 
                if task.status in [TaskStatus.PENDING, TaskStatus.RETRYING] 
                and task.next_run <= current_time]
    
    async def run_task(self, task: ScheduledTask) -> Dict[str, Any]:
        """执行任务"""
        task.status = TaskStatus.RUNNING
        self._save_data()
        
        start_time = time.time()
        
        try:
            # 使用三模型系统执行任务
            result = await self.triple_model.execute_task({
                "type": task.task_type,
                "description": f"执行定时任务: {task.name}",
                "metadata": task.metadata
            })
            
            execution_time = time.time() - start_time
            
            # 更新任务状态
            task.status = TaskStatus.COMPLETED
            task.last_run = time.time()
            task.next_run = self._calculate_next_run(task.schedule)
            task.retry_count = 0
            
            # 记录指标
            self._record_metric(task.id, "success", execution_time)
            
            self._save_data()
            
            return {
                "task_id": task.id,
                "status": "success",
                "execution_time": execution_time,
                "result": result
            }
            
        except Exception as e:
            execution_time = time.time() - start_time
            
            # 处理失败
            task.retry_count += 1
            
            if task.retry_count < task.max_retries:
                task.status = TaskStatus.RETRYING
                task.next_run = time.time() + (task.retry_count * 60)  # 指数退避
            else:
                task.status = TaskStatus.FAILED
            
            # 记录指标
            self._record_metric(task.id, "failure", execution_time, str(e))
            
            self._save_data()
            
            return {
                "task_id": task.id,
                "status": "failed",
                "execution_time": execution_time,
                "error": str(e),
                "retry_count": task.retry_count
            }
    
    def _record_metric(self, task_id: str, status: str, execution_time: float, 
                       error: Optional[str] = None):
        """记录指标"""
        if task_id not in self.metrics:
            self.metrics[task_id] = {
                "total_runs": 0,
                "successful_runs": 0,
                "failed_runs": 0,
                "avg_execution_time": 0,
                "errors": []
            }
        
        metric = self.metrics[task_id]
        metric["total_runs"] += 1
        
        if status == "success":
            metric["successful_runs"] += 1
        else:
            metric["failed_runs"] += 1
            if error:
                metric["errors"].append({
                    "time": time.time(),
                    "error": error
                })
                # 只保留最近10个错误
                metric["errors"] = metric["errors"][-10:]
        
        # 更新平均执行时间
        total_time = metric["avg_execution_time"] * (metric["total_runs"] - 1) + execution_time
        metric["avg_execution_time"] = total_time / metric["total_runs"]
    
    async def start(self):
        """启动调度器"""
        self.running = True
        print("[START] 启动智能定时任务调度器")
        
        while self.running:
            # 获取待执行任务
            pending_tasks = self.get_pending_tasks()
            
            if pending_tasks:
                print(f"[INFO] 发现 {len(pending_tasks)} 个待执行任务")
                
                # 按优先级排序
                pending_tasks.sort(key=lambda t: list(TaskPriority).index(t.priority), reverse=True)
                
                # 执行任务
                for task in pending_tasks:
                    print(f"  [RUN] 执行任务: {task.name}")
                    result = await self.run_task(task)
                    
                    if result["status"] == "success":
                        print(f"  [OK] 成功 (耗时: {result['execution_time']:.2f}s)")
                    else:
                        print(f"  [FAIL] 失败: {result.get('error', 'Unknown error')}")
            
            # 等待10秒
            await asyncio.sleep(10)
    
    def stop(self):
        """停止调度器"""
        self.running = False
        print("⏹️  停止调度器")
    
    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        total_tasks = len(self.tasks)
        pending_tasks = len([t for t in self.tasks if t.status == TaskStatus.PENDING])
        running_tasks = len([t for t in self.tasks if t.status == TaskStatus.RUNNING])
        completed_tasks = len([t for t in self.tasks if t.status == TaskStatus.COMPLETED])
        failed_tasks = len([t for t in self.tasks if t.status == TaskStatus.FAILED])
        
        return {
            "total_tasks": total_tasks,
            "pending_tasks": pending_tasks,
            "running_tasks": running_tasks,
            "completed_tasks": completed_tasks,
            "failed_tasks": failed_tasks,
            "success_rate": completed_tasks / total_tasks if total_tasks > 0 else 0,
            "metrics": self.metrics
        }


# 全局实例
_scheduler_instance = None

def get_scheduler(api_key: Optional[str] = None, data_dir: str = "data") -> SmartScheduler:
    """获取调度器单例"""
    global _scheduler_instance
    if _scheduler_instance is None:
        _scheduler_instance = SmartScheduler(api_key, data_dir)
    return _scheduler_instance
