// 真实文献数据 → 生产平台验证 + 入库扩张
// 读取 scripts/real_dataset.js 的 CASES, 逐条打 swarmlabs.tools API,
// 比对平台预测 vs 文献实测值, 计算误差/状态, 持久化到:
//   data/model-validation-baseline.json (results: 论文引用式验证)
//   data/benchmark-dataset.json          (records: 真实来源基准记录)
// 并写出 reports/real-expansion-<date>.json 汇总(含各域通过率/模型偏差).
// 用法: BASE_URL=https://swarmlabs.tools/api node scripts/expand_real_data.js

const fs = require('fs');
const path = require('path');
const { CASES } = require('./real_dataset.js');

const BASE_URL = (process.env.BASE_URL || 'https://swarmlabs.tools/api').replace(/\/$/, '');
const DATA = path.join(__dirname, '..', 'data');
const REPORTS = path.join(__dirname, '..', 'reports');

function getPath(obj, p) {
  if (p === '__kp_n__') {
    // 兼容 kinetic_models 在完整响应 j 或 j.result 两种位置
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

async function callOnce(url, body) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), 25000);
  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-cache' },
      body: JSON.stringify(body),
      signal: ctrl.signal
    });
    const j = await res.json();
    clearTimeout(t);
    return { ok: res.ok, status: res.status, j };
  } catch (e) {
    clearTimeout(t);
    return { ok: false, status: 0, err: String(e && e.message || e) + (e && e.cause ? ' | ' + e.cause.message : '') };
  }
}

async function callAPI(c, onErr) {
  const url = `${BASE_URL}/v2/run/${c.engine}`;
  // 冷启动为主因: 每个引擎首次命中冷 Worker 会 503/超时。最多2次尝试(第二次等5s焐热)。
  // 429 限流则等15s后退避1次, 不再即时轰炸。
  // 注意: outPath 相对完整响应 j 设计(如 result.result.drag_coefficient), 故必须传完整 r.j。
  for (let attempt = 1; attempt <= 2; attempt++) {
    const r = await callOnce(url, c.params);
    if (r.ok) {
      const result = r.j; // 传完整响应, 与 outPath 深度一致
      const predicted = getPath(result, c.outPath);
      return { ok: true, status: r.status, predicted, raw: result, model: result && result.result && result.result.model };
    }
    if (r.status === 429) {
      if (onErr) onErr('HTTP429 限流, 退避15s');
      await new Promise(res => setTimeout(res, 15000));
      continue;
    }
    if (attempt < 2) { await new Promise(res => setTimeout(res, 5000)); continue; }
    if (onErr) onErr(`HTTP${r.status} ${r.err || ''}`.trim());
  }
  return { ok: false, status: 0, predicted: undefined, raw: { error: 'all retries failed' }, model: undefined };
}

// 预热: 每个引擎先打一次(丢弃结果, 3s 间隔), 把冷 Worker 焐热, 避免批量首次命中冷实例失败
async function warmUp(engines) {
  console.log(`[warmup] 预热 ${engines.length} 个引擎(3s 间隔)...`);
  for (const eng of engines) {
    const probe = { reynolds_number: 100, time_h: 50, inlet_velocity: 1, viscosity: 0.01, mesh_size: 0.01,
      youngs_modulus: 210000, load_N: 1000, poisson_ratio: 0.3, density: 7850, length_m: 1,
      temperature_C: 25, thermal_conductivity: 50, heat_flux: 1000, ambient_temp_C: 25,
      cycle_count: 100, current_density: 1, voltage_V: 3.7, discharge_rate: 1,
      time_points: [30, 60, 120], release_pct: [20, 40, 60], drug_load_mg: 100,
      concentration: 5, pressure_bar: 4, voltage_V: 2.7, pressure_MPa: 2, pressure_atm: 1, time_h: 1 };
    try { await fetch(`${BASE_URL}/v2/run/${eng}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(probe), signal: AbortSignal.timeout(20000) }); }
    catch (_) {}
    await new Promise(r => setTimeout(r, 3000));
  }
  console.log('[warmup] 完成');
}

// 始终返回对象, 避免上游把 'ERR' 字符串当对象读取导致字段被 JSON.stringify 丢弃
function computeStatus(expected, predicted, tolPct) {
  if (predicted === undefined || predicted === null || isNaN(predicted)) {
    return { diffAbs: undefined, diffPct: undefined, status: 'ERR' };
  }
  const diffAbs = Math.round((predicted - expected) * 1000) / 1000;
  const diffPct = expected !== 0 ? Math.round((diffAbs / Math.abs(expected)) * 1000) / 10 : Math.round(diffAbs * 10) / 10;
  const status = Math.abs(diffPct) <= tolPct ? 'PASS' : 'FAIL';
  return { diffAbs, diffPct, status };
}

(async () => {
  const results = [];
  const domainStats = {};
  let pass = 0, fail = 0, err = 0;

  let lastEngine = null;
  for (const c of CASES) {
    // 切引擎时多歇 10s, 让平台从上一个引擎的负载中恢复, 避免批量突发打进降频状态
    if (lastEngine && c.engine !== lastEngine) { await new Promise(res => setTimeout(res, 10000)); }
    lastEngine = c.engine;

    const r = await callAPI(c, (e) => { if (!global.__firstErr) { global.__firstErr = true; console.log('  [debug] 首次调用真实错误:', e); } });
    // 平台返回空/降频(取不到预测值) → 明确标记 ERR 对象, 不再回退成字符串
    let st;
    if (!r.ok || r.predicted === undefined || r.predicted === null) {
      st = { diffAbs: null, diffPct: null, status: 'ERR' };
    } else {
      st = computeStatus(c.expected, r.predicted, c.tolPct);
    }
    if (st.status === 'PASS') pass++; else if (st.status === 'FAIL') fail++; else err++;

    const ds = domainStats[c.domain] || (domainStats[c.domain] = { total: 0, pass: 0, fail: 0, err: 0 });
    ds.total++; if (st.status === 'PASS') ds.pass++; else if (st.status === 'FAIL') ds.fail++; else ds.err++;

    const rec = {
      domain: c.domain, engine: c.engine, name: c.label, paper: c.source,
      paper_result: c.expected, paper_unit: c.unit,
      predicted: r.predicted, predicted_unit: c.unit,
      diff_abs: st.diffAbs, diff_pct: st.diffPct, status: st.status,
      model: r.model || null, params: c.params, http: r.status
    };
    results.push(rec);

    const flag = st.status === 'PASS' ? '✓' : (st.status === 'FAIL' ? '✗' : '⚠');
    console.log(`${flag} [${c.domain}] ${c.label} | 文献=${c.expected}${c.unit} 平台=${r.predicted === undefined ? 'ERR' : r.predicted} diff=${st.diffPct === null ? '-' : st.diffPct + '%'} (tol ${c.tolPct}%)`);
    await new Promise(res => setTimeout(res, 3000)); // 限速: 单请求间隔3s
  }

  // 持久化: model-validation-baseline.json
  const baseFile = path.join(DATA, 'model-validation-baseline.json');
  const base = JSON.parse(fs.readFileSync(baseFile, 'utf8'));
  const prevCount = (base.results || []).length;
  base.results = (base.results || []).concat(results.map(r => ({
    domain: r.domain, engine: r.engine, name: r.name, paper: r.paper,
    paper_result: r.paper_result, paper_unit: r.paper_unit,
    predicted: r.predicted, predicted_unit: r.predicted_unit,
    diff_abs: r.diff_abs, diff_pct: r.diff_pct, status: r.status,
    model: r.model, params: r.params
  })));
  base.timestamp = new Date().toISOString();
  fs.writeFileSync(baseFile, JSON.stringify(base, null, 2));

  // 持久化: benchmark-dataset.json (真实来源记录)
  const benchFile = path.join(DATA, 'benchmark-dataset.json');
  const bench = JSON.parse(fs.readFileSync(benchFile, 'utf8'));
  const now = new Date().toISOString();
  const newRecs = results.map(r => ({
    id: 'REAL-' + r.name.slice(0, 6) + '-' + Math.random().toString(36).slice(2, 7).toUpperCase(),
    domain: r.domain.toLowerCase(),
    engine_type: r.engine,
    experiment_name: r.name,
    input_parameters: r.params,
    expected_output: { value: r.paper_result, unit: r.paper_unit },
    benchmark_range: { value: { min: r.paper_result * 0.9, max: r.paper_result * 1.1, typical: r.paper_result } },
    source: 'real_literature',
    citation: r.paper,
    platform_predicted: r.predicted,
    diff_pct: r.diff_pct,
    validation_status: r.status,
    dp_epsilon: null, dp_method: 'none_real_data', dp_noise_applied: {},
    created_at: now, version: 'v2.0-real'
  }));
  bench.records = (bench.records || []).concat(newRecs);
  bench.total_records = bench.records.length;
  // 更新 domain_summary
  const dsum = {};
  for (const rec of bench.records) {
    const d = rec.domain;
    dsum[d] = dsum[d] || { count: 0, engine_types: new Set() };
    dsum[d].count++;
    dsum[d].engine_types.add(rec.engine_type);
  }
  bench.domain_summary = {};
  for (const d of Object.keys(dsum)) bench.domain_summary[d] = { count: dsum[d].count, engine_types: [...dsum[d].engine_types] };
  bench.domains_covered = Object.keys(dsum).length;
  bench.last_updated = now;
  fs.writeFileSync(benchFile, JSON.stringify(bench, null, 2));

  // 汇总报告
  const summary = {
    generated: now, base_url: BASE_URL,
    total_cases: results.length, pass, fail, err,
    pass_rate_pct: Math.round((pass / results.length) * 1000) / 10,
    prev_baseline_count: prevCount, new_baseline_count: base.results.length,
    benchmark_total_records: bench.total_records, benchmark_domains: bench.domains_covered,
    per_domain: domainStats,
    details: results
  };
  fs.mkdirSync(REPORTS, { recursive: true });
  const repFile = path.join(REPORTS, `real-expansion-${now.slice(0, 10)}.json`);
  fs.writeFileSync(repFile, JSON.stringify(summary, null, 2));

  console.log('\n================ 汇总 ================');
  console.log(`总用例: ${results.length} | PASS ${pass} / FAIL ${fail} / ERR ${err} | 通过率 ${summary.pass_rate_pct}%`);
  console.log(`baseline.results: ${prevCount} → ${base.results.length}`);
  console.log(`benchmark.records: ${bench.total_records} (覆盖 ${bench.domains_covered} 域)`);
  console.log('\n各域:');
  for (const [d, s] of Object.entries(domainStats)) {
    console.log(`  ${d}: ${s.pass}/${s.total} PASS (FAIL ${s.fail}, ERR ${s.err})`);
  }
  console.log(`\n报告: ${repFile}`);
})();
