#!/usr/bin/env bash

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=tests/predeploy/common.sh
source "${script_dir}/common.sh"

check_name="Secrets Scan"
predeploy_print_banner "$check_name"

if predeploy_has_tool gitleaks; then
  if predeploy_run "$check_name" gitleaks detect --source . --exit-code 1; then
    predeploy_pass "$check_name" "gitleaks completed with no findings."
  else
    rc=$?
    if [[ $rc -eq 1 ]]; then
      predeploy_fail "$check_name" "gitleaks detected one or more secrets."
    else
      predeploy_blocked "$check_name" "gitleaks could not complete successfully."
    fi
  fi
elif predeploy_has_tool trufflehog; then
  if predeploy_run "$check_name" trufflehog filesystem . --only-verified --fail; then
    predeploy_pass "$check_name" "trufflehog completed with no verified secrets."
  else
    rc=$?
    if [[ $rc -eq 1 ]]; then
      predeploy_fail "$check_name" "trufflehog detected one or more secrets."
    else
      predeploy_blocked "$check_name" "trufflehog could not complete successfully."
    fi
  fi
else
  predeploy_blocked "$check_name" "Neither gitleaks nor trufflehog is installed. Install one of them and rerun the scan."
fi
