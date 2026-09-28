#!/usr/bin/env bash
# 分支守卫：防止「控制器以为在 A 分支，提交却落在 B 分支」。
#
# 背景：2026-09-28 出过一次事故——越界的子代理自己 git switch -c 建了分支并切了过去，
# 控制器后续 19 个提交全部落在了一个自己没意识到的分支上，直到合并时才发现。
# 光靠「记得检查分支」这种自觉是防不住的，所以做成机械约束：
#
#   scripts/agent-guard.sh claim <branch>   # 派发子代理前：声明本轮工作的分支
#   scripts/agent-guard.sh check            # 提交时由 git hook 调用
#   scripts/agent-guard.sh release          # 本轮结束：解除约束
#
# 没 claim 过的时候 check 永远通过——不干扰日常单人开发。
set -euo pipefail

root=$(git rev-parse --show-toplevel)
marker="$root/.agent-branch"

current_branch() {
  git rev-parse --abbrev-ref HEAD
}

case "${1:-check}" in
  claim)
    if [ $# -ne 2 ]; then
      echo "用法: $0 claim <branch>" >&2
      exit 2
    fi
    echo "$2" > "$marker"
    echo "本轮预期分支锁为：$2（当前：$(current_branch)）"
    ;;
  release)
    rm -f "$marker"
    echo "已解除分支锁"
    ;;
  check)
    if [ ! -f "$marker" ]; then
      exit 0
    fi
    expected=$(cat "$marker")
    actual=$(current_branch)
    if [ "$expected" != "$actual" ]; then
      cat >&2 <<EOF

✖ 提交被拒绝：当前分支与预期不符。

  预期分支（.agent-branch）：$expected
  当前分支：              $actual

  这通常意味着有子代理自己切了分支，或者你自己切错了分支。
  先确认：
    git branch -a -v
    git reflog -10
  确认清楚后，要么回到预期分支，要么用下面这条把预期改成你真正要提交的分支：
    scripts/agent-guard.sh claim $actual

EOF
      exit 1
    fi
    ;;
  status)
    if [ -f "$marker" ]; then
      echo "branch-lock: $(cat "$marker") (current: $(current_branch))"
    else
      echo "branch-lock: none (current: $(current_branch))"
    fi
    ;;
  *)
    echo "用法: $0 {claim <branch>|check|release|status}" >&2
    exit 2
    ;;
esac
