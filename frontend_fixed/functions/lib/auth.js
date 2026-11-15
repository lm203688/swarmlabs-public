// SwarmLabs v3 — lightweight API auth + metering scaffold (no external deps)
//
// Design (honest scope):
//   * Free tier: anonymous POST to v3 mutating endpoints, throttled by a daily
//     per-IP quota (default 50, configurable via env.FREE_DAILY_QUOTA).
//   * Premium tier: requests carrying a valid x-api-key (comma-separated list in
//     env.API_KEYS) bypass the quota. This is the hook that lets the value-based
//     pricing (Pro / Premium / Team) map to real usage metering.
//   * GET endpoints (health / scenarios / strains / media / openapi) stay open.
//
// All KV ops are best-effort: on any KV error we fail OPEN (allow) so the demo
// never hard-breaks, but we still log via the existing analytics bucket.

const MUTATING_RE = /^\/api\/v3\/(predict|fit|guard|sample|submit-strain)$/;

export function parseKey(request) {
  const h = request.headers.get('x-api-key');
  if (h) return h.trim();
  try {
    const u = new URL(request.url);
    const q = u.searchParams.get('api_key');
    return q ? q.trim() : null;
  } catch { return null; }
}

export function isPremium(key, env) {
  if (!key || !env || !env.API_KEYS) return false;
  const list = String(env.API_KEYS).split(',').map(s => s.trim()).filter(Boolean);
  return list.includes(key);
}

function dayStr() { return new Date().toISOString().slice(0, 10); }

async function readCount(kv, k) {
  try { const v = await kv.get(k); return v ? parseInt(v, 10) || 0 : 0; }
  catch { return 0; }
}

export function isMutating(request) {
  return request.method === 'POST' && MUTATING_RE.test(new URL(request.url).pathname);
}

// Returns null if allowed, or a Response to short-circuit with 429.
export async function gateApi(request, env) {
  if (!isMutating(request)) return null;
  const key = parseKey(request);
  if (key && isPremium(key, env)) return null; // premium: no quota

  const kv = env && env.SWARMLABS_KV;
  const limit = parseInt((env && env.FREE_DAILY_QUOTA) || '50', 10) || 50;
  const ip = request.headers.get('cf-connecting-ip') || 'unknown';
  const k = `analytics:quota:${dayStr()}:${ip}`;

  if (kv && typeof kv.get === 'function') {
    const used = await readCount(kv, k);
    if (used >= limit) {
      return new Response(JSON.stringify({
        error: 'Free daily quota reached',
        limit,
        upgrade: 'https://swarmlabs.tools/pricing',
        hint: 'Add x-api-key header to lift the limit (Pro/Premium/Team).'
      }), { status: 429, headers: { 'Content-Type': 'application/json', 'Retry-After': '86400' } });
    }
    // best-effort increment
    try {
      await kv.put(k, String(used + 1), { expirationTtl: 60 * 60 * 24 * 2 });
    } catch { /* fail open */ }
  }
  return null;
}
