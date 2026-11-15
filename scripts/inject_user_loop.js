/* 幂等注入：用户闭环（我的实验 + 积分赚取 + 再应用 + 自带API）
 * 运行：node scripts/inject_user_loop.js
 * 会修改 frontend_fixed/functions/api/[[path]].js
 */
const fs = require('fs');
const FILE = 'C:/Users/xing/Desktop/swarmlabs/frontend_fixed/functions/api/[[path]].js';
let s = fs.readFileSync(FILE, 'utf8');

function insertBefore(anchor, guard, block) {
  if (s.includes(guard)) { console.log('  跳过(已存在):', guard); return; }
  const i = s.indexOf(anchor);
  if (i < 0) throw new Error('anchor not found: ' + anchor);
  s = s.slice(0, i) + block + '\n' + s.slice(i);
  console.log('  插入(before):', anchor);
}
function insertAfterLine(anchor, guard, block) {
  if (s.includes(guard)) { console.log('  跳过(已存在):', guard); return; }
  const i = s.indexOf(anchor);
  if (i < 0) throw new Error('anchor not found: ' + anchor);
  const nl = s.indexOf('\n', i);
  s = s.slice(0, nl + 1) + block + '\n' + s.slice(nl + 1);
  console.log('  插入(after):', anchor);
}

// ===== 1. 顶层函数块（插入到 SWARMLABS_ML_ROUTES 之前）=====
const FUNC_BLOCK = `/* ===== SWARMLABS_USER_LOOP ===== */
const EARN = { feedback_base: 10, feedback_validated: 30, negative: 15, first_engine: 20 };
const BYO_PROVIDERS = {
  openai: 'https://api.openai.com/v1',
  deepseek: 'https://api.deepseek.com',
  qwen: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
  moonshot: 'https://api.moonshot.cn/v1',
  ollama: 'local',
  custom: 'custom'
};
function maskKey(k){ if(!k) return ''; if(k.length<=8) return '••••'; return k.slice(0,4)+'••••'+k.slice(-4); }
function domainMask(email){ if(!email||!email.includes('@')) return '***'; const parts=email.split('@'); const u=parts[0]||''; return (u.slice(0,2)||'')+'***@'+(parts[1]||''); }

// 再应用：把用户已验证实验注入目标引擎GP作为训练点 → 生成下一建议
async function reapplySuggest(engine, env, extras) {
  const surr = await buildSurrogate(engine, env);
  if (!surr.ok) return { engine, ok: false, reason: surr.reason };
  const X = surr.X.slice(), y = surr.y.slice(), sigma = surr.sigma.slice();
  let injected = 0;
  for (const e of (extras||[])) {
    if (!e || !e.params || typeof e.actual !== 'number') continue;
    const xv = []; let ok = true;
    for (const p of surr.names) {
      const v = e.params[p];
      if (typeof v !== 'number') { ok = false; break; }
      const b = surr.bounds[surr.names.indexOf(p)];
      xv.push((v - b[0]) / (b[1] - b[0]));
    }
    if (!ok) continue;
    X.push(xv); y.push(e.actual); sigma.push(0.03); injected++;
  }
  const bestY = Math.max.apply(null, y);
  const cand = normGrid(surr.bounds, Math.min(5, Math.floor(Math.pow(200, 1/surr.names.length))+1)).slice(0,200);
  const gp = gpPosterior(X, y, 0.03, cand, 0.5, 1);
  const scored = cand.map(function(c,i){
    const params = {};
    surr.names.forEach(function(p,k){ params[p] = Math.round((surr.bounds[k][0]+c[k]*(surr.bounds[k][1]-surr.bounds[k][0]))*1000)/1000; });
    return { params: params, pred: gp.mean[i], variance: gp.variance[i], ei: expectedImprovement(gp.mean[i], gp.variance[i], bestY, 0.01) };
  });
  scored.sort(function(a,b){ return b.ei - a.ei; });
  const top = scored.slice(0,3).map(function(s){
    return { params: s.params, predicted_yield: Math.round(s.pred*1000)/1000, uncertainty: Math.round(Math.sqrt(s.variance)*1000)/1000, ei: Math.round(s.ei*1000)/1000, rationale: s.ei>0.05 ? '高期望提升(探索优先)' : '接近已知最优(利用)' };
  });
  return { engine: engine, ok: true, model: 'gp_bo_reapply', injected_points: injected, n_training: X.length, best_known_yield: Math.round(bestY*1000)/1000, suggestions: top };
}

async function getMyExperiments(env, email, engineFilter) {
  if (!env || !env.SWARMLABS_KV) return [];
  const prefix = 'exp:' + email + ':';
  const list = await env.SWARMLABS_KV.list({ prefix: prefix });
  const keys = (list.keys||[]).map(function(k){ return k.name; });
  const recs = [];
  for (const k of keys) {
    if (engineFilter && !k.startsWith(prefix + engineFilter + ':')) continue;
    const raw = await env.SWARMLABS_KV.get(k);
    if (raw) { try { recs.push(JSON.parse(raw)); } catch(e){} }
  }
  recs.sort(function(a,b){ return (b.ts||'').localeCompare(a.ts||''); });
  return recs;
}

async function getLeaderboard(env, topN) {
  if (!env || !env.SWARMLABS_KV) return [];
  const list = await env.SWARMLABS_KV.list({ prefix: 'user:' });
  const rows = [];
  for (const k of (list.keys||[])) {
    const raw = await env.SWARMLABS_KV.get(k.name);
    if (!raw) continue;
    try {
      const u = JSON.parse(raw);
      rows.push({ email: u.email, earned: u.earned_credits||0, validated: u.validated_count||0, experiments: u.experiments_count||0 });
    } catch(e){}
  }
  rows.sort(function(a,b){ return b.earned - a.earned; });
  return rows.slice(0, topN||20).map(function(r){
    return { email: domainMask(r.email), earned_credits: r.earned, validated_experiments: r.validated, total_experiments: r.experiments };
  });
}
/* ===== SWARMLABS_USER_LOOP_END ===== */`;

// ===== 2. 路由块（插入到 ACCOUNT_ROUTE_END 之后）=====
const ROUTE_BLOCK = `/* ===== SWARMLABS_USERLOOP_ROUTES ===== */
  // 我的实验（按用户取回自己提交的实验结果，支撑再应用）
  if (apiPath === 'v2/my-experiments' || apiPath.startsWith('v2/my-experiments/')) {
    const ut = extractUserToken(request);
    if (!ut) return new Response(JSON.stringify({error:'unauthorized',login_url:'/login'}), {status:401, headers:CORS_HEADERS});
    const u = await getUserByTokenAsync(ut, env);
    if (!u) return new Response(JSON.stringify({error:'invalid_token',login_url:'/login'}), {status:401, headers:CORS_HEADERS});
    const ef = apiPath.replace('v2/my-experiments/','').replace('v2/my-experiments','');
    const list = await getMyExperiments(env, u.email, ef);
    return new Response(JSON.stringify({ email: u.email, count: list.length, experiments: list }), {headers:CORS_HEADERS});
  }
  // 实验结果再应用（把已验证实验作为GP先验，生成下一建议）
  if (apiPath === 'v2/reapply' && request.method === 'POST') {
    try {
      const body = JSON.parse(await request.clone().text() || '{}');
      const ut = extractUserToken(request);
      const u = ut ? await getUserByTokenAsync(ut, env) : null;
      if (!u) return new Response(JSON.stringify({error:'unauthorized',login_url:'/login'}), {status:401, headers:CORS_HEADERS});
      const target = body.target_engine || body.engine;
      if (!target || !ENGINE_CATEGORIES[target]) return new Response(JSON.stringify({error:'unknown_target_engine'}), {status:404, headers:CORS_HEADERS});
      let srcKey = body.source_id;
      if (!srcKey && body.source_engine && body.source_ts) srcKey = 'exp:' + u.email + ':' + body.source_engine + ':' + body.source_ts;
      if (!srcKey || !srcKey.startsWith('exp:' + u.email + ':')) return new Response(JSON.stringify({error:'invalid_source', message:'只能再应用你自己的实验'}), {status:403, headers:CORS_HEADERS});
      const srcRaw = env.SWARMLABS_KV ? await env.SWARMLABS_KV.get(srcKey) : null;
      if (!srcRaw) return new Response(JSON.stringify({error:'source_not_found'}), {status:404, headers:CORS_HEADERS});
      const src = JSON.parse(srcRaw);
      if (!src.params || typeof src.actual !== 'number') return new Response(JSON.stringify({error:'source_not_usable', message:'该实验缺少可用参数或实际结果'}), {status:422, headers:CORS_HEADERS});
      const r = await reapplySuggest(target, env, [{ params: src.params, actual: src.actual }]);
      if (!r.ok) return new Response(JSON.stringify(r), {status:200, headers:CORS_HEADERS});
      return new Response(JSON.stringify({
        status: 'success', target_engine: target,
        anchored_on: { engine: src.engine, ts: src.ts, yield: src.actual, params: src.params, validated: src.validated },
        model: r.model, injected_points: r.injected_points, n_training: r.n_training, best_known_yield: r.best_known_yield,
        suggestions: r.suggestions,
        note: target === src.engine ? ('已把你的已验证实验注入 ' + target + ' 的GP作为训练点，建议围绕该点细化/探索') : '跨引擎启发式复用：已将源实验参数作为模板建议(参数空间不同，未做训练注入)'
      }), {headers:CORS_HEADERS});
    } catch(e) { return new Response(JSON.stringify({error:'reapply_failed', detail:e.message}), {status:500, headers:CORS_HEADERS}); }
  }
  // 贡献榜（积分赚取榜）
  if (apiPath === 'v2/leaderboard') {
    const rows = await getLeaderboard(env, 20);
    return new Response(JSON.stringify({ platform:'SwarmLabs', note:'贡献积分=提交已验证实验结果赚取，可抵计算消耗；数据由用户自有回流构成', top_contributors: rows }), {headers:CORS_HEADERS});
  }
  // 自带API：存/取/删 用户自有LLM密钥
  if (apiPath === 'v2/account/apikey') {
    const ut = extractUserToken(request);
    const u = ut ? await getUserByTokenAsync(ut, env) : null;
    if (!u) return new Response(JSON.stringify({error:'unauthorized',login_url:'/login'}), {status:401, headers:CORS_HEADERS});
    if (request.method === 'POST') {
      try {
        const b = JSON.parse(await request.clone().text() || '{}');
        const provider = (b.provider||'').toLowerCase();
        if (!BYO_PROVIDERS[provider]) return new Response(JSON.stringify({error:'unsupported_provider', supported:Object.keys(BYO_PROVIDERS)}), {status:400, headers:CORS_HEADERS});
        if (!b.apiKey) return new Response(JSON.stringify({error:'missing_apiKey'}), {status:400, headers:CORS_HEADERS});
        const rec = { provider: provider, apiKey: b.apiKey, baseUrl: b.baseUrl||null, model: b.model||null, added_at: new Date().toISOString(), prefer: b.prefer !== false };
        await env.SWARMLABS_KV.put('userapi:' + u.email, JSON.stringify(rec), { expirationTtl: 86400*365 });
        return new Response(JSON.stringify({ status:'saved', provider: provider, masked: maskKey(b.apiKey), note:'自带API将在 /v2/llm 中优先路由，平台零成本且数据不出你的密钥' }), {headers:CORS_HEADERS});
      } catch(e) { return new Response(JSON.stringify({error:'apikey_save_failed', detail:e.message}), {status:500, headers:CORS_HEADERS}); }
    }
    if (request.method === 'DELETE') {
      await env.SWARMLABS_KV.put('userapi:' + u.email, '', { expirationTtl: 60 });
      return new Response(JSON.stringify({ status:'removed' }), {headers:CORS_HEADERS});
    }
    const raw = env.SWARMLABS_KV ? await env.SWARMLABS_KV.get('userapi:' + u.email) : null;
    if (!raw) return new Response(JSON.stringify({ configured:false }), {headers:CORS_HEADERS});
    try { const r = JSON.parse(raw); return new Response(JSON.stringify({ configured:true, provider:r.provider, masked:maskKey(r.apiKey), baseUrl:r.baseUrl, model:r.model, prefer:r.prefer, added_at:r.added_at }), {headers:CORS_HEADERS}); } catch(e) { return new Response(JSON.stringify({configured:false}), {headers:CORS_HEADERS}); }
  }
  // 自带API LLM 代理：经用户密钥路由，平台不消耗积分
  if (apiPath === 'v2/llm' && request.method === 'POST') {
    try {
      const b = JSON.parse(await request.clone().text() || '{}');
      const ut = extractUserToken(request);
      const u = ut ? await getUserByTokenAsync(ut, env) : null;
      if (!u) return new Response(JSON.stringify({error:'unauthorized',login_url:'/login'}), {status:401, headers:CORS_HEADERS});
      const raw = env.SWARMLABS_KV ? await env.SWARMLABS_KV.get('userapi:' + u.email) : null;
      if (!raw) return new Response(JSON.stringify({error:'no_byo_key', message:'请先在工作台 /api/v2/account/apikey 添加你的LLM密钥', add_url:'/workbench'}), {status:402, headers:CORS_HEADERS});
      const k = JSON.parse(raw);
      const provider = k.provider;
      let url, body;
      const msgs = b.messages || [];
      if (provider === 'ollama') {
        url = (k.baseUrl || 'http://localhost:11434') + '/api/chat';
        body = { model: k.model || 'qwen3.6:latest', messages: msgs, stream: false };
      } else {
        url = (k.baseUrl || BYO_PROVIDERS[provider]) + '/chat/completions';
        body = { model: k.model || (provider==='deepseek'?'deepseek-chat':provider==='qwen'?'qwen-plus':provider==='moonshot'?'moonshot-v1-8k':'gpt-4o-mini'), messages: msgs, temperature: b.temperature || 0.3 };
      }
      const upstream = await fetch(url, { method:'POST', headers:{ 'Content-Type':'application/json', 'Authorization':'Bearer '+k.apiKey }, body: JSON.stringify(body) });
      const txt = await upstream.text();
      return new Response(txt, { status: upstream.status, headers: { 'Content-Type':'application/json', 'X-Routed-By':'user-byo:'+provider } });
    } catch(e) { return new Response(JSON.stringify({error:'llm_proxy_failed', detail:e.message}), {status:502, headers:CORS_HEADERS}); }
  }
/* ===== SWARMLABS_USERLOOP_ROUTES_END ===== */`;

// ===== 3. 反馈增强（插入到 SWARMLABS_FB_PARAMS 注释之后）=====
const FB_ENRICH_BLOCK = `/* ===== SWARMLABS_FB_ENRICH ===== */
  try {
    const _ut = extractUserToken(request);
    let _reporter = fb.reported_by || null;
    if (!_reporter && _ut) { const _tu = await getUserByTokenAsync(_ut, env); if (_tu) _reporter = _tu.email; }
    if (_reporter) {
      const _expKey = 'exp:' + _reporter + ':' + fb.engine + ':' + Date.now();
      let _validated = false, _earned = EARN.feedback_base;
      if (fb.actual !== undefined && fb.actual !== null && typeof fb.actual === 'number') {
        const _close = (typeof fb.predicted === 'number') && Math.abs(fb.actual - fb.predicted) <= Math.max(10, Math.abs(fb.predicted) * 0.2);
        _validated = _close || fb.signal === 'success' || fb.signal === 'improved';
        _earned = _validated ? EARN.feedback_validated : EARN.feedback_base;
      }
      const _exp = { id: _expKey, engine: fb.engine, predicted: fb.predicted, actual: fb.actual, signal: fb.signal, context: fb.context, params: fb.params || null, ts: entry.ts, validated: _validated, earned: _earned };
      if (env && env.SWARMLABS_KV) await env.SWARMLABS_KV.put(_expKey, JSON.stringify(_exp), { expirationTtl: 86400*365 });
      if (_ut) {
        const _u = await getUserByTokenAsync(_ut, env);
        if (_u) {
          const _lst = await getMyExperiments(env, _u.email, fb.engine);
          if (_lst.length === 0) _earned += EARN.first_engine;
          addCredits(_u, _earned, 'contribution:feedback:' + fb.engine);
          _u.earned_credits = (_u.earned_credits || 0) + _earned;
          _u.experiments_count = (_u.experiments_count || 0) + 1;
          if (_validated) _u.validated_count = (_u.validated_count || 0) + 1;
          _exp.earned = _earned;
          if (env && env.SWARMLABS_KV) await env.SWARMLABS_KV.put(_expKey, JSON.stringify(_exp), { expirationTtl: 86400*365 });
          await kvSaveUser(env, _u.email, _u);
        }
      }
    }
  } catch(e) {}`;

console.log('注入用户闭环...');
insertBefore('/* ===== SWARMLABS_ML_ROUTES ===== */', 'SWARMLABS_USER_LOOP', FUNC_BLOCK);
insertAfterLine('/* ===== SWARMLABS_ACCOUNT_ROUTE_END ===== */', 'SWARMLABS_USERLOOP_ROUTES', ROUTE_BLOCK);
insertAfterLine('/* ===== SWARMLABS_FB_PARAMS ===== */', 'SWARMLABS_FB_ENRICH', FB_ENRICH_BLOCK);

fs.writeFileSync(FILE, s);
console.log('完成。文件大小:', s.length, '字节');
