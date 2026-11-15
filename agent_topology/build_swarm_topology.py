"""
Build SwarmLabs multi-agent topologies from the flybrain-derived motif templates.

MOTIF-LEVEL builder — one edge table per motif (inhibitory_hub /
reservoir_smallworld / recurrent_feedback).

Offline by design: reads swarm_agent_topology_motifs.json (no flybrain server needed).
Output: swarm_agent_topologies.json — a ready-to-instantiate graph spec for agentteams.

For ORGANISM-LEVEL configs (many modules x cells, composed by C1), see
`load_organism_config.py` — it loads the `<name>.swarm.json/.yaml` that
flybrain-connectome-mcp/synthetic_bio_topology.py emits.

Usage:
    python agent_topology/build_swarm_topology.py
"""
from __future__ import annotations

import json
import os

from flybrain_connectome_client import build_agent_topology

HERE = os.path.dirname(os.path.abspath(__file__))
MOTIF_PATH = os.path.join(HERE, "swarm_agent_topology_motifs.json")

# K sizing per motif (tunable for your swarm size)
K = {
    "inhibitory_hub": 12,       # 1 coordinator + 12 guarded workers
    "reservoir_smallworld": 64,  # collective short-term memory buffer
    "recurrent_feedback": 8,    # 8 self-stabilizing modules
}


def main() -> dict:
    motif_doc = json.load(open(MOTIF_PATH, encoding="utf-8"))
    out = {
        "schema_version": "1.0",
        "source": "swarm_agent_topology_motifs.json (MaleCNS v1.0 derived)",
        "topologies": {},
    }
    for name, mot in motif_doc["motifs"].items():
        k = K.get(name, 12)
        topo = build_agent_topology(mot, K_workers=k)
        out["topologies"][name] = {**topo, "K_workers": k}
        print(f"{name:22s} nodes={len(topo['nodes']):3d} edges={len(topo['edges']):3d}")
    dest = os.path.join(HERE, "swarm_agent_topologies.json")
    json.dump(out, open(dest, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"wrote {dest}")
    return out


if __name__ == "__main__":
    main()
