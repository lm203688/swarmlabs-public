#!/usr/bin/env node
/* 注入知识图谱端点 (任务 #59):
   - module 级 loader loadKG(env) 从 KV kg:v1 读取紧凑图谱(缓存于 globalThis)
   - GET /v2/graph          返回全局统计
   - GET /v2/graph/{engine} 返回该引擎邻域(文献/负样本/期刊/作者/主题/相关引擎)
   幂等、可重复运行。
*/
const fs = require('fs');
const path = require('path');
const F = path.join(__dirname, '..', 'frontend_fixed', 'functions', 'api', '[[path]].js');
let src = fs.readFileSync(F, 'utf8');
const MARK = '/* ===== SWARMLABS_KG_ROUTES ===== */';

// 1) module 级 loader（注入到现有 helper 区，靠近 SWARMLABS_FB_ROUTES_END 之前即可，用 globalThis 跨请求缓存）
const LOADER = `
/* ===== SWARMLABS_KG_LOADER ===== */
async function loadKG(env) {
  if (globalThis.__KG_CACHE) return globalThis.__KG_CACHE;
  if (!env || !env.SWARMLABS_KV) throw new Error('KV 不可用');
  const raw = await env.SWARMLABS_KV.get('kg:v1');
  if (!raw) throw new Error('KV 中未找到 kg:v1 (请先部署知识图谱: wrangler kv key put kg:v1 --path data/kg_compact.json)');
  globalThis.__KG_CACHE = JSON.parse(raw);
  return globalThis.__KG_CACHE;
}
`;

// 2) 路由处理（插入到 FB_ROUTES_END 标记之前）
const ROUTES = `
${MARK}
  // === 知识图谱端点 (A) ===
  if (apiPath === 'v2/graph') {
    try {
      const kg = await loadKG(env);
      return new Response(JSON.stringify({ status: 'success', graph: kg.global }), { headers: CORS_HEADERS });
    } catch (e) {
      return new Response(JSON.stringify({ error: 'kg_unavailable', detail: e.message }), { status: 503, headers: CORS_HEADERS });
    }
  }
  if (apiPath.startsWith('v2/graph/')) {
    const engine = decodeURIComponent(apiPath.split('/').pop());
    try {
      const kg = await loadKG(env);
      const node = kg.engines[engine];
      if (!node) {
        return new Response(JSON.stringify({ error: 'unknown_engine', engine, available_engines: Object.keys(kg.engines).slice(0, 30), total_engines: kg.global.n_engines }), { status: 404, headers: CORS_HEADERS });
      }
      return new Response(JSON.stringify({ engine, status: 'success', ...node }), { headers: CORS_HEADERS });
    } catch (e) {
      return new Response(JSON.stringify({ error: 'kg_unavailable', detail: e.message }), { status: 503, headers: CORS_HEADERS });
    }
  }
`;

function replaceOnce(tag, anchor, block) {
  if (src.includes(tag)) { console.log('已存在:', tag, '-> 跳过'); return; }
  if (!src.includes(anchor)) throw new Error('锚点未找到: ' + anchor);
  src = src.replace(anchor, block + anchor);
  console.log('注入:', tag);
}

// loader 注入到 FB_ROUTES_END 之前
replaceOnce('/* ===== SWARMLABS_KG_LOADER ===== */', '/* ===== SWARMLABS_FB_ROUTES_END ===== */', LOADER);
// routes 注入到 FB_ROUTES_END 之前
replaceOnce(MARK, '/* ===== SWARMLABS_FB_ROUTES_END ===== */', ROUTES);

fs.writeFileSync(F, src);
console.log('写入完成');
