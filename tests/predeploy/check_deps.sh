#!/usr/bin/env bash

set -u -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/lib.sh"

REPO_ROOT="$(predeploy_repo_root)"
cd "$REPO_ROOT"

manifest=""
for candidate in pyproject.toml requirements.txt requirements-dev.txt requirements/*.txt poetry.lock package-lock.json npm-shrinkwrap.json; do
  if compgen -G "$candidate" >/dev/null 2>&1 || [[ -f "$candidate" ]]; then
    manifest="$candidate"
    break
  fi
done

output_file="$(mktemp)"
if command -v pip-audit >/dev/null 2>&1; then
  if predeploy_capture "$output_file" pip-audit; then
    status="PASS"
  else
    if grep -qiE 'connection|Name or service not known|Temporary failure in name resolution|pypi.org' "$output_file"; then
      status="BLOCKED"
    elif grep -qiE 'vulnerability|Known vulnerabilities' "$output_file"; then
      status="FAIL"
    else
      status="FAIL"
    fi
  fi
  command_text="pip-audit"
  tool_text="pip-audit"
else
  status="BLOCKED"
  command_text="pip-audit"
  tool_text="pip-audit"
  printf 'timestamp=%s\n' "$(predeploy_timestamp)"
  printf 'check=%s\n' "dependency-audit"
  printf 'status=%s\n' "$status"
  printf 'command=%s\n' "$command_text"
  printf 'detail=%s\n' "pip-audit is not installed."
  printf 'remediation=%s\n' "Install pip-audit and rerun the dependency audit."
  exit 1
fi

predeploy_print_result \
  "dependency-audit" \
  "$status" \
  "$command_text" \
  "Tool=${tool_text}; manifest=${manifest:-none}; repo has no lockfile/manifest, so the check audited the installed Python environment." \
  "If blocked by network, rerun in an environment with access to PyPI or use a vendored vulnerability database."
printf 'command_output_begin\n'
cat "$output_file"
printf 'command_output_end\n'
rm -f "$output_file"

[[ "$status" == "PASS" ]]

