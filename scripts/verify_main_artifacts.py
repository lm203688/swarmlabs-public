"""GitHub main 复验脚本（修正版）。

修复点：原复验脚本用 `urllib.request.Request(url, context=ctx)` —— 但 `context` 不是
Request 构造器的参数，会抛 `TypeError`。本版改用 **curl + Bearer token**（与
`scripts/gh_push.py` 同一可达通道，绕过 TLS-intercepting 代理），对私有仓库做_contents
接口复验，并把大文件（worker）的 base64 增量读出来校验关键标记。

用法：
    python scripts/verify_main_artifacts.py
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECRET = os.path.expanduser("~/.workbuddy/secrets/github.json")
REPO = "lm203688/swarmlabs"

# (path_on_main, 期望包含的标记串)；不传 marker 则只查存在性
CHECKS = [
    ("frontend_fixed/functions/api/[[path]].js", "PROJECT_API_KEYS"),
    ("data/entity_buckets/neuro_robotics.json", None),
    ("frontend_fixed/reports/scenario_library_density.json", None),
    ("frontend_fixed/reports/neuro_robotics_gap_scan_2026-09-16.json", None),
    ("engine/verification_gate.py", "cite_check_ledger"),
    ("engine/snapshot.py", "run_guarded_change"),
    ("scripts/patch_only.py", "FullRewriteDetected"),
    ("agentteams/core/compactor.py", "ObservationPack"),
    ("agentteams/config/role_contracts.yaml", "leader"),
    ("skills/agent-skills/MANIFEST.json", "schema_version"),
    ("docs/strategy/rsi_autonomy_levels.md", "L1"),
    ("data/gate_ledger_cite_audit.json", "pass_with_anchor"),
]


def _token() -> str:
    if not os.path.exists(SECRET):
        raise SystemExit(f"缺少 secret: {SECRET}")
    return json.load(open(SECRET, encoding="utf-8"))["GITHUB_TOKEN"]


def _api_get(path: str, token: str) -> dict:
    url = f"https://api.github.com/repos/{REPO}/contents/{path}"
    out = subprocess.run(
        ["curl", "-sS", "--ssl-no-revoke", "--tlsv1.3", "-w", "\n%{http_code}",
         "-H", f"Authorization: Bearer {token}",
         "-H", "Accept: application/vnd.github+json", url],
        capture_output=True, text=True,
    )
    body, _, code = out.stdout.rpartition("\n")
    return {"code": code.strip(), "body": body}


def main() -> int:
    token = _token()
    ok = 0
    fail = 0
    for path, marker in CHECKS:
        enc = path.replace("[[", "%5B%5B").replace("]]", "%5D%5D")
        res = _api_get(enc, token)
        if res["code"] != "200":
            print(f"[FAIL] {path}  http={res['code']}")
            fail += 1
            continue
        content = json.loads(res["body"]).get("content", "")
        text = base64.b64decode(content).decode("utf-8", "ignore")
        if marker and marker not in text:
            print(f"[FAIL] {path}  存在但缺标记 '{marker}'")
            fail += 1
            continue
        print(f"[ OK ] {path}  ({len(text)} bytes)" + (f"  marker='{marker}'" if marker else ""))
        ok += 1
    print(f"\nSUMMARY: {ok} OK, {fail} FAIL")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
