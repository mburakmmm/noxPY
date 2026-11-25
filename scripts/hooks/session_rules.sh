#!/bin/sh
# SessionStart / SubagentStart hook: injects concise strict RTK + NoxPy Phase 0 discipline.
set -eu

ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
AGENT="claude"
case "${1:-}" in
  --agent=*) AGENT="${1#--agent=}" ;;
  --agent) AGENT="${2:-claude}" ;;
esac

input="$(cat 2>/dev/null || true)"
msg="[NoxPy Strict RTK & Upstream Policy] Use rtk commands for shell work (rtk ls/find/grep/rg/read/git/diff/err/test/pytest). Never pipe rtk output to | head/tail/cut/awk/sed, never use raw cat/head/tail/sed -n, and use rtk proxy <cmd> only when byte-exact raw stdout is mandatory. NEVER modify /Users/melihburakmemis/Documents/nox-lang (read-only; managed by user). Phase 0 baseline: noxc 2.2.70."

if ! command -v rtk >/dev/null 2>&1; then
  msg="$msg WARNING: rtk missing from PATH!"
fi

if [ "$AGENT" = "antigravity" ]; then
  printf '%s' "$msg" | python3 -c 'import json,sys; print(json.dumps({"injectSteps":[{"systemMessage":{"systemMessage":sys.stdin.read()}}]}))'
  exit 0
fi

case "$input" in
  *'"SubagentStart"'*)
    printf '%s' "$msg" | python3 -c 'import json,sys; print(json.dumps({"hookSpecificOutput":{"hookEventName":"SubagentStart","additionalContext":sys.stdin.read()}}))'
    ;;
  *)
    printf '%s\n' "$msg"
    ;;
esac
exit 0
