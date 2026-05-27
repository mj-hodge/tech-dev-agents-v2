#!/usr/bin/env bash

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=tests/predeploy/common.sh
source "${script_dir}/common.sh"

check_name="Migration Chain"
predeploy_print_banner "$check_name"

if ! predeploy_has_tool alembic; then
  predeploy_blocked "$check_name" "alembic is not installed."
fi

if [[ ! -f alembic.ini && ! -d migrations && ! -d backend/migrations ]]; then
  predeploy_blocked "$check_name" "No Alembic configuration or migrations directory exists in this worktree."
fi

heads_output="$(alembic heads 2>&1 || true)"
predeploy_print_command "$check_name" alembic heads
printf '%s\n' "$heads_output"
head_count="$(printf '%s\n' "$heads_output" | grep -c '.*' || true)"
predeploy_print_note "$check_name" "head_count=${head_count}"

if printf '%s\n' "$heads_output" | grep -q '^[^[:space:]].*head'; then
  if [[ "$head_count" -eq 1 ]]; then
    predeploy_pass "$check_name" "Single migration head detected."
  else
    predeploy_fail "$check_name" "Multiple migration heads detected."
  fi
else
  predeploy_blocked "$check_name" "alembic heads could not complete successfully."
fi
