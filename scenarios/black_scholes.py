"""
Black-Scholes 期权定价模型
金融数学经典期权定价模型
"""

import numpy as np
from scipy.stats import norm
from typing import Dict, Any, Optional
from dataclasses import dataclass


@dataclass
class BSParameters:
    """Black-Scholes 参数"""
    S: float       # 标的价格 (当前股价)
    K: float       # 执行价格
    T: float       # 到期时间 (年)
    r: float       # 无风险利率
    sigma: float   # 波动率
    
    def to_dict(self) -> Dict[str, float]:
        return {
            "S": self.S,
            "K": self.K,
            "T": self.T,
            "r": self.r,
            "sigma": self.sigma
        }


class BlackScholesModel:
    """Black-Scholes 期权定价模型"""
    
    def __init__(self, params: BSParameters):
        self.params = params
    
    def d1(self) -> float:
        """计算 d1"""
        S, K, T, r, sigma = (
            self.params.S, self.params.K, self.params.T,
            self.params.r, self.params.sigma
        )
        return (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    
    def d2(self) -> float:
        """计算 d2"""
        return self.d1() - self.params.sigma * np.sqrt(self.params.T)
    
    def call_price(self) -> float:
        """计算看涨期权价格"""
        S, K, T, r = (
            self.params.S, self.params.K, self.params.T, self.params.r
        )
        
        d1 = self.d1()
        d2 = self.d2()
        
        return S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
    
    def put_price(self) -> float:
        """计算看跌期权价格"""
        S, K, T, r = (
            self.params.S, self.params.K, self.params.T, self.params.r
        )
        
        d1 = self.d1()
        d2 = self.d2()
        
        return K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
    
    def delta_call(self) -> float:
        """看涨期权 Delta"""
        return norm.cdf(self.d1())
    
    def delta_put(self) -> float:
        """看跌期权 Delta"""
        return norm.cdf(self.d1()) - 1
    
    def gamma(self) -> float:
        """期权 Gamma"""
        S, T, sigma = self.params.S, self.params.T, self.params.sigma
        return norm.pdf(self.d1()) / (S * sigma * np.sqrt(T))
    
    def theta_call(self) -> float:
        """看涨期权 Theta"""
        S, K, T, r, sigma = (
            self.params.S, self.params.K, self.params.T,
            self.params.r, self.params.sigma
        )
        
        d1 = self.d1()
        d2 = self.d2()
        
        term1 = -S * norm.pdf(d1) * sigma / (2 * np.sqrt(T))
        term2 = r * K * np.exp(-r * T) * norm.cdf(d2)
        
        return term1 - term2
    
    def theta_put(self) -> float:
        """看跌期权 Theta"""
        S, K, T, r, sigma = (
            self.params.S, self.params.K, self.params.T,
            self.params.r, self.params.sigma
        )
        
        d1 = self.d1()
        d2 = self.d2()
        
        term1 = -S * norm.pdf(d1) * sigma / (2 * np.sqrt(T))
        term2 = -r * K * np.exp(-r * T) * norm.cdf(-d2)
        
        return term1 + term2
    
    def vega(self) -> float:
        """期权 Vega"""
        S, T = self.params.S, self.params.T
        return S * norm.pdf(self.d1()) * np.sqrt(T)
    
    def rho_call(self) -> float:
        """看涨期权 Rho"""
        K, T, r = self.params.K, self.params.T, self.params.r
        return K * T * np.exp(-r * T) * norm.cdf(self.d2())
    
    def rho_put(self) -> float:
        """看跌期权 Rho"""
        K, T, r = self.params.K, self.params.T, self.params.r
        return -K * T * np.exp(-r * T) * norm.cdf(-self.d2())
    
    def implied_volatility(self, market_price: float, option_type: str = "call",
                          tolerance: float = 1e-6, max_iterations: int = 100) -> float:
        """计算隐含波动率 (牛顿法)"""
        sigma = 0.3  # 初始猜测
        
        for _ in range(max_iterations):
            if option_type == "call":
                model_price = BlackScholesModel(BSParameters(
                    S=self.params.S, K=self.params.K, T=self.params.T,
                    r=self.params.r, sigma=sigma
                )).call_price()
            else:
                model_price = BlackScholesModel(BSParameters(
                    S=self.params.S, K=self.params.K, T=self.params.T,
                    r=self.params.r, sigma=sigma
                )).put_price()
            
            diff = model_price - market_price
            
            if abs(diff) < tolerance:
                return sigma
            
            # Vega 用于牛顿法
            vega = BlackScholesModel(BSParameters(
                S=self.params.S, K=self.params.K, T=self.params.T,
                r=self.params.r, sigma=sigma
            )).vega()
            
            if vega < 1e-10:
                break
            
            sigma -= diff / vega
        
        return sigma
    
    def analyze_sensitivity(self, param_name: str, param_range: np.ndarray) -> Dict[str, Any]:
        """参数敏感性分析"""
        results = {"param_values": param_range.tolist(), "call_prices": [], "put_prices": []}
        
        for value in param_range:
            # 创建新的参数集
            new_params_dict = self.params.to_dict()
            new_params_dict[param_name] = value
            new_params = BSParameters(**new_params_dict)
            
            # 计算价格
            model = BlackScholesModel(new_params)
            results["call_prices"].append(model.call_price())
            results["put_prices"].append(model.put_price())
        
        return results


def demo_black_scholes():
    """Black-Scholes 演示"""
    print("Black-Scholes 期权定价模型")
    print("=" * 50)
    
    # 创建模型
    params = BSParameters(S=100, K=100, T=1.0, r=0.05, sigma=0.2)
    model = BlackScholesModel(params)
    
    # 计算价格
    call_price = model.call_price()
    put_price = model.put_price()
    
    # 计算希腊字母
    delta_call = model.delta_call()
    gamma = model.gamma()
    theta_call = model.theta_call()
    vega = model.vega()
    
    print(f"标的资产价格 (S): ${params.S:.2f}")
    print(f"执行价格 (K): ${params.K:.2f}")
    print(f"到期时间 (T): {params.T:.2f} 年")
    print(f"无风险利率 (r): {params.r:.2%}")
    print(f"波动率 (σ): {params.sigma:.2%}")
    print()
    print(f"看涨期权价格: ${call_price:.4f}")
    print(f"看跌期权价格: ${put_price:.4f}")
    print()
    print(f"Delta (看涨): {delta_call:.4f}")
    print(f"Gamma: {gamma:.6f}")
    print(f"Theta (看涨): {theta_call:.4f}")
    print(f"Vega: {vega:.4f}")
    
    # 隐含波动率示例
    market_call_price = 10.0
    iv = model.implied_volatility(market_call_price, "call")
    print(f"\n市场看涨期权价格: ${market_call_price:.2f}")
    print(f"隐含波动率: {iv:.4f} ({iv:.2%})")
    
    return params, model


if __name__ == "__main__":
    demo_black_scholes()
