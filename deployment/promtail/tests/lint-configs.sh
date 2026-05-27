#!/usr/bin/env bash
# STORY-915: Promtail config lint + static validation
# Groups A (syntax), B (schema), C (mutation guard)
# Usage: ./lint-configs.sh [--check-syntax]
#   --check-syntax  also run promtail --check-syntax (requires promtail binary)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIGS_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

PASS=0
FAIL=0
SKIP=0

ok()   { echo "  ✓ $*"; (( PASS++ )) || true; }
fail() { echo "  ✗ $*"; (( FAIL++ )) || true; }
skip() { echo "  - $*  [SKIP]"; (( SKIP++ )) || true; }

# ---------------------------------------------------------------------------
# Group A: Promtail --check-syntax (requires binary)
# ---------------------------------------------------------------------------
if [[ "${1:-}" == "--check-syntax" ]]; then
  echo ""
  echo "=== Group A: Promtail syntax check ==="
  if ! command -v promtail &>/dev/null; then
    skip "promtail binary not found — install promtail to run syntax checks"
  else
    for config in \
      promtail-config-dan.yaml \
      promtail-config-derrick.yaml \
      promtail-config-daisy.yaml \
      promtail-config-devon.yaml \
      promtail-config-ops-console.yaml \
      promtail-config-morris.yaml \
      promtail-config-morris-vm.yaml
    do
      f="${CONFIGS_DIR}/${config}"
      if [[ ! -f "$f" ]]; then
        skip "${config}: file not found (optional)"
      elif promtail --check-syntax --config.file="$f" &>/dev/null; then
        ok "${config}: syntax OK"
      else
        fail "${config}: syntax error"
        promtail --check-syntax --config.file="$f" 2>&1 | sed 's/^/    /'
      fi
    done
  fi
fi

# ---------------------------------------------------------------------------
# Group B: YAML structure validation
# ---------------------------------------------------------------------------
echo ""
echo "=== Group B: YAML schema validation ==="

HERMES_CONFIGS=(
  "promtail-config-dan.yaml"
  "promtail-config-derrick.yaml"
  "promtail-config-daisy.yaml"
  "promtail-config-devon.yaml"
)

# Morris/ops_console may be one file (both projects on same VM) or separate files
MORRIS_FILE=""
for candidate in promtail-config-morris-vm.yaml promtail-config-morris.yaml promtail-config-ops-console.yaml; do
  if [[ -f "${CONFIGS_DIR}/${candidate}" ]]; then
    MORRIS_FILE="${CONFIGS_DIR}/${candidate}"
    break
  fi
done

MANAGER_CONFIGS=()
if [[ -n "$MORRIS_FILE" ]]; then
  MANAGER_CONFIGS=( "$(basename "${MORRIS_FILE}")" )
fi

ALL_CONFIGS=( "${HERMES_CONFIGS[@]}" "${MANAGER_CONFIGS[@]}" )

for config in "${ALL_CONFIGS[@]}"; do
  f="${CONFIGS_DIR}/${config}"
  if [[ ! -f "$f" ]]; then
    fail "B: ${config}: file not found"
    continue
  fi

  # B-01: required top-level keys
  for key in server positions clients scrape_configs; do
    if grep -q "^${key}:" "$f"; then
      ok "B-01 ${config}: has '${key}:'"
    else
      fail "B-01 ${config}: missing '${key}:'"
    fi
  done

  # B-02: project label
  if grep -q 'project:' "$f"; then
    ok "B-02 ${config}: has 'project:' label"
  else
    fail "B-02 ${config}: missing 'project:' label"
  fi

  # B-03: agent label
  if grep -q 'agent:' "$f"; then
    ok "B-03 ${config}: has 'agent:' label"
  else
    fail "B-03 ${config}: missing 'agent:' label"
  fi

  # B-04: service label
  if grep -q 'service:' "$f"; then
    ok "B-04 ${config}: has 'service:' label"
  else
    fail "B-04 ${config}: missing 'service:' label"
  fi

  # B-05: Loki push URL
  expected_url="https://grafana.gorillacommerce.ai/loki/api/v1/push"
  if grep -q "$expected_url" "$f"; then
    ok "B-05 ${config}: correct Loki push URL"
  else
    fail "B-05 ${config}: missing or wrong Loki push URL"
  fi
done

# Morris config check
if [[ -n "$MORRIS_FILE" ]]; then
  morris_name="$(basename "${MORRIS_FILE}")"
  for key in server positions clients scrape_configs; do
    if grep -q "^${key}:" "$MORRIS_FILE"; then
      ok "B-01 ${morris_name}: has '${key}:'"
    else
      fail "B-01 ${morris_name}: missing '${key}:'"
    fi
  done
  for label in project agent service; do
    if grep -q "${label}:" "$MORRIS_FILE"; then
      ok "B-0x ${morris_name}: has '${label}:' label"
    else
      fail "B-0x ${morris_name}: missing '${label}:' label"
    fi
  done
else
  fail "B: no morris config file found (expected promtail-config-morris.yaml or promtail-config-morris-vm.yaml)"
fi

# ---------------------------------------------------------------------------
# Group B-06: correct project label values
# ---------------------------------------------------------------------------
echo ""
echo "=== Group B-06: project label value checks ==="

for agent in dan derrick daisy devon; do
  f="${CONFIGS_DIR}/promtail-config-${agent}.yaml"
  [[ -f "$f" ]] || { fail "B-06 ${agent}: config not found"; continue; }
  if grep -q 'project: hermes' "$f"; then
    ok "B-06 ${agent}: project=hermes label present"
  else
    fail "B-06 ${agent}: project=hermes label missing"
  fi
done

# ops_console — may be in morris-vm config or a separate file
ops_f=""
for candidate in promtail-config-ops-console.yaml promtail-config-morris-vm.yaml promtail-config-morris.yaml; do
  if [[ -f "${CONFIGS_DIR}/${candidate}" ]] && grep -q 'project: ops_console' "${CONFIGS_DIR}/${candidate}"; then
    ops_f="${CONFIGS_DIR}/${candidate}"
    break
  fi
done
if [[ -n "$ops_f" ]]; then
  ok "B-06 ops-console: project=ops_console label present (in $(basename "${ops_f}"))"
else
  fail "B-06 ops-console: project=ops_console label missing in any config"
fi

# morris
if [[ -n "$MORRIS_FILE" ]]; then
  if grep -q 'project: morris' "$MORRIS_FILE"; then
    ok "B-06 morris: project=morris label present"
  else
    fail "B-06 morris: project=morris label missing"
  fi
fi

# ---------------------------------------------------------------------------
# Group B-07: severity pipeline present in at least one job per config
# ---------------------------------------------------------------------------
echo ""
echo "=== Group B-07: severity pipeline ==="

for config in "${ALL_CONFIGS[@]}"; do
  f="${CONFIGS_DIR}/${config}"
  [[ -f "$f" ]] || continue
  if grep -q 'severity' "$f"; then
    ok "B-07 ${config}: severity parsing present"
  else
    fail "B-07 ${config}: severity parsing missing"
  fi
done

if [[ -n "$MORRIS_FILE" ]]; then
  morris_name="$(basename "${MORRIS_FILE}")"
  if grep -q 'severity' "$MORRIS_FILE"; then
    ok "B-07 ${morris_name}: severity parsing present"
  else
    fail "B-07 ${morris_name}: severity parsing missing"
  fi
fi

# ---------------------------------------------------------------------------
# Group C: Job name preservation guard
# ---------------------------------------------------------------------------
echo ""
echo "=== Group C: Job name preservation guard ==="

PRESERVED_JOBS=(
  "job_name: hermes-combined"
  "job_name: claude-code-files"
  "job_name: hermes-logs"
  "job_name: dispatch-poller"
  "job_name: dispatch-poller-journal"
)

for agent in dan derrick; do
  f="${CONFIGS_DIR}/promtail-config-${agent}.yaml"
  [[ -f "$f" ]] || { fail "C: ${agent} config not found"; continue; }
  for job in "${PRESERVED_JOBS[@]}"; do
    if grep -q "$job" "$f"; then
      ok "C ${agent}: '${job}' still present"
    else
      fail "C ${agent}: '${job}' MISSING — original job was removed"
    fi
  done
done

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "=== Summary ==="
echo "  PASS: ${PASS}  FAIL: ${FAIL}  SKIP: ${SKIP}"
echo ""

if [[ $FAIL -gt 0 ]]; then
  echo "RESULT: FAIL (${FAIL} failures)"
  exit 1
else
  echo "RESULT: PASS"
  exit 0
fi
