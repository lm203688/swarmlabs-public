# SwarmLabs `agent_topology` — 果蝇连接组衍生的多智能体拓扑

> 原则：**蜂群科研不嵌入果蝇数据**。166,700 神经元 / 1.24 亿突触始终留在独立项目
> `flybrain-connectome`（本地 MCP/HTTP 服务）。本目录只持有**派生的 motif 模板
> JSON**（拓扑描述，不存原始神经元），并把它们翻译成 SwarmLabs 多智能体图。

## 文件

| 文件 | 作用 |
|---|---|
| `swarm_agent_topology_motifs.json` | 真实连接组（MaleCNS v1.0）抽取的三 motif 模板：抑制枢纽 / 小世界储备池 / 递归反馈。离线快照，可断网使用。 |
| `flybrain_connectome_client.py` | drop-in 客户端：`FlybrainHTTPClient`（实时查，含 `export_swarm_motifs` 运行时拉取）+ `build_agent_topology`（离线，仅用 motif JSON）。与 flybrain 项目 `motifs/` 下的同名文件 sha256 一致。 |
| `flybrain_connectome.yaml` | 连接配置（transport/base_url/api_key/motif_source/motif_path）。密钥走 `FLYBRAIN_API_KEY` 环境变量，不写进 git。 |
| `build_swarm_topology.py` | **motif 级**离线构建：三 motif → `swarm_agent_topologies.json`（每种 motif 一张边表）。 |
| `load_organism_config.py` | **organism 级**加载器：读 C1 产出的 `<name>.swarm.json/.yaml`，独立复算校验（fail-closed）、合并进 `agentteams/config/team.yaml` 形状。 |
| `__init__.py` | 包导出上述 API。 |

## 两种 motif → 蜂群科研落地

| Motif | 生物原型（真实数字） | SwarmLabs 用法 |
|---|---|---|
| `inhibitory_hub` | APL 单枢纽广播抑制（fan_out 102,263；APL→DPM 711 突触） | 1 个协调/护栏 agent 对 top-K worker 发**负权边**，防失控/超提交 |
| `reservoir_smallworld` | 小世界布线（fanout 101–1000 占 49.36%） | K 个松散 agent 当集体短期记忆，readout agent 读状态 |
| `recurrent_feedback` | 子系统自循环（ol_intrinsic 28.74%） | 模块输出回灌自身，跨步保活状态，无需外部存储 |

---

## A. motif 来源：运行时连服务，快照只作离线兜底

生成器（C1）**不再读第二份静态文件**，而是运行时决定 motif 从哪来：

| `--motif-source` | 行为 | 用途 |
|---|---|---|
| `api` | 必须连上服务；拉不到直接报错退出（不静默降级） | CI/巡检，确认"服务真的活着" |
| `auto`（默认） | 服务 → API 热缓存 → 离线快照，逐级回退 | 生产：正常走实时，服务挂了也不停工 |
| `local` | 只读快照，完全不碰网络 | 断网/救援/可复现实验 |

key 优先级：`--api-key` → `FLYBRAIN_API_KEY` → 服务自己的 `mcp_connectome/config.json`
（仅同机可用；**SwarmLabs 侧没有那个文件**，所以必须显式给 key）。
**鉴权失败（401/403）判定为配置错误，即使在 `auto` 下也直接抛错**，不会悄悄退回旧快照。

三种模式都会把来源写进产物 `motif_provenance`：`mode` / `served_from` /
`doc_sha256` / `fallback_reason`。**因此任何一份拓扑都能被追溯**——是基于实时服务，
还是基于某天的冻结快照。

启动服务（数据在基地文件夹，5.6 GB，已校验）：

```bash
cd C:\Users\xing\Desktop\flybrain-connectome\flybrain-connectome-mcp
set FLYBRAIN_DATA_DIR=C:\Users\xing\Desktop\flybrain-connectome\male_cns
C:\Users\xing\.workbuddy\binaries\python\envs\minimind\Scripts\python.exe run_api.py
```

```python
from agent_topology import FlybrainHTTPClient
c = FlybrainHTTPClient("http://127.0.0.1:8799")   # key 从 FLYBRAIN_API_KEY 读取
print(c.pair_synapses(10540, 12569))              # -> 711 (已解包)
print(c.export_swarm_motifs()["motifs"].keys())   # 运行时拉取的 motif 文档
```

---

## B. C1 拓扑 → SwarmLabs 可实例化 config

C1 每产出一个虚拟生物，会同时写出 SwarmLabs 能直接加载的配置三件套：

```
generated_organisms/
  <name>.json                    原始 organism（节点/边/metrics/provenance）
  <name>.html                    交互式力导向预览
  swarmlabs/<name>.swarm.json    ★ SwarmLabs 可实例化 config（规范真值）
  swarmlabs/<name>.swarm.yaml     同一对象的 YAML 镜像（人工可读可改）
```

config 结构（对齐 `agentteams/config/team.yaml`）：

- `swarm.agentteams.leader` —— 抑制枢纽（guardrail）即 leader；若没有则取第一个模块代表
- `swarm.agents[]` —— 每个 agent 带 `role`（organism 层）+ `swarm_role`（leader/planner/executor/verifier）
- `swarm.modules[]` —— 模块代表 + 成员 + 模块内递归权重
- `swarm.edges[]` —— 全量带权边表（`kind` ∈ inhibitory / reservoir / recurrent）
- `swarm.guardrails[]` —— 拓扑不变式（护栏必须是模块外节点、负权边不可被抵消……）
- `swarm.validation` —— 生成时的自检结果（悬空边/权重区间/leader 归属）
- `swarm.source.motif_provenance` —— 回到 A 的来源链

在 SwarmLabs 里加载：

```bash
# 单文件体检
python agent_topology/load_organism_config.py <path>\<name>.swarm.yaml

# 合并进 agentteams team 形状并落盘
python agent_topology/load_organism_config.py <path>\<name>.swarm.json \
    --merge-team agentteams/config/team.yaml --out merged_team.json
```

```python
from agent_topology import load_organism_config, to_agentteams_team, validate_swarm_config

cfg = load_organism_config(r"...\sample_organism.swarm.json")
assert validate_swarm_config(cfg)["ok"]
team = to_agentteams_team(cfg)     # 校验不通过会抛 SwarmConfigError（fail-closed）
```

**fail-closed**：`to_agentteams_team` 会**独立复算**校验（不信任文件里的
`validation` 块），发现悬空边 / 权重越界 / 负权edge不是 inhibitory /
模块内自环以外 recurrent / leader 掉进模块内 —— 任一命中即拒绝产出 team。

---

## 诚实注意事项

- motif 是**拓扑模式**，不是神经元数据；真实 16 万神经元永远只在 flybrain 项目，SwarmLabs 只引用 body id。
- 数据目录 `C:\Users\xing\Desktop\flybrain-connectome\male_cns` 缺失时，`api` / 实时查询不可用，但 `auto`（回退快照）与 `local` **照常可用**。
- 实测数字（均来自真实连接组）：总突触 124,177,617；APL→DPM 711；fanout 101–1000 占比 49.36%；ol_intrinsic 自循环 28.74%。
- `APL` 的 `neurotransmitter` 字段在该数据集里为空；"GABAergic" 来自文献，非文件标注。拓扑价值不受影响。
- `agent_topologies` 的解析/校验是**拓扑层**检查，不代表生物真实性——生物真实性由 flybrain 项目的数据口径与 `DATA_MANIFEST.json` 保证。
