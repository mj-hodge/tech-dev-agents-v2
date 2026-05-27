#!/usr/bin/env bash

set -u -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/lib.sh"

REPO_ROOT="$(predeploy_repo_root)"
cd "$REPO_ROOT"

CHECK_NAME="logs-ingestion"

if ! command -v az >/dev/null 2>&1; then
  predeploy_print_result \
    "$CHECK_NAME" \
    "BLOCKED" \
    "az containerapp logs show / az monitor log-analytics query" \
    "Azure CLI is not installed in this environment." \
    "Install Azure CLI where predeploy runs, then retry."
  exit 1
fi

if [[ -z "${AZURE_RESOURCE_GROUP:-}" || -z "${AZURE_CONTAINER_APP:-}" ]]; then
  predeploy_print_result \
    "$CHECK_NAME" \
    "BLOCKED" \
    "az containerapp logs show" \
    "AZURE_RESOURCE_GROUP or AZURE_CONTAINER_APP is missing." \
    "Export AZURE_RESOURCE_GROUP and AZURE_CONTAINER_APP, then rerun."
  exit 1
fi

if [[ -z "${LOG_ANALYTICS_WORKSPACE:-}" && -z "${LOG_ANALYTICS_WORKSPACE_ID:-}" ]]; then
  predeploy_print_result \
    "$CHECK_NAME" \
    "BLOCKED" \
    "az monitor log-analytics query" \
    "LOG_ANALYTICS_WORKSPACE or LOG_ANALYTICS_WORKSPACE_ID is missing." \
    "Export LOG_ANALYTICS_WORKSPACE (name) or LOG_ANALYTICS_WORKSPACE_ID, then rerun."
  exit 1
fi

workspace="${LOG_ANALYTICS_WORKSPACE_ID:-${LOG_ANALYTICS_WORKSPACE}}"

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

status="PASS"

if ! az containerapp logs show \
  --name "$AZURE_CONTAINER_APP" \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --tail 20 >"$tmpdir/containerapp_logs.txt" 2>&1; then
  status="FAIL"
fi

query="ContainerAppConsoleLogs_CL | where ContainerAppName_s == '${AZURE_CONTAINER_APP}' | where TimeGenerated > ago(60m) | take 1"
if ! az monitor log-analytics query \
  --workspace "$workspace" \
  --analytics-query "$query" \
  --timespan PT1H >"$tmpdir/log_query.json" 2>&1; then
  status="FAIL"
fi

if [[ "$status" == "PASS" ]]; then
  if ! rg -q "ContainerAppConsoleLogs_CL|ContainerAppName_s|rows|tables" "$tmpdir/log_query.json"; then
    status="FAIL"
  fi
fi

predeploy_print_result \
  "$CHECK_NAME" \
  "$status" \
  "az containerapp logs show + az monitor log-analytics query" \
  "resource_group=${AZURE_RESOURCE_GROUP}; container_app=${AZURE_CONTAINER_APP}; workspace=${workspace}" \
  "Ensure container logs are emitted and workspace ingestion is enabled."
printf 'containerapp_logs_begin\n'
cat "$tmpdir/containerapp_logs.txt"
printf '\ncontainerapp_logs_end\n'
printf 'log_analytics_query_begin\n'
cat "$tmpdir/log_query.json"
printf '\nlog_analytics_query_end\n'

[[ "$status" == "PASS" ]]
