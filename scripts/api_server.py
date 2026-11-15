"""
Claude Triple Model 系统 API 服务器
提供 RESTful API 接口
"""

import asyncio
import json
from aiohttp import web
from pathlib import Path
import sys

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent))


class APIServer:
    """API 服务器"""
    
    def __init__(self, host: str = "0.0.0.0", port: int = 8000):
        self.host = host
        self.port = port
        self.app = web.Application()
        self.components = {}
        
        # 初始化组件
        self._init_components()
        
        # 注册路由
        self._register_routes()
    
    def _init_components(self):
        """初始化组件"""
        try:
            from core.triple_model_system import TripleModelSystem
            from core.experience_store import ExperienceStore
            from core.tech_radar import TechRadar
            from core.smart_scheduler import SmartScheduler
            
            self.components["triple_model"] = TripleModelSystem()
            self.components["experience_store"] = ExperienceStore()
            self.components["tech_radar"] = TechRadar()
            self.components["scheduler"] = SmartScheduler()
            
            print("[OK] 组件初始化成功")
            
        except Exception as e:
            print(f"[FAIL] 组件初始化失败: {e}")
    
    def _register_routes(self):
        """注册路由"""
        # 健康检查
        self.app.router.add_get('/health', self.health_check)
        self.app.router.add_get('/api/status', self.get_status)
        
        # 系统信息
        self.app.router.add_get('/api/system/info', self.get_system_info)
        self.app.router.add_get('/api/system/statistics', self.get_statistics)
        
        # 任务管理
        self.app.router.add_post('/api/tasks/execute', self.execute_task)
        self.app.router.add_get('/api/tasks/history', self.get_task_history)
        
        # 经验存储
        self.app.router.add_get('/api/experiences', self.get_experiences)
        self.app.router.add_post('/api/experiences', self.add_experience)
        
        # 技术雷达
        self.app.router.add_get('/api/tech-radar', self.get_tech_radar)
        self.app.router.add_post('/api/tech-radar/items', self.add_tech_item)
        
        # 调度器
        self.app.router.add_get('/api/scheduler/tasks', self.get_scheduled_tasks)
        self.app.router.add_post('/api/scheduler/tasks', self.add_scheduled_task)
    
    async def health_check(self, request):
        """健康检查"""
        return web.json_response({
            "status": "healthy",
            "service": "claude-triple-model",
            "version": "1.0.0"
        })
    
    async def get_status(self, request):
        """获取系统状态"""
        status = {
            "running": True,
            "components": {}
        }
        
        for name, component in self.components.items():
            status["components"][name] = {
                "status": "running"
            }
        
        return web.json_response(status)
    
    async def get_system_info(self, request):
        """获取系统信息"""
        info = {
            "name": "Claude Triple Model System",
            "version": "1.0.0",
            "description": "gen-1.5 + Mythos Preview + Opus 4.8 协同系统",
            "components": ["TripleModelSystem", "ExperienceStore", "TechRadar", "SmartScheduler"]
        }
        
        return web.json_response(info)
    
    async def get_statistics(self, request):
        """获取统计信息"""
        stats = {}
        
        if "triple_model" in self.components:
            stats["triple_model"] = self.components["triple_model"].get_statistics()
        
        if "experience_store" in self.components:
            stats["experience_store"] = self.components["experience_store"].get_statistics()
        
        if "tech_radar" in self.components:
            stats["tech_radar"] = self.components["tech_radar"].get_statistics()
        
        if "scheduler" in self.components:
            stats["scheduler"] = self.components["scheduler"].get_statistics()
        
        return web.json_response(stats)
    
    async def execute_task(self, request):
        """执行任务"""
        try:
            data = await request.json()
            
            if "triple_model" not in self.components:
                return web.json_response({"error": "TripleModelSystem not initialized"}, status=500)
            
            result = await self.components["triple_model"].execute_task(data)
            
            return web.json_response(result)
            
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)
    
    async def get_task_history(self, request):
        """获取任务历史"""
        if "triple_model" in self.components:
            history = self.components["triple_model"].experience_store[-100:]  # 最近100条
            return web.json_response({"history": history})
        
        return web.json_response({"history": []})
    
    async def get_experiences(self, request):
        """获取经验"""
        if "experience_store" in self.components:
            experiences = self.components["experience_store"].get_recent_experiences(50)
            return web.json_response({"experiences": experiences})
        
        return web.json_response({"experiences": []})
    
    async def add_experience(self, request):
        """添加经验"""
        try:
            data = await request.json()
            
            if "experience_store" in self.components:
                exp_id = self.components["experience_store"].record(
                    task=data.get("task", {}),
                    model_used=data.get("model_used", "gen15"),
                    result=data.get("result", {}),
                    metadata=data.get("metadata", {})
                )
                
                return web.json_response({"id": exp_id, "status": "success"})
            
            return web.json_response({"error": "ExperienceStore not initialized"}, status=500)
            
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)
    
    async def get_tech_radar(self, request):
        """获取技术雷达"""
        if "tech_radar" in self.components:
            items = [item.to_dict() for item in self.components["tech_radar"].items]
            return web.json_response({"items": items})
        
        return web.json_response({"items": []})
    
    async def add_tech_item(self, request):
        """添加技术条目"""
        try:
            data = await request.json()
            
            if "tech_radar" in self.components:
                from core.tech_radar import TechCategory, TechMaturity
                
                item = self.components["tech_radar"].add_tech_item(
                    name=data.get("name", ""),
                    category=TechCategory(data.get("category", "languages")),
                    maturity=TechMaturity(data.get("maturity", "emerging")),
                    risk_score=data.get("risk_score", 0.5),
                    adoption_rate=data.get("adoption_rate", 0.5),
                    trend=data.get("trend", "stable"),
                    metadata=data.get("metadata", {})
                )
                
                return web.json_response({"name": item, "status": "success"})
            
            return web.json_response({"error": "TechRadar not initialized"}, status=500)
            
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)
    
    async def get_scheduled_tasks(self, request):
        """获取定时任务"""
        if "scheduler" in self.components:
            tasks = [task.to_dict() for task in self.components["scheduler"].tasks]
            return web.json_response({"tasks": tasks})
        
        return web.json_response({"tasks": []})
    
    async def add_scheduled_task(self, request):
        """添加定时任务"""
        try:
            data = await request.json()
            
            if "scheduler" in self.components:
                from core.smart_scheduler import TaskPriority
                
                task_id = self.components["scheduler"].add_task(
                    name=data.get("name", ""),
                    task_type=data.get("task_type", "basic"),
                    schedule=data.get("schedule", "3600"),
                    priority=TaskPriority(data.get("priority", "medium")),
                    max_retries=data.get("max_retries", 3),
                    metadata=data.get("metadata", {})
                )
                
                return web.json_response({"id": task_id, "status": "success"})
            
            return web.json_response({"error": "SmartScheduler not initialized"}, status=500)
            
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)
    
    async def start(self):
        """启动服务器"""
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, self.host, self.port)
        await site.start()
        
        print(f" API 服务器启动成功: http://{self.host}:{self.port}")
        print("[INFO] 可用端点:")
        print("  - GET  /health")
        print("  - GET  /api/status")
        print("  - GET  /api/system/info")
        print("  - GET  /api/system/statistics")
        print("  - POST /api/tasks/execute")
        print("  - GET  /api/tasks/history")
        print("  - GET  /api/experiences")
        print("  - POST /api/experiences")
        print("  - GET  /api/tech-radar")
        print("  - POST /api/tech-radar/items")
        print("  - GET  /api/scheduler/tasks")
        print("  - POST /api/scheduler/tasks")
        
        return runner


async def main():
    """主函数"""
    server = APIServer()
    runner = await server.start()
    
    try:
        await asyncio.Event().wait()  # 永久运行
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
