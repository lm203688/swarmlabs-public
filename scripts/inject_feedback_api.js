// 注入 POST /api/v2/feedback 用户回流端点（数据飞轮地基，P1 主动学习闭环入口）
// 幂等；备份 + node --check。
const fs = require('fs');
const path = require('path');
const cp = require('child_process');

const ROOT = 'C:/Users/xing/Desktop/swarmlabs';
const TARGET = path.join(ROOT, 'frontend_fixed/functions/api/[[path]].js');

const bak = TARGET + '.bak-feedback-' + Date.now();
fs.copyFileSync(TARGET, bak);
console.log('备份 ->', path.basename(bak));

let s = fs.readFileSync(TARGET, 'utf8');

const START = '/* ===== SWARMLABS_FEEDBACK_ROUTE_START ===== */';
const END = '/* ===== SWARMLABS_FEEDBACK_ROUTE_END ===== */';
const re = new RegExp(START + '[\\s\\S]*?' + END + '\\n?');
if (re.test(s)) { s = s.replace(re, ''); console.log('已 strip 旧 feedback 路由'); }

const accEnd = '/* ===== SWARMLABS_ACCOUNT_ROUTE_END ===== */';
const ai = s.indexOf(accEnd);
if (ai < 0) { console.error('找不到 ACCOUNT_ROUTE_END 锚点'); process.exit(1); }
const ale = s.indexOf('\n', ai) + 1;

const block = START + '\n' +
"  // === 用户回流（数据飞轮入口）：接收真实实验结果/纠偏，沉淀平台自有数据 ===\n" +
"  if (apiPath === 'v2/feedback' || apiPath.startsWith('v2/feedback/')) {\n" +
'    try {\n' +
'      const body = await request.clone().text();\n' +
'      const fb = JSON.parse(body || "{}");\n' +
'      if (!fb.engine) {\n' +
'        return new Response(JSON.stringify({ error: "missing_engine" }), { status: 400, headers: CORS_HEADERS });\n' +
'      }\n' +
'      const entry = {\n' +
'        engine: fb.engine, reported_by: fb.reported_by || null,\n' +
'        predicted: fb.predicted !== undefined ? fb.predicted : null,\n' +
'        actual: fb.actual !== undefined ? fb.actual : null,\n' +
'        signal: fb.signal || null, context: fb.context || null,\n' +
'        ts: new Date().toISOString()\n' +
'      };\n' +
'      // 写入 KV（持久化平台自有数据，区别于公开文献库）\n' +
'      if (env && env.SWARMLABS_KV) {\n' +
'        const key = "feedback:" + fb.engine + ":" + Date.now();\n' +
'        await env.SWARMLABS_KV.put(key, JSON.stringify(entry), { expirationTtl: 86400 * 365 });\n' +
'      }\n' +
'      return new Response(JSON.stringify({ accepted: true, note: "已记录，将用于平台自有数据闭环与主动学习", entry }), { headers: CORS_HEADERS });\n' +
'    } catch (e) {\n' +
'      return new Response(JSON.stringify({ error: "feedback_failed", detail: e.message }), { status: 400, headers: CORS_HEADERS });\n' +
'    }\n' +
'  }\n' +
END + '\n';

s = s.slice(0, ale) + block + s.slice(ale);

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
