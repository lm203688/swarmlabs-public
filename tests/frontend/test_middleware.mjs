#!/usr/bin/env node
// Unit tests for frontend_fixed/functions/_middleware.js
// Tests the real onRequest() behavior: probe blocking, scanner bucketing, legit passthrough.
// Run: node tests/frontend/test_middleware.mjs
import { onRequest } from '../../frontend_fixed/functions/_middleware.js';

const BASE = 'https://swarmlabs.tools';

let kvStore = {};
let nextCalled = false;

function makeEnv() {
  return {
    SWARMLABS_KV: {
      put: async (key, value) => { kvStore[key] = value; }
    }
  };
}

function makeContext(path, { ua = 'Mozilla/5.0 (Mozilla/5.0)', scanner = false } = {}) {
  nextCalled = false;
  const url = new URL(BASE + path);
  const headers = new Headers({
    'user-agent': ua,
    'cf-connecting-ip': '203.0.113.7',
    'referer': 'https://example.org/landing'
  });
  return {
    request: new Request(url.toString(), { headers }),
    env: makeEnv(),
    next: async () => { nextCalled = true; return new Response('ok', { status: 200 }); }
  };
}

function isScannerUA() {
  return /nuclei|masscan|nikto|gobuster|headlesschrome|sqlmap/i.test(arguments[0] || '');
}

const results = [];
function check(name, cond, detail = '') {
  results.push({ name, pass: !!cond, detail: String(detail).slice(0, 110) });
}

async function run() {
  kvStore = {};

  // ---- 1. Vulnerability probe paths -> 404, no analytics record -----------
  const probes = [
    '/env', '/env.example', '/2020/env', '/env.bak',
    '/2021/phpinfo.php', '/phpinfo.php', '/admin.php',
    '/.git/config', '/.git/HEAD', '/.svn/entries',
    '/.aws/credentials', '/_debug__/ping', '/_next', '/circleci',
    '/wp-login.php', '/wp-admin', '/xmlrpc.php', '/actuator'
  ];
  for (const p of probes) {
    kvStore = {}; nextCalled = false;
    const ctx = makeContext(p);
    const res = await onRequest(ctx);
    check(`404 ${p}`, res.status === 404 && !nextCalled && Object.keys(kvStore).length === 0,
      `status=${res.status} next=${nextCalled} kv=${Object.keys(kvStore).length}`);
  }

  // ---- 2. Double-encoding evasion -> still 404 ---------------------------
  for (const p of ['/%2f.env', '/%252f.env', '/%2f.git%2fconfig', '/2021%2fphpinfo.php']) {
    kvStore = {}; nextCalled = false;
    const res = await onRequest(makeContext(p));
    check(`404 ${p} (encoded)`, res.status === 404 && !nextCalled, `status=${res.status}`);
  }

  // ---- 3. Real pages (allowlist) are bucketed as human traffic ------------
  // /robots.txt is NOT blocked on purpose: the site ships a real content-signal
  // robots.txt that static hosting serves ahead of the middleware.
  const legit = ['/', '/robots.txt', '/pricing', '/login', '/workbench',
                 '/lab', '/terms', '/blog', '/blog/ai-research-uncertainty.html'];
  for (const p of legit) {
    kvStore = {}; nextCalled = false;
    const res = await onRequest(makeContext(p));
    const keys = Object.keys(kvStore);
    check(`human ${p}`, res.status === 200 && nextCalled && keys.length === 1 && keys[0].startsWith('analytics:page:'),
      `status=${res.status} next=${nextCalled} key=${keys[0]}`);
  }

  // ---- 3b. Unknown paths -> noise bucket (still served 200 by the SPA) -----
  // These are the long tail that a blocklist cannot cover (K8s/ES/Docker/monitoring
  // probes and generic path guessing). They must NOT inflate page_views.
  const noise = ['/environment', '/envy', '/version', '/admin', '/metrics',
                 '/api-docs', '/unknown-xyz', '/rest/v1/'];
  for (const p of noise) {
    kvStore = {}; nextCalled = false;
    const res = await onRequest(makeContext(p));
    const keys = Object.keys(kvStore);
    check(`noise ${p}`, res.status === 200 && nextCalled && keys.length === 1 && keys[0].startsWith('analytics:pagebot:'),
      `status=${res.status} next=${nextCalled} key=${keys[0]}`);
  }

  // ---- 4. Scanner UA -> separate pagebot bucket, still served ------------
  for (const ua of ['Mozilla/5.0 (compatible; masscan/1.0)',
                     'Mozilla/5.0 (compatible; nuclei/3.0)',
                     'Mozilla/5.0 HeadlessChrome/120.0']) {
    kvStore = {}; nextCalled = false;
    const ctx = makeContext('/workbench', { ua });
    const res = await onRequest(ctx);
    const keys = Object.keys(kvStore);
    check(`scanner-UA ${ua.slice(0, 34)}...`,
      res.status === 200 && nextCalled && keys.length === 1 && keys[0].startsWith('analytics:pagebot:'),
      `status=${res.status} key=${keys[0]}`);
  }

  // ---- 5. API routes skip analytics entirely ------------------------------
  kvStore = {}; nextCalled = false;
  const apiRes = await onRequest(makeContext('/api/v3/health'));
  check('api route skips analytics', apiRes.status === 200 && !Object.keys(kvStore).length,
    `status=${apiRes.status} kv=${Object.keys(kvStore).length}`);

  // ---- 6. Missing KV binding degrades gracefully --------------------------
  kvStore = {}; nextCalled = false;
  const ctx = makeContext('/pricing');
  ctx.env = {};
  const res = await onRequest(ctx);
  check('no KV -> still 200', res.status === 200 && nextCalled, `status=${res.status} next=${nextCalled}`);

  // ---- report -------------------------------------------------------------
  console.log('SwarmLabs middleware unit tests\n' + '='.repeat(78));
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
