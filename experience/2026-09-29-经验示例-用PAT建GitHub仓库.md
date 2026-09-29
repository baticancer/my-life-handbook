# 用 PAT + REST API 在 WorkBuddy 里建 GitHub 私有仓库

> 日期：2026-09-29 ｜ 领域：自动化 / GitHub

## 1. 背景
想在 WorkBuddy 里建一个保存个人工作与生活爱好的私有 GitHub 仓库 `my-life-handbook`。

## 2. 问题 / 卡点
WorkBuddy 自带的 GitHub 连接器（MCP）是「应用/集成」类型令牌，调用 `mcp__github__create_repository` 直接报错：

```
403 Resource not accessible by integration
```

这是 GitHub 对 App 安装令牌的硬性限制——只能访问已安装该 App 的仓库，**不能新建仓库**。
连接器的 `get_me` 等接口也返回 `unauthorized`（底层无有效令牌）。沙箱内也没有 `git` / `gh`。

## 3. 做法
让用户提供一个带 `repo` 权限的 Personal Access Token，用 Python `urllib` 直接调 GitHub REST API：

```python
HDRS = {"Authorization": f"token {TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"}
# 1) POST /user/repos 建仓库（auto_init=true 自动生成 main 分支与初始 README）
# 2) GET  /repos/{owner}/{repo}/contents/README.md?ref=main 取 sha
# 3) 对每个文件 PUT /repos/{owner}/{repo}/contents/{path}，README 带 sha 覆盖，其余不带 sha 新建
```

可复用的脚本：`sync_repo.py`（仓库根目录，已含此逻辑，且能处理「新增/更新」）。

## 4. 踩坑
- ❌ 建仓库时不带 `auto_init`：仓库没有默认分支，后续 PUT 文件报「branch not found」。→ 用 `auto_init:true`。
- ❌ 直接 PUT 覆盖 `README.md` 不带 `sha`：报 409 冲突。→ 先 GET 取 `sha` 再带 `sha` 更新。
- ❌ 用 `GET` 判断仓库是否存在时遇到 404：脚本把 404 当异常抛出中断。→ 显式捕获 404 当作「不存在」。
- ❌ 在 `api()` 里对 404 也会 `sys.stderr.write` 一行日志：无害，只是先打印再被捕获，看着像报错。

## 5. 可复用结论
- GitHub 连接器是 App 令牌时，**建仓库 / 推送都得走 PAT + REST API**，MCP 那套创建接口不可用。
- 以后任何「在 WorkBuddy 里操作 GitHub 仓库」的需求，统一用 `sync_repo.py` + `GH_TOKEN` 环境变量模式：
  ```bash
  GH_TOKEN='ghp_xxx' python3 sync_repo.py
  ```
- 令牌敏感：走环境变量传入、不写盘、不在对话里长期保留；用完建议到 GitHub 撤销重发。
- 账号已确认：`baticancer`。
