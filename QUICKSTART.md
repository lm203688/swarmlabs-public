# SwarmLabs · 快速开始

> 根目录历史 quickstart。PR-05 起，唯一权威入口迁移至 `docs/QUICKSTART.md`。
> 本文件保留为兼容入口，请勿再在此处添加新内容；如有差异，以 `docs/QUICKSTART.md` 为准。

## 唯一权威文档

请跳转至：

- [`docs/QUICKSTART.md`](docs/QUICKSTART.md) — 30 秒上手（安装、demo、CLI、工作台）
- [`README.md`](README.md) — 项目定位与能力
- [`FOR-REVIEWERS.md`](FOR-REVIEWERS.md) — 复算路径
- [`docs/DEVELOPER.md`](docs/DEVELOPER.md) — 开发工作流

## 快速三步（与 docs/QUICKSTART.md 一致）

```bash
pip install -e ".[dev,full]"      # 依赖唯一真源：pyproject.toml
swarmlabs demo                     # 端到端 6 阶段闭环，~3s
swarmlabs scenarios --limit 20     # 95 个已验证场景
```

## 已归档的旧入口

- `pip install -r requirements.txt` —— 已从主路径移除；使用 `pip install -e ".[dev,full]"`。
- `python -m engine.flywheel --domain ...` —— 内部飞轮，不面向新用户；请走 `swarmlabs demo` 或线上 `https://swarmlabs.tools`。
- `python -m core.trust_layer` —— 可信面生成已改为 `scripts/rebuild_trust_chain.py`；由 CI 触发。
- `scripts/verify_all.py` —— 已废弃，请走 `scripts/check_public_numbers.py` + `python -m pytest tests/`。

---

若你正在阅读根目录 `QUICKSTART.md`，说明链接未更新——请在 GitHub 搜索并跳转到 `docs/QUICKSTART.md`。
