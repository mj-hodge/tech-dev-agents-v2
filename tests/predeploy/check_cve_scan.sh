#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

CHECK_NAME="1/CVE_SCAN"

if ! command_exists trivy; then
  finish_check "$CHECK_NAME" "BLOCKED" "trivy is not installed in this environment"
fi

if [[ -z "${IMAGE_TAG:-}" ]]; then
  finish_check "$CHECK_NAME" "BLOCKED" "IMAGE_TAG is not set"
fi

IMAGE_REF="${IMAGE_REF:-${IMAGE_NAME:-}}"
if [[ -z "$IMAGE_REF" ]]; then
  finish_check "$CHECK_NAME" "BLOCKED" "IMAGE_REF/IMAGE_NAME is not set, so no container image can be scanned"
fi

if ! command_exists docker; then
  finish_check "$CHECK_NAME" "BLOCKED" "docker is not installed, so local image existence cannot be verified"
fi

if ! docker image inspect "$IMAGE_REF" >/dev/null 2>&1; then
  finish_check "$CHECK_NAME" "BLOCKED" "image '$IMAGE_REF' is not available locally"
fi

tmp_output="$(mktemp)"
set +e
trivy image --exit-code 1 --severity CRITICAL,HIGH "$IMAGE_REF" >"$tmp_output" 2>&1
rc=$?
set -e

if [[ $rc -eq 0 ]]; then
  finish_check "$CHECK_NAME" "PASS" "trivy completed with no CRITICAL or HIGH findings for '$IMAGE_REF'"
fi

if [[ $rc -eq 1 ]]; then
  finish_check "$CHECK_NAME" "FAIL" "trivy reported CRITICAL/HIGH findings for '$IMAGE_REF': $(tr '\n' ' ' <"$tmp_output" | sed 's/[[:space:]]\+/ /g')"
fi

finish_check "$CHECK_NAME" "BLOCKED" "trivy exited with code $rc: $(tr '\n' ' ' <"$tmp_output" | sed 's/[[:space:]]\+/ /g')"

