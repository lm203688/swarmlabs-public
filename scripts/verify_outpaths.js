// 探针: 对 real_dataset.js 中每个唯一引擎调用一次, 确认其 outPath 在真实响应中
// 能解析出数值(而非 undefined/对象). 用于在校验前发现路径错配.
// 用法: node scripts/verify_outpaths.js

const { CASES } = require('./real_dataset.js');

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

(async () => {
  const seen = {};
  for (const c of CASES) {
    if (seen[c.engine]) continue;
    seen[c.engine] = true;
    try {
      const r = await fetch(`https://swarmlabs.tools/api/v2/run/${c.engine}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(c.params), signal: AbortSignal.timeout(25000)
      });
      const j = await r.json();
      const resolved = getPath(j, c.outPath);
      const inner = j.result && j.result.result;
      const innerKeys = inner && typeof inner === 'object' ? Object.keys(inner).join(',') : '(no inner result)';
      const ok = (typeof resolved === 'number' && !isNaN(resolved));
      console.log(`${ok ? 'OK ' : 'BAD'} [${c.engine}] outPath=${c.outPath}`);
      console.log(`     resolved=${resolved}  (expected≈${c.expected}${c.unit})  innerKeys=[${innerKeys}]`);
    } catch (e) {
      console.log(`ERR [${c.engine}] ${e.message}`);
    }
    await new Promise(x => setTimeout(x, 6000));
  }
})();
