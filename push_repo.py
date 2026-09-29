#!/usr/bin/env python3
"""用 GitHub PAT 创建私有仓库 my-life-handbook 并推送本地骨架。
令牌仅从环境变量 GH_TOKEN 读取，不写盘、不打印。
"""
import os, sys, time, base64, json
import urllib.request

TOKEN = os.environ.get("GH_TOKEN")
if not TOKEN:
    sys.exit("缺少环境变量 GH_TOKEN")

BASE = os.path.dirname(os.path.abspath(__file__))
REPO = "my-life-handbook"
DESC = "个人工作与生活的爱好和习惯手册 / My personal handbook for work, life, hobbies and habits"

HDRS = {
    "Authorization": f"token {TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "workbuddy-push",
}

FILES = [
    ("README.md", "README.md", True),          # (local, repo_path, needs_sha_update)
    ("work/README.md", "work/README.md", False),
    ("life/README.md", "life/README.md", False),
    ("hobbies/README.md", "hobbies/README.md", False),
    ("habits/README.md", "habits/README.md", False),
    ("habits/habit-tracker.md", "habits/habit-tracker.md", False),
    ("templates/weekly-review.md", "templates/weekly-review.md", False),
    ("templates/monthly-review.md", "templates/monthly-review.md", False),
    (".gitignore", ".gitignore", False),
]


def api(method, url, data=None, retries=3):
    for i in range(retries):
        req = urllib.request.Request(url, headers=HDRS, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
            req.data = json.dumps(data).encode("utf-8")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read().decode("utf-8")
                return r.status, (json.loads(body) if body else {})
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8")
            if e.code in (409, 422) and i < retries - 1:
                time.sleep(1.5)
                continue
            sys.stderr.write(f"{method} {url} -> {e.code}: {err}\n")
            raise


def main():
    # 1. 当前用户
    st, me = api("GET", "https://api.github.com/user")
    owner = me.get("login")
    scopes = me  # placeholder
    print(f"已认证账号: {owner}")

    # 2. 仓库是否已存在
    exists = False
    try:
        st, _ = api("GET", f"https://api.github.com/repos/{owner}/{REPO}")
        exists = (st == 200)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            exists = False
        else:
            raise
    if exists:
        print(f"仓库已存在，跳过创建：https://github.com/{owner}/{REPO}")
    else:
        st, repo = api("POST", "https://api.github.com/user/repos", {
            "name": REPO,
            "private": True,
            "description": DESC,
            "auto_init": True,
        })
        print(f"已创建私有仓库：{repo.get('html_url')}")

    # 3. 等待默认分支就绪
    default_branch = None
    for _ in range(10):
        st, repo = api("GET", f"https://api.github.com/repos/{owner}/{REPO}")
        default_branch = repo.get("default_branch")
        if default_branch:
            break
        time.sleep(1.0)
    if not default_branch:
        default_branch = "main"
    print(f"默认分支：{default_branch}")

    # 4. 读取 auto_init 的 README sha（用于覆盖）
    readme_sha = None
    try:
        st, rf = api("GET", f"https://api.github.com/repos/{owner}/{REPO}/contents/README.md?ref={default_branch}")
        readme_sha = rf.get("sha")
    except Exception:
        pass

    # 5. 逐个推送
    for local, repo_path, is_readme in FILES:
        p = os.path.join(BASE, local)
        if not os.path.exists(p):
            print(f"跳过（本地缺失）: {local}")
            continue
        with open(p, "r", encoding="utf-8") as f:
            content = f.read()
        payload = {
            "message": f"add {repo_path}",
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "branch": default_branch,
        }
        if is_readme and readme_sha:
            payload["sha"] = readme_sha
        st, _ = api("PUT", f"https://api.github.com/repos/{owner}/{REPO}/contents/{repo_path}", payload)
        print(f"[{'OK' if st in (200, 201) else 'FAIL'}] {repo_path}")

    print(f"\n完成 ✅  https://github.com/{owner}/{REPO}")


if __name__ == "__main__":
    main()
