#!/usr/bin/env node
/**
 * deviation_report.js — 按严格口径重算验证偏差分布。
 *
 * 背景: 各用例原本使用各自的 tolerance(部分高达 50%)，"100% PASS" 会掩盖真实偏差。
 * 本脚本给出统一口径(±5% / ±10% / ±15%)下的通过率，以及所有偏差 >10% 的条目清单，
 * 作为下一轮模型改进的靶子。诚实优先：对外披露以严格口径为准。
 */
const fs = require('fs');
const path = require('path');

const BASE = path.join(__dirname, '..', 'data', 'model-validation-baseline.json');
const OUT = path.join(__dirname, '..', 'data', 'deviation-report.json');

const b = JSON.parse(fs.readFileSync(BASE, 'utf8'));
const r = b.results || [];
const dev = x => Math.abs(Number(x.diff_pct) || 0);

const buckets = [
  ['<=2%', x => dev(x) <= 2],
  ['2-5%', x => dev(x) > 2 && dev(x) <= 5],
  ['5-10%', x => dev(x) > 5 && dev(x) <= 10],
  ['10-15%', x => dev(x) > 10 && dev(x) <= 15],
  ['15-25%', x => dev(x) > 15 && dev(x) <= 25],
  ['>25%', x => dev(x) > 25]
];

console.log('=== 真实偏差分布 (' + r.length + ' 条，线上生产 API 实测) ===');
const dist = {};
for (const [label, fn] of buckets) {
  const n = r.filter(fn).length;
  dist[label] = n;
  console.log('  ' + label.padEnd(8) + String(n).padStart(3) + '  ' + '#'.repeat(n));
}

const p5 = r.filter(x => dev(x) <= 5).length;
const p10 = r.filter(x => dev(x) <= 10).length;
const p15 = r.filter(x => dev(x) <= 15).length;
const pct = n => (n / r.length * 100).toFixed(1) + '%';

console.log('\n=== 不同口径通过率 ===');
console.log('  各用例自带 tolerance :  ' + r.length + '/' + r.length + '  = 100.0%   <- 宽松，含 25~50% 容差');
console.log('  统一 ±15%           :  ' + p15 + '/' + r.length + '  = ' + pct(p15));
console.log('  统一 ±10% (建议对外) :  ' + p10 + '/' + r.length + '  = ' + pct(p10));
console.log('  统一 ±5%  (严苛)     :  ' + p5 + '/' + r.length + '  = ' + pct(p5));

const offenders = r.filter(x => dev(x) > 10).sort((a, b2) => dev(b2) - dev(a));
console.log('\n=== 偏差 >10% 的条目 (' + offenders.length + ' 条，下一轮改进靶子) ===');
offenders.forEach(x => {
  console.log('  ' + String(x.diff_pct).padStart(8) + '%  [' + x.domain + '] ' + x.name +
    '  文献=' + x.paper_result + ' 平台=' + x.predicted + ' (' + (x.paper_unit || '') + ')');
});

const byDomain = {};
r.forEach(x => {
  const d = x.domain;
  byDomain[d] = byDomain[d] || { total: 0, within10: 0, maxDev: 0 };
  byDomain[d].total++;
  if (dev(x) <= 10) byDomain[d].within10++;
  byDomain[d].maxDev = Math.max(byDomain[d].maxDev, dev(x));
});
console.log('\n=== 分域表现 (±10% 口径) ===');
Object.entries(byDomain).sort((a, b2) => (a[1].within10 / a[1].total) - (b2[1].within10 / b2[1].total))
  .forEach(([d, s]) => {
    console.log('  ' + d.padEnd(16) + s.within10 + '/' + s.total +
      '  (' + (s.within10 / s.total * 100).toFixed(0) + '%)  最大偏差 ' + s.maxDev.toFixed(1) + '%');
  });

fs.writeFileSync(OUT, JSON.stringify({
  generated_at: new Date().toISOString(),
  source: 'model-validation-baseline.json (线上生产 API 实测)',
  total_cases: r.length,
  distribution: dist,
  pass_rate: {
    per_case_tolerance: { passed: r.length, total: r.length, rate: '100.0%', caveat: '各用例容差不一，部分达 25~50%，不宜单独对外引用' },
    unified_15pct: { passed: p15, total: r.length, rate: pct(p15) },
    unified_10pct: { passed: p10, total: r.length, rate: pct(p10), recommended_for_public: true },
    unified_5pct: { passed: p5, total: r.length, rate: pct(p5) }
  },
  by_domain: byDomain,
  improvement_targets: offenders.map(x => ({
    domain: x.domain, engine: x.engine, name: x.name,
    paper: x.paper_result, predicted: x.predicted, unit: x.paper_unit, diff_pct: x.diff_pct
  }))
}, null, 2));
console.log('\n报告写入: ' + OUT);
