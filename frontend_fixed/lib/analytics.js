// SwarmLabs T1 — 轻量埋点模块（独立可复用，供主 API 文件 import）
// 零外部依赖；所有异常静默吞掉，绝不阻断主请求。
// 存储: Cloudflare KV (env.SWARMLABS_KV)，键格式 analytics:event:{date}:{path}:{uuid}
// 单条写入 + 90 天 TTL，summary 端点按日期聚合（无需回读 value，低延迟）。

/**
 * 记录一次 API 请求事件。
 * @param {object} env  Pages Functions 的 context.env（需含 SWARMLABS_KV）
 * @param {string} apiPath  请求路径（如 v2/run/drug_release）
 * @param {string} ip  客户端 IP
 */
export async function trackEvent(env, apiPath, ip) {
  try {
    const kv = env && env.SWARMLABS_KV;
    if (!kv || typeof kv.put !== 'function') return;
    const d = new Date().toISOString().slice(0, 10);
    const p = String(apiPath || 'unknown').replace(/[^a-zA-Z0-9_/-]/g, '_').slice(0, 80);
    const id = (typeof crypto !== 'undefined' && crypto.randomUUID)
      ? crypto.randomUUID()
      : Math.random().toString(36).slice(2);
    await kv.put(`analytics:event:${d}:${p}:${id}`,
      JSON.stringify({ t: Date.now(), ip: ip || 'unknown' }),
      { expirationTtl: 60 * 60 * 24 * 90 });
  } catch (e) { /* 埋点失败不影响主流程 */ }
}

/**
 * 按天聚合近 N 天 API 使用情况。
 * @param {object} env
 * @param {number} days  默认 7
 */
export async function getAnalyticsSummary(env, days = 7) {
  try {
    const kv = env && env.SWARMLABS_KV;
    if (!kv || typeof kv.list !== 'function') return { status: 'unavailable', error: 'SWARMLABS_KV 未配置' };
    const out = {};
    for (let i = 0; i < days; i++) {
      const d = new Date(Date.now() - i * 86400000).toISOString().slice(0, 10);
      const prefix = `analytics:event:${d}:`;
      const listed = await kv.list({ prefix });
      const byPath = {};
      for (const k of listed.keys) {
        const seg = k.name.slice(prefix.length).split(':');
        const p = seg[0] || 'unknown';
        byPath[p] = (byPath[p] || 0) + 1;
      }
      out[d] = { total: listed.keys.length, by_path: byPath };
    }
    return { status: 'ok', days, data: out };
  } catch (e) {
    return { status: 'error', error: String((e && e.message) || e) };
  }
}
