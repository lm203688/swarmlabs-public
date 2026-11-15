#!/usr/bin/env node
/**
 * rebuild_engine_refs.js — 用 CrossRef 真实文献重建全部引擎参考文献映射。
 *
 * 起因: 校验发现 156 条 refMap 中仅 12 条 (7.7%) DOI 既真实又主题吻合;
 *       68 条 DOI 根本不存在, 76 条 DOI 真实但指向完全不相干的论文。
 *       这些引用会随 API 响应展示给用户, 属严重可信度缺陷, 必须整体重建。
 *
 * 方法: 以原 title 作为检索词 (它准确表达了该引擎的主题意图),
 *       从 CrossRef 检索真实文献, 取相关性+引用量最优者。
 *       所有 DOI/标题/期刊/年份/引用数均来自 CrossRef 返回, 无一手写。
 */
const fs = require('fs');
const path = require('path');

const SRC = path.join(__dirname, '..', 'frontend_fixed', 'functions', 'api', '[[path]].js');
const MAP_OUT = path.join(__dirname, '..', 'data', 'engine-refs-rebuilt.json');
const MAILTO = 'ops@swarmlabs.tools';

const src = fs.readFileSync(SRC, 'utf8');
const re = /(\w+):\s*\{doi:'([^']+)',\s*title:'([^']*)',\s*journal:'([^']*)',\s*year:(\d+),\s*citations:(\d+)\}/g;
const entries = [];
let m;
while ((m = re.exec(src)) !== null) {
  if (m[2] === '10.1000/validation-ref') continue;
  entries.push({ engine: m[1], oldDoi: m[2], title: m[3], journal: m[4], year: +m[5], citations: +m[6], raw: m[0] });
}
console.log(`待重建 ${entries.length} 条引擎参考文献\n`);

function clean(s) {
  return String(s || '')
    .replace(/<[^>]+>/g, '')
    .replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>')
    .replace(/&[a-z]+;/gi, '')
    .replace(/['']/g, '')      // 去掉单引号, 避免破坏 JS 字面量
    .replace(/\s+/g, ' ')
    .trim();
}

async function search(query) {
  const u = new URL('https://api.crossref.org/works');
  u.searchParams.set('query.bibliographic', query);
  u.searchParams.set('rows', '5');
  u.searchParams.set('filter', 'type:journal-article');
  u.searchParams.set('select', 'DOI,title,container-title,published,is-referenced-by-count');
  u.searchParams.set('mailto', MAILTO);
  const r = await fetch(u, { headers: { 'User-Agent': `SwarmLabs/1.0 (mailto:${MAILTO})` } });
  if (!r.ok) return [];
  const j = await r.json();
  return (j.message && j.message.items) || [];
}

// 相关性打分: 标题与查询的词重合度 + 引用量加权
function score(item, query) {
  const qw = new Set(query.toLowerCase().replace(/[^a-z0-9 ]/g, ' ').split(/\s+/).filter(w => w.length > 3));
  const t = clean(Array.isArray(item.title) ? item.title[0] : item.title).toLowerCase();
  const tw = t.replace(/[^a-z0-9 ]/g, ' ').split(/\s+/).filter(w => w.length > 3);
  const hit = tw.filter(w => qw.has(w)).length;
  const overlap = qw.size ? hit / qw.size : 0;
  const cit = Math.log10((item['is-referenced-by-count'] || 0) + 1);
  return overlap * 10 + cit;
}

(async () => {
  const rebuilt = [];
  const failed = [];
  for (let i = 0; i < entries.length; i++) {
    const e = entries[i];
    try {
      const items = await search(e.title);
      const best = items.map(x => ({ x, s: score(x, e.title) })).sort((a, b) => b.s - a.s)[0];
      if (!best || best.s < 1.5) { failed.push(e); console.log(`- ${e.engine}: 无足够相关结果 (保留原值待人工处理)`); }
      else {
        const x = best.x;
        rebuilt.push({
          engine: e.engine,
          doi: x.DOI.toLowerCase(),
          title: clean(Array.isArray(x.title) ? x.title[0] : x.title).slice(0, 110),
          journal: clean(Array.isArray(x['container-title']) ? x['container-title'][0] : x['container-title']).slice(0, 55),
          year: (x.published && x.published['date-parts'] && x.published['date-parts'][0][0]) || e.year,
          citations: x['is-referenced-by-count'] || 0,
          old_doi: e.oldDoi,
          intent: e.title,
          relevance_score: Number(best.s.toFixed(2))
        });
      }
    } catch (err) {
      failed.push(e);
      console.log(`✗ ${e.engine}: ${err.message}`);
    }
    if ((i + 1) % 25 === 0) console.log(`  … ${i + 1}/${entries.length} (成功 ${rebuilt.length})`);
    await new Promise(s => setTimeout(s, 220));
  }

  fs.writeFileSync(MAP_OUT, JSON.stringify({
    generated_at: new Date().toISOString(),
    source: 'CrossRef REST API',
    method: '以原 title 为检索词, 按 词重合度*10 + log10(引用数) 排序取最优',
    rebuilt_count: rebuilt.length,
    failed_count: failed.length,
    failed_engines: failed.map(f => f.engine),
    refs: rebuilt
  }, null, 2));
  console.log(`\n重建完成: ${rebuilt.length} 条真实文献, ${failed.length} 条待人工处理`);
  console.log('写入:', MAP_OUT);
})();
