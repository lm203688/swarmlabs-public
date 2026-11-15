// SwarmLabs middleware — 页面浏览埋点 + 扫描器过滤
// 在 Pages Function 执行前运行，不阻断正常请求。
//
// 背景（2026-08-29 实测）：单日 page_views=1000 中约 670 条来自全网漏洞扫描器，
// 探测路径形如 /env、/2021/phpinfo.php、/.aws/credentials、/_next、/circleci、
// /.git/config 等。站点原先对这类路径返回 200（SPA fallback），导致：
//   1) 流量数据被噪声淹没，无法判断真实用户；
//   2) 站点被扫描器标记为"活跃/值得深挖的主机"，招致更多探测。
//
// 处理策略：
//   1) 已知漏洞探测路径 → 直接 404，且不记录埋点（切断"响应=有趣"的信号）。
//   2) 已知扫描器/无头浏览器 UA，或 路径不在真实页面白名单内 → 记入独立桶
//      analytics:pagebot:（不计入 page_views）。数据保留但不污染人工流量统计。
//   3) 真实页面（白名单）+ 非扫描器 UA → 正常 analytics:page: 埋点。
//
// 为何用白名单而非黑名单（2026-08-29 实测结论）：黑名单只覆盖 .env/.git/phpinfo 等经典
// 探测，实测单日仍有 720 个不同标签漏过，含 K8s 探针（rest_v1_、v2_keys_、apply_）、
// Elasticsearch（_cat_indices、_cluster_settings）、Docker（containers_json、images_json）、
// 监控端点（metrics、tree、info、version、admin）与双重编码点
// （_252eenv_252eaws、_252eterraform_252elock_252ehcl）。黑名单永远追不上扫描器，
// 而本站真实页面只有 63 个 HTML 文件，白名单才能一次收口。
//
// 注意：扫描器常用 %2f / %252f / %252e 多重编码绕过朴素路径匹配，因此先解码（最多两轮）再匹配。
// 新增页面：真实页面白名单定义在 lib/analytics.js 的 HUMAN_PAGE_RE（blog 文章自动匹配）。

import { isHumanPage, normalizeLabel, trackPageView, trackBotView } from './lib/analytics.js';
import { gateApi } from './lib/auth.js';

const PROBE_RE = new RegExp(
  [
    '\\.env(?![a-z])', '(^|/)env([._/-]|$)', '\\.env\\.example', '\\.git', '\\.svn', '\\.hg',
    '\\.gitignore', '\\.gitattributes', '\\.gitmodules', '\\.htaccess',
    '\\.DS_Store', '\\.bash_history', '\\.mysql_history', '\\.psql_history',
    '\\.ssh', 'id_rsa', 'authorized_keys', 'phpinfo', 'phpmyadmin',
    '\\.php', '\\.jsp', '\\.asp', '\\.aspx', '\\.cgi', 'cgi-bin',
    '\\.aws', 'aws_credentials', 'aws_config', 'aws_sso_cache',
    '_debug__', 'actuator', 'heapdump', 'amplify', 'drone\\.yml',
    'circleci', 'elasticbeanstalk', 'fragment', '_next', 'next\\.config',
    'wp-login', 'wp-admin', 'wp-content', 'wp-json', 'xmlrpc',
    '\\.bak$', '\\.sql$', '\\.sqlite', '\\.pem$', '\\.p12$', '\\.jks$',
    'config\\.json'
  ].join('|'),
  'i'
);

// 安全扫描器 / 无头浏览器。刻意不含 Googlebot/Bingbot 等搜索引擎爬虫，
// 也不含 python-urllib / go-http-client 等通用库（合法 API 客户端同样在用）。
const SCANNER_UA_RE = new RegExp(
  [
    'masscan', 'nmap', 'nikto', 'gobuster', 'dirb', 'dirbuster', 'wpscan',
    'sqlmap', 'acunetix', 'qualys', 'nessus', 'openvas', 'metasploit',
    'w3af', 'zaproxy', 'httpx', 'nuclei', 'zmeu', 'awvs', 'appspider',
    'burp', 'headlesschrome', 'phantomjs', 'playwright', 'puppeteer',
    'zgrab', 'whatweb', 'sslscan', 'testssl', 'joomscan', 'openvas'
  ].join('|'),
  'i'
);

/** 最多解码两轮，用于匹配与标签生成（仅用于分类，不用于文件查找）。 */
function decodeDeep(path) {
  let out = path;
  for (let i = 0; i < 2; i++) {
    try { out = decodeURIComponent(out); } catch (e) { break; }
  }
  return out;
}

// 已废弃（2026-09-24）：埋点改写为 lib/analytics.js 的按天聚合写入，
// 因此本文件内不再需要本地 normalize 与 record 函数。normalizeLabel 由
// lib/analytics.js 导出，此处直接调用。

export async function onRequest(context) {
  const { request, env, next } = context;
  const url = new URL(request.url);
  const pathname = url.pathname;

  // API 路由：premium/限额网关（free GET 仍放行，仅拦截 v3 写/计量端点）
  // 2026-09-09：裸 /v2/* 与 /v3/* 现在同样过网关。
  // 背景：Pages Function 只挂在 /api/ 下，裸路径此前一律落到 index.html（200 text/html），
  // 导致按直觉调用 /v2/health、/v3/scenarios 的客户端拿到 HTML 而非 JSON。
  // 修复方式不是 context.rewrite（Pages Functions 无此 API，调用直接 500），
  // 而是新增 functions/v2、functions/v3 路由垫片；这里同步补上网关，
  // 确保裸路径不会绕过 premium 限额。
  const apiLike = pathname.startsWith('/api/')
    || (/^\/(v2|v3)\//.test(pathname) && !/\.[a-z0-9]+$/i.test(pathname));
  if (apiLike) {
    try {
      const block = await gateApi(request, env);
      if (block) return block;
    } catch (e) { /* 网关异常不阻断主流程 */ }
    return await next();
  }

  // 静态资源：不埋点
  if (/\.(js|css|png|jpg|jpeg|gif|ico|svg|woff2|woff|eot|ttf|xml|json|map|webp|avif)$/.test(pathname)) {
    return await next();
  }

  // 1) 漏洞探测路径 → 404，不记录
  const probePath = decodeDeep(pathname);
  if (PROBE_RE.test(probePath)) {
    return new Response(JSON.stringify({ error: 'not found' }), {
      status: 404,
      headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' }
    });
  }

  const ua = request.headers.get('user-agent') || '';
  const isScanner = SCANNER_UA_RE.test(ua);
  const isRealPage = isHumanPage(normalizeLabel(probePath));

  // 2/3) 埋点：扫描器 UA 或非真实页面路径 → 独立桶，不计入 page_views
  // 2026-09-24：改走 lib/analytics.js 的按天聚合写入，避免每次访问一个唯一 key
  // 把 KV 免费层 1K 写/天配额烧光（配额耗尽会导致用户注册/订单写不进 KV）。
  try {
    const kv = env && env.SWARMLABS_KV;
    if (kv && typeof kv.put === 'function') {
      const ip = request.headers.get('cf-connecting-ip') || 'unknown';
      const ref = request.headers.get('referer') || '';
      if (isScanner || !isRealPage) {
        await trackBotView(env, pathname);
      } else {
        await trackPageView(env, pathname, ip, ua, ref);
      }
    }
  } catch (e) { /* 埋点失败不影响主流程 */ }

  return await next();
}
