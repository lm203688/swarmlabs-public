# 快速开始（30 秒上手）

> 面向：想快速验证 SwarmLabs 是否满足需求的用户、研究者、投资人。

## 前置条件

- Python 3.10+
- 无 GPU 要求，无付费 API 要求

## 三步跑通

### 1. 安装

```bash
pip install -e ".[dev,full]"   # 依赖唯一真源：pyproject.toml
```

### 2. 端到端 Demo（<5 秒）

```bash
swarmlabs demo
```

输出示例：

```
Pipeline (6 stages)
  [✓] intake → scope → build → verify → corroborate → deliver
Verdict: PASS (PROCEED)
Scenarios: 95 | Trainable: 10 | Gates: 11 | Anchors: 19
```

### 3. 查看场景列表

```bash
swarmlabs scenarios --limit 20
```

## 常用命令速查

| 命令 | 用途 | 耗时 |
|---|---|---|
| `swarmlabs demo` | 端到端验证 demo | ~3s |
| `swarmlabs scenarios` | 列出 95 个已验证场景 | ~1s |
| `swarmlabs gates` | 查看 11 个验证闸门 | <1s |
| `swarmlabs anchors` | 查看 19 个湿实验锚点 | <1s |
| `swarmlabs doctor` | 环境自检（依赖、版本） | ~2s |
| `swarmlabs workbench` | 启动本地 Web UI | 常驻 |
| `swarmlabs serve` | 启动 v3 API 服务器 | 常驻 |
| `swarmlabs rebuild` | 重建所有产物（CI 用） | ~60s |

## 本地工作台

```bash
swarmlabs workbench
```

打开 `http://127.0.0.1:8788` 进入交互式界面，支持：
- 场景浏览与筛选
- 单场景预测与置信区间可视化
- 湿实验锚点对照
- 结论 + 适用域边界 + 下一实验建议

## 线上体验

无需安装，直接访问：
- 首页：https://swarmlabs.tools
- 实验台：https://swarmlabs.tools/lab
- 验证仪表盘：https://swarmlabs.tools/verify
- API 文档：https://swarmlabs.tools/docs/api

## 下一步

- 想了解项目定位 → 读 [README](https://swarmlabs.tools/README.md)
- 想贡献代码 → 读 [DEVELOPER.md](DEVELOPER.md)
- 想复算论文数据 → 读 [FOR-REVIEWERS.md](FOR-REVIEWERS.md)
