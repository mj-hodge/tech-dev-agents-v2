#!/usr/bin/env bash

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=tests/predeploy/common.sh
source "${script_dir}/common.sh"

check_name="Infrastructure Drift"
predeploy_print_banner "$check_name"

if ! predeploy_has_tool terraform && ! predeploy_has_tool az; then
  predeploy_blocked "$check_name" "Neither terraform nor az CLI is installed."
fi

if [[ ! -d infra ]]; then
  predeploy_blocked "$check_name" "No infra/ directory exists in this worktree. Add infrastructure configuration before running drift detection."
fi

if predeploy_has_tool terraform && [[ -f infra/terraform.tfstate || -f infra/main.tf || -f infra/versions.tf ]]; then
  if predeploy_run "$check_name" bash -lc "cd infra && terraform init -backend=true >/tmp/predeploy-terraform-init.log 2>&1 && terraform plan -detailed-exitcode"; then
    predeploy_pass "$check_name" "terraform plan reported no drift."
  else
    rc=$?
    if [[ $rc -eq 2 ]]; then
      predeploy_fail "$check_name" "terraform plan reported infrastructure changes."
    else
      predeploy_blocked "$check_name" "terraform plan could not complete successfully. See the command output above."
    fi
  fi
elif predeploy_has_tool az; then
  rg_name="${RESOURCE_GROUP:-}"
  template_file="${AZ_TEMPLATE_FILE:-}"
  parameters_file="${AZ_PARAMETERS_FILE:-}"
  if [[ -z "$rg_name" || -z "$template_file" || -z "$parameters_file" ]]; then
    predeploy_blocked "$check_name" "Set RESOURCE_GROUP, AZ_TEMPLATE_FILE, and AZ_PARAMETERS_FILE to run az deployment group what-if."
  fi
  if predeploy_run "$check_name" az deployment group what-if --resource-group "$rg_name" --template-file "$template_file" --parameters "@${parameters_file}"; then
    predeploy_pass "$check_name" "az deployment group what-if reported no unexpected drift."
  else
    rc=$?
    if [[ $rc -eq 1 ]]; then
      predeploy_fail "$check_name" "az deployment group what-if reported unexpected changes or deployment errors."
    else
      predeploy_blocked "$check_name" "az deployment group what-if could not complete successfully."
    fi
  fi
else
  predeploy_blocked "$check_name" "No supported drift workflow is available."
fi
