#!/usr/bin/env node
/**
 * enrich_abstracts.js — 零成本双通道补全文献摘要。
 *
 * 通道优先级:
 *   1) Semantic Scholar Graph API /paper/batch  — 免费无 key，单批 500 个 DOI，全学科覆盖；
 *      无 key 时限速严格，故采用「大批量 + 指数退避重试」策略（1236 篇仅需 3 批）。
 *   2) Europe PMC REST search — 完全免费无限制，对生物医学/药学覆盖极好，用于补漏。
 *
 * 注: OpenAlex 自 2026 年起对 API 调用计费（免费额度极低），本项目零成本原则下不采用。
 *
 * 诚实约束: 摘要原样保存，不改写、不摘编；abstract_source 字段如实标注来源。
 */
const fs = require('fs');
const path = require('path');

const LIB = path.join(__dirname, '..', 'data', 'doi-reference-library-mass.json');
const S2_BATCH = 500;
const UA = 'SwarmLabs-Validation/1.0 (mailto:ops@swarmlabs.tools)';

const sleep = ms => new Promise(r => setTimeout(r, ms));

// ------------------------------------------------ 通道 1: Semantic Scholar
async function s2Batch(dois) {
  const body = JSON.stringify({ ids: dois.map(d => `DOI:${d}`) });
  const url = 'https://api.semanticscholar.org/graph/v1/paper/batch?fields=externalIds,abstract,citationCount,year,venue';
  for (let attempt = 0; attempt < 6; attempt++) {
    try {
      const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'User-Agent': UA },
        body,
        signal: AbortSignal.timeout(90000),
      });
      if (res.status === 429 || res.status === 503) {
        const wait = 8000 * Math.pow(1.8, attempt);
        process.stdout.write(`\r  [S2] 限速，等待 ${Math.round(wait / 1000)}s 后重试 (${attempt + 1}/6)…            `);
        await sleep(wait);
        continue;
      }
      if (!res.ok) return null;
      return await res.json();
    } catch (e) {
      if (attempt === 5) return null;
      await sleep(6000 * (attempt + 1));
    }
  }
  return null;
}

// ------------------------------------------------ 通道 2: Europe PMC
async function epmcOne(doi) {
  const q = `DOI:"${doi}"`;
  const url = `https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=${encodeURIComponent(q)}`
    + `&resultType=core&format=json&pageSize=1`;
  try {
    const res = await fetch(url, { headers: { 'User-Agent': UA }, signal: AbortSignal.timeout(20000) });
    if (!res.ok) return null;
    const j = await res.json();
    const it = j?.resultList?.result?.[0];
    if (!it) return null;
    const abs = it.abstractText ? String(it.abstractText).replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim() : null;
    return abs && abs.length >= 40 ? abs : null;
  } catch { return null; }
}

// ------------------------------------------------ 主流程
(async () => {
  const lib = JSON.parse(fs.readFileSync(LIB, 'utf8'));
  const refs = lib.references || [];
  for (const r of refs) if (r.abstract && !r.abstract_source) r.abstract_source = 'CrossRef';

  const byDoi = new Map(refs.map(r => [r.doi, r]));
  let need = refs.filter(r => !r.abstract);
  console.log(`[enrich] 文献 ${refs.length} 篇，缺摘要 ${need.length} 篇\n`);

  // ---- 通道 1: Semantic Scholar ----
  let s2Filled = 0;
  const totalBatches = Math.ceil(need.length / S2_BATCH);
  for (let i = 0; i < need.length; i += S2_BATCH) {
    const chunk = need.slice(i, i + S2_BATCH);
    const bi = Math.floor(i / S2_BATCH) + 1;
    process.stdout.write(`\r[S2] 批次 ${bi}/${totalBatches} (${chunk.length} 篇) 请求中…                    `);
    const results = await s2Batch(chunk.map(r => r.doi));
    if (Array.isArray(results)) {
      for (const w of results) {
        if (!w) continue;
        const doi = (w.externalIds?.DOI || '').toLowerCase();
        const ref = byDoi.get(doi);
        if (!ref || ref.abstract) continue;
        if (w.abstract && w.abstract.length >= 40) {
          ref.abstract = w.abstract.replace(/\s+/g, ' ').trim();
          ref.abstract_available = true;
          ref.abstract_source = 'SemanticScholar';
          s2Filled++;
        }
        if (typeof w.citationCount === 'number' && w.citationCount > (ref.citations || 0)) {
          ref.citations_s2 = w.citationCount;
        }
      }
    }
    process.stdout.write(`\r[S2] 批次 ${bi}/${totalBatches} 完成，累计补 ${s2Filled} 篇                    \n`);
    fs.writeFileSync(LIB, JSON.stringify(lib, null, 1));
    await sleep(4000);
  }
  console.log(`[S2] 小计新增摘要 ${s2Filled} 篇`);

  // ---- 通道 2: Europe PMC 补漏 ----
  need = refs.filter(r => !r.abstract);
  console.log(`\n[EPMC] 仍缺摘要 ${need.length} 篇，逐条补漏（免费无限制）…`);
  let epmcFilled = 0, n = 0;
  for (const ref of need) {
    n++;
    const abs = await epmcOne(ref.doi);
    if (abs) {
      ref.abstract = abs;
      ref.abstract_available = true;
      ref.abstract_source = 'EuropePMC';
      epmcFilled++;
    }
    if (n % 25 === 0) {
      process.stdout.write(`\r[EPMC] ${n}/${need.length}  已补 ${epmcFilled} 篇          `);
      fs.writeFileSync(LIB, JSON.stringify(lib, null, 1));
    }
    await sleep(120);
  }
  console.log(`\n[EPMC] 小计新增摘要 ${epmcFilled} 篇`);

  // ---- 统计落盘 ----
  const withAbs = refs.filter(r => r.abstract).length;
  lib.stats = lib.stats || {};
  lib.stats.with_abstract = withAbs;
  lib.stats.abstract_sources = refs.reduce((a, r) => {
    if (r.abstract_source) a[r.abstract_source] = (a[r.abstract_source] || 0) + 1;
    return a;
  }, {});
  fs.writeFileSync(LIB, JSON.stringify(lib, null, 1));

  console.log('\n========== 摘要补全完成 ==========');
  console.log(`含摘要总数 : ${withAbs}/${refs.length} (${(withAbs / refs.length * 100).toFixed(1)}%)`);
  console.log(`来源分布   : ${JSON.stringify(lib.stats.abstract_sources)}`);
  console.log('\n下一步: node scripts/doi_extract_mass.js');
})();
