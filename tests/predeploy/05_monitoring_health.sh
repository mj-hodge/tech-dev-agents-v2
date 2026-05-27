#!/usr/bin/env bash

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=tests/predeploy/common.sh
source "${script_dir}/common.sh"

check_name="Monitoring Health"
predeploy_print_banner "$check_name"

base_url="${BASE_URL:-}"
if [[ -z "$base_url" ]]; then
  predeploy_blocked "$check_name" "BASE_URL is unset. Set it to the deployed service URL and rerun."
fi

health_url="${base_url%/}/health"
ready_url="${base_url%/}/health/ready"
metrics_url="${base_url%/}/metrics"

if ! predeploy_has_tool curl; then
  predeploy_blocked "$check_name" "curl is required for endpoint checks."
fi

health_code="$(curl -sS -o /tmp/predeploy-health.out -w '%{http_code}' "$health_url" || true)"
ready_code="$(curl -sS -o /tmp/predeploy-ready.out -w '%{http_code}' "$ready_url" || true)"
metrics_code="$(curl -sS -o /tmp/predeploy-metrics.out -w '%{http_code}' "$metrics_url" || true)"
metrics_family_count="$(grep -c '^# HELP' /tmp/predeploy-metrics.out 2>/dev/null || true)"

predeploy_print_note "$check_name" "health=${health_url} code=${health_code}"
predeploy_print_note "$check_name" "ready=${ready_url} code=${ready_code}"
predeploy_print_note "$check_name" "metrics=${metrics_url} code=${metrics_code} families=${metrics_family_count}"

alert_rule_found=0
for alert_file in infra/monitoring/alert-rules.yaml infra/monitoring/alert-rules.yml features/story-001-container-runtime/alert-rules.yaml; do
  if [[ -f "$alert_file" ]]; then
    alert_rule_found=1
    predeploy_print_note "$check_name" "alert_rules_file=${alert_file}"
  fi
done

if [[ "$health_code" == "200" && "$ready_code" == "200" && "$metrics_code" == "200" && "$metrics_family_count" -gt 0 && $alert_rule_found -eq 1 ]]; then
  predeploy_pass "$check_name" "All monitoring endpoints responded successfully and alert rules were found."
else
  predeploy_blocked "$check_name" "One or more monitoring prerequisites were missing or the endpoints were not reachable."
fi
