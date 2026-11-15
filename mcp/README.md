# SwarmLabs V&V MCP Server

An **independent verification gate for any AI-for-Science model**, exposed as an
[Model Context Protocol](https://modelcontextprotocol.io) server.

## Why this exists

In 2026, frontier research agents (GPT-6 Astra on the Insilico DDD antibody
developability benchmark, Stanford Biomni, DrugAgent, …) began *generating*
scientific results with no bioinformatics plugin and no fine-tuning. The reviews
are unanimous: generic LLM agents are *"prone to plausible-but-wrong outputs,
weak guardrails"* and every deployment needs *"a held-out benchmark + quantified
uncertainty + a human checkpoint."*

SwarmLabs **is that checkpoint**. This server wraps the SwarmLabs virtual-experiment
V&V engine (`engine/surrogate.py` + `engine/report_generator.py`) as MCP tools so a
research agent built on **any** base model can submit its own predictions and get
an honest, computed verdict. This converts *"being disrupted by GPT-6"* into
*"being integrated into GPT-6's pipeline as the trust layer."*

## Tools

| Tool | What it does |
|---|---|
| `list_scenarios` | List all 95 published scenarios (key / domain / dim / references). |
| `get_verdict_summary` | Published PASS / MARGINAL / REFUTED / ERROR counts (from live index). |
| `get_report` | Render a full 10-chapter V&V report: `md` / `html` / `json` / `docx-b64`. |
| `get_uncertainty_budget` | First-class honesty numbers: noise floor, calibration κ, coverage, cal_err. |
| `verify_prediction` | **The gate.** Feed a model's own predictions; we own the ground truth and return R² / coverage / κ + `PROCEED` / `BLOCK`. |
| `agentic_verify_loop` | 5-stage agentic verification loop with a hard confidence gate. |
| `verification_gate` | Static trust decision per scenario from the published ledger (no engine needed). |

### No Python? Use the HTTP gate instead

The same decisions are published as a static, machine-readable ledger, so an agent
that cannot run Python can still ask "can this scenario be trusted right now?":

| Endpoint | Returns |
|---|---|
| `GET https://swarmlabs.tools/v3/gate` | Full ledger: policy, counts, blocked list, per-scenario entries. |
| `GET https://swarmlabs.tools/v3/gate/{key}` | One scenario's gate + uncertainty budget + report links. |
| `GET https://swarmlabs.tools/v3/reports` | The 95-scenario report index. Per-scenario JSON: `GET /reports/json/{key}.json`; HTML: `GET /reports/html/{key}.html`; DOCX: `GET /reports/docx/{key}.docx`. |

Generated offline by `scripts/build_gate_ledger.py` and refreshed automatically by
the daily flywheel. **Honest boundary:** the HTTP gate answers the *static* question
("is this scenario trustworthy?"); verifying a model's *own arbitrary predictions*
against the noise-free ground truth needs the engine, i.e. this MCP server.

`mcp/public_gate_server.py` is the **distributable** edition of this server: it has
no engine dependency and answers purely from those HTTP endpoints, so it can be
shipped openly (e.g. inside `swarmlabs-engine-kit`). Its `verify_prediction` returns
an explicit "not available in the public edition" instead of a confident guess.

Both the MCP server and the HTTP ledger read the gate policy from one place —
`engine/verification_gate.py` — so the two can never disagree.

### `verify_prediction` — the core value

```jsonc
// A research agent calls this with the points IT predicted:
{
  "scenario_key": "micro_doe_ecoli_monod",
  "predictions": [
    {"x": [0.4, 1.2], "y_pred": 0.83, "y_std": 0.06},
    {"x": [0.9, 0.5], "y_pred": 0.41, "y_std": 0.05}
  ]
}
```

SwarmLabs evaluates those points against the **noise-free ground truth** (which the
agent does *not* have), computes out-of-sample R², prediction-interval coverage (if
`y_std` supplied), and a suggested calibration scale κ, then returns:

```jsonc
{
  "r2": 0.81, "coverage": 0.93,
  "verdict": "PASS", "gate": "PROCEED",
  "rationale": "R²=0.810 ≥ 0.90 …"   // (illustrative)
}
```

If the agent is overconfident (narrow intervals that miss truth) or simply wrong,
the verdict is `REFUTED` and `gate: "BLOCK_AUTONOMOUS_ACTION"`. The agent must not
act autonomously on that scenario until a human or more data intervenes.

## Run it

```bash
cd swarmlabs
python -m venv .venv && .venv/Scripts/activate      # or your managed venv
pip install -r mcp/requirements.txt
python mcp/swarmlabs_vv_server.py                    # stdio MCP server
python mcp/swarmlabs_vv_server.py --selftest        # offline smoke test
```

### Wire it to a client (Claude Desktop / Cursor / any MCP host)

```jsonc
// mcp.json / claude_desktop_config.json
{
  "mcpServers": {
    "swarmlabs-vv": {
      "command": "python",
      "args": ["/absolute/path/to/swarmlabs/mcp/swarmlabs_vv_server.py"]
    }
  }
}
```

The server imports the engine from the repo root, so run it from the `swarmlabs`
directory (or ensure the repo root is on `PYTHONPATH`).

## Honesty constraints (carried from the engine)

- **3% noise floor is never lowered** — no cheating the fit tighter.
- **Coverage is single-sided & conservative** — over-confident narrow bands *fail*.
- **Calibration κ only widens uncertainty, never tightens** (capped at 40).
- **1 of 95 scenarios is REFUTED and kept visible** — we do not manufacture a clean scoreboard.

## Repo layout

```
mcp/
  swarmlabs_vv_server.py   # the server (FastMCP)
  requirements.txt
  README.md
```

The same engine powers `swarmlabs.tools/report-gallery` and the public benchmark
suite at `github.com/lm203688/swarmlabs-benchmarks`.
