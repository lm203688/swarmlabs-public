// 注入负样本库常量 + GET /api/v2/negative-samples 与 {engine} 端点
// 幂等；带备份 + node --check。
const fs = require('fs');
const path = require('path');
const cp = require('child_process');

const ROOT = 'C:/Users/xing/Desktop/swarmlabs';
const TARGET = path.join(ROOT, 'frontend_fixed/functions/api/[[path]].js');
const NEG = path.join(ROOT, 'data/negative-samples.json');

const neg = JSON.parse(fs.readFileSync(NEG, 'utf8'));
// 紧凑化：{d:doi, e:engine, s:signal_type, c:context, y:year, j:journal}
const compact = neg.samples.map(x => ({ d: x.doi, e: x.engine, s: x.signal_type, c: x.context, y: x.year, j: x.journal }));
const bySignal = neg.stats.by_signal;

const bak = TARGET + '.bak-neg-' + Date.now();
fs.copyFileSync(TARGET, bak);
console.log('备份 ->', path.basename(bak));

let s = fs.readFileSync(TARGET, 'utf8');

// 1) 幂等 strip 旧 constBlock
const CSTART = '/* ===== SWARMLABS_NEGATIVE_START ===== */';
const CEND = '/* ===== SWARMLABS_NEGATIVE_END ===== */';
const cre = new RegExp(CSTART + '[\\s\\S]*?' + CEND + '\\n?');
if (cre.test(s)) { s = s.replace(cre, ''); console.log('已 strip 旧 constBlock'); }

// 2) 注入顶层常量（在 CONFIDENCE_END 之后）
const confEnd = '/* ===== SWARMLABS_CONFIDENCE_END ===== */';
const ci = s.indexOf(confEnd);
if (ci < 0) { console.error('找不到 CONFIDENCE_END 锚点'); process.exit(1); }
const cle = s.indexOf('\n', ci) + 1;
const constBlock = CSTART + '\n' +
'// 负样本/失败案例库（平台版"80% 负样本"）—— 来自公开文献摘要显式负信号，句子级溯源\n' +
'const NEGATIVE_SAMPLES = ' + JSON.stringify(compact) + ';\n' +
'const NEGATIVE_SIGNAL_COUNTS = ' + JSON.stringify(bySignal) + ';\n' +
'function getNegativeSamples(engine) {\n' +
'  if (!engine) return NEGATIVE_SAMPLES;\n' +
'  return NEGATIVE_SAMPLES.filter(function (x) { return x.e === engine; });\n' +
'}\n' +
CEND + '\n';
s = s.slice(0, cle) + constBlock + s.slice(cle);

// 3) 注入路由（在 LITERATURE_ROUTE_END 之后；幂等）
const RSTART = '/* ===== SWARMLABS_NEGATIVE_ROUTE_START ===== */';
const REND = '/* ===== SWARMLABS_NEGATIVE_ROUTE_END ===== */';
const rre = new RegExp(RSTART + '[\\s\\S]*?' + REND + '\\n?');
if (rre.test(s)) { s = s.replace(rre, ''); console.log('已 strip 旧路由'); }

const litEnd = '/* ===== SWARMLABS_LITERATURE_ROUTE_END ===== */';
const li = s.indexOf(litEnd);
if (li < 0) { console.error('找不到 LITERATURE_ROUTE_END 锚点'); process.exit(1); }
const lle = s.indexOf('\n', li) + 1;
const routeBlock = RSTART + '\n' +
'  // === 负样本/失败案例查询（平台版"80% 负样本"，诚实披露失败案例）===\n' +
"  if (apiPath === 'v2/negative-samples' || apiPath.startsWith('v2/negative-samples/')) {\n" +
'    const parts = apiPath.split("/");\n' +
"    const eng = parts.length > 2 ? decodeURIComponent(parts[2]) : null;\n" +
'    if (eng) {\n' +
'      const list = getNegativeSamples(eng);\n' +
'      return new Response(JSON.stringify({\n' +
'        engine: eng, count: list.length, samples: list.map(function (x) {\n' +
"          return { doi: x.d, url: 'https://doi.org/' + x.d, engine: x.e, signal_type: x.s, journal: x.j, year: x.y, context: x.c };\n" +
'        })\n' +
'      }), { headers: CORS_HEADERS });\n' +
'    }\n' +
'    return new Response(JSON.stringify({\n' +
'      total: NEGATIVE_SAMPLES.length,\n' +
'      by_signal: NEGATIVE_SIGNAL_COUNTS,\n' +
'      engines_covered: (function () { const m = {}; NEGATIVE_SAMPLES.forEach(function (x) { m[x.e] = 1; }); return Object.keys(m).length; })(),\n' +
'      samples: NEGATIVE_SAMPLES.map(function (x) {\n' +
"        return { doi: x.d, url: 'https://doi.org/' + x.d, engine: x.e, signal_type: x.s, journal: x.j, year: x.y, context: x.c };\n" +
'      })\n' +
'    }), { headers: CORS_HEADERS });\n' +
'  }\n' +
REND + '\n';
s = s.slice(0, lle) + routeBlock + s.slice(lle);

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
