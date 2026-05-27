#!/usr/bin/env bash

set -u -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/lib.sh"

REPO_ROOT="$(predeploy_repo_root)"
cd "$REPO_ROOT"

if ! command -v alembic >/dev/null 2>&1; then
  predeploy_print_result \
    "migration-chain" \
    "BLOCKED" \
    "alembic heads" \
    "alembic is not installed in this environment." \
    "Install alembic and rerun against a repository with migration metadata."
  exit 1
fi

output_file="$(mktemp)"
if predeploy_capture "$output_file" alembic heads; then
  head_lines="$(grep -c 'head' "$output_file" || true)"
  if [[ "$head_lines" -eq 1 ]]; then
    status="PASS"
  elif [[ "$head_lines" -gt 1 ]]; then
    status="FAIL"
  else
    status="FAIL"
  fi
else
  status="BLOCKED"
fi

if grep -qi "No 'script_location' key found in configuration" "$output_file"; then
  status="BLOCKED"
fi

predeploy_print_result \
  "migration-chain" \
  "$status" \
  "alembic heads" \
  "Checked for a single migration head." \
  "If blocked, add or point to the project's Alembic configuration and rerun."
printf 'command_output_begin\n'
cat "$output_file"
printf 'command_output_end\n'
rm -f "$output_file"

[[ "$status" == "PASS" ]]

