"""SwarmLabs Workbench — zero-dependency local web UI.

The Workbench is a *product* layer on top of the CLI: a single Python
module (stdlib only) that exposes an HTTP server with:

- GET  /                      — serve the single-file HTML front-end
- GET  /api/doctor            — environment self-check
- GET  /api/scenarios         — 82 scenario catalog
- GET  /api/gates             — 11 registered gates
- GET  /api/anchors           — 19 wet-lab anchors (3D coverage)
- GET  /api/trajectories      — recent runs (log tail)
- GET  /api/trajectory/<id>   — full trajectory snapshot
- POST /api/run               — run a work order through the pipeline
- POST /api/feedback          — submit feedback → pair-eval → audit
- GET  /api/feedback/audit    — audit log tail
- POST /api/check             — run the full check battery

Why stdlib instead of FastAPI/Flask?
------------------------------------
SwarmLabs is meant to be pip-installable without pulling a web framework
into a scientific Python environment. stdlib's ``http.server`` is
absolutely sufficient for a trusted localhost UI. Zero CDN, zero npm.

The front-end is a *single self-contained HTML file* — no CDN imports, no
fetch to remote origins, all CSS/JS inlined. This means:
- Works fully offline after ``pip install swarmlabs``.
- No mixed-content issues on a local HTTP origin.
- Easy to audit (one file, no supply chain).
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse, unquote

from swarmlabs import runner, feedback as fb_mod, trajectory as traj_mod
from swarmlabs.rebuild import repo_root as _rr


# ---------------------------------------------------------------------------
# API handlers — one function per route, all return (status, body_dict)
# ---------------------------------------------------------------------------

def _doctor() -> Dict[str, Any]:
    return runner.doctor()


def _scenarios() -> Dict[str, Any]:
    return runner.list_scenarios(limit=0)


def _gates() -> Dict[str, Any]:
    return runner.list_gates()


def _anchors() -> Dict[str, Any]:
    return runner.list_anchors()


def _trajectories(limit: int = 20) -> Dict[str, Any]:
    return {"total": len(traj_mod.list_trajectories(limit=10**9)),
            "recent": traj_mod.list_trajectories(limit=limit)}


def _trajectory_by_id(wo_id: str) -> Optional[Dict[str, Any]]:
    return traj_mod.read_trajectory(wo_id)


def _run(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Run a work order from an inline dict or a JSON file path."""
    if "work_order" in payload and isinstance(payload["work_order"], dict):
        wo = payload["work_order"]
    elif "work_order_path" in payload:
        wo = runner.load_work_order(payload["work_order_path"])
    elif "preset" in payload:
        preset = payload["preset"]
        root = _rr()
        presets = {
            "w3-microbio-ecoli": "data/vlab/work-orders/w3-microbio-ecoli.json",
        }
        if preset not in presets:
            return {"error": f"unknown preset: {preset}",
                    "known": list(presets.keys())}
        wo = runner.load_work_order(str(root / presets[preset]))
    else:
        return {"error": "need one of: work_order (dict), work_order_path (str), "
                         "preset (str)"}

    job = runner.run_work_order(wo, record_trajectory=True)
    return job


def _submit_feedback(payload: Dict[str, Any]) -> Dict[str, Any]:
    fb = payload.get("feedback") or payload
    if not isinstance(fb, dict) or "fb_id" not in fb and "feedback_type" not in fb:
        return {"error": "payload must contain a feedback object with feedback_type"}
    remove = payload.get("remove_gate_ids")
    promote = payload.get("promote", False)
    try:
        return fb_mod.submit(fb, promote=promote, remove_gate_ids=remove)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def _audit() -> Dict[str, Any]:
    entries = fb_mod.audit_log(tail=50)
    return {"total": len(entries), "entries": entries}


def _run_checks() -> Dict[str, Any]:
    return runner.run_checks(verbose=False)


# ---------------------------------------------------------------------------
# Front-end HTML — single self-contained file
# ---------------------------------------------------------------------------

FRONTEND_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SwarmLabs Workbench</title>
<style>
  :root {
    --bg: #0f1419;
    --panel: #171c22;
    --panel-2: #1e252d;
    --border: #2d3640;
    --text: #e6edf3;
    --text-dim: #8b949e;
    --accent: #4fc3f7;
    --ok: #3fb950;
    --warn: #d29922;
    --err: #f85149;
    --mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
  }
  html, body { margin: 0; padding: 0; height: 100%; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
                 sans-serif, "Helvetica Neue", Arial, "PingFang SC",
                 "Microsoft YaHei", sans-serif;
    background: var(--bg); color: var(--text); font-size: 13px;
    line-height: 1.5; overflow: hidden;
  }
  header {
    padding: 10px 18px; border-bottom: 1px solid var(--border);
    display: flex; align-items: center; justify-content: space-between;
    background: var(--panel);
  }
  header .brand { font-weight: 600; letter-spacing: 0.4px; }
  header .brand b { color: var(--accent); }
  header .stats { color: var(--text-dim); font-size: 12px; }
  header .stats span { margin-left: 14px; }
  header .stats b { color: var(--text); font-weight: 500; }

  .layout {
    display: grid;
    grid-template-columns: 1fr 1.4fr 1fr;
    grid-template-rows: 1fr;
    height: calc(100vh - 44px);
  }
  .panel {
    display: flex; flex-direction: column;
    border-right: 1px solid var(--border);
    overflow: hidden;
  }
  .panel:last-child { border-right: 0; }
  .panel h2 {
    margin: 0; padding: 8px 14px; font-size: 11px;
    letter-spacing: 1.4px; text-transform: uppercase;
    color: var(--text-dim); border-bottom: 1px solid var(--border);
    background: var(--panel);
  }
  .panel-body {
    flex: 1; overflow-y: auto; padding: 10px 12px;
  }
  .panel-body::-webkit-scrollbar { width: 6px; }
  .panel-body::-webkit-scrollbar-thumb { background: #2d3640; border-radius: 3px; }

  /* Trajectory panel */
  .run-card {
    border: 1px solid var(--border); border-radius: 6px;
    padding: 8px 10px; margin-bottom: 8px;
    background: var(--panel-2); cursor: pointer;
    transition: border-color 0.15s;
  }
  .run-card:hover { border-color: var(--accent); }
  .run-card.active { border-color: var(--accent); background: #1e2831; }
  .run-card .row1 { display: flex; justify-content: space-between; gap: 8px; }
  .run-card .wo-id { font-family: var(--mono); font-size: 11.5px; color: var(--text); }
  .run-card .time { font-size: 11px; color: var(--text-dim); }
  .run-card .verdict {
    display: inline-block; margin-top: 4px; padding: 2px 6px;
    border-radius: 3px; font-size: 10.5px; font-weight: 600;
    letter-spacing: 0.5px;
  }
  .v-UNVALIDATED { background: #4a3a1a; color: #f0c419; }
  .v-PASS        { background: #1a4a2a; color: var(--ok); }
  .v-MARGINAL    { background: #4a3a1a; color: var(--warn); }
  .v-REFUTED     { background: #4a1a1a; color: var(--err); }
  .v-ERROR       { background: #4a1a1a; color: var(--err); }
  .badge { padding: 1px 6px; border-radius: 3px; font-size: 10.5px;
           font-weight: 600; margin-right: 6px; }
  .b-ok { background: rgba(63,185,80,0.15); color: var(--ok); }
  .b-err { background: rgba(248,81,73,0.15); color: var(--err); }

  /* Chat / command panel */
  .cmd-tabs { display: flex; gap: 2px; padding: 6px 8px 0; }
  .cmd-tabs button {
    flex: 1; background: transparent; border: 1px solid transparent;
    padding: 5px 8px; color: var(--text-dim); cursor: pointer;
    font-size: 11.5px; border-radius: 4px;
  }
  .cmd-tabs button.active { background: var(--panel-2); color: var(--text); border-color: var(--border); }
  .cmd-form {
    display: flex; flex-direction: column; gap: 8px;
    padding: 8px 12px; border-bottom: 1px solid var(--border);
  }
  .cmd-form label { font-size: 11px; color: var(--text-dim); letter-spacing: 0.3px; }
  .cmd-form select, .cmd-form input[type=text] {
    background: var(--panel); color: var(--text);
    border: 1px solid var(--border); padding: 5px 7px;
    border-radius: 3px; font-family: var(--mono); font-size: 12px;
  }
  .cmd-form textarea {
    background: var(--panel); color: var(--text);
    border: 1px solid var(--border); padding: 6px 8px;
    border-radius: 3px; font-family: var(--mono); font-size: 11.5px;
    resize: vertical; min-height: 60px;
  }
  .cmd-form button {
    align-self: flex-start;
    background: var(--accent); color: #0b1418; border: 0;
    padding: 6px 14px; border-radius: 4px; font-weight: 600;
    cursor: pointer; font-size: 12.5px;
  }
  .cmd-form button:disabled { opacity: 0.5; cursor: not-allowed; }
  .cmd-form button.danger { background: #f85149; color: white; }

  .log { padding: 4px 12px 12px; font-family: var(--mono); font-size: 11.5px; }
  .log .entry { margin-bottom: 6px; word-break: break-word; }
  .log .entry .t { color: var(--text-dim); margin-right: 6px; }
  .log .entry.ok .m { color: var(--ok); }
  .log .entry.err .m { color: var(--err); }
  .log .entry.run .m { color: var(--accent); }
  .log .entry pre {
    background: var(--panel); padding: 6px 8px; margin: 3px 0;
    border-radius: 3px; overflow-x: auto; font-size: 11px;
    max-height: 200px;
  }

  /* Compute panel */
  .kv { display: flex; justify-content: space-between; padding: 3px 0;
        border-bottom: 1px dashed var(--border); font-size: 12px; }
  .kv .k { color: var(--text-dim); }
  .kv .v { font-family: var(--mono); }
  .section-h { font-size: 11px; letter-spacing: 1.2px; color: var(--text-dim);
               text-transform: uppercase; margin: 10px 0 4px; }
  .stage-row { display: flex; align-items: center; gap: 8px; padding: 3px 0;
               font-size: 12px; }
  .stage-row .name { flex: 1; font-family: var(--mono); }
  .stage-row .time { color: var(--text-dim); font-size: 10.5px; }

  .empty { color: var(--text-dim); font-style: italic; padding: 20px 0;
           text-align: center; }
  details summary { cursor: pointer; color: var(--accent); }
</style>
</head>
<body>
<header>
  <div class="brand">Swarm<b>Labs</b> · Workbench <span style="color:var(--text-dim); font-weight:400; font-size:11px;">v0.1</span></div>
  <div class="stats" id="stats">
    <span>scenarios <b id="s-scen">—</b></span>
    <span>gates <b id="s-gates">—</b></span>
    <span>anchors <b id="s-anch">—</b></span>
    <span>runs <b id="s-runs">—</b></span>
  </div>
</header>
<div class="layout">

  <!-- Left: Trajectory -->
  <div class="panel">
    <h2>Trajectory</h2>
    <div class="panel-body" id="traj-body">
      <div class="empty">Loading…</div>
    </div>
  </div>

  <!-- Middle: Command + Log -->
  <div class="panel">
    <h2>Command</h2>
    <div class="cmd-tabs">
      <button id="tab-run" class="active">Run</button>
      <button id="tab-fb">Feedback</button>
      <button id="tab-check">Check</button>
    </div>
    <div id="form-run" class="cmd-form">
      <label>Preset</label>
      <select id="run-preset">
        <option value="w3-microbio-ecoli">w3-microbio-ecoli (microbio growth)</option>
      </select>
      <button id="btn-run">▶ Run pipeline</button>
    </div>
    <div id="form-fb" class="cmd-form" style="display:none;">
      <label>Feedback type</label>
      <select id="fb-type">
        <option value="gap">gap (missing capability)</option>
        <option value="false_positive">false_positive (wrong reject)</option>
        <option value="coverage">coverage (insufficient)</option>
        <option value="robustness">robustness (edge case)</option>
      </select>
      <label>Scenario</label>
      <input type="text" id="fb-scenario" value="micro_52">
      <label>Message</label>
      <textarea id="fb-msg" placeholder="Describe the observation and what the harness should learn."></textarea>
      <button id="btn-fb">Submit feedback</button>
    </div>
    <div id="form-check" class="cmd-form" style="display:none;">
      <label>Full gate battery</label>
      <button id="btn-check">▶ Run all checks</button>
    </div>

    <h2 style="border-top:1px solid var(--border);">Log</h2>
    <div class="panel-body">
      <div class="log" id="log">
        <div class="entry"><span class="t">—</span><span class="m" style="color:var(--text-dim)">Idle. Run a pipeline to begin.</span></div>
      </div>
    </div>
  </div>

  <!-- Right: Compute -->
  <div class="panel">
    <h2>Compute</h2>
    <div class="panel-body" id="compute-body">
      <div class="empty">Run a work order to see stage-by-stage output.</div>
    </div>
  </div>
</div>

<script>
// -------- state --------
const state = { activeWoId: null };

// -------- helpers --------
const $ = (id) => document.getElementById(id);
function ts() {
  const d = new Date();
  return d.toTimeString().slice(0, 8) + "." + String(d.getMilliseconds()).padStart(3, '0');
}
function el(tag, attrs, ...children) {
  const e = document.createElement(tag);
  if (attrs) for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') e.className = v;
    else if (k === 'style') e.style.cssText = v;
    else if (k === 'html') e.innerHTML = v;
    else e.setAttribute(k, v);
  }
  for (const c of children) {
    if (c == null) continue;
    e.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
  }
  return e;
}
function verdictClass(v) {
  return 'v-' + (v || 'UNVALIDATED');
}
function escapeHtml(s) {
  if (s == null) return '';
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

async function api(path, opts) {
  const r = await fetch(path, opts);
  const txt = await r.text();
  try { return JSON.parse(txt); } catch { return { _raw: txt, _status: r.status }; }
}

function log(kind, msg, payload) {
  const logEl = $('log');
  const e = el('div', { class: 'entry ' + kind });
  e.appendChild(el('span', { class: 't' }, ts()));
  e.appendChild(el('span', { class: 'm' }, msg));
  if (payload) {
    const pre = el('pre', null, JSON.stringify(payload, null, 2));
    e.appendChild(pre);
  }
  logEl.insertBefore(e, logEl.firstChild);
  while (logEl.childElementCount > 100) logEl.lastChild.remove();
}

// -------- trajectory panel --------
async function refreshTrajectories() {
  const d = await api('/api/trajectories');
  $('s-runs').textContent = d.total;
  const body = $('traj-body');
  body.innerHTML = '';
  if (!d.recent || !d.recent.length) {
    body.appendChild(el('div', { class: 'empty' }, 'No runs yet.'));
    return;
  }
  for (const run of d.recent) {
    const card = el('div', {
      class: 'run-card' + (state.activeWoId === run.work_order_id ? ' active' : ''),
      onclick: () => showTrajectory(run.work_order_id)
    });
    const row1 = el('div', { class: 'row1' });
    row1.appendChild(el('span', { class: 'wo-id' }, run.work_order_id));
    row1.appendChild(el('span', { class: 'time' }, (run.recorded_at || '').replace('T', ' ').replace('+00:00','')));
    card.appendChild(row1);
    const v = run.verdict || '—';
    const o = run.outcome || '—';
    const row2 = el('div', { style: 'margin-top:4px; display:flex; gap:6px; align-items:center;' });
    row2.appendChild(el('span', { class: 'verdict ' + verdictClass(v) }, v));
    row2.appendChild(el('span', { style: 'color:var(--text-dim); font-size:10.5px;' }, o));
    row2.appendChild(el('span', { style: 'margin-left:auto; font-size:10.5px; color:var(--text-dim);' },
                       (run.duration_ms || 0) + 'ms'));
    card.appendChild(row2);
    body.appendChild(card);
  }
}

async function showTrajectory(woId) {
  state.activeWoId = woId;
  refreshTrajectories();
  const t = await api('/api/trajectory/' + encodeURIComponent(woId));
  renderCompute(t);
  log('run', 'loaded trajectory: ' + woId);
}

// -------- compute panel --------
function renderCompute(job) {
  const body = $('compute-body');
  body.innerHTML = '';
  if (!job || job._raw !== undefined) {
    body.appendChild(el('div', { class: 'empty' }, job && job.error ? job.error : 'No compute data.'));
    return;
  }
  // Verdict
  const v = job.verdict || {};
  const head = el('div');
  head.appendChild(el('div', { class: 'kv' },
    el('span', { class: 'k' }, 'outcome'),
    el('span', { class: 'v' }, job.outcome || '—')
  ));
  head.appendChild(el('div', { class: 'kv' },
    el('span', { class: 'k' }, 'verdict'),
    el('span', { class: 'v', style: 'color:var(--accent)' }, v.hard_verdict || '—')
  ));
  head.appendChild(el('div', { class: 'kv' },
    el('span', { class: 'k' }, 'gate'),
    el('span', { class: 'v' }, v.gate || '—')
  ));
  if (v.reason) head.appendChild(el('div', { class: 'kv' },
    el('span', { class: 'k' }, 'reason'),
    el('span', { class: 'v', style: 'font-family:inherit; font-size:11px; max-width:70%; text-align:right;' },
       v.reason)
  ));
  body.appendChild(head);

  // Screening
  const s = job.screening || {};
  const sc = s.counts || {};
  if (Object.keys(sc).length) {
    body.appendChild(el('div', { class: 'section-h' }, 'Screening'));
    for (const [k, val] of Object.entries(sc)) {
      const cls = k === 'ACCEPTED' ? 'b-ok' : (k === 'REJECTED' ? 'b-err' : 'b-ok');
      body.appendChild(el('div', { class: 'kv' },
        el('span', { class: 'k' }, el('span', { class: 'badge ' + cls }, k)),
        el('span', { class: 'v' }, String(val))
      ));
    }
  }

  // Stages
  if (job.stages && job.stages.length) {
    body.appendChild(el('div', { class: 'section-h' }, 'Pipeline stages'));
    for (const st of job.stages) {
      const row = el('div', { class: 'stage-row' });
      const mark = st.ok ? '✓' : '✗';
      const cls = st.ok ? 'color:var(--ok)' : 'color:var(--err)';
      row.appendChild(el('span', { style: cls, 'font-family':'var(--mono)' }, mark));
      row.appendChild(el('span', { class: 'name' }, st.stage));
      const t = st.at_utc ? st.at_utc.replace('T', ' ').replace('+00:00','') : '';
      row.appendChild(el('span', { class: 'time' }, t));
      body.appendChild(row);
      if (st.errors && st.errors.length) {
        body.appendChild(el('pre', { style: 'background:rgba(248,81,73,0.08); padding:4px 8px; margin:0 0 4px 20px; border-radius:3px; font-size:10.5px; color:var(--err);' },
          st.errors.join('\n')));
      }
    }
  }

  // Work order summary
  if (job.work_order_summary) {
    body.appendChild(el('div', { class: 'section-h' }, 'Work order summary'));
    const wrap = el('details');
    wrap.appendChild(el('summary', null, 'show'));
    wrap.appendChild(el('pre', { style: 'font-size:10.5px; background:var(--panel); padding:6px; border-radius:3px;' },
      JSON.stringify(job.work_order_summary, null, 2)));
    body.appendChild(wrap);
  }
}

// -------- command handlers --------
function setTab(name) {
  ['run','fb','check'].forEach(t => {
    $('tab-'+t).classList.toggle('active', t === name);
    $('form-'+t).style.display = (t === name) ? 'flex' : 'none';
  });
}
$('tab-run').onclick = () => setTab('run');
$('tab-fb').onclick  = () => setTab('fb');
$('tab-check').onclick = () => setTab('check');

$('btn-run').onclick = async (ev) => {
  const btn = ev.currentTarget;
  btn.disabled = true;
  log('run', 'running preset=' + $('run-preset').value + ' ...');
  const t0 = performance.now();
  try {
    const r = await api('/api/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ preset: $('run-preset').value })
    });
    const dt = Math.round(performance.now() - t0);
    if (r.error) {
      log('err', 'run failed: ' + r.error, r);
    } else {
      log('run', 'run ok in ' + dt + 'ms — outcome=' + (r.outcome || '?') + ' verdict=' + ((r.verdict||{}).hard_verdict||'?'));
      renderCompute(r);
      await refreshTrajectories();
    }
  } catch (e) {
    log('err', 'network error: ' + e);
  } finally {
    btn.disabled = false;
  }
};

$('btn-fb').onclick = async (ev) => {
  const msg = $('fb-msg').value.trim();
  if (!msg) { log('err', 'feedback message required'); return; }
  const btn = ev.currentTarget;
  btn.disabled = true;
  log('run', 'submitting feedback ...');
  try {
    const r = await api('/api/feedback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        feedback: {
          feedback_type: $('fb-type').value,
          scenario_id: $('fb-scenario').value,
          message: msg
        }
      })
    });
    if (r.error) log('err', 'feedback failed: ' + r.error, r);
    else {
      const st = r.state || {};
      log(r.accepted ? 'ok' : 'err',
        'feedback accepted=' + r.accepted + ' reason=' + ((r.decision||{}).reason||'') +
        ' v=' + (st.current_version||'?') + ' score=' + (st.current_score||0).toFixed(4));
      $('fb-msg').value = '';
    }
  } catch (e) { log('err', 'network: ' + e); }
  finally { btn.disabled = false; }
};

$('btn-check').onclick = async (ev) => {
  const btn = ev.currentTarget;
  btn.disabled = true;
  log('run', 'running check battery ...');
  try {
    const r = await api('/api/check', { method: 'POST' });
    log(r.ok ? 'ok' : 'err',
        'checks: ' + (r.checks_passed||0) + '/' + (r.checks_run||0) + ' passed');
    renderCheck(r);
  } catch (e) { log('err', 'network: ' + e); }
  finally { btn.disabled = false; }
};

function renderCheck(r) {
  const body = $('compute-body');
  body.innerHTML = '';
  body.appendChild(el('div', { class: 'section-h' }, 'Check battery'));
  for (const c of (r.results || [])) {
    body.appendChild(el('div', { class: 'kv' },
      el('span', { class: 'k' },
         el('span', { class: 'badge ' + (c.ok ? 'b-ok' : 'b-err') },
            c.ok ? 'PASS' : 'FAIL'),
         ' ' + c.check),
      el('span', { class: 'v' }, 'rc=' + c.returncode)
    ));
  }
}

// -------- startup --------
(async function init() {
  const [d, s, g, a] = await Promise.all([
    api('/api/doctor'),
    api('/api/scenarios'),
    api('/api/gates'),
    api('/api/anchors')
  ]);
  $('s-scen').textContent  = (s.registry_total || 0) + '/' + (s.trainable || 0);
  $('s-gates').textContent = (g.count || 0);
  $('s-anch').textContent  = (a.total || 0);
  log('ok', 'doctor: python ' + (d.python||'?') + ' rdkit=' + (d.rdkit||'?'));
  await refreshTrajectories();
})();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class WorkbenchHandler(BaseHTTPRequestHandler):
    """Routes requests to handler functions."""

    server_version = "SwarmLabs-Workbench/0.1"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        # Keep the console clean; only log non-200s.
        code = args[1] if len(args) >= 2 else "?"
        if not str(code).startswith("2"):
            sys.stderr.write("[workbench] %s %s\n" % (self.address_string(), fmt % args))

    def _json(self, status: int, obj: Any) -> None:
        body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _html(self, status: int, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        try:
            if path == "/" or path == "/index.html":
                self._html(200, FRONTEND_HTML)
            elif path == "/api/doctor":
                self._json(200, _doctor())
            elif path == "/api/scenarios":
                self._json(200, _scenarios())
            elif path == "/api/gates":
                self._json(200, _gates())
            elif path == "/api/anchors":
                self._json(200, _anchors())
            elif path == "/api/trajectories":
                self._json(200, _trajectories())
            elif path.startswith("/api/trajectory/"):
                wo_id = unquote(path[len("/api/trajectory/"):])
                t = _trajectory_by_id(wo_id)
                if t is None:
                    self._json(404, {"error": f"no trajectory for {wo_id}"})
                else:
                    self._json(200, t)
            elif path == "/api/feedback/audit":
                self._json(200, _audit())
            elif path == "/healthz":
                self._json(200, {"ok": True})
            else:
                self._json(404, {"error": "unknown route", "path": path})
        except Exception as e:
            self._json(500, {"error": f"{type(e).__name__}: {e}",
                             "traceback": traceback.format_exc()[-800:]})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length > 0 else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8"))
            except Exception:
                payload = {}

            if path == "/api/run":
                result = _run(payload)
                self._json(200, result)
            elif path == "/api/feedback":
                result = _submit_feedback(payload)
                self._json(200, result)
            elif path == "/api/check":
                result = _run_checks()
                self._json(200, result)
            else:
                self._json(404, {"error": "unknown POST route", "path": path})
        except Exception as e:
            self._json(500, {"error": f"{type(e).__name__}: {e}",
                             "traceback": traceback.format_exc()[-800:]})


# ---------------------------------------------------------------------------
# Server runner
# ---------------------------------------------------------------------------

def run(host: str = "127.0.0.1",
        port: int = 8788,
        open_browser: bool = False,
        quiet: bool = False) -> int:
    """Start the Workbench server. Blocks until interrupted."""
    import os
    import threading

    try:
        httpd = ThreadingHTTPServer((host, port), WorkbenchHandler)
    except OSError as e:
        sys.stderr.write(f"error: cannot bind {host}:{port}: {e}\n")
        return 1

    url = f"http://{host}:{port}/"
    if not quiet:
        sys.stderr.write(f"SwarmLabs Workbench listening on {url}\n")
        sys.stderr.write(f"  API endpoints: /api/doctor, /api/scenarios, /api/gates,\n"
                         f"                 /api/anchors, /api/trajectories, /api/check\n")
        sys.stderr.write(f"  POST: /api/run, /api/feedback\n")
        sys.stderr.write(f"  Press Ctrl+C to stop\n")

    if open_browser:
        try:
            if sys.platform.startswith("win"):
                os.system(f'start "{url}"')
            elif sys.platform == "darwin":
                os.system(f"open {url}")
            else:
                os.system(f"xdg-open {url}")
        except Exception:
            pass  # noqa: silent-ok — Exception 可安全忽略（已审计 2026-10-08）

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("\nshutting down\n")
    finally:
        httpd.server_close()
    return 0
