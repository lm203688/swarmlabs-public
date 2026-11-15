"""
Claude Triple Model 系统启动脚本
一键启动所有服务
"""

import asyncio
import sys
import os
import signal
from pathlib import Path

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent))


class SystemManager:
    """系统管理器"""
    
    def __init__(self):
        self.running = False
        self.components = {}
    
    async def start(self):
        """启动系统"""
        print("=" * 60)
        print("[START] 启动 Claude Triple Model 系统")
        print("=" * 60)
        
        # 检查环境
        if not self._check_environment():
            print("[FAIL] 环境检查失败，请先运行部署脚本")
            return False
        
        # 初始化组件
        if not await self._initialize_components():
            print("[FAIL] 组件初始化失败")
            return False
        
        self.running = True
        
        # 注册信号处理
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        print("\n[OK] 系统启动成功！")
        print("[INFO] 服务状态:")
        for name, component in self.components.items():
            print(f"  - {name}: 运行中")
        
        print("\n[RETRY] 系统运行中... (按 Ctrl+C 停止)")
        
        # 启动所有组件
        await self._run_components()
        
        return True
    
    def _check_environment(self):
        """检查环境"""
        print("[CHECK] 检查环境...")
        
        # 检查配置文件
        config_path = Path("config/system_config.json")
        if not config_path.exists():
            print("  [WARN]  配置文件不存在，请先运行部署脚本")
            return False
        
        # 检查 API 密钥
        api_key = os.environ.get("CLAUDE_API_KEY")
        if not api_key:
            print("  [WARN]  CLAUDE_API_KEY 未设置")
            # 使用测试密钥
            os.environ["CLAUDE_API_KEY"] = "test_key"
            print("  [INFO] 使用测试密钥")
        
        print("  [OK] 环境检查通过")
        return True
    
    async def _initialize_components(self):
        """初始化组件"""
        print("[FIX] 初始化组件...")
        
        try:
            # 初始化 Triple Model 系统
            from core.triple_model_system import TripleModelSystem
            self.components["triple_model"] = TripleModelSystem()
            print("  [OK] TripleModelSystem 初始化成功")
            
            # 初始化经验存储
            from core.experience_store import ExperienceStore
            self.components["experience_store"] = ExperienceStore()
            print("  [OK] ExperienceStore 初始化成功")
            
            # 初始化技术雷达
            from core.tech_radar import TechRadar
            self.components["tech_radar"] = TechRadar()
            print("  [OK] TechRadar 初始化成功")
            
            # 初始化智能调度器
            from core.smart_scheduler import SmartScheduler
            self.components["scheduler"] = SmartScheduler()
            print("  [OK] SmartScheduler 初始化成功")
            
            # Sprint 1: 文献检索智能体
            from core.document_qa_agent import DocumentQAAgent
            self.components["document_qa"] = DocumentQAAgent()
            print("  [OK] DocumentQAAgent 初始化成功")
            
            # Sprint 1: 多智能体辩论系统
            from core.multi_agent_debate import MultiAgentDebateSystem
            self.components["debate_system"] = MultiAgentDebateSystem()
            print("  [OK] MultiAgentDebateSystem 初始化成功")
            
            # Sprint 1: 专业数据分析智能体
            from core.specialized_finch_agent import SpecializedFinchAgent
            self.components["finch_agent"] = SpecializedFinchAgent()
            print("  [OK] SpecializedFinchAgent 初始化成功")
            
            # Sprint 2: 数据适配器
            from core.data_adapter import DataAdapter
            self.components["data_adapter"] = DataAdapter()
            print("  [OK] DataAdapter 初始化成功")
            
            # Sprint 2: 混合管道
            from core.hybrid_pipeline import HybridPipeline
            self.components["hybrid_pipeline"] = HybridPipeline()
            print("  [OK] HybridPipeline 初始化成功")
            
            # Sprint 2: 实验室集成
            from core.lab_in_loop import LabInTheLoop
            self.components["lab_in_loop"] = LabInTheLoop()
            print("  [OK] LabInTheLoop 初始化成功")
            
            # Sprint 3: Robin架构
            from core.robin_architecture import RobinArchitecture
            self.components["robin_architecture"] = RobinArchitecture()
            print("  [OK] RobinArchitecture 初始化成功")
            
            # Sprint 3: LIMO微调
            from core.limo_finetuning import LIMOFineTuning
            self.components["limo_finetuning"] = LIMOFineTuning()
            print("  [OK] LIMOFineTuning 初始化成功")
            
            # Phase 1: Policy Gateway (安全控制面)
            from core.policy_gateway import PolicyGateway
            self.components["policy_gateway"] = PolicyGateway()
            print("  [OK] PolicyGateway 初始化成功")
            
            # Phase 1: Cost Breaker (成本熔断)
            from core.cost_breaker import CostBreaker
            self.components["cost_breaker"] = CostBreaker()
            print("  [OK] CostBreaker 初始化成功")
            
            # Phase 2: Task Contract (任务契约)
            from core.task_contract import TaskContractManager
            self.components["task_contract"] = TaskContractManager()
            print("  [OK] TaskContractManager 初始化成功")
            
            # Phase 2: Agent Relay (多模型接力)
            from core.agent_relay import AgentRelay
            self.components["agent_relay"] = AgentRelay()
            print("  [OK] AgentRelay 初始化成功")
            
            print(f"\n[OK] 共初始化 {len(self.components)} 个组件")
            return True
            
        except Exception as e:
            print(f"  [FAIL] 组件初始化失败: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    async def _run_components(self):
        """运行所有组件"""
        # 启动调度器
        scheduler = self.components.get("scheduler")
        if scheduler:
            asyncio.create_task(scheduler.start())
        
        # 主循环
        while self.running:
            await asyncio.sleep(1)
    
    def _signal_handler(self, signum, frame):
        """信号处理"""
        print("\n⏹️  收到停止信号，正在关闭系统...")
        self.running = False
        self._stop_components()
        print("[OK] 系统已停止")
        sys.exit(0)
    
    def _stop_components(self):
        """停止所有组件"""
        for name, component in self.components.items():
            if hasattr(component, 'stop'):
                component.stop()
                print(f"  [OK] {name} 已停止")
    
    def get_status(self):
        """获取系统状态"""
        status = {
            "running": self.running,
            "components": {}
        }
        
        for name, component in self.components.items():
            status["components"][name] = {
                "status": "running" if self.running else "stopped"
            }
        
        return status


async def main():
    """主函数"""
    manager = SystemManager()
    
    try:
        success = await manager.start()
        return success
    except KeyboardInterrupt:
        print("\n⏹️  用户中断")
        manager._stop_components()
        return True
    except Exception as e:
        print(f"\n[FAIL] 系统启动失败: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
