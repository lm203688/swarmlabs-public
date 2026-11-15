# 上架物料包：扣子(Coze) + 魔搭(ModelScope)

> 目的：把 SwarmLabs 的"诚实验证"能力放上国内两大开发者平台，拿真实曝光与调用数据（解决 GitHub stars=0 的可发现性短板）。
> 两个平台都需要**你本人的账号**登录提交；下面是把文案和步骤都备好，你点几下即可。

---

## 一、扣子 Coze（字节）— 上架一个"科研验证"智能体/插件

**入口**：https://www.coze.cn （用你字节/抖音账号登录）
**形态建议**：做一个 Bot「SwarmLabs 科研可信验证官」，用大模型节点 + 我们 report-gallery 的公开 API（v3/report-index 免鉴权）做"查询某场景验证判定"的演示技能。

**Bot 基础信息（直接复制）**
- 名称：`SwarmLabs 科研可信验证官`
- 简介：`一个敢公开自己 8% 失败率的科研验证 Agent。基于 62 场景 10 章 V&V 报告，用不确定性量化(UQ)判断 AI 科研结果该不该信。`
- 开场白：`我是 SwarmLabs 科研可信验证官。问我"某个科学实验仿真可不可信"，我会用 ASME 对齐的验证报告给你诚实判定——包括那 5 个我们公开承认算不对的。`
- 人设：`你是一个严谨的科研验证助手。当被问到某个实验/模型的可靠性时，优先引用可复现的验证证据，明确区分"虚拟实验验证"与"湿实验实测"，不夸大、不隐藏失败。`

**插件/工作流节点（接我们公开 API）**
- `GET https://swarmlabs.tools/v3/report-index` → 返回 62 场景判定（PASS 53 / MARGINAL 4 / REFUTED 5）
- `GET https://swarmlabs.tools/v3/report/{key}` → 单场景 10 章报告
- 无需鉴权（report 资产走免网关的 ASSETS 通道）

**标签**：AI4S / 科研 / 不确定性量化 / 大模型应用
**提交步骤**：登录 → 创建 Bot → 填信息 → 加一个"调用 API"的插件节点（贴上面 URL）→ 调试一次 → 发布到"扣子商店"并勾选"公开"。

---

## 二、魔搭 ModelScope（阿里）— 上架 engine-kit 开源 SDK / 模型卡

**入口**：https://modelscope.cn （用你阿里/淘宝/钉钉账号登录）
**形态建议**：把 `swarmlabs-engine-kit`（MIT，已在 GitHub）镜像成一个"SDK / 工具"库，配模型卡说明"科研实验虚拟验证 + 诚实 UQ"。

**模型卡信息（直接复制）**
- 名称：`swarmlabs-engine-kit`
- 任务类型：`科学计算 / 不确定性量化 / 科研实验仿真`
- 许可证：`MIT`
- 简介：`Open-source client SDK + MCP server + Skill catalog for the SwarmLabs physics-informed scientific experiment automation engine. Honesty-first: every prediction reports uncertainty; validation is split into empirical/literature; 62-scenario benchmark with honest REFUTED disclosure.`
- 中文简介：`SwarmLabs 物理信息科学实验自动化引擎的开源开发者工具包（SDK + MCP + 技能目录）。诚实优先：每个预测都报告不确定性；62 场景基准含 5 个公开 REFUTED；报告对照解析解与文献基准。`
- 仓库地址（GitHub）：`https://github.com/lm203688/swarmlabs-engine-kit`
- 基准仓库：`https://github.com/lm203688/swarmlabs-benchmarks`

**标签**：`科学计算` `不确定性量化` `Gaussian Process` `科研自动化` `开源`
**提交步骤**：登录 → 右上"创建"→ 选"库(Library)"/"模型"→ 填模型卡 → 粘贴 GitHub 地址（或上传 SDK 源码）→ 发布。
**加分项**：在魔搭"社区"发一篇短文《我们公开了自己 8% 的失败率》，链接回仓库与 swarmlabs.tools，借阿里社区流量反哺 GEO。

---

## 三、提交后顺手做（AI 自治，已做/可做）
- [x] 英文 GEO 长文已上线 swarmlabs.tools/blog/honest-failure-rate（LLM 可引用）
- [x] 公开基准仓库 github.com/lm203688/swarmlabs-benchmarks（MIT，含 5 REFUTED 根因）
- [ ] 你提交扣子 + 魔搭后，把链接回链到 swarmlabs.tools/investors 与博客，形成流量闭环
- [ ] （可选）我可继续写英文版发 Hacker News / dev.to，进一步扩大可发现性

> 需你本人操作的硬阻塞（注册公司、算力券、加速器）见 `docs/hard_blockers_draft_2026-09-13.md`。
