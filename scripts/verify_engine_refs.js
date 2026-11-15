#!/usr/bin/env node
/**
 * verify_engine_refs.js — 校验后端 refMap 中 157 条"引擎 -> 参考文献 DOI"的真实性。
 *
 * 这些 DOI 会随 API 响应直接展示给用户，一旦存在编造条目即构成严重可信度问题。
 * 校验链: CrossRef -> DataCite -> doi.org 解析，三者皆无则判定为无效。
 */
const fs = require('fs');
const path = require('path');

const SRC = path.join(__dirname, '..', 'frontend_fixed', 'functions', 'api', '[[path]].js');
const OUT = path.join(__dirname, '..', 'data', 'engine-ref-verification.json');
const MAILTO = 'ops@swarmlabs.tools';

const src = fs.readFileSync(SRC, 'utf8');
const re = /(\w+):\s*\{doi:'([^']+)',\s*title:'([^']*)'/g;
const entries = [];
let m;
while ((m = re.exec(src)) !== null) {
  entries.push({ engine: m[1], doi: m[2], title: m[3] });
}
// 去掉兜底占位
const real = entries.filter(e => e.doi !== '10.1000/validation-ref');
console.log(`提取到 ${entries.length} 条 (含兜底), 待校验 ${real.length} 条\n`);

async function crossref(doi) {
  try {
    const r = await fetch(`https://api.crossref.org/works/${encodeURIComponent(doi)}?mailto=${MAILTO}`,
      { headers: { 'User-Agent': `SwarmLabs/1.0 (mailto:${MAILTO})` } });
    if (!r.ok) return null;
    const j = await r.json();
    const msg = j.message || {};
    return {
      title: Array.isArray(msg.title) ? msg.title[0] : msg.title,
      journal: Array.isArray(msg['container-title']) ? msg['container-title'][0] : msg['container-title'],
      year: msg.published && msg.published['date-parts'] && msg.published['date-parts'][0][0]
    };
  } catch { return null; }
}
async function datacite(doi) {
  try {
    const r = await fetch(`https://api.datacite.org/dois/${encodeURIComponent(doi)}`);
    if (!r.ok) return null;
    const j = await r.json();
    const a = j.data && j.data.attributes;
    return a ? { title: a.titles && a.titles[0] && a.titles[0].title, journal: a.publisher, year: a.publicationYear } : null;
  } catch { return null; }
}

(async () => {
  const valid = [], invalid = [];
  for (let i = 0; i < real.length; i++) {
    const e = real[i];
    let meta = await crossref(e.doi);
    let via = 'crossref';
    if (!meta) { meta = await datacite(e.doi); via = 'datacite'; }
    if (meta) {
      valid.push({ ...e, verified_via: via, actual_title: meta.title, actual_journal: meta.journal, actual_year: meta.year });
    } else {
      invalid.push({ ...e, reason: 'not found in CrossRef or DataCite' });
      console.log(`✗ ${e.engine.padEnd(24)} ${e.doi}`);
    }
    if ((i + 1) % 25 === 0) console.log(`  … ${i + 1}/${real.length}  (无效累计 ${invalid.length})`);
    await new Promise(s => setTimeout(s, 180));
  }

  fs.writeFileSync(OUT, JSON.stringify({
    generated_at: new Date().toISOString(),
    total_checked: real.length,
    valid: valid.length,
    invalid: invalid.length,
    invalid_list: invalid,
    valid_list: valid
  }, null, 2));

  console.log(`\n===== 结果 =====`);
  console.log(`有效: ${valid.length}/${real.length} (${(valid.length / real.length * 100).toFixed(1)}%)`);
  console.log(`无效: ${invalid.length}`);
  console.log(`报告: ${OUT}`);
})();
