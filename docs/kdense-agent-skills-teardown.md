# K-Dense-AI/scientific-agent-skills 深度拆解

> 调研日期：2026-08-28 | 一手来源：GitHub API + 仓库 CONTRIBUTING.md / plugin.json / skills 目录

---

## 一、真相核查

| 指标 | 实测值 | 判断 |
|------|--------|------|
| Stars | **35,455** | ✅ 真实，GitHub API 直读 |
| Forks | 3,408 | ✅ 真实 |
| Subscribers | 161 | ✅ 真实（注意：远低于 stars） |
| Skills 数 | 163 | ✅ 真实 |
| 数据库覆盖 | 100+（78 个统一访问） | ⚠️ 部分为文档引用，非全部有 API 封装 |
| 版本 | v2.64.0 | ✅ |
| 创建时间 | 2025-10-19（约 10 个月） | ✅ |
| 许可证 | MIT（仓库级，单 skill 另有声明） | ✅ |
| **「175,000+ 科学家」** | **无来源、无统计接口** | ❌ **不可采信** |

### 为什么 175,000 不可信

1. 仓库无注册/登录/用量上报接口，数字无处产生
2. **161 subscribers vs 175,000 用户**——订阅量是真实活跃度的代理指标，差 1000 倍
3. 10 个月净增 17.5 万科学家 = 每天约 600 人，垂直科研工具做不到这个增速
4. 该数字只出现在 repo title 字段，README 正文未再复述

**结论**：35K stars 是真金，175,000 是包装。**stars 说明"skill 商店"这个形态被验证了，但不证明它被真实使用。**

---

## 二、规范速查（可直接抄）

来源：`CONTRIBUTING.md`（20.6KB）+ `plugin.json`

### 目录结构

```
plugin.json                    ← 根级 Agent Plugins 包声明
skills/
└── <skill-name>/
    ├── SKILL.md               ← 唯一必需
    ├── references/            ← 长文档，Agent 按需读
    ├── scripts/               ← 可执行 helper
    └── assets/                ← 模板/静态资源
tests/                         ← 测试绝不放在 skills/ 下
└── <skill-name>/
    ├── test_scripts.py
    └── fixtures/
```

**skill 目录只允许这 4 项**，多一个目录就违反约定。

### Frontmatter 六字段封闭集

| 字段 | 必需 | 约束 |
|------|------|------|
| `name` | ✅ | 1-64 字符，仅小写字母+数字+连字符，不匹配目录名即失败 |
| `description` | ✅ | ≤1024 字符，第三人称，**必须写清"何时该用"**（决定 Agent 是否加载） |
| `license` | ❌ | 单 skill 许可，可区别于仓库 MIT |
| `compatibility` | ❌ | ≤500 字符，环境要求（Python 版本/系统包/网络） |
| `allowed-tools` | ❌ | **空格分隔字符串**，如 `Read Write Edit Bash`，不是 YAML list |
| `metadata` | ✅ | **必须 block mapping**，必须含 `version`（带引号） |

### 三个致命坑（照抄时必避）

```yaml
# ❌ 坑1：metadata 写成 JSON flow mapping
# strictyaml 会让整段 frontmatter 解析失败 → name/description 读不到 → skill 不注册
metadata: {"version": "1.0", "skill-author": "K-Dense Inc."}

# ✅ 正确
metadata:
  version: "1.0"
  skill-author: K-Dense Inc.
```

```yaml
# ❌ 坑2：allowed-tools 写成 list
allowed-tools: [Read, Write]
# ✅ 正确：空格分隔字符串
allowed-tools: Read Write Edit Bash
```

```yaml
# ❌ 坑3：version 不带引号 → 解析成 float 1.0 而非字符串
metadata:
  version: 1.0
# ✅
metadata:
  version: "1.0"
```

### 硬约束

- `SKILL.md` **< 500 行**，超了 CI 报警；长内容下沉 `references/`（渐进式披露）
- 顶层**只能有那 6 个字段**，多一个 key 就被参考验证器拒绝
- 每次改动必须 bump `metadata.version`（小改进 1.0→1.1，破坏性 2.0）
- 根 `plugin.json` 的 `version` 必须与 `pyproject.toml` 同步

### 工具链

| 用途 | 命令 |
|------|------|
| 格式验证 | `uv run skills-ref validate ./skills/<name>` |
| 全量验证 | `for d in skills/*/; do uv run skills-ref validate "$d"; done` |
| 安全扫描 | `skill-scanner scan ./skills/<name> --use-behavioral`（Cisco AI Skill Scanner） |
| 元测试（CI 卡点） | `uv run --with pytest python -m pytest tests/_meta -q` |
| 分发 | `npx skills add <repo>` / `gh skill install <repo>` / clone 到 `~/.agents/skills/` |

`tests/_meta` 是**仓库级守卫**，用 `ast` 静态解析所有 skill 的脚本（不 import），检查：frontmatter 合规、500 行限制、skills/ 下无测试无 pyc、本地链接可解析、脚本可解析、无 `eval`/`exec`/`os.system`、脚本不覆盖标准库模块名、无硬编码本地路径。

### 根 plugin.json（Agent Plugins 1.0.0）

```json
{
  "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
  "name": "scientific-agent-skills",
  "version": "2.64.0",
  "description": "...",
  "author": { "name": "K-Dense Inc.", "url": "https://k-dense.ai" },
  "homepage": "...", "repository": "...",
  "license": "MIT",
  "keywords": ["agent-skills", "science", "research", ...]
}
```

---

## 三、Skill 类型分类学

| 类型 | 占比 | 代表 | 深度 |
|------|------|------|------|
| **Python 包封装** | ~45% | scanpy, cobrapy, biopython, rdkit, pydeseq2, astropy | 浅：教 Agent 调库 |
| **方法论/工作流** | ~20% | experimental-design, hypothesis-generation, peer-review, pkpd-modeling, literature-review | **浅：只给文本流程** |
| **数据源访问** | ~15% | depmap, primekg, ncats-arax, cellxgene-census, database-lookup | 中：API 封装 |
| **文档/输出** | ~12% | docx, pdf, pptx, latex-posters, infographics, citation-management | 浅：模板生成 |
| **平台集成** | ~8% | benchling, dnanexus, latchbio, omero, opentrons, ginkgo | 浅：API 接线 |

**关键观察**：占比第二的「方法论 skill」全是**文本流程指导**——告诉 Agent "你应该做 DOE"，但**不提供任何计算能力**。Agent 看完还得自己写代码。

---

## 四、SwarmLabs 可借鉴清单

### P0 — 直接照搬（零风险，纯规范层）

| # | 借鉴项 | 落地动作 |
|---|--------|---------|
| 1 | 目录规范 `skills/<name>/` + 4 项限制 | 建 `skills/` 目录，每个场景一个子目录 |
| 2 | Frontmatter 六字段封闭集 | 写生成器保证合规，尤其是 metadata block mapping |
| 3 | 根 `plugin.json` (Agent Plugins 1.0.0) | 让整仓成为可分发的 plugin 包 |
| 4 | 渐进式披露（SKILL.md<500 行 + references/） | 公式推导/参数表放 references/，主文件保持精简 |
| 5 | 测试分离 `tests/<skill-name>/` | 不污染 skill 目录 |
| 6 | `skills-ref validate` 接入 CI | 格式自动卡点 |

### P1 — 改造后借鉴（需适配）

| # | 借鉴项 | SwarmLabs 的差异化改造 |
|---|--------|----------------------|
| 7 | `scripts/` 放可执行 helper | K-Dense-AI 放"怎么调库"的示例；我们放**真正能算的引擎脚本**（GP 拟合 + UQ） |
| 8 | 安全扫描（Cisco AI Skill Scanner） | 我们有自研 `guard.py` 三态门禁，可**组合**：静态扫描 + 运行时 UQ 门禁，这是他们没有的 |
| 9 | 平台集成 skill 思路 | 他们接 Benchling/DNAnexus；我们可接**自有 v3 API**（skill 直接调 swarmlabs.tools） |
| 10 | 数据源 skill 思路 | 他们接 100+ 外部库；我们有 69 菌株 + 20 培养基 + 47,566 实体，可做成**机理数据源** |

### P2 — 战略层借鉴

| # | 借鉴项 | 说明 |
|---|--------|------|
| 11 | 多平台兼容声明（Cursor/Claude Code/Codex/Gemini/Antigravity/Pi/Hermes） | 一份 skill 多处加载，扩大分发面 |
| 12 | 版本号纪律（每次改动必 bump） | 建立可信度 |
| 13 | `tests/_meta` 仓库级守卫思路 | 静态 ast 解析，快且不 import，值得抄 |
| 14 | 一 skill 一独立环境（`skill-requirements.toml`） | 解决依赖冲突，我们纯 numpy 无此问题 |

---

## 五、K-Dense-AI 的能力空白 = SwarmLabs 的差异化机会

对照 163 个 skill，以下能力**他们一个都没有**：

| 空白领域 | K-Dense-AI 现状 | SwarmLabs 现有资产 |
|---------|----------------|-------------------|
| **不确定性量化 (UQ)** | ❌ 无 | ✅ GP 后验方差、噪声地板归一化 3%、覆盖率 ≈0.95 |
| **代理模型 / surrogate** | ❌ 无 | ✅ `surrogate.py` RBF+Cholesky+主动学习+`run_parity_loop` |
| **虚拟实验替代真实实验** | ❌ 无 | ✅ 52 微生物场景 + 11 跨领域基准，GP 全收敛 |
| **OOD 门禁 / guard** | ❌ 无 | ✅ `guard.py` pass/controlled/reject 三态 |
| **主动学习 / 实验锦标赛** | ❌ 无（有 experimental-design 但仅文本） | ✅ `elo.py` + 按 GP 后验方差排名 |
| **微生物发酵动力学深度** | ⚠️ 仅 cobrapy（FBA，无动力学） | ✅ 52 场景覆盖 Monod/Andrews/Contois/Haldane/Pirt/Luedeking-Piret/Tessier/Baranyi/Fed-batch/Chemostat/Thiele/Antibiotic/Thermal |
| **仿真↔真实校准** | ❌ 无 | ✅ `mirror_run` |

### 一句话定位差异

> **K-Dense-AI 教 Agent「该做什么」（文本流程）**
> **SwarmLabs 让 Agent「直接拿到算完的结果 + 置信区间」（可执行引擎）**

他们的 `experimental-design` skill 会告诉 Agent：「你可以考虑用响应面法做 DOE」。
我们的 skill 应该直接返回：**「最优 3 组实验条件 + 每组预测值 ±95% 置信区间 + OOD 风险标记」**。

这是**降维打击点**，不是数量竞赛。

---

## 六、落地路径建议

### 不要做的

- ❌ 凑 52 个 skill 去对标 163 个 → 拿短板攻长板，且我们只有 52 场景 vs 他们 163
- ❌ 复制他们的数据源 skill（接 PubChem/UniProt）→ 我们没优势，纯体力活
- ❌ 指望 skill 发布直接带来付费转化 → 用户已验证：0 真实用户，493「访客」全是 Agent 调用

### 建议做的（分阶段）

**阶段 1：验证（1-2 周）**
选 **3 个旗舰 skill**，全部围绕 UQ 差异化：

1. `virtual-experiment-design` — 输入实验条件空间，返回 GP+UQ 优化后的最优设计点 + 置信区间
2. `uncertainty-aware-prediction` — 调 v3 API /predict，返回预测 + 不确定性 + OOD 门禁判定
3. `fermentation-kinetics-fit` — 发酵动力学自动拟合（Monod/Andrews/Haldane 等 13 种模型）

用 `skills-ref validate` 验证格式，发到 `swarmlabs-engine-kit` 公开仓。

**阶段 2：观测（2-4 周）**
看三个指标：GitHub stars 增量、**真实 page views**（需先解决 KV binding）、是否有非 Agent 的人类访问。

**阶段 3：决策**
- 若 page views 有真实人类增长 → 扩到 10-15 个 skill
- 若仍是纯 Agent 流量 → 停止，转向其他获客路径

---

## 七、附：验收清单（写 skill 时逐条过）

- [ ] 目录名 == `name` frontmatter，1-64 字符，仅小写字母+数字+连字符
- [ ] `description` ≤1024 字符，第三人称，写清「何时用」
- [ ] `metadata` 是 block mapping，含带引号的 `version`
- [ ] `allowed-tools` 是空格分隔字符串（若使用）
- [ ] 顶层只有 6 个合法字段
- [ ] `SKILL.md` < 500 行
- [ ] skill 目录下只有 SKILL.md / references/ / scripts/ / assets/
- [ ] 测试在 `tests/<skill-name>/`
- [ ] `skills-ref validate` 通过
- [ ] 无 secrets / 无硬编码本地路径 / 无 eval|exec|os.system
- [ ] 根 `plugin.json` version 与 pyproject.toml 同步

---

## 八、已落地：`virtual-experiment-design`（2026-08-28）

按上述规范建了第一个旗舰 skill，并**实跑验证**（不是纸面设计）。

```
skills/agent-skills/virtual-experiment-design/
├── SKILL.md                                # 规范合规，正文 <500 行
├── references/microbiology-scenarios.md    # 52 场景目录（328 行，自动生成）
└── scripts/design_next_experiment.py       # 零依赖，标准库 only

tests/agent-skills/virtual-experiment-design/
└── test_design_next_experiment.py          # 22 tests, all pass
```

### 实测结果（对 live API）

| 输入 | trusted_ratio | 输出行为 |
|------|--------------|---------|
| 5 点训练（0.1→5.0） | **0.95** | 推荐 1.97 / 0.12 等，verdict 全 `pass`，std≈0.0072 |
| 2 点稀疏训练（0.1–0.2） | **0.25** | 只推荐 0.32–0.49（紧贴训练区），**自动过滤掉 75% 候选** |
| 2D `surrogate_branin` | **0.347** | std 随距离 0.77 → 1.73，UQ 随外推增大 |

稀疏数据那一行是核心价值：**模型知道自己在哪不知道**。训练只覆盖 0.1–0.2 时，它不会给 8.0 编一个数字，而是把建议收敛到可靠区。

### 踩到的两个真坑（照抄时必看）

**坑 1 — Cloudflare 拦截 Python 默认 UA**

```
API /scenarios failed: HTTP 403
error code: 1010
```

`urllib` 默认发 `Python-urllib/3.x`，被 CF bot 防护直接拒。curl 能过是因为 UA 不同。**必须显式设置 User-Agent**：

```python
USER_AGENT = "swarmlabs-agent-skill/1.0 (+https://swarmlabs.tools)"
```

**坑 2 — `/guard` 的判定字段叫 `status`，不是 `verdict`**

实际返回：

```json
{"counts":{"pass":3,"controlled":0,"reject":0},"trusted_ratio":1,
 "mean":[...],"std":[...],"status":["pass","pass","pass"]}
```

按 `verdicts`/`verdict` 取会拿到空数组 → 门禁静默失效。更危险的是：门禁失效后，若把「无判定」当成「拒绝」处理，会对着一个**好模型谎报"全部 OOD"**。已加回归测试 `test_guard_down_is_not_a_rejection` 锁死这个语义。

### 测试覆盖（22/22）

| 类别 | 数量 | 覆盖点 |
|------|------|--------|
| Frontmatter 规范 | 9 | 六字段封闭集、name 字符集、description≤1024、metadata 非 JSON flow、version 带引号、allowed-tools 非 list、<500 行 |
| Grid 生成 | 4 | 1D/2D/3D 边界与单调性 |
| Rank 逻辑 | 9 | exploit/explore/balanced、1.96σ 区间、reject 过滤、controlled 保留、**status 字段回归**、**unknown≠reject 回归**、全 OOD 回退最近点 |
