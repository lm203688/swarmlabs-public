"""CLI 入口。

常用命令
--------
  # 1) 端到端跑一次（只读，高风险写操作会被拦下并列为待批准）
  python -m agentteams.run --request agentteams/samples/request_monod.json

  # 2) 批准知识库修正后重跑（会先自动快照，可回滚）
  python -m agentteams.run --request agentteams/samples/request_monod.json --approve

  # 3) 回滚某次运行的所有写操作
  python -m agentteams.run --rollback run-20260810-140233

  # 4) 导出 Skill 清单（提交材料可直接用）
  python -m agentteams.run --skills > docs/skill_catalog_generated.md
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

if __package__ in (None, ""):                       # 支持 `python agentteams/run.py`
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "agentteams"

from agentteams.agents.executor import ExecutorAgent          # noqa: E402
from agentteams.agents.leader import TeamLeader               # noqa: E402
from agentteams.agents.planner import PlannerAgent            # noqa: E402
from agentteams.agents.verifier import VerifierAgent          # noqa: E402
from agentteams.core.context import Context                   # noqa: E402
from agentteams.core.loop import load_state                   # noqa: E402
from agentteams.core.policy import Policy                     # noqa: E402
from agentteams.core.runtime import build_runtime             # noqa: E402
from agentteams.core.trace import Tracer                      # noqa: E402
from agentteams.skills import _engine                         # noqa: E402
from agentteams.skills.registry import default_registry       # noqa: E402

PKG_ROOT = os.path.dirname(os.path.abspath(__file__))
RUNS_DIR = os.path.join(PKG_ROOT, "runs")


def load_yaml(path):
    try:
        import yaml
    except ImportError:                                        # pragma: no cover
        raise SystemExit("缺少依赖 pyyaml：pip install -r agentteams/requirements.txt")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_config():
    team = load_yaml(os.path.join(PKG_ROOT, "config", "team.yaml"))
    policy = load_yaml(os.path.join(PKG_ROOT, "config", "policy.yaml"))
    cfg = dict(team)
    cfg.update(policy)
    return cfg


def build_workers(topology_file=None):
    """按配置构建 Worker 集合（INTERFACE.md Phase 1 / S1）。

    * ``topology_file`` 为空 → 默认 3-worker（加上 leader 即 4-agent 团队），
      与历史行为**逐字节一致**，保证既有 runs 可复现。
    * 非空 → 读 flybrain 的 ``.swarm.json``，按 motif 拓扑动态生成 N-agent
      团队（含 ``task_hint`` / ``module`` 语义，为 Phase 3 路由备好）。

    返回 ``(workers, topology_meta)``；``topology_meta`` 为 ``None`` 表示走了
    默认路径。
    """
    if not topology_file:
        return {"planner": PlannerAgent(), "executor": ExecutorAgent(),
                "verifier": VerifierAgent()}, None
    from agentteams.core.runtime import build_workers_from_topology
    workers, meta, _team = build_workers_from_topology(topology_file)
    return workers, meta


def main(argv=None):
    ap = argparse.ArgumentParser(description="SwarmLabs AgentTeams 形 Leader-Worker 运行器")
    ap.add_argument("--request", help="请求 JSON 文件路径")
    ap.add_argument("--goal", help="直接给目标（不用请求文件时）")
    ap.add_argument("--scenarios", help="逗号分隔的场景 key")
    ap.add_argument("--max-scenarios", type=int, default=3)
    ap.add_argument("--approve", action="store_true",
                    help="批准本次运行中的高风险写操作（Human-in-the-Loop）")
    ap.add_argument("--online", action="store_true",
                    help="允许联网拉取 arXiv 参考文献（默认离线以保证可复现）")
    ap.add_argument("--runtime", default=None, help="local | agentteams")
    ap.add_argument("--topology", default=None,
                    help="C1 .swarm.json 路径：按 flybrain motif 拓扑动态组建 "
                         "N-agent 团队（缺省沿用 team.yaml 的 team.topology_file，"
                         "两者都空则默认 4-agent，向后兼容）")
    ap.add_argument("--local-model", default=None,
                    help="启用本地小模型顾问层：'auto' 按 flybrain→minimind→ornith "
                         "探测，或直接给模型名（如 flybrain:latest）。顾问只把判定"
                         "结果转成叙述，**不参与任何数值判定**")
    ap.add_argument("--no-local-model", action="store_true",
                    help="强制关闭本地小模型顾问层（覆盖 team.yaml 的 local_model.enabled）")
    ap.add_argument("--rollback", help="回滚指定 run_id 的全部写操作")
    ap.add_argument("--skills", action="store_true", help="导出 Skill 清单 Markdown 后退出")
    ap.add_argument("--selftest", action="store_true", help="自检：配置/Skill/角色授权一致性")
    args = ap.parse_args(argv)

    registry = default_registry()

    if args.skills:
        print(registry.to_markdown())
        return 0

    cfg = load_config()

    if args.selftest:
        problems = registry.validate_roles(cfg.get("roles"))
        # 反向检查：注册了但没被任何角色授权的 Skill（潜在的"僵尸能力"）
        granted = {s for r in (cfg.get("roles") or {}).values()
                   for s in (r.get("allow_skills") or [])}
        orphan = [n for n in registry.names() if n not in granted]
        print(f"已注册 Skill：{registry.names()}")
        print(f"角色：{sorted((cfg.get('roles') or {}))}")
        print(f"配置问题：{problems or '无'}")
        print(f"未被任何角色授权的 Skill：{orphan or '无'}")
        return 0 if not problems else 1

    if args.rollback:
        # 既接受 run_id（run-2026...），也接受相对/绝对路径，避免二次拼接。
        cand = [args.rollback,
                os.path.abspath(args.rollback),
                os.path.join(RUNS_DIR, os.path.basename(args.rollback.rstrip("/\\")))]
        run_dir = next((c for c in cand if os.path.isdir(c)), "")
        if not run_dir:
            print("找不到运行目录，已尝试：\n  " + "\n  ".join(cand))
            return 1
        res = Policy.restore(run_dir)
        print(f"回滚完成：还原 {res['restored']} 个文件")
        for d in res.get("detail", []):
            print("  -", d)
        return 0

    _engine.set_offline(not args.online)

    # -- 组装请求 ----------------------------------------------------------
    request = {}
    if args.request:
        with open(args.request, encoding="utf-8") as f:
            request = json.load(f)
    if args.goal:
        request["goal"] = args.goal
    if args.scenarios:
        request["scenarios"] = [s.strip() for s in args.scenarios.split(",") if s.strip()]
    request.setdefault("goal", "验证科学场景精度与误差棒，并给出下一步实验方向")
    request.setdefault("max_scenarios", args.max_scenarios)

    # -- 运行环境 ----------------------------------------------------------
    # run_id 精确到秒，而 trace/audit 是 append 写入：同一秒内起两次会把两份
    # 结论混进同一目录（观测表现为 span 数翻倍、产物互相覆盖）。自动化场景
    # （飞轮 / CI / 双路径对照）非常容易撞上，所以这里做一次显式避让。
    run_id = f"run-{time.strftime('%Y%m%d-%H%M%S')}"
    run_dir = os.path.join(RUNS_DIR, run_id)
    if os.path.exists(run_dir):
        for i in range(2, 1000):
            cand = f"{run_id}-{i}"
            cand_dir = os.path.join(RUNS_DIR, cand)
            if not os.path.exists(cand_dir):
                run_id, run_dir = cand, cand_dir
                break
    os.makedirs(run_dir, exist_ok=True)

    tracer = Tracer(run_dir, run_id)
    approvals = request.get("approvals") or {}
    policy = Policy(cfg, run_dir, tracer, approve_all=args.approve, approvals=approvals)
    state = load_state(PKG_ROOT)
    ctx = Context(cfg=cfg, policy=policy, tracer=tracer, registry=registry,
                  run_dir=run_dir, root=PKG_ROOT, state=state, run_id=run_id)

    problems = registry.validate_roles(cfg.get("roles"))
    if problems:
        print("配置校验失败：")
        for p in problems:
            print("  -", p)
        return 1

    runtime_name = args.runtime or (cfg.get("team", {}) or {}).get("runtime", "local")
    runtime = build_runtime(runtime_name)

    # -- 拓扑（S1/S2）：CLI 优先 → team.yaml 的 team.topology_file → 默认 4-agent
    topology_file = args.topology or (cfg.get("team", {}) or {}).get("topology_file")
    try:
        workers, topology_meta = build_workers(topology_file)
    except (ValueError, OSError) as e:
        # fail-closed：坏拓扑绝不静默启动（宁可拒绝，也不跑一个被忽略的 motif）
        print(f"拓扑加载失败（fail-closed，未启动运行）：{type(e).__name__}: {e}")
        return 1

    # -- 本地小模型顾问层：team.yaml 为底，CLI 覆盖 -------------------------
    # 默认关闭，保证既有 runs 复现；显式开启后其输出只进 loop_report，无裁决权。
    local_model_cfg = dict((cfg.get("team", {}) or {}).get("local_model") or {})
    if args.no_local_model:
        local_model_cfg["enabled"] = False
    if args.local_model:
        local_model_cfg["enabled"] = True
        local_model_cfg["model"] = args.local_model
    local_model_cfg.setdefault("enabled", False)
    local_model_cfg.setdefault("model", "auto")

    leader = TeamLeader(workers, runtime, PKG_ROOT, topology=topology_meta,
                        local_model=local_model_cfg)
    team = leader.run(request, ctx)

    payload = team.to_dict()
    payload["blocked"] = ctx.blocked
    if topology_meta:
        payload["topology_source"] = topology_meta
    with open(os.path.join(run_dir, "result.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)

    # -- 控制台摘要 --------------------------------------------------------
    ev = team.loop_report.get("evaluate", {})
    tel = team.loop_report.get("analyze", {})
    print(f"\n== SwarmLabs AgentTeams · {run_id} ==")
    print(f"目标      : {team.goal}")
    print(f"运行时    : {runtime.name}（拓扑 leader-worker）")
    if topology_meta:
        print(f"拓扑来源  : {topology_meta.get('name')} · "
              f"{topology_meta.get('n_agents')} agents / "
              f"{topology_meta.get('n_edges')} edges / "
              f"{topology_meta.get('n_modules')} modules "
              f"(schema v{topology_meta.get('schema_version')}, "
              f"leader={topology_meta.get('leader')})")
    else:
        print("拓扑来源  : 默认 4-agent（未指定 topology_file）")
    # 本地小模型顾问层：状态必须显式可见（含不可用事实），不能静默
    adv = team.loop_report.get("local_model_advisory")
    if adv:
        st = str(adv.get("status"))
        if st == "ok":
            print(f"本地模型  : {adv.get('model')} · digest {str(adv.get('digest'))[:12]}"
                  f" · {adv.get('latency_ms')}ms（顾问层，无裁决权）")
            txt = (adv.get("text") or "").strip().replace("\n", " ")
            if txt:
                print(f"  复核叙述: {txt[:180]}{'…' if len(txt) > 180 else ''}")
        else:
            print(f"本地模型  : {st} —— {str(adv.get('reason', ''))[:120]}")
    elif local_model_cfg.get("enabled"):
        print("本地模型  : 未产出顾问结果（异常路径，判定不受影响）")
    else:
        print("本地模型  : 顾问层未启用（--local-model auto 可开启）")
    print(f"团队状态  : {team.status}")
    print(f"成熟度    : {ev.get('scorecard')} · 已收敛 {ev.get('converged_ratio')} · 得分 {ev.get('team_score')}")
    for r in team.loop_report.get("rows", []):
        print(f"  - {r['key']:<22} rel={r['rel']:<8.4f} cov={r['coverage']:<6.2f} "
              f"cal={r['cal_err']:<6.3f} {r['maturity']}")
    print(f"可观测    : span {tel.get('spans')}（agent {tel.get('agent_calls')} / skill {tel.get('skill_calls')}）"
          f" · 拦截 {tel.get('blocked')} · 审计 {tel.get('audit_events')}")

    # -- 治理摘要（任务看板 / 可行性预校验 / 防跑偏守卫）--------------------
    gov = team.loop_report.get("governance") or {}
    bd, fz, df = gov.get("board") or {}, gov.get("feasibility") or {}, gov.get("drift") or {}
    if bd:
        c = bd.get("counts", {})
        print(f"任务看板  : {bd.get('health')} · "
              f"完成 {c.get('completed', 0)} / 阻断 {c.get('blocked', 0)} / "
              f"取消 {c.get('cancelled', 0)} / 挂起 {c.get('pending', 0) + c.get('active', 0)}")
        for line in (leader.board.render().splitlines() if leader.board else []):
            print(line)
        fp = bd.get("failure_points") or []
        if fp:
            # 跳过与故障都显示为 cancelled，但归因不同：故障点才是要修的那个
            print(f"  故障点  : {fp}（其余 cancelled 为上游连锁跳过）")
    if fz:
        flag = "通过" if fz.get("ok") else "不可行"
        print(f"可行性    : {flag}（{fz.get('status')}）")
        for rsn in (fz.get("reasons") or []):
            print(f"  - {rsn}")
        for drop in (fz.get("dropped_scenarios") or []):
            print(f"  - 剔除场景 {drop}：不在目录内，已跳过而非静默失败")
    subst = gov.get("scenario_substitution") or {}
    if subst.get("substituted"):
        sev = "全部落空" if subst.get("total_miss") else "部分替换"
        print(f"场景替换  : {sev} · 点名 {len(subst.get('requested') or [])} · "
              f"实际跑 {len(subst.get('picked') or [])}")
        print(f"  - 不在目录：{subst.get('unknown')}")
        print(f"  - 实际执行：{subst.get('picked')}")
        if subst.get("total_miss"):
            print("  - 本次结论不覆盖原定目标，请核对场景 key 后重跑")
    if df:
        print(f"防跑偏    : {'无异常' if df.get('clean') else '发现异常'} · "
              f"调用 {df.get('total_calls')} · 去重后 {df.get('distinct_fingerprints')} · "
              f"慢调用 {len(df.get('slow_calls') or {})}")
        for e in (df.get("events") or []):
            print(f"  - [{e.get('kind')}] {e.get('message')}")

    if ctx.blocked:
        print("待人工确认:")
        for b in ctx.blocked:
            print(f"  - {b['skill']}（{b['actor']}）：{b['reason']}")
        print("  → 重跑并加 --approve 即可执行（执行前自动快照，可 --rollback 还原）")
    print(f"产物      : {os.path.relpath(run_dir, os.path.dirname(PKG_ROOT))}"
          f"  [report.md / result.json / board.json / trace.jsonl / audit.jsonl]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
