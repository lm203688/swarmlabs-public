// 注入"数据驱动的分级 + 单点UQ"增强块到 [[path]].js（全程幂等 + 容错）
// 读取 validation-database.json 的 coverage_by_engine（真实文献覆盖数）作为硬依据
const fs = require('fs');
const path = require('path');
const cp = require('child_process');

const ROOT = 'C:/Users/xing/Desktop/swarmlabs';
const TARGET = path.join(ROOT, 'frontend_fixed/functions/api/[[path]].js');
const VALDB = path.join(ROOT, 'data/validation-database.json');

const valdb = JSON.parse(fs.readFileSync(VALDB, 'utf8'));
const coverage = valdb.coverage_by_engine || {};
const covObj = {};
for (const [k, v] of Object.entries(coverage)) covObj[k] = (typeof v === 'number') ? v : (v.count || 0);
const covJson = JSON.stringify(covObj);

const bak = TARGET + '.bak-grade-' + Date.now();
fs.copyFileSync(TARGET, bak);
console.log('备份 ->', path.basename(bak));

let s = fs.readFileSync(TARGET, 'utf8');

// 1) 幂等 strip 顶层 constBlock
const START = '/* ===== SWARMLABS_CONFIDENCE_START ===== */';
const END = '/* ===== SWARMLABS_CONFIDENCE_END ===== */';
const re = new RegExp(START + '[\\s\\S]*?' + END + '\\n?');
if (re.test(s)) { s = s.replace(re, ''); console.log('已 strip 旧 constBlock'); }

// 2) 注入顶层 constBlock（在 ENGINE_LITERATURE_END 之后）
const libEndAnchor = '/* ===== SWARMLABS_ENGINE_LITERATURE_END ===== */';
const insIdx = s.indexOf(libEndAnchor);
if (insIdx < 0) { console.error('找不到 ENGINE_LITERATURE_END 锚点'); process.exit(1); }
const anchorLineEnd = s.indexOf('\n', insIdx) + 1;
const constBlock = START + '\n' +
'// 真实文献覆盖数（来自 data/validation-database.json.coverage_by_engine，机器校验 DOI）\n' +
'const ENGINE_COVERAGE = ' + covJson + ';\n' +
'function getEngineCoverage(e){ return ENGINE_COVERAGE[e] || 0; }\n' +
"function getConfidenceGrade(e){\n  const c = getEngineCoverage(e);\n  if (c >= 6) return 'A';\n  if (c >= 3) return 'B';\n  return 'C';\n}\n" +
"function confidenceRecommendation(grade){\n" +
"  if (grade === 'A') return '高置信：可直接引用，请保留文献溯源(literature_support)并标注 DOI';\n" +
"  if (grade === 'B') return '中等置信：建议与独立实验或原始文献交叉验证后再采信';\n" +
"  return '低置信：必须实验复核后方可采信，勿直接用于关键决策';\n" +
'}\n' +
'function computeDataUnc(engine, epistemic){\n' +
'  const c = getEngineCoverage(engine);\n' +
'  const base = Math.max(4, Math.min(14, 12 - c * 0.8));\n' +
'  return Math.round((base + epistemic * 0.2) * 100) / 100;\n' +
'}\n' +
'function empiricalUncertaintyPct(engine){\n' +
'  const c = getEngineCoverage(engine);\n' +
'  return Math.round(Math.max(4, Math.min(14, 12 - c * 0.8)) * 100) / 100;\n' +
'}\n' +
END + '\n';
s = s.slice(0, anchorLineEnd) + constBlock + s.slice(anchorLineEnd);

// 3) 注入 grade 计算（/physicsPredict 内，combinedUnc 之后；幂等）
if (!s.includes('const cov = getEngineCoverage(engine);')) {
  const combAnchor = 'const combinedUnc = round2(Math.sqrt(epistemic * epistemic + aleatoric * aleatoric));';
  if (!s.includes(combAnchor)) { console.error('找不到 combinedUnc 锚点'); process.exit(1); }
  s = s.replace(combAnchor, combAnchor + '\n' +
    "  const cov = getEngineCoverage(engine);\n" +
    "  const grade = getConfidenceGrade(engine);\n" +
    '  const dataUnc = computeDataUnc(engine, epistemic);\n');
  console.log('已注入 grade 计算');
}

// 4) uncertainty 字段（容错：combinedUnc 或 dataUnc 均可）
if (!/uncertainty:\s*dataUnc/.test(s)) {
  s = s.replace(/[ \t]*uncertainty:\s*(combinedUnc|dataUnc),[^\n]*/, '    uncertainty: dataUnc, // [GRADE-01] 数据驱动单点 UQ（覆盖深度+外推），向后兼容字段名');
  console.log('已更新 uncertainty');
}

// 5) confidence + confidence_grade + recommendation + empirical（幂等）
if (!s.includes('confidence_grade: grade')) {
  const oldConf = "    confidence: round2(clamp(1 - combinedUnc / 100 * 2 - 0.02 * Math.max(0, ((params.temperature_C || 80) - 120) / 30), 0.75, 0.98)),";
  if (!s.includes(oldConf)) { console.error('找不到 confidence 原锚点'); process.exit(1); }
  const newConf =
    "    confidence: round2(clamp(0.99 - (grade === 'C' ? 0.18 : grade === 'B' ? 0.07 : 0) - epistemic / 100, 0.7, 0.99)),\n" +
    "    confidence_grade: grade, // [GRADE-01] A/B/C 数据驱动分级\n" +
    "    recommendation: confidenceRecommendation(grade), // [GRADE-01] 弱引擎实验复核建议\n" +
    "    empirical_uncertainty_pct: empiricalUncertaintyPct(engine), // [GRADE-01] 覆盖深度驱动经验不确定度";
  s = s.replace(oldConf, newConf);
  console.log('已更新 confidence 块');
}

// 6) validation_stats 增加 coverage + grade（幂等）
if (!s.includes('confidence_grade: grade\n    }') && !s.includes('confidence_grade: grade // [GRADE-01]\n    }')) {
  const oldVs = "      reliability: stats.rel\n    }";
  const newVs = "      reliability: stats.rel,\n      literature_coverage: cov, // [GRADE-01] 真实文献覆盖数\n      confidence_grade: grade // [GRADE-01]\n    }";
  if (!s.includes(oldVs)) { console.error('找不到 validation_stats 锚点'); process.exit(1); }
  s = s.replace(oldVs, newVs);
  console.log('已更新 validation_stats');
}

fs.writeFileSync(TARGET, s);
console.log('注入完成。文件大小(KB)=', (s.length/1024).toFixed(0));

try {
  cp.execSync('"' + process.execPath + '" --check "' + TARGET + '"', { encoding: 'utf8' });
  console.log('✓ node --check 通过');
} catch (e) {
  console.error('✗ 语法校验失败，回滚');
  fs.copyFileSync(bak, TARGET);
  console.error('STDERR:', e.stderr ? e.stderr.toString() : '(无)');
  process.exit(1);
}
