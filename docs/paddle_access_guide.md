# 对接百度飞桨 AI Studio · 分步指导（SwarmLabs PINN 原型）

> 目标：把 `scripts/virtual_experiment/pinn_paddle/heat_pinn_paddle.ipynb`  
> 在百度飞桨 AI Studio 上跑通（**CPU / GPU 均可**），实装 `parity_framework` 的 Tier 2 PINN 桩。  
> 本操作**不影响** GOAI 复赛代码包（独立目录、零污染）。

---

## 一、前置：准备材料（本地完成）

| 步骤 | 操作                             | 说明                                                             |
| -- | ------------------------------ | -------------------------------------------------------------- |
| 1  | 确认本地有 `heat_pinn_paddle.ipynb` | 由 `make_notebook.py` 生成，已通过 JSON + 语法校验                        |
| 2  | 先本地跑 `heat_baseline.py` 验证物理   | `python heat_baseline.py` → 应输出 `rmse≈1e-6`，证明 ground-truth 正确 |

---

## 二、注册与新建项目

| 步骤 | 操作                                      | 直达链接                          |
| -- | --------------------------------------- | ----------------------------- |
| 3  | 用百度账号登录飞桨 AI Studio（需实名认证）              | <https://aistudio.baidu.com/> |
| 4  | 「项目」→「新建项目」→ IDE 选 **JupyterLab**、框架选 **PaddlePaddle** | 同上加号/新建入口                     |
| 5  | **环境启动后直接运行**；不必死等 V100；cell 0 会自动检测 CPU/GPU | 启动后点右上角「启动环境」                  |
| 6  | 若抢到 V100 可切换；没抢到就 CPU（QUICK 档）先跑通验证链路      | 顶部「CPU」按钮切换为「GPU · V100」        |



> 算力卡机制：每日运行项目即送算力，详见  
> <https://ai.baidu.com/ai-doc/AISTUDIO/nk39v9kec>  
> 实战指南：<https://cloud.baidu.com/article/4686148>

---

## 三、上传 Notebook 并运行

| 步骤 | 操作                                                                       |
| -- | ------------------------------------------------------------------------ |
| 7  | 在项目里「新建/上传文件」→ 上传 `heat_pinn_paddle.ipynb`（或直接新建 .ipynb 粘贴内容）            |
| 8  | 打开 Notebook，确认 cell 0 能 `import paddle` 且显示 `实际使用设备: cpu 或 gpu`                  |
| 9  | 依次运行 cell 1→11（CPU 约 2–3 分钟，GPU 更快）                                       |
| 10 | cell 1 验证物理 ground-truth 正确（rmse≈1e-6）；cell 5 验证 Paddle 二阶自动微分通            |
| 11 | cell 9 训练 5 个 PINN（CPU QUICK 档约 2 分钟）；cell 10 做保形校准并报告验证集覆盖率            |

---

## 四、验收标准（对齐 Calibrator）

运行结束后 cell 5 会打印：

```
集成 PINN 预测：rmse=...  coverage(95%)=...
```

- **目标**：保形-归一化残差校准后的 `val 覆盖率 ≈ 0.95` 且 `rmse` 低 → 虚拟实验误差棒可信，Tier 2 PINN 桩实装成功。
- **关键**：未校准的原始 `coverage(95%)` 通常远低于 0.95（如 ~0.37），这是**预期负面结果**，不是 bug。本 Notebook 用 split conformal 校准修正它。

---

## 五、与 GOAI 复赛的关系（再次确认零污染）

| 维度     | GOAI 复赛 `agentteams/` | 飞桨 PINN 原型                                |
| ------ | --------------------- | ----------------------------------------- |
| 是什么    | Agent Infra 协同架构      | 底层科学计算引擎（PINN）                            |
| 目录     | 仓库根 `agentteams/`     | `scripts/virtual_experiment/pinn_paddle/` |
| 依赖     | numpy + PyYAML        | PaddlePaddle（CPU / GPU 均可）              |
| 是否互相影响 | ❌ 不 import、不共享文件      | ❌ 不碰 `agentteams/`                        |

**结论**：飞桨跑通后，可把「国产算力已实装 PINN」作为 GOAI 赛题的**独立加分证据**，  
与 `agentteams/` 架构证据并列，互不污染。但请注意诚实边界：  
**未实跑通过前，不要在 GOAI 材料里声称已适配飞桨。**

---

## 六、参考链接

- 飞桨 AI Studio 首页：<https://aistudio.baidu.com/>
- 算力卡说明：<https://ai.baidu.com/ai-doc/AISTUDIO/nk39v9kec>
- 100 行实战指南：<https://cloud.baidu.com/article/4686148>

## 本地预验证结果（CPU）
使用 `run_notebook_cpu.py` 在本地 CPU 完整跑通：
- 全部 11 个 code cell 通过，总耗时约 2 分钟。
- ground-truth cell rmse ≈ 3.7e-06。
- 集成 PINN（未校准）coverage = 0.37（符合预期负面结果）。
- 保形-归一化残差校准后，**验证集覆盖率 = 0.955，cal_err = 0.005**。
- 这意味着原集成误差棒需要放大约 4.1 倍才可信——定量揭示了深度集成的过自信。

> 结果文件：`scripts/virtual_experiment/pinn_paddle/cpu_run_fig1.png`
