# Zenodo 存档上传指南（A-5）

> 目的：给两篇论文的 artifacts 拿 DOI（TMLR / ICLR JC 鼓励，部分要求）。
> AI 能做的（已完成）：组装存档包 `archives/zenodo-package-<date>/`（含 `.zenodo.json` 元数据 + `MANIFEST.json` 全文件 sha256）。
> 只有用户能做的：Zenodo 账号操作本身（AI 不代注册、不代持凭证）。

## 1. 前置检查（AI 已完成，可复跑）

```bash
python scripts/build_zenodo_package.py --verify --date 2026-10-18
# 期望: PASS: N 个文件 sha256 全部一致
```

存档包内容：

| 路径 | 内容 |
|---|---|
| `code/` | engine/ + scripts/ + tests/ + schemas/ + requirements.txt + LICENSE |
| `papers/` | paper-1（tex/bib/figures/投稿 zip）+ paper-2 |
| `data/` | gate_ledger / trust_surface / ablation_plan_report / sensitivity_ablation / extrapolation_stress / wet_lab_anchors 等论文引用产物 |
| `.zenodo.json` | Zenodo 元数据（上传时自动识别，无需手填表单） |
| `MANIFEST.json` | 全文件 sha256，第三方可独立核对 |

## 2. 上传步骤（约 10 分钟，需用户操作）

1. 登录 <https://zenodo.org>（可用 GitHub 账号直接授权登录，免注册）。
2. 右上 **Upload → New Upload**。
3. **Drag & drop**：把 `archives/zenodo-package-2026-10-18/` 整个目录拖进去
   （`.zenodo.json` 会被自动读取，表单自动填好标题/作者/关键词/license——
   只需核对，不要手改覆盖）。
4. 校验：Files 列表里应看到 `.zenodo.json`、`MANIFEST.json`、`README.md`、
   `code/`、`papers/`、`data/` 三棵树。
5. **Reserve DOI**（先点 Reserve，拿到 DOI 再发布——论文引用需要 DOI 先存在）。
6. 页面底部 **Publish**。发布后 DOI 永久生效（如 `10.5281/zenodo.XXXXXXX`）。

## 3. 拿到 DOI 后要做的事（AI 可代办）

把 DOI 告诉 AI，由 AI 完成：

1. `outputs/paper-1-applied-intelligence/paper.tex` 的 Data availability 节
   加入 DOI 引用；
2. `README.md` 关键指标表加入 artifacts DOI 行；
3. `.zenodo.json` 的 `related_identifiers` 无变化，但 `docs/zenodo-upload-guide.md`
   记录实际 DOI；
4. 全量门禁复跑 → Git Data API 推送 → 部署 → 线上复验。

## 4. 注意事项

- **一个 DOI 对应一个版本**：论文录用后若 artifacts 有更新，在 Zenodo 上
  **New version**，不要覆盖旧版（旧 DOI 保持可解析）。
- **发布不可撤销**：Publish 前确认 `MANIFEST.json` 与本地一致（第 1 步）。
- Zenodo 单存档上限 50GB——本包约 35MB，无压力。
- 若想走 GitHub Release 自动存档（Zenodo ↔ GitHub 集成），在
  <https://zenodo.org/account/settings/github> 启用 `lm203688/swarmlabs`，
  之后每次 GitHub Release 自动产生 Zenodo DOI。适合长期维护；本次投稿
  用手动上传更快。

## 5. 时间线建议

| 时点 | 动作 |
|---|---|
| 投稿前 | 完成本上传，DOI 写进论文 Data availability |
| 论文录用/修回 | 若 artifacts 变更 → Zenodo New version |
| TMLR/ICLR JC 投稿（12-18 截止） | 直接引用同一 DOI |
