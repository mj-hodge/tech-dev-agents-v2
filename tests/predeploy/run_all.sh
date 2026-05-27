#!/usr/bin/env bash

set -u -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/lib.sh"

CHECKS=(
  check_cve.sh
  check_deps.sh
  check_secrets.sh
  check_drift.sh
  check_monitoring.sh
  check_logs.sh
  check_adapters.sh
  check_migrations.sh
  check_smoke.sh
  check_cicd.sh
)

LOG_DIR="${PREDEPLOY_LOG_DIR:-$(mktemp -d /tmp/story-005-predeploy.XXXXXX)}"
SUMMARY_FILE="$LOG_DIR/summary.txt"
mkdir -p "$LOG_DIR"

printf 'timestamp=%s\n' "$(predeploy_timestamp)"
printf 'log_dir=%s\n' "$LOG_DIR"
printf 'summary_file=%s\n' "$SUMMARY_FILE"
printf '\n| Check | Status | Exit | Log |\n'
printf '|-------|--------|------|-----|\n'

all_pass=true
for check in "${CHECKS[@]}"; do
  log_file="$LOG_DIR/${check%.sh}.log"
  if bash "$SCRIPT_DIR/$check" >"$log_file" 2>&1; then
    exit_code=0
  else
    exit_code=$?
  fi

  status="$(grep -E '^status=' "$log_file" | tail -n 1 | cut -d= -f2-)"
  [[ -z "$status" ]] && status="BLOCKED"
  printf '| %s | %s | %s | %s |\n' "$check" "$status" "$exit_code" "$log_file"
  printf '%s\t%s\t%s\t%s\n' "$check" "$status" "$exit_code" "$log_file" >>"$SUMMARY_FILE"

  if [[ "$status" != "PASS" ]]; then
    all_pass=false
  fi
done

printf '\nSummary written to: %s\n' "$SUMMARY_FILE"

if [[ "$all_pass" == true ]]; then
  exit 0
fi

exit 1
