#!/usr/bin/env node
/**
 * build_engine_literature_bundle.js
 *
 * 把大规模采集的真实文献精简成可嵌入 Worker 的引擎文献包，
 * 让平台具备「每个引擎背后有多篇真实文献支撑」的可查询能力。
 *
 * 体积控制: 每引擎取 TOP N（按引用数降序），仅保留 doi/title/journal/year/citations，
 *          不含摘要，控制在 ~100KB 以内，避免 Worker 体积超限。
 *
 * 诚实约束: 所有条目均来自 CrossRef 官方返回，不做任何人工编造。
 */
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const MASS = path.join(ROOT, 'data', 'doi-reference-library-mass.json');
const OUT_JSON = path.join(ROOT, 'data', 'engine-literature-bundle.json');

const TOP_N = parseInt(process.argv.find((a, i) => process.argv[i - 1] === '--top') || '4', 10);

if (!fs.existsSync(MASS)) {
  console.error('[bundle] 缺少 doi-reference-library-mass.json，请先运行 doi_harvest_mass.js');
  process.exit(1);
}

const lib = JSON.parse(fs.readFileSync(MASS, 'utf8'));
const refs = lib.references || [];

// 按引擎分组
const byEngine = {};
for (const r of refs) {
  (byEngine[r.engine] = byEngine[r.engine] || []).push(r);
}

const bundle = {};
let kept = 0;
for (const [engine, list] of Object.entries(byEngine)) {
  // 优先保留：① 有抽取数值的（可比对）② 引用数高的
  const sorted = list.slice().sort((a, b) => {
    const av = a.value_extracted ? 1 : 0, bv = b.value_extracted ? 1 : 0;
    if (av !== bv) return bv - av;
    return (b.citations || 0) - (a.citations || 0);
  });
  const top = sorted.slice(0, TOP_N).map(r => ({
    doi: r.doi,
    title: r.title.length > 120 ? r.title.slice(0, 117) + '...' : r.title,
    journal: r.journal ? (r.journal.length > 60 ? r.journal.slice(0, 57) + '...' : r.journal) : null,
    year: r.year,
    citations: r.citations || 0,
    ...(r.value_extracted ? { has_data: 1 } : {}),
  }));
  bundle[engine] = top;
  kept += top.length;
}

const payload = {
  meta: {
    generated_at: new Date().toISOString(),
    source: 'CrossRef REST API',
    engines: Object.keys(bundle).length,
    entries: kept,
    top_per_engine: TOP_N,
    policy: '全部 DOI 由 CrossRef 官方 API 返回，真实可溯源；has_data=1 表示已从摘要抽取到可比对数值。',
  },
  literature: bundle,
};

fs.writeFileSync(OUT_JSON, JSON.stringify(payload, null, 1));

const minified = JSON.stringify(payload.literature);
const sizeKB = (Buffer.byteLength(minified, 'utf8') / 1024).toFixed(1);

console.log('=== 引擎文献包 ===');
console.log(`覆盖引擎 : ${Object.keys(bundle).length}`);
console.log(`条目总数 : ${kept}`);
console.log(`每引擎   : TOP ${TOP_N}`);
console.log(`压缩体积 : ${sizeKB} KB (minified JSON)`);
console.log(`输出     : ${OUT_JSON}`);

if (sizeKB > 200) {
  console.log(`\n⚠ 体积偏大 (${sizeKB}KB)，建议降低 --top 参数`);
}
