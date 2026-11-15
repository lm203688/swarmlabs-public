#!/usr/bin/env node
/**
 * inject_literature_api.js — 把引擎文献包注入 Worker，并新增文献查询能力。
 *
 * 注入三处:
 *   1. ENGINE_LITERATURE 常量（155 引擎 × TOP4 真实文献）；
 *   2. GET  /api/v2/literature            — 全库统计概览
 *      GET  /api/v2/literature/{engine}   — 指定引擎的文献支撑
 *   3. physicsPredict 响应新增 literature_support 字段（TOP2），
 *      使每次计算结果都自带可核验的文献出处。
 *
 * 幂等: 重复运行会先移除旧注入块再重新写入。
 */
const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

const ROOT = path.join(__dirname, '..');
const SRC = path.join(ROOT, 'frontend_fixed', 'functions', 'api', '[[path]].js');
const BUNDLE = path.join(ROOT, 'data', 'engine-literature-bundle.json');

const MARK_START = '/* ===== SWARMLABS_ENGINE_LITERATURE_START ===== */';
const MARK_END = '/* ===== SWARMLABS_ENGINE_LITERATURE_END ===== */';
const ROUTE_START = '/* ===== SWARMLABS_LITERATURE_ROUTE_START ===== */';
const ROUTE_END = '/* ===== SWARMLABS_LITERATURE_ROUTE_END ===== */';

let s = fs.readFileSync(SRC, 'utf8');
const original = s;
const bundle = JSON.parse(fs.readFileSync(BUNDLE, 'utf8'));

// ---------------------------------------------- 幂等: 清除旧注入
function stripBlock(str, a, b) {
  const i = str.indexOf(a), j = str.indexOf(b);
  if (i >= 0 && j > i) return str.slice(0, i) + str.slice(j + b.length);
  return str;
}
s = stripBlock(s, MARK_START, MARK_END);
s = stripBlock(s, ROUTE_START, ROUTE_END);

// ---------------------------------------------- 1) 注入常量
const litJson = JSON.stringify(bundle.literature);
const constBlock = `${MARK_START}
// 155 个引擎的真实文献支撑（DOI 全部由 CrossRef 官方 API 返回并校验，无人工编造）
// 生成时间: ${bundle.meta.generated_at} | 条目: ${bundle.meta.entries} | 每引擎 TOP ${bundle.meta.top_per_engine}
const ENGINE_LITERATURE = ${litJson};
function getEngineLiterature(engine, limit) {
  const list = ENGINE_LITERATURE[engine] || [];
  return limit ? list.slice(0, limit) : list;
}
${MARK_END}
`;

// 插到顶层 const PHYS 之前（顶层作用域，早于所有消费者；避免误插进 findClosestRef 等嵌套函数导致 1101）
// 为什么不用 'const refMap = {' 作锚点：refMap 位于 findClosestRef() 函数体内（嵌套作用域），
// 常量若插在那里，外层路由处理器访问不到 → Worker 运行时 ReferenceError → error code 1101。
const anchor = s.indexOf('const PHYS = {');
if (anchor < 0) { console.error('找不到顶层 const PHYS 锚点'); process.exit(1); }
// 回退到该行行首（顶层，无缩进）
const lineStart = s.lastIndexOf('\n', anchor) + 1;
s = s.slice(0, lineStart) + constBlock + s.slice(lineStart);

// ---------------------------------------------- 2) 注入路由
const routeBlock = `${ROUTE_START}
  // === 引擎文献支撑查询 ===
  if (apiPath === 'v2/literature' || apiPath.startsWith('v2/literature/')) {
    const parts = apiPath.split('/');
    const eng = parts.length > 2 ? decodeURIComponent(parts[2]) : null;
    if (!eng) {
      const engines = Object.keys(ENGINE_LITERATURE);
      let entries = 0, withData = 0;
      for (const k of engines) {
        entries += ENGINE_LITERATURE[k].length;
        withData += ENGINE_LITERATURE[k].filter(function (x) { return x.has_data; }).length;
      }
      return new Response(JSON.stringify({
        engines_covered: engines.length,
        total_entries: entries,
        entries_with_extracted_data: withData,
        source: 'CrossRef REST API',
        integrity: '所有 DOI 由 CrossRef 官方索引返回并校验，无人工编造条目',
        usage: 'GET /api/v2/literature/{engine}',
        status: 'success'
      }), { headers: CORS_HEADERS });
    }
    const refs = ENGINE_LITERATURE[eng];
    if (!refs) {
      return new Response(JSON.stringify({
        error: 'No literature indexed for engine: ' + eng,
        available_engines: Object.keys(ENGINE_LITERATURE).slice(0, 20)
      }), { status: 404, headers: CORS_HEADERS });
    }
    return new Response(JSON.stringify({
      engine: eng,
      count: refs.length,
      references: refs.map(function (r) {
        return {
          doi: r.doi, url: 'https://doi.org/' + r.doi, title: r.title,
          journal: r.journal, year: r.year, citations: r.citations,
          has_extracted_data: !!r.has_data
        };
      }),
      source: 'CrossRef REST API',
      status: 'success'
    }), { headers: CORS_HEADERS });
  }
${ROUTE_END}
`;

const routeAnchor = s.indexOf("  // === 引擎列表（支持 v1 和 v2 路由） ===");
if (routeAnchor < 0) { console.error('找不到路由锚点'); process.exit(1); }
s = s.slice(0, routeAnchor) + routeBlock + s.slice(routeAnchor);

// ---------------------------------------------- 3) physicsPredict 响应加 literature_support
const respAnchor = '    acceptance_criteria: prediction.acceptance_criteria || null,';
if (s.indexOf('literature_support:') < 0) {
  if (s.indexOf(respAnchor) < 0) { console.error('找不到响应锚点'); process.exit(1); }
  s = s.replace(respAnchor,
    respAnchor + `
    // [LIT-01] 每次计算自带真实文献出处（CrossRef 校验，可点击核验）
    literature_support: getEngineLiterature(engine, 2).map(function (r) {
      return { doi: r.doi, url: 'https://doi.org/' + r.doi, title: r.title, journal: r.journal, year: r.year, citations: r.citations };
    }),`);
}

// ---------------------------------------------- 写入 + 校验
const bak = SRC + '.bak-lit-' + Date.now();
fs.writeFileSync(bak, original);
fs.writeFileSync(SRC, s);

try {
  execSync(`node --check "${SRC}"`, { stdio: 'pipe' });
  const sizeKB = (Buffer.byteLength(s, 'utf8') / 1024).toFixed(0);
  const beforeKB = (Buffer.byteLength(original, 'utf8') / 1024).toFixed(0);
  console.log('✓ 注入完成，语法校验通过');
  console.log(`  引擎文献: ${bundle.meta.engines} 引擎 / ${bundle.meta.entries} 条`);
  console.log(`  文件体积: ${beforeKB} KB -> ${sizeKB} KB`);
  console.log(`  新增端点: GET /api/v2/literature, GET /api/v2/literature/{engine}`);
  console.log(`  响应新增: literature_support (TOP2)`);
  console.log(`  备份: ${path.basename(bak)}`);
} catch (e) {
  fs.writeFileSync(SRC, original);
  console.error('✗ 语法校验失败，已回滚');
  console.error(String(e.stderr || e.message).slice(0, 1000));
  process.exit(1);
}
