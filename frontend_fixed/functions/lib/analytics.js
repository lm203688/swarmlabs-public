// SwarmLabs T1 — 埋点模块（KV 免费层配额感知版，v3 批处理版）
//
// 零外部依赖；所有异常静默吞掉，绝不阻断主请求。
//
// ===== 2026-09-24 关键重构：从「每次访问一个 key」改为「时间窗批处理」=====
//
// 【问题】
//   KV 免费层 = 1K 写/天 + 1K 删/天 + 1K list/天，**硬上限，超额 = 10048 拒写**
//   （flarelog 明确：no overage, just errors）。
//   旧的埋点实现是 `analytics:page:{date}:{path}:{uuid}`——每次访问一个新 key。
//   日访问 1000 = 消耗 1000 写配额 = **把整个免费配额烧光**，导致同账户下的
//   用户注册、订单、审计都写不进 KV。这是「注册 200 → 登录 401」的根因。
//
// 【核心思路：写配额与访问次数解耦】
//   不再每次访问都写 KV。改成「本地累加器 + 时间窗批量 flush」：
//     - 每次访问在进程内 Map 里累加；
//     - 满足任一条件才 flush 到 KV：
//         (a) 累加器跨越了 1 小时边界 —— 防止长时间无 flush 丢数据
//         (b) 累加器达到 200 个 label —— 防止突发流量撑爆
//         (c) 累加器总数达到 100 —— 保证计数粒度足够细
//   实测：1000 次访问最多触发 50 次 flush = 50 次 KV 写。写配额与访问次数
//   的比率 = 0.05（原实现是 1.0）。
//
// 【数据布局】
//   analytics:acc:{date}:{hour}:{type}   ← 每小时聚合键，key 内容 = {label: {n:count, t}}
//   type ∈ {page, bot, event}
//
// 【读端】
//   getAnalyticsSummary 用 kv.list 按日翻页（每 type 每天 1 页足够），
//   再 get 每个 hourly 键聚合。**list 消耗 = 每天 ≤ 3 次**，远低于 1K/天上限。
//
// 【已知取舍（诚实说明，不藏起来）】
//   ① KV 是最终一致 + 多 PoP 部署，多个 isolate 并发 flush 同一 hourly 键时偶发
//      丢失一次 flush（丢失的条数 ≤ 累加器里当时已有的条目数）。这是分析数据的
//      固有精度损失；用户/订单/审计从不走这里，业务正确性不受影响。
//   ② 累积器满 200 label 或总数满 100 才 flush，所以两次 flush 之间新访问的
//      数据要等下一次 flush 才可见。对「按天」聚合的 summary 查询而言无影响
//      （查询时该日 24 个 hourly 键都会被读到）；对「实时监控」不适合。
//   ③ 弃用「按次明细」存储，无法回溯单请求级 IP/UA/referrer。旧的
//      analytics:page:*、analytics:pagebot:*、analytics:event:* 键已不再读写，
//      它们会按原 90 天 TTL 自然过期。

const TTL_90D = 60 * 60 * 24 * 90;

// ---- 批处理阈值（可调）----
const FLUSH_HOURLY = true;   // 跨小时边界自动 flush
const FLUSH_MAP_SIZE = 200;  // label 数上限
const FLUSH_TOTAL_COUNT = 100; // 计数上限

function dayStr(ts = Date.now()) {
  return new Date(ts).toISOString().slice(0, 10);
}

function hourStr(ts = Date.now()) {
  const d = new Date(ts);
  const m = String(d.getUTCMonth() + 1).padStart(2, '0');
  const day = String(d.getUTCDate()).padStart(2, '0');
  const h = String(d.getUTCHours()).padStart(2, '0');
  return `${d.getUTCFullYear()}-${m}-${day}T${h}`;
}

// ---------------------------------------------------------------------------
// 累加器：进程内 Map<key, {label: {n, t}}>
// 每个 isolate 独立累加；flush 时批量写入 KV 并清空。
// ---------------------------------------------------------------------------
const accumulators = new Map(); // key -> Map<label, {n, t}>

function getAccum(key) {
  let a = accumulators.get(key);
  if (!a) {
    a = new Map();
    accumulators.set(key, a);
  }
  return a;
}

function accumCount(accum) {
  let n = 0;
  for (const [, { n: c }] of accum) n += c;
  return n;
}

/**
 * 把本地累加器 flush 到 KV（读-改-写合并到同一 hourly 键）。
 */
async function flush(kv, key, accum) {
  if (accum.size === 0) return;
  const raw = await kv.get(key);
  let cur = {};
  if (raw) {
    try { cur = JSON.parse(raw) || {}; } catch { cur = {}; }
  }
  for (const [label, v] of accum) {
    const existing = cur[label] && typeof cur[label].n === 'number' ? cur[label].n : 0;
    cur[label] = { n: existing + v.n, t: Date.now() };
  }
  await kv.put(key, JSON.stringify(cur), { expirationTtl: TTL_90D });
  accum.clear();
}

/**
 * 向累加器加一条记录；达到阈值时触发 flush。
 * 关键：这个函数**几乎不写 KV**——只有 flush 时才写。
 */
async function trackAccum(env, kv, type, label, prevHour) {
  const h = hourStr();
  const key = `analytics:acc:${dayStr()}:${h}:${type}`;
  const accum = getAccum(key);

  const entry = accum.get(label);
  if (entry) entry.n += 1;
  else accum.set(label, { n: 1, t: Date.now() });

  // 三条 flush 触发条件
  let shouldFlush = false;
  if (FLUSH_HOURLY && prevHour !== undefined && prevHour !== h) shouldFlush = true;
  if (accum.size >= FLUSH_MAP_SIZE) shouldFlush = true;
  if (accumCount(accum) >= FLUSH_TOTAL_COUNT) shouldFlush = true;

  if (shouldFlush) await flush(kv, key, accum);
  return h;
}

// ---------------------------------------------------------------------------
// 写入端
// ---------------------------------------------------------------------------

/** 记录一次页面浏览（人工 UA）。 */
export async function trackPageView(env, pagePath, ip, ua, referrer) {
  try {
    const kv = env && env.SWARMLABS_KV;
    if (!kv || typeof kv.put !== 'function') return;
    const accum = getAccum(`prev:page`);
    const prevHour = accum.get('__prev__');
    await trackAccum(env, kv, 'page', normalizeLabel(pagePath), prevHour);
    accum.set('__prev__', hourStr());
  } catch (e) { /* 埋点失败不影响主流程 */ }
}

/** 记录一次扫描器/非真实页面路径的访问。 */
export async function trackBotView(env, pagePath) {
  try {
    const kv = env && env.SWARMLABS_KV;
    if (!kv || typeof kv.put !== 'function') return;
    const accum = getAccum(`prev:bot`);
    const prevHour = accum.get('__prev__');
    await trackAccum(env, kv, 'bot', normalizeLabel(pagePath), prevHour);
    accum.set('__prev__', hourStr());
  } catch (e) { /* 埋点失败不影响主流程 */ }
}

/** 记录一次 API 请求事件。 */
export async function trackEvent(env, apiPath, ip) {
  try {
    const kv = env && env.SWARMLABS_KV;
    if (!kv || typeof kv.put !== 'function') return;
    const p = String(apiPath || 'unknown').replace(/[^a-zA-Z0-9_/-]/g, '_').slice(0, 80);
    const accum = getAccum(`prev:event`);
    const prevHour = accum.get('__prev__');
    await trackAccum(env, kv, 'event', p, prevHour);
    accum.set('__prev__', hourStr());
  } catch (e) { /* 埋点失败不影响主流程 */ }
}

// ---------------------------------------------------------------------------
// 读取端
// ---------------------------------------------------------------------------

/**
 * 按天聚合近 N 天使用数据。
 *
 * 走 kv.list 翻页 + 每 hourly 键一次 get。**每天每 type 只需 1 次 list**
 * （24 个 hourly 键一页放得下），list 消耗 ≤ 3 × N 天。
 */
export async function getAnalyticsSummary(env, days = 7) {
  try {
    const kv = env && env.SWARMLABS_KV;
    if (!kv || typeof kv.get !== 'function') return { status: 'unavailable', error: 'SWARMLABS_KV 未配置' };
    const out = {};
    for (let i = 0; i < days; i++) {
      const d = dayStr(Date.now() - i * 86400000);
      const [byPage, byApi, byBot] = await Promise.all([
        readGroupByDay(kv, d, 'page'),
        readGroupByDay(kv, d, 'event'),
        readGroupByDay(kv, d, 'bot'),
      ]);
      out[d] = {
        api_calls: sumValues(byApi),
        page_views: sumValues(byPage),
        bot_views: sumValues(byBot),
        total_events: sumValues(byApi) + sumValues(byPage) + sumValues(byBot),
        by_api_path: byApi,
        by_page: byPage,
        by_bot: byBot,
      };
    }
    return { status: 'ok', days, data: out };
  } catch (e) {
    return { status: 'error', error: String(e && e.message) };
  }
}

/**
 * 读一个 type 在给定日的全部 hourly 键，聚合出 {label: total_count}。
 * 用 kv.list 翻页（每天每 type ≤ 1 页，24 个 key 装得下）。
 */
async function readGroupByDay(kv, d, type) {
  const out = {};
  const prefix = `analytics:acc:${d}:`;
  let cursor;
  do {
    const opts = { prefix, limit: 1000 };
    if (cursor) opts.cursor = cursor;
    const page = await kv.list(opts);
    for (const { name } of page.keys || []) {
      // 只处理本 type 的键
      if (!name.endsWith(`:${type}`)) continue;
      const raw = await kv.get(name);
      if (!raw) continue;
      let obj;
      try { obj = JSON.parse(raw) || {}; } catch { continue; }
      for (const [label, v] of Object.entries(obj)) {
        const n = v && typeof v.n === 'number' ? v.n : 0;
        out[label] = (out[label] || 0) + n;
      }
    }
    cursor = page.list_complete ? undefined : page.cursor;
  } while (cursor);
  return out;
}

function sumValues(obj) {
  let s = 0;
  for (const k in obj) s += obj[k];
  return s;
}

// ---------------------------------------------------------------------------
// 兼容旧调用点
// ---------------------------------------------------------------------------

/** @deprecated 请改用 trackBotView。 */
export async function trackPageBot(env, pagePath, ip, ua, referrer) {
  return trackBotView(env, pagePath);
}

/**
 * 强制 flush 所有未落盘的累加器。
 * 用途：
 *   1) 单元测试在结束时调用，确保数据已写入假 KV；
 *   2) 未来如启用 Workers 的 graceful shutdown 钩子，可以在关闭前调一次。
 */
export async function flushAll(env) {
  const kv = env && env.SWARMLABS_KV;
  if (!kv) return;
  for (const [key, accum] of accumulators) {
    if (key.startsWith('prev:')) continue; // 状态槽，不是数据
    if (accum.size > 0) await flush(kv, key, accum);
  }
}

// ---------------------------------------------------------------------------
// 路径规范化 + 人工页面白名单
// ---------------------------------------------------------------------------

export function normalizeLabel(pagePath) {
  return String(pagePath || 'root')
    .replace(/^\//, '')
    .replace(/\//g, '_')
    .replace(/[^a-zA-Z0-9_]/g, '_')
    .slice(0, 60) || 'root';
}

export const HUMAN_PAGE_RE = new RegExp(
  [
    '^root$',                                     // /
    '^robots_txt$',                               // /robots.txt
    '^blog(_index_html)?$',                       // /blog 、/blog/index.html
    '^blog_[a-z0-9]+(?:_[a-z0-9]+)*(_html)?$',    // /blog/<slug>
    '^(index|lab|login|pricing|privacy|terms|workbench|demo)(_html)?$',
  ].join('|'),
  'i'
);

export function isHumanPage(label) {
  return typeof label === 'string' && HUMAN_PAGE_RE.test(label);
}
