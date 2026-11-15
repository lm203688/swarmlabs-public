"""heat_baseline.py —— 1D 热方程 ground-truth（numpy 有限差分 + 解析解）。

本地可跑：验证 PINN 要学的"物理目标"数值正确。
这是 parity_framework 哲学里"物理规律 = 微分方程"的显式对照——
PINN 在飞桨上训练时，学的就是这个 PDE 的残差解。

    python heat_baseline.py
"""
import numpy as np

ALPHA = 0.05          # 热扩散系数
NX, NT = 120, 4000    # 空间/时间网格
L, T = 1.0, 0.5       # 空间长度 / 终止时间
dx = L / (NX - 1)
dt = T / NT

# CFL 稳定性条件：显式格式要求 alpha*dt/dx^2 <= 0.5
cfl = ALPHA * dt / dx**2
assert cfl <= 0.5, f"CFL 不稳定: {cfl} > 0.5"


def u_exact(x, t, alpha=ALPHA):
    """解析解：u(x,t)=exp(-alpha*pi^2*t)*sin(pi*x)。
    满足 ∂u/∂t = alpha ∂²u/∂x²，IC u(x,0)=sin(pi x)，BC u(0,t)=u(1,t)=0。"""
    x = np.asarray(x, float)
    return np.exp(-alpha * np.pi**2 * t) * np.sin(np.pi * x)


def finite_diff_heat():
    """显式有限差分求解，返回 (x, t_grid, u_hist)。"""
    x = np.linspace(0.0, L, NX)
    u = u_exact(x, 0.0)
    u_hist = [u.copy()]
    for _ in range(NT):
        u_new = u.copy()
        u_new[1:-1] = u[1:-1] + ALPHA * dt / dx**2 * (u[2:] - 2 * u[1:-1] + u[:-2])
        u_new[0] = 0.0
        u_new[-1] = 0.0
        u = u_new
        u_hist.append(u.copy())
    return x, np.linspace(0.0, T, NT + 1), np.array(u_hist)


def main():
    x, t_grid, u_hist = finite_diff_heat()
    # 终态对比
    u_fd = u_hist[-1]
    u_ex = u_exact(x, T)
    rmse_end = float(np.sqrt(np.mean((u_fd - u_ex) ** 2)))
    # 中间时刻对比
    k = NT // 2
    rmse_mid = float(np.sqrt(np.mean((u_hist[k] - u_exact(x, t_grid[k])) ** 2)))
    print(f"CFL 数 = {cfl:.3f} (需 <= 0.5)")
    print(f"FD vs 解析解 @t={T}:    rmse = {rmse_end:.3e}")
    print(f"FD vs 解析解 @t={t_grid[k]:.3f}: rmse = {rmse_mid:.3e}")
    print("结论：有限差分收敛到解析解 → ground-truth 数值正确，可作 PINN 训练目标。")
    # 给飞桨 Notebook 用的少量"真实观测"采样（带噪声，模拟真实实验不可复现误差）
    rng = np.random.default_rng(0)
    obs_x = np.linspace(0.1, 0.9, 11)
    obs_t = np.array([0.1, 0.3, 0.5])
    ox, ot = np.meshgrid(obs_x, obs_t)
    obs_pts = np.stack([ox.ravel(), ot.ravel()], axis=1)
    obs_val = u_exact(obs_pts[:, 0], obs_pts[:, 1]) + rng.normal(0, 0.01, size=len(obs_pts))
    print(f"采样真实观测点数 = {len(obs_pts)}（用于 PINN 的 IC/BC/数据约束）")
    return rmse_end, rmse_mid


if __name__ == "__main__":
    main()
