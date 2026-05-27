#!/usr/bin/env bash

set -u -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/lib.sh"

REPO_ROOT="$(predeploy_repo_root)"
cd "$REPO_ROOT"

output_file="$(mktemp)"
if predeploy_capture "$output_file" pytest -m smoke --tb=short -q; then
  status="PASS"
else
  rc=$?
  if [[ "$rc" -eq 5 ]] || grep -qi 'deselected' "$output_file"; then
    status="BLOCKED"
  else
    status="FAIL"
  fi
fi

predeploy_print_result \
  "smoke-tests" \
  "$status" \
  "pytest -m smoke --tb=short -q" \
  "Smoke-marker test selection was evaluated." \
  "Add smoke-marked tests or configure the smoke suite, then rerun."
printf 'command_output_begin\n'
cat "$output_file"
printf 'command_output_end\n'
rm -f "$output_file"

[[ "$status" == "PASS" ]]

