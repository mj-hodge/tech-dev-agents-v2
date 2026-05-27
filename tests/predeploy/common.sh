#!/usr/bin/env bash

set -u
set -o pipefail

predeploy_now() {
  date -u +%Y-%m-%dT%H:%M:%SZ
}

predeploy_script_dir() {
  cd "$(dirname "${BASH_SOURCE[0]}")" && pwd
}

predeploy_has_tool() {
  command -v "$1" >/dev/null 2>&1
}

predeploy_print_banner() {
  local check_name="$1"
  printf '[%s] CHECK=%s START\n' "$(predeploy_now)" "$check_name"
}

predeploy_print_note() {
  local check_name="$1"
  shift
  printf '[%s] CHECK=%s NOTE %s\n' "$(predeploy_now)" "$check_name" "$*"
}

predeploy_print_command() {
  local check_name="$1"
  shift
  printf '[%s] CHECK=%s CMD %s\n' "$(predeploy_now)" "$check_name" "$*"
}

predeploy_run() {
  local check_name="$1"
  shift
  predeploy_print_command "$check_name" "$*"
  local output
  local rc
  set +e
  output="$("$@" 2>&1)"
  rc=$?
  set -e
  printf '%s\n' "$output"
  printf '[%s] CHECK=%s EXIT %s\n' "$(predeploy_now)" "$check_name" "$rc"
  return "$rc"
}

predeploy_finish() {
  local check_name="$1"
  local status="$2"
  local summary="$3"
  printf '[%s] CHECK=%s STATUS=%s SUMMARY=%s\n' "$(predeploy_now)" "$check_name" "$status" "$summary"
}

predeploy_blocked() {
  local check_name="$1"
  local summary="$2"
  predeploy_finish "$check_name" "BLOCKED" "$summary"
  return 2
}

predeploy_fail() {
  local check_name="$1"
  local summary="$2"
  predeploy_finish "$check_name" "FAIL" "$summary"
  return 1
}

predeploy_pass() {
  local check_name="$1"
  local summary="$2"
  predeploy_finish "$check_name" "PASS" "$summary"
  return 0
}
