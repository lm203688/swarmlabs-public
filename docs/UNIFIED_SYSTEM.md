# SwarmLabs 统一体系（One System）整合文档

> 状态：2026-08-15 整合落地。本文是项目「一个体系」的权威说明——融合内核、按使用场景组织领域、理顺成果/使用/自动化、飞轮持续迭代。

## 0. 为什么必须整合

整合前项目是 **5 套并行子系统 + 3 个历史残留目录**，彼此几乎零打通：

| 子系统 | 内容 | 问题 |
|---|---|---|
| A · GP 验证飞轮 | 8 个学术基准 + UQ + 主动学习 | 最严谨，但只覆盖学术玩具场景 |
| B · 155 引擎目录 | `validate_models.js` 启发式引擎（化学/能源/材料…） | 无 UQ、有系统性偏差、与 A 互不兼容 |
| C · AgentTeams | 多智能体编排代码包 | 只接 A，调不动 B 的 155 域 |
| D · 发酵 Demo | 仅 1 个场景 | 与 155 引擎绝缘 |
| E · PINN 飞桨 | 1D 热方程 | 孤立 |
| F · 内容 14 站 | genetech/aerospace… 营销微站 | 与核心无映射（已清理，见 §5） |

**最致命**：两套"验证"互不兼容——严谨的 GP 飞轮（A）用解析解做 ground-truth，而化学场景（suzuki/heck）全在启发式引擎（B）里、无 UQ、有已知偏差。所以"化学场景补齐了吗"在 A 里是伪命题：化学压根没进严谨体系。

**整合目标**（用户原话）：融合一个体系，围绕使用场景理顺资源与过程，成果呈现/使用过程/自动化运营都理顺，飞轮不断迭代，平台数据库·技术能力·算法·领域能力持续加强。

## 1. 一个体系架构

```
                         ┌─────────────────────────────────────┐
   平台数据库 ──────────▶│           SwarmLabs Engine Kit         │◀──────── 领域能力
   data/ (datasets,      │           （一个体系 · 统一内核）        │          domains/ (按场景)
   trust_surface,         │                                          │
   store)                 │   engine/  (域无关内核, 唯一真源)         │
        ▲                 │   ├ surrogate.py  GPSurrogate+UQ         │   已验证(ACTIVE):
        │                 │   │     +主动学习+SurrogateEngine门面     │     optimization/biology/
        │                 │   └ domains.py   领域taxonomy            │     pde/llm  (8 域)
        │                 │                                          │   待接入(PENDING):
   飞轮(flywheel)         │   apps/  (成果呈现 + 使用过程)            │     化学/能源/材料/制药…
        │                 │   ├ demo/        发酵/通用虚拟实验         │     (155引擎/9类)
        │                 │   ├ agentteams/  多智能体编排                │
        │                 │   └ validation-dashboard.html 能力证据   │
        └─────────────────│                                          │
                          └─────────────────────────────────────┘
                                      │
                              每日自动化: engine/flywheel.py
```

**脊柱 = `engine/` 包**（域无关统一内核）。所有上层（飞轮、demo、agentteams）只通过 `SurrogateEngine` 门面操作「领域」，不再各自引一份内核。

## 2. 统一内核 `engine/`

| 文件 | 职责 |
|---|---|
| `engine/surrogate.py` | **唯一真源**：`GPSurrogate`(GP+RBF+噪声, 预测带 std) / `Calibrator`(双侧覆盖率) / `ActiveLearner`(最大方差推荐) / `SurrogateEngine`(领域门面) |
| `engine/domains.py` | 领域 taxonomy：ACTIVE 来自 `paper_validation_loop.SCENARIOS`；PENDING 来自 155 引擎目录的 9 大类 |
| `engine/flywheel.py` | 每日自动化总开关（见 §4） |
| `engine/__init__.py` | 对外 `from engine import SurrogateEngine` |

**兼容垫片**：`scripts/virtual_experiment/parity_framework.py` 已退化为转发 `engine.surrogate`，历史代码 `from virtual_experiment.parity_framework import ...` 仍可用——零改动融合飞轮。

`SurrogateEngine` 门面（一个 API 操作一个领域）：
```python
eng = SurrogateEngine(domain)          # domain: key/category/f/bounds/ls/auto_ls/references
eng.fit(X, y)                          # 自动 z-score + 噪声地板（诚实 UQ 基线）
eng.evaluate(test_xs)                  # {rmse, coverage, cal_err}
eng.recommend_next()                   # 主动学习：下一个最该做的真实实验点
eng.report(X, y, test_xs)              # 可直发领域报告
```

## 3. 领域能力（围绕使用场景组织）

`engine/domains.py` 把全部能力归到「使用场景」之下：

- **ACTIVE 已验证 8 域**（统一引擎 + UQ + 主动学习，数据库充分）：
  - optimization：`surrogate_forrester` / `surrogate_branin`(ARD) / `surrogate_hartmann6`(ARD)
  - biology：`bio_logistic` / `microbio_monod` / `microbio_inhibition`(ARD)
  - pde：`pde_heat1d`
  - llm：`llm_scaling`
  - 全部 rubric 5/5 通过、覆盖率 0.88–1.00、gold 溯源 8/8
- **PENDING 待接入 138 引擎 / 9 大类**（来自 155 引擎目录）：
  - Chemistry 11 · Environment 18 · Energy 12 · Materials 24 · Biology 6 · Separation 31 · Analysis 8 · Pharma 24 · Electrochemistry 4
  - **接入方式 = 用发表基准做 gold + UQ**，即"真补齐"（非信任启发式）。这是领域能力扩展的主路径。

## 4. 飞轮（自动化运营逻辑）

`engine/flywheel.py` 是每日总开关，闭环：

1. `run_day()` — 遍历已验证领域，统一引擎复跑验证 + 主动学习补最不确定点（写入平台数据库 `validation_store/`）
2. `build_dashboard()` — 导出 LHS 数据集（`data/scenario_datasets/`）+ 更新可信面 `data/trust_surface.json`（双侧覆盖率）+ 重建 `validation-dashboard.html`
3. `growth_monitor.main()` — GEO/可达性/转化漏斗监测（失败不阻断）
4. 打印当日汇总

**迭代机制**：更多领域 → 更多数据 → 更好模型 → 更好推荐 → 更多用户 → 更多真实数据 → 下一轮更强。这正是项目的真正意义。

**自动化已对齐**：每日 automation `1786267221077` 现指向 `python engine/flywheel.py`（原"论文数据验证闭环"升级为"统一飞轮"）。

## 5. 清理记录（可恢复）

以下"不相关 / 历史残留"已移入 `_retired/`（**非删除，可恢复**）：

| 移入 `_retired/` | 原因 |
|---|---|
| `frontend_fixed/blog/` 下 28 个营销微站（aerospace/biotech/genetech/materials/quantum… 及其 -cost 变体） | 14 站营销内容，与核心无映射 |
| `content/`（cases/education/seo 内容壳） | 内容站生成物 |
| `_cleanup_pending` / `_consolidate_backup` / `_platform` / `archive` | 此前 3+ 轮失败整理尝试残留 |

**保留**：`validation-dashboard.html`（真产物）、`frontend_fixed/` 根（index/demo/lab 真产品壳）、`agentteams/`、`data/`、`engine/`、`scripts/`、`demo_fermentation/`。

> ⚠️ **待理顺项（GEO 自动化）**：飞轮第 3 步的 GEO 引擎会重新生成 `frontend_fixed/blog/` 下的 `geo-*.html`（4 篇方法论页）与 `blog/index.html`（含 30 篇、部分链接已失效的微站）。这些属既定 GEO 推广策略的方法论页，与已清理的 14 站营销微站不同类；但 blog 索引可能仍引用已隔离微站（死链）。建议后续将 GEO 生成输出定向到独立 `promotion/` 目录，或在生成时剔除死链——待你确认推广策略后处理。

## 6. 成果呈现 / 使用过程（理顺）

- **成果呈现**：`validation-dashboard.html` = 能力证据（8 域指标 + 可信面 rubric），飞轮每日重建。
- **使用过程**：`demo_fermentation/`（发酵/通用虚拟实验 demo）是用户使用入口——左侧填实验点 → 右侧 GP 曲线±误差棒 → 推荐下一个实验。已改为优先用 `engine` 统一内核（部署时回退本地快照）。可挂 HF Spaces / 魔搭创空间（国内可达）。

## 7. 路线图（能力持续加强）

| 阶段 | 内容 |
|---|---|
| 现在 | 8 域严谨验证 + 统一内核 + 飞轮 + 清理 |
| 近期 | 化学场景真补齐（suzuki/heck/氢化，发表基准做 gold + UQ）；把 §3 PENDING 中 1–2 类接入统一内核 |
| 中期 | 155 引擎目录系统性审计（清掉"全验证"口径地雷，改真实通过率）+ 逐步接入 |
| 算力桩 | PINN（飞桨已本地跑通 coverage 0.955）、神经算子、RemoteLab 待 GPU/设备接入后启用，接口已对齐 |

## 8. 诚实边界

- 华为昇腾 / 阿里云 是**生态入口**，非已签约分发；飞桨 PINN 是本地 CPU 实测，非 GPU 云实测。
- 多数 1D 场景覆盖率=1.00 是保守 UQ（拟合好+噪声地板），rubric 判 [ok]，非过覆盖。
- 当前真实外部用户≈0；飞轮首要 KPI 是拿到**第一个真实实验数据集**，非 star/下载量。
