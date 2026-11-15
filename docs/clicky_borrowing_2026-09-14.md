# Clicky (HeyClicky) 对 SwarmLabs 的可借鉴分析

> 调研日：2026-09-14 · 目标：把 Clicky 的核心创新"借过来"提升 SwarmLabs
> 开源仓库：`github.com/farzaa/clicky`（MIT）· 官网 `heyclicky.com` · YC S26，Farza Majeed（Buildspace 创始人）· 融资 $10.1M

## 1. Clicky 是什么（事实锚定）

一个 **macOS 原生 AI 桌面助手**：住在光标旁，按快捷键"看"你的屏幕、听你语音提问，用**一个透明覆盖层的蓝色光标飞到指定 UI 元素**指给你看，并能用 "`clicky agent`" 派发后台代理。

技术栈（95% Swift）：
- **ScreenCaptureKit** 截图 → **Claude** 视觉分析（核心推理）
- **AssemblyAI** 实时语音转录（Push-to-Talk，亚秒级）
- **ElevenLabs** 文本转语音
- **Cloudflare Worker** 作 API 密钥代理（密钥不下客户端）
- `[POINT:x,y]` 坐标标签 → 覆盖层光标动画（最魔性的创新点）

商业：免费（25 talk / 25 agent 条/月）+ Pro $20（150 agent）+ Max $100（1000 agent）；GitHub 7.3k stars；#6 Product Hunt。

## 2. 核心创新拆解 → SwarmLabs 借鉴映射

| Clicky 创新 | 对 SwarmLabs 的借用 | 优先级 | 落地状态 |
|---|---|---|---|
| **`[POINT:x,y]` 视觉指向覆盖层** | web 版 `swarm-pointer.js`：在报告/仪表盘把光标/气泡**精确指到具体证据**（哪个 R² 不足、哪个 κ 过度自信） | **P0** | ✅ 已实现并接入 report-gallery |
| **"AI 来到你身边"降低上下文切换** | 把"读懂 V&V 判定"从读长篇变成一步式指针引导（点按钮即有人指给你看） | **P0** | ✅ gallery "带我读懂这些数字" |
| **诚实 build-in-public**（自曝 15M 视图、自问"产品是垃圾吗"） | 已发 `blog/honest-failure-rate`；加码：**演示 GIF/视频** + 指针引导讲失败，让"公开 8% 失败率"可触摸 | **P0→P1** | 长文已发；指针引导已接 |
| **开源换 stars**（MIT 7.3k） | `swarmlabs-engine-kit` 已是 MIT 但 stars=0；缺"一个魔性 demo"。指针可视化 = demo 种子 | **P1** | 待：用指针做 10s 录屏/GIF |
| **后台 Agent（"clicky agent"）** | 飞轮已是自治后台循环；暴露为"研究 Agent"可查询接口（问"本周新增了哪些研究空白"） | **P1** | 待：加 `/v3/agent` 查询端点 |
| **Cloudflare Worker 代理密钥** | license API 让用户**自带模型 key**，经 CF Worker 代理不下客户端（与 Clicky 同架构） | **P1** | 待：在 license 网关接 |
| **语音交互（Push-to-Talk）** | 实验室场景手占着→语音建虚拟实验；需 ASR+TTS，工程大 | **P2** | 评估中 |
| **屏幕感知（ScreenCaptureKit）** | Web 无截屏范式；可"看用户当前 notebook/分析"做浏览器插件 | **P2** | scope 存疑，暂缓 |

## 3. 从 Clicky 的**短板**反学的教训（更重要）

Clicky 被点名的硬伤，恰恰是我们该避免的：

- **"No public API or MCP support"** → SwarmLabs 的 license 产品**必须优先做 API + MCP server**，别重蹈"无程序化接口、开发者无法集成"的覆辙。这是战略级教训。
- **macOS-only 排除 80% 用户** → 我们是 Web/CF Pages，天然跨平台，保持。
- **代理消息上限 / 复杂任务成功率有限** → 我们的"虚拟实验替代真实实验"定位必须靠**诚实 UQ + 10 章 V&V 报告**兜底，而非假装全对。与现有"公开 REFUTED"策略一致。
- **太新无可靠性记录** → 我们用 62 场景确定性离线计算 + 可复现清单建立信任，比 Clicky 更稳。

## 4. 本次已落地的借用（P0）

1. `frontend_fixed/js/swarm-pointer.js` — 零依赖 web 微库，复刻 `[POINT:x,y]` 魔法：动画光标 + 语音气泡指向任意元素。
2. `report-gallery.html` 新增 **"🤖 带我读懂这些数字"** 按钮，启动指针巡游：依次指向 REFUTED 统计（"我们公开了 5 个失败，不藏"）、κ 说明、报告卡、REFUTED 过滤器——把"诚实失败率"叙事变成可触摸的引导。
3. 复用同一微库可进一步注入 62 份 10 章报告（P1，见下）。

## 5. 建议下一步（P1/P2，按 ROI 排序）

1. **指针 + 录屏做 10s GIF**：在 report-gallery 跑一遍引导，录成 GIF 放 investors 页与 GEO 长文——这是"魔性 demo"种子，直击 stars=0 短板。
2. **`/v3/agent` 查询端点**：把飞轮实体库/研究空白暴露成可问的接口（类比 "clicky agent"）。
3. **license API 自带 key 代理**：复用 CF Worker 模式，密钥不下客户端。
4. **MCP server**：把引擎 API 包成 MCP，让 Cursor/Claude Code 类客户端直接调用虚拟实验（直接反制 Clicky 的"无 MCP"短板）。
5. **报告内指针引导**：在 `engine/report_generator.py::render_html` 注入 `swarm-pointer.js` + 每场景"报告向导"按钮（future flywheel 自动再生 62 份）。

## 6. 不可迁移项（明确排除，防止盲目照搬）

- **macOS 原生 Swift / ScreenCaptureKit 低延迟**：我们是 Web/CF Pages（JS-only），原生栈不适用；勿为"原生"牺牲跨平台与可托管性。
- **桌面级屏幕常驻感知**：Web 无等价范式，且科研数据敏感，常驻截屏反伤隐私信任；我们用"用户主动打开报告"替代。
- **通用消费者语音助手定位**：SwarmLabs 是科研验证基础设施，不为"学 DaVinci Resolve"服务；语音仅作为实验室手占场景的候选增强（P2），不主航道。

---
*诚实边界：以上借鉴均不改变 SwarmLabs 的"虚拟实验替代真实实验 + 诚实 UQ + 可归档 V&V"定位；指针引导只解释既有数据，不生成新结论。*
