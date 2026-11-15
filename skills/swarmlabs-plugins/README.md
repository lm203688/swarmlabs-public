# SwarmLabs Science Compute — Anthropic Plugin

四个**离线可运行**的科研计算 skill，纯 Python + numpy，无需 API key、无需联网。全部为 AI agent 提供「可复现的数值结论」而非「文字建议」。

## 安装

```bash
claude plugin marketplace add lm203688/swarmlabs
claude plugin install swarmlabs-science-compute@lm203688-science-plugins
```

## 包含的 4 个 skill

| Skill | 用途 | 关键脚本 |
|---|---|---|
| `uq-coverage-audit` | 用留一法经验覆盖率验证预测区间是否可信，返回 PASS / MARGINAL / OVERCONFIDENT 判定 | `scripts/uq_audit.py`（纯 numpy） |
| `ood-guard` | 判断输入点是否在训练分布内，给 pass / controlled / reject 门控判定 | `scripts/guard_local.py` |
| `paper-claim-validation` | 给论文中的一个定量声明打分（数值支撑 vs 措辞强度） | `scripts/grade_claim.py` |
| `kinetic-model-selection` | 在 Monod / Andrews / Contois / Haldane 等多个动力学模型之间做比较拟合 | `scripts/select_model.py` |

## 边界

这四个 skill 是**完整 API 服务的开源子集**：

- 完整 SwarmLabs API（63 场景 / 52 微生物动力学模型 / GP surrogate）在 https://swarmlabs.tools/api/v3
- Pro 授权（¥39.9 / 月 或 ¥199 终身）解锁更多 scenario 与更多 skill
- 数据护城河：47,566+ 条结构化科研实体（趋势 / 研究空白 / 跨域桥接 / 合著网络）

## 为什么选这四个开放

**共同点**：都是纯计算、有明确数学定义、输出可复现——即使离开 SwarmLabs 后端也能独立完成。它们的价值是证明「SwarmLabs 的方法论可信」，而不是替代付费服务。

**未打包的 4 个**（`fermentation-design` / `mirror-run` / `strain-media-selection` / `virtual-experiment-design`）主要价值在 API 服务（跨域 benchmark / 63 场景库 / GP surrogate），保留在付费墙后。

## 上游仓库

- 源码：https://github.com/lm203688/swarmlabs
- 数据与 API：https://swarmlabs.tools
- License：MIT
