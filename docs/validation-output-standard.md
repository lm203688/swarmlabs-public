# SwarmLabs 实验成果形式标准（对标竞品升级）

> 2026-08-10 制定。学习 FutureHouse Robin / DeerFlow 2.0 / AngelSpec 的**实验过程与成果形式**，
> 把 SwarmLabs 的验证产物升级为「结构化、可排名、带误差棒、出下一步建议」的成果链。
> 落地见 `scripts/paper_validation_loop.py` 的 `build_dashboard()` 与公开页 `blog/validation-dashboard.html`。

## 一、竞品的实验过程与成果形式（调研结论）

| 竞品 | 实验过程 | 成果形式 | 可借鉴 |
|---|---|---|---|
| **FutureHouse Robin** | 假设→实验→解读 三阶段闭环（Crow/Falcon/Finch 编排） | 结构化目录：`experimental_assay_detailed_hypotheses/`（假设文本）、`*_literature_reviews/`（文献）、`ranking_results.csv`（**成对排名**）、`summary.txt`、`ranked_candidates.csv`（最终排名+强度分） | ① 假设/候选**排名 CSV 带强度分**；② 每假设独立报告+文献支撑；③ 执行摘要分层 |
| **DeerFlow 2.0** | 调研→产出 编排（沙箱/子代理/MCP/记忆） | 带**引用**的研究综述报告、可复现的 plan | ① 引用即证据；② 产出可复现 |
| **AngelSpec** | 投机采样端到端训练 | 用**真实指标**（接受长度、加速比）而非 loss 评估 | ① 评估必须看真实业务指标；② 误差棒比单一 loss 更诚实 |

## 二、SwarmLabs 升级后的成果形式（已落地）

1. **结构化场景报告**（对标 Robin 的假设文件）
   每场景独立卡片：域 / ground-truth 来源 / 累积验证点 / rmse / 95% CI 覆盖率 / 校准误差 / 来源文献。
2. **成熟度排名**（对标 Robin 的 ranking CSV）
   `验证成熟度排名`：已收敛 > 收敛中 > 需数据/校准，附 rmse 与覆盖率，相当于"候选强度分"。
3. **误差棒即一等公民**（对标 AngelSpec 真实指标）
   每个场景报告 95% 预测区间覆盖率与校准误差——这是"虚拟实验对等现实"的硬指标。
4. **新实验方向建议**（**Robin/DeerFlow 都没有**）
   依据 rmse/覆盖率/校准误差自动产出下一步：补点 / 调噪声先验 / 升保真度阶梯 / 跨域迁移 / 分离 epistemic+aleatoric。
5. **可复现 + 引用**
   每次运行记录日期、种子、参数、版本；ground-truth 均引发表明来源，非自我造数。

## 三、SwarmLabs 相对竞品的差异化定位（诚实）

- 竞品（Co-Scientist / Robin）多在"**提实验让人做**"或"假设→候选排名"；
  SwarmLabs 做"**仿真 + 量化不确定性 + 挑出信息量最大的真实实验点**"，虚拟层本身可独立达标。
- **误差棒量化 + 主动建议新方向** 是 Robin 缺失的能力——这是当前空白带的切入点。
- 局限：真实实验校准尚未接入（RemoteLab 为桩）；PINN/神经算子为算力桩，待规模。

## 四、使用

- 每日自动化（`automation-1786267221077`）运行 `paper_validation_loop.py`，自动跑当日场景 + 重建仪表盘。
- 手动：`python scripts/paper_validation_loop.py --dashboard` 仅重建仪表盘。
- 输出：`frontend_fixed/blog/validation-dashboard.html`（FAQPage JSON-LD，GEO 友好）+ `data/paper_validation.json`（历史）。

## 五、对标 Google/Anthropic/DeepMind 后的升级项（2026-08-10）

对标 `docs/competitor-benchmark-2026.md`，把对手最强的两个可迁移模式显式化、可见化：

1. **验证主导计算（对标 Co-Scientist「多数算力用于验证」）**
   对外表达中明确：每日闭环 100% 在"用论文真实数据验证"，而非生成假设；这与生成型竞品（Co-Scientist/Robin）形成定位差异。
2. **实验锦标赛 = 候选下一点 Elo 式排名（对标 Co-Scientist idea tournament）**
   新增 `rank_next_experiments()`：把未采样候选点按 GP 后验方差（期望信息增益）排名，
   仪表盘每场景新增「下一步实验锦标赛」面板——把"主动建议新方向"从文字升级为**可排序、可复现**的候选清单。
3. **验证而非仅预测（对标 DeepMind Hassabis 哲学）**
   仪表盘新增「对标前沿实验室」区块，明示 SwarmLabs 与 Co-Scientist/A-Lab 的同构内核与轻量差异。
4. **赋能而非替代 + 伦理闸门（对标 Anthropic 定位）**
   对外文档声明：虚拟实验是科学家的"加速透镜"，最终实验决策由人把关；不生成编造数据。

> 诚实边界不变：真实实验台（RemoteLab）与 PINN/神经算子仍为桩，待用户松口接入或融资/算力上规模。
