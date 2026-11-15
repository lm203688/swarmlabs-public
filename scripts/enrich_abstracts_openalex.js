#!/usr/bin/env node
/**
 * enrich_abstracts_openalex.js — 用 OpenAlex 批量补全文献摘要。
 *
 * 背景: CrossRef 仅约 25% 的条目带摘要，严重限制数值抽取率。
 *       OpenAlex（免费、无需 API key、覆盖 2.5 亿+ 文献）摘要覆盖率高得多。
 *
 * 做法: 按 DOI 批量查询（每批 50 个），把 abstract_inverted_index 还原为正文，
 *       仅在原条目无摘要时填充，并标注 abstract_source 便于溯源。
 *
 * 诚实约束: 摘要原样保存，不改写、不摘编；来源字段如实标注 CrossRef / OpenAlex。
 */
const fs = require('fs');
const path = require('path');

const MAILTO = 'ops@swarmlabs.tools';
const LIB = path.join(__dirname, '..', 'data', 'doi-reference-library-mass.json');
const BATCH = 50;

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

// OpenAlex 用倒排索引存摘要: { "word": [pos1, pos2], ... } → 还原为原文顺序
function invertedToText(inv) {
  if (!inv || typeof inv !== 'object') return null;
  const slots = [];
  for (const [word, positions] of Object.entries(inv)) {
    if (!Array.isArray(positions)) continue;
    for (const p of positions) slots[p] = word;
  }
  const text = slots.filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
  return text.length >= 40 ? text : null;
}

async function fetchBatch(dois) {
  const filter = `doi:${dois.join('|')}`;
  const url = `https://api.openalex.org/works?filter=${encodeURIComponent(filter)}`
    + `&per-page=${dois.length}&select=doi,abstract_inverted_index,cited_by_count,publication_year`
    + `&mailto=${MAILTO}`;
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const res = await fetch(url, {
        headers: { 'User-Agent': `SwarmLabs-Validation/1.0 (mailto:${MAILTO})` },
        signal: AbortSignal.timeout(40000),
      });
      if (res.status === 429) { await sleep(4000 * (attempt + 1)); continue; }
      if (!res.ok) return null;
      const j = await res.json();
      return j.results || [];
    } catch (e) {
      if (attempt === 2) return null;
      await sleep(2000 * (attempt + 1));
    }
  }
  return null;
}

(async () => {
  const lib = JSON.parse(fs.readFileSync(LIB, 'utf8'));
  const refs = lib.references || [];

  // 标注已有摘要的来源
  for (const r of refs) {
    if (r.abstract && !r.abstract_source) r.abstract_source = 'CrossRef';
  }

  const need = refs.filter(r => !r.abstract);
  console.log(`[enrich] 文献总数 ${refs.length}，缺摘要 ${need.length} 篇，开始从 OpenAlex 补全…`);

  const byDoi = new Map(refs.map(r => [r.doi, r]));
  let filled = 0, batches = 0;

  for (let i = 0; i < need.length; i += BATCH) {
    const chunk = need.slice(i, i + BATCH);
    const results = await fetchBatch(chunk.map(r => r.doi));
    batches++;
    if (results) {
      for (const w of results) {
        const doi = (w.doi || '').replace(/^https?:\/\/doi\.org\//i, '').toLowerCase();
        const ref = byDoi.get(doi);
        if (!ref || ref.abstract) continue;
        const text = invertedToText(w.abstract_inverted_index);
        if (text) {
          ref.abstract = text;
          ref.abstract_available = true;
          ref.abstract_source = 'OpenAlex';
          filled++;
        }
        // 顺带用 OpenAlex 的引用数补全（CrossRef 常滞后）
        if (typeof w.cited_by_count === 'number' && w.cited_by_count > (ref.citations || 0)) {
          ref.citations_openalex = w.cited_by_count;
        }
      }
    }
    process.stdout.write(`\r[enrich] 批次 ${batches}/${Math.ceil(need.length / BATCH)}  已补 ${filled} 篇   `);
    await sleep(300);
  }

  const withAbs = refs.filter(r => r.abstract).length;
  lib.stats = lib.stats || {};
  lib.stats.with_abstract = withAbs;
  lib.stats.abstract_sources = refs.reduce((a, r) => {
    if (r.abstract_source) a[r.abstract_source] = (a[r.abstract_source] || 0) + 1;
    return a;
  }, {});

  fs.writeFileSync(LIB, JSON.stringify(lib, null, 1));

  console.log('\n\n========== 摘要补全完成 ==========');
  console.log(`OpenAlex 新补 : ${filled} 篇`);
  console.log(`当前含摘要   : ${withAbs}/${refs.length} (${(withAbs / refs.length * 100).toFixed(1)}%)`);
  console.log(`来源分布     : ${JSON.stringify(lib.stats.abstract_sources)}`);
  console.log('\n下一步: node scripts/doi_extract_mass.js  重新抽取数值');
})();
