"""Write .workbuddy-ai/last_push.json pin with the current remote commit SHA.

用途（地基承重审计报告 §九 · 硬骨头 4 · git_commit_binding "advisory only" 消除）：
    engine.verification_gate.engine_fingerprint() 的 git_commit 字段读的是**本地
    .git/HEAD**，而本仓库的推送走 GitHub Contents API —— API 推送**不会**回写
    本地 HEAD，也不推进本地 commit 历史（每次 Contents PUT 都是服务器端新建的
    一个 commit）。结果：本地 HEAD 与远端 main 最新 commit 长期不一致，
    ``git_commit_binding`` 只能标 "advisory only"，不能作权威绑定。

本脚本做的事：
    1. 通过 Contents API 查询远端 main 的最新 commit SHA + 时间 + 作者
    2. 把结果写入 ``.workbuddy-ai/last_push.json``
    3. engine_fingerprint() 检测到该文件存在后，将其 sha 提升为**权威绑定**
       （tier=remote），本地 HEAD 降级为参考（tier=local）

调用时机：
    - 每次 push_files_atomic.py 或 Contents API 推送完成后
    - CI 每日验证任务开始之前（保证 CI 报告的 git_commit 反映远端状态）

用法：
    python scripts/write_remote_commit_pin.py            # 写盘并打印结果
    python scripts/write_remote_commit_pin.py --check    # 只打印，不落盘
    python scripts/write_remote_commit_pin.py --json     # JSON 输出（给 CI 用）

不需要 git 二进制，只需要 Python stdlib + curl（用 urllib 实现即可）。
"""
from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from typing import Dict, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = "https://api.github.com"
OWNER, REPO, BRANCH = "lm203688", "swarmlabs", "main"
PIN_PATH = os.path.join(ROOT, ".workbuddy-ai", "last_push.json")
SECRETS_PATH = os.path.expanduser("~/.workbuddy/secrets/github.json")


def _load_token() -> str:
    if not os.path.exists(SECRETS_PATH):
        raise RuntimeError(f"missing secrets file: {SECRETS_PATH}")
    with open(SECRETS_PATH, encoding="utf-8") as f:
        sec = json.load(f)
    tok = sec.get("GITHUB_TOKEN") or sec.get("CLASSIC_TOKEN_2")
    if not tok:
        raise RuntimeError(f"no GITHUB_TOKEN in {SECRETS_PATH}")
    return tok


def _fetch_remote_head(tok: str) -> Dict:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    url = f"{API}/repos/{OWNER}/{REPO}/branches/{BRANCH}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {tok}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "swarmlabs-pinwriter/1.0",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
        d = json.loads(r.read().decode("utf-8", "replace"))
    return {
        "sha": d["commit"]["sha"],
        "message": d["commit"]["commit"]["message"].splitlines()[0][:140],
        "committer_date": d["commit"]["commit"]["committer"]["date"],
        "author": d["commit"]["commit"]["author"]["name"],
        "branch": BRANCH,
        "repo": f"{OWNER}/{REPO}",
    }


def _load_local_head() -> Tuple[str, str]:
    git = os.path.join(ROOT, ".git")
    try:
        with open(os.path.join(git, "HEAD"), encoding="utf-8") as f:
            ref = f.read().strip()
        if ref.startswith("ref:"):
            target = ref.split(":", 1)[1].strip()
            with open(os.path.join(git, target), encoding="utf-8") as f:
                return f.read().strip(), "ref:" + target
        return ref, "detached"
    except Exception:
        return "", "unavailable"


def _write_pin(remote: Dict, local_sha: str, local_source: str) -> Dict:
    local_matches_remote = bool(local_sha) and local_sha == remote["sha"]
    local_behind = bool(local_sha) and not local_matches_remote
    os.makedirs(os.path.dirname(PIN_PATH), exist_ok=True)
    record = {
        "schema_version": 1,
        "pinned_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "pinned_by": os.path.basename(__file__),
        "remote": remote,
        "local": {
            "sha": local_sha,
            "source": local_source,
            "matches_remote": local_matches_remote,
            "behind_remote": local_behind,
        },
        "binding_tier": (
            "remote" if not local_matches_remote else "remote==local"
        ),
        "note": (
            "Remote SHA captured via GitHub Contents API. "
            "Local .git/HEAD is not authoritative because Contents API "
            "pushes create server-side commits that never advance local HEAD."
        ),
    }
    with open(PIN_PATH, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    return record


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只查询远端，不落盘")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    args = ap.parse_args(argv)

    try:
        tok = _load_token()
        remote = _fetch_remote_head(tok)
        local_sha, local_source = _load_local_head()
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    record = _write_pin(remote, local_sha, local_source) if not args.check else {
        "would_pin": True,
        "remote": remote,
        "local": {"sha": local_sha, "source": local_source},
    }

    if args.json:
        print(json.dumps(record, ensure_ascii=False, indent=2))
    else:
        print(f"remote main:    {remote['sha']}")
        print(f"  author:       {remote['author']}")
        print(f"  date:         {remote['committer_date']}")
        print(f"  message:      {remote['message'][:80]}")
        print(f"local  HEAD:    {local_sha or '(unavailable)'} [{local_source}]")
        print(f"pin status:     {'remote==local' if record.get('binding_tier','')=='remote==local' else 'remote (local behind)'}")
        if args.check:
            print("  (dry run — not written to disk)")
        else:
            print(f"pin path:       {os.path.relpath(PIN_PATH, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
