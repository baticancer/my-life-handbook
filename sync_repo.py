#!/usr/bin/env python3
"""把本地 my-life-handbook 目录幂等同步到 GitHub 私有仓库。
令牌仅从环境变量 GH_TOKEN 读取；已存在的文件先取 sha 再更新，不存在则新建。
用法：
    GH_TOKEN='ghp_xxx' python3 sync_repo.py
"""
import os, sys, time, base64, json
import urllib.request, urllib.error, urllib.parse

TOKEN = os.environ.get("GH_TOKEN")
if not TOKEN:
    sys.exit("缺少环境变量 GH_TOKEN")

BASE = os.path.dirname(os.path.abspath(__file__))
REPO = "my-life-handbook"
HDRS = {
    "Authorization": f"token {TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "workbuddy-sync",
}
SKIP_DIRS = {".git"}


def request(method, url, data=None):
    req = urllib.request.Request(url, headers=HDRS, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
        req.data = json.dumps(data).encode("utf-8")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, (json.loads(r.read().decode("utf-8")) if r.read else {})
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return 404, {}
        if e.code in (409, 422):
            time.sleep(1.5)
            return request(method, url, data)
        sys.stderr.write(f"{method} {url} -> {e.code}: {e.read().decode('utf-8')}\n")
        raise


def main():
    st, me = request("GET", "https://api.github.com/user")
    owner = me.get("login")
    print(f"已认证账号: {owner}")

    st, repo = request("GET", f"https://api.github.com/repos/{owner}/{REPO}")
    if st != 200:
        sys.exit(f"仓库 {owner}/{REPO} 不存在，请先用 push_repo.py 创建")
    branch = repo.get("default_branch", "main")

    # 收集本地文件
    local_files = []
    for root, dirs, files in os.walk(BASE):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            local_files.append(os.path.relpath(os.path.join(root, f), BASE))

    for rel in sorted(local_files):
        local = os.path.join(BASE, rel)
        with open(local, "r", encoding="utf-8") as fh:
            content = fh.read()
        body = base64.b64encode(content.encode("utf-8")).decode("ascii")
        # 是否已存在 → 取 sha（路径含中文需 URL 编码）
        enc = urllib.parse.quote(rel, safe="/")
        st_get, existing = request(
            "GET", f"https://api.github.com/repos/{owner}/{REPO}/contents/{enc}?ref={branch}")
        payload = {"message": f"sync: {rel}", "content": body, "branch": branch}
        if st_get == 200 and existing.get("sha"):
            # 内容相同则跳过，避免产生空提交
            remote = existing.get("content", "")
            try:
                remote_dec = base64.b64decode(remote).decode("utf-8")
            except Exception:
                remote_dec = None
            if remote_dec == content:
                print(f"[跳过] 内容未变 {rel}")
                continue
            payload["sha"] = existing["sha"]
        st_put, _ = request(
            "PUT", f"https://api.github.com/repos/{owner}/{REPO}/contents/{enc}", payload)
        verb = "更新" if st_get == 200 else "新增"
        print(f"[{'OK' if st_put in (200, 201) else 'FAIL'}] {verb} {rel}")

    print(f"\n同步完成 ✅  https://github.com/{owner}/{REPO}")


if __name__ == "__main__":
    main()
