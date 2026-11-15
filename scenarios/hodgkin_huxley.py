"""
Hodgkin-Huxley 神经元模型
神经科学经典神经元动作电位模型
"""

import numpy as np
from typing import List, Dict, Any
from dataclasses import dataclass


@dataclass
class HHParameters:
    """Hodgkin-Huxley 参数"""
    C_m: float = 1.0       # 膜电容 (uF/cm^2)
    g_Na: float = 120.0    # 钠通道最大电导 (mS/cm^2)
    g_K: float = 36.0      # 钾通道最大电导 (mS/cm^2)
    g_L: float = 0.3       # 漏电流电导 (mS/cm^2)
    E_Na: float = 50.0     # 钠平衡电位 (mV)
    E_K: float = -77.0     # 钾平衡电位 (mV)
    E_L: float = -54.4     # 漏电流平衡电位 (mV)
    
    def to_dict(self) -> Dict[str, float]:
        return {
            "C_m": self.C_m,
            "g_Na": self.g_Na,
            "g_K": self.g_K,
            "g_L": self.g_L,
            "E_Na": self.E_Na,
            "E_K": self.E_K,
            "E_L": self.E_L
        }


class HodgkinHuxleyModel:
    """Hodgkin-Huxley 模型"""
    
    def __init__(self, params: HHParameters = None):
        self.params = params or HHParameters()
        self.V = -65.0  # 初始膜电位 (mV)
        self.m = 0.05   # 钠通道激活门控变量
        self.h = 0.6    # 钠通道失活门控变量
        self.n = 0.32   # 钾通道门控变量
        
    def alpha_m(self, V: float) -> float:
        """m 门控变量的 alpha 函数"""
        return 0.1 * (V + 40.0) / (1.0 - np.exp(-(V + 40.0) / 10.0))
    
    def beta_m(self, V: float) -> float:
        """m 门控变量的 beta 函数"""
        return 4.0 * np.exp(-(V + 65.0) / 18.0)
    
    def alpha_h(self, V: float) -> float:
        """h 门控变量的 alpha 函数"""
        return 0.07 * np.exp(-(V + 65.0) / 20.0)
    
    def beta_h(self, V: float) -> float:
        """h 门控变量的 beta 函数"""
        return 1.0 / (1.0 + np.exp(-(V + 35.0) / 10.0))
    
    def alpha_n(self, V: float) -> float:
        """n 门控变量的 alpha 函数"""
        return 0.01 * (V + 55.0) / (1.0 - np.exp(-(V + 55.0) / 10.0))
    
    def beta_n(self, V: float) -> float:
        """n 门控变量的 beta 函数"""
        return 0.125 * np.exp(-(V + 65.0) / 80.0)
    
    def derivatives(self, V: float, m: float, h: float, n: float, 
                    I_ext: float = 0.0) -> tuple:
        """计算导数"""
        C_m = self.params.C_m
        g_Na, g_K, g_L = self.params.g_Na, self.params.g_K, self.params.g_L
        E_Na, E_K, E_L = self.params.E_Na, self.params.E_K, self.params.E_L
        
        # 电流
        I_Na = g_Na * (m**3) * h * (V - E_Na)
        I_K = g_K * (n**4) * (V - E_K)
        I_L = g_L * (V - E_L)
        
        # 膜电位变化
        dV = (I_ext - I_Na - I_K - I_L) / C_m
        
        # 门控变量变化
        dm = self.alpha_m(V) * (1 - m) - self.beta_m(V) * m
        dh = self.alpha_h(V) * (1 - h) - self.beta_h(V) * h
        dn = self.alpha_n(V) * (1 - n) - self.beta_n(V) * n
        
        return dV, dm, dh, dn
    
    def step(self, I_ext: float = 0.0, dt: float = 0.01) -> float:
        """欧拉法单步"""
        dV, dm, dh, dn = self.derivatives(self.V, self.m, self.h, self.n, I_ext)
        
        self.V += dV * dt
        self.m += dm * dt
        self.h += dh * dt
        self.n += dn * dt
        
        # 确保门控变量在 [0, 1] 范围内
        self.m = np.clip(self.m, 0, 1)
        self.h = np.clip(self.h, 0, 1)
        self.n = np.clip(self.n, 0, 1)
        
        return self.V
    
    def simulate(self, I_ext_func, duration: float = 50.0, 
                 dt: float = 0.01) -> Dict[str, Any]:
        """运行模拟"""
        steps = int(duration / dt)
        time = np.arange(0, duration, dt)
        
        V_history = []
        m_history = []
        h_history = []
        n_history = []
        
        for i, t in enumerate(time):
            I_ext = I_ext_func(t)
            V = self.step(I_ext, dt)
            
            V_history.append(V)
            m_history.append(self.m)
            h_history.append(self.h)
            n_history.append(self.n)
        
        return {
            "time": time.tolist(),
            "V": V_history,
            "m": m_history,
            "h": h_history,
            "n": n_history
        }
    
    def analyze_action_potential(self, V_history: List[float], 
                                 threshold: float = 0.0) -> Dict[str, Any]:
        """分析动作电位"""
        # 找到动作电位峰值
        peaks = []
        for i in range(1, len(V_history) - 1):
            if V_history[i] > V_history[i-1] and V_history[i] > V_history[i+1]:
                if V_history[i] > threshold:
                    peaks.append(V_history[i])
        
        # 计算频率 (假设固定时间步长)
        if len(peaks) > 1:
            avg_amplitude = np.mean(peaks)
            frequency = len(peaks) / (len(V_history) * 0.01)  # 假设 dt=0.01
        else:
            avg_amplitude = peaks[0] if peaks else 0
            frequency = 0
        
        return {
            "num_peaks": len(peaks),
            "avg_amplitude": avg_amplitude,
            "max_amplitude": max(peaks) if peaks else 0,
            "frequency_hz": frequency,
            "threshold": threshold
        }


def demo_hodgkin_huxley():
    """Hodgkin-Huxley 演示"""
    print("Hodgkin-Huxley 神经元模型")
    print("=" * 50)
    
    model = HodgkinHuxleyModel()
    
    # 外部电流函数 (脉冲刺激)
    def I_ext_pulse(t):
        if 5.0 <= t <= 5.1 or 10.0 <= t <= 10.1 or 15.0 <= t <= 15.1:
            return 10.0  # uA/cm^2
        return 0.0
    
    # 运行模拟
    result = model.simulate(I_ext_pulse, duration=20.0)
    
    # 分析动作电位
    ap_analysis = model.analyze_action_potential(result["V"])
    
    print(f"模拟时间: 20.0 ms")
    print(f"动作电位数量: {ap_analysis['num_peaks']}")
    print(f"平均振幅: {ap_analysis['avg_amplitude']:.2f} mV")
    print(f"最大振幅: {ap_analysis['max_amplitude']:.2f} mV")
    print(f"发放频率: {ap_analysis['frequency_hz']:.2f} Hz")
    
    return result, ap_analysis


if __name__ == "__main__":
    demo_hodgkin_huxley()
