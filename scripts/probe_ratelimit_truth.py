#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
验证「限流真在计数」——两项：

  T1 单调性：同一来源连续请求，X-RateLimit-Remaining 必须随请求**非增**。
     这是上一版 KV 计数器缺失的不变量（实测在 57/58/59 之间摆动 = 假计数）。
  T2 契约自述：X-RateLimit-Scope 必须存在，且 /v3/partner/usage 的
     enforcement_scope 与之一致。
  T3 第 61 次：按 spec §8 打 61 次，看是否 429（不预设结论，如实报）。

判据单独打印，避免"跑通了"和"断言通过"混为一谈。
"""
import json
import ssl
import sys
import time
import random
import urllib.request
import urllib.error

BASE = (sys.argv[1] if len(sys.argv) > 1 else "https://swarmlabs.tools").rstrip("/")
UA = "SwarmLabs-RateLimit-Verify/2.0"
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def get(path, tries=3):
    last = None
    for _ in range(tries):
        req = urllib.request.Request(BASE + path, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=25, context=CTX) as r:
                return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
        except urllib.error.HTTPError as e:
            return e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, e.read()
        except Exception as e:
            last = "%s: %s" % (type(e).__name__, e)
            time.sleep(0.4)
    raise RuntimeError("transport: " + str(last))


def uniq(path):
    return "%s%c_=%d%d" % (path, "&" if "?" in path else "?",
                           int(time.time() * 1000), random.randint(100, 999))


# ---------- 先确认新代码已上线 ----------
st, hd, body = get(uniq("/api/health"))
scope = hd.get("x-ratelimit-scope")
print("新代码标记 X-RateLimit-Scope = %r   (None 表示还是旧部署)" % scope)
print("  health status=%s limit=%s remaining=%s reset=%s"
      % (st, hd.get("x-ratelimit-limit"), hd.get("x-ratelimit-remaining"),
         hd.get("x-ratelimit-reset")))
if scope is None:
    print("  !! 新代码未上线，后续断言无意义 —— 等传播后重跑")
    sys.exit(3)

# ---------- T2 契约自述 ----------
st, hd, body = get(uniq("/v3/partner/usage"))
try:
    u = json.loads(body)
    print("\nT2 /v3/partner/usage: enforcement_scope=%r day_quota_enforced=%r used_day=%r"
          % (u.get("enforcement_scope"), u.get("day_quota_enforced"), u.get("used_day")))
    print("   scope_basis=%r" % u.get("scope_basis"))
    print("   rate_per_min=%s burst_10s=%s rate_per_day=%s"
          % (u.get("rate_per_min"), u.get("burst_10s"), u.get("rate_per_day")))
    ok_t2 = (u.get("enforcement_scope") == scope)
    print("   T2 %s：usage 与响应头一致（%r vs %r）"
          % ("PASS" if ok_t2 else "FAIL", u.get("enforcement_scope"), scope))
except Exception as e:
    print("   T2 FAIL：usage 解析失败 %s / body=%r" % (e, body[:200]))
    ok_t2 = False

# ---------- T1 单调性 ----------
print("\nT1 单调性：12 次连续请求（每次唯一 query 强制绕过 CDN 缓存）")
seq = []
for i in range(12):
    st, hd, _ = get(uniq("/api/entities/search?q=monotonic-probe&limit=1"))
    r = hd.get("x-ratelimit-remaining")
    seq.append((st, r, hd.get("x-ratelimit-scope")))
    time.sleep(0.45)
for i, (s, r, sc) in enumerate(seq, 1):
    print("   #%-2d status=%s remaining=%-4s scope=%s" % (i, s, r, sc))
nums = []
for _, r, _ in seq:
    try:
        nums.append(int(r))
    except (TypeError, ValueError):
        nums.append(None)
if any(n is None for n in nums):
    print("   T1 FAIL：有请求没带 Remaining 头")
    ok_t1 = False
else:
    violations = [(i + 1, nums[i], nums[i + 1])
                  for i in range(len(nums) - 1) if nums[i + 1] > nums[i]]
    # 窗口重置会让 Remaining 跳回高位 —— 只有 reset 变化时才允许
    print("   T1 %s：Remaining 序列 %s"
          % ("PASS（非增）" if not violations else "FAIL（出现回升）", nums))
    if violations:
        print("      回升点（调用序号, 前值, 后值）: %s" % violations[:5])
    ok_t1 = not violations

# ---------- T3 spec §8：第 61 次 ----------
print("\nT3 spec §8：同一分钟第 61 次调用是否 429")
st0, hd0, _ = get(uniq("/api/health"))
reset = int(hd0.get("x-ratelimit-reset") or 0)
wait = reset - int(time.time()) + 2
if 0 < wait <= 75:
    print("   等 %ds 让窗口重置..." % wait)
    time.sleep(wait)
first429 = None
seq3 = []
for i in range(1, 66):
    st, hd, _ = get(uniq("/api/entities/search?q=spec8.8&limit=1"))
    seq3.append(st)
    if st == 429 and first429 is None:
        first429 = i
    # 0.7s ≈ 14 req/10s，不碰自身 20/10s 突发闸
    time.sleep(0.7)
print("   状态序列尾部: %s" % seq3[-8:])
print("   首次 429 出现在第 %s 次" % (first429 if first429 else "（整轮无 429）"))
print("   按剩余时间为该窗口内已用量: 若 %d 次内无 429，则本窗口实际放行 > %d" % (len(seq3), len(seq3)))

print("\n== 汇总 ==")
print("  T1 单调性        : %s" % ("PASS" if ok_t1 else "FAIL"))
print("  T2 契约自述一致  : %s" % ("PASS" if ok_t2 else "FAIL"))
print("  T3 第61次 429    : %s" % ("PASS" if first429 == 61 else
      ("观察到 429 但落在第 %s 次" % first429 if first429 else "未观察到 429")))
