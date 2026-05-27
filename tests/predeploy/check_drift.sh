#!/usr/bin/env bash

set -u -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/lib.sh"

REPO_ROOT="$(predeploy_repo_root)"
cd "$REPO_ROOT"

infra_dir=""
for candidate in infra terraform bicep; do
  if [[ -d "$candidate" ]]; then
    infra_dir="$candidate"
    break
  fi
done

if ! command -v terraform >/dev/null 2>&1 && ! command -v bicep >/dev/null 2>&1; then
  predeploy_print_result \
    "infrastructure-drift" \
    "BLOCKED" \
    "terraform plan -detailed-exitcode | az deployment group what-if" \
    "Neither terraform nor bicep CLI is installed, and no infrastructure directory is present in this repository snapshot." \
    "Install terraform or bicep, point the script at the real infra directory, and rerun."
  exit 1
fi

if [[ -z "$infra_dir" ]]; then
  predeploy_print_result \
    "infrastructure-drift" \
    "BLOCKED" \
    "terraform plan -detailed-exitcode | az deployment group what-if" \
    "No infra directory was found in the worktree, so drift detection cannot be evaluated here." \
    "Add the infrastructure project location to this repo or update the script to the correct infra path."
  exit 1
fi

output_file="$(mktemp)"
if [[ -f "$infra_dir/main.bicep" && command -v bicep >/dev/null 2>&1 ]]; then
  if predeploy_capture "$output_file" bicep build --file "$infra_dir/main.bicep"; then
    status="PASS"
  else
    status="FAIL"
  fi
  command_text="bicep build --file $infra_dir/main.bicep"
else
  if predeploy_capture "$output_file" terraform -chdir="$infra_dir" plan -detailed-exitcode; then
    status="PASS"
  else
    rc=$?
    if [[ "$rc" -eq 2 ]]; then
      status="FAIL"
    else
      status="BLOCKED"
    fi
  fi
  command_text="terraform -chdir=$infra_dir plan -detailed-exitcode"
fi

predeploy_print_result \
  "infrastructure-drift" \
  "$status" \
  "$command_text" \
  "Infrastructure path=${infra_dir}" \
  "Re-run after updating the infra toolchain and confirming the infrastructure source of truth."
printf 'command_output_begin\n'
cat "$output_file"
printf 'command_output_end\n'
rm -f "$output_file"

[[ "$status" == "PASS" ]]

