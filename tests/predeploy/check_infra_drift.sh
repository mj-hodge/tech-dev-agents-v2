#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

CHECK_NAME="4/INFRA_DRIFT"
ROOT_DIR="$(cd -- "$SCRIPT_DIR/../.." && pwd)"

cd "$ROOT_DIR"

if command_exists terraform && [[ -d infra ]]; then
  tmp_output="$(mktemp)"
  set +e
  (cd infra && terraform init -backend=false >/dev/null 2>&1 && terraform plan -detailed-exitcode) >"$tmp_output" 2>&1
  rc=$?
  set -e

  if [[ $rc -eq 0 ]]; then
    finish_check "$CHECK_NAME" "PASS" "terraform plan reported no drift"
  fi

  if [[ $rc -eq 2 ]]; then
    finish_check "$CHECK_NAME" "FAIL" "terraform plan reported drift: $(tr '\n' ' ' <"$tmp_output" | sed 's/[[:space:]]\+/ /g')"
  fi

  finish_check "$CHECK_NAME" "BLOCKED" "terraform plan failed with code $rc: $(tr '\n' ' ' <"$tmp_output" | sed 's/[[:space:]]\+/ /g')"
fi

if command_exists az && [[ -n "${AZURE_RESOURCE_GROUP:-}" ]]; then
  tmp_output="$(mktemp)"
  set +e
  az deployment group what-if --resource-group "$AZURE_RESOURCE_GROUP" --template-file "$AZURE_TEMPLATE_FILE" ${AZURE_PARAMETERS_FILE:+--parameters @"$AZURE_PARAMETERS_FILE"} >"$tmp_output" 2>&1
  rc=$?
  set -e

  if [[ $rc -eq 0 ]]; then
    finish_check "$CHECK_NAME" "PASS" "az what-if completed"
  fi

  finish_check "$CHECK_NAME" "BLOCKED" "az what-if failed with code $rc: $(tr '\n' ' ' <"$tmp_output" | sed 's/[[:space:]]\+/ /g')"
fi

finish_check "$CHECK_NAME" "BLOCKED" "no infrastructure config or drift tool is available in this worktree"

