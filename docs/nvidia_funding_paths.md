# NVIDIA 资助路径分析（SwarmLabs 对口版，2026-08-11 核实）

## 一、用户身份（决定路径的关键事实）
- 目前**未注册公司**（无合法实体）。
- 职称为**教授级高工**（中国工程技术职称「正高」，与大学教授同级，但非高校教职）。
- 为**乌克兰工程院外籍院士**（learned society 选举荣誉成员，非教职、非授学位机构）。
- **不在学校任职**。

## 二、NVIDIA 各资源门槛（官方 FAQ 核实）
| 资源 | 申请人硬门槛 | SwarmLabs 现状 | 结论 |
|---|---|---|---|
| **Inception 初创加速** | 合法实体 + 网站 + <10年 + ≥1开发者；排除咨询/外包/加密/云商/经销商 | 无实体 ❌；网站✅ 开发者✅ | 注册公司即解锁 |
| **Academic Grant（主PI）** | 必须是「授予 PhD 的认证高校」全职 faculty | 非 faculty ❌ | 不能当主 PI |
| **Academic Grant（协作者）** | 可挂教授名下；产业 partner 不受限 | 头衔可作强背书 ✅ | co-PI/技术负责人可行 |
| DGX Spark 黑客松 | Agent 主题，2026 届 8/2 已决赛 | 错过 | 盯 2027 届 |

## 三、两条现实路径
**路 A（自控·最快）：注册公司 → 申 Inception**
- 免费、$100K DGX Cloud(H100)、无截止、不拿股权。
- 注册是法律动作，需用户本人办（我备指南+申请材料）。
- 直接补「PINN/神经算子/RemoteLab 算力桩未启用」缺口。

**路 B（需搭档）：高校教授主 PI + 用户 co-PI**
- 2026 CFP「Simulation and Modeling」含 physics-informed ML / scientific simulation，与 parity_framework 同频；最高 30,000 H100 80GB 小时。
- 季度窗口：下一轮 **2026-09-30 截止，12 月出结果**。
- 必须用 NVIDIA 软件栈（PhysicsNeMo/Modulus、CUDA、RAPIDS）→ 需把 parity_framework 接 NVIDIA 栈。
- 用户用院士+教授级高工人脉敲高校主 PI；SwarmLabs 出代码+部署+技术负责人。

## 四、风险/诚实边界
- Academic Grant 主页面部分地区显示 "not accepting" 横幅，但多校 2026 季度截止仍在挂 → 动手前先确认 portal 当季开放。
- 乌克兰工程院通常不授学位，host 路径为 long-shot，勿押宝。
- 用户头衔 ≠ 主 PI 资格，勿误导。
