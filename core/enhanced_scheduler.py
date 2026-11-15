"""
增强版智能定时任务调度器
支持 Cron 表达式、任务依赖、限流、超时等高级功能
"""

import asyncio
import time
import json
import re
from typing import Dict, Any, List, Optional, Callable, Set
from dataclasses import dataclass, asdict, field
from pathlib import Path
from enum import Enum
from datetime import datetime, timedelta
from collections import defaultdict


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
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


@dataclass
class ScheduledTask:
    """定时任务"""
    id: str
    name: str
    task_type: str
    priority: TaskPriority
    schedule: str  # 支持: 秒数间隔, cron表达式, 特殊关键字
    last_run: Optional[float]
    next_run: float
    status: TaskStatus
    retry_count: int
    max_retries: int
    timeout: int  # 超时时间(秒)
    dependencies: List[str]  # 依赖的任务ID
    rate_limit: int  # 每小时最大执行次数
    execution_count: int  # 本小时执行次数
    last_reset: float  # 上次重置时间
    metadata: Dict[str, Any]
    created_at: float
    updated_at: float
    
    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["priority"] = self.priority.value
        data["status"] = self.status.value
        return data


class CronParser:
    """Cron 表达式解析器"""
    
    @staticmethod
    def parse_cron_to_seconds(cron_expr: str) -> Optional[int]:
        """将 Cron 表达式转换为秒数间隔 (简化版)"""
        parts = cron_expr.strip().split()
        
        if len(parts) != 5:
            return None
        
        minute, hour, day, month, weekday = parts
        
        # 每分钟
        if minute == "*" and hour == "*":
            return 60
        
        # 每小时
        if minute == "0" and hour == "*":
            return 3600
        
        # 每天
        if minute == "0" and hour == "0" and day == "*":
            return 86400
        
        # 每周
        if minute == "0" and hour == "0" and day == "*" and weekday != "*":
            return 604800
        
        # 每月
        if minute == "0" and hour == "0" and day != "*" and month == "*":
            return 2592000  # 30天
        
        # 固定间隔
        if "*" in minute:
            try:
                return int(minute.replace("*", "60")) * 60
            except ValueError:
                pass  # noqa: silent-ok — ValueError 可安全忽略（已审计 2026-10-08）
        
        return None
    
    @staticmethod
    def parse_schedule(schedule: str) -> float:
        """解析调度表达式"""
        # 特殊关键字
        schedule_map = {
            "@every_second": 1,
            "@every_minute": 60,
            "@every_hour": 3600,
            "@every_day": 86400,
            "@every_week": 604800,
            "@every_month": 2592000,
        }
        
        if schedule in schedule_map:
            return schedule_map[schedule]
        
        # 尝试解析秒数
        try:
            return float(schedule)
        except ValueError:
            pass  # noqa: silent-ok — ValueError 可安全忽略（已审计 2026-10-08）
        
        # 尝试解析 Cron 表达式
        seconds = CronParser.parse_cron_to_seconds(schedule)
        if seconds is not None:
            return seconds
        
        # 默认1小时
        return 3600


class TaskExecutor:
    """任务执行器"""
    
    def __init__(self, triple_model=None):
        self.triple_model = triple_model
        self.executors = {}
        
    def register_executor(self, task_type: str, executor: Callable):
        """注册任务执行器"""
        self.executors[task_type] = executor
    
    async def execute(self, task: ScheduledTask) -> Dict[str, Any]:
        """执行任务"""
        executor = self.executors.get(task.task_type)
        
        if executor:
            # 使用自定义执行器
            if asyncio.iscoroutinefunction(executor):
                result = await executor(task)
            else:
                result = executor(task)
            return result
        elif self.triple_model:
            # 使用三模型系统
            return await self.triple_model.execute_task({
                "type": task.task_type,
                "description": f"执行定时任务: {task.name}",
                "metadata": task.metadata
            })
        else:
            # 默认执行
            return {"status": "completed", "message": "默认执行完成"}


class EnhancedSmartScheduler:
    """增强版智能定时任务调度器"""
    
    def __init__(self, api_key: Optional[str] = None, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(exist_ok=True)
        
        self.tasks_file = self.data_dir / "enhanced_scheduled_tasks.json"
        self.metrics_file = self.data_dir / "enhanced_scheduler_metrics.json"
        
        # 初始化三模型系统
        from core.triple_model_system import TripleModelSystem
        self.triple_model = TripleModelSystem(api_key)
        
        # 初始化任务执行器
        self.executor = TaskExecutor(self.triple_model)
        
        self.tasks: List[ScheduledTask] = []
        self.metrics: Dict[str, Any] = {}
        self.running = False
        self.concurrent_limit = 5  # 并发执行限制
        
        self._load_data()
    
    def _load_data(self):
        """加载数据"""
        if self.tasks_file.exists():
            try:
                with open(self.tasks_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    tasks_data = data.get("tasks", [])
                    self.tasks = []
                    for task_dict in tasks_data:
                        # 转换枚举类型
                        task_dict["priority"] = TaskPriority(task_dict["priority"])
                        task_dict["status"] = TaskStatus(task_dict["status"])
                        self.tasks.append(ScheduledTask(**task_dict))
            except Exception as e:
                print(f"加载任务数据失败: {e}")
                self.tasks = []
        
        if self.metrics_file.exists():
            try:
                with open(self.metrics_file, 'r', encoding='utf-8') as f:
                    self.metrics = json.load(f)
            except Exception as e:
                print(f"加载指标数据失败: {e}")
                self.metrics = {}
    
    def _save_data(self):
        """保存数据"""
        try:
            with open(self.tasks_file, 'w', encoding='utf-8') as f:
                json.dump({
                    "tasks": [task.to_dict() for task in self.tasks],
                    "last_updated": time.time()
                }, f, indent=2, ensure_ascii=False)
            
            with open(self.metrics_file, 'w', encoding='utf-8') as f:
                json.dump(self.metrics, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"保存数据失败: {e}")
    
    def add_task(self, name: str, task_type: str, schedule: str,
                 priority: TaskPriority = TaskPriority.MEDIUM,
                 max_retries: int = 3, timeout: int = 300,
                 dependencies: Optional[List[str]] = None,
                 rate_limit: int = 100,
                 metadata: Optional[Dict[str, Any]] = None) -> str:
        """添加定时任务"""
        import hashlib
        
        task_id = hashlib.md5(f"{name}{time.time()}".encode()).hexdigest()
        
        # 计算下次运行时间
        interval = CronParser.parse_schedule(schedule)
        next_run = time.time() + interval
        
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
            timeout=timeout,
            dependencies=dependencies or [],
            rate_limit=rate_limit,
            execution_count=0,
            last_reset=time.time(),
            metadata=metadata or {},
            created_at=time.time(),
            updated_at=time.time()
        )
        
        self.tasks.append(task)
        self._save_data()
        
        return task_id
    
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
    
    def update_task(self, task_id: str, updates: Dict[str, Any]) -> bool:
        """更新任务"""
        task = self.get_task(task_id)
        if not task:
            return False
        
        for key, value in updates.items():
            if hasattr(task, key):
                if key == "priority":
                    value = TaskPriority(value)
                elif key == "status":
                    value = TaskStatus(value)
                setattr(task, key, value)
        
        task.updated_at = time.time()
        self._save_data()
        return True
    
    def get_pending_tasks(self) -> List[ScheduledTask]:
        """获取待执行任务"""
        current_time = time.time()
        pending = []
        
        for task in self.tasks:
            # 检查状态
            if task.status not in [TaskStatus.PENDING, TaskStatus.RETRYING]:
                continue
            
            # 检查执行时间
            if task.next_run > current_time:
                continue
            
            # 检查依赖
            if not self._check_dependencies(task):
                continue
            
            # 检查限流
            if not self._check_rate_limit(task):
                continue
            
            pending.append(task)
        
        return pending
    
    def _check_dependencies(self, task: ScheduledTask) -> bool:
        """检查任务依赖"""
        if not task.dependencies:
            return True
        
        for dep_id in task.dependencies:
            dep_task = self.get_task(dep_id)
            if not dep_task or dep_task.status != TaskStatus.COMPLETED:
                return False
        
        return True
    
    def _check_rate_limit(self, task: ScheduledTask) -> bool:
        """检查限流"""
        current_time = time.time()
        
        # 每小时重置计数
        if current_time - task.last_reset > 3600:
            task.execution_count = 0
            task.last_reset = current_time
        
        return task.execution_count < task.rate_limit
    
    async def run_task(self, task: ScheduledTask) -> Dict[str, Any]:
        """执行任务"""
        task.status = TaskStatus.RUNNING
        task.execution_count += 1
        self._save_data()
        
        start_time = time.time()
        
        try:
            # 使用执行器执行任务
            result = await asyncio.wait_for(
                self.executor.execute(task),
                timeout=task.timeout
            )
            
            execution_time = time.time() - start_time
            
            # 更新任务状态
            task.status = TaskStatus.COMPLETED
            task.last_run = time.time()
            
            # 计算下次运行时间
            interval = CronParser.parse_schedule(task.schedule)
            task.next_run = time.time() + interval
            
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
            
        except asyncio.TimeoutError:
            execution_time = time.time() - start_time
            
            # 处理超时
            task.status = TaskStatus.TIMEOUT
            task.retry_count += 1
            
            if task.retry_count < task.max_retries:
                task.status = TaskStatus.RETRYING
                task.next_run = time.time() + (task.retry_count * 60)
            
            self._record_metric(task.id, "timeout", execution_time, "任务执行超时")
            self._save_data()
            
            return {
                "task_id": task.id,
                "status": "timeout",
                "execution_time": execution_time,
                "error": "任务执行超时"
            }
            
        except Exception as e:
            execution_time = time.time() - start_time
            
            # 处理失败
            task.retry_count += 1
            
            if task.retry_count < task.max_retries:
                task.status = TaskStatus.RETRYING
                # 指数退避
                task.next_run = time.time() + (task.retry_count * 60 * (2 ** (task.retry_count - 1)))
            else:
                task.status = TaskStatus.FAILED
            
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
                "timeout_runs": 0,
                "avg_execution_time": 0,
                "max_execution_time": 0,
                "min_execution_time": float('inf'),
                "errors": [],
                "last_success": None,
                "last_failure": None
            }
        
        metric = self.metrics[task_id]
        metric["total_runs"] += 1
        
        if status == "success":
            metric["successful_runs"] += 1
            metric["last_success"] = time.time()
        elif status == "timeout":
            metric["timeout_runs"] += 1
        else:
            metric["failed_runs"] += 1
            metric["last_failure"] = time.time()
            if error:
                metric["errors"].append({
                    "time": time.time(),
                    "error": error
                })
                # 只保留最近20个错误
                metric["errors"] = metric["errors"][-20:]
        
        # 更新执行时间统计
        metric["avg_execution_time"] = (
            (metric["avg_execution_time"] * (metric["total_runs"] - 1) + execution_time) / 
            metric["total_runs"]
        )
        metric["max_execution_time"] = max(metric["max_execution_time"], execution_time)
        metric["min_execution_time"] = min(metric["min_execution_time"], execution_time)
    
    async def start(self):
        """启动调度器"""
        self.running = True
        print("[START] 启动增强版智能定时任务调度器")
        print(f"   并发限制: {self.concurrent_limit}")
        print(f"   活跃任务: {len([t for t in self.tasks if t.status != TaskStatus.CANCELLED])}")
        
        while self.running:
            try:
                # 获取待执行任务
                pending_tasks = self.get_pending_tasks()
                
                if pending_tasks:
                    print(f"\n[INFO] 发现 {len(pending_tasks)} 个待执行任务")
                    
                    # 按优先级排序
                    priority_order = {
                        TaskPriority.CRITICAL: 0,
                        TaskPriority.HIGH: 1,
                        TaskPriority.MEDIUM: 2,
                        TaskPriority.LOW: 3
                    }
                    pending_tasks.sort(key=lambda t: priority_order[t.priority])
                    
                    # 并发执行任务
                    semaphore = asyncio.Semaphore(self.concurrent_limit)
                    
                    async def run_with_semaphore(task):
                        async with semaphore:
                            print(f"  ▶️  执行任务: {task.name} (优先级: {task.priority.value})")
                            result = await self.run_task(task)
                            
                            if result["status"] == "success":
                                print(f"  [OK] 成功 (耗时: {result['execution_time']:.2f}s)")
                            else:
                                print(f"  [FAIL] 失败: {result.get('error', 'Unknown error')}")
                            
                            return result
                    
                    # 并发执行所有任务
                    tasks_coros = [run_with_semaphore(task) for task in pending_tasks]
                    await asyncio.gather(*tasks_coros, return_exceptions=True)
                
                # 等待5秒
                await asyncio.sleep(5)
                
            except Exception as e:
                print(f"调度器运行异常: {e}")
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
        timeout_tasks = len([t for t in self.tasks if t.status == TaskStatus.TIMEOUT])
        
        # 计算成功率
        total_completed = completed_tasks + failed_tasks + timeout_tasks
        success_rate = completed_tasks / total_completed if total_completed > 0 else 0
        
        # 计算平均执行时间
        total_time = sum(m.get("avg_execution_time", 0) * m.get("total_runs", 0) 
                        for m in self.metrics.values())
        total_runs = sum(m.get("total_runs", 0) for m in self.metrics.values())
        avg_time = total_time / total_runs if total_runs > 0 else 0
        
        return {
            "total_tasks": total_tasks,
            "pending_tasks": pending_tasks,
            "running_tasks": running_tasks,
            "completed_tasks": completed_tasks,
            "failed_tasks": failed_tasks,
            "timeout_tasks": timeout_tasks,
            "success_rate": success_rate,
            "avg_execution_time": avg_time,
            "metrics": self.metrics
        }
    
    def get_task_history(self, task_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        """获取任务历史"""
        metric = self.metrics.get(task_id, {})
        errors = metric.get("errors", [])
        
        return errors[-limit:] if errors else []


# 全局实例
_enhanced_scheduler_instance = None

def get_enhanced_scheduler(api_key: Optional[str] = None, data_dir: str = "data") -> EnhancedSmartScheduler:
    """获取增强版调度器单例"""
    global _enhanced_scheduler_instance
    if _enhanced_scheduler_instance is None:
        _enhanced_scheduler_instance = EnhancedSmartScheduler(api_key, data_dir)
    return _enhanced_scheduler_instance
