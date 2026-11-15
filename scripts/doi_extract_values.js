#!/usr/bin/env node
/**
 * doi_extract_values.js — 从 CrossRef 摘要中抽取可比对的定量指标。
 *
 * 诚实约束:
 *  - 数值必须来自论文摘要原文，同时保存 context 原句，供人工/后续复核；
 *  - 抽不到数值的条目保持 value_extracted=false，不进入基准统计；
 *  - 不做任何推断、插值或"合理估计"。
 */
const fs = require('fs');
const path = require('path');

const LIB = path.join(__dirname, '..', 'data', 'doi-reference-library.json');
const MAILTO = 'ops@swarmlabs.tools';

// 相对提升/对比语境 —— 这类数值不是绝对指标，必须剔除
const RELATIVE_CTX = /higher than|lower than|increase[sd]?\s+by|improve[sd]?\s+by|enhanc\w+\s+by|reduc\w+\s+by|decreas\w+\s+by|compared\s+(?:to|with)|relative to|times (?:higher|greater)|vs\.?\s/i;

// 各域关注的指标模式: [指标名, 正则, 单位, 语境关键词]
// 注: 百分比用 (?<![\d.]) 前瞻，避免从 "100%" 里截出 "00"
const PATTERNS = [
  ['efficiency_pct', /(?<![\d.])(\d{1,3}(?:\.\d{1,2})?)\s*%/gi, '%', /efficien|pce|conversion|retention|yield|removal|rejection|recovery|purity|selectivit/i],
  ['capacity_mahg', /(\d{2,4}(?:\.\d+)?)\s*mA\s*h[·\s]*g/gi, 'mAh/g', /capacit/i],
  ['power_density', /(\d+(?:\.\d+)?)\s*(?:mW|W)\s*(?:cm|\/cm)/gi, 'W/cm2', /power densit/i],
  ['current_density', /(\d+(?:\.\d+)?)\s*mA\s*(?:cm|\/cm)/gi, 'mA/cm2', /current densit/i],
  ['overpotential_mv', /(\d{2,4}(?:\.\d+)?)\s*mV/gi, 'mV', /overpotential|tafel/i],
  ['surface_area', /(\d{3,4}(?:\.\d+)?)\s*m2?\s*(?:\/|·)?\s*g/gi, 'm2/g', /surface area|bet/i],
  ['flux_lmh', /(\d+(?:\.\d+)?)\s*L\s*(?:m|\/m)/gi, 'L/m2h', /flux|permeance|permeabilit/i],
  ['rate_mmol', /(\d+(?:\.\d+)?)\s*(?:m|μ|u)mol/gi, 'mmol', /evolution|production|rate|h2|hydrogen/i],
  ['temperature_c', /(\d{2,4}(?:\.\d+)?)\s*°?\s*C\b/gi, 'degC', /temperature|sinter|anneal|calcin/i],
  ['release_exponent', /n\s*=\s*(0\.\d+)/gi, 'n', /peppas|release exponent|diffusion/i],
  ['drag_coefficient', /C\s*(?:d|D)\s*(?:=|of)\s*(\d(?:\.\d+)?)/gi, 'Cd', /drag/i],
  ['strouhal', /St\s*(?:=|of)\s*(0\.\d+)/gi, 'St', /strouhal|shedding/i]
];

function stripJats(s) {
  if (!s) return '';
  return String(s)
    .replace(/<[^>]+>/g, ' ')
    .replace(/&lt;[^&]*&gt;/g, ' ')
    .replace(/&amp;/g, '&')
    .replace(/&[a-z]+;/gi, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

async function fetchAbstract(doi) {
  try {
    const r = await fetch(`https://api.crossref.org/works/${encodeURIComponent(doi)}?mailto=${MAILTO}`, {
      headers: { 'User-Agent': `SwarmLabs/1.0 (mailto:${MAILTO})` }
    });
    if (!r.ok) return null;
    const j = await r.json();
    return stripJats(j.message && j.message.abstract);
  } catch { return null; }
}

function extract(text) {
  if (!text || text.length < 40) return [];
  const found = [];
  for (const [metric, re, unit, ctxRe] of PATTERNS) {
    re.lastIndex = 0;
    let m;
    while ((m = re.exec(text)) !== null) {
      const start = Math.max(0, m.index - 90);
      const ctx = text.slice(start, Math.min(text.length, m.index + 90));
      if (!ctxRe.test(ctx)) continue;

      // 剔除"相对提升/对比"语境: 只看数值紧邻的 ±45 字符
      const near = text.slice(Math.max(0, m.index - 45), Math.min(text.length, m.index + 45));
      if (RELATIVE_CTX.test(near)) continue;

      const val = parseFloat(m[1]);
      if (!isFinite(val)) continue;
      // 值域合理性检查
      if (metric === 'efficiency_pct' && (val <= 0.5 || val > 100)) continue;
      if (metric === 'release_exponent' && (val <= 0 || val >= 1)) continue;
      if (metric === 'capacity_mahg' && (val < 10 || val > 4000)) continue;
      if (found.some(f => f.metric === metric && f.value === val)) continue;

      // 置信度: 语境关键词紧邻数值 = high，仅出现在宽窗口 = low
      const confidence = ctxRe.test(near) ? 'high' : 'low';
      found.push({ metric, value: val, unit, confidence, context: ctx.trim() });
      if (found.filter(f => f.metric === metric).length >= 2) break;
    }
  }
  return found;
}

(async () => {
  const lib = JSON.parse(fs.readFileSync(LIB, 'utf8'));
  let withVal = 0, noAbs = 0;

  for (let i = 0; i < lib.references.length; i++) {
    const r = lib.references[i];
    // 重跑时先复位，避免残留旧抽取结果
    delete r.extracted_metrics;
    r.value_extracted = false;
    const abs = await fetchAbstract(r.doi);
    if (!abs) { noAbs++; r.abstract_available = false; }
    else {
      r.abstract_available = true;
      const metrics = extract(abs);
      if (metrics.length) {
        r.extracted_metrics = metrics;
        r.value_extracted = true;
        r.note = '数值由论文摘要原文正则抽取，附 context 原句可溯源；未经人工复核前作为候选基准。';
        withVal++;
      }
    }
    if ((i + 1) % 20 === 0) console.log(`  进度 ${i + 1}/${lib.references.length} …`);
    await new Promise(s => setTimeout(s, 200));
  }

  const domainWith = {};
  lib.references.filter(r => r.value_extracted).forEach(r => { domainWith[r.domain] = (domainWith[r.domain] || 0) + 1; });
  const totalMetrics = lib.references.reduce((s, r) => s + ((r.extracted_metrics || []).length), 0);
  const highConf = lib.references.reduce((s, r) => s + ((r.extracted_metrics || []).filter(m => m.confidence === 'high').length), 0);

  lib.stats = {
    ...lib.stats,
    with_extracted_values: withVal,
    without_abstract: noAbs,
    extracted_metrics_total: totalMetrics,
    extracted_metrics_high_confidence: highConf,
    extracted_by_domain: domainWith
  };
  fs.writeFileSync(LIB, JSON.stringify(lib, null, 2));
  console.log(`\n抽取完成: ${withVal}/${lib.references.length} 篇抽到指标, 共 ${totalMetrics} 个数值 (高置信 ${highConf}), ${noAbs} 篇无摘要`);
  console.log('按域:', JSON.stringify(domainWith));
})();
