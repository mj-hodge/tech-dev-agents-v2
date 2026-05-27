#!/usr/bin/env bash

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=tests/predeploy/common.sh
source "${script_dir}/common.sh"

check_name="Smoke Test Dry-Run"
predeploy_print_banner "$check_name"

if ! predeploy_has_tool pytest; then
  predeploy_blocked "$check_name" "pytest is not installed."
fi

smoke_output_file="$(mktemp /tmp/predeploy-smoke.XXXXXX)"
set +e
pytest -m smoke --tb=short >"$smoke_output_file" 2>&1
smoke_rc=$?
set -e

smoke_output="$(cat "$smoke_output_file")"
printf '%s\n' "$smoke_output"
predeploy_print_note "$check_name" "pytest_exit_code=${smoke_rc}"

if [[ $smoke_rc -eq 5 ]] || printf '%s\n' "$smoke_output" | grep -qiE 'no tests ran|collected 0 items|0 selected'; then
  predeploy_blocked "$check_name" "No smoke-marked tests were collected. Add @pytest.mark.smoke coverage."
elif [[ $smoke_rc -eq 0 ]]; then
  predeploy_pass "$check_name" "Smoke tests passed."
else
  predeploy_fail "$check_name" "Smoke tests failed."
fi
