#!/usr/bin/env bash

set -u -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/lib.sh"

REPO_ROOT="$(predeploy_repo_root)"
cd "$REPO_ROOT"

if ! command -v gitleaks >/dev/null 2>&1; then
  predeploy_print_result \
    "secrets-scan" \
    "BLOCKED" \
    "gitleaks detect --source . --exit-code 1" \
    "gitleaks is not installed in this environment." \
    "Install gitleaks or trufflehog, then rerun the secrets scan."
  exit 1
fi

output_file="$(mktemp)"
if predeploy_capture "$output_file" gitleaks detect --source . --exit-code 1; then
  status="PASS"
else
  status="FAIL"
fi

predeploy_print_result \
  "secrets-scan" \
  "$status" \
  "gitleaks detect --source . --exit-code 1" \
  "Scanned repository root for secrets." \
  "Rotate any exposed credentials, remove the secret from history if needed, and rerun."
printf 'command_output_begin\n'
cat "$output_file"
printf 'command_output_end\n'
rm -f "$output_file"

[[ "$status" == "PASS" ]]

