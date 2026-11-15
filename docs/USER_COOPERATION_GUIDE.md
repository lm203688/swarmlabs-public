# SwarmLabs 用户配合指南（需你亲自操作的项）

> **文档版本**: 1.0
> **日期**: 2026-10-10
> **目的**: 列出 AI 无法自主完成、需用户本人操作的硬阻塞项，附直达链接和一步步指引。
> **原则**: 零成本优先，不编造，诚实告知投入产出比。

---

## 1. 总览：哪些事 AI 能做，哪些必须你做

| 类别 | AI 能自主完成 | 需你亲自操作 |
|---|---|---|
| 代码 / 文档 / 脚本 | ✅ 全部 | ❌ |
| GitHub 推送 / 部署 | ✅ 全部 | ❌ |
| CI 配置 / 门禁 | ✅ 全部 | ❌ |
| GitHub PAT 权限升级 | ❌ 做不到 | ✅ **必须你做** |
| GitHub Settings 页面设置 | ❌ API 不暴露 | ✅ **必须你做** |
| 注册公司 / 银行 / 税务 | ❌ | ✅ **必须你做** |
| 算力券 / 赛事报名 / 资质申请 | ❌ | ✅ **必须你做** |
| 发邮件 / 签合同 / 签合同 | ❌ | ✅ **必须你做** |
| 找实验室要数据 | ❌ | ✅ **必须你做** |

---

## 2. GitHub PAT Workflows:write 权限升级

### 问题

当前 PAT 缺 `Workflows:write` 权限，导致：
- 无法编辑现有 workflow YAML 文件
- 无法触发 `workflow_dispatch`
- CI 全红（7 个 workflow 全部被调度器拒绝）

### 操作步骤

1. 打开 https://github.com/settings/tokens?type=beta
2. 点击 **「Generate new token」** → 选择 **「Fine-grained token」**
3. **Repository access** → 选择 **Only select repositories** → 勾选 **lm203688/swarmlabs**
4. **Repository permissions** 勾选：
   - ✅ `Contents: Read and write`
   - ✅ **`Actions: Read and write`** ← 这是关键
   - ✅ `Metadata: Read`
   - ✅ `Checks: Read and write`
   - ✅ `Pull requests: Read and write`
5. 设置过期时间 → 建议 **90 天**（平衡安全与便利）
6. 点击 **「Generate token」**
7. **复制 token**（只显示一次）

### 配置到本地

```bash
# 更新 token 文件
echo '{"token": "github_pat_新token_xxx"}' > ~/.workbuddy/secrets/github.json
```

### 验证

```bash
python scripts/push_files_atomic.py --check-perms
```

期望输出包含 `Actions: read/write`。

---

## 3. GitHub Actions 设置修复（CI 全红）

### 问题

CI 全红：7 个 workflow 全部 `steps=0`、3 秒完成、从未分配到 runner。
最可能原因：GitHub Settings > Actions > General 中某个 UI 设置阻止了 workflow 运行。

### 操作步骤

1. 打开 https://github.com/lm203688/swarmlabs/settings/actions
2. 检查以下设置：
   - **「Allow GitHub Actions and reusable workflows」** → 确保 **Enabled**
   - **「Workflow permissions」** → 选择 **「Read and write permissions」**
   - **「Fork pull request workflows from external repositories」** → 按需
   - **「Default workflow permissions」** → 选择 **「Read and write permissions」**
3. 检查是否有 **branch protection rules** 阻止了 workflow：
   - 打开 https://github.com/lm203688/swarmlabs/settings/branches
   - 确认没有阻止 CI 的规则
4. 如果以上都没问题，尝试：
   - 打开 https://github.com/lm203688/swarmlabs/actions
   - 取消所有正在运行的 workflow
   - 手动触发一个 `workflow_dispatch`

### 验证

```bash
# 查看最新 CI run 状态
python -c "
import urllib.request, json
req = urllib.request.Request(
    'https://api.github.com/repos/lm203688/swarmlabs/actions/runs?per_page=1',
    headers={'Authorization': 'Bearer TOKEN', 'Accept': 'application/vnd.github+json'}
)
runs = json.loads(urllib.request.urlopen(req).read())['workflow_runs']
if runs:
    r = runs[0]
    print(f'Run #{r[\"run_number\"]}: {r[\"status\"]} / {r[\"conclusion\"]} ({r[\"steps\"]} steps)')
"
```

---

## 4. 注册公司（如需商业化）

### 当前状态

- 个人项目，无公司实体
- 影响：无法签正式商业合同、无法开票、无法参加部分政府采购

### 何时需要

- 与科研机构签合作协议
- 申请政府补贴 / 算力券
- 企业级客户需要正式合同
- 参加需要公司资质的赛事 / 招标

### 不需要的情况

- 纯 GitHub 开源项目
- 个人学术用途
- 小规模商业（<10 万 / 年）

### 参考

- 深圳 / 广州 个体户注册：https://amr.gd.gov.cn/
- 有限责任公司：通过当地市场监管局

---

## 5. 算力券 / 赛事报名

> **一站式仪表盘：[Grants Radar](https://swarmlabs.tools/grants-radar)**（12 项 AI4S 相关机会，按截止日排序、状态徽章、直达链接；含 AI 能做的 vs 用户必须做的分离标注）。数据源 `data/grants_radar.json`，2026-10-25 生成。

### 当前已申请 / 进行中

| 平台 | 状态 | 备注 |
|---|---|---|
| Digital Science Catalyst Grant | 🔥 评审中 | 2026-10-12 开始，截止 11-20（剩 26 天），公布 11-30 |
| 阿里云 GOAI 2026 | ✅ 已提交初赛 | 等评审结果 |
| 百度千帆 bac | ✅ 已申请 | 零成本 |
| Kaggle ARC 2026 | ✅ 已申请 | 零成本 |
| 人工智能未来设计大赛 | ✅ 已备材料 | 需确认是否冲突 |

### 待准备（高契合度，AI 已备稿）

1. **NVIDIA Academic Grant — Simulation & Modeling**（fit_score=9）
   - Q4 call 预计 12-31 截止（67 天），30K H100 80GB hours
   - **AI 已备稿**：`docs/nvidia_grant_faculty_pitch.md`（一页纸 pitch）
   - **用户必须做**：拉高校教授做主 PI（SwarmLabs 个人主体不符合资格）
2. **NSFC 面上/青年项目**（fit_score=8，2027-03 申报窗口）
   - AI4S 可信验证是当前国家重点方向（"人工智能+"行动）
   - **AI 可起草**：中文研究计划、国内外现状综述、技术路线图
   - **用户必须做**：确认依托单位、通过单位提交
3. **Anthropic Claude for Scientists**（fit_score=6，rolling）
   - 10,000 免费学术座位 + 单项目最高 $50K API credits
   - **用户必须做**：确认是否有机构 PI 资格，填写申请

### 需你操作的

1. **检查已提交赛事状态**（链接见 `docs/competitions/` 目录）
2. **Digital Science Catalyst Grant**：跟踪评审进展（AI 不代发邮件）
3. **确认合作教授**：NVIDIA / MSRA / NSFC 均需机构 PI 主体
4. **Grants Radar 页面反馈**：如需增补 grant 项，见 `data/grants_radar.json`

### AI 边界（强制）

- ✅ AI 可做的：起草文档、技术附录、模板、状态更新、公开进展博文
- ❌ AI 不做的：代发邮件、代提交申请、代签授权、代填表单

---

## 6. 找实验室要数据（P0-2 真实数据锚点）

### 问题

当前 wet_lab_anchors=19 条（全部来自公开文献），无第一方实测数据。
P0-2 要求接入真实实验数据作为「ground truth」锚点。

### 操作步骤

1. 联系合作实验室 / 高校课题组
2. 索取实验数据（参数 + 测量值 + 误差）
3. 提供数据来源证明（文献引用 / 实验报告）
4. 导入格式：`data/wet_lab_anchors/{domain}/{experiment_id}.json`

### 数据格式

```json
{
  "source": "实验报告 / 论文引用",
  "experiment_id": "unique_id",
  "parameters": {"temp_K": 310, "pH": 7.0},
  "measurement": {"growth_rate": 0.52, "unit": "1/h"},
  "uncertainty": {"growth_rate": 0.05},
  "date": "2026-10-10"
}
```

---

## 7. 汇总：优先级排序

| # | 事项 | 紧急度 | 难度 | 建议时间 |
|---|---|---|---|---|
| 1 | GitHub PAT Workflows:write | 🔴 高 | ⭐ 简单 | 5 分钟 |
| 2 | GitHub Actions 设置修复 | 🔴 高 | ⭐ 简单 | 5 分钟 |
| 3 | 检查赛事状态 | 🟡 中 | ⭐ 简单 | 10 分钟 |
| 4 | AI Grant 申请 | 🟡 中 | ⭐⭐ 一般 | 30 分钟 |
| 5 | 找实验室要数据 | 🟡 中 | ⭐⭐⭐ 难 | 1-2 天 |
| 6 | 注册公司 | 🟢 低 | ⭐⭐⭐⭐ 难 | 1-2 周 |

---

## 附录：直达链接汇总

| 项目 | 链接 |
|---|---|
| GitHub PAT 管理 | https://github.com/settings/tokens?type=beta |
| swarmlabs Actions 设置 | https://github.com/lm203688/swarmlabs/settings/actions |
| swarmlabs Branch Protection | https://github.com/lm203688/swarmlabs/settings/branches |
| swarmlabs CI Runs | https://github.com/lm203688/swarmlabs/actions |
| GOAI 2026 状态 | https://ai4s.alipay.com/goai |
| 百度千帆 bac | https://qianfan.bj.baidubce.com/ |
| Kaggle ARC 2026 | https://www.kaggle.com/competitions/arc-2026 |
| AI Grant | https://openai.com/index/introducing-openai-grants/ |

---

> **最后更新**: 2026-10-10
> **关联文档**: `docs/axiom-audit-g3-zero.md`, `AXIOM_AUDIT.md`