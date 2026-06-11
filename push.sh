#!/bin/bash
# push.sh — GitHub Actions との競合を自動解決して push する
# Usage: bash push.sh [commit message] [--data]
#   --data : trades.json / execution_history*.json も含めて push（データ復元時のみ使用）
set -euo pipefail

cd "$(dirname "$0")"

MSG="${1:-Update}"
INCLUDE_DATA=false
for arg in "$@"; do
  [[ "$arg" == "--data" ]] && INCLUDE_DATA=true
done

# Stage: docs/ は常に除外。trade data は --data フラグがある時だけ含める
if [[ "$INCLUDE_DATA" == true ]]; then
  git add -A -- ':!docs/'
  echo "※ trades.json / execution_history.json を含めて push します"
else
  # GitHub Actions が管理するファイルは除外
  git add -A -- ':!docs/' ':!trades*.json' ':!execution_history*.json'
fi

if git diff --cached --quiet; then
  echo "コミットするものがありません"
else
  git commit -m "$MSG"
fi

# pull --rebase。競合は remote（GitHub Actions）優先で自動解消
# -X ours = rebase中の "ours" = origin/main（remote）を優先
if ! git pull --rebase -X ours origin main; then
  echo "rebase 失敗。手動で解決してください: git rebase --abort" >&2
  exit 1
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
