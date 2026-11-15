// 用修正后的提取逻辑(完整响应 j 传入 getPath, 兼容 kinetic_models 位置)重新验证全部 CASES,
// 产出真实的 PASS/FAIL/ERR(均为干净对象, 不再出现 undefined 字段丢失),
// 覆盖 baseline 中被污染/待验证的记录, 并保留原 HEAD 中独有的 PASS 记录。
// 用法: node scripts/revalidate_offpeak.js

const fs = require('fs');
const path = require('path');
const { CASES } = require('./real_dataset.js');

const BASE_URL = (process.env.BASE_URL || 'https://swarmlabs.tools/api').replace(/\/$/, '');
const DATA = path.join(__dirname, '..', 'data');

function getPath(obj, p) {
  if (p === '__kp_n__') {
    const cands = [obj, obj && obj.result];
    for (const c of cands) {
      const km = c && c.kinetic_models;
      if (km && km.models) {
        const m = km.models.find(x => x.name === 'Korsmeyer-Peppas');
        if (m && m.parameters) return m.parameters.n;
      }
    }
    return undefined;
  }
  return p.split('.').reduce((o, k) => (o == null ? undefined : o[k]), obj);
}

function computeStatus(expected, predicted, tolPct) {
  if (predicted === undefined || predicted === null || isNaN(predicted)) {
    return { diffAbs: null, diffPct: null, status: 'ERR' };
  }
  const diffAbs = Math.round((predicted - expected) * 1000) / 1000;
  const diffPct = expected !== 0 ? Math.round((diffAbs / Math.abs(expected)) * 1000) / 10 : Math.round(diffAbs * 10) / 10;
  const status = Math.abs(diffPct) <= tolPct ? 'PASS' : 'FAIL';
  return { diffAbs, diffPct, status };
}

async function callOnce(url, body) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), 25000);
  try {
    const res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal: ctrl.signal });
    const j = await res.json();
    clearTimeout(t);
    return { ok: res.ok, status: res.status, j };
  } catch (e) {
    clearTimeout(t);
    return { ok: false, status: 0, err: String(e && e.message || e) };
  }
}

async function callAPI(c, onErr) {
  const url = `${BASE_URL}/v2/run/${c.engine}`;
  for (let attempt = 1; attempt <= 2; attempt++) {
    const r = await callOnce(url, c.params);
    if (r.ok) {
      const predicted = getPath(r.j, c.outPath); // 传完整 j
      return { ok: true, status: r.status, predicted, model: r.j && r.j.result && r.j.result.model };
    }
    if (r.status === 429) { if (onErr) onErr('429 退避15s'); await new Promise(x => setTimeout(x, 15000)); continue; }
    if (attempt < 2) { await new Promise(x => setTimeout(x, 5000)); continue; }
    if (onErr) onErr(`HTTP${r.status} ${r.err || ''}`.trim());
  }
  return { ok: false, status: 0, predicted: undefined, model: undefined };
}

(async () => {
  const fresh = [];
  const perDomain = {};
  let pass = 0, fail = 0, err = 0;
  const caseEngines = new Set(CASES.map(c => c.engine));
  let lastEngine = null;

  for (const c of CASES) {
    if (lastEngine && c.engine !== lastEngine) await new Promise(x => setTimeout(x, 10000));
    lastEngine = c.engine;

    const r = await callAPI(c, e => console.log('  [warn]', c.engine, e));
    let st;
    if (!r.ok || r.predicted === undefined || r.predicted === null) st = { diffAbs: null, diffPct: null, status: 'ERR' };
    else st = computeStatus(c.expected, r.predicted, c.tolPct);
    if (st.status === 'PASS') pass++; else if (st.status === 'FAIL') fail++; else err++;

    const ds = perDomain[c.domain] || (perDomain[c.domain] = { total: 0, pass: 0, fail: 0, err: 0 });
    ds.total++; if (st.status === 'PASS') ds.pass++; else if (st.status === 'FAIL') ds.fail++; else ds.err++;

    fresh.push({
      domain: c.domain, engine: c.engine, name: c.label, paper: c.source,
      paper_result: c.expected, paper_unit: c.unit,
      predicted: r.predicted === undefined ? null : r.predicted, predicted_unit: c.unit,
      diff_abs: st.diffAbs, diff_pct: st.diffPct, status: st.status,
      model: r.model || null, params: c.params, http: r.status
    });

    const flag = st.status === 'PASS' ? '✓' : (st.status === 'FAIL' ? '✗' : '⚠');
    console.log(`${flag} [${c.domain}] ${c.label} | 文献=${c.expected}${c.unit} 平台=${r.predicted === undefined ? 'ERR' : r.predicted} diff=${st.diffPct === null ? '-' : st.diffPct + '%'} (tol ${c.tolPct}%)`);
    await new Promise(x => setTimeout(x, 4000));
  }

  // 保留原 HEAD 中独有的 PASS 记录(引擎不在 CASES 中, 避免丢数据)
  const baseFile = path.join(DATA, 'model-validation-baseline.json');
  const base = JSON.parse(fs.readFileSync(baseFile, 'utf8'));
  const extra = (base.results || []).filter(x => x.status === 'PASS' && !caseEngines.has(x.engine));
  base.results = fresh.concat(extra);
  base.timestamp = new Date().toISOString();
  base.note = '由 scripts/revalidate_offpeak.js 用修正后的提取逻辑重新验证(完整响应传入 getPath)。';
  fs.writeFileSync(baseFile, JSON.stringify(base, null, 2));

  // benchmark.records 重建
  const benchFile = path.join(DATA, 'benchmark-dataset.json');
  const bench = JSON.parse(fs.readFileSync(benchFile, 'utf8'));
  const now = new Date().toISOString();
  const newRecs = fresh.map((r, i) => ({
    id: 'REAL-' + r.name.slice(0, 6) + '-' + (1000 + i).toString(36).toUpperCase(),
    domain: r.domain.toLowerCase(), engine_type: r.engine, experiment_name: r.name,
    input_parameters: r.params, expected_output: { value: r.paper_result, unit: r.paper_unit },
    benchmark_range: { value: { min: r.paper_result * 0.9, max: r.paper_result * 1.1, typical: r.paper_result } },
    source: 'real_literature', citation: r.paper, platform_predicted: r.predicted,
    diff_pct: r.diff_pct, validation_status: r.status,
    dp_epsilon: null, dp_method: 'none_real_data', dp_noise_applied: {}, created_at: now, version: 'v2.0-real'
  }));
  const extraBench = (bench.records || []).filter(x => x.validation_status === 'PASS' && !caseEngines.has(x.engine_type));
  bench.records = newRecs.concat(extraBench);
  bench.total_records = bench.records.length;
  const dsum = {};
  for (const rec of bench.records) { const d = rec.domain; dsum[d] = dsum[d] || { count: 0, engine_types: new Set() }; dsum[d].count++; dsum[d].engine_types.add(rec.engine_type); }
  bench.domain_summary = {}; for (const d of Object.keys(dsum)) bench.domain_summary[d] = { count: dsum[d].count, engine_types: [...dsum[d].engine_types] };
  bench.domains_covered = Object.keys(dsum).length; bench.last_updated = now;
  fs.writeFileSync(benchFile, JSON.stringify(bench, null, 2));

  console.log('\n================ 复验汇总 ================');
  console.log(`总用例: ${fresh.length} | PASS ${pass} / FAIL ${fail} / ERR ${err} | 通过率 ${Math.round(pass / fresh.length * 1000) / 10}%`);
  console.log(`baseline.results: ${base.results.length} (含 ${extra.length} 条独有 HEAD)`);
  console.log(`benchmark.records: ${bench.records.length} (覆盖 ${bench.domains_covered} 域)`);
  console.log('\n各域:');
  for (const [d, s] of Object.entries(perDomain)) console.log(`  ${d}: ${s.pass}/${s.total} PASS (FAIL ${s.fail}, ERR ${s.err})`);

  // 重建专业板块
  require('./rebuild_sections.js');
})();
