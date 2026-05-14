#!/bin/bash
# push.sh — GitHub Actions との競合を自動解決して push する
# Usage: bash push.sh [commit message]
set -euo pipefail

cd "$(dirname "$0")"

MSG="${1:-Update}"

# Stage & commit (docs/ は含めない)
git add -A -- ':!docs/'
if git diff --cached --quiet; then
  echo "コミットするものがありません"
else
  git commit -m "$MSG"
fi

# pull --rebase で最新を取得、docs/ の競合は theirs を採用
if ! git pull --rebase origin main 2>&1; then
  echo "競合を自動解決中 (docs/ は remote を採用)..."
  git checkout --theirs docs/ 2>/dev/null || true
  git add docs/ 2>/dev/null || true
  GIT_EDITOR=true git rebase --continue
fi

# docs/ を最新コードで再生成してコミット
echo "ダッシュボード再生成中..."
python3 generate_dashboard.py >> log.txt 2>&1
git add docs/
if ! git diff --cached --quiet; then
  git commit -m "Regenerate dashboard"
fi

git push
echo "push 完了"
