// Corrected 3-journey QA walk. Uses the REAL mounted prefix /api/ and correct
// engine/scenario namespaces (v2 ENGINE_CATEGORIES vs v3 __V3_ALL).
const BASE = 'https://swarmlabs.tools';

async function call(method, path, { token, body } = {}) {
  const headers = { 'Content-Type': 'application/json' };
  if (token) headers['Authorization'] = 'Bearer ' + token;
  const init = { method, headers };
  if (body !== undefined) init.body = JSON.stringify(body);
  try {
    const r = await fetch(BASE + path, init);
    const ct = r.headers.get('content-type') || '';
    const text = await r.text();
    let json = null;
    if (ct.includes('application/json')) { try { json = JSON.parse(text); } catch {} }
    return { status: r.status, ct, json, text: text.slice(0, 300) };
  } catch (e) {
    return { status: 'ERR', ct: '', json: null, text: String(e) };
  }
}

const sum = (o) => {
  // produce a short, stable summary of a JSON response
  if (o === null) return '(null)';
  if (typeof o === 'object') {
    const keys = Object.keys(o).slice(0, 12);
    return '{' + keys.map(k => {
      const v = o[k];
      if (Array.isArray(v)) return k + ':[' + v.length + ']';
      if (typeof v === 'object' && v) return k + ':{…}';
      return k + ':' + JSON.stringify(v).slice(0, 40);
    }).join(', ') + '}';
  }
  return JSON.stringify(o).slice(0, 80);
};

const W = [];
function log(journey, step, res) {
  const ok = res.status === 200 && res.ct.includes('application/json');
  W.push(`  [${ok ? 'OK ' : 'FAIL'}] ${journey} / ${step} -> HTTP ${res.status} ${res.ct}`);
  W.push(`        ${sum(res.json ?? res.text)}`);
  return ok;
}

async function main() {
  const ts = Date.now();
  const email = `qa_walk_${ts}@swarmlabs.test`;
  // ---- register ----
  const reg = await call('POST', '/api/v2/auth/register', { body: { email, password: 'qawalk123' } });
  log('auth', 'register', reg);
  const token = reg.json && reg.json.token;
  W.push(`        token=${token ? token.slice(0, 12) + '…' : '(none)'}`);

  // ===== Journey A: v2 chemistry (suzuki) =====
  W.push('\n=== Journey A: v2 chemistry / suzuki ===');
  log('A', 'literature', await call('GET', '/api/v2/literature/suzuki'));
  log('A', 'run (Bearer)', await call('POST', '/api/v2/run/suzuki', { token, body: { temperature_C: 80, catalyst_loading: 2.0, time_h: 12 } }));
  log('A', 'validate', await call('GET', '/api/v2/validate/suzuki'));

  // ===== Journey B: v2 biology (microbial_growth) =====
  W.push('\n=== Journey B: v2 biology / microbial_growth ===');
  log('B', 'literature', await call('GET', '/api/v2/literature/microbial_growth'));
  log('B', 'run (Bearer)', await call('POST', '/api/v2/run/microbial_growth', { token, body: { temperature_C: 37, concentration: 5, time_h: 10, pH: 7 } }));
  log('B', 'sweep', await call('POST', '/api/v2/sweep', { token, body: { engine: 'microbial_growth', param_combos: [{ temperature_C: 30, concentration: 2, time_h: 5, pH: 6 }, { temperature_C: 37, concentration: 5, time_h: 10, pH: 7 }] } }));
  log('B', 'validate', await call('GET', '/api/v2/validate/microbial_growth'));

  // ===== Journey C: v3 microbiology (micro_ecoli_glucose, has evaluator) =====
  W.push('\n=== Journey C: v3 microbiology / micro_ecoli_glucose (has ground-truth evaluator) ===');
  const scen = await call('GET', '/api/v3/scenarios');
  log('C', 'scenarios(list)', scen);
  const allN = scen.json && scen.json.scenarios ? scen.json.scenarios.length : 0;
  W.push(`        v3 scenarios listed: ${allN}  (52 micro_* have server evaluators; 11 cross-domain do NOT)`);
  const samp = await call('POST', '/api/v3/sample', { body: { scenario: 'micro_ecoli_glucose', n: 12, seed: 7 } });
  log('C', 'sample(n=12)', samp);
  if (samp.json && samp.json.points && samp.json.points.length) {
    const pts = samp.json.points;
    const kk = Math.max(2, Math.floor(pts.length * 0.7));
    const train = pts.slice(0, kk), query = pts.slice(kk);
    const train_x = train.map(p => p.x), train_y = train.map(p => p.y);
    const query_x = query.map(p => p.x);
    const pred = await call('POST', '/api/v3/predict', { body: { train_x, train_y, query_x, noise_rel: 0.03 } });
    log('C', 'predict(GP+UQ)', pred);
    if (pred.json) {
      const m = pred.json.mean, s = pred.json.std;
      W.push(`        GP query[0]: mean=${m && m[0] != null ? (+m[0]).toFixed(4) : '?'} ± std=${s && s[0] != null ? (+s[0]).toFixed(4) : '?'}  (UQ demonstrated)`);
    }
    const guard = await call('POST', '/api/v3/guard', { body: { scenario: 'micro_ecoli_glucose', train_x, train_y, query_x, predicted: pred.json } });
    log('C', 'guard', guard);
  } else {
    W.push('        (sample returned no points — cannot run predict/guard)');
  }
  // documented gap: cross-domain scenarios lack evaluators -> 501
  const gap = await call('POST', '/api/v3/sample', { body: { scenario: 'bio_logistic', n: 8 } });
  log('C', 'sample(bio_logistic) [gap: no evaluator -> 501]', gap);

  // ===== cross-check: the prior "wrong-prefix" probes should be HTML =====
  W.push('\n=== Control: wrong-prefix routes (no /api) expected HTML SPA fallback ===');
  log('ctrl', 'GET /v2/health (no /api)', await call('GET', '/v2/health'));
  log('ctrl', 'GET /v2/validate/suzuki (no /api)', await call('GET', '/v2/validate/suzuki'));

  console.log(W.join('\n'));
}
main().catch(e => { console.error('FATAL', e); process.exit(1); });
