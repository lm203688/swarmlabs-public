#!/usr/bin/env node
/**
 * Inject F — 诚实边界可落地部分（不依赖 GPU 训练、不伪造数据）
 * 1) 主动学习闭环：用真实用户反馈增量校准单点 UQ（诚实自举）
 * 2) GET /v2/feedback/summary  — 飞轮可见化（真实偏差统计）
 * 3) POST /v2/experiment-plan/{engine} — 干湿闭环接口种子（标准化实验模板，不连真实验室）
 * 幂等 + 容错；注入后调用方用 node --check 校验。
 */
const fs = require('fs');
const path = require('path');

const FILE = path.join(__dirname, '..', 'frontend_fixed', 'functions', 'api', '[[path]].js');
const BAK = FILE + '.bak-calib-' + Date.now();

if (!fs.existsSync(FILE)) { console.error('FILE NOT FOUND', FILE); process.exit(1); }
fs.copyFileSync(FILE, BAK);
let s = fs.readFileSync(FILE, 'utf8');

function replaceOnce(label, oldS, newS) {
  if (s.includes(newS)) { console.log('  [skip] ' + label + ' (已存在)'); return; }
  if (!s.includes(oldS)) { throw new Error('ANCHOR NOT FOUND for ' + label + '\n>>> ' + oldS.slice(0, 120)); }
  s = s.replace(oldS, newS);
  console.log('  [ok]   ' + label);
}

/* ---------- 1) 模块级 helper ---------- */
const HELPERS = `
/* ===== SWARMLABS_FB_CALIB_START ===== */
// 主动学习闭环：用真实用户反馈增量校准单点 UQ（诚实自举，不依赖 GPU 训练）
const FB_AGG_CACHE = {}; // 同 isolate 内缓存，减少 KV 读取
function fbAggKey(engine){ return 'fbagg:' + engine; }
function fbGlobalKey(){ return 'fbagg:_global'; }
async function getFeedbackAggregate(env, engine){
  if(!env || !env.SWARMLABS_KV) return null;
  if(FB_AGG_CACHE[engine]) return FB_AGG_CACHE[engine];
  try{
    const raw = await env.SWARMLABS_KV.get(fbAggKey(engine));
    if(!raw) return null;
    const agg = JSON.parse(raw);
    FB_AGG_CACHE[engine] = agg;
    return agg;
  }catch(e){ return null; }
}
// 增量更新聚合（在 feedback 写入时调用；仅当 predicted/actual 均为数值）
async function updateFeedbackAggregate(env, engine, predicted, actual){
  if(!env || !env.SWARMLABS_KV) return;
  if(typeof predicted !== 'number' || typeof actual !== 'number') return;
  const err = actual - predicted;
  let agg = await getFeedbackAggregate(env, engine) || { n:0, sumErr:0, sumSqErr:0 };
  agg.n += 1; agg.sumErr += err; agg.sumSqErr += err*err;
  agg.meanErr = round4(agg.sumErr / agg.n);
  agg.rmse = round2(Math.sqrt(agg.sumSqErr / agg.n));
  agg.updated = new Date().toISOString();
  FB_AGG_CACHE[engine] = agg;
  await env.SWARMLABS_KV.put(fbAggKey(engine), JSON.stringify(agg), { expirationTtl: 86400*365 });
  try{
    const gRaw = await env.SWARMLABS_KV.get(fbGlobalKey());
    const g = gRaw ? JSON.parse(gRaw) : { total:0, engines:{} };
    g.total = (g.total||0) + 1;
    g.engines = g.engines || {};
    g.engines[engine] = (g.engines[engine]||0) + 1;
    g.updated = agg.updated;
    await env.SWARMLABS_KV.put(fbGlobalKey(), JSON.stringify(g), { expirationTtl: 86400*365 });
  }catch(e){}
}
// 把真实反馈校准叠加到预测结果（诚实：观测 RMSE 不低于文献 UQ 时取较大者）
function applyFeedbackCalibration(result, agg){
  if(!agg || !agg.n || agg.n < 3) return result;
  const obs = agg.rmse;
  const base = typeof result.empirical_uncertainty_pct === 'number' ? result.empirical_uncertainty_pct : (typeof result.uncertainty === 'number' ? result.uncertainty : 0);
  result.empirical_uncertainty_pct = round1(Math.max(base, obs));
  result.uncertainty = round1(Math.max(base, obs));
  result.feedback_calibrated = true;
  result.observed_rmse = agg.rmse;
  result.bias_mean = agg.meanErr;
  result.n_feedback = agg.n;
  return result;
}
/* ===== SWARMLABS_FB_CALIB_END ===== */
`;
replaceOnce('helpers', '/* ===== SWARMLABS_NEGATIVE_END ===== */', '/* ===== SWARMLABS_NEGATIVE_END ===== */' + HELPERS);

/* ---------- 2) feedback handler 调用增量校准 + 贪婪路由排除 summary/stats ---------- */
replaceOnce('feedback-guard',
  "  if (apiPath === 'v2/feedback' || apiPath.startsWith('v2/feedback/')) {",
  "  if (apiPath === 'v2/feedback' || (apiPath.startsWith('v2/feedback/') && apiPath !== 'v2/feedback/summary' && apiPath !== 'v2/feedback/stats')) {"
);
replaceOnce('feedback-update',
  '      return new Response(JSON.stringify({ accepted: true, note: "已记录，将用于平台自有数据闭环与主动学习", entry }), { headers: CORS_HEADERS });',
  `      if (env && env.SWARMLABS_KV) {
        await updateFeedbackAggregate(env, fb.engine, fb.predicted, fb.actual);
      }
      return new Response(JSON.stringify({ accepted: true, note: "已记录，将用于平台自有数据闭环与主动学习", entry, calibrated: !!(env && env.SWARMLABS_KV) }), { headers: CORS_HEADERS });`
);

/* ---------- 3) run handler 应用校准 + evidence 块 ---------- */
replaceOnce('run-calib',
  '      const result = physicsPredict(engine, params);\n      return new Response(JSON.stringify({\n        engine,\n        result,\n        status: \'success\',',
  `      const result = physicsPredict(engine, params);
      const fbAgg = env ? await getFeedbackAggregate(env, engine) : null;
      applyFeedbackCalibration(result, fbAgg);
      return new Response(JSON.stringify({
        engine,
        result,
        status: 'success',
        evidence: { literature_coverage: getEngineCoverage(engine), negative_samples: getNegativeSamples(engine).length, feedback_n: fbAgg ? fbAgg.n : 0, calibrated: !!(fbAgg && fbAgg.n >= 3) },`
);

/* ---------- 4) sweep handler 应用校准 + evidence 块 ---------- */
replaceOnce('sweep-calib',
  '      const sweep = sweepParams(engine, combos);\n      if (sweep.error) {',
  `      const sweep = sweepParams(engine, combos);
      const swAgg = env ? await getFeedbackAggregate(env, engine) : null;
      if (swAgg && swAgg.n >= 3 && sweep.series) {
        sweep.series = sweep.series.map(function(pt){ if (typeof pt.uncertainty === 'number') pt.uncertainty = round1(Math.max(pt.uncertainty, swAgg.rmse)); pt.feedback_calibrated = true; pt.observed_rmse = swAgg.rmse; pt.n_feedback = swAgg.n; return pt; });
      }
      if (sweep.error) {`
);
replaceOnce('sweep-evidence',
  '        engine,\n        sweep,\n        status: \'success\',\n        timestamp: new Date().toISOString(),',
  `        engine,
        sweep,
        status: 'success',
        evidence: { literature_coverage: getEngineCoverage(engine), negative_samples: getNegativeSamples(engine).length, feedback_n: swAgg ? swAgg.n : 0, calibrated: !!(swAgg && swAgg.n >= 3) },
        timestamp: new Date().toISOString(),`
);

/* ---------- 5) 两个新路由（诚实边界端点） ---------- */
const NEW_ROUTES = `
/* ===== SWARMLABS_FB_SUMMARY_ROUTE ===== */
  if (apiPath === 'v2/feedback/summary' || apiPath === 'v2/feedback/stats') {
    try {
      const summary = { platform: 'SwarmLabs', note: '仅统计真实用户回流反馈(平台自有数据), 非公开文献', total_feedback: 0, engines: {}, top_engines: [], engine_details: [] };
      if (env && env.SWARMLABS_KV) {
        const gRaw = await env.SWARMLABS_KV.get('fbagg:_global');
        if (gRaw) {
          const g = JSON.parse(gRaw);
          summary.total_feedback = g.total || 0;
          summary.engines = g.engines || {};
          const arr = Object.entries(summary.engines).map(([e,n])=>({engine:e,count:n})).sort((a,b)=>b.count-a.count);
          summary.top_engines = arr.slice(0,20);
          const details = [];
          for (const item of arr.slice(0,20)) {
            const a = await getFeedbackAggregate(env, item.engine);
            if (a) details.push({ engine: item.engine, n: a.n, observed_rmse: a.rmse, bias_mean: a.meanErr, updated: a.updated });
          }
          summary.engine_details = details;
        }
      }
      return new Response(JSON.stringify(summary), { headers: CORS_HEADERS });
    } catch(e) {
      return new Response(JSON.stringify({ error: 'summary_failed', detail: e.message }), { status: 500, headers: CORS_HEADERS });
    }
  }
/* ===== SWARMLABS_EXP_PLAN_ROUTE ===== */
  if (apiPath.startsWith('v2/experiment-plan/')) {
    const engine = decodeURIComponent(apiPath.split('/').pop());
    if (!ENGINE_CATEGORIES[engine]) {
      return new Response(JSON.stringify({error:'Unknown engine: ' + engine, available:Object.keys(ENGINE_CATEGORIES).slice(0,20)}), {status:404, headers:CORS_HEADERS});
    }
    try {
      const body = await request.clone().text();
      const opts = JSON.parse(body || '{}');
      const cat = ENGINE_CATEGORIES[engine];
      const paramDef = CATEGORY_PARAMS[cat] || CATEGORY_PARAMS['General'];
      const sample = physicsPredict(engine, opts.fixed || {});
      const plan = {
        engine,
        category: cat,
        disclaimer: '本方案为标准化实验模板(由预测推导), 不含真实实验室连接; 请由实验人员执行并回填 POST /api/v2/feedback 完成数据闭环',
        suggested_doe: { design: '中心复合 / Box-Behnken (3 水平)', replicates_per_point: 3, min_points: 12, rationale: '覆盖参数空间以校准模型不确定度' },
        parameters_to_vary: (paramDef.required||[]).concat(paramDef.optional||[]).slice(0,6),
        parameters_fixed: opts.fixed || {},
        metrics_to_record: [sample.metric_unit || 'yield', 'purity', 'byproduct'],
        predict_then_measure: '先调用 POST /api/v2/run/' + engine + ' 取值作为目标, 实验实测后比对',
        closure: '实验完成后 POST /api/v2/feedback {engine, predicted, actual, signal} 反哺平台'
      };
      return new Response(JSON.stringify({ plan, status: 'success' }), { headers: CORS_HEADERS });
    } catch(e) {
      return new Response(JSON.stringify({ error: 'plan_failed', detail: e.message }), { status: 400, headers: CORS_HEADERS });
    }
  }
/* ===== SWARMLABS_FB_ROUTES_END ===== */
`;
replaceOnce('new-routes', '/* ===== SWARMLABS_FEEDBACK_ROUTE_END ===== */', '/* ===== SWARMLABS_FEEDBACK_ROUTE_END ===== */' + NEW_ROUTES);

fs.writeFileSync(FILE, s);
console.log('WROTE', FILE, '| backup', BAK);
console.log('BYTES', s.length);
