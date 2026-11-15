-- SwarmLabs 限流计数器（D1 / SQLite）
--
-- 为什么是 D1 而不是 KV：
--   spec §3 承诺 60 请求/分钟，spec §8 要求「第 61 次调用触发 429（不是 62、不是 59）」。
--   这是一个**精确**要求，而 Cloudflare 上能满足它的免费选项只有两个：
--     · D1 —— SQLite 单主库，写强一致，一条 UPSERT...RETURNING 即完成原子自增
--     · Durable Objects —— 同样强一致，但 Pages Functions **不能**自己定义 DO 类，
--       必须额外部署一个 Worker 来托管（见 docs 里的说明）
--   D1 是 Pages Functions 原生支持的 binding，不用第二个 Worker，所以选它。
--
-- 为什么不能用 KV（2026-09-17 实测否定）：
--   KV 读有边缘缓存（默认 60s）、写是最终一致、没有原子操作。
--   get → +1 → put 的 read-modify-write 会读到旧快照并丢更新。
--   实测 18 次连续请求：X-RateLimit-Remaining 在 57/58/59 之间摆动不递增，
--   X-RateLimit-Reset 甚至来回跳变。KV 路线已废弃。

CREATE TABLE IF NOT EXISTS rl (
  -- 计数主体：如 'ip:1.2.3.4'（当前按来源 IP；将来 per-key 只需改这里）
  k     TEXT    NOT NULL,
  -- 维度：'min' | 'burst' | 'day'
  kind  TEXT    NOT NULL,
  -- 窗口标识：min/burst 为窗口起始毫秒时间戳；day 为 'YYYY-MM-DD'（IST 日界）
  win   TEXT    NOT NULL,
  -- 窗口内已用次数
  cnt   INTEGER NOT NULL DEFAULT 0,
  -- 过期毫秒时间戳，供顺手清理（day 行用 0 表示不参与自动清理）
  exp   INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (k, kind, win)
);

-- 清理扫描用
CREATE INDEX IF NOT EXISTS rl_exp ON rl (exp);
