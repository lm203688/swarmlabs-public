# SwarmLabs Tech Radar 闭环启用指南（PAT 分步引导）

> 闭环缺口 R6 的代码已就绪：`scripts/tech_radar.py` + `.github/workflows/tech-radar.yml`。
> 唯一剩余硬阻塞：需要一个带 `Workflows:write` 的 GitHub PAT 来提交 workflow 文件并触发 `workflow_dispatch`。

## 为什么需要 Workflows:write
- `.github/workflows/*.yml` 属于受保护分支的 **workflow 文件**。普通 `contents:write` PAT 无法 push 它们。
- 手动在 GitHub UI 点 "Run workflow"（workflow_dispatch）也需要仓库的 Workflows 权限。
- 当前 PAT 报 "缺 Workflows:write" → 卡在"无法提交/触发 Tech Radar workflow"。

## 分步生成新 PAT
1. 打开 https://github.com/settings/tokens?type=beta （Fine-grained 或 Classic 均可）。
2. 若用 **Fine-grained PAT**：
   - Repository access → 选 `lm203688/swarmlabs`。
   - Permissions → Contents: **Read and write**（回写 `data/tech_radar.json`）。
   - Permissions → **Workflows: Read and write**（关键，提交/触发 workflow）。
3. 若用 **Classic PAT**：
   - 勾选 `repo`（含 workflow 子作用域）+ `workflow`。
4. 生成后复制 token（只显示一次）。
5. 在仓库 Settings → Secrets → Actions 新增 `GH_RADAR_TOKEN`（值=新 PAT）。

## 提交并启用（两种路径）
**路径 A：本地提交（需直连 github.com；当前 DNS 被封，推荐路径 B）**
```bash
git add .github/workflows/tech-radar.yml scripts/tech_radar.py
git commit -m "feat(tech-radar): signal→candidate→promote→effect 闭环"
git push   # 用带 Workflows:write 的 PAT
```

**路径 B：经 gh_push.py / Contents API**
- 用 `scripts/gh_push.py`（Contents API，绕过 DNS 封禁）提交上面两个文件，token 用新 PAT。
- 提交后到 Actions 页面对 `SwarmLabs Tech Radar` 点 "Run workflow" 验证；之后每日 02:00(UTC) 自动跑。

## 验证标准
- 跑通后 `data/tech_radar.json` 出现 `generated_at` + `promoted` 列表。
- 日志含 "信号论文 / 候选 / 晋升" 三行统计即闭环成立。
- 效果监测：连续多日运行，`promoted[].traction` 计数递增 = 跨运行牵引追踪生效。

## 备注
- `tech_radar.py` 纯 stdlib，本地已可跑通（见 `python scripts/tech_radar.py`）。
- 当前 `GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}` 是仓库默认 token；若默认 token 无 Workflows 权限，回写步骤需改用上面的 `GH_RADAR_TOKEN`（在 yml 里把 `GITHUB_TOKEN` 换成 `GH_RADAR_TOKEN`）。
