#!/usr/bin/env bash

set -u -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/lib.sh"

REPO_ROOT="$(predeploy_repo_root)"
cd "$REPO_ROOT"

if [[ -z "${DATABASE_URL:-}" ]]; then
  predeploy_print_result \
    "adapter-connections" \
    "BLOCKED" \
    "database/cache/external adapter probes" \
    "DATABASE_URL is not set, and no application-specific adapter targets are configured in this sandbox." \
    "Export DATABASE_URL (and REDIS_URL if applicable), then rerun the adapter checks against a reachable environment."
  exit 1
fi

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

status="PASS"
db_out="$tmpdir/db.out"
if ! python3 - <<'PY' >"$db_out" 2>&1; then
from sqlalchemy import create_engine, text
import os

engine = create_engine(os.environ["DATABASE_URL"])
with engine.connect() as conn:
    conn.execute(text("SELECT 1"))
print("DB: PASS")
PY
  status="FAIL"
fi

if [[ -n "${REDIS_URL:-}" ]]; then
  redis_out="$tmpdir/redis.out"
  if ! python3 - <<'PY' >"$redis_out" 2>&1; then
import os
import redis

r = redis.from_url(os.environ["REDIS_URL"])
r.ping()
print("Redis: PASS")
PY
    status="FAIL"
  fi
else
  redis_out="$tmpdir/redis.out"
  printf 'Redis URL not set; skipped.\n' >"$redis_out"
fi

predeploy_print_result \
  "adapter-connections" \
  "$status" \
  "python3 connectivity probes for DATABASE_URL and REDIS_URL" \
  "DATABASE_URL=${DATABASE_URL}; REDIS_URL=${REDIS_URL:-unset}" \
  "If any adapter fails, verify credentials, DNS/network reachability, and service health before re-running."
printf 'db_probe_output_begin\n'
cat "$db_out"
printf 'db_probe_output_end\n'
printf 'redis_probe_output_begin\n'
cat "$redis_out"
printf 'redis_probe_output_end\n'

[[ "$status" == "PASS" ]]

