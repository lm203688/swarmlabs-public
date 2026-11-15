#!/usr/bin/env node
/**
 * doi_extract_mass.js — 从大规模文献库摘要中抽取可比对定量指标。
 *
 * 与 doi_extract_values.js 的区别:
 *  - 作用于 doi-reference-library-mass.json（采集时已内嵌 abstract，无需二次请求 CrossRef）；
 *  - 指标模式扩充到 24 类，覆盖平台全部 13 个领域；
 *  - 三级置信度: high（绝对指标 + 强语境）/ medium（弱语境）/ low（可能有歧义）。
 *
 * 诚实约束（与前一致，不可违背）:
 *  - 数值必须逐字来自摘要原文，同时保存 context 原句供复核；
 *  - 抽不到数值 → value_extracted=false，不计入基准统计；
 *  - 不做任何推断、插值或"合理估计"。
 */
const fs = require('fs');
const path = require('path');

const LIB = path.join(__dirname, '..', 'data', 'doi-reference-library-mass.json');
const OUT = path.join(__dirname, '..', 'data', 'literature-benchmarks-extracted.json');

// 相对提升/对比语境 —— 这类数值不是绝对指标，必须剔除
const RELATIVE_CTX = /higher than|lower than|greater than|increase[sd]?\s+by|improve[sd]?\s+by|enhanc\w+\s+by|reduc\w+\s+by|decreas\w+\s+by|compared\s+(?:to|with)|relative to|times (?:higher|greater|larger)|vs\.?\s|than that of|over that of/i;

// 「improved/increased … by X%」型相对提升: 动词与数值间可能隔多个词，
// 故单独判定「数值紧前是 by」+「更宽上下文含增减类动词」。
const BY_PREFIX = /\bby\s*$/i;
const REL_VERB = /improv|increas|enhanc|reduc|decreas|declin|boost|raise[sd]?|drop(?:ped)?|gain|outperform/i;

// 不确定语境 —— 降级为 low confidence
const VAGUE_CTX = /approximately|about|around|nearly|almost|up to|as (?:high|much) as|more than|less than|over\s+\d|below|exceed/i;

// 范围语境 —— 数值是区间端点，标记但降级（含 "between A and B" 写法）
const RANGE_CTX = /\d\s*(?:-|–|—|to|and)\s*\d|between\s+\d|ranged?\s+(?:from|between)/i;

// 非指标语境 —— 误差/偏差/统计量被误当成效率，必须整条剔除
const NON_METRIC_CTX = /\berrors?\b|deviation|uncertaint|\brsd\b|tolerance|accuracy of|precision of|confidence interval|standard error|r\s*2\s*=|r²|p\s*[<>]\s*0\./i;

// 界限语境 —— "less than X%" 是上界声明而非测量值，剔除
const BOUND_CTX = /(?:far\s+)?(?:less|lower|smaller|fewer)\s+than\s*$|below\s*$|under\s*$|no\s+more\s+than\s*$|at\s+most\s*$|within\s*$/i;

// [指标名, 正则, 单位, 语境关键词, 值域[min,max]]
const PATTERNS = [
  // 通用百分比类
  ['efficiency_pct',    /(?<![\d.])(\d{1,3}(?:\.\d{1,2})?)\s*%/gi,                    '%',        /efficien|pce|conversion|retention|yield|removal|rejection|recovery|purity|selectivit|degradation|encapsulation|loading/i, [0.5, 100]],
  // 电化学 / 储能
  ['capacity_mahg',     /(\d{2,4}(?:\.\d+)?)\s*mA\s*h[·\s]*(?:\/)?\s*g/gi,            'mAh/g',    /capacit|discharge|charge/i,                    [10, 4000]],
  ['power_density',     /(\d+(?:\.\d+)?)\s*(?:mW|W)\s*(?:\/)?\s*cm/gi,                'W/cm2',    /power densit/i,                                [0.001, 10000]],
  ['current_density',   /(\d+(?:\.\d+)?)\s*mA\s*(?:\/)?\s*cm/gi,                      'mA/cm2',   /current densit/i,                              [0.01, 10000]],
  ['overpotential_mv',  /(\d{2,4}(?:\.\d+)?)\s*mV/gi,                                 'mV',       /overpotential|tafel|onset/i,                   [10, 2000]],
  ['energy_density',    /(\d{2,4}(?:\.\d+)?)\s*Wh\s*(?:\/)?\s*(?:kg|L)/gi,            'Wh/kg',    /energy densit/i,                               [5, 2000]],
  ['coulombic_pct',     /coulombic efficiency of\s*(\d{2}(?:\.\d+)?)\s*%/gi,          '%',        /coulombic/i,                                   [50, 100]],
  // 材料
  ['surface_area',      /(\d{2,4}(?:\.\d+)?)\s*m\s*(?:2|²)?\s*(?:\/|·)?\s*g/gi,       'm2/g',     /surface area|bet|specific area/i,              [1, 8000]],
  ['tensile_mpa',       /(\d{1,4}(?:\.\d+)?)\s*MPa/gi,                                'MPa',      /tensile|strength|stress|modulus|yield strength/i, [0.1, 6000]],
  ['modulus_gpa',       /(\d{1,4}(?:\.\d+)?)\s*GPa/gi,                                'GPa',      /modulus|young|stiffness|elastic/i,             [0.01, 1200]],
  ['hardness_hv',       /(\d{2,4}(?:\.\d+)?)\s*HV/gi,                                 'HV',       /hardness|vickers/i,                            [10, 3000]],
  ['conductivity_wmk',  /(\d+(?:\.\d+)?)\s*W\s*(?:\/|·)?\s*(?:m|\(m)/gi,              'W/mK',     /thermal conductivit/i,                         [0.01, 3000]],
  ['grain_size_nm',     /(\d{1,4}(?:\.\d+)?)\s*nm/gi,                                 'nm',       /grain size|crystallite|particle size|thickness/i, [0.5, 5000]],
  // 分离 / 膜
  ['flux_lmh',          /(\d+(?:\.\d+)?)\s*L\s*(?:m|\/m)/gi,                          'L/m2h',    /flux|permeance|permeabilit/i,                  [0.01, 10000]],
  ['rejection_pct',     /rejection of\s*(\d{1,3}(?:\.\d+)?)\s*%/gi,                   '%',        /rejection|retention/i,                         [1, 100]],
  ['adsorption_mgg',    /(\d{1,4}(?:\.\d+)?)\s*mg\s*(?:\/|·)?\s*g/gi,                 'mg/g',     /adsorption|capacit|uptake|sorption/i,          [0.1, 5000]],
  // 化学 / 催化
  ['rate_mmol',         /(\d+(?:\.\d+)?)\s*(?:m|μ|u)mol/gi,                           'mmol',     /evolution|production|rate|h2|hydrogen|turnover/i, [0.001, 100000]],
  ['tof_h',             /(\d+(?:\.\d+)?)\s*h\s*(?:-1|−1|\^-1)/gi,                     '1/h',      /turnover frequency|tof/i,                      [0.01, 1e6]],
  ['activation_energy', /(\d{1,3}(?:\.\d+)?)\s*kJ\s*(?:\/|·)?\s*mol/gi,               'kJ/mol',   /activation energy|barrier|ea\b/i,              [1, 500]],
  ['temperature_c',     /(\d{2,4}(?:\.\d+)?)\s*°?\s*C\b/gi,                           'degC',     /temperature|sinter|anneal|calcin|reaction at|operated at/i, [-100, 3000]],
  // 药剂
  ['release_exponent',  /n\s*=\s*(0\.\d+)/gi,                                         'n',        /peppas|release exponent|diffusion|korsmeyer/i, [0.01, 0.999]],
  ['dissolution_pct',   /(\d{1,3}(?:\.\d+)?)\s*%\s*(?:of\s*)?(?:drug\s*)?(?:was\s*)?released/gi, '%', /release|dissolution/i,                     [0.5, 100]],
  // CFD
  ['drag_coefficient',  /C\s*(?:d|D)\s*(?:=|of|was)\s*(\d(?:\.\d+)?)/gi,              'Cd',       /drag/i,                                        [0.001, 5]],
  ['strouhal',          /St\s*(?:=|of|was)\s*(0\.\d+)/gi,                             'St',       /strouhal|shedding/i,                           [0.01, 1]],
  ['nusselt',           /Nu\s*(?:=|of|was)\s*(\d{1,4}(?:\.\d+)?)/gi,                  'Nu',       /nusselt|heat transfer/i,                       [0.1, 10000]],
  ['reynolds',          /Re\s*(?:=|of)\s*(\d{2,7})/gi,                                'Re',       /reynolds/i,                                    [1, 1e7]],
];

function extract(text) {
  if (!text || text.length < 40) return [];
  const found = [];
  for (const [metric, re, unit, ctxRe, range] of PATTERNS) {
    re.lastIndex = 0;
    let m;
    while ((m = re.exec(text)) !== null) {
      const start = Math.max(0, m.index - 100);
      const ctx = text.slice(start, Math.min(text.length, m.index + 100));
      if (!ctxRe.test(ctx)) continue;

      // 剔除"相对提升/对比"语境: 只看数值紧邻的 ±50 字符
      const near = text.slice(Math.max(0, m.index - 50), Math.min(text.length, m.index + 50));
      if (RELATIVE_CTX.test(near)) continue;

      // 剔除「improved … by X%」型相对提升（动词与数值可相隔多词）
      const justBefore = text.slice(Math.max(0, m.index - 8), m.index);
      const wideBefore = text.slice(Math.max(0, m.index - 150), m.index);
      if (BY_PREFIX.test(justBefore) && REL_VERB.test(wideBefore)) continue;

      // 剔除误差/偏差/统计量语境（"relative error of less than 5%" 不是效率）
      if (NON_METRIC_CTX.test(near)) continue;

      // 剔除上界声明（"far less than 10%" 是界限而非测量值）
      const bound30 = text.slice(Math.max(0, m.index - 30), m.index);
      if (BOUND_CTX.test(bound30)) continue;

      const val = parseFloat(m[1]);
      if (!isFinite(val)) continue;
      if (range && (val < range[0] || val > range[1])) continue;
      if (found.some(f => f.metric === metric && f.value === val)) continue;

      // 置信度分级
      let confidence = 'high';
      if (VAGUE_CTX.test(near)) confidence = 'medium';
      if (RANGE_CTX.test(near)) confidence = 'low';

      found.push({
        metric,
        value: val,
        unit,
        confidence,
        context: ctx.replace(/\s+/g, ' ').trim(),
      });
      if (found.length >= 12) break;
    }
  }
  return found;
}

// ---------------------------------------------------------------- 主流程
(function main() {
  if (!fs.existsSync(LIB)) {
    console.error(`[extract] 找不到 ${LIB}，请先运行 doi_harvest_mass.js`);
    process.exit(1);
  }
  const lib = JSON.parse(fs.readFileSync(LIB, 'utf8'));
  const refs = lib.references || [];
  console.log(`[extract] 载入 ${refs.length} 篇文献`);

  let hit = 0, metricsTotal = 0;
  const conf = { high: 0, medium: 0, low: 0 };
  const byDomain = {};
  const byMetric = {};

  for (const r of refs) {
    // 复位旧结果，保证可重复执行
    r.value_extracted = false;
    delete r.extracted_values;

    const vals = extract(r.abstract);
    if (!vals.length) continue;

    r.value_extracted = true;
    r.extracted_values = vals;
    hit++;
    metricsTotal += vals.length;
    byDomain[r.domain] = (byDomain[r.domain] || 0) + 1;
    for (const v of vals) {
      conf[v.confidence]++;
      byMetric[v.metric] = (byMetric[v.metric] || 0) + 1;
    }
  }

  lib.stats = lib.stats || {};
  lib.stats.with_extracted_values = hit;
  lib.stats.extracted_metrics_total = metricsTotal;
  lib.stats.extracted_confidence = conf;
  lib.stats.extracted_by_domain = byDomain;
  lib.stats.extracted_by_metric = byMetric;
  fs.writeFileSync(LIB, JSON.stringify(lib, null, 1));

  // 单独导出"可比对基准"子集（仅 high/medium 置信度）
  const benchmarks = [];
  for (const r of refs) {
    if (!r.value_extracted) continue;
    const usable = r.extracted_values.filter(v => v.confidence !== 'low');
    if (!usable.length) continue;
    benchmarks.push({
      doi: r.doi,
      engine: r.engine,
      domain: r.domain,
      title: r.title,
      journal: r.journal,
      year: r.year,
      citations: r.citations,
      source: 'CrossRef abstract (machine-extracted, context preserved)',
      metrics: usable,
    });
  }
  fs.writeFileSync(OUT, JSON.stringify({
    meta: {
      generated_at: new Date().toISOString(),
      principle: '数值逐字取自 CrossRef 摘要原文并保留 context 原句；low 置信度（区间端点）已排除；未经人工编造或推断。',
      confidence_note: 'high=绝对指标强语境; medium=含 approximately/up to 等模糊词。二者均可溯源至原文。',
    },
    stats: {
      papers: benchmarks.length,
      metrics: benchmarks.reduce((s, b) => s + b.metrics.length, 0),
      by_domain: benchmarks.reduce((a, b) => (a[b.domain] = (a[b.domain] || 0) + 1, a), {}),
    },
    benchmarks,
  }, null, 1));

  console.log('\n========== 抽取完成 ==========');
  console.log(`命中文献   : ${hit}/${refs.length} (${(hit / refs.length * 100).toFixed(1)}%)`);
  console.log(`指标总数   : ${metricsTotal}`);
  console.log(`置信度     : high=${conf.high}  medium=${conf.medium}  low=${conf.low}`);
  console.log(`可比对基准 : ${benchmarks.length} 篇 / ${benchmarks.reduce((s, b) => s + b.metrics.length, 0)} 个指标`);
  console.log('\n按指标类型 (Top 12):');
  for (const [k, v] of Object.entries(byMetric).sort((a, b) => b[1] - a[1]).slice(0, 12)) {
    console.log(`  ${k.padEnd(20)} ${v}`);
  }
  console.log(`\n输出: ${OUT}`);
})();
