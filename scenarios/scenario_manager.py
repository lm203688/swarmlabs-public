"""
实验场景管理器
集成 Lotka-Volterra, Hodgkin-Huxley, Black-Scholes 模型
"""

from typing import Dict, Any, List, Optional
from dataclasses import dataclass

from scenarios.lotka_volterra import LotkaVolterraModel, LVParameters
from scenarios.hodgkin_huxley import HodgkinHuxleyModel, HHParameters
from scenarios.black_scholes import BlackScholesModel, BSParameters


@dataclass
class ScenarioConfig:
    """场景配置"""
    name: str
    model_type: str  # lotka_volterra, hodgkin_huxley, black_scholes
    params: Dict[str, Any]
    description: str = ""


class ScenarioManager:
    """场景管理器"""
    
    def __init__(self):
        self.scenarios = {}
        self.results = {}
        
        # 注册默认场景
        self._register_default_scenarios()
    
    def _register_default_scenarios(self):
        """注册默认场景"""
        # Lotka-Volterra 场景
        self.register_scenario(ScenarioConfig(
            name="lv_standard",
            model_type="lotka_volterra",
            params={"alpha": 1.5, "beta": 1.0, "delta": 0.75, "gamma": 1.0},
            description="标准 Lotka-Volterra 捕食者-猎物模型"
        ))
        
        self.register_scenario(ScenarioConfig(
            name="lv_stable",
            model_type="lotka_volterra",
            params={"alpha": 1.0, "beta": 0.5, "delta": 0.5, "gamma": 0.5},
            description="稳定 Lotka-Volterra 模型"
        ))
        
        # Hodgkin-Huxley 场景
        self.register_scenario(ScenarioConfig(
            name="hh_standard",
            model_type="hodgkin_huxley",
            params={},
            description="标准 Hodgkin-Huxley 神经元模型"
        ))
        
        self.register_scenario(ScenarioConfig(
            name="hh_excitable",
            model_type="hodgkin_huxley",
            params={"g_Na": 100.0, "g_K": 30.0, "g_L": 0.2},
            description="可兴奋 Hodgkin-Huxley 模型"
        ))
        
        # Black-Scholes 场景
        self.register_scenario(ScenarioConfig(
            name="bs_atm",
            model_type="black_scholes",
            params={"S": 100, "K": 100, "T": 1.0, "r": 0.05, "sigma": 0.2},
            description="平值期权 (ATM)"
        ))
        
        self.register_scenario(ScenarioConfig(
            name="bs_itm",
            model_type="black_scholes",
            params={"S": 110, "K": 100, "T": 1.0, "r": 0.05, "sigma": 0.2},
            description="实值期权 (ITM)"
        ))
        
        self.register_scenario(ScenarioConfig(
            name="bs_otm",
            model_type="black_scholes",
            params={"S": 90, "K": 100, "T": 1.0, "r": 0.05, "sigma": 0.2},
            description="虚值期权 (OTM)"
        ))
    
    def register_scenario(self, config: ScenarioConfig):
        """注册场景"""
        self.scenarios[config.name] = config
    
    def get_scenario(self, name: str) -> Optional[ScenarioConfig]:
        """获取场景配置"""
        return self.scenarios.get(name)
    
    def list_scenarios(self) -> List[Dict[str, str]]:
        """列出所有场景"""
        return [
            {"name": name, "type": config.model_type, "description": config.description}
            for name, config in self.scenarios.items()
        ]
    
    def run_scenario(self, name: str, **kwargs) -> Dict[str, Any]:
        """运行场景"""
        config = self.scenarios.get(name)
        if not config:
            return {"error": f"Scenario '{name}' not found"}
        
        if config.model_type == "lotka_volterra":
            return self._run_lotka_volterra(config, **kwargs)
        elif config.model_type == "hodgkin_huxley":
            return self._run_hodgkin_huxley(config, **kwargs)
        elif config.model_type == "black_scholes":
            return self._run_black_scholes(config, **kwargs)
        else:
            return {"error": f"Unknown model type: {config.model_type}"}
    
    def _run_lotka_volterra(self, config: ScenarioConfig, **kwargs) -> Dict[str, Any]:
        """运行 Lotka-Volterra 场景"""
        params = LVParameters(**config.params)
        model = LotkaVolterraModel(params)
        
        prey_init = kwargs.get("prey_init", 100)
        predator_init = kwargs.get("predator_init", 50)
        steps = kwargs.get("steps", 500)
        dt = kwargs.get("dt", 0.1)
        
        result = model.simulate(prey_init, predator_init, steps, dt)
        stability = model.analyze_stability()
        
        self.results[config.name] = {
            "model": "lotka_volterra",
            "params": config.params,
            "result": result,
            "stability": stability
        }
        
        return self.results[config.name]
    
    def _run_hodgkin_huxley(self, config: ScenarioConfig, **kwargs) -> Dict[str, Any]:
        """运行 Hodgkin-Huxley 场景"""
        params = HHParameters(**config.params) if config.params else HHParameters()
        model = HodgkinHuxleyModel(params)
        
        # 默认脉冲刺激
        def I_ext_default(t):
            if 5.0 <= t <= 5.1 or 10.0 <= t <= 10.1 or 15.0 <= t <= 15.1:
                return 10.0
            return 0.0
        
        I_ext_func = kwargs.get("I_ext_func", I_ext_default)
        duration = kwargs.get("duration", 20.0)
        dt = kwargs.get("dt", 0.01)
        
        result = model.simulate(I_ext_func, duration, dt)
        ap_analysis = model.analyze_action_potential(result["V"])
        
        self.results[config.name] = {
            "model": "hodgkin_huxley",
            "params": config.params,
            "result": result,
            "analysis": ap_analysis
        }
        
        return self.results[config.name]
    
    def _run_black_scholes(self, config: ScenarioConfig, **kwargs) -> Dict[str, Any]:
        """运行 Black-Scholes 场景"""
        params = BSParameters(**config.params)
        model = BlackScholesModel(params)
        
        # 计算价格和希腊字母
        call_price = model.call_price()
        put_price = model.put_price()
        
        delta_call = model.delta_call()
        delta_put = model.delta_put()
        gamma = model.gamma()
        theta_call = model.theta_call()
        vega = model.vega()
        
        self.results[config.name] = {
            "model": "black_scholes",
            "params": config.params,
            "prices": {
                "call": call_price,
                "put": put_price
            },
            "greeks": {
                "delta_call": delta_call,
                "delta_put": delta_put,
                "gamma": gamma,
                "theta_call": theta_call,
                "vega": vega
            }
        }
        
        return self.results[config.name]
    
    def compare_scenarios(self, scenario_names: List[str]) -> Dict[str, Any]:
        """比较多个场景"""
        comparison = {}
        
        for name in scenario_names:
            if name in self.results:
                comparison[name] = self.results[name]
            elif name in self.scenarios:
                # 先运行场景
                self.run_scenario(name)
                comparison[name] = self.results[name]
        
        return comparison
    
    def sensitivity_analysis(self, scenario_name: str, param_name: str,
                           param_range: List[float]) -> Dict[str, Any]:
        """参数敏感性分析"""
        config = self.scenarios.get(scenario_name)
        if not config:
            return {"error": f"Scenario '{scenario_name}' not found"}
        
        if config.model_type == "black_scholes":
            params = BSParameters(**config.params)
            model = BlackScholesModel(params)
            import numpy as np
            return model.analyze_sensitivity(param_name, np.array(param_range))
        else:
            return {"error": "Sensitivity analysis only available for Black-Scholes"}


def demo_scenario_manager():
    """场景管理器演示"""
    print("实验场景管理器")
    print("=" * 50)
    
    manager = ScenarioManager()
    
    # 列出所有场景
    print("\n可用场景:")
    for scenario in manager.list_scenarios():
        print(f"  - {scenario['name']}: {scenario['description']}")
    
    # 运行 Lotka-Volterra
    print("\n运行 Lotka-Volterra 场景...")
    lv_result = manager.run_scenario("lv_standard")
    print(f"  猎物最终数量: {lv_result['result']['prey'][-1]:.2f}")
    print(f"  捕食者最终数量: {lv_result['result']['predator'][-1]:.2f}")
    
    # 运行 Hodgkin-Huxley
    print("\n运行 Hodgkin-Huxley 场景...")
    hh_result = manager.run_scenario("hh_standard")
    print(f"  动作电位数量: {hh_result['analysis']['num_peaks']}")
    print(f"  平均振幅: {hh_result['analysis']['avg_amplitude']:.2f} mV")
    
    # 运行 Black-Scholes
    print("\n运行 Black-Scholes 场景...")
    bs_result = manager.run_scenario("bs_atm")
    print(f"  看涨期权价格: ${bs_result['prices']['call']:.4f}")
    print(f"  看跌期权价格: ${bs_result['prices']['put']:.4f}")
    
    return manager


if __name__ == "__main__":
    demo_scenario_manager()
