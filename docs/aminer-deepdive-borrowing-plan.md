# AMiner DeepDive 借鉴计划（SwarmLabs 提升分析）

> 参考对象：https://www.aminer.cn/v2/deepdive?fr=benefits （AMiner 科研助手 DeepDive，底层智谱 GLM-5.2）
> 日期：2026-09-02 ｜ 性质：竞品借鉴 / 产品提升路线图（非实施，待确认后落地）

## 一、AMiner DeepDive 是什么（能力清单）

AMiner（清华）学术知识图谱平台；DeepDive 是其 AI 科研助手，定位"覆盖**搜、读、写、做**全科研流程"。核心模块：

| 模块 | 功能 |
|---|---|
| 学术搜索 | 海量文献智能检索 |
| AI 阅读 / 速读 | 学术预训练模型抽取论文 背景/方法/结果/结论/图表，~60s 生成速读 |
| **溯源树** | 生成论文"发展脉络"：相关论文 / 引用论文 / 被引论文的引用树 |
| AI 文库 / 学术空间 | 个人文献库 + 知识沉淀 |
| AMiner 沉思 / Research Labs | 深度推理 / 研究智能体工作区（deep research） |
| 学者库 / AI2000 | 专家发现、顶会学者排行 |
| 学术API / 数据集 | 程序化访问、数据集检索 |
| 订阅小程序 | 微信订阅，每日追踪 + 总结最新论文 |
| 会员 ¥0.73/天 | VIP 变现 |
| 典型场景 | 跨域学习、为基金（NSF GNN-DDI）检索文献支撑、回复审稿人意见 |

一句话：**AMiner = 文献情报 + 阅读 + 综述写作 + 专家/溯源的"科研全链路"工具，强在知识层。**

## 二、SwarmLabs 现状 vs AMiner 缺口

SwarmLabs 强在**验证层**：GP 代理 + UQ（不确定性量化一等公民）+ 虚拟实验替代真实实验 + OOD 门禁 + 主动学习/实验锦标赛 + 52 微生物深度场景；已有 157,866 结构化科研实体、paper_validation（18 基准）、v3 API（10 端点）、每日飞轮、license 变现。

AMiner 有、我们没有的缺口：
1. 文献阅读 / 速读 — 无
2. 引用溯源树 / 发展脉络可视化 — 无
3. 一键综述合成 / 回答科研问题 — 无
4. 专家/学者发现界面（有共著网络原始数据，未产品化）— 弱
5. 订阅 / 每日论文推送（有飞轮，无对外订阅产品）— 弱
6. "搜·读·写·做"完整用户链路的前端闭环 — 无

## 三、核心论点：不要抄 AMiner，要做"验证层 × 文献层"融合

AMiner 做"读 + 写"但**零验证**——它的综述不区分"有量化证据"与"拍脑袋描述"。这正是 SwarmLabs 的护城河。
**借鉴方向 = 把 AMiner 的"读 / 溯源 / 综述"套上 SwarmLabs 的"验证态"**，做出唯一一个"可读、可溯源、且每条结论带误差棒与 OOD 红区"的科研助手。

> 差异化一句话：**AMiner 告诉你别人说了什么；SwarmLabs 告诉你哪些说得通、在哪儿会翻车。**

风险红线：AMiner 有海量文献语料，我们**不要**试图在"文献量"上超越它；所有借借鉴必须落在"验证"差异化上。

## 四、优先级借用处（P0 → P2）

### P0（最高杠杆 / 最低成本 / 复用现有资产）
1. **文献验证态徽章（Verified Badge）**
   - 复用 `engine/paper_validation.py` + `data/validation_results.json`（18 基准），在任意"论文速读 / 综述"输出挂 `验证状态：PASS / MARGINAL / UNVALIDATED` + 覆盖率 + 误差棒。
   - 把已做的 paper validation 变成**用户可感知特性**。成本：前端 widget + 读现有 JSON。
2. **可信溯源树（Trustworthy Provenance Tree）**
   - 复用共著网络 + 引用数据画溯源树，每个节点叠 SwarmLabs 验证态。AMiner 溯源树只是引用链接；我们的树标"哪些被验证过 / 哪些进 OOD 红区"。
3. **可行性综述生成（Feasibility Review）**
   - 把刚做的 `grant-proposal-validation` 博客思路泛化为通用能力：输入研究方向 → 输出"文献支撑 + 虚拟实验验证态 + 风险与应对"。复用 surrogate + UQ + 主动学习。

### P1
4. **AI 速读 + 不确定性判读** — 速读论文时附"该方法在 SwarmLabs 52 微生物 / 11 跨域场景中的可复现性"。复用 `microbiology_full.py` + `domains.py`。
5. **专家发现 + 验证履历** — 学者库不只列 H 指数，列"该学者方向被 SwarmLabs 验证过的次数 / 覆盖率"。复用共著网络洞察。
6. **每日验证简报（订阅）** — 把每日飞轮 paper_validation 结果包装成邮件 / 小程序订阅（对标 AMiner 订阅小程序）。复用 `flywheel.py`。

### P2
7. **学术API 扩展为 Verification API** — v3 API 加 `/verify-paper`、`/provenance` 端点。复用 `serve.py`。
8. **会员分层对齐** — 把 AMiner ¥0.73/天 思路对齐 `license.swarmlabs.tools` 的 Pro / 终身档，把 P0 特性设为付费墙。

## 五、复用资产清单（已存在，零从零构建）

| 借用项 | 复用资产 |
|---|---|
| P0-1 / P0-3 | `engine/paper_validation.py` + `data/validation_results.json`（18 基准） |
| P0-3 / P2-7 | `engine/surrogate.py` / `serve.py` / `domains.py` |
| P1-4 | `engine/microbiology_full.py`（52 场景）+ 21 菌株库 |
| P0-2 / P1-5 | 共著网络洞察（157,866 实体子集） |
| P1-6 | `engine/flywheel.py` |
| P2-7 | v3 API（swarmlabs.tools，10 端点） |
| P2-8 | `license.swarmlabs.tools`（虎皮椒支付） |

## 六、建议下一步

**先做 P0-1（验证态徽章）**：数据已齐（18 基准 + validation_results.json），1–2 天可出 demo，且直接把"我们有验证层"变成用户可感知特性——是差异化的最小可行切片。

确认后我可立即动手：在 `frontend_fixed/blog/` 或新建 `frontend_fixed/verify/` 下出一个"论文验证态"展示页 + 读取 `data/validation_results.json`，并接入 v3 `/guard` 思路做 OOD 红区可视化。
