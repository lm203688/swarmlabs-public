"""仪器模拟器 (Instrument Simulator) — 模拟真实实验室仪器的输出数据。

对标：PyLabRobot SDK / Emerald Cloud Lab API / SLAS 2026 开放SDL平台
核心：在无需硬件的情况下模拟完整实验数据管线。

模拟仪器：
  1. 分光光度计 (Plate Reader): OD600 时间序列
  2. 溶氧探头 (DO Probe): DO 百分比时间序列
  3. pH 探头: pH 时间序列
  4. 温度计: 温度时间序列
  5. CO2 流量计: 尾气 CO2 时间序列
  6. 生物量分析仪: 干重 (DCW) 时间序列

输出格式与 SLAS 2026 开放SDL FAIR 数据主干兼容。
"""

from __future__ import annotations

import math
import numpy as np

# ---------------------------------------------------------------------------
# 仪器模拟器
# ---------------------------------------------------------------------------

class PlateReaderSimulator:
    """模拟分光光度计 OD600 输出。

    参数：
      mu_max: h⁻¹, Ks: g/L, Sf: g/L, X0: g/L, Yxs: g/g, T_opt: °C
      T: °C, pH: float, hours: float, dt: h, noise_std: float (OD 单位)
    """
    def __init__(self, mu_max=0.81, Ks=0.004, Sf=10.0, X0=0.01,
                 Yxs=0.5, T_opt=37.0, pH=7.0, T=37.0, hours=24.0, dt=0.1,
                 noise_std=0.02, seed=42):
        self.mu_max = mu_max
        self.Ks = Ks
        self.Sf = Sf
        self.X0 = X0
        self.Yxs = Yxs
        self.T_opt = T_opt
        self.T = T
        self.pH = pH
        self.hours = hours
        self.dt = dt
        self.noise_std = noise_std
        self.rng = np.random.default_rng(seed)

    def simulate(self) -> dict:
        """
        返回:
          {
            "instrument": "plate_reader",
            "parameter": "OD600",
            "time_h": [...],
            "od600": [...],
            "od600_std": [...],
            "metadata": {...},
          }
        """
        n_steps = int(self.hours / self.dt)
        t = np.linspace(0, self.hours, n_steps)

        # 温度修正
        T_factor = math.exp(-0.5 * ((self.T - self.T_opt) / 5.0) ** 2)
        mu_eff = self.mu_max * T_factor

        # 批次培养 ODE（Euler）
        X = np.zeros(n_steps)
        S = np.zeros(n_steps)
        X[0] = self.X0
        S[0] = self.Sf

        for i in range(1, n_steps):
            dX = mu_eff * S[i-1] / (self.Ks + S[i-1]) * X[i-1] * self.dt
            dS = -dX / self.Yxs
            X[i] = X[i-1] + dX
            S[i] = max(S[i-1] + dS, 0.0)

        # OD600 ≈ 0.35 * X (g/L)，典型 E. coli 标定
        od600_clean = 0.35 * X
        noise = self.rng.normal(0, self.noise_std, n_steps)
        od600 = np.clip(od600_clean + noise, 0, 4.0)

        return {
            "instrument": "plate_reader",
            "parameter": "OD600",
            "unit": "AU",
            "wavelength_nm": 600,
            "time_h": [round(float(x), 3) for x in t],
            "od600": [round(float(x), 4) for x in od600],
            "od600_std": [round(float(self.noise_std), 4)] * n_steps,
            "metadata": {
                "protocol": "batch_culture",
                "organism": "E. coli K-12",
                "temperature_C": self.T,
                "pH": self.pH,
                "media": "LB",
                "volume_mL": 2.0,
            },
        }


class DOProbeSimulator:
    """模拟溶氧探头（DO probe）输出。

    模拟 kLa 与摄氧率 (OUR) 的平衡。
    DO% 动态：饱和溶解氧=100%，呼吸速率与菌浓正相关。
    """
    def __init__(self, kLa=0.3, Csat=100.0, OUR_rate=0.05, hours=24.0, dt=0.1,
                 X0=0.01, mu=0.81, noise_std=2.0, seed=42):
        self.kLa = kLa
        self.Csat = Csat
        self.OUR_rate = OUR_rate
        self.hours = hours
        self.dt = dt
        self.X0 = X0
        self.mu = mu
        self.noise_std = noise_std
        self.rng = np.random.default_rng(seed)

    def simulate(self) -> dict:
        n_steps = int(self.hours / self.dt)
        t = np.linspace(0, self.hours, n_steps)

        DO = np.zeros(n_steps)
        DO[0] = 100.0  # 初始饱和
        X = np.zeros(n_steps)
        X[0] = self.X0

        for i in range(1, n_steps):
            dX = self.mu * X[i-1] * self.dt
            X[i] = X[i-1] + dX
            # OUR = OUR_rate * X (g O2 / L / h)
            our = self.OUR_rate * X[i]
            # DO 动态：kLa*(Csat-DO) - OUR
            dDO = self.kLa * (self.Csat - DO[i-1]) - our
            DO[i] = max(DO[i-1] + dDO, 0.0)

        noise = self.rng.normal(0, self.noise_std, n_steps)
        do_values = np.clip(DO + noise, 0, 100)

        return {
            "instrument": "do_probe",
            "parameter": "DO",
            "unit": "%_saturation",
            "type": "Clark_electrode",
            "time_h": [round(float(x), 3) for x in t],
            "do_pct": [round(float(x), 3) for x in do_values],
            "do_std": [round(float(self.noise_std), 3)] * n_steps,
            "metadata": {
                "klA_h": self.kLa,
                "Csat_pct": self.Csat,
                "OUR_rate_gO2_per_L_h": self.OUR_rate,
                "volume_L": 2.0,
            },
        }


class PHProbeSimulator:
    """模拟 pH 探头输出。

    微生物代谢产酸（pH 下降），培养基缓冲能力决定下降速度。
    """
    def __init__(self, initial_pH=7.0, acid_rate=0.02, buffer_capacity=0.5,
                 X0=0.01, mu=0.81, hours=24.0, dt=0.1, noise_std=0.05, seed=42):
        self.initial_pH = initial_pH
        self.acid_rate = acid_rate
        self.buffer_capacity = buffer_capacity
        self.X0 = X0
        self.mu = mu
        self.hours = hours
        self.dt = dt
        self.noise_std = noise_std
        self.rng = np.random.default_rng(seed)

    def simulate(self) -> dict:
        n_steps = int(self.hours / self.dt)
        t = np.linspace(0, self.hours, n_steps)

        pH = np.zeros(n_steps)
        pH[0] = self.initial_pH
        X = np.zeros(n_steps)
        X[0] = self.X0

        for i in range(1, n_steps):
            dX = self.mu * X[i-1] * self.dt
            X[i] = X[i-1] + dX
            # 产酸速率 ∝ 生物量，被缓冲能力衰减
            dpH = -self.acid_rate * X[i] / (self.buffer_capacity + X[i]) * self.dt
            pH[i] = max(pH[i-1] + dpH, 3.0)

        noise = self.rng.normal(0, self.noise_std, n_steps)
        ph_values = np.clip(pH + noise, 3.0, 9.0)

        return {
            "instrument": "ph_probe",
            "parameter": "pH",
            "unit": "pH",
            "type": "combination_glass",
            "time_h": [round(float(x), 3) for x in t],
            "ph": [round(float(x), 4) for x in ph_values],
            "ph_std": [round(float(self.noise_std), 4)] * n_steps,
            "metadata": {
                "initial_pH": self.initial_pH,
                "buffer_capacity_g_per_L": self.buffer_capacity,
                "acid_rate_g_per_L_h": self.acid_rate,
                "calibration_interval_h": 24,
            },
        }


class TempProbeSimulator:
    """模拟温度探头输出。

    设定点控制 + 扰动 + 冷却水响应延迟。
    """
    def __init__(self, setpoint_C=37.0, max_overshoot=0.5, recovery_time_h=0.2,
                 hours=24.0, dt=0.1, noise_std=0.1, disturbance_every_h=8.0, seed=42):
        self.setpoint = setpoint_C
        self.max_overshoot = max_overshoot
        self.recovery = recovery_time_h
        self.hours = hours
        self.dt = dt
        self.noise_std = noise_std
        self.disturbance_h = disturbance_every_h
        self.rng = np.random.default_rng(seed)

    def simulate(self) -> dict:
        n_steps = int(self.hours / self.dt)
        t = np.linspace(0, self.hours, n_steps)

        T = np.zeros(n_steps)
        T[0] = self.setpoint

        for i in range(1, n_steps):
            # 稳态控制：小幅波动
            T[i] = self.setpoint + self.rng.normal(0, 0.05)

        # 定期扰动（模拟培养基更换/换瓶）
        for i in range(n_steps):
            if t[i] > 0 and abs(t[i] - (self.disturbance_h + self.disturbance_h * int(t[i] / self.disturbance_h))) < 0.1:
                pass  # 扰动事件标记
            # 加扰动向
            disturbance = self.max_overshoot * math.sin(2 * math.pi * t[i] / self.disturbance_h)
            T[i] += disturbance * 0.3

        noise = self.rng.normal(0, self.noise_std, n_steps)
        T_values = T + noise

        return {
            "instrument": "temp_probe",
            "parameter": "Temperature",
            "unit": "C",
            "type": "PT100",
            "time_h": [round(float(x), 3) for x in t],
            "temperature_C": [round(float(x), 3) for x in T_values],
            "temp_std": [round(float(self.noise_std), 3)] * n_steps,
            "metadata": {
                "setpoint_C": self.setpoint,
                "control_type": "PID",
                "disturbance_interval_h": self.disturbance_h,
                "probe_depth_cm": 5.0,
            },
        }


class CO2FlowSimulator:
    """模拟尾气 CO2 流量计输出。

    基于呼吸商 (RQ) 与摄氧率。
    RQ = CO2 produced / O2 consumed ≈ 1.0 for glucose, <1 for ethanol production.
    """
    def __init__(self, RQ=1.0, OUR_rate=0.05, inflow_air_Lmin=2.0, X0=0.01,
                 mu=0.81, hours=24.0, dt=0.1, noise_std=0.01, seed=42):
        self.RQ = RQ
        self.OUR_rate = OUR_rate
        self.inflow = inflow_air_Lmin
        self.X0 = X0
        self.mu = mu
        self.hours = hours
        self.dt = dt
        self.noise_std = noise_std
        self.rng = np.random.default_rng(seed)

    def simulate(self) -> dict:
        n_steps = int(self.hours / self.dt)
        t = np.linspace(0, self.hours, n_steps)

        X = np.zeros(n_steps)
        X[0] = self.X0
        for i in range(1, n_steps):
            X[i] = X[i-1] * math.exp(self.mu * self.dt)

        # CO2 产生速率 ∝ OUR × RQ
        co2_rate = self.OUR_rate * X * self.RQ  # mol/min ≈ g/L/min
        co2_pct = co2_rate * 100.0  # % CO2 in exhaust (relative to inflow)

        noise = self.rng.normal(0, self.noise_std, n_steps)
        co2_values = np.clip(co2_pct + noise, 0, 15.0)

        return {
            "instrument": "co2_flow_meter",
            "parameter": "CO2",
            "unit": "%_v/v",
            "type": "NDIR",
            "time_h": [round(float(x), 3) for x in t],
            "co2_pct": [round(float(x), 4) for x in co2_values],
            "co2_std": [round(float(self.noise_std), 4)] * n_steps,
            "metadata": {
                "RQ": self.RQ,
                "OUR_rate_gO2_per_L_h": self.OUR_rate,
                "airflow_L_per_min": self.inflow,
                "inlet_CO2_pct": 0.04,
            },
        }


class DCWAnalyzerSimulator:
    """模拟干重（Dry Cell Weight）分析。

    通过在线浊度/离线离心称重获取生物量。
    离线数据点：每小时采一个样。
    """
    def __init__(self, mu_max=0.81, Ks=0.004, Sf=10.0, X0=0.01,
                 Yxs=0.5, T=37.0, T_opt=37.0, hours=24.0, sampling_dt_h=1.0,
                 noise_std=0.05, seed=42):
        self.mu_max = mu_max
        self.Ks = Ks
        self.Sf = Sf
        self.X0 = X0
        self.Yxs = Yxs
        self.T = T
        self.T_opt = T_opt
        self.hours = hours
        self.sampling_dt = sampling_dt_h
        self.noise_std = noise_std
        self.rng = np.random.default_rng(seed)

    def simulate(self) -> dict:
        n_steps = int(self.hours / self.sampling_dt)
        t = np.linspace(0, self.hours, n_steps + 1)
        n_steps = len(t)

        T_factor = math.exp(-0.5 * ((self.T - self.T_opt) / 5.0) ** 2)
        mu_eff = self.mu_max * T_factor
        dt_sub = 0.01

        X = np.zeros(n_steps)
        S = np.zeros(n_steps)
        X[0] = self.X0
        S[0] = self.Sf

        for i in range(1, n_steps):
            X_sub = X[i-1]
            S_sub = S[i-1]
            n_sub = int(self.sampling_dt / dt_sub)
            for _ in range(n_sub):
                dX = mu_eff * S_sub / (self.Ks + S_sub) * X_sub * dt_sub
                dS = -dX / self.Yxs
                X_sub = X_sub + dX
                S_sub = max(S_sub + dS, 0.0)
            X[i] = X_sub
            S[i] = S_sub
            # Integrate over sampling_dt with dt_sub sub-steps
            X_sub = X[i-1]
            S_sub = S[i-1]
            n_sub = int(self.sampling_dt / dt_sub)
            for _ in range(n_sub):
                dX = mu_eff * S_sub / (self.Ks + S_sub) * X_sub * dt_sub
                dS = -dX / self.Yxs
                X_sub = X_sub + dX
                S_sub = max(S_sub + dS, 0.0)
            X[i] = X_sub
            S[i] = S_sub

        noise = self.rng.normal(0, self.noise_std, n_steps)
        dcw = np.clip(X + noise, 0, 50.0)

        return {
            "instrument": "dcw_analyzer",
            "parameter": "DCW",
            "unit": "g/L",
            "type": "offline_centrifuge_weighing",
            "time_h": [round(float(x), 2) for x in t],
            "dcw_gL": [round(float(x), 3) for x in dcw],
            "dcw_std": [round(float(self.noise_std), 4)] * n_steps,
            "metadata": {
                "sampling_interval_h": self.sampling_dt,
                "centrifuge_g": 10000,
                "drying_temp_C": 105,
                "drying_hours": 12,
            },
        }


# ---------------------------------------------------------------------------
# 完整实验管线模拟器（多仪器联合）
# ---------------------------------------------------------------------------

def full_experiment_simulation(mu_max=0.81, Ks=0.004, Sf=10.0, X0=0.01,
                               Yxs=0.5, T=37.0, pH=7.0, hours=24.0, dt=0.1,
                               noise_std=0.02, seed=42) -> dict:
    """
    运行全仪器模拟管线，返回所有仪器输出。

    返回:
      {
        "experiment_id": str,
        "timestamp": str,
        "instruments": {
          "plate_reader": {...},
          "do_probe": {...},
          "ph_probe": {...},
          "temperature": {...},
          "co2_meter": {...},
          "dcw": {...},
        },
        "summary": {...},
      }
    """
    import uuid
    from datetime import datetime

    experiment_id = f"exp_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

    result = {
        "experiment_id": experiment_id,
        "timestamp": datetime.now().isoformat(),
        "parameters": {
            "mu_max": mu_max, "Ks": Ks, "Sf": Sf, "X0": X0,
            "Yxs": Yxs, "T": T, "pH": pH, "hours": hours, "dt": dt,
        },
        "instruments": {},
    }

    result["instruments"]["plate_reader"] = PlateReaderSimulator(
        mu_max=mu_max, Ks=Ks, Sf=Sf, X0=X0, Yxs=Yxs, T=T, pH=pH,
        hours=hours, dt=dt, noise_std=noise_std, seed=seed).simulate()
    result["instruments"]["do_probe"] = DOProbeSimulator(
        kLa=0.3, OUR_rate=0.05, X0=X0, mu=mu_max,
        hours=hours, dt=dt, noise_std=2.0, seed=seed).simulate()
    result["instruments"]["ph_probe"] = PHProbeSimulator(
        initial_pH=pH, acid_rate=0.02, X0=X0, mu=mu_max,
        hours=hours, dt=dt, noise_std=0.05, seed=seed).simulate()
    result["instruments"]["temperature"] = TempProbeSimulator(
        setpoint_C=T, hours=hours, dt=dt, noise_std=0.1, seed=seed).simulate()
    result["instruments"]["co2_meter"] = CO2FlowSimulator(
        RQ=1.0, OUR_rate=0.05, X0=X0, mu=mu_max,
        hours=hours, dt=dt, noise_std=0.01, seed=seed).simulate()
    result["instruments"]["dcw"] = DCWAnalyzerSimulator(
        mu_max=mu_max, Ks=Ks, Sf=Sf, X0=X0, Yxs=Yxs, T=T,
        hours=hours, sampling_dt_h=1.0, noise_std=0.05, seed=seed).simulate()

    # 计算关键汇总指标
    od = result["instruments"]["plate_reader"]["od600"]
    dcw = result["instruments"]["dcw"]["dcw_gL"]
    do = result["instruments"]["do_probe"]["do_pct"]

    result["summary"] = {
        "final_od600": round(od[-1], 3),
        "final_dcw_gL": round(dcw[-1], 3),
        "min_do_pct": round(min(do), 2),
        "max_do_pct": round(max(do), 2),
        "total_data_points": sum(len(v.get("time_h", [])) for v in result["instruments"].values()),
        "instrument_count": len(result["instruments"]),
    }

    return result


if __name__ == "__main__":
    print("=" * 70)
    print("仪器模拟器 — 完整实验管线演示")
    print("=" * 70)

    result = full_experiment_simulation()

    print(f"实验 ID: {result['experiment_id']}")
    print(f"参数: μmax={result['parameters']['mu_max']} Ks={result['parameters']['Ks']} "
          f"Sf={result['parameters']['Sf']} T={result['parameters']['T']}°C")
    print()

    for name, inst in result["instruments"].items():
        n_pts = len(inst["time_h"])
        first_t = inst["time_h"][0]
        last_t = inst["time_h"][-1]
        param = inst["parameter"]
        values = inst.get(param.lower() + "_pct", inst.get("od600", inst.get("dcw_gL", inst.get("ph", inst.get("temperature_C", [])))))
        print(f"  {name:15s}  {param:12s}  {n_pts:4d} 点  "
              f"t=[{first_t:.0f}, {last_t:.0f}]h  "
              f"range=[{values[0]:.3f}, {values[-1]:.3f}]")

    print()
    print(f"汇总: OD600={result['summary']['final_od600']} "
          f"DCW={result['summary']['final_dcw_gL']} g/L "
          f"DO范围=[{result['summary']['min_do_pct']}, {result['summary']['max_do_pct']}]%")
    print(f"总数据点: {result['summary']['total_data_points']}  仪器数: {result['summary']['instrument_count']}")
    print("=" * 70)
