// 抓取每个唯一引擎的完整原始响应, 保存到 reports/engine-raw-dump.json 供分析,
// 以便把 real_dataset.js 的 outPath/expected 对齐到平台真实输出字段.
// 用法: node scripts/dump_responses.js

const fs = require('fs');
const path = require('path');
const { CASES } = require('./real_dataset.js');

(async () => {
  const dump = {};
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
      dump[c.engine] = { params: c.params, response: j };
      console.log(`dumped [${c.engine}]`);
    } catch (e) {
      dump[c.engine] = { params: c.params, error: e.message };
      console.log(`ERR [${c.engine}] ${e.message}`);
    }
    await new Promise(x => setTimeout(x, 4000));
  }
  const out = path.join(__dirname, '..', 'reports', 'engine-raw-dump.json');
  fs.mkdirSync(path.dirname(out), { recursive: true });
  fs.writeFileSync(out, JSON.stringify(dump, null, 2));
  console.log(`\nwrote ${out} (${Object.keys(dump).length} engines)`);
})();
