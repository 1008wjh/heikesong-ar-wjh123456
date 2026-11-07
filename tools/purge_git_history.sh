#!/usr/bin/env bash
# 危险的维护脚本：从「全部 git 历史」里删掉真实人脉数据（声纹/照片）。
#
# 适用场景：仓库曾经提交过 data/people.json 或 data/photos/*，现在想彻底清掉。
# 注意：
#   1. 会改写 commit hash（包括别人已经拉过的提交）；
#   2. 之后必须 `git push --force-with-lease`，队友要重新 clone；
#   3. GitHub 上的旧对象不会立刻消失，需要联系 GitHub 支持或等 GC；
#   4. 先备份：cp -r . ../memorylens-backup 或 git clone --mirror。
#
# 用法（在仓库根目录）：
#   bash tools/purge_git_history.sh --dry-run   # 先看会删哪些路径
#   bash tools/purge_git_history.sh             # 真正改写（会二次确认）

set -euo pipefail

if [[ "${1:-}" == "--dry-run" ]]; then
  echo "当前历史里包含这些敏感路径的提交："
  git log --all --oneline -- data/people.json data/photos || true
  echo
  echo "对象："
  git rev-list --objects --all | grep -E 'data/(people\.json|photos/)' || true
  echo
  echo "dry-run 结束；没有修改任何东西。"
  exit 0
fi

if [[ "${1:-}" != "--yes" ]]; then
  echo "这会改写全部 commit hash，并需要 force push。"
  read -r -p "确认请输入 REWRITE: " answer
  if [[ "$answer" != "REWRITE" ]]; then
    echo "已取消。"
    exit 0
  fi
fi

echo "改写前备份到 ../memorylens-history-backup.git（mirror）"
git clone --mirror . ../memorylens-history-backup.git 2>/dev/null || true

echo "开始改写历史..."
git filter-branch --force --index-filter \
  'git rm -r --cached --ignore-unmatch data/people.json data/photos' \
  --prune-empty --tag-name-filter cat -- --all

echo "清理引用和旧对象..."
git for-each-ref --format='delete %(refname)' refs/original | git update-ref --stdin || true
git reflog expire --expire=now --all
git gc --prune=now --aggressive

echo
echo "完成。检查："
echo "  git log --all --oneline -- data/people.json data/photos   # 应该为空"
echo "  git status"
echo "然后（确认无误后）："
echo "  git push --force-with-lease origin main"
echo "队友需要重新 clone。"
