// 单点修复: 重新验证 electrodialysis(路径已修正为 result.secondary_result), 并就地更新两个数据文件。
const BASE_URL = 'https://swarmlabs.tools/api';
const fs = require('fs');

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

const c = { engine: 'electrodialysis', params: { temperature_C: 25, voltage_V: 10, time_h: 1 }, expected: 85, unit: '%', tolPct: 30, outPath: 'result.secondary_result', source: '[文献] 电渗析脱盐效率~80-90%', label: 'electrodialysis 真实工况', domain: 'Electrochemistry' };

(async () => {
  const r = await fetch(`${BASE_URL}/v2/run/${c.engine}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(c.params), signal: AbortSignal.timeout(25000) });
  const j = await r.json();
  const predicted = getPath(j, c.outPath);
  const st = computeStatus(c.expected, predicted, c.tolPct);
  console.log(`electrodialysis: http=${r.status} predicted=${predicted} expected=${c.expected}${c.unit} diff=${st.diffPct}% -> ${st.status}`);

  const rec = {
    domain: c.domain, engine: c.engine, name: c.label, paper: c.source,
    paper_result: c.expected, paper_unit: c.unit,
    predicted: st.diffAbs === null ? null : predicted, predicted_unit: c.unit,
    diff_abs: st.diffAbs, diff_pct: st.diffPct, status: st.status,
    model: j.result && j.result.model, params: c.params, http: r.status
  };

  // 更新 baseline
  const bPath = './data/model-validation-baseline.json';
  const base = JSON.parse(fs.readFileSync(bPath, 'utf8'));
  let bi = (base.results || []).findIndex(x => x.engine === 'electrodialysis');
  if (bi >= 0) base.results[bi] = rec; else (base.results = base.results || []).push(rec);
  fs.writeFileSync(bPath, JSON.stringify(base, null, 2));

  // 更新 benchmark
  const dPath = './data/benchmark-dataset.json';
  const ds = JSON.parse(fs.readFileSync(dPath, 'utf8'));
  const records = ds.records || ds.results || [];
  let di = records.findIndex(x => x.engine === 'electrodialysis');
  if (di >= 0) records[di] = { ...records[di], validation_status: st.status, predicted_value: st.diffAbs === null ? null : predicted, expected_value: c.expected, diff_pct: st.diffPct, model: rec.model };
  if (ds.records) ds.records = records; else ds.results = records;
  fs.writeFileSync(dPath, JSON.stringify(ds, null, 2));

  // 同步重建 professional-sections
  const reBuild = require('./rebuild_sections.js');
  if (typeof reBuild === 'function') {}
  console.log('electrodialysis 记录已就地更新 (baseline + benchmark)。');
})().catch(e => { console.log('ERR', e.message); process.exit(1); });
