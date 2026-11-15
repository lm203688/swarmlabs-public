#!/usr/bin/env node
/**
 * doi_verify.js — 用 CrossRef 官方 API 校验语料库中每个 DOI 是否真实存在。
 * 诚实优先：查不到的 DOI 一律标记 UNVERIFIED，不得计入"已验证"统计。
 * 零成本：CrossRef REST API 免费、无需 key。
 */
const fs = require('fs');
const path = require('path');

const CORPUS = path.join(__dirname, '..', 'data', 'doi-validation-corpus.json');
const OUT = path.join(__dirname, '..', 'data', 'doi-verification-report.json');
const MAILTO = 'ops@swarmlabs.tools'; // CrossRef polite pool

function pickDoi(x) {
  return x.doi || (x.reference && x.reference.doi) || (x.source && x.source.doi) || null;
}

async function verifyDoi(doi) {
  const url = `https://api.crossref.org/works/${encodeURIComponent(doi)}?mailto=${MAILTO}`;
  try {
    const r = await fetch(url, { headers: { 'User-Agent': `SwarmLabs-Validator/1.0 (mailto:${MAILTO})` } });
    if (r.status === 404) return { doi, ok: false, reason: 'not_found_in_crossref' };
    if (!r.ok) return { doi, ok: false, reason: `http_${r.status}` };
    const j = await r.json();
    const m = j.message || {};
    return {
      doi,
      ok: true,
      title: Array.isArray(m.title) ? m.title[0] : m.title,
      journal: Array.isArray(m['container-title']) ? m['container-title'][0] : m['container-title'],
      year: (m.published && m.published['date-parts'] && m.published['date-parts'][0] && m.published['date-parts'][0][0]) || null,
      type: m.type,
      publisher: m.publisher,
      citations: m['is-referenced-by-count'] || 0
    };
  } catch (e) {
    return { doi, ok: false, reason: 'network_error: ' + e.message };
  }
}

(async () => {
  const c = JSON.parse(fs.readFileSync(CORPUS, 'utf8'));
  const set = new Set();
  [...(c.validation_cases || []), ...(c.literature_benchmarks || [])].forEach(x => {
    const d = pickDoi(x);
    if (d) set.add(d.toLowerCase());
  });
  const list = [...set];
  console.log(`校验 ${list.length} 个 DOI …`);

  const results = [];
  for (const d of list) {
    const r = await verifyDoi(d);
    results.push(r);
    console.log(`${r.ok ? '✓' : '✗'} ${d}${r.ok ? '  ' + String(r.journal || '').slice(0, 42) + ' (' + r.year + ')' : '  ' + r.reason}`);
    await new Promise(s => setTimeout(s, 220)); // 礼貌限速
  }

  const ok = results.filter(r => r.ok);
  const bad = results.filter(r => !r.ok);
  const report = {
    generated_at: new Date().toISOString(),
    source: 'CrossRef REST API (api.crossref.org)',
    total: results.length,
    verified: ok.length,
    unverified: bad.length,
    verified_list: ok,
    unverified_list: bad
  };
  fs.writeFileSync(OUT, JSON.stringify(report, null, 2));
  console.log(`\n结果: ${ok.length}/${results.length} 通过 CrossRef 校验, ${bad.length} 条未通过`);
  console.log('报告写入:', OUT);
})();
