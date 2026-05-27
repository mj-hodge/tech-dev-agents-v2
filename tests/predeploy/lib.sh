#!/usr/bin/env bash

set -u

predeploy_timestamp() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

predeploy_script_dir() {
  cd "$(dirname "${BASH_SOURCE[0]}")" && pwd
}

predeploy_repo_root() {
  cd "$(predeploy_script_dir)/../.." && pwd
}

predeploy_print_result() {
  local check="$1"
  local status="$2"
  local command="$3"
  local detail="$4"
  local remediation="$5"

  printf 'timestamp=%s\n' "$(predeploy_timestamp)"
  printf 'check=%s\n' "$check"
  printf 'status=%s\n' "$status"
  printf 'command=%s\n' "$command"
  printf 'detail=%s\n' "$detail"
  printf 'remediation=%s\n' "$remediation"
}

predeploy_missing() {
  local name="$1"
  command -v "$name" >/dev/null 2>&1
}

predeploy_capture() {
  local outfile="$1"
  shift
  "$@" >"$outfile" 2>&1
  local rc=$?
  return "$rc"
}
