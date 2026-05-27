#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

CHECK_NAME="3/SECRETS_SCAN"
ROOT_DIR="$(cd -- "$SCRIPT_DIR/../.." && pwd)"

cd "$ROOT_DIR"

if command_exists gitleaks; then
  tmp_output="$(mktemp)"
  set +e
  gitleaks detect --source . --exit-code 1 >"$tmp_output" 2>&1
  rc=$?
  set -e

  if [[ $rc -eq 0 ]]; then
    finish_check "$CHECK_NAME" "PASS" "gitleaks completed with no verified secrets"
  fi

  if [[ $rc -eq 1 ]]; then
    finish_check "$CHECK_NAME" "FAIL" "gitleaks reported secret findings: $(tr '\n' ' ' <"$tmp_output" | sed 's/[[:space:]]\+/ /g')"
  fi

  finish_check "$CHECK_NAME" "BLOCKED" "gitleaks exited with code $rc: $(tr '\n' ' ' <"$tmp_output" | sed 's/[[:space:]]\+/ /g')"
fi

if command_exists trufflehog; then
  tmp_output="$(mktemp)"
  set +e
  trufflehog filesystem . --only-verified --fail >"$tmp_output" 2>&1
  rc=$?
  set -e

  if [[ $rc -eq 0 ]]; then
    finish_check "$CHECK_NAME" "PASS" "trufflehog completed with no verified secrets"
  fi

  if [[ $rc -eq 1 ]]; then
    finish_check "$CHECK_NAME" "FAIL" "trufflehog reported secret findings: $(tr '\n' ' ' <"$tmp_output" | sed 's/[[:space:]]\+/ /g')"
  fi

  finish_check "$CHECK_NAME" "BLOCKED" "trufflehog exited with code $rc: $(tr '\n' ' ' <"$tmp_output" | sed 's/[[:space:]]\+/ /g')"
fi

tmp_output="$(mktemp)"
set +e
rg -n -I --hidden --glob '!.git' --glob '!**/__pycache__/**' \
  -e 'AKIA[0-9A-Z]{16}' \
  -e 'ASIA[0-9A-Z]{16}' \
  -e 'xox[baprs]-[A-Za-z0-9-]+' \
  -e '-----BEGIN (RSA|EC|OPENSSH) PRIVATE KEY-----' \
  -e 'ghp_[A-Za-z0-9]{36}' \
  -e 'sk-[A-Za-z0-9]{20,}' \
  . >"$tmp_output" 2>&1
rc=$?
set -e

if [[ $rc -eq 1 ]]; then
  finish_check "$CHECK_NAME" "BLOCKED" "official secret scanners are unavailable; fallback regex sweep found no obvious secret patterns"
fi

finish_check "$CHECK_NAME" "FAIL" "fallback regex sweep found potential secret patterns: $(tr '\n' ' ' <"$tmp_output" | sed 's/[[:space:]]\+/ /g')"

