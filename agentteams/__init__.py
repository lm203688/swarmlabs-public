"""SwarmLabs AgentTeams ——  【生产主链路：确定性科学验证闭环】

角色定位（与 core/autonomous_loop.py 的关系）：
- 本包是**生产主链路 / 竞赛 Demo 的准绳**：Planner（只读规划）→ Executor
  （执行无写权限）→ Verifier（唯一写权限 + 人工审批）的三角色权限隔离，
  驱动真实的科学验证闭环（非 LLM 算数值结论）。
- `core/autonomous_loop.py` 是独立的 LLM 驱动「假设树探索」研究模块，
  **不属于本生产链路**，仅在需要「自主发现新假设」时作为上游 candidate 来源。
- 对外可信交付（VeritasGuard 证书、trust_surface 数据）均以本包产出为准。

以 AgentTeams 的 Leader-Worker 拓扑与 AgentLoop 数据飞轮为设计基点，
用零依赖的本地运行时驱动真实的科学验证闭环（非玩具实现）。
"""

__version__ = "1.0.0"
