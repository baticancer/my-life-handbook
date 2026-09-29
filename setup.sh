#!/usr/bin/env bash
#
# 一键创建并推送 GitHub 私有仓库：my-life-handbook
#
# 前置条件（在本机终端执行一次即可）：
#   1. 安装 gh:        https://cli.github.com/
#   2. 登录:           gh auth login
#   3. 进入本目录:     cd my-life-handbook
#   4. 运行:           bash setup.sh
#
# 脚本会自动 git init、提交、创建私有仓库并推送。
# 若你更想用 GitHub Personal Access Token（PAT），见文件末尾「方式 B」。

set -euo pipefail

REPO="my-life-handbook"
DESC="个人工作与生活的爱好和习惯手册 / My personal handbook for work, life, hobbies and habits"
DIR="$(cd "$(dirname "$0")" && pwd)"

cd "$DIR"

# —— 检查 gh ——
if ! command -v gh >/dev/null 2>&1; then
  echo "❌ 未检测到 gh，请先安装并登录：https://cli.github.com/  （然后 gh auth login）"
  exit 1
fi

if [ -z "$(gh auth status 2>&1)" ] || ! gh auth status >/dev/null 2>&1; then
  echo "⚠️  尚未登录 GitHub，请先运行：gh auth login"
  exit 1
fi

# —— git 初始化并提交 ——
if [ ! -d .git ]; then
  git init -q
  git branch -M main
fi
git add -A
git commit -q -m "init: my-life-handbook 骨架（README + 工作/生活/爱好/习惯 + 模板）" || echo "（无新提交，继续）"

# —— 创建私有仓库并推送 ——
echo "🚀 正在创建私有仓库 $REPO 并推送……"
gh repo create "$REPO" --private --description "$DESC" --source . --remote origin --push

echo ""
echo "✅ 完成！仓库地址："
gh repo view "$REPO" --json url -q .url 2>/dev/null || echo "https://github.com/$(gh api user -q .login)/$REPO"

# ============================================================
# 方式 B：用 Personal Access Token（PAT）创建（可选）
#   适用于无法用 gh 登录的场景。把下方 TOKEN 换成你的 PAT（需 repo 权限）。
#   然后在本目录执行：
#
#   TOKEN=ghp_xxx
#   USER=$(curl -s -H "Authorization: Bearer $TOKEN" https://api.github.com/user | grep -o '"login":"[^"]*"' | head -1 | cut -d'"' -f4)
#   curl -s -X POST -H "Authorization: Bearer $TOKEN" \
#     -H "Accept: application/vnd.github+json" \
#     https://api.github.com/user/repos \
#     -d "{\"name\":\"my-life-handbook\",\"private\":true,\"description\":\"$DESC\"}"
#   git remote add origin https://$TOKEN@github.com/$USER/my-life-handbook.git
#   git push -u origin main
# ============================================================
