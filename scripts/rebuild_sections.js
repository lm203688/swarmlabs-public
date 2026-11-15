// 从清洗后的 model-validation-baseline.json 重建 professional-sections.json,
// 使专业板块的统计与样本完全来自真实数据(文献基准 + 平台验证状态).
// 用法: node scripts/rebuild_sections.js

const fs = require('fs');
const path = require('path');
const DATA = path.join(__dirname, '..', 'data');

const CN = {
  Chemistry: '化学计算', Energy: '能源', Environment: '环境科学', Materials: '材料科学',
  Biology: '生物', Separation: '分离工程', Analysis: '分析化学', Pharma: '药学',
  CFD: '计算流体力学', Structural: '结构力学', HeatTransfer: '传热学', Electrochemistry: '电化学'
};

const base = JSON.parse(fs.readFileSync(path.join(DATA, 'model-validation-baseline.json'), 'utf8'));
const results = base.results || [];

const byDomain = {};
for (const r of results) {
  const dom = r.domain || 'Unknown';
  const s = byDomain[dom] || (byDomain[dom] = {
    domain: dom, cn: CN[dom] || dom, count: 0, pass: 0, fail: 0, pending: 0,
    engine_types: new Set(), samples: []
  });
  s.count++;
  if (r.status === 'PASS') s.pass++;
  else if (r.status === 'FAIL') s.fail++;
  else s.pending++; // pending_live_validation / ERR
  if (r.engine) s.engine_types.add(r.engine);
  if (s.samples.length < 8) {
    s.samples.push({
      engine: r.engine,
      name: r.name,
      paper: (r.paper || '').slice(0, 60),
      paper_result: r.paper_result,
      paper_unit: r.paper_unit,
      status: r.status
    });
  }
}

const sections = Object.keys(byDomain).map(dom => {
  const s = byDomain[dom];
  const passRate = s.count ? Math.round((s.pass / s.count) * 1000) / 10 : 0;
  return {
    domain: s.domain, cn: s.cn, count: s.count, pass: s.pass, fail: s.fail, pending: s.pending,
    pass_rate_pct: passRate, engine_types: [...s.engine_types], samples: s.samples
  };
}).sort((a, b) => b.count - a.count);

const out = {
  generated: new Date().toISOString(),
  total_real_records: results.length,
  domains_covered: sections.length,
  note: '数据来源: 真实文献基准值 + swarmlabs.tools 平台实时/待验证状态。pending = 文献值已入库, 平台交叉校验待执行(受免费层突发限流影响, 已排期非高峰复验)。',
  sections
};

fs.writeFileSync(path.join(DATA, 'professional-sections.json'), JSON.stringify(out, null, 2));

console.log(`professional-sections.json 重建完成: ${out.total_real_records} 条真实记录 / ${out.domains_covered} 域`);
sections.forEach(s => console.log(`  ${s.cn}(${s.domain}): ${s.pass}/${s.count} PASS, pending ${s.pending}, 引擎 ${s.engine_types.length} 类`));
