#!/bin/sh
# PreToolUse guard for Bash. Blocks (exit 2, reason on stderr) commands that
# violate ENGINEERING_LAWS.md. Contract: https://code.claude.com/docs/en/hooks.md
#
# Known limit: the branch check uses the session cwd from the hook payload, so
# `cd <elsewhere> && git commit` is checked against the session cwd, not <elsewhere>.
set -eu

payload=$(cat)
cmd=$(printf '%s' "$payload" | jq -r '.tool_input.command // empty')
cwd=$(printf '%s' "$payload" | jq -r '.cwd // empty')

[ -n "$cmd" ] || exit 0

block() {
  printf 'eng-laws guard: %s\n' "$1" >&2
  exit 2
}

# Production Diligence: never git revert (use git reset).
printf '%s' "$cmd" | grep -Eq '(^|[;&|[:space:]])git[[:space:]]+revert([[:space:]]|$)' &&
  block "git revert is banned. Use git reset to undo commits."

# Production Diligence: never skip hooks or signing.
printf '%s' "$cmd" | grep -Eq '(^|[;&|[:space:]])git[[:space:]]+(commit|push|merge|rebase)[^;&|]*--no-verify' &&
  block "--no-verify is banned unless the user explicitly requests it."

# Production Diligence: never force-push to main/master.
printf '%s' "$cmd" | grep -Eq '(^|[;&|[:space:]])git[[:space:]]+push[^;&|]*(--force([[:space:]]|$)|--force-with-lease|[[:space:]]-f([[:space:]]|$))[^;&|]*[[:space:]](origin[[:space:]]+)?(main|master)([[:space:]]|$)' &&
  block "force-push to main/master is banned."

# Worktrees: no commits or pushes from the main branch.
if printf '%s' "$cmd" | grep -Eq '(^|[;&|[:space:]])git[[:space:]]+(commit|push)([[:space:]]|$)'; then
  [ -n "$cwd" ] || exit 0
  branch=$(git -C "$cwd" rev-parse --abbrev-ref HEAD 2>/dev/null || true)
  case "$branch" in
    main|master)
      block "on '$branch'. Every code change goes in a worktree on its own branch: git worktree add .claude/worktrees/<slug> -b <branch> origin/$branch"
      ;;
  esac
fi

exit 0
