// 从 mass reference library 抽取"负样本/失败案例"（平台版 80% 负样本）
// 扫描摘要中的负信号词，提取句子级上下文（可溯源，不推断）。
const fs = require('fs');
const path = require('path');
const ROOT = 'C:/Users/xing/Desktop/swarmlabs';

const mass = JSON.parse(fs.readFileSync(path.join(ROOT, 'data/doi-reference-library-mass.json'), 'utf8'));
const refs = mass.references || [];
const bundle = JSON.parse(fs.readFileSync(path.join(ROOT, 'data/engine-literature-bundle.json'), 'utf8'));
// 引擎 -> TOP 文献（用于 api 端回填文献支撑）
const engineDocs = {};
for (const e of Object.keys(bundle)) engineDocs[e] = bundle[e];

// 负信号模式（句级，匹配整句以保留上下文）
const NEG = [
  { type: 'failed', re: /\b(fail(?:s|ed|ure|ing)?|unsuccessful|did not (?:work|succeed|improve))\b/i },
  { type: 'no_improvement', re: /\b(no (?:significant |marked )?improvement|without (?:improvement|effect)|not (?:improve|enhance|increase|yield))\b/i },
  { type: 'low_yield', re: /\b(low (?:yield|efficiency|conversion|activity|performance)|poor (?:catalyst|performance|activity|catalytic)|ineffective|negligible)\b/i },
  { type: 'decreased', re: /\b(decreas(?:e|ed|ing)|declin(?:e|ed|ing)|drop(?:s|ped)?|reduced? (?:to|by|significantly)|loss of)\b/i },
  { type: 'inhibited', re: /\b(inhibit(?:s|ed|ion|ory)?|suppress(?:es|ed|ion)?|block(?:s|ed|age)?|detrimental|adverse)\b/i }
];

function sentences(text) {
  // 简单按 . ! ? 切句（保留分隔符）
  return text.match(/[^.!?]+[.!?]+|\S[^.!?]*$/g) || [text];
}

const out = [];
const seen = new Set();
for (const r of refs) {
  const abs = r.abstract;
  if (!abs) continue;
  const sents = sentences(abs);
  for (const s of sents) {
    for (const sig of NEG) {
      if (sig.re.test(s)) {
        const key = r.doi + '|' + sig.type + '|' + s.trim().slice(0, 60);
        if (seen.has(key)) continue;
        seen.add(key);
        out.push({
          doi: r.doi,
          url: 'https://doi.org/' + r.doi,
          engine: r.engine,
          domain: r.domain,
          title: r.title,
          journal: r.journal,
          year: r.year,
          citations: r.citations,
          signal_type: sig.type,
          context: s.trim().slice(0, 400),
          source: 'abstract'
        });
        break; // 一句只归一个最强信号
      }
    }
  }
}

// 按引擎聚合计数
const byEngine = {};
for (const x of out) byEngine[x.engine] = (byEngine[x.engine] || 0) + 1;
const bySignal = {};
for (const x of out) bySignal[x.signal_type] = (bySignal[x.signal_type] || 0) + 1;

const result = {
  meta: {
    generated_at: new Date().toISOString(),
    purpose: '负样本/失败案例库（平台版"80% 负样本"）—— 从公开文献摘要抽取显式负信号，句子级溯源，不推断数值',
    source: 'data/doi-reference-library-mass.json (1236 篇, 547 含摘要)',
    integrity: '仅抽取摘要中明确出现的失败/低效/无改善/下降/抑制表述；不捏造、不推断；每条附 DOI 与句子上下文'
  },
  stats: {
    total_negative_samples: out.length,
    papers_scanned: refs.filter(r => r.abstract).length,
    by_signal: bySignal,
    engines_with_negatives: Object.keys(byEngine).length,
    top_engines: Object.entries(byEngine).sort((a, b) => b[1] - a[1]).slice(0, 15)
  },
  samples: out
};

fs.writeFileSync(path.join(ROOT, 'data/negative-samples.json'), JSON.stringify(result, null, 2));
console.log('负样本抽取完成:');
console.log('  总负样本:', out.length);
console.log('  扫描论文(含摘要):', result.stats.papers_scanned);
console.log('  信号分布:', JSON.stringify(bySignal));
console.log('  覆盖引擎数:', result.stats.engines_with_negatives);
console.log('  输出 -> data/negative-samples.json');
