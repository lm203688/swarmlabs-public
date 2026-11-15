#!/usr/bin/env python
"""
AOCI Whole-Index 批量建立驱动。
- 通过 MCP stdio 与 aoci.exe 通信（aoci_maintain / aoci_update_entry）
- 每条候选：读源码 → 调本地 AI 起草 F/R/A/S 单行 → 汇总回写
- 循环直到 remaining=0

用法:
  python scripts/aoci_index_driver.py [--resume] [--concurrency N] [--dry-run]

前提:
  环境变量 AOCI_AI_KEY 已设（本地 SenseNova 端点）
"""
import argparse, json, os, re, subprocess, sys, threading, time, traceback
from pathlib import Path

AOCI = r"C:\Users\xing\tools\aoci-code\aoci.exe"
REPO = r"C:\Users\xing\Desktop\swarmlabs"
AI_URL = "http://127.0.0.1:11434/api/chat"
AI_MODEL = "ornith-1.5:35b"

# 官方 AOCI Meta 词典（来自 aoci_maintain.authoring_meta）
LAYER_LINE = "A-应用编排 C-共享基础 D-领域逻辑 E-入口边界 F-声明配置 I-集成适配 K-算法计算 L-库与SDK M-中间件 O-运维交付 P-持久化 R-运行基础 S-文档规范 T-测试验证 X-开发工具 Z-其他"
MODULE_LINE = "A-分析智能 B-核心业务 C-配置策略 D-数据状态 E-扩展插件 G-跨域通用 H-硬件设备 I-身份权限 L-本地化 M-消息事件 N-网络协议 O-可观测性 P-性能资源 Q-质量保障 R-可靠性恢复 S-安全隐私 U-用户交互 V-构建发布 W-流程调度 Z-其他"
IMPORTANCE_LINE = "1-最低 2-很低 3-低 4-较低 5-中等 6-较高 7-高 8-很高 9-最高"
SCALE_LINE = "L-大>400行 M-中200-400 S-小100-200 T-微<100行"

# 官方合法字符（严格）
VALID_A = set("ACDEFIKLMOPRSTXZ")
VALID_B = set("ABCDEGHI LMNOPQRSTUVWZ".replace(" ", ""))
VALID_C = set("123456789")
VALID_E = set("LMST")


def scale_for_lines(n):
    if n > 400: return "L"
    if n >= 200: return "M"
    if n >= 100: return "S"
    return "T"


def fix_tag(tag, nlines):
    """校验/修正 4 位 tag，保证 A/B/C/E 都在合法集。E 强制按行数。"""
    tag = tag.strip()
    if len(tag) != 4:
        return f"KC5{scale_for_lines(nlines)}"
    a, b, c, e = tag[0], tag[1], tag[2], tag[3]
    if a not in VALID_A: a = "K"
    if b not in VALID_B: b = "G"
    if c not in VALID_C: c = "5"
    # E 一律按实际行数强制，避免模型估错
    e = scale_for_lines(nlines)
    return f"{a}{b}{c}{e}"


def normalize_entry(entry_text, path, nlines):
    """校验 new_entry 单行格式，修正 tag。返回 (text, ok)；ok=False 表示无法修复。

    目标格式：
        basename[TAG]: F:<...> | R:<...> | A:<...> | S:<...>
    """
    if not entry_text:
        return "", False
    line = entry_text.splitlines()[0].strip().strip('`').strip()
    if not line:
        return "", False

    base = os.path.basename(path)

    # 1) 定位 [ ... ] tag
    lb = line.find("[")
    rb = line.find("]")
    if lb < 0 or rb < 0 or rb < lb:
        # 尝试补齐：把整段当 head+空 tag
        return "", False

    # head 部分：basename（强制用真实 basename，忽略模型可能乱写的前缀）
    head = base

    tag = line[lb+1:rb].strip()

    # body 部分：tag 之后的 : F:... | R:... | A:... | S:...
    body = line[rb+1:].strip()
    if body.startswith(":"):
        body = body[1:].lstrip()

    # 2) 提取 F/R/A/S 四段
    parts = [p.strip() for p in body.split("|")]
    fields = {"F": "", "R": "", "A": "", "S": ""}
    for p in parts:
        # 兼容 "F:..." / "F :" / 无前缀（按顺序）
        if ":" in p:
            k, v = p.split(":", 1)
            k = k.strip().upper()
            v = v.strip()
            if k in fields:
                fields[k] = v
        elif not fields["F"] and p:
            fields["F"] = p
        elif fields["F"] and not fields["R"] and p:
            fields["R"] = p
        elif fields["R"] and not fields["A"] and p:
            fields["A"] = p
        elif fields["A"] and not fields["S"] and p:
            fields["S"] = p

    # 3) 拼装
    new_tag = fix_tag(tag, nlines)
    r_field = normalize_r_field(fields.get("R", ""), path)
    out = (
        f"{head}[{new_tag}]: "
        f"F:{fields['F'] or '-'} | "
        f"R:{r_field} | "
        f"A:{fields['A'] or '-'} | "
        f"S:{fields['S'] or '-'}"
    )
    return out, True


def normalize_r_field(r_text, self_path):
    """规范化 R 字段：每个引用必须带 code: 或 database:// 前缀；剥离 <...> 尖括号。"""
    if not r_text or r_text.strip() in ("", "-"):
        return f"code:{self_path}"
    # 按逗号分隔，逐个规范化
    parts = [p.strip() for p in r_text.split(",") if p.strip()]
    fixed = []
    for p in parts:
        # 剥离 <...> 或 [...] 包裹
        p = p.strip("<>【】[]")
        if not p:
            continue
        if p.startswith("code:") or p.startswith("database://"):
            fixed.append(p)
        else:
            # 补 code: 前缀（假定是仓库相对路径）
            fixed.append(f"code:{p}")
    if not fixed:
        return f"code:{self_path}"
    return ",".join(fixed)

PROMPT_TMPL = """/no_think
你是代码分析引擎，为 AOCI 认知层起草一条索引 Entry。
直接输出一行，不要解释、不要思考过程、不要 markdown、不要前缀、不要代码块。

严格格式（单行）：
{fname}[TAG]: F:<功能一句话> | R:<关系引用，用 code:相对路径 逗号分隔> | A:<对外接口> | S:<非显然约束/陷阱>

TAG 规则（严格 4 字符，格式 A+B+C+E）：
- A 层（第1位，必须从下列选一个）：{layer_line}
- B 模块（第2位，必须从下列选一个）：{module_line}
- C 重要性（第3位，1-9 数字，1最低 9最高）：{importance_line}
- E 规模（第4位，从下列选一个）：{scale_line}
  E 由文件行数决定：L>400行 / M 200-400 / S 100-200 / T<100

**严禁**使用 F/U/N/V/K/I/L/G/H/P/O/Q/W/X/Y/Z 等未列出的字母作为 B 位（B 只允许：{module_letters}）。
**严禁**使用 A 位除 {layer_letters} 之外的字母。

源码文件：{path}
行数：{nlines}（**必须**据此推断 E 位：>400→L, 200-400→M, 100-200→S, <100→T）

源码（可能截断）：
{body}

只输出一行，示例：
engine/surrogate.py[KD9M]: F:多保真 GP 代理内核唯一真源 | R:code:engine/multifidelity.py,code:engine/generalization_bench.py | A:GPSurrogate,fallback_to_mean | S:mean_fn 必须记忆化否则 conformal 标定爆炸"""

# ---------------------------------------------------------------------------
# MCP stdio client
# ---------------------------------------------------------------------------
class MCP:
    def __init__(self, aoci=AOCI, repo=REPO):
        self.proc = subprocess.Popen(
            [aoci, "--repo", repo, "mcp"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1,
            cwd=repo,
        )
        self.reqs = {}
        self.event = threading.Event()
        self.id_seq = 0
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except Exception:
                continue
            if "id" in msg:
                self.reqs[msg["id"]] = msg
                self.event.set()

    def _next_id(self):
        self.id_seq += 1
        return self.id_seq

    def call(self, method, params=None, rid=None, timeout=180):
        self.event.clear()
        rid = rid or self._next_id()
        req = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            req["params"] = params
        self.proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()
        if not self.event.wait(timeout):
            raise TimeoutError(f"timeout calling {method}")
        resp = self.reqs.pop(rid, None)
        if resp is None:
            raise RuntimeError(f"no response for {method} id={rid}")
        if "error" in resp:
            raise RuntimeError(f"{method} error: {resp['error']}")
        return resp.get("result", {})

    def notify(self, method, params=None):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self.proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def initialize(self):
        self.call("initialize", {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "aoci-driver", "version": "1.0"},
        }, rid=1, timeout=30)
        self.notify("notifications/initialized")

    def tool_call(self, name, arguments=None, timeout=300):
        result = self.call("tools/call", {
            "name": name,
            "arguments": arguments or {},
        }, timeout=timeout)
        # result 可能是 {"content":[{"type":"text","text":"...json..."}]}
        content = result.get("content")
        if content and isinstance(content, list):
            text_parts = [c.get("text", "") for c in content if c.get("type") == "text"]
            joined = "\n".join(text_parts)
            try:
                return json.loads(joined)
            except Exception:
                return {"_raw": joined}
        return result

    def close(self):
        try:
            self.proc.stdin.close()
        except Exception:
            pass  # noqa: silent-ok — Exception 可安全忽略（已审计 2026-10-08）
        try:
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()


# ---------------------------------------------------------------------------
# AI draft
# ---------------------------------------------------------------------------
def draft_entry(path, body, api_key, max_retries=6):
    nlines = body.count("\n") + 1
    # 截断到 24KB，避免超出上下文
    truncated = body[:24000]
    prompt = PROMPT_TMPL.format(
        fname=os.path.basename(path),
        layer_line=LAYER_LINE,
        module_line=MODULE_LINE,
        importance_line=IMPORTANCE_LINE,
        scale_line=SCALE_LINE,
        layer_letters="".join(sorted(VALID_A)),
        module_letters="".join(sorted(VALID_B)),
        path=path,
        nlines=nlines,
        body=truncated,
    )
    payload = {
        "model": AI_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "think": False,
        "options": {
            "temperature": 0.2,
            "num_predict": 300,
        },
    }
    import urllib.request, urllib.error, time as _t
    last_err = None
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(
                AI_URL,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=180) as resp:
                raw = resp.read().decode("utf-8")
            data = json.loads(raw)
            msg = data.get("message") or {}
            text = (msg.get("content") or "").strip()
            if not text:
                # 兜底：从 reasoning 里找答案
                rz = (msg.get("reasoning_content") or msg.get("reasoning") or "")
                for line in rz.splitlines():
                    line = line.strip()
                    if "[" in line and "]" in line and "F:" in line and "R:" in line:
                        text = line
                        break
            if not text:
                last_err = f"empty_content: {str(data)[:200]}"
                _t.sleep(5 + attempt * 5)
                continue
            # 取第一行有效内容
            for line in text.splitlines():
                line = line.strip().strip('`').strip()
                if line and ("[" in line and "]" in line) and ":" in line:
                    return line
            # 兜底：任何带冒号且不是纯中文注释的行
            for line in text.splitlines():
                line = line.strip().strip('`').strip()
                if line and ":" in line and len(line) > 15:
                    return line
            return text.splitlines()[0].strip() if text else ""
        except urllib.error.HTTPError as e:
            body = ""
            try: body = e.read().decode("utf-8")[:300]
            except Exception: body = ""  # 读取错误体失败则留空，不影响主流程
            last_err = f"HTTP {e.code}: {body}"
            # 429 退避更长
            if e.code == 429:
                _t.sleep(15 + attempt * 10)
            else:
                _t.sleep(3 + attempt * 3)
        except Exception as e:
            last_err = str(e)[:200]
            _t.sleep(3 + attempt * 3)
    raise RuntimeError(f"all_retries_failed: {last_err}")


def read_source(path, repo):
    fp = Path(repo) / path
    if not fp.exists():
        return None
    try:
        return fp.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--concurrency", type=int, default=6, help="AI 并发")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-rounds", type=int, default=0, help="0=不限制")
    args = ap.parse_args()

    api_key = os.environ.get("AOCI_AI_KEY")
    if not api_key:
        print("[fatal] AOCI_AI_KEY not set", file=sys.stderr)
        sys.exit(2)

    log_path = Path("C:/Users/xing/Desktop/swarmlabs/logs/aoci_index_build.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_fh = open(log_path, "a", encoding="utf-8", buffering=1)

    def log(msg):
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        print(line)
        log_fh.write(line + "\n")

    mcp = MCP()
    mcp.initialize()
    log("MCP initialized.")

    total_written = 0
    total_fail = 0
    round_i = 0

    from concurrent.futures import ThreadPoolExecutor, as_completed

    while True:
        round_i += 1
        try:
            batch = mcp.tool_call("aoci_maintain", {}, timeout=300)
        except Exception as e:
            log(f"[maintain] error: {e}")
            break

        # 抽取 candidates
        candidates = batch.get("candidates") or []
        # 关键：remaining 在 code_plan 里，不在顶层
        cp = batch.get("code_plan") or {}
        remaining = cp.get("remaining", batch.get("remaining", 0))
        batch_id = cp.get("batch_id") or None
        # 尝试提取 candidate.batch_id 作为兜底
        if not batch_id:
            for c in candidates:
                if c.get("batch_id"):
                    batch_id = c["batch_id"]
                    break
        status = batch.get("status") or batch.get("result")
        if status and status != "authoring_required":
            log(f"round {round_i}: status={status} (not authoring_required), candidates={len(candidates)}")
            if not candidates:
                log("No candidates & status is terminal, stopping.")
                break

        log(f"round {round_i}: candidates={len(candidates)} remaining={remaining} batch_id={batch_id[:12] if batch_id else None}")

        if not candidates:
            log("Empty candidates, stopping.")
            break

        # 并发起草
        entries = []
        fails = []
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            fut_map = {}
            for cand in candidates:
                path = cand["path"]
                body = read_source(path, REPO)
                if body is None:
                    fails.append((path, "read_failed"))
                    continue
                fut_map[pool.submit(draft_entry, path, body, api_key)] = cand

            for fut in as_completed(fut_map):
                cand = fut_map[fut]
                path = cand["path"]
                base = os.path.basename(path)
                body = read_source(path, REPO) or ""
                nlines = body.count("\n") + 1
                e_scale = scale_for_lines(nlines)
                stub = f"{base}[KC3{e_scale}]: F:stub | R:code:{path} | A:- | S:-"
                try:
                    raw_entry = fut.result()
                    if not raw_entry:
                        # 用 stub 兜底，保证批次完整
                        entries.append({
                            "path": path, "new_entry": stub,
                            "source_sha256": cand["source_sha256"],
                            "candidate_id": cand["candidate_id"],
                        })
                        fails.append((path, "empty_response->stub"))
                        continue
                    fixed, ok = normalize_entry(raw_entry, path, nlines)
                    if not ok or not fixed:
                        # normalize 失败也用 stub，保证批次完整
                        entries.append({
                            "path": path, "new_entry": stub,
                            "source_sha256": cand["source_sha256"],
                            "candidate_id": cand["candidate_id"],
                        })
                        fails.append((path, "normalize_failed->stub"))
                        continue
                    entry = {
                        "path": path,
                        "new_entry": fixed,
                        "source_sha256": cand["source_sha256"],
                        "candidate_id": cand["candidate_id"],
                    }
                    entries.append(entry)
                except Exception as e:
                    # AI 调用失败也要 stub，保证批次完整
                    entries.append({
                        "path": path, "new_entry": stub,
                        "source_sha256": cand["source_sha256"],
                        "candidate_id": cand["candidate_id"],
                    })
                    fails.append((path, str(e)[:100] + "->stub"))

        log(f"  drafted {len(entries)} / {len(candidates)}; failed {len(fails)}")
        if fails:
            for p, e in fails[:5]:
                log(f"    FAIL {p}: {e}")
            if len(fails) > 5:
                log(f"    ... and {len(fails)-5} more")

        if not entries:
            log("No entries drafted this round, stopping to avoid infinite loop.")
            break

        if args.dry_run:
            log("  [dry-run] skipping update_entry")
        else:
            try:
                upd_args = {"entries": entries}
                if batch_id:
                    upd_args["code_batch_id"] = batch_id
                upd = mcp.tool_call("aoci_update_entry", upd_args, timeout=300)
                total_written += len(entries)
                total_fail += len(fails)
                st = upd.get("status") or upd.get("result")
                log(f"  update status={st}")
            except Exception as e:
                log(f"  update_entry error: {e}")

        # 检查是否收敛
        if remaining == 0:
            log(f"remaining=0, stopping.")
            break
        if args.max_rounds and round_i >= args.max_rounds:
            log(f"reached max_rounds={args.max_rounds}, stopping.")
            break

    mcp.close()
    log(f"=== DONE: rounds={round_i} written={total_written} failed={total_fail} ===")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("interrupted")
    except Exception:
        traceback.print_exc()
        sys.exit(1)
