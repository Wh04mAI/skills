#!/usr/bin/env bash
# 认领一个 GitHub issue。用法：claim.sh <issue号> [agent标识]
# 退出码：0 认领成功（或补齐了上次没做完的步骤）；2 已被别人认领；1 其他错误。
set -euo pipefail

N="${1:?用法：claim.sh <issue号> [agent标识]}"
AGENT="${2:-claude@$(hostname -s)}"
BRANCH="claim/issue-${N}"
ROOT="$(git rev-parse --show-toplevel)"
WT="${ROOT}/.worktrees/issue-${N}"

die() { echo "错误：$*" >&2; exit 1; }
warn() { echo "警告：$*" >&2; }

STATE="$(gh issue view "${N}" --json state --jq .state)" || die "找不到 issue #${N}"
[ "${STATE}" = "OPEN" ] || die "issue #${N} 已关闭"

git -C "${ROOT}" fetch -q origin main

if [ -d "${WT}" ]; then
  # 本地已有这个 worktree：只有当远端分支就是它推上去的，才算自己的认领，接着补后面的步骤。
  REMOTE_SHA="$(git -C "${ROOT}" ls-remote origin "refs/heads/${BRANCH}" | cut -f1)"
  [ -n "${REMOTE_SHA}" ] || die "本地有 ${WT}，但远端没有 ${BRANCH}。先确认这个 worktree 是否废弃，清理后再认领"
  git -C "${WT}" merge-base --is-ancestor "${REMOTE_SHA}" HEAD 2>/dev/null || die "${WT} 和远端 ${BRANCH} 对不上，可能不是你的认领"
  echo "继续补齐 #${N} 的认领"
else
  git -C "${ROOT}" worktree add -q -b "${BRANCH}" "${WT}" origin/main
  git -C "${WT}" commit -q --allow-empty -m "claim: #${N} by ${AGENT}"
  # 冒号后为空表示「要求远端不存在这个分支」。同时推送时 GitHub 服务端只让一个成功，这一步就是互斥锁。
  if ! git -C "${WT}" push -q --force-with-lease="refs/heads/${BRANCH}:" origin "HEAD:refs/heads/${BRANCH}" 2>/dev/null; then
    git -C "${ROOT}" worktree remove --force "${WT}"
    git -C "${ROOT}" branch -q -D "${BRANCH}"
    HOLDER="$(git -C "${ROOT}" fetch -q origin "${BRANCH}" 2>/dev/null && git -C "${ROOT}" log -1 --format='%s（%an，%ar）' FETCH_HEAD || echo '未知')"
    echo "#${N} 已被认领：${HOLDER}" >&2
    exit 2
  fi
  echo "已认领 #${N}，worktree：${WT}"
fi

# 以下步骤失败不影响认领本身，重跑本脚本即可补齐。
if [ -z "$(gh pr list --head "${BRANCH}" --state open --json number --jq '.[].number')" ]; then
  TITLE="$(gh issue view "${N}" --json title --jq .title)"
  gh pr create --draft --base main --head "${BRANCH}" --title "${TITLE} (#${N})" \
    --body "$(printf 'Closes #%s\n\n认领者：%s' "${N}" "${AGENT}")" >/dev/null \
    && echo "已开草稿 PR" || warn "草稿 PR 没开成，重跑本脚本补齐"
fi
gh issue edit "${N}" --add-assignee @me --remove-label backlog --add-label in-progress >/dev/null \
  || warn "issue 的负责人或标签没改成，重跑本脚本补齐"

echo "worktree：${WT}"
