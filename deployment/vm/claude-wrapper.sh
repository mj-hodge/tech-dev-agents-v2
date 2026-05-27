#!/bin/bash
# Force all claude calls through the SDK tool
# Dan must not run claude directly — the SDK tool handles approvals and logging
BLOCKED_FLAGS=(
  "--dangerously-skip-permissions"
  "--bypass-permissions"
  "--permission-mode"
)

for arg in "$@"; do
  for blocked in "${BLOCKED_FLAGS[@]}"; do
    if [[ "$arg" == "$blocked"* ]]; then
      echo "[BLOCKED] $arg — use the SDK tool: python3 /opt/agent/claude_sdk_tool.py" >&2
      exit 1
    fi
  done
done

exec /usr/bin/claude-real "$@"
