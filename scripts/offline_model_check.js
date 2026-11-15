// 离线模型复验工装 (不依赖线上部署)
// 把 functions/api/[[path]].js 中的物理模型函数沙箱加载，
// 用 scripts/real_dataset.js 中的真实文献用例逐条重跑，
// 输出 预测值 / 文献值 / 偏差% / PASS|FAIL(按每条用例自带的 tolPct)。
//
// 用法:
//   node scripts/offline_model_check.js                 全量
//   node scripts/offline_model_check.js --domain=CFD    指定域
//   node scripts/offline_model_check.js --fails         只列不通过的
//   node scripts/offline_model_check.js --json out.json 额外落盘

const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const SRC = path.join(ROOT, 'frontend_fixed', 'functions', 'api', '[[path]].js');

// ---- 1. 沙箱加载模型函数 ----
function loadModels() {
  let code = fs.readFileSync(SRC, 'utf8');
  code = code.replace(/^import\s+[\s\S]*?;\s*$/gm, '// [offline] import stripped');
  code = code.replace(/^export\s+/gm, '');
  const prelude = 'const trackEvent = async () => {}; const getAnalyticsSummary = async () => ({});\n';
  const names = ['modelCFD', 'modelChemistry', 'modelEnergy', 'modelEnvironment', 'modelMaterials',
    'modelBiology', 'modelSeparation', 'modelHeatTransfer', 'modelStructural', 'modelPDE',
    'modelElectrochemistry', 'modelAnalysis', 'modelPharma', 'cylinderDragCoefficient'];
  const tail = '; return {' + names.map(n => `${n}: typeof ${n}==='function'?${n}:null`).join(',') + '};';
  // eslint-disable-next-line no-new-func
  return new Function(prelude + code + tail)();
}

const DOMAIN_MODEL = {
  cfd: 'modelCFD', chemistry: 'modelChemistry', energy: 'modelEnergy',
  environment: 'modelEnvironment', materials: 'modelMaterials', biology: 'modelBiology',
  separation: 'modelSeparation', heattransfer: 'modelHeatTransfer',
  structural: 'modelStructural', pde: 'modelPDE', electrochemistry: 'modelElectrochemistry'
};

// outPath 形如 'result.result.drag_coefficient'，其中首段 'result' 指 HTTP 响应外层，
// 离线调用直接拿到模型返回对象，故剥掉第一段。
function extract(modelOut, outPath) {
  if (!outPath) return undefined;
  const segs = outPath.split('.');
  const rest = segs[0] === 'result' ? segs.slice(1) : segs;
  return rest.reduce((o, k) => (o == null ? undefined : o[k]), modelOut);
}

function main() {
  const args = process.argv.slice(2);
  const onlyFails = args.includes('--fails');
  const domArg = (args.find(a => a.startsWith('--domain=')) || '').split('=')[1];
  const jsonOut = (args.find(a => a.startsWith('--json=')) || '').split('=')[1];

  const M = loadModels();
  const { CASES } = require('./real_dataset.js');

  let cases = CASES;
  if (domArg) cases = cases.filter(c => c.domain.toLowerCase() === domArg.toLowerCase());

  const rows = [];
  const stats = { pass: 0, fail: 0, skip: 0, err: 0 };

  for (const c of cases) {
    const fnName = DOMAIN_MODEL[String(c.domain).toLowerCase()];
    const fn = fnName ? M[fnName] : null;
    if (!fn) { stats.skip++; rows.push({ ...c, predicted: null, status: 'SKIP', note: '离线不支持(' + c.domain + ')' }); continue; }
    let out;
    try { out = fn(c.engine, c.params, {}); }
    catch (e) { stats.err++; rows.push({ ...c, predicted: null, status: 'ERR', note: e.message }); continue; }
    const pred = extract(out, c.outPath);
    if (typeof pred !== 'number' || isNaN(pred)) {
      stats.err++; rows.push({ ...c, predicted: null, status: 'ERR', note: 'outPath 未取到数值: ' + c.outPath }); continue;
    }
    const diffPct = c.expected !== 0
      ? Math.round(((pred - c.expected) / Math.abs(c.expected)) * 1000) / 10
      : Math.round(pred * 10) / 10;
    const ok = Math.abs(diffPct) <= c.tolPct;
    ok ? stats.pass++ : stats.fail++;
    rows.push({ domain: c.domain, engine: c.engine, label: c.label, expected: c.expected,
      unit: c.unit, predicted: pred, diffPct, tolPct: c.tolPct, status: ok ? 'PASS' : 'FAIL',
      source: c.source });
  }

  const shown = onlyFails ? rows.filter(r => r.status !== 'PASS') : rows;
  const W = (s, n) => {
    let str = String(s === null || s === undefined ? '-' : s);
    // 中文按 2 宽度粗略对齐
    let w = 0, outStr = '';
    for (const ch of str) { const cw = /[\u4e00-\u9fa5\uff00-\uffef]/.test(ch) ? 2 : 1; if (w + cw > n) break; outStr += ch; w += cw; }
    return outStr + ' '.repeat(Math.max(0, n - w));
  };

  console.log('');
  console.log(W('域', 13) + W('引擎', 22) + W('用例', 30) + W('文献', 10) + W('预测', 10) + W('偏差%', 9) + W('容差', 6) + '判定');
  console.log('-'.repeat(108));
  let lastDomain = null;
  for (const r of shown) {
    if (r.domain !== lastDomain) { if (lastDomain) console.log(''); lastDomain = r.domain; }
    console.log(W(r.domain, 13) + W(r.engine, 22) + W(r.label, 30) + W(r.expected, 10) +
      W(r.predicted, 10) + W(r.diffPct, 9) + W(r.tolPct, 6) + r.status + (r.note ? '  ' + r.note : ''));
  }
  console.log('-'.repeat(108));
  const evaluated = stats.pass + stats.fail;
  console.log(`可离线评测 ${evaluated} 例 | PASS ${stats.pass} | FAIL ${stats.fail} | ERR ${stats.err} | SKIP ${stats.skip}(需线上)`);
  if (evaluated) console.log(`离线通过率 ${(stats.pass / evaluated * 100).toFixed(1)}%`);

  // 分域统计
  const byDom = {};
  for (const r of rows) {
    if (r.status === 'SKIP') continue;
    byDom[r.domain] = byDom[r.domain] || { p: 0, f: 0, e: 0 };
    if (r.status === 'PASS') byDom[r.domain].p++;
    else if (r.status === 'FAIL') byDom[r.domain].f++;
    else byDom[r.domain].e++;
  }
  console.log('');
  console.log('分域: ' + Object.entries(byDom)
    .map(([d, v]) => `${d} ${v.p}/${v.p + v.f + v.e}`).join('  |  '));

  if (jsonOut) {
    fs.writeFileSync(path.join(ROOT, jsonOut), JSON.stringify({
      timestamp: new Date().toISOString(), mode: 'offline', stats, results: rows
    }, null, 2));
    console.log('\n已写出: ' + jsonOut);
  }
}

main();
