#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

CHECK_NAME="5/MONITORING_HEALTH"
BASE_URL_VALUE="${BASE_URL:-http://127.0.0.1:3978}"
blocked_reason=""

check_endpoint() {
  local endpoint="$1"
  local expected_code="$2"
  local out_file
  out_file="$(mktemp)"
  local start end elapsed status body
  start="$(date +%s%3N)"
  set +e
  status="$(curl -sS -o "$out_file" -w '%{http_code}' --max-time 5 "${BASE_URL_VALUE}${endpoint}")"
  rc=$?
  set -e
  end="$(date +%s%3N)"
  elapsed="$((end - start))"
  body="$(tr '\n' ' ' <"$out_file" | sed 's/[[:space:]]\+/ /g')"

  if [[ $rc -ne 0 ]]; then
    blocked_reason="curl failed for ${endpoint} against ${BASE_URL_VALUE}: ${body:-curl exit $rc}"
    return 2
  fi

  if [[ "$status" != "$expected_code" ]]; then
    blocked_reason="unexpected HTTP $status for ${endpoint} against ${BASE_URL_VALUE}; body=${body}"
    return 1
  fi

  log_line "$CHECK_NAME" "endpoint ${endpoint} returned HTTP ${status} in ${elapsed}ms"
  return 0
}

overall=0

check_endpoint "/health" "200" || overall=$?
if [[ $overall -eq 1 ]]; then
  finish_check "$CHECK_NAME" "FAIL" "$blocked_reason"
fi
if [[ $overall -eq 2 ]]; then
  finish_check "$CHECK_NAME" "BLOCKED" "$blocked_reason"
fi

check_endpoint "/health/ready" "200" || overall=$?
if [[ $overall -eq 1 ]]; then
  finish_check "$CHECK_NAME" "FAIL" "$blocked_reason"
fi
if [[ $overall -eq 2 ]]; then
  finish_check "$CHECK_NAME" "BLOCKED" "$blocked_reason"
fi

check_endpoint "/metrics" "200" || overall=$?
if [[ $overall -eq 1 ]]; then
  finish_check "$CHECK_NAME" "FAIL" "$blocked_reason"
fi
if [[ $overall -eq 2 ]]; then
  finish_check "$CHECK_NAME" "BLOCKED" "$blocked_reason"
fi

alert_rule_found="$(find . -path './infra/monitoring/alert-rules.yaml' -o -path './infra/monitoring/alerts.yaml' -o -path './monitoring/alert-rules.yaml' 2>/dev/null | head -n 1 || true)"
if [[ -z "$alert_rule_found" ]]; then
  finish_check "$CHECK_NAME" "BLOCKED" "health endpoints were not verifiable and no alert rules file was found"
fi

finish_check "$CHECK_NAME" "PASS" "health endpoints returned 200 and alert rules were present"

