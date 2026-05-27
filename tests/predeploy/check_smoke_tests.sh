#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

CHECK_NAME="8/SMOKE_TESTS"
ROOT_DIR="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT_DIR"

tmp_output="$(mktemp)"
set +e
pytest -m smoke --tb=short >"$tmp_output" 2>&1
rc=$?
set -e

if [[ $rc -eq 0 ]]; then
  finish_check "$CHECK_NAME" "PASS" "pytest -m smoke completed successfully: $(tr '\n' ' ' <"$tmp_output" | sed 's/[[:space:]]\+/ /g')"
fi

if [[ $rc -eq 5 ]]; then
  fallback_output="$(mktemp)"
  set +e
  pytest --tb=short >"$fallback_output" 2>&1
  fallback_rc=$?
  set -e
  if [[ $fallback_rc -eq 0 ]]; then
    finish_check "$CHECK_NAME" "BLOCKED" "no smoke tests were collected; full pytest suite passed as fallback: $(tr '\n' ' ' <"$fallback_output" | sed 's/[[:space:]]\+/ /g')"
  fi
  finish_check "$CHECK_NAME" "BLOCKED" "no smoke tests were collected and full pytest fallback failed: $(tr '\n' ' ' <"$fallback_output" | sed 's/[[:space:]]\+/ /g')"
fi

finish_check "$CHECK_NAME" "FAIL" "pytest -m smoke failed: $(tr '\n' ' ' <"$tmp_output" | sed 's/[[:space:]]\+/ /g')"

