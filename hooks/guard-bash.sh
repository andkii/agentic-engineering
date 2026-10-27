#!/bin/sh
# PreToolUse entry point. Fails closed: if the guard cannot run, the command is
# blocked, because a silently disabled guard is worse than none.
command -v python3 >/dev/null 2>&1 || {
  echo "eng-laws guard: python3 is required. Install it or disable the eng-laws plugin." >&2
  exit 2
}
python3 "$(dirname "$0")/guard.py"
rc=$?
case $rc in
  0 | 2) exit "$rc" ;;
  *)
    echo "eng-laws guard: internal error (exit $rc); failing closed." >&2
    exit 2
    ;;
esac
