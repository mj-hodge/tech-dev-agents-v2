#!/usr/bin/env bash

set -u -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/lib.sh"

REPO_ROOT="$(predeploy_repo_root)"
cd "$REPO_ROOT"

if [[ -z "${IMAGE_TAG:-}" ]]; then
  predeploy_print_result \
    "container-cve-scan" \
    "BLOCKED" \
    "trivy image --exit-code 1 --severity CRITICAL,HIGH <image>:<tag>" \
    "IMAGE_TAG is not set, so there is no release image to scan. Required scanners are also unavailable in this sandbox (trivy/grype missing)." \
    "Build and tag the release image, export IMAGE_TAG, install trivy or grype, and rerun."
  exit 1
fi

scanner=""
if predeploy_missing trivy; then
  scanner="trivy"
elif predeploy_missing grype; then
  scanner="grype"
else
  predeploy_print_result \
    "container-cve-scan" \
    "BLOCKED" \
    "trivy image --exit-code 1 --severity CRITICAL,HIGH <image>:<tag>" \
    "No supported image scanner is installed (trivy or grype)." \
    "Install trivy or grype, then rerun against IMAGE_TAG=${IMAGE_TAG}."
  exit 1
fi

output_file="$(mktemp)"
if [[ "$scanner" == "trivy" ]]; then
  if predeploy_capture "$output_file" trivy image --exit-code 1 --severity CRITICAL,HIGH "$IMAGE_TAG"; then
    status="PASS"
  else
    rc=$?
    if grep -qiE 'permission denied|cannot connect|no such file|daemon|not permitted' "$output_file"; then
      status="BLOCKED"
    else
      status="FAIL"
    fi
  fi
  command_text="trivy image --exit-code 1 --severity CRITICAL,HIGH $IMAGE_TAG"
else
  if predeploy_capture "$output_file" grype "$IMAGE_TAG" --fail-on high; then
    status="PASS"
  else
    rc=$?
    if grep -qiE 'permission denied|cannot connect|no such file|daemon|not permitted' "$output_file"; then
      status="BLOCKED"
    else
      status="FAIL"
    fi
  fi
  command_text="grype $IMAGE_TAG --fail-on high"
fi

predeploy_print_result \
  "container-cve-scan" \
  "$status" \
  "$command_text" \
  "Scanner=${scanner}; image=${IMAGE_TAG}" \
  "If blocked, install scanner and ensure the image is built/tagged and accessible to the daemon."
printf 'command_output_begin\n'
cat "$output_file"
printf 'command_output_end\n'
rm -f "$output_file"

[[ "$status" == "PASS" ]]

