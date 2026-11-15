// 注入 GET /api/v2/account 端点（复用现有 Bearer token 体系返回余额/分级，支撑计量与商业化）
// 幂等；备份 + node --check。
const fs = require('fs');
const path = require('path');
const cp = require('child_process');

const ROOT = 'C:/Users/xing/Desktop/swarmlabs';
const TARGET = path.join(ROOT, 'frontend_fixed/functions/api/[[path]].js');

const bak = TARGET + '.bak-account-' + Date.now();
fs.copyFileSync(TARGET, bak);
console.log('备份 ->', path.basename(bak));

let s = fs.readFileSync(TARGET, 'utf8');

const START = '/* ===== SWARMLABS_ACCOUNT_ROUTE_START ===== */';
const END = '/* ===== SWARMLABS_ACCOUNT_ROUTE_END ===== */';
const re = new RegExp(START + '[\\s\\S]*?' + END + '\\n?');
if (re.test(s)) { s = s.replace(re, ''); console.log('已 strip 旧 account 路由'); }

const entEnd = '/* ===== SWARMLABS_ENTERPRISE_ROUTE_END ===== */';
const ei = s.indexOf(entEnd);
if (ei < 0) { console.error('找不到 ENTERPRISE_ROUTE_END 锚点'); process.exit(1); }
const ele = s.indexOf('\n', ei) + 1;

const block = START + '\n' +
"  if (apiPath === 'v2/account' || apiPath === 'v2/me') {\n" +
'    const userToken = extractUserToken(request);\n' +
'    if (!userToken) {\n' +
'      return new Response(JSON.stringify({ authenticated: false, message: "提供 Authorization: Bearer <token> 查看余额与分级", tiers: "见 /api/v2/enterprise" }), { headers: CORS_HEADERS });\n' +
'    }\n' +
'    try {\n' +
'      const user = await getUserByTokenAsync(userToken, env);\n' +
'      if (!user) {\n' +
'        return new Response(JSON.stringify({ authenticated: false, error: "invalid_or_expired_token", login_url: "/login" }), { status: 401, headers: CORS_HEADERS });\n' +
'      }\n' +
'      const tier = user.credits >= 10000 ? "pro" : (user.credits > 0 ? "free" : "free");\n' +
'      return new Response(JSON.stringify({\n' +
'        authenticated: true, email: user.email, credits_balance: user.credits, tier,\n' +
'        usage_tip: "每次 POST /api/v2/run/{engine} 消耗积分；参数扫描 /api/v2/sweep 免费用于趋势分析",\n' +
'        status: "success"\n' +
'      }), { headers: CORS_HEADERS });\n' +
'    } catch (e) {\n' +
'      return new Response(JSON.stringify({ error: "account_failed", detail: e.message }), { status: 400, headers: CORS_HEADERS });\n' +
'    }\n' +
'  }\n' +
END + '\n';

s = s.slice(0, ele) + block + s.slice(ele);

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
