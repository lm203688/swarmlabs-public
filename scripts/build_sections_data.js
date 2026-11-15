// 专业板块数据聚合 (SwarmLabs)
// 读取 data/benchmark-dataset.json (真实来源验证记录), 按域聚合,
// 生成 data/professional-sections.json —— 为各"专业板块"提供真实数据量支撑。
// 用法: node scripts/build_sections_data.js
const fs = require('fs');
const path = require('path');
const DATA = path.join(__dirname, '..', 'data');

function domainCN(d) {
  return ({
    chemistry: '化学合成', energy: '能源电化学', environment: '环境', materials: '材料',
    biology: '生物', separation: '分离', analysis: '分析', pharma: '药学',
    electrochemistry: '电化学', cfd: '计算流体', heattransfer: '传热', structural: '结构力学', pde: '偏微分方程'
  })[d] || d;
}

const bench = JSON.parse(fs.readFileSync(path.join(DATA, 'benchmark-dataset.json'), 'utf8'));
const recs = bench.records || [];
const byDomain = {};
for (const r of recs) {
  const d = r.domain;
  byDomain[d] = byDomain[d] || { domain: d, cn: domainCN(d), count: 0, engines: new Set(), pass: 0, fail: 0, err: 0, samples: [] };
  const s = byDomain[d];
  s.count++;
  s.engines.add(r.engine_type);
  if (r.validation_status === 'PASS') s.pass++;
  else if (r.validation_status === 'FAIL') s.fail++;
  else s.err++;
  if (s.samples.length < 5) s.samples.push({
    engine: r.engine_type, name: r.experiment_name, citation: r.citation,
    expected: r.expected_output && r.expected_output.value, unit: r.expected_output && r.expected_output.unit,
    platform: r.platform_predicted, diff_pct: r.diff_pct, status: r.validation_status
  });
}

const sections = Object.values(byDomain).map(s => ({
  domain: s.domain, cn: s.cn, count: s.count,
  engine_types: [...s.engines], pass: s.pass, fail: s.fail, err: s.err,
  pass_rate_pct: s.count ? Math.round((s.pass / s.count) * 1000) / 10 : 0,
  samples: s.samples
}));
sections.sort((a, b) => b.count - a.count);

const out = {
  generated: new Date().toISOString(),
  total_real_records: recs.length,
  domains_covered: sections.length,
  sections
};
fs.writeFileSync(path.join(DATA, 'professional-sections.json'), JSON.stringify(out, null, 2));
console.log(`专业板块数据聚合完成: ${recs.length} 条真实记录, 覆盖 ${sections.length} 个域`);
for (const s of sections) console.log(`  ${s.cn}(${s.domain}): ${s.count} 条 | PASS ${s.pass} FAIL ${s.fail} ERR ${s.err} | 引擎 ${s.engine_types.length} 类`);
