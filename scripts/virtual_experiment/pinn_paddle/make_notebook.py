"""make_notebook.py —— 生成 heat_pinn_paddle.ipynb（飞桨 PINN 一键跑 Notebook）。

运行：python make_notebook.py  -> 产出 heat_pinn_paddle.ipynb

设计要点：
- Notebook 在百度飞桨 AI Studio 运行；**CPU / GPU 均可**（CPU 自动降规模，见 cell 0）。
- 其中 numpy ground-truth cell 本地也能跑，用于验证物理目标。
- 只用 PaddlePaddle 原生 API（不混用 PyTorch API）。
"""
import json
import os

cells = []

cells.append(("markdown", """# 1D 热方程 PINN · 百度飞桨一键跑
> 把 `parity_framework` 的 **Tier 2 PINN 桩**（`fidelity_ladder.py` 标注为 `scale-when-funded`）
> 在**国产算力（飞桨 AI Studio）**上实装为一个可运行原型。**CPU / GPU 都能跑**。

## 对齐 SwarmLabs 核心哲学（parity_framework.py 顶部）
1. **物理规律 = 微分方程**：用热方程 PDE 残差作为无监督损失。
2. **随机性 = 噪声 + 采样**：多随机种子训练，模拟真实实验的不可复现误差。
3. **UQ 是一等公民**：训练多个随机种子的 PINN（深度集成），预测返回 `mean ± std`，
   并用 95% 置信区间覆盖率（coverage≈0.95）量化"虚拟实验的可信度"，
   与 `parity_framework.Calibrator` 的指标完全对齐。

## 与 GOAI 复赛代码包的关系（重要）
- 本 Notebook **独立于** `agentteams/`（GOAI 复赛交付物）。它只实装底层科学计算引擎的
  PINN 这一级，**不改动** `agentteams/` 的 numpy 核心，因此不影响参赛资格。
- 若实跑通过，可作为"国产算力适配 + 科学 ML"的独立加分证据。
"""))

cells.append(("code", '''# 0) 环境检查 + 设备自适应（CPU / GPU 都能跑）
import time

import paddle

print("PaddlePaddle:", paddle.__version__)

# 注意：is_compiled_with_cuda() 只说明"编译时带 CUDA"，不代表运行时真有卡。
# 运行时是否有可用 GPU，必须看 device_count()。
n_gpu = 0
try:
    n_gpu = paddle.device.cuda.device_count()
except Exception as e:
    print("查询 GPU 数量失败：", e)

if n_gpu > 0:
    paddle.device.set_device("gpu:0")
    DEVICE = "gpu"
else:
    paddle.device.set_device("cpu")
    DEVICE = "cpu"

print("可用 GPU 数:", n_gpu, "| 实际使用设备:", paddle.get_device())

# ---- 规模自适应：CPU 用 QUICK 档，GPU 用 FULL 档 ----
if DEVICE == "gpu":
    PROFILE = "FULL"
    N_COLLOC, N_IC, N_BC = 4000, 2000, 2000
    EPOCHS_SINGLE, EPOCHS_ENS, N_ENS = 6000, 4000, 5
else:
    PROFILE = "QUICK"
    N_COLLOC, N_IC, N_BC = 2000, 1000, 1000
    EPOCHS_SINGLE, EPOCHS_ENS, N_ENS = 3000, 2500, 5

print("规模档位:", PROFILE,
      "| 配点=%d 初值=%d 边界=%d" % (N_COLLOC, N_IC, N_BC),
      "| 单模型 epoch=%d 集成 epoch=%d x %d 个" % (EPOCHS_SINGLE, EPOCHS_ENS, N_ENS))
print("CPU 下 QUICK 档预计总耗时 5-15 分钟；GPU 下 FULL 档预计数分钟。")
'''))

cells.append(("markdown", """## 物理问题
一维热方程：

$$\\frac{\\partial u}{\\partial t} = \\alpha \\frac{\\partial^2 u}{\\partial x^2}, \\quad x\\in[0,1],\\ t\\in[0,0.5]$$

- 初值 $u(x,0)=\\sin(\\pi x)$
- 边界 $u(0,t)=u(1,t)=0$
- 解析解 $u(x,t)=e^{-\\alpha\\pi^2 t}\\sin(\\pi x)$（用于校验 PINN）

PINN 目标：用神经网络 $u_\\theta(x,t)$ 拟合，使 PDE 残差 + 初/边值条件共同最小。
"""))

cells.append(("code", '''# 1) ground-truth：numpy 有限差分 + 解析解（本地无 paddle 也能跑，验证物理目标正确）
import numpy as np

ALPHA = 0.05
NX, NT = 120, 4000
L, T = 1.0, 0.5
dx = L / (NX - 1)
dt = T / NT
cfl = ALPHA * dt / dx**2
assert cfl <= 0.5, "CFL 不稳定：%.4f" % cfl
print("CFL = %.4f (<=0.5 稳定)" % cfl)


def u_exact(x, t, alpha=ALPHA):
    x = np.asarray(x, float)
    return np.exp(-alpha * np.pi**2 * t) * np.sin(np.pi * x)


x = np.linspace(0.0, L, NX)
u = u_exact(x, 0.0)
u_hist = [u.copy()]
for _ in range(NT):
    u_new = u.copy()
    u_new[1:-1] = u[1:-1] + ALPHA * dt / dx**2 * (u[2:] - 2 * u[1:-1] + u[:-2])
    u_new[0] = u_new[-1] = 0.0
    u = u_new
    u_hist.append(u.copy())
u_hist = np.array(u_hist)

fd_rmse = float(np.sqrt(np.mean((u_hist[-1] - u_exact(x, T))**2)))
print("FD vs 解析解 @t=%.2f: rmse = %.3e" % (T, fd_rmse))
print("ground-truth 数值正确，可作 PINN 训练目标与校验基准。")
'''))

cells.append(("markdown", """## PINN 设计
- 网络：MLP $(x,t)\\to u$，Tanh 激活。
- 损失 = PDE 残差 MSE + 初值 MSE + 边界 MSE。
- **UQ（深度集成）**：用不同随机种子训练 $N$ 个 PINN，预测取集成的
  `mean` 与 `std`，95% 区间 = `mean ± 1.96·std`。
"""))

cells.append(("code", '''# 2) PINN 模型（纯 PaddlePaddle API —— 不使用 PyTorch 的 requires_grad_）
import paddle
import paddle.nn as nn

ALPHA = 0.05


class PINN(nn.Layer):
    def __init__(self, widths=(2, 32, 32, 32, 1)):
        super().__init__()
        self.linears = nn.LayerList()
        for i in range(len(widths) - 1):
            self.linears.append(nn.Linear(widths[i], widths[i + 1]))

    def forward(self, xt):
        h = xt
        for i, lin in enumerate(self.linears):
            h = lin(h)
            if i < len(self.linears) - 1:
                h = paddle.tanh(h)
        return h


def pde_residual(model, xt, alpha=ALPHA):
    """热方程残差 u_t - alpha * u_xx。

    Paddle 里开启对输入求导用 `stop_gradient = False`
    （PyTorch 的 requires_grad_() 在 Paddle 中不存在）。
    """
    xt = xt.detach().clone()
    xt.stop_gradient = False
    u = model(xt)
    du = paddle.grad(u, xt, create_graph=True)[0]        # [du/dx, du/dt]
    u_x = du[:, 0:1]
    u_t = du[:, 1:2]
    u_xx = paddle.grad(u_x, xt, create_graph=True)[0][:, 0:1]
    return u_t - alpha * u_xx


# 快速自检：随机初始化的网络也应能算出有限的残差（不 NaN、形状正确）
_probe = paddle.to_tensor(np.array([[0.3, 0.1], [0.7, 0.4]], dtype="float32"))
_r = pde_residual(PINN(), _probe)
print("残差自检 shape=%s  finite=%s" % (tuple(_r.shape), bool(paddle.isfinite(_r).all())))
'''))

cells.append(("code", '''# 3) 构造训练点：PDE 配点 + 初值 + 边界
import numpy as np
import paddle

rng = np.random.default_rng(0)

# PDE 配点
xc = rng.uniform(0, 1, (N_COLLOC, 1))
tc = rng.uniform(0, 0.5, (N_COLLOC, 1))
xt_colloc = paddle.to_tensor(np.hstack([xc, tc]).astype("float32"))

# 初值 t=0
xi = rng.uniform(0, 1, (N_IC, 1))
xt_ic = paddle.to_tensor(np.hstack([xi, np.zeros((N_IC, 1))]).astype("float32"))
u_ic = paddle.to_tensor(np.sin(np.pi * xi).astype("float32"))

# 边界 x=0 与 x=1（各一半）
half = N_BC // 2
tb = rng.uniform(0, 0.5, (N_BC, 1))
xb0 = paddle.to_tensor(np.hstack([np.zeros((half, 1)), tb[:half]]).astype("float32"))
xb1 = paddle.to_tensor(np.hstack([np.ones((half, 1)), tb[half:half * 2]]).astype("float32"))

print("配点 %d / 初值 %d / 边界 %d" % (len(xt_colloc), len(xt_ic), len(xb0) + len(xb1)))
'''))

cells.append(("code", '''# 4) 训练单个 PINN（固定 seed 以便复现）
import time


def train_one(seed=0, epochs=None, lr=1e-3, verbose=True):
    epochs = EPOCHS_SINGLE if epochs is None else epochs
    paddle.seed(seed)
    np.random.seed(seed)
    model = PINN()
    opt = paddle.optimizer.Adam(parameters=model.parameters(), learning_rate=lr)
    log_every = max(1, epochs // 5)
    t0 = time.time()
    for ep in range(epochs):
        loss_pde = (pde_residual(model, xt_colloc)**2).mean()
        loss_ic = ((model(xt_ic) - u_ic)**2).mean()
        loss_bc = (model(xb0)**2).mean() + (model(xb1)**2).mean()
        loss = loss_pde + loss_ic + loss_bc
        loss.backward()
        opt.step()
        opt.clear_grad()
        if verbose and ep % log_every == 0:
            print("  seed=%d ep=%d loss=%.3e (pde=%.3e ic=%.3e bc=%.3e)"
                  % (seed, ep, float(loss), float(loss_pde), float(loss_ic), float(loss_bc)))
    if verbose:
        print("  seed=%d 完成，用时 %.1fs，末端 loss=%.3e" % (seed, time.time() - t0, float(loss)))
    return model


print("训练单模型（档位 %s，%d epoch）..." % (PROFILE, EPOCHS_SINGLE))
model0 = train_one(seed=0)

# 单模型精度快速核对（对解析解）
xs_chk = np.linspace(0, 1, 101).astype("float32")
xt_chk = paddle.to_tensor(np.stack([xs_chk, np.full_like(xs_chk, 0.5)], axis=1))
pred_chk = model0(xt_chk).numpy().ravel()
true_chk = np.exp(-ALPHA * np.pi**2 * 0.5) * np.sin(np.pi * xs_chk)
print("单模型 @t=0.5 rmse = %.3e" % float(np.sqrt(np.mean((pred_chk - true_chk)**2))))
'''))

cells.append(("code", '''# 5) UQ：集成多个随机种子 -> mean ± std -> 95% 覆盖率
import numpy as np

# 测试网格：50 x 50，index = i*50 + j 对应 (x_i, t_j)
xg = np.linspace(0, 1, 50)
tg = np.linspace(0, 0.5, 50)
xt_test_np = np.hstack([
    np.repeat(xg[:, None], 50, axis=0),
    np.tile(tg[:, None], (50, 1)),
]).astype("float32")
xt_test = paddle.to_tensor(xt_test_np)

print("集成训练 %d 个模型（每个 %d epoch）..." % (N_ENS, EPOCHS_ENS))
models = []
for s in range(N_ENS):
    print("[%d/%d]" % (s + 1, N_ENS))
    models.append(train_one(seed=s, epochs=EPOCHS_ENS, verbose=True))

preds = np.stack([m(xt_test).numpy().ravel() for m in models], axis=0)
mean = preds.mean(axis=0)
std = preds.std(axis=0)

u_true = np.exp(-ALPHA * np.pi**2 * xt_test_np[:, 1]) * np.sin(np.pi * xt_test_np[:, 0])
rmse = float(np.sqrt(np.mean((mean - u_true)**2)))
lo, hi = mean - 1.96 * std, mean + 1.96 * std
coverage = float(np.mean((u_true >= lo) & (u_true <= hi)))
mean_width = float(np.mean(hi - lo))

print()
print("=== 集成 PINN 原始结果（未校准，档位 %s，设备 %s）===" % (PROFILE, paddle.get_device()))
print("rmse            = %.3e" % rmse)
print("coverage(95%%)   = %.3f   (目标 ~0.95)" % coverage)
print("平均区间宽度     = %.3e" % mean_width)
print()
if coverage < 0.5:
    print("判读：欠覆盖 -> 误差棒过窄、模型过自信。")
    print("      这是深度集成的已知缺陷：std 只度量'模型间分歧'(epistemic)，")
    print("      不含系统性偏差。对应 parity_framework 里 GP 的 `var + noise_var` 那一项。")
    print("      -> 见下一个 cell 的保形校准。**不要靠调参硬凑覆盖率。**")
elif coverage > 0.995:
    print("判读：过覆盖 -> 误差棒过宽，虽保守但信息量低。")
else:
    print("判读：覆盖率在合理区间，虚拟实验误差棒基本可信。")
'''))

cells.append(("markdown", """## UQ 校准：为什么必须做，以及怎么做才诚实

上一个 cell 通常会看到 **coverage 远低于 0.95**（欠覆盖）。这不是 bug，是深度集成的
**结构性缺陷**：

- 集成的 `std` 只反映"不同随机种子的模型彼此有多不一致"（epistemic 不确定性）；
- 但 PINN 的主要误差是**所有模型朝同一方向偏**（例如边界层欠拟合），
  这部分系统偏差集成内部看不见，于是误差棒天生偏窄。
- `parity_framework` 的 GP 用 `var + noise_var` 补这一项；深度集成需要
  **保形校准（conformal calibration）** 来补。

**做法（split conformal）**：把测试点随机分成校准集 / 验证集。
在校准集上求经验分位数，用它替代理论上的 1.96；再到**没参与校准的验证集**上报告覆盖率。
只有验证集上的覆盖率才算数——这样不存在"用同一批数据凑指标"的自欺。

两种打分方式都报：
1. **归一化残差**（保留 `std` 的空间异质性，区间宽窄随位置变化）；
2. **绝对残差**（等宽区间，理论最干净）。
"""))

cells.append(("code", '''# 5b) UQ 保形校准：cal/val 分割，只在未参与校准的 val 集上报告覆盖率
import numpy as np

ALPHA_LEVEL = 0.05          # 目标 95% 覆盖
rng_cal = np.random.default_rng(12345)

n_pts = len(mean)
perm = rng_cal.permutation(n_pts)
n_cal = n_pts // 2
cal_idx, val_idx = perm[:n_cal], perm[n_cal:]

err = np.abs(u_true - mean)
std_safe = np.maximum(std, 1e-12)

# 有限样本修正的分位数水平（split conformal 标准做法）
q_level = min(1.0, np.ceil((n_cal + 1) * (1 - ALPHA_LEVEL)) / n_cal)

# --- 方法 1：归一化残差打分（异方差，保留 std 形状）---
score_norm = err[cal_idx] / std_safe[cal_idx]
tau = float(np.quantile(score_norm, q_level))
half_norm = tau * std_safe
cov_val_norm = float(np.mean(err[val_idx] <= half_norm[val_idx]))
width_norm = float(np.mean(2 * half_norm))

# --- 方法 2：绝对残差打分（等宽区间）---
q_abs = float(np.quantile(err[cal_idx], q_level))
cov_val_abs = float(np.mean(err[val_idx] <= q_abs))
width_abs = float(2 * q_abs)

# --- 未校准基线在同一 val 集上的覆盖率（公平对比）---
cov_val_raw = float(np.mean(err[val_idx] <= 1.96 * std_safe[val_idx]))
width_raw = float(np.mean(2 * 1.96 * std_safe))

print("校准集 %d 点 / 验证集 %d 点 | 分位数水平 %.4f" % (n_cal, len(val_idx), q_level))
print()
print("%-28s %10s %14s" % ("方法", "val覆盖率", "平均区间宽度"))
print("-" * 56)
print("%-28s %10.3f %14.3e" % ("未校准 (z=1.96)", cov_val_raw, width_raw))
print("%-28s %10.3f %14.3e" % ("保形-归一化残差", cov_val_norm, width_norm))
print("%-28s %10.3f %14.3e" % ("保形-绝对残差", cov_val_abs, width_abs))
print()
print("过自信倍数 tau/1.96 = %.2f" % (tau / 1.96))
print("  -> 含义：原始集成误差棒需要放大约 %.1f 倍才达到名义 95%% 覆盖。" % (tau / 1.96))
print("     这个倍数本身就是'深度集成有多过自信'的定量证据。")
print()
best = max([("保形-归一化残差", cov_val_norm), ("保形-绝对残差", cov_val_abs)],
           key=lambda kv: -abs(kv[1] - 0.95))
print("校准后 cal_err = |coverage - 0.95|：")
print("  未校准        : %.3f" % abs(cov_val_raw - 0.95))
print("  归一化残差校准 : %.3f" % abs(cov_val_norm - 0.95))
print("  绝对残差校准   : %.3f" % abs(cov_val_abs - 0.95))
print()
print("诚实结论：校准让误差棒可信，代价是区间变宽 %.1f 倍。"
      % (width_norm / width_raw if width_raw > 0 else float("nan")))
print("宽而诚实 > 窄而过自信 —— 与 parity_framework 的 UQ 底线一致。")
'''))

cells.append(("code", '''# 6) 可视化：终态 t=0.5 的预测 vs 解析解，未校准 vs 保形校准误差棒对比
# 说明：标签用英文，避免 AI Studio 默认镜像缺中文字体导致方框乱码。
import matplotlib.pyplot as plt

M = mean.reshape(50, 50)      # [i, j] = (x_i, t_j)
S = std.reshape(50, 50)
U = u_true.reshape(50, 50)
H = (tau * std_safe).reshape(50, 50)     # 保形校准后的半宽

m_end, s_end, t_end, h_end = M[:, -1], S[:, -1], U[:, -1], H[:, -1]

fig, axes = plt.subplots(1, 2, figsize=(12, 4), sharey=True)

for ax, (half, name, cov) in zip(axes, [
    (1.96 * s_end, "uncalibrated (z=1.96)", cov_val_raw),
    (h_end, "conformal calibrated", cov_val_norm),
]):
    ax.plot(xg, t_end, "k-", lw=2, label="analytic solution")
    ax.plot(xg, m_end, "b--", lw=2, label="PINN ensemble mean")
    ax.fill_between(xg, m_end - half, m_end + half,
                    alpha=0.25, color="b", label="95% interval")
    ax.set_xlabel("x")
    ax.set_title("%s\\nval coverage = %.3f" % (name, cov))
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=8)

axes[0].set_ylabel("u(x, t=0.5)")
fig.suptitle("Heat equation PINN on PaddlePaddle (%s / %s)  rmse=%.2e"
             % (PROFILE, paddle.get_device(), rmse))
plt.tight_layout()
plt.show()

print("rmse=%.3e" % rmse)
print("coverage  uncalibrated=%.3f  ->  conformal=%.3f  (target 0.95)"
      % (cov_val_raw, cov_val_norm))
'''))

cells.append(("markdown", """## 在百度飞桨 AI Studio 上运行本 Notebook
1. 打开 https://aistudio.baidu.com/ ，用百度账号登录（需实名）。
2. 「项目」→「新建项目」→ IDE 选 **JupyterLab** → 框架选 **PaddlePaddle**。
3. **不需要非等 GPU**：cell 0 会自动检测设备。若只有 CPU，自动切 QUICK 档，
   预计 5–15 分钟跑完；若拿到 V100/A100，自动切 FULL 档，数分钟跑完。
4. 上传本 `.ipynb`（文件树顶部「上传」按钮，或直接拖拽到文件树），依次运行 cell 0→6。

> 算力卡机制：每日运行项目送算力，详见
> https://ai.baidu.com/ai-doc/AISTUDIO/nk39v9kec

## 验收标准
- ground-truth cell 输出 `CFL <= 0.5` 且 `rmse ≈ 1e-6` → 物理目标正确。
- 模型 cell 输出 `残差自检 finite=True` → Paddle 二阶自动微分链路通。
- 集成 cell 的 `rmse` 尽量小（QUICK 档约 1e-3 量级即正常）。
- **校准 cell 的 val 覆盖率接近 0.95** → UQ 可信。
  注意：原始（未校准）覆盖率通常远低于 0.95，这是**预期的负面结果**，不是故障。

## 诚实边界
- 深度集成给出的是**近似 UQ**，只含 epistemic 分量，天生过自信；
  本 Notebook 用 **split conformal 校准**补上系统偏差，并且只在
  **未参与校准的验证集**上报告覆盖率，避免自欺。
- 保形校准提供的是**边际（marginal）覆盖**保证，不保证每个局部位置都达标；
  要逐点条件覆盖需 Mondrian / 局部化保形方法。
- **QUICK（CPU）档精度低于 FULL（GPU）档**。
  若要对外声称"国产 GPU 已实测"，必须在 GPU + FULL 档下跑通并截图；
  CPU 跑通只能声称"飞桨框架下 PINN+UQ+保形校准链路已验证"。
"""))

nb = {
    "cells": [
        {
            "cell_type": ctype,
            "metadata": {},
            "source": src.splitlines(keepends=True),
            **({"outputs": [], "execution_count": None} if ctype == "code" else {}),
        }
        for ctype, src in cells
    ],
    "metadata": {
        "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        "language_info": {"name": "python", "version": "3.8"},
    },
    "nbformat": 4,
    "nbformat_minor": 5,
}

out = os.path.join(os.path.dirname(__file__), "heat_pinn_paddle.ipynb")
with open(out, "w", encoding="utf-8") as f:
    json.dump(nb, f, ensure_ascii=False, indent=1)
print("written:", out, "| cells:", len(nb["cells"]))
