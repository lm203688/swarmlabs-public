#!/usr/bin/env node
// Unit tests for frontend_fixed/functions/lib/analytics.js
// Covers: pagination beyond the 1000-key cap, scanner reclassification of historical
// keys (without deletion), and the pagebot bucket.
// Run: node tests/frontend/test_analytics.mjs
import { getAnalyticsSummary } from '../../frontend_fixed/functions/lib/analytics.js';

const TODAY = new Date().toISOString().slice(0, 10);

/** Fake KV with cursor pagination, faithful to the CF KV list() contract. */
function makeKV(store) {
  return {
    put: async (k, v) => { store[k] = v; },
    list: async ({ prefix, limit = 1000, cursor } = {}) => {
      const all = Object.keys(store).filter(k => k.startsWith(prefix)).sort();
      const start = typeof cursor === 'number' ? cursor : 0;
      const slice = all.slice(start, start + limit);
      const done = start + limit >= all.length;
      return {
        keys: slice.map(name => ({ name, value: store[name] })),
        list_complete: done,
        cursor: done ? undefined : start + limit
      };
    }
  };
}

const results = [];
function check(name, cond, detail = '') {
  results.push({ name, pass: !!cond, detail: String(detail).slice(0, 120) });
}

async function run() {
  // ---- 1. Small dataset: humans vs scanners split correctly ---------------
  {
    const store = {};
    const kv = makeKV(store);
    for (const a of ['v3/predict', 'v3/guard', 'v3/health']) {
      store[`analytics:event:${TODAY}:${a}:e${a.length}`] = '{}';
    }
    for (const h of ['root', 'workbench', 'pricing', 'index_html', 'blog_foo']) {
      store[`analytics:page:${TODAY}:${h}:h1`] = '{}';
    }
    for (const b of ['_next', '__nextjs_action', '2020__env', '_2F_next',
                     'phpinfo_php', '_aws_credentials', 'git_config', 'wp_login_php']) {
      store[`analytics:page:${TODAY}:${b}:b1`] = '{}';
    }
    for (const b of ['_next', 'phpinfo_php']) {
      store[`analytics:pagebot:${TODAY}:${b}:s1`] = '{}';
    }

    const r = await getAnalyticsSummary({ SWARMLABS_KV: kv }, 1);
    const d = r.data && r.data[TODAY];
    check('api_calls = 3', d && d.api_calls === 3, `got ${d && d.api_calls}`);
    check('page_views = 5 (human only)', d && d.page_views === 5, `got ${d && d.page_views}`);
    // 8 legacy scanner labels in the page: bucket + 2 in the pagebot: bucket.
    check('bot_views = 10 (legacy + pagebot, no counting hole)', d && d.bot_views === 10,
      `got ${d && d.bot_views}`);
    check('total_events = 18', d && d.total_events === 18, `got ${d && d.total_events}`);
    check('by_page excludes scanner labels',
      d && Object.keys(d.by_page).length === 5 && !d.by_page._next && !d.by_page.phpinfo_php,
      `by_page=${JSON.stringify(d && d.by_page)}`);
    check('status ok', r.status === 'ok', `got ${r.status}`);
  }

  // ---- 2. Pagination: >1000 keys must NOT be capped -----------------------
  {
    const store = {};
    const kv = makeKV(store);
    // 1500 human page views spread over 3 real pages, plus 1200 scanner keys.
    for (let i = 0; i < 1500; i++) {
      const page = ['root', 'workbench', 'pricing'][i % 3];
      store[`analytics:page:${TODAY}:${page}:uid${i}`] = '{}';
    }
    for (let i = 0; i < 1200; i++) {
      const label = ['_next', 'phpinfo_php', '2020__env', '_2F_next'][i % 4];
      store[`analytics:page:${TODAY}:${label}:sid${i}`] = '{}';
    }
    const r = await getAnalyticsSummary({ SWARMLABS_KV: kv }, 1);
    const d = r.data && r.data[TODAY];
    check('page_views = 1500 (not capped at 1000)', d && d.page_views === 1500,
      `got ${d && d.page_views}`);
    check('by_page reflects true top pages',
      d && d.by_page.root === 500 && d.by_page.workbench === 500 && d.by_page.pricing === 500,
      `by_page=${JSON.stringify(d && d.by_page)}`);
  }

  // ---- 3. Real pages that resemble scanner tokens must stay human ---------
  {
    const store = {};
    const kv = makeKV(store);
    for (const h of ['robots_txt', 'index_html', 'environment', 'envy', 'root']) {
      store[`analytics:page:${TODAY}:${h}:z1`] = '{}';
    }
    const r = await getAnalyticsSummary({ SWARMLABS_KV: kv }, 1);
    const d = r.data && r.data[TODAY];
    // robots_txt / index_html / root are real pages; environment / envy are probes.
    check('real pages kept, lookalike probes dropped',
      d && d.page_views === 3 && d.bot_views === 2,
      `page_views=${d && d.page_views} bot_views=${d && d.bot_views} by=${JSON.stringify(d && d.by_page)}`);
  }

  // ---- 4. Missing KV binding -> graceful ----------------------------------
  {
    const r = await getAnalyticsSummary({}, 1);
    check('no KV -> unavailable', r.status === 'unavailable', `got ${r.status}`);
  }

  // ---- 5. Pagination loop terminates on exactly-1000 keys -----------------
  {
    const store = {};
    const kv = makeKV(store);
    for (let i = 0; i < 1000; i++) store[`analytics:page:${TODAY}:root:p${i}`] = '{}';
    const r = await getAnalyticsSummary({ SWARMLABS_KV: kv }, 1);
    const d = r.data && r.data[TODAY];
    check('exactly 1000 -> no infinite loop', d && d.page_views === 1000, `got ${d && d.page_views}`);
  }

  // ---- report -------------------------------------------------------------
  console.log('SwarmLabs analytics unit tests\n' + '='.repeat(78));
  let pass = 0;
  for (const r of results) {
    console.log(`  ${r.pass ? 'PASS' : 'FAIL'}  ${r.name}   ${r.pass ? '' : '-> ' + r.detail}`);
    if (r.pass) pass++;
  }
  console.log('-'.repeat(78));
  console.log(`${pass}/${results.length} passed`);
  process.exit(pass === results.length ? 0 : 1);
}

run().catch(e => { console.error('test harness error:', e); process.exit(2); });
