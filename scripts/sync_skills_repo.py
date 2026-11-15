#!/usr/bin/env python3
"""Mirror the local skill package into the public distribution repo.

The skills live in this monorepo at `skills/agent-skills/<name>/`, but the
public install target (`lm203688/swarmlabs-skills`, used by
`npx skills add lm203688/swarmlabs-skills`) expects `<name>/` at `skills/`.
This script performs that path translation and pushes via the Contents API,
which is the only route that works from here (direct git push to github.com
is blocked).

Usage:
    python scripts/sync_skills_repo.py
    python scripts/sync_skills_repo.py --dry-run
    python scripts/sync_skills_repo.py --repo lm203688/swarmlabs-skills

Exit codes:
    0  every file pushed (or dry-run completed)
    1  at least one push failed
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.github.com"
SRC = os.path.join("skills", "agent-skills")
BRANCH = "main"

# Files that are not pushed to the public package.
SKIP_SUFFIXES = (".pyc",)
SKIP_DIRS = {"__pycache__", ".pytest_cache", ".git"}


def load_token() -> str:
    tok = os.environ.get("GITHUB_TOKEN")
    if tok:
        return tok
    path = os.path.expanduser("~/.workbuddy/secrets/github.json")
    if not os.path.exists(path):
        raise SystemExit(f"error: no GITHUB_TOKEN and no {path}")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)["GITHUB_TOKEN"]


def dest_path(rel: str, keep_prefix: bool = False) -> str | None:
    """Translate a local path under skills/agent-skills into its public path.

    keep_prefix=True pushes to the *same* path as the monorepo (used when the
    target is the monorepo itself, e.g. to make files available to CI).
    """
    if keep_prefix:
        return rel.replace("\\", "/")
    parts = rel.replace("\\", "/").split("/")
    if parts[0] != "skills" or len(parts) < 2 or parts[1] != "agent-skills":
        return None
    rest = parts[2:]
    if not rest:
        return None
    # <skill>/... -> skills/<skill>/... ; everything else stays at the root.
    if rest[0] in ("README.md", "LICENSE", "plugin.json"):
        return rest[0]
    if rest[0] == "tests":
        return "/".join(rest)
    return "/".join(["skills", *rest])


def sha_of(owner: str, repo: str, path: str, token: str) -> str | None:
    """Existing blob sha, needed to update rather than create."""
    url = f"{API}/repos/{owner}/{repo}/contents/{urllib.parse.quote(path)}?ref={BRANCH}"
    req = urllib.request.Request(
        url, headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "swarmlabs-sync",
        })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r).get("sha")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def push(owner: str, repo: str, path: str, content: bytes, message: str,
         token: str) -> tuple[int, str]:
    body = {
        "message": message,
        "content": base64.b64encode(content).decode("ascii"),
        "branch": BRANCH,
    }
    sha = sha_of(owner, repo, path, token)
    if sha:
        body["sha"] = sha
    url = f"{API}/repos/{owner}/{repo}/contents/{urllib.parse.quote(path)}"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="PUT", headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "swarmlabs-sync",
            "Content-Type": "application/json",
        })
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, "updated" if sha else "created"
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.load(e).get("message", "")
        except Exception:
            pass  # noqa: silent-ok — Exception 可安全忽略（已审计 2026-10-08）
        return e.code, detail or f"HTTP {e.code}"


def main() -> int:
    global BRANCH
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default="lm203688/swarmlabs-skills")
    ap.add_argument("--branch", default=BRANCH)
    ap.add_argument("-m", "--message", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--keep-prefix", action="store_true",
                    help="Push to the same path as the monorepo instead of "
                         "translating skills/<name>/ (for the monorepo itself)")
    args = ap.parse_args()

    BRANCH = args.branch

    if "/" not in args.repo:
        print("error: --repo must be owner/name", file=sys.stderr)
        return 1
    owner, repo = args.repo.split("/", 1)
    if not os.path.isdir(SRC):
        print(f"error: {SRC} not found (run from the repo root)", file=sys.stderr)
        return 1

    token = load_token()
    message = args.message or "sync: mirror skill package from monorepo"

    plan: list[tuple[str, str]] = []
    for root, dirs, files in os.walk(SRC):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fn in files:
            if fn.endswith(SKIP_SUFFIXES):
                continue
            local = os.path.join(root, fn)
            rel = os.path.relpath(local, ".").replace("\\", "/")
            dest = dest_path(rel, keep_prefix=args.keep_prefix)
            if dest:
                plan.append((local, dest))

    if not plan:
        print("error: nothing to sync", file=sys.stderr)
        return 1

    print(f"Mirroring {len(plan)} files -> {owner}/{repo}@{BRANCH}")
    if args.dry_run:
        for local, dest in sorted(plan, key=lambda t: t[1]):
            print(f"  {local}  ->  {dest}")
        return 0

    failures = 0
    for local, dest in sorted(plan, key=lambda t: t[1]):
        with open(local, "rb") as fh:
            content = fh.read()
        code, note = push(owner, repo, dest, content, message, token)
        ok = code in (200, 201)
        if not ok:
            failures += 1
        print(f"  [{'ok ' if ok else 'ERR'}] {code} {note:<8} {dest}")
    print("-" * 60)
    print(f"  {len(plan) - failures}/{len(plan)} pushed")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
