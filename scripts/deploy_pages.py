"""部署 frontend_fixed/ 到 Cloudflare Pages（project=swarmlabs），并在线复验。

用法：
  python scripts/deploy_pages.py              # 部署 + 复验
  python scripts/deploy_pages.py --verify-only # 只复验线上（不改部署）

凭证取自 ~/.workbuddy/secrets/swarmlabs_cf.json（仓库外存储，不入库）。

两个实测坑（本脚本因此存在）：
  1) Windows 下 `subprocess.run(["npx", "wrangler@4", ...])` 报
     FileNotFoundError——npx 是 .cmd 包装器，Python CreateProcess 不认。
     改为直接调 `node + wrangler/bin/wrangler.js` 绝对路径。
  2) 复验时 **HTTPError 必须当作正常应答返回状态码**，不能当网络错误重试：
     /v3/anchors/surrogate_branin 期望 404，把它当网络故障吞掉会误报 0 造成假失败。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error
import ssl

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONT = os.path.join(ROOT, "frontend_fixed")
SECRETS = os.path.expanduser("~/.workbuddy/secrets/swarmlabs_cf.json")

cred = json.load(open(SECRETS, encoding="utf-8"))
env = dict(os.environ)
env["CLOUDFLARE_API_TOKEN"] = cred["CLOUDFLARE_API_TOKEN"]
env["CLOUDFLARE_ACCOUNT_ID"] = cred["CLOUDFLARE_ACCOUNT_ID"]

# Windows 下 npx 不在 PATH，且 Python CreateProcess 无法直接执行 .cmd 包装器，
# 因此直接走「node + wrangler bin 脚本」的绝对路径。
NODE = r"C:\Users\xing\.workbuddy\binaries\node\versions\22.22.2-6\node.exe"
WRANGLER = (r"C:\Users\xing\.workbuddy\binaries\node\workspace\node_modules"
            r"\wrangler\bin\wrangler.js")
if not (os.path.exists(NODE) and os.path.exists(WRANGLER)):
    NODE = os.environ.get("SWARMLABS_NODE", NODE)
    WRANGLER = os.environ.get("SWARMLABS_WRANGLER", WRANGLER)
if not (os.path.exists(NODE) and os.path.exists(WRANGLER)):
    sys.stderr.write(
        f"[err] 找不到 node 或 wrangler：\n  NODE={NODE}\n  WRANGLER={WRANGLER}\n"
        "请把上面路径改成本机实际位置，或设置环境变量 SWARMLABS_NODE / SWARMLABS_WRANGLER。")
    sys.exit(2)

# ============================================================================
# 部署完整性门禁（2026-09-17 新增，本文件存在的主要理由之一）
# ----------------------------------------------------------------------------
# 实测事实（当日 10 个连续部署逐一对账）：
#   wrangler 每次都报 `Uploading... (374/374)` + `Success! Uploaded 374 files`，
#   但 Cloudflare 侧的部署**有 6/10 是缺件的**——缺的正是
#   reports/benchmark/ 下 62 个场景文件（只留了 index.json）。
#   缺件与 wrangler 的 Source commit 一一对应（同 sha 结果一致），
#   说明它不是网络抖动，而是**某些部署在服务端落地不完整**。
#
# 为什么此前一直没被发现：Pages 对「未命中的路径」不返回 404，
#   而是返回 SPA 的 index.html，状态码 **200 text/html**。
#   于是任何「状态码是 200 就算部署成功」的复验都会绿着通过，
#   而真实用户拿到的是兜底页。/v3/verify 因此以 ~15% 概率
#   把「资产读不到」渲染成 404 unknown_scenario —— 一个**错误答案**。
#
# 判据因此必须落在**内容**而不是状态码上：
#   逐文件 GET 新部署域名的每个本地资产，比对远端响应的前若干字节
#   与本地文件是否一致。不一致（或非 200）即判定缺件/被替换。
#   用**部署专属域名**而不是自定义域名，避免再被 zone 缓存遮住真相。
# ============================================================================

SKIP_ROOT_NAMES = {".assetsignore", "_headers", "_redirects", "_routes.json"}
# functions/ 是 Pages Functions 的源码目录，会被单独打包成 Worker bundle，
# **不作为静态资产提供**（请求它只会拿到 SPA 兜底页）。2026-09-17 实测：
# 不排除它会让门禁报 7 个假阳性，进而触发无谓的重部署。
SKIP_ROOT_DIRS = {"functions"}
DEPLOY_URL = None


def _load_assetsignore(root):
    """读取 .assetsignore（仅支持简单的 glob 与目录名，够用且不引第三方）。"""
    pats = []
    fp = os.path.join(root, ".assetsignore")
    if os.path.exists(fp):
        for line in open(fp, encoding="utf-8", errors="replace"):
            line = line.strip()
            if line and not line.startswith("#"):
                pats.append(line)
    return pats


def collect_assets(root):
    """列出所有会被 Pages 当作静态资产上传的文件（相对路径，/ 分隔）。"""
    import fnmatch
    pats = _load_assetsignore(root)
    skip_dirs = {".wrangler", ".build-check", "node_modules"}
    out = []
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns
                  if d not in skip_dirs and not (dp == root and d in SKIP_ROOT_DIRS)]
        for fn in fns:
            ap = os.path.join(dp, fn)
            rel = os.path.relpath(ap, root).replace(os.sep, "/")
            if any(seg in skip_dirs for seg in rel.split("/")[:-1]):
                continue
            top = rel.split("/")[0]
            if rel in SKIP_ROOT_NAMES or top in SKIP_ROOT_NAMES or top in SKIP_ROOT_DIRS:
                continue
            if any(fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(fn, p)
                   or rel.startswith(p.rstrip("/") + "/") for p in pats):
                continue
            out.append(rel)
    return sorted(out), pats


def wait_deploy_ready(dep_url, root=None, timeout=120, interval=5):
    """等新建的 <hash> 域名真的开始提供本次部署的 /index.html。

    2026-09-17 实测：刚 deploy 完立刻全树扫描会拿到一批 404（连 /index.html 都 404），
    几十秒后同一域名完全正常。**那是传播延迟，不是缺件。**
    不先等就扫描，门禁会把"还没生效"报成"部署残缺"，进而触发无谓的重部署——
    而重部署本身又制造一个新的未生效域名，形成自我伤害的循环。
    """
    import time
    global DEPLOY_URL
    root = root or FRONT
    DEPLOY_URL = dep_url.rstrip("/")
    try:
        local = open(os.path.join(root, "index.html"), "rb").read(512)
    except Exception:
        return True  # 没有 index.html 就无从指纹化，交给后面的扫描自行判断
    base = dep_url.rstrip("/")
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        st, ct, body = _sniff("index.html")
        last = f"HTTP {st} ct={ct}"
        if st == 200 and body == local:
            print(f"  ok  部署域名已就绪（{time.time()-t0:.0f}s）")
            return True
        time.sleep(interval)
    print(f"  ! 等待超时（{timeout}s）：{base}/index.html 最后状态 {last}")
    return False


def _sniff(rel):
    """GET 一个资产，返回 (status, content_type, 远端前 512 字节)。

    只读 512 字节：够判定"是不是同一份内容"，又不把整棵树拉下来。
    """
    import urllib.request
    import urllib.error
    url = DEPLOY_URL + "/" + rel
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "swarmlabs-deploy-gate/1.0",
                          "Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.headers.get("content-type", ""), r.read(512)
    except urllib.error.HTTPError as e:
        return e.code, (e.headers.get("content-type", "") if e.headers else ""), e.read(512)
    except Exception as e:
        return 0, "net_error:" + repr(e)[:60], b""


def gate_assets(dep_url, root=None, workers=16):
    """逐文件比对部署上的资产与本地文件。返回 (ok, total, bad[]) 。

    判定用的是**内容**而不是状态码：Pages 对未命中路径返回 SPA 的
    index.html（200 text/html），所以「200 即成功」在这里必然假绿。
    这里额外取部署自身 /index.html 的前 512 字节当指纹，
    一旦某个非 index.html 的资产返回了这段字节，就是确凿的 SPA 兜底。
    """
    import concurrent.futures as cf
    global DEPLOY_URL
    DEPLOY_URL = dep_url.rstrip("/")
    root = root or FRONT
    rels, _ = collect_assets(root)

    _st, _ct, index_fp = _sniff("index.html")
    if _st != 200:
        print(f"  ! 取不到部署的 /index.html（HTTP {_st}），SPA 兜底无法指纹化")

    def one(rel):
        lp = os.path.join(root, rel.replace("/", os.sep))
        try:
            local = open(lp, "rb").read(512)
        except Exception as e:
            return rel, "local_read_error:" + repr(e)[:40]
        st, ct, remote = _sniff(rel)
        if st != 200:
            return rel, f"HTTP {st} ct={ct}"
        if remote == local:
            return None
        if index_fp and remote[:len(index_fp)] == index_fp and rel != "index.html":
            return rel, f"SPA_fallback_html（拿到 {len(remote)}B 的 index.html 而非资产）"
        return rel, f"content_mismatch (local {len(local)}B vs remote {len(remote)}B, ct={ct})"

    bad = []
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        for res in ex.map(one, rels):
            if res:
                bad.append(res)
    return (not bad), len(rels), bad


def deploy_once():
    """跑一次 wrangler deploy，返回部署专属域名（失败返回 None）。"""
    import re
    print("== 部署到 Cloudflare Pages (project=swarmlabs) ==")
    t0 = time.time()
    p = subprocess.run(
        [NODE, WRANGLER, "pages", "deploy", ".", "--project-name", "swarmlabs",
         "--skip-caching", "--commit-dirty=true"],
        cwd=FRONT, env=env, capture_output=True, text=True, timeout=900)
    out = (p.stdout or "") + (p.stderr or "")
    print(out[-1800:])
    print(f"exit={p.returncode}  ({time.time()-t0:.0f}s)")
    if p.returncode != 0:
        print("[FAIL] wrangler 退出非零")
        return None
    m = re.search(r"https://([0-9a-f]{8})\.swarmlabs-dnh\.pages\.dev", out)
    if not m:
        print("[FAIL] 无法从 wrangler 输出解析部署域名")
        return None
    return m.group(0)


if "--gate-only" in sys.argv:
    _t = sys.argv[sys.argv.index("--gate-only") + 1]
    print(f"== 门禁单跑：{_t} ==")
    wait_deploy_ready(_t)
    _ok, _total, _bad = gate_assets(_t)
    print(f"  {_total - len(_bad)}/{_total} 资产与本地逐字节一致")
    for _rel, _why in _bad[:40]:
        print(f"    {_rel:<52} {_why}")
    if len(_bad) > 40:
        print(f"    … 另有 {len(_bad) - 40} 个")
    sys.exit(0 if _ok else 1)

# ---------------------------------------------------------------------------
# 部署前置闸门（2026-09-24 新增）
# ---------------------------------------------------------------------------
# gate_assets 是**事后**闸门：它比对的是已经上线的资产，等它报错时线上已经是
# 一份被截断的账本了（2026-09-23 实测：rdkit 缺失重建 → 82 塌成 80 → 唯一的
# REFUTED 场景 mol_suzuki_yield 从公开面消失）。这里把闸门前移到上传之前。
#
# 为什么必须内置而不是靠 CI：本仓库是私有仓库，GitHub branch protection 是付费
# 功能（PUT /branches/main/protection -> 403 "Upgrade to GitHub Pro…"，2026-09-24
# 实测）。所以 .github/workflows/honesty-gate.yml 判红也挡不住推送。闸门必须落在
# 部署动作本身，而不是提交流程上。
if "--skip-preflight" not in sys.argv:
    _pf = subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "preflight_deploy.py")],
        cwd=ROOT)
    if _pf.returncode != 0:
        print("[FAIL] 部署前置闸门未通过 —— 本次部署被中止，线上保持不变")
        sys.exit(2)
else:
    print("!! --skip-preflight：前置闸门被跳过（仅限紧急情况）")

if "--verify-only" not in sys.argv:
    DEPLOY_URL = None
    for attempt in range(1, 4):
        url = deploy_once()
        if not url:
            sys.exit(1)
        print(f"\n== 部署完整性门禁（第 {attempt}/3 次）：{url} ==")
        ready = wait_deploy_ready(url)
        t0 = time.time()
        ok, total, bad = gate_assets(url)
        print(f"  {total - len(bad)}/{total} 资产与本地逐字节一致  ({time.time()-t0:.0f}s)")
        if ok:
            DEPLOY_URL = url
            print("  ok  部署完整")
            break
        if not ready:
            # 域名还没生效时扫出来的东西不算证据。把"还没生效"当"缺件"
            # 会触发无谓重部署，而重部署又造出新的未生效域名——自我伤害循环。
            print("  ! 域名未就绪，本次结果不采信；重新部署后重试")
            continue
        print(f"  FAIL  {len(bad)}/{total} 个资产在部署上缺失或被替换：")
        for rel, why in bad[:20]:
            print(f"        {rel:<52} {why}")
        if len(bad) > 20:
            print(f"        … 另有 {len(bad) - 20} 个")
        print("  → 重新部署（实测服务端落地存在随机缺件，重跑是有效缓解）")
    else:
        print("[FAIL] 3 次部署后资产仍不完整——不要把这个部署当作可用版本")
        sys.exit(1)
else:
    print("== 跳过部署（--verify-only），直接复验线上 ==")

# ---- 部署后复验 ----
print("\n== 部署后复验 ==")
BASE = "https://swarmlabs.tools/api"
ctx = ssl._create_unverified_context()


def get(path, tries=4):
    """Pages 生产别名推广有传播延迟，仅在**网络层失败**时退避重试。

    HTTPError（4xx/5xx）是端点正常应答，必须原样返回状态码——
    /v3/anchors/surrogate_branin 期望 404，若把 404 当网络错误重试并吞掉，
    会误报成 0 造成假失败。
    """
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(f"{BASE}/{path}",
                                         headers={"User-Agent": "swarmlabs-verify/1.0"})
            with urllib.request.urlopen(req, timeout=45, context=ctx) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")
        except Exception as e:
            last = (0, repr(e)[:120])
        time.sleep(15 * (i + 1))
    return last


def jget(path):
    """始终返回 (status, dict)，非 200 时 dict 为 {} 以免下游 .get 崩掉。"""
    st, body = get(path)
    if body is None or st != 200:
        return st, {}
    try:
        d = json.loads(body)
        return st, d if isinstance(d, dict) else {"_not_dict": str(d)[:120]}
    except Exception:
        return st, {"_unparsed": body[:200]}


# ---- 等待生产别名传播 ----
# 这里必须等：get() 对 HTTPError（含 404）**立即返回不重试**——这是对的，
# 因为 /v3/anchors/{key} 的 404 本身是合法应答。但代价是「部署尚未传播」与
# 「端点真的不存在」在复验里长得一模一样，会把刚加好的端点误报成 FAIL。
# 用 /v3/openapi 当部署标记（旧版没有这个路径，一旦 200 即证明新版本已上线），
# 把等待显式化，而不是靠猜。
MARKER = "v3/openapi"
_deadline = time.time() + 180
_last = None
for _i in range(18):
    _st, _ = jget(MARKER)
    _last = _st
    if _st == 200:
        print(f"  传播就绪（{MARKER} -> 200，第 {_i + 1} 次探测）")
        break
    time.sleep(10)
else:
    print(f"  ! 传播等待超时（{MARKER} 最后状态 {_last}）——复验可能针对旧版本，结论不可信")

checks = []

# 期望值一律从**本地产物**读，绝不写死在脚本里。
# 教训（2026-09-21）：这里曾硬编码 anchors==6 / total==62 / test_points==3110，
# 场景扩到 80 后部署复验稳定出现 3 个 FAIL —— 线上其实是对的，
# 是"尺子"没跟着长。硬编码的期望值会把「部署正确」误报成「部署失败」，
# 而更危险的反向情形是它也可能把失败误报成成功。
def _local_json(rel):
    try:
        with open(os.path.join(FRONT, rel), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

_exp_anchors = _local_json("reports/wet_lab_anchors.json")
_exp_anchor_n = len(_exp_anchors.get("anchors", [])) or None
_exp_anchor_scen = (_exp_anchors.get("summary") or {}).get("total")
_exp_bm = _local_json("reports/benchmark/index.json")
_exp_bm_total = _exp_bm.get("total")
_exp_bm_pts = _exp_bm.get("total_test_points")
_exp_ledger = _local_json("reports/gate_ledger.json")
_exp_gate_total = _exp_ledger.get("total") or len(_exp_ledger.get("scenarios", {})) or None
print(f"  本地口径期望：anchors={_exp_anchor_n} 可锚定场景={_exp_anchor_scen} "
      f"benchmark={_exp_bm_total}/{_exp_bm_pts}pts gate={_exp_gate_total}")

try:
    st, doc = jget("v3/anchors")
    checks.append(("GET /v3/anchors 200", st == 200, st))
    _got_a = len(doc.get("anchors", []))
    _got_s = (doc.get("summary") or {}).get("total")
    checks.append((f"  {_exp_anchor_n} 锚点 / {_exp_anchor_scen} 可锚定场景",
                   (_exp_anchor_n is None or _got_a == _exp_anchor_n)
                   and (_exp_anchor_scen is None or _got_s == _exp_anchor_scen),
                   f"anchors={_got_a}(期望 {_exp_anchor_n}) total={_got_s}(期望 {_exp_anchor_scen})"))
    checks.append(("  CONTRADICTED 清单",
                   set(doc.get("summary", {}).get("blocked", [])) == {"micro_thermal_death", "microbio_monod"},
                   str(doc.get("summary", {}).get("blocked"))))

    st, one = jget("v3/anchors/micro_ecoli_glucose")
    checks.append(("GET /v3/anchors/{key} 200", st == 200, st))
    checks.append(("  双链并存（wet_lab + vv_gate）",
                   bool(one.get("wet_lab")) and bool(one.get("vv_gate")),
                   f"wet_lab={one.get('wet_lab', {}).get('grade')} vv={one.get('vv_gate', {}).get('gate')}"))
    checks.append(("  AGREE/PROCEED",
                   one.get("wet_lab", {}).get("grade") == "AGREE"
                   and one.get("wet_lab", {}).get("gate") == "PROCEED",
                   str(one.get("wet_lab", {}).get("grade"))))
    checks.append(("  反推 Ks≈0.004",
                   abs((one.get("wet_lab", {}).get("implied_params", {}).get("Ks") or 0) - 0.004) < 1e-5,
                   str(one.get("wet_lab", {}).get("implied_params", {}).get("Ks"))))

    st, contra = jget("v3/anchors/microbio_monod")
    checks.append(("  CONTRADICTED 场景 gate=BLOCK",
                   contra.get("wet_lab", {}).get("grade") == "CONTRADICTED"
                   and contra.get("wet_lab", {}).get("gate") == "BLOCK_AUTONOMOUS_ACTION",
                   str(contra.get("wet_lab", {}).get("gate"))))
    # 双证据链的**核心展品**：同一场景上 V&V 链给 PROCEED、湿实验链给 BLOCK。
    # 这条断言一旦变绿就说明两条链真的并列返回了，而不是只返回了一半。
    checks.append(("  双链并列且公开分歧（V&V PROCEED vs wet-lab BLOCK）",
                   contra.get("vv_gate", {}).get("gate") == "PROCEED"
                   and contra.get("chains_agree") is False,
                   f"vv={contra.get('vv_gate', {}).get('gate')} agree={contra.get('chains_agree')}"))

    st, na = jget("v3/anchors/surrogate_branin")
    checks.append(("  无锚点场景 404", st == 404, st))

    st, api = jget("v3/openapi")
    paths = api.get("paths", {})
    checks.append(("  OpenAPI 收录新端点",
                   "/v3/anchors" in paths and "/v3/anchors/{key}" in paths,
                   str([k for k in paths if "anchor" in k])))
    checks.append(("  OpenAPI 收录 /v3/verify",
                   "/v3/verify" in paths, str([k for k in paths if "verify" in k])))

    st, one_gate = jget("v3/gate/microbio_monod")
    checks.append(("  /v3/gate/{key} 单键 200",
                   st == 200 and one_gate.get("verdict") == "PASS",
                   f"st={st} verdict={one_gate.get('verdict')}"))

    st, bm = jget("v3/benchmark")
    checks.append((f"  /v3/benchmark 未回归 (期望 {_exp_bm_total}/{_exp_bm_pts}pts)",
                   st == 200
                   and (_exp_bm_total is None or bm.get("total") == _exp_bm_total)
                   and (_exp_bm_pts is None or bm.get("total_test_points") == _exp_bm_pts),
                   f"total={bm.get('total')} pts={bm.get('total_test_points')}"))
    st, gl = jget("v3/gate")
    checks.append((f"  /v3/gate 未回归 (期望 total={_exp_gate_total})",
                   st == 200 and (_exp_gate_total is None or gl.get("total") == _exp_gate_total),
                   f"total={gl.get('total')}"))
except Exception as e:
    import traceback
    traceback.print_exc()
    checks.append(("复验过程异常", False, repr(e)[:200]))

ok = fail = 0
for name, cond, detail in checks:
    if cond:
        ok += 1
        print(f"  ok  {name}")
    else:
        fail += 1
        print(f"FAIL  {name}  {detail}")
print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
