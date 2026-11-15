# SwarmLabs 发酵虚拟实验 Demo — 平台发布分步指南

目标：把 `demo_fermentation/`（已本地跑通的 Gradio demo）推到现成平台，免注册触达真实用户。
已核实链接截至 2026-08-14。涉及登录/发布动作需你本人点一次（凭证我不能代登）。

---

## 准备：本地文件已就绪

`demo_fermentation/` 内含 4 个发布必需文件：
- `app.py` — Gradio 入口（发酵虚拟实验主程序）
- `parity_framework.py` — GPSurrogate 自包含副本（app.py 依赖）
- `requirements.txt` — 依赖（gradio / numpy / matplotlib / pillow）
- `README.md` — 说明 + HF Space 元数据头

> 本地预览：后台 Gradio 已验证可启动，Monod 闭环覆盖率 0.65（小样本诚实欠覆盖）、Andrews 抑制同样跑通。

---

## A. Hugging Face Spaces（全球 AI4S 受众，免费 CPU）

1. **登录/注册**：https://huggingface.co/join （可用 GitHub 账号直接登录）
2. **创建 Space**：https://huggingface.co/new-space
   - Owner：选你的个人账号
   - Space name：`swarmlabs-fermentation`（或自定义）
   - SDK：**Gradio**
   - Hardware：**CPU basic（免费）**
   - Visibility：**Public**
   - 点 **Create Space**
3. **把 4 个文件传上去**（二选一）：
   - **方式一（网页上传，最简单）**：创建后进入 Space 页面 → 点 `Files and versions` → `Add file` → 把本地 `app.py` / `parity_framework.py` / `requirements.txt` / `README.md` 逐个上传 → 每次 `Commit`
   - **方式二（Git 推送）**：
     ```
     git clone https://huggingface.co/spaces/<你的用户名>/swarmlabs-fermentation
     cd swarmlabs-fermentation
     # 把上面 4 个文件复制进来
     git add . && git commit -m "fermentation demo" && git push
     ```
     > 首次 push 需 Access Token：https://huggingface.co/settings/tokens 生成 read 以上权限 token，push 时用户名填你的 HF 名、密码填 token。
4. **等构建**：push/上传后 HF 自动构建（约 2–4 分钟），`Logs` 标签看进度，状态变绿即上线。
5. **公开地址**：`https://huggingface.co/spaces/<你的用户名>/swarmlabs-fermentation`

---

## B. 魔搭 ModelScope 创空间（中文开发者，免费 CPU/GPU）

1. **登录**：https://modelscope.cn （阿里云账号 / 手机号）
2. **进创空间页**：https://modelscope.cn/studios → 点右上角 **「我要创建」**
3. **填写配置**：
   - 名称：`swarmlabs-fermentation`
   - SDK 类型：**Gradio**
   - 云资源：**CPU basic（免费）**（demo 无需 GPU）
   - 可见性：先**私有**调试，跑通后改公开
   - 点创建
4. **传文件**（二选一）：
   - **方式一（ZIP 上传，最省事）**：把 `demo_fermentation/` 里 4 个文件打成 zip → 创空间页面「上传文件」拖入 → 平台自动解包部署
   - **方式二（Git 绑定）**：
     ```
     git lfs install
     git clone http://oauth2:<你的ModelScope令牌>@www.modelscope.cn/studios/<用户名>/swarmlabs-fermentation.git
     # 复制 4 个文件进去
     git add . && git commit -m "fermentation demo" && git push
     ```
     > 令牌在 ModelScope 个人中心 → 访问令牌 获取，格式即上面 `oauth2:<令牌>@`。
5. **运行验证**：页面点「在线体验」，看到 Gradio 界面即成功。
6. **公开地址**：`https://modelscope.cn/studios/<你的用户名>/swarmlabs-fermentation`

---

## C. MCP 市场（让任意 AI agent 一键调用 SwarmLabs 能力）

> 现状诚实说明：我们的 `swarmlabs-engine-kit` 是 **stdio MCP server**，真能被第三方跑通需你部署一个 engine endpoint（README 里的 `base_url`）。下面两条**不依赖端点**、现在就能挂；Smithery 需公网 HTTPS，暂跳过。

1. **mcp.so（最简单，一行描述，无验证）**：https://mcp.so → 提交表单，填名称 `swarmlabs-engine-kit`、描述、GitHub 链接 `https://github.com/lm203688/swarmlabs-engine-kit`
2. **Glama 自动收录**：在 `swarmlabs-engine-kit` 仓库根放 `glama.json`（含 name/description/tools 字段），Glama 爬虫自动发现 → https://glama.ai/mcp/servers
3. **魔搭 MCP 广场**：https://modelscope.cn 顶部「MCP广场」入口 → 提交/关联你的 server 仓库
4. **（暂缓）Smithery**：https://smithery.ai/new — 需公网 HTTPS 端点，待 engine 部署后再做
5. **（可选）官方 Registry**：https://registry.modelcontextprotocol.io — 给 `github.com/modelcontextprotocol/registry` 提 PR

---

## 诚实边界（再强调）

- **所有「登录/发布」动作最终需你本人点一次**——百度/华为/飞桨/HF/魔搭/Coze 账号是你的凭证，我不能代登。
- **华为/阿里目前都不是已签约分发**：阿里云 AgentTeams 集成样板、华为昇腾算子适配都还在「规划中」，材料里别当事实写。
- **MCP 挂牌 ≠ 真能用**：第三方要实际调用需你部署 engine endpoint；挂牌零成本，我可全包元数据。
- **KPI 不是 star/下载量**，是通过 demo 的「上传你的实验数据 → 我跑系统回建议」入口，拿到第一个真实发酵数据集。

## 推荐执行顺序

1. HF Spaces（方式一网页上传，5 分钟）→ 2. 魔搭创空间（ZIP 上传，5 分钟）→ 3. MCP：mcp.so + glama.json（10 分钟）。三步都只需你登录后点几下。
