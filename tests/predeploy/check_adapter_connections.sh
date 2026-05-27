#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

CHECK_NAME="6/ADAPTER_CONNECTIONS"
ROOT_DIR="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT_DIR"

blocked=0
messages=()

if [[ -n "${DATABASE_URL:-}" ]]; then
  tmp_output="$(mktemp)"
  set +e
  python3 - <<'PY' >"$tmp_output" 2>&1
import os
from sqlalchemy import create_engine, text
engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
with engine.connect() as conn:
    conn.execute(text("SELECT 1"))
print("DATABASE: PASS")
PY
  rc=$?
  set -e
  if [[ $rc -eq 0 ]]; then
    log_line "$CHECK_NAME" "database connection passed"
  else
    messages+=("database connection failed: $(tr '\n' ' ' <"$tmp_output" | sed 's/[[:space:]]\+/ /g')")
    blocked=1
  fi
else
  messages+=("DATABASE_URL is not set")
  blocked=1
fi

if [[ -n "${REDIS_URL:-}" ]]; then
  tmp_output="$(mktemp)"
  set +e
  python3 - <<'PY' >"$tmp_output" 2>&1
import os
import redis
r = redis.from_url(os.environ["REDIS_URL"])
r.ping()
print("REDIS: PASS")
PY
  rc=$?
  set -e
  if [[ $rc -eq 0 ]]; then
    log_line "$CHECK_NAME" "redis connection passed"
  else
    messages+=("redis connection failed: $(tr '\n' ' ' <"$tmp_output" | sed 's/[[:space:]]\+/ /g')")
    blocked=1
  fi
else
  messages+=("REDIS_URL is not set")
  blocked=1
fi

tmp_output="$(mktemp)"
set +e
python3 - <<'PY' >"$tmp_output" 2>&1
import requests

resp = requests.post(
    "https://api.monday.com/v2",
    json={"query": "{ __typename }"},
    timeout=10,
)
print(f"MONDAY_API_HTTP={resp.status_code}")
PY
rc=$?
set -e
if [[ $rc -eq 0 ]]; then
  log_line "$CHECK_NAME" "Monday API reachable: $(tr '\n' ' ' <"$tmp_output" | sed 's/[[:space:]]\+/ /g')"
else
  messages+=("Monday API reachability failed: $(tr '\n' ' ' <"$tmp_output" | sed 's/[[:space:]]\+/ /g')")
  blocked=1
fi

if [[ $blocked -eq 0 ]]; then
  finish_check "$CHECK_NAME" "PASS" "all configured adapters were reachable"
fi

joined_messages=""
for message in "${messages[@]}"; do
  if [[ -z "$joined_messages" ]]; then
    joined_messages="$message"
  else
    joined_messages="$joined_messages; $message"
  fi
done

finish_check "$CHECK_NAME" "BLOCKED" "$joined_messages"
