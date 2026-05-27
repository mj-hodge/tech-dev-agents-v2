#!/usr/bin/env bash

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=tests/predeploy/common.sh
source "${script_dir}/common.sh"

check_name="Dependency Audit"
predeploy_print_banner "$check_name"

manifest_found=0
manifest_summary=()

for manifest in pyproject.toml poetry.lock requirements.txt requirements-dev.txt package.json package-lock.json; do
  if [[ -f "${manifest}" ]]; then
    manifest_found=1
    manifest_summary+=("${manifest}")
  fi
done

if [[ $manifest_found -eq 0 ]]; then
  predeploy_blocked "$check_name" "No dependency manifest found in the worktree. Add a Python or Node manifest and rerun the audit."
fi

if predeploy_has_tool pip-audit && [[ -f requirements.txt || -f requirements-dev.txt ]]; then
  audit_file="requirements.txt"
  [[ -f requirements-dev.txt ]] && audit_file="requirements-dev.txt"
  if predeploy_run "$check_name" pip-audit --requirement "$audit_file"; then
    predeploy_pass "$check_name" "pip-audit reported no vulnerabilities."
  else
    rc=$?
    if [[ $rc -eq 1 ]]; then
      predeploy_fail "$check_name" "pip-audit reported vulnerabilities."
    else
      predeploy_blocked "$check_name" "pip-audit could not complete successfully."
    fi
  fi
elif predeploy_has_tool npm && [[ -f package.json ]]; then
  if predeploy_run "$check_name" npm audit --audit-level=high; then
    predeploy_pass "$check_name" "npm audit reported no high-severity vulnerabilities."
  else
    rc=$?
    if [[ $rc -eq 1 ]]; then
      predeploy_fail "$check_name" "npm audit reported high-severity vulnerabilities."
    else
      predeploy_blocked "$check_name" "npm audit could not complete successfully."
    fi
  fi
else
  predeploy_blocked "$check_name" "No compatible audit tool/manifest pair was available. Install pip-audit or use npm with package.json."
fi
