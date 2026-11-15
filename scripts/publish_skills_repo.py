"""Create the public swarmlabs-skills repository and publish all skill files.

Everything goes through the GitHub REST API (Contents API), because direct
git pushes to github.com are blocked on this network.

Usage:
    GITHUB_TOKEN=ghp_xxx python scripts/publish_skills_repo.py

Idempotent: safe to re-run. Files already present with identical content are
skipped; changed files are updated.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.github.com"
OWNER = "lm203688"
REPO = "swarmlabs-skills"

DESCRIPTION = (
    "Math-backed verification for scientific agents: virtual experiment design, "
    "uncertainty coverage audits, out-of-distribution guards, kinetic model "
    "selection, and literature claim validation."
)
HOMEPAGE = "https://swarmlabs.tools/verify"
TOPICS = [
    "agent-skills",
    "scientific-computing",
    "uncertainty-quantification",
    "gaussian-process",
    "bioprocess",
    "fermentation",
    "experimental-design",
    "verification",
]

SKILLS_ROOT = Path(__file__).resolve().parent.parent / "skills" / "agent-skills"

# Skill names published at repo-root by the previous (buggy) run — these must
# be deleted so the canonical skills/<name>/ layout is the only one present.
STALE_ROOT_DIRS = [
    "virtual-experiment-design",
    "uq-coverage-audit",
    "ood-guard",
    "kinetic-model-selection",
    "fermentation-design",
    "strain-media-selection",
    "paper-claim-validation",
    "mirror-run",
]

SKIP_DIRS = {"__pycache__", ".git", ".ipynb_checkpoints"}
SKIP_SUFFIX = {".pyc", ".pyo", ".bak", ".tmp"}


def ctx() -> ssl.SSLContext:
    # The local network intercepts TLS; verification must be relaxed here.
    c = ssl.create_default_context()
    c.check_hostname = False
    c.verify_mode = ssl.CERT_NONE
    return c


def http(method: str, url: str, token: str, data: dict | None = None, retries: int = 3):
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "swarmlabs-skills-publisher/1.0",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    payload = None
    if data is not None:
        headers["Content-Type"] = "application/json"
        payload = json.dumps(data).encode("utf-8")

    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=payload, headers=headers, method=method)
            with urllib.request.urlopen(req, context=ctx(), timeout=60) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "ignore")
            if e.code in (502, 503, 504) and attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            return e.code, {"error": body[:400]}
        except Exception as e:  # network flake
            last_err = e
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            return 0, {"error": str(last_err)}
    return 0, {"error": "exhausted"}


def ensure_repo(token: str) -> bool:
    code, _ = http("GET", f"{API}/repos/{OWNER}/{REPO}", token)
    if code == 200:
        print(f"repository {OWNER}/{REPO} already exists")
        return True

    code, res = http(
        "POST",
        f"{API}/user/repos",
        token,
        {
            "name": REPO,
            "description": DESCRIPTION,
            "homepage": HOMEPAGE,
            "private": False,
            "has_issues": True,
            "has_wiki": False,
            "has_projects": False,
            "license_template": "mit",
            "auto_init": False,
        },
    )
    if code in (200, 201):
        print(f"created {OWNER}/{REPO}")
        return True

    print(f"failed to create repository (HTTP {code}): {res.get('error')}")
    return False


def apply_topics(token: str) -> None:
    code, _ = http(
        "PUT", f"{API}/repos/{OWNER}/{REPO}/topics", token, {"names": TOPICS}
    )
    print(f"topics: {'applied' if code in (200, 204) else f'skipped (HTTP {code})'}")


def collect_files() -> list[tuple[str, bytes]]:
    out = []
    for path in sorted(SKILLS_ROOT.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(SKILLS_ROOT).as_posix()
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix in SKIP_SUFFIX or path.name.endswith("~"):
            continue
        out.append((rel, path.read_bytes()))
    return out


def remote_for(rel: str) -> str:
    """Canonical marketplace layout: skills/<name>/..., plus root README/LICENSE/tests."""
    if rel in ("README.md", "LICENSE") or rel.startswith("tests/"):
        return rel
    return "skills/" + rel


def delete_path(token: str, path: str) -> bool:
    """Delete a file or (recursively) a directory in the repo via the Contents API.

    GitHub's Contents API returns a dict for a file and a LIST for a directory.
    """
    code, listing = http("GET", f"{API}/repos/{OWNER}/{REPO}/contents/{path}", token)
    if code != 200:
        return False
    # file
    if isinstance(listing, dict) and listing.get("type") == "file":
        sha = listing.get("sha")
        if not sha:
            return False
        c, _ = http(
            "DELETE",
            f"{API}/repos/{OWNER}/{REPO}/contents/{path}",
            token,
            {"message": f"remove stale {path}", "sha": sha},
        )
        return c in (200, 204)
    # directory: listing is a list of entries (each has path/type/sha)
    entries = listing if isinstance(listing, list) else listing.get("entries", [])
    ok = True
    for entry in entries:
        if not delete_path(token, entry["path"]):
            ok = False
    return ok


def cleanup_stale(token: str) -> None:
    print("cleaning stale root-level skill dirs from previous layout")
    for d in STALE_ROOT_DIRS:
        code, _ = http("GET", f"{API}/repos/{OWNER}/{REPO}/contents/{d}", token)
        if code != 200:
            print(f"  -  {d} (absent, skip)")
            continue
        if delete_path(token, d):
            print(f"  x  {d}")
        else:
            print(f"  !  {d} (could not delete — remove manually)")


def publish(token: str) -> int:
    files = collect_files()
    if not files:
        print("error: no files found under", SKILLS_ROOT)
        return 1

    print(f"publishing {len(files)} files\n")
    created = updated = skipped = failed = 0

    for rel, content in files:
        remote = remote_for(rel)
        b64 = base64.b64encode(content).decode("ascii")
        # git blob SHA1 lets the API skip no-op updates cheaply and reliably.
        blob_sha = hashlib.sha1(
            b"blob " + str(len(content)).encode() + b"\0" + content
        ).hexdigest()

        code, existing = http(
            "GET",
            f"{API}/repos/{OWNER}/{REPO}/contents/{remote}",
            token,
        )

        if code == 200 and isinstance(existing, dict):
            if existing.get("sha") == blob_sha:
                print(f"  =  {remote}")
                skipped += 1
                continue
            payload = {
                "message": f"update {remote}",
                "content": b64,
                "sha": existing["sha"],
            }
        else:
            payload = {"message": f"add {remote}", "content": b64}

        code, res = http(
            "PUT", f"{API}/repos/{OWNER}/{REPO}/contents/{remote}", token, payload
        )
        if code in (200, 201):
            if "update" in payload["message"]:
                updated += 1
                print(f"  ~  {remote}")
            else:
                created += 1
                print(f"  +  {remote}")
        else:
            failed += 1
            print(f"  !  {remote} -> HTTP {code} {res.get('error', '')[:120]}")
            if code == 403:
                print("      (403 usually means the token lacks contents:write)")

    print(
        f"\ncreated={created} updated={updated} skipped={skipped} failed={failed}"
    )
    return 0 if failed == 0 else 1


def main() -> int:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        tokfile = Path("/tmp/.ghtok")
        if tokfile.exists():
            token = tokfile.read_text().strip()
    if not token:
        print("error: set GITHUB_TOKEN before running", file=sys.stderr)
        return 1

    if not ensure_repo(token):
        return 1
    apply_topics(token)
    cleanup_stale(token)
    return publish(token)


if __name__ == "__main__":
    sys.exit(main())
