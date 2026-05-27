#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

CHECK_NAME="2/DEPENDENCY_AUDIT"
ROOT_DIR="$(cd -- "$SCRIPT_DIR/../.." && pwd)"

cd "$ROOT_DIR"

if command_exists pip-audit; then
  tmp_output="$(mktemp)"
  set +e
  pip-audit -f json >"$tmp_output" 2>&1
  rc=$?
  set -e

  if [[ $rc -eq 0 ]]; then
    finish_check "$CHECK_NAME" "PASS" "pip-audit completed with no known vulnerabilities"
  fi

  if [[ $rc -eq 1 ]]; then
    if rg -qi 'ConnectionError|Max retries exceeded|Temporary failure in name resolution|Name or service not known|Failed to establish a new connection' "$tmp_output"; then
      finish_check "$CHECK_NAME" "BLOCKED" "pip-audit could not reach the vulnerability service: $(tr '\n' ' ' <"$tmp_output" | sed 's/[[:space:]]\+/ /g')"
    fi
    vuln_count="$(python3 - "$tmp_output" <<'PY'
import json, sys
path = sys.argv[1]
try:
    data = json.load(open(path, "r", encoding="utf-8"))
except Exception:
    print("unknown")
    raise SystemExit(0)
print(len(data.get("dependencies", [])))
PY
)"
    finish_check "$CHECK_NAME" "FAIL" "pip-audit reported vulnerabilities (dependency groups: $vuln_count); raw output: $(tr '\n' ' ' <"$tmp_output" | sed 's/[[:space:]]\+/ /g')"
  fi

  finish_check "$CHECK_NAME" "BLOCKED" "pip-audit exited with code $rc; raw output: $(tr '\n' ' ' <"$tmp_output" | sed 's/[[:space:]]\+/ /g')"
fi

if command_exists python3; then
  tmp_output="$(mktemp)"
  set +e
  python3 -m pip check >"$tmp_output" 2>&1
  rc=$?
  set -e
  if [[ $rc -eq 0 ]]; then
    finish_check "$CHECK_NAME" "BLOCKED" "pip-audit is unavailable; pip check passed as an environment integrity fallback: $(tr '\n' ' ' <"$tmp_output" | sed 's/[[:space:]]\+/ /g')"
  fi
  finish_check "$CHECK_NAME" "BLOCKED" "pip-audit is unavailable and pip check failed: $(tr '\n' ' ' <"$tmp_output" | sed 's/[[:space:]]\+/ /g')"
fi

finish_check "$CHECK_NAME" "BLOCKED" "no dependency audit tool is available"
