#!/usr/bin/env bash

set -u -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/lib.sh"

REPO_ROOT="$(predeploy_repo_root)"
cd "$REPO_ROOT"

workflow_files=(.github/workflows/*.yml .github/workflows/*.yaml)
existing_workflows=()
for file in "${workflow_files[@]}"; do
  [[ -f "$file" ]] && existing_workflows+=("$file")
done

if [[ "${#existing_workflows[@]}" -eq 0 ]]; then
  predeploy_print_result \
    "cicd-gate-verification" \
    "BLOCKED" \
    "inspect .github/workflows for health/smoke/migration gates" \
    "No GitHub Actions workflow files are present in this repository snapshot." \
    "Add the CI/CD pipeline definitions or point this check at the correct repo path, then rerun."
  exit 1
fi

output_file="$(mktemp)"
{
  printf 'workflow_files:\n'
  printf '%s\n' "${existing_workflows[@]}"
  printf '\nhealth_gate_matches:\n'
  health_matches="$(grep -nEi 'health' "${existing_workflows[@]}" || true)"
  printf '%s\n' "$health_matches"
  printf '\nsmoke_gate_matches:\n'
  smoke_matches="$(grep -nEi 'smoke' "${existing_workflows[@]}" || true)"
  printf '%s\n' "$smoke_matches"
  printf '\nmigration_gate_matches:\n'
  migration_matches="$(grep -nEi 'alembic|migration' "${existing_workflows[@]}" || true)"
  printf '%s\n' "$migration_matches"
} >"$output_file"

if [[ -n "${health_matches:-}" && -n "${smoke_matches:-}" && -n "${migration_matches:-}" ]]; then
  status="PASS"
else
  status="FAIL"
fi

predeploy_print_result \
  "cicd-gate-verification" \
  "$status" \
  "inspect .github/workflows for health/smoke/migration gates" \
  "Workflow files=${existing_workflows[*]}" \
  "Add or enable the missing pipeline gates, then rerun."
printf 'command_output_begin\n'
cat "$output_file"
printf 'command_output_end\n'
rm -f "$output_file"

[[ "$status" == "PASS" ]]
