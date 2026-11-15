#!/usr/bin/env node
/**
 * doi_recheck.js — 对 CrossRef 未命中的 DOI 做二次判定。
 * CrossRef 只索引 Crossref 注册的 DOI；Zenodo/Figshare 等由 DataCite 注册。
 * 判定顺序: DataCite API -> doi.org 解析(HEAD) -> 判定为不存在。
 */
const DOIS = process.argv.slice(2);

async function datacite(doi) {
  try {
    const r = await fetch(`https://api.datacite.org/dois/${encodeURIComponent(doi)}`);
    if (!r.ok) return null;
    const j = await r.json();
    const a = j.data && j.data.attributes;
    if (!a) return null;
    return {
      registrant: 'DataCite',
      title: a.titles && a.titles[0] && a.titles[0].title,
      year: a.publicationYear,
      publisher: a.publisher
    };
  } catch { return null; }
}

async function resolve(doi) {
  try {
    const r = await fetch(`https://doi.org/${doi}`, { method: 'HEAD', redirect: 'follow' });
    return r.ok ? r.url : null;
  } catch { return null; }
}

(async () => {
  for (const d of DOIS) {
    const dc = await datacite(d);
    if (dc) {
      console.log(`✓ DATACITE  ${d}\n            ${String(dc.title).slice(0, 80)} | ${dc.publisher} ${dc.year}`);
      continue;
    }
    const url = await resolve(d);
    if (url) {
      console.log(`~ RESOLVES  ${d}\n            -> ${url.slice(0, 100)}`);
      continue;
    }
    console.log(`✗ INVALID   ${d}  (CrossRef+DataCite+doi.org 均无)`);
  }
})();
