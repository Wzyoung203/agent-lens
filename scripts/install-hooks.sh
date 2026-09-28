#!/usr/bin/env bash
# 把仓库自带的 hooks 目录接到 .git/hooks 上。克隆之后跑一次即可。
#
# git 不允许把 hooks 提交进版本库，所以走 core.hooksPath 指向受版本管理的 scripts/hooks。
set -euo pipefail

root=$(git rev-parse --show-toplevel)
cd "$root"

chmod +x scripts/agent-guard.sh scripts/hooks/pre-commit
git config core.hooksPath scripts/hooks

echo "已安装：core.hooksPath = $(git config core.hooksPath)"
echo "验证：scripts/agent-guard.sh status"
scripts/agent-guard.sh status
