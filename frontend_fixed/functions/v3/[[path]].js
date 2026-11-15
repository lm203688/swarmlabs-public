// SwarmLabs API 路由垫片（2026-09-09）
//
// 目的：让裸 /v3/* 请求返回 JSON，而不是 SPA 的 index.html 兜底页面（200 text/html）。
// 机制：本文件是 /v3/ 下的 catch-all 路由，把捕获到的 path 前面补回 'v3'，
//       再交给 functions/api/[[path]].js 里的单体分发器 handle()，
//       使其看到的 apiPath 与 /api/v3/* 完全一致（/v3/scenarios -> 'v3/scenarios'）。
//
// 背景与约束：
//   * Pages Function 只挂在 functions/api/ 下，只服务 /api/*，所以裸 /v3/* 原本无函数匹配。
//   * Pages Functions 的 middleware 没有 context.rewrite（调用会抛 TypeError -> 500），
//     因此不能靠重写，只能用这种目录垫片承载。
//   * premium/限额网关已在 _middleware.js 中对 /v3/* 生效（会拦截 v3 写/计量端点），
//     本垫片不绕过限额。
//
// 影响：未知 apiPath 由 handle() 返回 404 JSON（优于原先的 200 HTML 兜底）。
import {
  onRequestGet as _get,
  onRequestPost as _post,
  onRequestOptions as _options
} from '../api/[[path]].js';

/** 在 catch-all 捕获的 path 前面补上版本前缀，复用同一分发器。 */
function withPathPrefix(context, prefix) {
  const p = (context.params && context.params.path) || [];
  return Object.assign({}, context, {
    params: Object.assign({}, context.params, { path: [prefix].concat(p) })
  });
}

export async function onRequestGet(c) { return _get(withPathPrefix(c, 'v3')); }
export async function onRequestPost(c) { return _post(withPathPrefix(c, 'v3')); }
export async function onRequestOptions(c) { return _options(withPathPrefix(c, 'v3')); }
