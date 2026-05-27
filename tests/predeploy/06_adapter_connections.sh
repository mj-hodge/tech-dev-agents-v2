#!/usr/bin/env bash

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=tests/predeploy/common.sh
source "${script_dir}/common.sh"

check_name="Adapter Connections"
predeploy_print_banner "$check_name"

if [[ -z "${DATABASE_URL:-}" ]]; then
  predeploy_blocked "$check_name" "DATABASE_URL is unset."
fi

if ! predeploy_has_tool python3; then
  predeploy_blocked "$check_name" "python3 is required for adapter connectivity checks."
fi

db_probe="$(python3 - <<'PY2' 2>&1
import os
from sqlalchemy import create_engine, text

engine = create_engine(os.environ["DATABASE_URL"])
with engine.connect() as conn:
    conn.execute(text("SELECT 1"))
print("DB: PASS")
PY2
)" || true

if [[ "$db_probe" == "DB: PASS" ]]; then
  predeploy_print_note "$check_name" "$db_probe"
else
  predeploy_print_note "$check_name" "$db_probe"
fi

redis_status="SKIPPED"
if [[ -n "${REDIS_URL:-}" ]]; then
  redis_status="$(python3 - <<'PY3' 2>&1
import os
import redis

r = redis.from_url(os.environ["REDIS_URL"])
r.ping()
print("REDIS: PASS")
PY3
)" || true
  predeploy_print_note "$check_name" "$redis_status"
else
  predeploy_print_note "$check_name" "REDIS_URL is unset"
fi

external_status="SKIPPED"
if [[ -n "${MONDAY_API_URL:-}" ]]; then
  external_status="$(curl -sS --max-time 5 "${MONDAY_API_URL%/}/health" >/dev/null && echo "EXTERNAL_API: PASS" || echo "EXTERNAL_API: FAIL")"
  predeploy_print_note "$check_name" "$external_status"
else
  predeploy_print_note "$check_name" "No external API probe configured."
fi

if [[ "$db_probe" == "DB: PASS" && ( -z "${REDIS_URL:-}" || "$redis_status" == "REDIS: PASS" ) && ( -z "${MONDAY_API_URL:-}" || "$external_status" == "EXTERNAL_API: PASS" ) ]]; then
  predeploy_pass "$check_name" "All configured adapters responded."
else
  predeploy_blocked "$check_name" "One or more adapter connection prerequisites were missing or failed."
fi
