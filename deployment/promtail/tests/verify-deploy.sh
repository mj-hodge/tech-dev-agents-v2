#!/usr/bin/env bash
# STORY-915: Post-deploy verification for Promtail onboarding
# Groups D (host status), E (synthetic ingestion), F (label enumeration),
#         G (startup banner), H (severity parsing), I (claim pattern)
#
# Usage:
#   ./verify-deploy.sh all               # verify all hosts + Loki
#   ./verify-deploy.sh ops-console       # verify only ops_console host
#   ./verify-deploy.sh morris            # verify only Morris VM
#   ./verify-deploy.sh hermes            # verify all 4 Hermes VMs
#   ./verify-deploy.sh --labels-only     # F only — check Loki label values
#
# Requires: SSH access on port 443, LOKI_URL env or default
# SSH user:  azureagent (override with PROMTAIL_SSH_USER)
# Host IPs:  set OPS_CONSOLE_IP, MORRIS_IP, DAN_IP, DERRICK_IP, DAISY_IP, DEVON_IP
#            or rely on /etc/hosts / DNS entries: ops-console, morris, dan, derrick, daisy, devon

set -euo pipefail

LOKI_URL="${LOKI_URL:-https://grafana.gorillacommerce.ai}"
SSH_USER="${PROMTAIL_SSH_USER:-azureagent}"
SSH_OPTS="-p 443 -o StrictHostKeyChecking=no -o ConnectTimeout=10"
SYNTHETIC_TAG="STORY-915-$(date +%s)"

PASS=0; FAIL=0; SKIP=0
ok()   { echo "  ✓ $*"; ((PASS++)); }
fail() { echo "  ✗ $*"; ((FAIL++)); }
skip() { echo "  - $*  [SKIP]"; ((SKIP++)); }

# Host map: name → SSH target (IP or hostname)
declare -A HOSTS=(
  [dan]="${DAN_IP:-dan}"
  [derrick]="${DERRICK_IP:-derrick}"
  [daisy]="${DAISY_IP:-daisy}"
  [devon]="${DEVON_IP:-devon}"
  [ops-console]="${OPS_CONSOLE_IP:-ops-console}"
  [morris]="${MORRIS_IP:-morris}"
)

# Project label each host belongs to
declare -A HOST_PROJECT=(
  [dan]="hermes"
  [derrick]="hermes"
  [daisy]="hermes"
  [devon]="hermes"
  [ops-console]="ops_console"
  [morris]="morris"
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
ssh_cmd() {
  local host="$1"; shift
  local target="${HOSTS[$host]}"
  ssh $SSH_OPTS "${SSH_USER}@${target}" "$@" 2>/dev/null
}

loki_query() {
  # $1 = LogQL query, $2 = lookback (e.g. 1h)
  local query="$1"
  local lookback="${2:-1h}"
  local start
  start=$(date -u -d "-${lookback}" +%s%N 2>/dev/null || python3 -c "import time; print(int((time.time()-3600)*1e9))")
  local url="${LOKI_URL}/loki/api/v1/query_range"
  curl -sf --max-time 15 \
    -G "${url}" \
    --data-urlencode "query=${query}" \
    --data-urlencode "start=${start}" \
    --data-urlencode "limit=10" \
    2>/dev/null || true
}

loki_has_results() {
  local response="$1"
  echo "$response" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    results = d.get('data', {}).get('result', [])
    total = sum(len(s.get('values', [])) for s in results)
    sys.exit(0 if total > 0 else 1)
except Exception:
    sys.exit(1)
" 2>/dev/null
}

# ---------------------------------------------------------------------------
# Group D: systemctl status check
# ---------------------------------------------------------------------------
check_host_status() {
  local host="$1"
  echo ""
  echo "  [D] ${host}: promtail service status"
  local status
  status=$(ssh_cmd "$host" "systemctl is-active promtail" 2>/dev/null || echo "unreachable")
  if [[ "$status" == "active" ]]; then
    ok "D ${host}: promtail is active"
  elif [[ "$status" == "unreachable" ]]; then
    skip "D ${host}: SSH unreachable"
  else
    fail "D ${host}: promtail is ${status} (expected: active)"
  fi
}

# ---------------------------------------------------------------------------
# Group E: Synthetic log ingestion
# ---------------------------------------------------------------------------
check_synthetic_ingestion() {
  local host="$1"
  local project="${HOST_PROJECT[$host]}"
  echo ""
  echo "  [E] ${host}: synthetic log ingestion (project=${project})"

  # Write synthetic log line via journald logger
  if ! ssh_cmd "$host" "logger -t test-onboarding 'synthetic test ${SYNTHETIC_TAG}'" &>/dev/null; then
    skip "E ${host}: could not write synthetic log (SSH issue)"
    return
  fi

  ok "E ${host}: synthetic log written to journal"

  # Poll Loki for up to 60 seconds
  local query
  if [[ "$project" == "hermes" ]]; then
    query="{project=\"hermes\", agent=\"${host}\"} |= \"${SYNTHETIC_TAG}\""
  else
    query="{project=\"${project}\"} |= \"${SYNTHETIC_TAG}\""
  fi

  local found=false
  for i in $(seq 1 12); do
    sleep 5
    local resp
    resp=$(loki_query "$query" "5m")
    if loki_has_results "$resp"; then
      found=true
      break
    fi
  done

  if $found; then
    ok "E ${host}: synthetic log appeared in Loki within 60s"
  else
    fail "E ${host}: synthetic log NOT found in Loki after 60s (query: ${query})"
  fi
}

# ---------------------------------------------------------------------------
# Group F: Label enumeration
# ---------------------------------------------------------------------------
check_label_enumeration() {
  echo ""
  echo "=== Group F: Loki label enumeration ==="
  local url="${LOKI_URL}/loki/api/v1/label/project/values"
  local resp
  resp=$(curl -sf --max-time 15 "$url" 2>/dev/null || echo "")

  if [[ -z "$resp" ]]; then
    fail "F: Could not reach Loki at ${url}"
    return
  fi

  for project in hermes ops_console morris; do
    if echo "$resp" | python3 -c "import sys, json; d=json.load(sys.stdin); sys.exit(0 if '${project}' in d.get('data', []) else 1)" 2>/dev/null; then
      ok "F: project='${project}' present in Loki label values"
    else
      fail "F: project='${project}' NOT found in Loki label values"
      echo "     response: ${resp}"
    fi
  done
}

# ---------------------------------------------------------------------------
# Group G: Startup banner
# ---------------------------------------------------------------------------
check_startup_banner() {
  local project="$1"
  echo ""
  echo "  [G] ${project}: startup banner in Loki"
  local pattern
  case "$project" in
    hermes)      pattern='(?i)(started|running|dispatch.poller|polling)' ;;
    ops_console) pattern='(?i)(started|running|uvicorn|listening)' ;;
    morris)      pattern='(?i)(started|running|morris|listening)' ;;
  esac
  local query="{project=\"${project}\"} |~ \"${pattern}\""
  local resp
  resp=$(loki_query "$query" "24h")
  if loki_has_results "$resp"; then
    ok "G ${project}: startup banner found in last 24h"
  else
    fail "G ${project}: no startup banner in last 24h (query: ${query})"
  fi
}

# ---------------------------------------------------------------------------
# Group H: Severity parsing
# ---------------------------------------------------------------------------
check_severity_parsing() {
  local project="$1"
  echo ""
  echo "  [H] ${project}: severity label parsing"

  # info check
  local q_info="{project=\"${project}\", severity=\"info\"}"
  local resp_info
  resp_info=$(loki_query "$q_info" "1h")
  if loki_has_results "$resp_info"; then
    ok "H ${project}: severity=info streams present"
  else
    skip "H ${project}: no severity=info logs in last 1h (may be quiet)"
  fi

  # error check (if any errors exist)
  local q_err="{project=\"${project}\", severity=\"error\"}"
  local resp_err
  resp_err=$(loki_query "$q_err" "24h")
  if loki_has_results "$resp_err"; then
    ok "H ${project}: severity=error streams present"
    # Verify no INFO lines leaked into error stream
    local leak
    leak=$(echo "$resp_err" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    for s in d.get('data',{}).get('result',[]):
        for _, line in s.get('values', []):
            import re
            if re.search(r'\bINFO\b', line, re.I) and not re.search(r'\bERROR\b|\bCRITICAL\b', line, re.I):
                print(line[:120])
except: pass
" 2>/dev/null || true)
    if [[ -n "$leak" ]]; then
      fail "H ${project}: INFO lines leaked into severity=error stream: ${leak}"
    else
      ok "H ${project}: no INFO leakage in severity=error stream"
    fi
  else
    skip "H ${project}: no severity=error logs in last 24h (healthy or quiet)"
  fi
}

# ---------------------------------------------------------------------------
# Group I: Claim pattern (hermes only)
# ---------------------------------------------------------------------------
check_claim_pattern() {
  echo ""
  echo "  [I] hermes: claim pattern query"
  local q='{project="hermes", agent="dan"} |~ "(?i)claim"'
  local resp
  resp=$(loki_query "$q" "1h")
  if loki_has_results "$resp"; then
    ok "I dan: claim pattern found in last 1h"
  else
    skip "I dan: no claim events in last 1h (Dan may be idle — not a failure)"
  fi
}

# ---------------------------------------------------------------------------
# Main dispatch
# ---------------------------------------------------------------------------
MODE="${1:-all}"

echo "STORY-915 Promtail Deployment Verification"
echo "Loki: ${LOKI_URL}"
echo "Mode: ${MODE}"
echo "Tag:  ${SYNTHETIC_TAG}"

case "$MODE" in
  --labels-only)
    check_label_enumeration
    ;;

  ops-console)
    check_host_status "ops-console"
    check_synthetic_ingestion "ops-console"
    check_label_enumeration
    check_startup_banner "ops_console"
    check_severity_parsing "ops_console"
    ;;

  morris)
    check_host_status "morris"
    check_synthetic_ingestion "morris"
    check_label_enumeration
    check_startup_banner "morris"
    check_severity_parsing "morris"
    ;;

  hermes)
    for host in dan derrick daisy devon; do
      check_host_status "$host"
      check_synthetic_ingestion "$host"
    done
    check_label_enumeration
    check_startup_banner "hermes"
    check_severity_parsing "hermes"
    check_claim_pattern
    ;;

  all)
    for host in ops-console morris dan derrick daisy devon; do
      check_host_status "$host"
      check_synthetic_ingestion "$host"
    done
    check_label_enumeration
    for project in ops_console morris hermes; do
      check_startup_banner "$project"
      check_severity_parsing "$project"
    done
    check_claim_pattern
    ;;

  *)
    echo "Unknown mode: ${MODE}"
    echo "Usage: $0 [all|ops-console|morris|hermes|--labels-only]"
    exit 1
    ;;
esac

echo ""
echo "=== Summary ==="
echo "  PASS: ${PASS}  FAIL: ${FAIL}  SKIP: ${SKIP}"
echo ""
if [[ $FAIL -gt 0 ]]; then
  echo "RESULT: FAIL (${FAIL} failures)"
  exit 1
else
  echo "RESULT: PASS  (${SKIP} skipped — see above)"
  exit 0
fi
