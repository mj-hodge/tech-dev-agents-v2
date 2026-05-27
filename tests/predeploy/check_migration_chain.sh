#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

CHECK_NAME="7/MIGRATION_CHAIN"
ROOT_DIR="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT_DIR"

if ! command_exists alembic; then
  finish_check "$CHECK_NAME" "BLOCKED" "alembic is not installed"
fi

tmp_output="$(mktemp)"
set +e
alembic heads >"$tmp_output" 2>&1
rc=$?
set -e

if [[ $rc -ne 0 ]]; then
  finish_check "$CHECK_NAME" "BLOCKED" "alembic heads failed: $(tr '\n' ' ' <"$tmp_output" | sed 's/[[:space:]]\+/ /g')"
fi

head_count="$(grep -c ' (head)$' "$tmp_output" || true)"
if [[ "$head_count" -eq 1 ]]; then
  finish_check "$CHECK_NAME" "PASS" "single migration head found: $(tr '\n' ' ' <"$tmp_output" | sed 's/[[:space:]]\+/ /g')"
fi

if [[ "$head_count" -gt 1 ]]; then
  finish_check "$CHECK_NAME" "FAIL" "multiple migration heads found: $(tr '\n' ' ' <"$tmp_output" | sed 's/[[:space:]]\+/ /g')"
fi

finish_check "$CHECK_NAME" "BLOCKED" "alembic heads returned no heads; output: $(tr '\n' ' ' <"$tmp_output" | sed 's/[[:space:]]\+/ /g')"

