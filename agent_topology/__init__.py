"""SwarmLabs agent_topology — flybrain-derived multi-agent topology templates.

Keeps SwarmLabs independent of the 166,700-neuron flybrain data: we only hold
the derived MOTIF JSON (topology descriptions) and build agent graphs from it.

Two layers:
  1. motif -> topology      build_agent_topology(motif, K)
  2. topology -> team spec  load_organism_config(...) / to_agentteams_team(...)
"""
from .flybrain_connectome_client import (
    FlybrainHTTPClient,
    build_agent_topology,
    TOOL_NAMES,
)
from .load_organism_config import (
    SwarmConfigError,
    build_adjacency,
    load_organism_config,
    summarize,
    to_agentteams_team,
    validate_swarm_config,
)

__all__ = [
    "FlybrainHTTPClient",
    "build_agent_topology",
    "TOOL_NAMES",
    "load_organism_config",
    "validate_swarm_config",
    "to_agentteams_team",
    "build_adjacency",
    "summarize",
    "SwarmConfigError",
]
