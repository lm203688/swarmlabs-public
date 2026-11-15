"""
load_organism_config.py  —  SwarmLabs loader for C1-generated agent-swarm configs
--------------------------------------------------------------------------------
C1 (`flybrain-connectome-mcp/synthetic_bio_topology.py`) emits, alongside every
virtual organism, a SwarmLabs-instantiable config pair:

    <name>.swarm.json    canonical, zero-dependency
    <name>.swarm.yaml    same object, human-editable

This module turns that file into something the SwarmLabs runtime can actually
use: validated agent list, leader, module breakdown, adjacency, and a merge into
the existing `agentteams/config/team.yaml` shape.

Design rules
    * The config file is the single source of truth — we never re-derive edges.
    * Validation is fail-closed: `validate_swarm_config` reports problems, and
      `to_agentteams_team` refuses to produce a team when the config is invalid
      (unless you pass force=True), so a broken topology can't silently ship.
    * YAML is optional: prefer the `.swarm.json` twin when PyYAML is missing.

CLI
    python agent_topology/load_organism_config.py <file.swarm.json|yaml>
    python agent_topology/load_organism_config.py <file> --merge-team \
        agentteams/config/team.yaml --out merged_team.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


class SwarmConfigError(ValueError):
    """Raised when a config file is missing, malformed, or fails validation."""


# ---------------------------------------------------------------------- #
# loading
# ---------------------------------------------------------------------- #
def load_organism_config(path: str | os.PathLike) -> dict:
    """Load a C1 swarm config from .json / .yaml / .yml (json preferred)."""
    p = Path(path)
    if not p.exists():
        # tolerate being handed the organism file instead of the swarm file
        alt = p.with_suffix(".swarm.json")
        if alt.exists():
            p = alt
        else:
            raise SwarmConfigError("config not found: %s (also tried %s)" % (p, alt))
    text = p.read_text(encoding="utf-8")
    if p.suffix.lower() in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore
        except ImportError as e:  # pragma: no cover
            raise SwarmConfigError(
                "PyYAML not available — load the .swarm.json twin instead: %s"
                % p.with_suffix(".swarm.json")) from e
        cfg = yaml.safe_load(text)
    else:
        cfg = json.loads(text)
    if not isinstance(cfg, dict) or "swarm" not in cfg:
        raise SwarmConfigError("%s is not a swarm config (no top-level 'swarm')" % p)
    return cfg


# ---------------------------------------------------------------------- #
# validation (fail-closed)
# ---------------------------------------------------------------------- #
def validate_swarm_config(cfg: dict) -> dict:
    """Independent re-check of the emitted config. Returns a problems dict.

    This intentionally re-derives the invariants rather than trusting the
    `validation` block the generator wrote — a generator that is wrong in the
    same way twice would otherwise validate itself.
    """
    swarm = cfg.get("swarm") or {}
    agents = swarm.get("agents") or []
    edges = swarm.get("edges") or []
    ids = [a.get("id") for a in agents]
    idset = set(ids)

    problems: dict[str, list] = {
        "duplicate_agent_ids": sorted({i for i in ids if i is not None and ids.count(i) > 1}),
        "missing_agent_id": [i for i, a in enumerate(agents) if not a.get("id")],
        "unknown_swarm_role": sorted({a.get("swarm_role") for a in agents
                                      if a.get("swarm_role") not in
                                      {"leader", "planner", "executor", "verifier"}}),
        "dangling_edges": [[e.get("from"), e.get("to")] for e in edges
                           if e.get("from") not in idset or e.get("to") not in idset],
        "weights_out_of_range": [[e.get("from"), e.get("to"), e.get("weight")]
                                 for e in edges
                                 if not isinstance(e.get("weight"), (int, float))
                                 or abs(e.get("weight")) > 1.0],
        "neg_weight_not_inhibitory": [[e.get("from"), e.get("to"), e.get("kind")]
                                      for e in edges
                                      if isinstance(e.get("weight"), (int, float))
                                      and e["weight"] < 0
                                      and e.get("kind") != "inhibitory"],
        "non_negative_inhibitory": [[e.get("from"), e.get("to"), e.get("weight")]
                                    for e in edges
                                    if e.get("kind") == "inhibitory"
                                    and isinstance(e.get("weight"), (int, float))
                                    and e["weight"] >= 0],
        "cross_module_recurrent": [],
    }

    module_of = {a.get("id"): a.get("module") for a in agents}
    for e in edges:
        if e.get("kind") == "recurrent" and e.get("from") != e.get("to"):
            problems["cross_module_recurrent"].append([e.get("from"), e.get("to")])

    leader = (swarm.get("agentteams") or {}).get("leader")
    if leader and leader not in idset:
        problems.setdefault("leader_unknown", []).append(leader)
    if leader and module_of.get(leader) is not None:
        problems.setdefault("leader_inside_module", []).append(leader)

    guardrails = swarm.get("guardrails") or []
    if not guardrails:
        problems.setdefault("missing_guardrails", []).append(True)

    problems["ok"] = all(len(v) == 0 for k, v in problems.items() if k != "ok")
    problems["n_agents"] = len(agents)
    problems["n_edges"] = len(edges)
    return problems


# ---------------------------------------------------------------------- #
# instantiation helpers
# ---------------------------------------------------------------------- #
def build_adjacency(cfg: dict) -> dict:
    """{"nodes": {id: agent}, "out": {id: [edge...]}, "in": {id: [edge...]}}."""
    swarm = cfg["swarm"]
    out: dict[str, list] = {a["id"]: [] for a in swarm["agents"]}
    inc: dict[str, list] = {a["id"]: [] for a in swarm["agents"]}
    for e in swarm["edges"]:
        out.setdefault(e["from"], []).append(e)
        inc.setdefault(e["to"], []).append(e)
    return {
        "nodes": {a["id"]: a for a in swarm["agents"]},
        "out": out,
        "in": inc,
        "leader": (swarm.get("agentteams") or {}).get("leader"),
    }


def to_agentteams_team(cfg: dict, base_team: dict | None = None,
                       force: bool = False) -> dict:
    """Merge a C1 swarm config into the `agentteams/config/team.yaml` shape.

    Refuses to emit a team when validation fails (fail-closed), unless
    force=True — a broken topology must not silently reach the runtime.
    """
    report = validate_swarm_config(cfg)
    if not report["ok"] and not force:
        bad = {k: v for k, v in report.items()
               if k not in ("ok", "n_agents", "n_edges") and v}
        raise SwarmConfigError("swarm config failed validation: %s" % bad)

    swarm = cfg["swarm"]
    leader = (swarm.get("agentteams") or {}).get("leader")
    adj = build_adjacency(cfg)
    role_contracts = (swarm.get("agentteams") or {}).get("role_map", {})
    thresholds = (swarm.get("agentteams") or {}).get("thresholds", {})

    team = dict(base_team or {})
    team["team"] = {
        "name": swarm.get("name"),
        "topology": swarm.get("topology"),
        "runtime": swarm.get("runtime", "local"),
        "description": (
            "%s —— C1 由果蝇连接组 motif 合成的虚拟生物群。"
            "leader 为抑制枢纽（护栏），模块代表为 planner，细胞为 executor。"
            % swarm.get("name")),
    }
    team["leader"] = {
        "agent_id": leader,
        "role": "leader",
        "impl": "agentteams.core.runtime.Leader",
        "source_organism": "flybrain-connectome",
    }
    team["workers"] = [
        {"agent_id": a["id"], "role": a.get("swarm_role"),
         "organism_role": a.get("role"), "module": a.get("module"),
         "duty": _duty_for(a)}
        for a in swarm["agents"] if a["id"] != leader
    ]
    team["modules"] = swarm.get("modules", [])
    team["graph"] = {"edges": swarm["edges"], "n_nodes": len(swarm["agents"]),
                     "n_edges": len(swarm["edges"])}
    team["guardrails"] = swarm.get("guardrails", [])
    team["agentteams_role_map"] = role_contracts
    if thresholds:
        team["thresholds"] = thresholds
    team["validation"] = validate_swarm_config(cfg)
    return team


def _duty_for(agent: dict) -> str:
    role = agent.get("role")
    if role == "guardrail":
        return "全局护栏：对模块发负权刹车边，否决越界/超提交动作"
    if role == "tissue_rep":
        return "模块代表：汇总本模块细胞结果并向 leader 上报，维护模块内递归记忆"
    if role == "cell":
        return "执行体：承担本模块的具体任务，按 motif 边权与邻居交互"
    return "执行体"


def summarize(cfg: dict) -> str:
    swarm = cfg["swarm"]
    v = validate_swarm_config(cfg)
    leader = (swarm.get("agentteams") or {}).get("leader")
    kinds: dict[str, int] = {}
    for e in swarm["edges"]:
        kinds[e.get("kind", "plain")] = kinds.get(e.get("kind", "plain"), 0) + 1
    lines = [
        "swarm           : %s" % swarm.get("name"),
        "topology        : %s" % swarm.get("topology"),
        "leader          : %s" % leader,
        "agents          : %d (modules=%d)" % (len(swarm["agents"]),
                                               len(swarm.get("modules", []))),
        "edges           : %d  %s" % (len(swarm["edges"]), kinds),
        "motif provenance: %s" % json.dumps(
            ((swarm.get("source") or {}).get("motif_provenance") or {}).get("mode"),
            ensure_ascii=False),
        "validation      : %s" % ("OK" if v["ok"] else "FAILED"),
    ]
    if not v["ok"]:
        lines.append("  problems: %s" % {
            k: x for k, x in v.items()
            if k not in ("ok", "n_agents", "n_edges") and x})
    return "\n".join(lines)


# ---------------------------------------------------------------------- #
# CLI
# ---------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Load / validate a C1 swarm config")
    ap.add_argument("config", help="path to <name>.swarm.json (or .yaml)")
    ap.add_argument("--merge-team", default=None,
                    help="existing agentteams/config/team.yaml to merge into")
    ap.add_argument("--out", default=None, help="write merged team JSON here")
    ap.add_argument("--json", action="store_true", help="dump raw config")
    args = ap.parse_args(argv)

    cfg = load_organism_config(args.config)
    print(summarize(cfg))

    if args.json:
        print(json.dumps(cfg, ensure_ascii=False, indent=2)[:2000])

    if args.merge_team:
        base = None
        p = Path(args.merge_team)
        if p.exists():
            try:
                import yaml  # type: ignore
                base = yaml.safe_load(p.read_text(encoding="utf-8"))
            except ImportError:
                print("! PyYAML missing — merging without the base team file",
                      file=sys.stderr)
        team = to_agentteams_team(cfg, base_team=base)
        if args.out:
            Path(args.out).write_text(
                json.dumps(team, ensure_ascii=False, indent=2), encoding="utf-8")
            print("wrote %s" % args.out)
        else:
            print(json.dumps(team, ensure_ascii=False, indent=2)[:1500])

    return 0 if validate_swarm_config(cfg)["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
