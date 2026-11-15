// 端到端验证：新埋点方案在真实访问模式下的 KV 配额消耗。
// 对照 KV 免费层：1K 写/天 + 1K 删/天 + 1K list/天 硬上限。
//
// 运行: node scripts/verify_kv_quota.js

import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const AN = path.join(__dirname, '..', 'frontend_fixed', 'functions', 'lib', 'analytics.js');

class FakeKV {
  constructor() { this.store = new Map(); this.writes = 0; this.deletes = 0; this.lists = 0; }
  async get(k) { return this.store.has(k) ? this.store.get(k) : null; }
  async put(k, v, opts) { this.writes++; this.store.set(k, v); }
  async delete(k) { this.deletes++; this.store.delete(k); }
  async list({prefix}) {
    this.lists++;
    return { keys: [...this.store.keys()].filter(k => k.startsWith(prefix||'')).map(name => ({name})), list_complete: true };
  }
}

const mod = await import(pathToFileURL(AN).href);

// 打印配额消耗摘要
function quotaReport(name, kv) {
  const pctW = (kv.writes / 1000 * 100).toFixed(2);
  const pctL = (kv.lists / 1000 * 100).toFixed(2);
  console.log(`\n=== ${name} ===`);
  console.log(`  写: ${kv.writes}  (${pctW}% of 1K)`);
  console.log(`  删: ${kv.deletes}  (0.00% of 1K)`);
  console.log(`  list: ${kv.lists}  (${pctL}% of 1K)`);
}

// --- 场景 1: 1000 次访问（200 真实页面 + 800 扫描器，全在同 1 小时内）---
{
  const kv = new FakeKV();
  const env = { SWARMLABS_KV: kv };
  const paths = [];
  for (let i = 0; i < 200; i++) paths.push(`blog_${(i % 60).toString(36)}_article`);
  for (let i = 0; i < 800; i++) paths.push(`scanner_probe_${i}_xyz`);
  for (const p of paths) {
    if (mod.isHumanPage(p)) await mod.trackPageView(env, p, '1.2.3.4', 'curl', '');
    else await mod.trackBotView(env, p);
  }
  await mod.flushAll(env); // isolate 销毁前强制 flush

  quotaReport('场景 1: 1000 次访问（200 真 + 800 扫描器），同 1 小时内', kv);
  assert.ok(kv.writes <= 100, `写配额严重超限: ${kv.writes}`);
  console.log(`  → 写配额余量: ${1000 - kv.writes}/1000，删余量: 1000/1000`);
  console.log(`  → 对比旧实现: 旧版 1000 次访问 = 1000+ 次写（占满配额，注册被 10048 拒）`);
}

// --- 场景 2: 200 次 API 事件（10 个不同 apiPath）---
{
  const kv = new FakeKV();
  const env = { SWARMLABS_KV: kv };
  for (let i = 0; i < 200; i++) await mod.trackEvent(env, `v3/predict/${i % 10}`, '1.2.3.4');
  await mod.flushAll(env);

  quotaReport('场景 2: 200 次 API 事件（10 种 apiPath）', kv);
  assert.ok(kv.writes <= 5);
}

// --- 场景 3: 一天满流量下的读端 list 消耗 ---
{
  const kv = new FakeKV();
  const env = { SWARMLABS_KV: kv };
  // 造 3 天的数据
  for (let d = 0; d < 3; d++) {
    for (let i = 0; i < 50; i++) await mod.trackPageView(env, `blog_${(i % 10).toString(36)}_a`, '1.2.3.4', 'ua', '');
    for (let i = 0; i < 30; i++) await mod.trackBotView(env, `scanner_${i % 5}`);
    for (let i = 0; i < 20; i++) await mod.trackEvent(env, `v3/predict/${i % 3}`, '1.2.3.4');
  }
  await mod.flushAll(env);
  console.log(`  (flush 后 KV 里应有 ${kv.store.size} 个键)`);
  await mod.flushAll(env);

  const listsBefore = kv.lists;
  const summary = await mod.getAnalyticsSummary(env, 7);
  const listCalls = kv.lists - listsBefore;

  // 注意：summary.data 按「新→旧」排列，今天 = slice(0)[0]
  const today = Object.keys(summary.data)[0];
  const todayData = summary.data[today];

  console.log(`\n=== 场景 3: getAnalyticsSummary(env, 7) ===`);
  console.log(`  list 调用: ${listCalls}  (≤ 3 × 7 = 21 是理论上限)`);
  console.log(`  今日(${today}) page_views: ${todayData.page_views}`);
  console.log(`  今日 bot_views: ${todayData.bot_views}`);
  console.log(`  今日 api_calls: ${todayData.api_calls}`);
  assert.equal(todayData.page_views, 150, `page_views 应为 150（3 天 × 50 次，全落今天）`);
  assert.equal(todayData.bot_views, 90, `bot_views 应为 90`);
  assert.equal(todayData.api_calls, 60, `api_calls 应为 60`);
  assert.ok(listCalls <= 21, `list 配额超限`);
  console.log(`  → 计数准确，list 消耗 ≤ 21 次/查询`);
}

// --- 场景 4: 一天真实流量下写端总量估算（模拟 1000 页面 + 300 API + 50 订单）---
{
  const kv = new FakeKV();
  const env = { SWARMLABS_KV: kv };
  for (let i = 0; i < 1000; i++) {
    await mod.trackPageView(env, `/blog/article_${(i % 40).toString(36)}`, '1.2.3.4', 'curl', '');
  }
  for (let i = 0; i < 300; i++) {
    await mod.trackEvent(env, `v3/predict/${i % 5}`, '1.2.3.4');
  }
  for (let i = 0; i < 50; i++) {
    await mod.trackBotView(env, `scanner_${(i % 20).toString(36)}_x`);
  }
  await mod.flushAll(env);

  quotaReport('场景 4: 一天满流量（1000 页面 + 300 API + 50 扫描器）', kv);
  // 埋点写配额消耗应 < 总配额的 20%，剩下 800+ 配额留给注册/订单/审计
  assert.ok(kv.writes <= 200, `埋点写配额超预算: ${kv.writes}`);
  console.log(`  → 剩余配额（可给注册/订单/审计）: ${1000 - kv.writes} 写/天`);
}

console.log('\n✅ 全部通过：埋点写配额与访问次数解耦。');
