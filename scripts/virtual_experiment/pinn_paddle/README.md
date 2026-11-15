# pinn_paddle/ — 飞桨 PINN 原型（Tier 2 实装）

SwarmLabs 虚拟实验引擎的 **Tier 2 PINN/PIML** 级（`fidelity_ladder.py` 标注为 `scale-when-funded`
算力桩），这里用百度飞桨（PaddlePaddle）+ 国产免费 GPU 实装为一个可一键跑的原型。

## 目录内容

| 文件 | 作用 | 是否需 GPU |
|---|---|---|
| `heat_baseline.py` | 1D 热方程 numpy 有限差分 ground-truth（本地可跑，已验证 rmse≈1e-6） | ❌ 纯 numpy |
| `heat_pinn_paddle.ipynb` | 飞桨 PINN Notebook：PDE 残差 + IC/BC + 多种子集成 UQ（mean±std, 95% CI coverage） | ✅ 需 V100/A100 |
| `make_notebook.py` | 生成 `.ipynb` 的可复现脚本（`python make_notebook.py`） | ❌ |

## 与 SwarmLabs 哲学对齐

- **物理规律 = 微分方程**：热方程 PDE 残差作为无监督损失。
- **随机性 = 噪声 + 采样**：训练含带噪观测，模拟真实实验不可复现误差。
- **UQ 一等公民**：训练 N 个随机种子 PINN 集成，预测返回 `mean ± std`，
  用 95% 置信区间覆盖率（coverage≈0.95）度量可信度——与
  `parity_framework.Calibrator` 指标完全对齐。

## 与 GOAI 复赛代码包的关系（重要）

- 本目录**独立于** `agentteams/`（GOAI 复赛交付物），只实装底层科学计算引擎的 PINN 级，
  **不改动** `agentteams/` 与 `parity_framework.py` 的 numpy 核心，**不影响参赛资格**。
- 飞桨实跑通过后，可作为 GOAI「国产算力适配 + 科学 ML」的独立加分证据，与
  `agentteams/` 架构证据并列呈现，互不污染。

## 如何跑

- **本地**（验证物理）：`python heat_baseline.py`
- **飞桨 AI Studio**（跑 PINN）：上传 `heat_pinn_paddle.ipynb`，镜像选 PaddlePaddle 2.x，
  算力切 V100，见 `docs/paddle_access_guide.md`。

## 诚实边界

- 集成 PINN 的 UQ 是**近似**（深度集成法），非严谨保形区间；上规模时换保形预测/拉普拉斯近似。
- 本 Notebook 需在飞桨云端实跑确认；在此之前请勿在 GOAI 材料里声称「已适配飞桨」。
