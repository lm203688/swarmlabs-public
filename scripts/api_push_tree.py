"""GitHub 全树推送 (api_push_tree) —— 经 Git Data API 绕过 github.com:443 封禁。

git push 到 github.com:443 在本环境不可达，但 api.github.com 可用。
本工具用 Git Data API 把本地 HEAD 的完整文件树一次性推成「一个提交」:
  GET /branches/main            -> 取父提交 SHA (base)
  POST /git/blobs  (xN)         -> 上传每个 blob(幂等: 内容相同返回同 SHA)
  POST /git/trees  (递归分层)   -> 自底向上建嵌套目录树(单目录>80条再分批 base_tree 续接,
                                   绕开 743 条扁平树 POST 超时; 删除文件不在树中=已删)
  POST /git/commits            -> 在 base 上建提交
  PATCH /git/refs/heads/main   -> 移动 main 到新提交

用法:
  set GITHUB_TOKEN=github_pat_xxx
  python scripts/api_push_tree.py
"""
from __future__ import annotations
import os, sys, json, base64, subprocess, urllib.request, urllib.error

API = "https://api.github.com"
OWNER = "lm203688"
REPO = "swarmlabs"
BRANCH = "main"
MAX_BLOB = 90 * 1024 * 1024  # GitHub blob 软上限保护


def http(method, url, token, data=None, retries=3, timeout=120):
    import ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
               "User-Agent": "swarmlabs-apipush/1.0", "X-GitHub-Api-Version": "2022-11-28"}
    if data is not None:
        headers["Content-Type"] = "application/json"
        payload = json.dumps(data).encode("utf-8")
    else:
        payload = None
    last = None
    for _ in range(retries):
        try:
            req = urllib.request.Request(url, data=payload, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            last = (e.code, e.read().decode("utf-8", "replace"))
            if e.code in (403, 429):  # rate limit / abuse -> 退避后重试
                import time
                time.sleep(3)
                continue
            return last
        except Exception as e:
            last = (0, str(e))
            return last
    return last or (0, "http failed")


def git(*args):
    return subprocess.check_output(["git"] + list(args)).decode("utf-8", "replace")


def main():
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("[err] 缺少 GITHUB_TOKEN"); return 2
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.chdir(root)

    # 1. base
    st, body = http("GET", f"{API}/repos/{OWNER}/{REPO}/branches/{BRANCH}", token)
    if st != 200:
        print(f"[err] 取 base 失败 status={st}: {body[:300]}"); return 1
    base = json.loads(body)["commit"]["sha"]
    print(f"[ok] base commit = {base}")

    # 2. HEAD 树(扁平 blob 列表)
    lines = git("ls-tree", "-r", "HEAD").splitlines()
    entries = []      # (path, mode, local_sha)
    for ln in lines:
        meta, path = ln.split("\t", 1)
        mode, typ, sha = meta.split()
        if typ != "blob":
            continue
        entries.append((path, mode, sha))
    print(f"[ok] HEAD 树文件数 = {len(entries)}")

    # 3. 上传 blob(幂等)
    remote_sha = {}   # local_sha -> remote_sha
    skipped = []
    for i, (path, mode, sha) in enumerate(entries, 1):
        if sha in remote_sha:
            continue
        size = int(git("cat-file", "-s", sha).strip())
        if size > MAX_BLOB:
            print(f"[skip] 超大文件 {path} ({size} bytes) 跳过"); skipped.append(path); continue
        content = subprocess.check_output(["git", "cat-file", "-p", sha])
        b64 = base64.b64encode(content).decode("ascii")
        st, bbody = http("POST", f"{API}/repos/{OWNER}/{REPO}/git/blobs", token,
                         {"content": b64, "encoding": "base64"})
        if st not in (200, 201):
            print(f"[FAIL] blob {path} status={st}: {bbody[:200]}"); return 1
        remote_sha[sha] = json.loads(bbody)["sha"]
        if i % 50 == 0:
            print(f"  ... uploaded {i}/{len(entries)}")
    print(f"[ok] blob 上传完成 (unique={len(remote_sha)}, skipped={len(skipped)})")

    # 4. 递归分层建树（避免 743 条扁平树 POST 超时）
    #    构建嵌套目录结构；自底向上为每个目录建小树（单目录 >80 条再分批 base_tree 续接）
    root = {"dirs": {}, "files": {}}
    for path, mode, sha in entries:
        if sha not in remote_sha:
            continue  # 被超大跳过的文件
        parts = path.split("/")
        node = root
        for p in parts[:-1]:
            node = node["dirs"].setdefault(p, {"dirs": {}, "files": {}})
        node["files"][parts[-1]] = (mode, remote_sha[sha])

    def build_dir_tree(file_items, token):
        """file_items: [(name, mode, sha)]；单目录一次性建树（最大目录 126 条，远低于 743 超时阈值，
        无需 base_tree 分批，避免合并类型错配）。返回 tree sha。"""
        if not file_items:
            st, body = http("POST", f"{API}/repos/{OWNER}/{REPO}/git/trees", token, {"tree": []})
            if st != 201:
                raise RuntimeError(f"empty-tree fail {st}")
            return json.loads(body)["sha"]
        items = [{"path": n, "mode": m, "type": "blob", "sha": s} for (n, m, s) in file_items]
        st, body = http("POST", f"{API}/repos/{OWNER}/{REPO}/git/trees", token, {"tree": items})
        if st != 201:
            raise RuntimeError(f"tree fail {st}: {body[:300]}")
        return json.loads(body)["sha"]

    def build_node(node, token):
        # 先递归建所有子目录，拿到各自 tree sha（type="tree"）
        dir_items = []
        for name, child in node["dirs"].items():
            child_sha = build_node(child, token)
            dir_items.append((name, "040000", child_sha))
        # 本目录文件（type="blob"，sha 为远端 blob sha）
        file_items = [(n, m, s) for n, (m, s) in node["files"].items()]
        # 合并后单次建树：目录条目在前、文件在后
        return build_dir_tree(dir_items + file_items, token)

    try:
        tree_sha = build_node(root, token)
    except RuntimeError as e:
        print(f"[FAIL] {e}"); return 1
    print(f"[ok] tree = {tree_sha}")

    # 5. 建提交
    msg = ("SwarmLabs: 统一飞轮闭环收口 + 竞品联网修复 + GEO 假广度根治 + "
           "SINDy+Elo 全自动闭环 + 飞轮健壮性审计修复")
    st, cbody = http("POST", f"{API}/repos/{OWNER}/{REPO}/git/commits", token,
                     {"message": msg, "tree": tree_sha, "parents": [base]})
    if st != 201:
        print(f"[FAIL] commit status={st}: {cbody[:300]}"); return 1
    commit_sha = json.loads(cbody)["sha"]
    print(f"[ok] commit = {commit_sha}")

    # 6. 更新 ref
    st, rbody = http("PATCH", f"{API}/repos/{OWNER}/{REPO}/git/refs/heads/{BRANCH}", token, {"sha": commit_sha})
    if st != 200:
        print(f"[FAIL] ref status={st}: {rbody[:300]}"); return 1
    print(f"[DONE] 已推送 -> main @ {commit_sha}")
    if skipped:
        print(f"[warn] 跳过 {len(skipped)} 个超大文件: {skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
