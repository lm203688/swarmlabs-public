// 清洗 expand_real_data.js 历史运行产生的污染数据:
// 因 computeStatus 曾返回 'ERR' 字符串(而非对象), 94 条记录的 status/predicted/diff_*
// 字段被 JSON.stringify 丢弃为 undefined. 本脚本将其明确重标为 'pending_live_validation',
// 保留可信的文献基准值(paper_result/paper), 不篡改任何真实数据.
// 用法: node scripts/clean_relabel.js

const fs = require('fs');
const path = require('path');
const DATA = path.join(__dirname, '..', 'data');

const VALID = new Set(['PASS', 'FAIL', 'ERR']);

function cleanStatus(s) {
  return (s !== undefined && s !== null && VALID.has(s)) ? s : 'pending_live_validation';
}

// ---- 1. model-validation-baseline.json ----
const baseFile = path.join(DATA, 'model-validation-baseline.json');
const base = JSON.parse(fs.readFileSync(baseFile, 'utf8'));
let relabeled = 0, kept = 0;
base.results = (base.results || []).map(r => {
  if (r.status === undefined || r.status === null || !VALID.has(r.status)) {
    relabeled++;
    return {
      ...r,
      status: 'pending_live_validation',
      predicted: (r.predicted !== undefined) ? r.predicted : null,
      diff_abs: null,
      diff_pct: null
    };
  }
  kept++;
  return r;
});
fs.writeFileSync(baseFile, JSON.stringify(base, null, 2));

// ---- 2. benchmark-dataset.json ----
const benchFile = path.join(DATA, 'benchmark-dataset.json');
const bench = JSON.parse(fs.readFileSync(benchFile, 'utf8'));
let brelabeled = 0;
bench.records = (bench.records || []).map(r => {
  const vs = r.validation_status;
  if (vs === undefined || vs === null || !VALID.has(vs)) {
    brelabeled++;
    return { ...r, validation_status: 'pending_live_validation', platform_predicted: null, diff_pct: null };
  }
  return r;
});
bench.last_updated = new Date().toISOString();
fs.writeFileSync(benchFile, JSON.stringify(bench, null, 2));

console.log(`baseline: ${relabeled} 条重标为 pending_live_validation, ${kept} 条保持原状态 (共 ${base.results.length})`);
console.log(`benchmark: ${brelabeled} 条重标为 pending_live_validation (共 ${(bench.records||[]).length})`);
