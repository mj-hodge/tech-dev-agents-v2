#!/usr/bin/env bash

set -u -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/lib.sh"

REPO_ROOT="$(predeploy_repo_root)"
cd "$REPO_ROOT"

if [[ -z "${BASE_URL:-}" ]]; then
  predeploy_print_result \
    "monitoring-health" \
    "BLOCKED" \
    "curl -sf <BASE_URL>/health; curl -sf <BASE_URL>/health/ready; curl -sf <BASE_URL>/metrics" \
    "BASE_URL is not set, so health endpoint verification cannot run." \
    "Export BASE_URL for the deployed or local environment and rerun."
  exit 1
fi

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

status="PASS"
health_output="$tmpdir/health.out"
ready_output="$tmpdir/ready.out"
metrics_output="$tmpdir/metrics.out"

if ! curl -sS -o "$health_output" -w '%{http_code} %{time_total}' "$BASE_URL/health" >"$tmpdir/health.meta" 2>&1; then
  status="FAIL"
fi
if ! curl -sS -o "$ready_output" -w '%{http_code} %{time_total}' "$BASE_URL/health/ready" >"$tmpdir/ready.meta" 2>&1; then
  status="FAIL"
fi
if ! curl -sS -o "$metrics_output" -w '%{http_code} %{time_total}' "$BASE_URL/metrics" >"$tmpdir/metrics.meta" 2>&1; then
  status="FAIL"
fi

predeploy_print_result \
  "monitoring-health" \
  "$status" \
  "curl -sS -o <files> -w '%{http_code} %{time_total}' $BASE_URL/health etc." \
  "BASE_URL=${BASE_URL}" \
  "If blocked or failed, verify the service is deployed and exposes /health, /health/ready, and /metrics."
printf 'health_endpoint_meta='
cat "$tmpdir/health.meta"
printf '\nhealth_endpoint_body_begin\n'
cat "$health_output"
printf '\nhealth_endpoint_body_end\n'
printf 'ready_endpoint_meta='
cat "$tmpdir/ready.meta"
printf '\nready_endpoint_body_begin\n'
cat "$ready_output"
printf '\nready_endpoint_body_end\n'
printf 'metrics_endpoint_meta='
cat "$tmpdir/metrics.meta"
printf '\nmetrics_endpoint_body_begin\n'
cat "$metrics_output"
printf '\nmetrics_endpoint_body_end\n'

[[ "$status" == "PASS" ]]
