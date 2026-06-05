#!/usr/bin/env bash
# Knowledge Evolution v1.0.1 — push to GitHub
# 用法: ./push_to_github.sh <github-username> [repo-name]
# 例:   ./push_to_github.sh pommotion knowledge-evolution
set -e

REPO_NAME="${2:-knowledge-evolution}"
GITHUB_USER="$1"

if [ -z "$GITHUB_USER" ]; then
  echo "❌ 用法: $0 <github-username> [repo-name]"
  echo "   例: $0 pommotion knowledge-evolution"
  exit 1
fi

echo "📦 准备推送: https://github.com/${GITHUB_USER}/${REPO_NAME}.git"
echo ""

# Step 1: 在 GitHub 网页端手动创建空仓库（不要勾选 README/license/.gitignore）
echo "⚠️  请先在 https://github.com/new 创建一个空仓库:"
echo "   - Repository name: ${REPO_NAME}"
echo "   - 取消勾选 Add a README file"
echo "   - 取消勾选 Add .gitignore"
echo "   - 取消勾选 Choose a license"
echo ""
read -p "✅ 仓库创建好了吗？(y/n) " CREATED
if [ "$CREATED" != "y" ]; then
  echo "👋 创建好后重新跑这个脚本"
  exit 0
fi

# Step 2: 初始化 git
cd "$(dirname "$0")"
git init
git add -A
git commit -m "feat: knowledge-evolution v1.0.1

- 3-step structured knowledge base evolution (scan → connection → action)
- 4-dimension quality scoring + diversity selection
- 3-layer connection discovery (TF-IDF + LLM + cross-domain bridging)
- LLM-empty fallback for connections (heuristic TF-IDF pairs)
- 3 action execution paths (supplement / create / connect)
- create-only with references (never modifies original notes)
- Report dedup on save
- Robust keyword extraction (quoted-then-verb)"
echo ""
echo "✅ Commit 完成"

# Step 3: 关联远程并推送
git branch -M main
git remote add origin "https://github.com/${GITHUB_USER}/${REPO_NAME}.git"
echo "🚀 正在推送..."
git push -u origin main

echo ""
echo "🎉 推送完成！访问: https://github.com/${GITHUB_USER}/${REPO_NAME}"
