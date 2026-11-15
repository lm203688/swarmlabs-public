"""
Lotka-Volterra 捕食者-猎物模型
生态学经典微分方程组
"""

import numpy as np
from typing import List, Tuple, Dict, Any
from dataclasses import dataclass


@dataclass
class LVParameters:
    """Lotka-Volterra 参数"""
    alpha: float = 1.5    # 猎物增长率
    beta: float = 1.0     # 捕食率
    delta: float = 0.75   # 捕食者死亡率
    gamma: float = 1.0    # 捕食者从猎物获得的增长率
    
    def to_dict(self) -> Dict[str, float]:
        return {
            "alpha": self.alpha,
            "beta": self.beta,
            "delta": self.delta,
            "gamma": self.gamma
        }


class LotkaVolterraModel:
    """Lotka-Volterra 模型"""
    
    def __init__(self, params: LVParameters = None):
        self.params = params or LVParameters()
        self.history = []
        
    def derivatives(self, prey: float, predator: float) -> Tuple[float, float]:
        """计算导数"""
        alpha, beta, delta, gamma = (
            self.params.alpha, self.params.beta,
            self.params.delta, self.params.gamma
        )
        
        d_prey = alpha * prey - beta * prey * predator
        d_predator = -delta * predator + gamma * prey * predator
        
        return d_prey, d_predator
    
    def step(self, prey: float, predator: float, dt: float = 0.1) -> Tuple[float, float]:
        """欧拉法单步"""
        d_prey, d_predator = self.derivatives(prey, predator)
        
        new_prey = prey + d_prey * dt
        new_predator = predator + d_predator * dt
        
        # 确保非负
        new_prey = max(new_prey, 0)
        new_predator = max(new_predator, 0)
        
        return new_prey, new_predator
    
    def runge_kutta_4(self, prey: float, predator: float, dt: float = 0.1) -> Tuple[float, float]:
        """RK4 高精度积分"""
        k1_prey, k1_pred = self.derivatives(prey, predator)
        
        k2_prey, k2_pred = self.derivatives(
            prey + 0.5 * dt * k1_prey,
            predator + 0.5 * dt * k1_pred
        )
        
        k3_prey, k3_pred = self.derivatives(
            prey + 0.5 * dt * k2_prey,
            predator + 0.5 * dt * k2_pred
        )
        
        k4_prey, k4_pred = self.derivatives(
            prey + dt * k3_prey,
            predator + dt * k3_pred
        )
        
        new_prey = prey + (dt / 6.0) * (k1_prey + 2*k2_prey + 2*k3_prey + k4_prey)
        new_predator = predator + (dt / 6.0) * (k1_pred + 2*k2_pred + 2*k3_pred + k4_pred)
        
        return max(new_prey, 0), max(new_predator, 0)
    
    def simulate(self, prey_init: float = 100, predator_init: float = 50,
                 steps: int = 1000, dt: float = 0.1, method: str = "rk4") -> Dict[str, Any]:
        """运行模拟"""
        prey_history = [prey_init]
        pred_history = [predator_init]
        time_history = [0]
        
        prey, predator = prey_init, predator_init
        
        for i in range(steps):
            if method == "rk4":
                prey, predator = self.runge_kutta_4(prey, predator, dt)
            else:
                prey, predator = self.step(prey, predator, dt)
            
            prey_history.append(prey)
            pred_history.append(predator)
            time_history.append((i + 1) * dt)
        
        self.history = {
            "time": time_history,
            "prey": prey_history,
            "predator": pred_history
        }
        
        return self.history
    
    def analyze_stability(self) -> Dict[str, Any]:
        """分析系统稳定性"""
        alpha, beta, delta, gamma = (
            self.params.alpha, self.params.beta,
            self.params.delta, self.params.gamma
        )
        
        # 平衡点
        prey_eq = delta / gamma
        pred_eq = alpha / beta
        
        # 雅可比矩阵在平衡点的特征值
        trace = 0
        determinant = alpha * delta
        
        eigenvalue_real = trace / 2
        eigenvalue_imag = np.sqrt(abs(determinant - (trace/2)**2))
        
        return {
            "equilibrium_prey": prey_eq,
            "equilibrium_predator": pred_eq,
            "trace": trace,
            "determinant": determinant,
            "eigenvalue_real": eigenvalue_real,
            "eigenvalue_imag": eigenvalue_imag,
            "is_stable": determinant > 0 and trace < 0,
            "is_center": abs(trace) < 1e-10 and determinant > 0
        }


def demo_lotka_volterra():
    """Lotka-Volterra 演示"""
    print("Lotka-Volterra 捕食者-猎物模型")
    print("=" * 50)
    
    # 创建模型
    model = LotkaVolterraModel(LVParameters(alpha=1.5, beta=1.0, delta=0.75, gamma=1.0))
    
    # 运行模拟
    result = model.simulate(prey_init=100, predator_init=50, steps=500, dt=0.1)
    
    # 分析结果
    stability = model.analyze_stability()
    
    print(f"初始状态: 猎物={100}, 捕食者={50}")
    print(f"最终状态: 猎物={result['prey'][-1]:.2f}, 捕食者={result['predator'][-1]:.2f}")
    print(f"平衡点: 猎物={stability['equilibrium_prey']:.2f}, 捕食者={stability['equilibrium_predator']:.2f}")
    print(f"系统稳定性: {'稳定' if stability['is_stable'] else '周期性振荡'}")
    
    return result, stability


if __name__ == "__main__":
    demo_lotka_volterra()
