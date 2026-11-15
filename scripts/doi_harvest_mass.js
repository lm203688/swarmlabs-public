#!/usr/bin/env node
/**
 * doi_harvest_mass.js — 大规模真实文献采集（平台级验证数据库建设）
 *
 * 目标：把文献验证库从 83 篇扩充到 1000+ 篇，覆盖平台全部 156 个引擎。
 *
 * 诚实原则（不可违背）:
 *  1. 所有 DOI 均来自 CrossRef 官方 REST API 返回，绝不手写、绝不凭记忆填写；
 *  2. 仅收录 journal-article / proceedings-article / book-chapter；
 *  3. 只记录 CrossRef 实际返回的字段（title/journal/year/citations/author/abstract）；
 *  4. 不编造任何实验数值。数值抽取单独进行且标注 confidence。
 *
 * 用法: node scripts/doi_harvest_mass.js [--per N] [--resume]
 */
const fs = require('fs');
const path = require('path');

const MAILTO = 'ops@swarmlabs.tools';
const ROOT = path.join(__dirname, '..');
const SRC = path.join(ROOT, 'frontend_fixed', 'functions', 'api', '[[path]].js');
const OUT = path.join(ROOT, 'data', 'doi-reference-library-mass.json');
const PROGRESS = path.join(ROOT, 'data', '_harvest-progress.json');

const argv = process.argv.slice(2);
const PER = (() => {
  const i = argv.indexOf('--per');
  return i >= 0 ? parseInt(argv[i + 1], 10) : 8;
})();
const RESUME = argv.includes('--resume');

// ---------------------------------------------------------------- 引擎清单
// 从生产源码 refMap 中提取全部引擎 id + 现有参考文献 title（作为检索种子）
function extractEngines() {
  const s = fs.readFileSync(SRC, 'utf8');
  const start = s.indexOf('const refMap = {');
  if (start < 0) throw new Error('refMap not found');
  const seg = s.slice(start, start + 200000);
  const re = /^\s+([a-z0-9_]+):\s*\{doi:'([^']*)',\s*title:'([^']*)'/gm;
  const out = [];
  let m;
  let curDomain = 'General';
  const lines = seg.split('\n');
  for (const line of lines) {
    const dm = line.match(/^\s*\/\/\s*=+\s*([A-Za-z][A-Za-z ]*?)\s*=+/);
    if (dm) { curDomain = dm[1].trim(); continue; }
    const em = line.match(/^\s+([a-z0-9_]+):\s*\{doi:'([^']*)',\s*title:'([^']*)'/);
    if (em) out.push({ engine: em[1], domain: curDomain, seedDoi: em[2], seedTitle: em[3] });
    if (/^\s*\};/.test(line) && out.length > 10) break;
  }
  return out;
}

// ---------------------------------------------------------------- 检索词
// 优先用引擎 id 的语义化拆词 + 现有 title 的关键词，构造领域相关的检索式
const ENGINE_QUERY_HINT = {
  suzuki: 'Suzuki-Miyaura cross coupling reaction yield palladium catalyst',
  heck: 'Heck reaction palladium catalyzed olefin arylation yield',
  sonogashira: 'Sonogashira coupling alkyne aryl halide yield copper',
  buchwald: 'Buchwald-Hartwig amination palladium catalyst yield',
  olefin_metathesis: 'olefin metathesis Grubbs ruthenium catalyst conversion',
  drug_release: 'drug release kinetics HPMC matrix tablet Korsmeyer-Peppas',
  co2_hydrogenation: 'CO2 hydrogenation methanol conversion selectivity catalyst',
  steam_reforming: 'methane steam reforming conversion hydrogen nickel catalyst',
};

function buildQuery(e) {
  if (ENGINE_QUERY_HINT[e.engine]) return ENGINE_QUERY_HINT[e.engine];
  // 引擎 id 拆词
  const words = e.engine.replace(/_/g, ' ');
  // 用 title 里的实词补充（去掉停用词）
  const stop = new Set(['a', 'an', 'the', 'of', 'for', 'and', 'on', 'in', 'to', 'with', 'by', 'from', 'using', 'via', 'new', 'recent', 'study', 'studies', 'analysis', 'review']);
  const tw = (e.seedTitle || '')
    .toLowerCase().replace(/[^a-z0-9 ]/g, ' ').split(/\s+/)
    .filter(w => w.length > 3 && !stop.has(w)).slice(0, 6).join(' ');
  return `${words} ${tw}`.trim();
}

// ---------------------------------------------------------------- CrossRef
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function crossrefSearch(query, rows) {
  const url = `https://api.crossref.org/works?query.bibliographic=${encodeURIComponent(query)}`
    + `&rows=${rows}&select=DOI,title,container-title,issued,is-referenced-by-count,author,abstract,type`
    + `&filter=type:journal-article&sort=score&order=desc&mailto=${MAILTO}`;
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const res = await fetch(url, {
        headers: { 'User-Agent': `SwarmLabs-Validation/1.0 (mailto:${MAILTO})` },
        signal: AbortSignal.timeout(30000),
      });
      if (res.status === 429) { await sleep(3000 * (attempt + 1)); continue; }
      if (!res.ok) return null;
      const j = await res.json();
      return j?.message?.items || [];
    } catch (err) {
      if (attempt === 2) return null;
      await sleep(1500 * (attempt + 1));
    }
  }
  return null;
}

function cleanAbstract(a) {
  if (!a) return null;
  return a.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
}

// ---------------------------------------------------------------- 主流程
(async () => {
  const engines = extractEngines();
  console.log(`[harvest] 引擎总数: ${engines.length}, 每引擎目标 ${PER} 篇`);

  let store = { meta: {}, stats: {}, references: [] };
  let done = new Set();
  if (RESUME && fs.existsSync(OUT)) {
    store = JSON.parse(fs.readFileSync(OUT, 'utf8'));
    if (fs.existsSync(PROGRESS)) done = new Set(JSON.parse(fs.readFileSync(PROGRESS, 'utf8')).done || []);
    console.log(`[harvest] 断点续采: 已有 ${store.references.length} 篇, 已完成 ${done.size} 个引擎`);
  }

  const seen = new Set(store.references.map(r => r.doi));
  let idx = 0;

  for (const e of engines) {
    idx++;
    if (done.has(e.engine)) continue;
    const q = buildQuery(e);
    const items = await crossrefSearch(q, PER + 4);
    if (!items) {
      console.log(`[${idx}/${engines.length}] ${e.engine} — 检索失败`);
      await sleep(400);
      continue;
    }
    let added = 0;
    for (const it of items) {
      if (added >= PER) break;
      const doi = (it.DOI || '').toLowerCase();
      if (!doi || !/^10\./.test(doi) || seen.has(doi)) continue;
      const title = Array.isArray(it.title) ? it.title[0] : it.title;
      if (!title) continue;
      const year = it.issued?.['date-parts']?.[0]?.[0] || null;
      const au = it.author?.[0];
      store.references.push({
        domain: e.domain,
        engine: e.engine,
        topic: e.engine,
        doi,
        title: String(title).slice(0, 200),
        journal: (Array.isArray(it['container-title']) ? it['container-title'][0] : it['container-title']) || null,
        year,
        citations: it['is-referenced-by-count'] ?? 0,
        first_author: au ? [au.family, au.given].filter(Boolean).join(', ') : null,
        source: 'CrossRef',
        query_used: q,
        abstract_available: !!it.abstract,
        abstract: cleanAbstract(it.abstract),
        value_extracted: false,
        note: 'DOI 与元数据经 CrossRef 官方索引返回，真实可溯源；数值未抽取，不计入已比对基准。',
      });
      seen.add(doi);
      added++;
    }
    done.add(e.engine);
    console.log(`[${idx}/${engines.length}] ${e.engine} (${e.domain}) +${added} 篇  累计 ${store.references.length}`);

    // 每 10 个引擎落盘一次，防中断丢失
    if (idx % 10 === 0) {
      fs.writeFileSync(OUT, JSON.stringify(store, null, 1));
      fs.writeFileSync(PROGRESS, JSON.stringify({ done: [...done] }, null, 1));
    }
    await sleep(350);
  }

  // -------------------------------------------------------------- 统计
  const byDomain = {}, byEngine = {};
  for (const r of store.references) {
    byDomain[r.domain] = (byDomain[r.domain] || 0) + 1;
    byEngine[r.engine] = (byEngine[r.engine] || 0) + 1;
  }
  store.meta = {
    generated_at: new Date().toISOString(),
    source: 'CrossRef REST API (api.crossref.org)',
    mailto: MAILTO,
    engines_covered: Object.keys(byEngine).length,
    engines_total: engines.length,
    principle: '所有 DOI 均由 CrossRef 官方 API 返回，未经人工编造；数值未抽取的条目明确标注 value_extracted=false。',
  };
  store.stats = {
    total: store.references.length,
    unique_doi: new Set(store.references.map(r => r.doi)).size,
    with_abstract: store.references.filter(r => r.abstract_available).length,
    by_domain: byDomain,
    engines_covered: Object.keys(byEngine).length,
    engines_with_zero: engines.filter(e => !byEngine[e.engine]).map(e => e.engine),
  };

  fs.writeFileSync(OUT, JSON.stringify(store, null, 1));
  fs.writeFileSync(PROGRESS, JSON.stringify({ done: [...done] }, null, 1));

  console.log('\n========== 采集完成 ==========');
  console.log(`总文献数   : ${store.stats.total}`);
  console.log(`唯一 DOI   : ${store.stats.unique_doi}`);
  console.log(`含摘要     : ${store.stats.with_abstract}`);
  console.log(`覆盖引擎   : ${store.stats.engines_covered}/${engines.length}`);
  console.log(`零结果引擎 : ${store.stats.engines_with_zero.length}`);
  console.log('按领域:');
  for (const [k, v] of Object.entries(byDomain).sort((a, b) => b[1] - a[1])) {
    console.log(`  ${k.padEnd(18)} ${v}`);
  }
  console.log(`\n输出: ${OUT}`);
})();
