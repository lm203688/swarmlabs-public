# SwarmLabs Workbench 前端选型方案

> 更新日期：2026-09-25
> 阶段：2 前置（阶段 1 harness_evolver 已完成）
> 目标：为 SwarmLabs 选一个"本地部署 + 用户自选模型 + 数据不出机器"的前端形态

---

## 1. 需求约束（不能妥协）

| 约束 | 来源 | 优先级 |
|---|---|---|
| **本地部署** | 用户数据敏感、准入判据"无网络" | P0 |
| **模型自选**（本地 Ollama / 云端 API key） | 用户明确要求 | P0 |
| **数据不出机器** | 科研数据合规 | P0 |
| **跨平台**（macOS / Windows / Linux） | 学术用户分散 | P1 |
| **包体积合理**（< 100MB） | 下载门槛 | P1 |
| **迭代快**（改动即时生效） | 一个人维护 | P1 |
| **不锁定单一厂商** | 长期可用性 | P0 |

---

## 2. 五个候选方案对比

### 方案 A：Electron（Chromium + Node.js）

| 维度 | 评分 |
|---|---|
| 包体积 | ❌ 100-150MB |
| 启动速度 | ⚠️ 3-5 秒 |
| 内存占用 | ❌ 高（300MB+） |
| 生态 | ✅ 成熟（npm 全生态） |
| Python 集成 | ⚠️ 需要子进程 + IPC |
| 跨平台 | ✅ |
| **综合** | ❌ **不适合 SwarmLabs**（过重） |

### 方案 B：Tauri（Rust + Web）

| 维度 | 评分 |
|---|---|
| 包体积 | ✅ 5-15MB |
| 启动速度 | ✅ < 1 秒 |
| 内存占用 | ✅ 低 |
| 生态 | ⚠️ 较新 |
| Python 集成 | ⚠️ 需要子进程 |
| 跨平台 | ✅ |
| **前置要求** | 用户需装 Rust 工具链（1-2GB） |
| **综合** | ⚠️ **技术最好，但用户门槛高** |

### 方案 C：纯 Web 前端 + 本地 Python 服务 ⭐ **推荐**

| 维度 | 评分 |
|---|---|
| 包体积 | ✅ 前端 2-5MB + Python 30MB |
| 启动速度 | ✅ 2-3 秒（浏览器打开） |
| 内存占用 | ✅ 低（浏览器复用） |
| 生态 | ✅ Web 生态 + Python 生态 |
| Python 集成 | ✅ **原生**（后端就是 Python） |
| 跨平台 | ✅（任何有浏览器的机器） |
| **前置要求** | 用户需装 Python 3.10+ |
| **综合** | ✅ **最匹配 SwarmLabs 生态** |

### 方案 D：PySide / PyQt（Qt Desktop）

| 维度 | 评分 |
|---|---|
| 包体积 | ⚠️ 100-200MB（Qt 库） |
| UI 现代化 | ❌ Qt 默认样式过时 |
| 开发速度 | ⚠️ 中等 |
| Python 集成 | ✅ 原生 |
| **综合** | ❌ **不适合**（UI 过时，开发慢） |

### 方案 E：Streamlit / Gradio

| 维度 | 评分 |
|---|---|
| 开发速度 | ✅ 极快 |
| 应用持久化 | ❌ 不是"应用" |
| 本地部署优势 | ❌ 弱 |
| **综合** | ❌ **只适合 demo，不适合产品** |

---

## 3. 推荐：方案 C（纯 Web + 本地 Python 服务）

### 架构

```
┌──────────────────────────────────────┐
│  SwarmLabs Workbench (浏览器)        │
│  ├─ Chat (对话面板)                  │
│  ├─ Trajectory (工具调用轨迹)        │
│  ├─ Compute (引擎运行日志)           │
│  └─ Results (产物文件)               │
└──────────────────┬───────────────────┘
                   │ HTTP + WebSocket
                   │ localhost:8765
┌──────────────────┴───────────────────┐
│  SwarmLabs Engine Server (Python)    │
│  ├─ FastAPI                          │
│  ├─ harness_evolver (自进化)         │
│  ├─ harness_registry (42 门禁)       │
│  ├─ V&V 引擎 (GP+UQ)                 │
│  └─ 3D 锚点库 (19 anchors)           │
└──────────────────┬───────────────────┘
                   │ LLM 调用（可选）
        ┌──────────┴──────────┐
        │                     │
  ┌─────┴─────┐         ┌────┴─────┐
  │  本地 LLM  │         │  云端 LLM │
  │  Ollama    │         │  SenseNova│
  │  (离线)    │         │  (联网)   │
  └───────────┘         └───────────┘
```

### 关键设计

1. **前端**：React 或 Vue 3 + TypeScript（任选，用户无感）
2. **后端**：FastAPI（Python，与引擎同进程）
3. **通信**：HTTP + WebSocket（Trajectory 实时流）
4. **本地存储**：SQLite（用户数据不出机器）
5. **模型适配**：OpenAI 兼容接口（本地 Ollama + 云端任意兼容端点）

### 用户启动流程

```bash
pip install swarmlabs          # 或 git clone + pip install -e .
swarmlabs serve                # 启动本地服务，浏览器自动打开 http://localhost:8765
```

**总安装**：Python 3.10+（大多数科研用户已有）

---

## 4. 为什么不选 Tauri（诚实说）

Tauri 技术上是**最好**的（体积小、启动快、性能好），但不推荐原因：

1. **用户门槛**：用户需要装 Rust 工具链（1-2GB），学术用户会嫌烦
2. **开发效率**：Rust + Python 双栈，一个人维护压力大
3. **SwarmLabs 生态**：引擎是 Python，前端也是 Python 驱动最合适
4. **迭代速度**：Web 前端改动刷新即可；Tauri 需要重新编译

**未来可选**：如果 SwarmLabs Workbench 用户群验证成功后，可以用 Tauri 壳把纯 Web 方案"打包"成桌面应用，Python 后端不变。这是**渐进式升级**，不是一步到位。

---

## 5. 阶段 2 实施路线

### 阶段 2a（1-2 周）：MVP
- 一个 `swarmlabs serve` 命令，启动本地服务
- 前端只有一个面板：输入问题 → 输出 V&V 结果
- 后端调用现有引擎 + harness_evolver
- 不含 LLM 集成

### 阶段 2b（2-4 周）：三面板
- Chat / Trajectory / Compute 三面板
- 接入本地 Ollama（ornith-1.5:35b）或 SenseNova API
- 支持上传论文 / 数据 / 图
- 结果保存到本地 SQLite

### 阶段 2c（4-6 周）：Harness 自进化 UI
- 显示当前 harness 版本、评分、审计日志
- 用户反馈 → 生成候选 → 配对评测 → 显示接受/拒绝
- 一键回滚到任意历史版本

### 阶段 2d（6-8 周）：打包发布
- pip install swarmlabs（PyPI 发布）
- Docker image
- 可选 Tauri 桌面应用

---

## 6. 本迭代交付（阶段 1 已完成的）

- ✅ `engine/harness_evolver.py` — Harness 自进化引擎
- ✅ `engine/harness_registry.py` — 11 条场景级门禁注册
- ✅ `scripts/verify_harness_evolver.py` — 7/7 通过
- ✅ `scripts/verify_harness_registry.py` — 4/4 通过
- ✅ `docs/sciencebuddy-alignment-v2.md` — 战略对齐
- ✅ `docs/workbench-frontend-selection.md` — 本文档

## 7. 阶段 2 前置待办

- ⏳ 决定前端框架（React vs Vue）—— **推荐 Vue 3**（更简洁，与 Python 后端一致的单页应用哲学）
- ⏳ 定义 MCP tools 清单（把现有引擎能力包装成 10-15 个 tool）
- ⏳ 定义 Workbench 的数据模型（session / message / trajectory / result）
- ⏳ 定义 harness_evolver 的 UI 交互（反馈 → 候选 → 评测 → 接受/拒绝）

---

## 附：一句话总结

**SwarmLabs Workbench = Web 前端 + Python 后端 + 用户自选 LLM + 本地 SQLite**，是**最匹配 SwarmLabs 生态**的本地部署方案。Tauri 是未来可选的"外壳升级"，不是当前首选。
