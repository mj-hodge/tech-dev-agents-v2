#!/usr/bin/env bash

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=tests/predeploy/common.sh
source "${script_dir}/common.sh"

check_name="Container Image CVE Scan"
predeploy_print_banner "$check_name"

if ! predeploy_has_tool trivy && ! predeploy_has_tool grype; then
  predeploy_blocked "$check_name" "No CVE scanner installed (trivy/grype missing). Install one of them and rerun."
fi

image_ref="${CONTAINER_IMAGE:-${IMAGE_NAME:-}}"
image_tag="${IMAGE_TAG:-}"
if [[ -z "$image_ref" || -z "$image_tag" ]]; then
  predeploy_blocked "$check_name" "Missing CONTAINER_IMAGE/IMAGE_NAME or IMAGE_TAG. Set the release image reference before scanning."
fi

if predeploy_has_tool trivy; then
  if predeploy_run "$check_name" trivy image --exit-code 1 --severity CRITICAL,HIGH "${image_ref}:${image_tag}"; then
    predeploy_pass "$check_name" "Trivy completed with no CRITICAL/HIGH findings."
  else
    rc=$?
    if [[ $rc -eq 1 ]]; then
      predeploy_fail "$check_name" "Trivy reported CRITICAL/HIGH vulnerabilities."
    else
      predeploy_blocked "$check_name" "Trivy could not complete successfully. Inspect the command output above."
    fi
  fi
else
  if predeploy_run "$check_name" grype "${image_ref}:${image_tag}" --fail-on high; then
    predeploy_pass "$check_name" "Grype completed with no HIGH findings."
  else
    rc=$?
    if [[ $rc -eq 1 ]]; then
      predeploy_fail "$check_name" "Grype reported HIGH or CRITICAL vulnerabilities."
    else
      predeploy_blocked "$check_name" "Grype could not complete successfully. Inspect the command output above."
    fi
  fi
fi
