#!/usr/bin/env node
/**
 * doi_sanitize.js — 清除语料库中经 CrossRef+DataCite+doi.org 三重校验判定为不存在的 DOI。
 *
 * 处理策略（诚实优先，绝不移花接木）:
 *  1) validation_cases: 该用例的实测比对仍然有效，仅 DOI 不可信 ->
 *     置 doi=null 并写 doi_status='unverified'，保留原始 source 文字说明。
 *  2) literature_benchmarks: 条目本身是"文献数值+DOI"的组合，DOI 既然虚构，
 *     数值来源即无法追溯 -> 整条移除，不得保留在基准库中。
 *  绝不把原数值改挂到另一篇真实论文上（那仍是伪造）。
 */
const fs = require('fs');
const path = require('path');

const CORPUS = path.join(__dirname, '..', 'data', 'doi-validation-corpus.json');
const QUARANTINE = path.join(__dirname, '..', 'data', 'doi-quarantine.json');

const INVALID = new Set([
  '10.1126/science.abd4017',
  '10.1021/acs.oprd.1c00123',
  '10.1016/j.jpowsour.2022.13015',
  '10.1016/j.jpowsour.2025.235',
  '10.1016/j.fuel.2023.127028',
  '10.1038/s41598-025-01990-0'
]);

const pick = x => x.doi || (x.reference && x.reference.doi) || null;

const c = JSON.parse(fs.readFileSync(CORPUS, 'utf8'));
const removed = [];
let cleared = 0;

// 1) validation_cases: 清空不可信 DOI，保留用例
c.validation_cases = (c.validation_cases || []).map(x => {
  const d = pick(x);
  if (d && INVALID.has(d.toLowerCase())) {
    cleared++;
    const copy = { ...x };
    copy.doi = null;
    copy.doi_status = 'unverified';
    copy.doi_note = `原填 DOI ${d} 经 CrossRef/DataCite/doi.org 三重校验不存在，已清除；用例数值与实测比对仍有效，DOI 待补。`;
    if (copy.reference && copy.reference.doi) { copy.reference = { ...copy.reference, doi: null }; }
    return copy;
  }
  return x;
});

// 2) literature_benchmarks: 整条移入隔离区
c.literature_benchmarks = (c.literature_benchmarks || []).filter(x => {
  const d = pick(x);
  if (d && INVALID.has(d.toLowerCase())) { removed.push(x); return false; }
  return true;
});

// 重算统计
const dois = new Set();
let withDoi = 0;
(c.validation_cases || []).forEach(x => { const d = pick(x); if (d) { dois.add(d.toLowerCase()); withDoi++; } });
(c.literature_benchmarks || []).forEach(x => { const d = pick(x); if (d) dois.add(d.toLowerCase()); });

c.stats = {
  validation_cases_total: (c.validation_cases || []).length,
  validation_cases_with_verified_doi: withDoi,
  literature_benchmarks_total: (c.literature_benchmarks || []).length,
  unique_verified_dois: dois.size,
  verification_method: 'CrossRef REST API + DataCite API + doi.org resolution',
  last_verified_at: new Date().toISOString()
};
c.meta = { ...(c.meta || {}), integrity_policy: '所有 DOI 必须通过 CrossRef/DataCite/doi.org 校验；未通过者清除或隔离，绝不改挂其他文献。' };

fs.writeFileSync(CORPUS, JSON.stringify(c, null, 2));
fs.writeFileSync(QUARANTINE, JSON.stringify({
  quarantined_at: new Date().toISOString(),
  reason: 'DOI 经 CrossRef+DataCite+doi.org 三重校验不存在，数值来源无法追溯',
  removed_benchmarks: removed,
  cleared_case_dois: [...INVALID]
}, null, 2));

console.log(`validation_cases: 清除不可信 DOI ${cleared} 条`);
console.log(`literature_benchmarks: 隔离虚构条目 ${removed.length} 条`);
console.log('新统计:', JSON.stringify(c.stats, null, 1));
