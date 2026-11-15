#!/usr/bin/env node
/** 针对 6 条无效 DOI 的主题，从 CrossRef 检索真实替代文献候选 */
const MAILTO = 'ops@swarmlabs.tools';
const QUERIES = [
  ['perovskite_pce', 'perovskite solar cell power conversion efficiency 25% certified stability'],
  ['esterification', 'esterification kinetics conversion sulfuric acid catalyst equilibrium ethyl acetate'],
  ['nmc811_coating', 'Ni-rich layered oxide cathode coating capacity retention 1000 cycles'],
  ['lfp_capacity', 'LiFePO4 cathode specific capacity 170 mAh/g electrochemical performance'],
  ['pt_tio2_h2', 'Pt loaded TiO2 photocatalytic hydrogen evolution water splitting rate'],
  ['psc_passivation', 'perovskite solar cell surface passivation efficiency enhancement phosphonic acid']
];

async function search(q, rows = 5) {
  const u = new URL('https://api.crossref.org/works');
  u.searchParams.set('query.bibliographic', q);
  u.searchParams.set('rows', String(rows));
  u.searchParams.set('filter', 'type:journal-article');
  u.searchParams.set('select', 'DOI,title,container-title,published,is-referenced-by-count');
  u.searchParams.set('mailto', MAILTO);
  const r = await fetch(u, { headers: { 'User-Agent': `SwarmLabs/1.0 (mailto:${MAILTO})` } });
  const j = await r.json();
  return (j.message && j.message.items) || [];
}

(async () => {
  for (const [tag, q] of QUERIES) {
    console.log(`\n### ${tag}`);
    const items = await search(q);
    items
      .sort((a, b) => (b['is-referenced-by-count'] || 0) - (a['is-referenced-by-count'] || 0))
      .slice(0, 3)
      .forEach(x => {
        const t = Array.isArray(x.title) ? x.title[0] : x.title;
        const j = Array.isArray(x['container-title']) ? x['container-title'][0] : x['container-title'];
        const y = x.published && x.published['date-parts'] && x.published['date-parts'][0][0];
        console.log(`  ${x.DOI}  [cit ${x['is-referenced-by-count'] || 0}] ${j} ${y}`);
        console.log(`    ${String(t).slice(0, 95)}`);
      });
    await new Promise(s => setTimeout(s, 260));
  }
})();
