# SwarmLabs Engine Kit — 推广分发物料草稿

> 用途：以下为高杠杆分发渠道的帖子草稿。GitHub 元数据/SEO 内容已自治闭环；
> 以下需外部账号，由你（或海外朋友）登录一键发。发了之后 `growth_promotion.py`
> 每日监控会记录 star/访客变化，形成"发→看效果→调物料"的半自治闭环。
>
> 仓库：https://github.com/lm203688/swarmlabs-engine-kit
> 在线 Demo：https://swarmlabs.tools/lab

---

## 1. Show HN (Hacker News) — 优先级最高

**标题**：Show HN: SwarmLabs Engine Kit – physics-informed multi-agent scientific computation

**正文**：
```
We open-sourced SwarmLabs Engine Kit: a Python SDK + reference MCP server for
SwarmLabs' physics-informed multi-agent engines.

- Honesty-first: every prediction is grounded in a real physics model. No black boxes.
- Surrogate modeling + Bayesian optimization + active learning for expensive
  scientific experiments (reaction yield, materials, catalysis...).
- MCP server exposes 15 engine types and the structured research knowledge base
  (47k+ entities) to any agent.
- MIT licensed, pip-installable.

Most "AI for science" tooling hides the model. We made the physics the contract.

Repo: https://github.com/lm203688/swarmlabs-engine-kit
Live lab: https://swarmlabs.tools/lab
```

---

## 2. Reddit r/MachineLearning

**标题**：[P] SwarmLabs Engine Kit: physics-informed multi-agent SDK + MCP server (MIT)

**正文**：
```
Hi all — sharing an open-source toolkit we built for honest scientific computation.

SwarmLabs Engine Kit wraps physics-informed multi-agent engines behind a clean
Python SDK and an MCP server, so agents can run surrogate modeling, Bayesian
optimization, and active learning without hand-rolling the math.

Key design choice: predictions are always tied to a real physics model
(we call it "honesty-first") instead of an opaque NN. That matters when a wrong
prediction is expensive (wet-lab, synthesis, simulations).

- 15 engine types, MIT, pip install swarmlabs-engine-kit
- MCP server for agent integration
- 47k+ structured research entities as a knowledge layer

Would love feedback from folks working on AI4S / Bayesian optimization.
Repo: https://github.com/lm203688/swarmlabs-engine-kit
```

---

## 3. Dev.to 技术文章

**标题**：Building honesty-first scientific computation agents with SwarmLabs Engine Kit

**导语**：
```
Most "AI for science" hides the model. SwarmLabs Engine Kit does the opposite:
it makes the physics the contract. This post walks through installing the SDK,
calling a physics-informed engine, and wiring it into an agent via the MCP server.
```

**要点骨架**：
1. 为什么需要 honesty-first（昂贵实验里错误预测的成本）
2. 5 分钟 Quickstart（pip install + 一个 yield 预测例子）
3. MCP server 让任意 agent 调用 15 类引擎
4. 主动学习闭环：跑实验 → 回填结果 → 模型变准
5. 链接仓库 + live lab

---

## 4. X / Twitter（国内打不开，需 VPN 或海外朋友代发）

**英文**：
```
Just open-sourced SwarmLabs Engine Kit 🧪 — physics-informed multi-agent
scientific computation. Honesty-first, MIT. SDK + MCP server, 15 engine types,
no black boxes.

Would love eyes from @Nat Friedman @danielgross and the OSS science crowd.
github.com/lm203688/swarmlabs-engine-kit
```

**中文（发微博/即刻/知乎备选）**：
```
开源了 SwarmLabs Engine Kit：物理信息驱动的多智能体科研计算工具，诚实优先、MIT 协议。
Python SDK + MCP server，15 类引擎，不黑箱。适合做 AI4S / 贝叶斯优化的朋友。
github.com/lm203688/swarmlabs-engine-kit
```

---

## 诚实边界（发之前请看）
- HN / Reddit / Dev.to 需登录账号，本环境无法代发（无凭证）。
- X 国内不可访问，需你或海外朋友代发。
- 发出后请告诉我"发了哪个渠道 + 日期"，我把它记入 `data/promotion_actions.jsonl`，
  这样 `growth_promotion.py` 才能把 star/访客增长**归因**到具体动作，而非只看趋势。
- GitHub topics 因当前 PAT 无写权限未能设置；如你手动在 repo 页面加
  `physics-informed / multi-agent / mcp / gaussian-process` 等标签，可发现性更佳。
