# SwarmLabs × MHS：把虚拟实验做成「虚拟仪器」驱动

> 战略文档 · 2026-08-29 更新
> 触发事件：Anthropic 于 2026-08-27 发布 **Model Hardware Standard (MHS)** 研究预览。

## 1. MHS 是什么（一手事实）

| 项 | 内容 |
|----|------|
| 发布 | 2026-08-27，Anthropic 研究预览 |
| 本质 | 让 AI Agent 通过统一驱动发现并控制**任何有可编程接口的物理设备**（显微镜、机械臂、液体处理、量子计算机激光锁定等） |
| 三大控制路径 | MCP（Agent 协议）/ CLI / 代码文件（API，可沉淀为确定性脚本） |
| 核心原语 | `read` / `write`（如设温度、读测量）；自动发现；自然语言标签 + 自动生成参考文件（含安全限值） |
| 早期数据 | Genentech BCA 蛋白实验跨液处理+机械臂+读板机自主编排；CMU 三台不兼容电脑设备速度×3；**QuEra 激光锁定恢复成功率 99.3%** |
| 生态 | AWS(Strands Robots)、Danaher、Doosan、QIAGEN、Tecan、Universal Robots、MBF Bioscience、Automata 已接入 |
| 开放计划 | 预览后**开源**，模型无关（不限于 Claude） |

**关键判断**：MHS 解决的是「Agent ↔ 真实仪器」这一段。它不解决「该让 Agent 做什么实验、做到什么程度才信」。

## 2. 对 SwarmLabs 的含义

### 2.1 威胁面

若 MHS 扩展到仿真/虚拟仪器，会直接吃 SwarmLabs 的地盘（虚拟实验也是「有可编程接口的设备」）。Anthropic 有分发优势——所有 Claude 用户天然到达。

### 2.2 更有利的读法：MHS 反而放大了 SwarmLabs 的位置

1. **执行的瓶颈消失，决策的瓶颈凸显**。真实实验现在便宜且易达（99% 控制率），但每次真实实验仍然贵（试剂、时间、仪器占用、泡沫/物理故障）。在 SwarmLabs 里迭代到收敛，再交给 MHS 去真实仪器执行——这正是 `mirror_run()` 的位置。
2. **MHS 需要「决策质量」**。99% 控制率下，真正稀缺的是：下一个实验点该选哪？模型对自己的预测有多不确定？——这正是 SwarmLabs 的 **UQ（不确定性量化）** 与 **GP surrogate + 主动学习** 的强项。
3. **Anthropic 自己承认的局限**：Claude 主要经文本/图像学物理世界，空间与物理推理仍需专家监督（如 Genentech 泡沫故障需人解释）。这意味着「可信度闸门」是刚需——而 SwarmLabs 的 VirtualLab Guard 正是这道闸门。

**结论**：不要和 MHS 拼「控制真实仪器」，要做 MHS 之前的那一层——**虚拟迭代 + 可信度闸门**，并以「MHS 虚拟仪器驱动」的身份接入，而不是对抗。

## 3. 适配层设计（SwarmLabs as a virtual instrument under MHS）

把 SwarmLabs 的 52 微生物场景 + GP surrogate + Guard，包装成 MHS 兼容的「虚拟仪器」驱动：

```
Agent (Claude / 任意 MHS 客户端)
   │  MCP / CLI / API
   ▼
MHS 调度层  ─── 真实仪器驱动 (Tecan/QIAGEN/...)  ──► 物理实验
   │
   └──── 虚拟仪器驱动 (SwarmLabs)  ──► /v3/predict /guard /sample
                                         │
                                         └─ mirror_run(): 虚拟↔真实 对等映射 + UQ 偏差
```

### 3.1 虚拟仪器驱动接口（MHS-compatible）

MHS 要求驱动暴露 `read` / `write` 原语 + 自然语言标签 + 安全限值。SwarmLabs 虚拟仪器映射：

| MHS 概念 | SwarmLabs 对应 |
|----------|---------------|
| `write` 设定参数 | POST `/v3/sample` 指定 scenario + bounds → 生成虚拟实验点 |
| `read` 读取测量 | POST `/v3/predict` 用 GP 返回均值+标准差 |
| `read` 读取状态 | GET `/v3/health` → 场景数/菌株数/Guard 状态 |
| 安全限值 | VirtualLab Guard：`pass`/`controlled`/`reject` + `trusted_ratio` |
| 自然语言标签 | 52 场景的 `published` 字段（已含文献模型与参数） |
| 参考文件 | `/v3/scenarios` 输出的结构化 spec（bounds/ls/noise/refs） |

### 3.2 mirror_run() 桥接协议

```json
{
  "virtual_instrument": "swarmlabs.v3",
  "scenario": "micro_haldane_putida",
  "virtual_policy": { "next_points": [[0.3],[0.8],[1.6]], "expected_mu": [..], "std": [..] },
  "real_adapter": {
    "mhs_device": "tecan_fluent",
    "command": "pipette(S, well); incubate(37C); read(OD600)",
    "guard": "pass",
    "trusted_ratio": 0.95
  },
  "sim2real_delta": "<filled after real run; UQ-quantified>"
}
```

## 4. 落地动作（按 ROI）

| 优先级 | 动作 | 状态 |
|--------|------|------|
| P0 | 52 场景上线 v3（已注入代码，待部署） | 代码完成，待 token |
| P0 | `/v3/sample` 真实虚拟采样端点（已加） | 代码完成，待部署 |
| P1 | 本适配层文档 + `mirror-run` 旗舰 skill（虚拟↔真实桥接） | 进行中 |
| P1 | UQ GEO 文章（锚点：Crooks/Jacobian/MHS） | 进行中 |
| P2 | 真正对接 MHS MCP（等 MHS 开源后提交 SwarmLabs 驱动） | 待 MHS 开源 |

## 5. 一句话定位

> **MHS 让 Agent 能动手做实验；SwarmLabs 让 Agent 知道该做什么实验、并判断结果可不可信。**
> 我们不做 Claude 的对手，做它开工前的「虚拟风洞」+ 开工后的「可信度仪表盘」。
