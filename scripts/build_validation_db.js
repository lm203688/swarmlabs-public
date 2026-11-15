#!/usr/bin/env node
/**
 * build_validation_db.js — 汇总 SwarmLabs 全部验证数据资产为统一总账。
 *
 * 汇入四类来源, 全部 DOI 均已通过 CrossRef/DataCite 校验:
 *   1) validation_cases     — 有实测比对的用例 (线上生产 API 实测)
 *   2) literature_benchmarks— 文献数值基准
 *   3) reference_library    — CrossRef 采集的领域文献池 (部分含抽取数值)
 *   4) engine_references    — 156 个引擎的参考文献 (CrossRef 重建)
 *
 * 诚实口径: 通过率一律以统一 ±10% 为准对外披露, 不使用各用例宽容差的 100%。
 */
const fs = require('fs');
const path = require('path');

const D = p => path.join(__dirname, '..', 'data', p);
const read = p => { try { return JSON.parse(fs.readFileSync(D(p), 'utf8')); } catch { return null; } };

const corpus = read('doi-validation-corpus.json') || {};
const refLib = read('doi-reference-library.json') || {};
const massLib = read('doi-reference-library-mass.json') || {};
const litBench = read('literature-benchmarks-extracted.json') || {};
const engineRefs = read('engine-refs-rebuilt.json') || {};
const deviation = read('deviation-report.json') || {};

const dois = new Set();
const add = d => { if (d && /^10\./.test(d)) dois.add(String(d).toLowerCase()); };

(corpus.validation_cases || []).forEach(x => add(x.doi || (x.reference && x.reference.doi)));
(corpus.literature_benchmarks || []).forEach(x => add(x.doi || (x.reference && x.reference.doi)));
(refLib.references || []).forEach(x => add(x.doi));
(massLib.references || []).forEach(x => add(x.doi));
(engineRefs.refs || []).forEach(x => add(x.doi));

const extractedMetrics = (refLib.references || []).reduce((s, r) => s + ((r.extracted_metrics || []).length), 0);
const massMetrics = (litBench.stats && litBench.stats.metrics) || 0;
const massPapersWithValues = (litBench.stats && litBench.stats.papers) || 0;

// 引擎覆盖统计
const engineCoverage = {};
(massLib.references || []).forEach(r => { engineCoverage[r.engine] = (engineCoverage[r.engine] || 0) + 1; });

const db = {
  meta: {
    generated_at: new Date().toISOString(),
    project: 'SwarmLabs',
    purpose: '科学计算引擎的文献验证数据库总账',
    integrity_policy: [
      '所有 DOI 必须通过 CrossRef 或 DataCite 校验为真实存在, 未通过者一律清除;',
      '文献数值须来自原文并保留 context 可溯源, 不做推断或估计;',
      '对外披露的通过率采用统一 ±10% 口径, 不使用各用例自带的宽容差(部分达 25~50%)。'
    ].join(' ')
  },
  scale: {
    unique_verified_dois: dois.size,
    validation_cases: (corpus.validation_cases || []).length,
    literature_benchmarks: (corpus.literature_benchmarks || []).length,
    reference_library_papers: (refLib.references || []).length,
    mass_reference_library_papers: (massLib.references || []).length,
    engine_references: (engineRefs.refs || []).length,
    extracted_metrics_from_abstracts: extractedMetrics + massMetrics,
    papers_with_extracted_values: massPapersWithValues,
    engines_covered_by_literature: Object.keys(engineCoverage).length
  },
  validation_result: deviation.pass_rate ? {
    total_cases: deviation.total_cases,
    measured_against: '线上生产 API (swarmlabs.tools)',
    pass_rate_unified_10pct: deviation.pass_rate.unified_10pct,
    pass_rate_unified_5pct: deviation.pass_rate.unified_5pct,
    deviation_distribution: deviation.distribution,
    remaining_improvement_targets: (deviation.improvement_targets || []).length
  } : null,
  coverage_by_domain: (massLib.stats && massLib.stats.by_domain) || (refLib.stats && refLib.stats.by_domain) || null,
  coverage_by_engine: engineCoverage,
  sources: {
    validation_corpus: 'data/doi-validation-corpus.json',
    reference_library: 'data/doi-reference-library.json',
    mass_reference_library: 'data/doi-reference-library-mass.json',
    literature_benchmarks_extracted: 'data/literature-benchmarks-extracted.json',
    engine_references: 'data/engine-refs-rebuilt.json',
    deviation_report: 'data/deviation-report.json',
    verification_reports: ['data/doi-verification-report.json', 'data/engine-ref-verification.json'],
    quarantine: 'data/doi-quarantine.json'
  },
  all_verified_dois: [...dois].sort()
};

fs.writeFileSync(D('validation-database.json'), JSON.stringify(db, null, 2));
console.log('=== SwarmLabs 验证数据库总账 ===');
console.log(JSON.stringify(db.scale, null, 1));
if (db.validation_result) {
  console.log('\n实测通过率(±10% 统一口径):', db.validation_result.pass_rate_unified_10pct.rate,
    `(${db.validation_result.pass_rate_unified_10pct.passed}/${db.validation_result.total_cases})`);
}
console.log('\n唯一真实 DOI 总数:', dois.size);
console.log('文献覆盖引擎数  :', Object.keys(engineCoverage).length);
console.log('写入: data/validation-database.json');
