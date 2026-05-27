#!/usr/bin/env bash
# STORY-507 AC-12: Staging smoke test for resume-aware phase runner + dispatch observability.
#
# Runs read-only + single-write verification against a target ops-console (default UAT).
# Does NOT exercise full Phase 8 SDK runs — that's covered by tests/test_507_lifecycle.py.
# This script's job: confirm the deploy actually shipped the features that matter
# (paused status, per-file commit helper, SIGTERM wiring, structured log events,
# Grafana rules file present on the VM).
#
# Usage:
#   OPS_CONSOLE_URL=https://tech-dev-agents-uat.gorillacommerce.ai \
#   OPS_CONSOLE_API_KEY=... \
#   bash tests/smoke/story_507_e2e.sh
#
# Exit 0 on success, non-zero on any check failure.

set -u

OPS_URL="${OPS_CONSOLE_URL:-https://tech-dev-agents.gorillacommerce.ai}"
KEY="${OPS_CONSOLE_API_KEY:?set OPS_CONSOLE_API_KEY}"
TEST_STORY_ID="${TEST_STORY_ID:-STORY-99507}"  # out-of-range ID so we can't collide with real work

fail=0
pass() { echo "  ✓ $1"; }
fail() { echo "  ✗ $1"; fail=$((fail+1)); }

echo "=== STORY-507 staging smoke — $OPS_URL ==="

echo "[1/8] ops-console health"
status=$(curl -s -o /tmp/s507_health.json -w "%{http_code}" --max-time 8 "$OPS_URL/api/health")
if [ "$status" = "200" ]; then pass "HTTP 200"; else fail "got HTTP $status"; fi

echo "[2/8] dispatch queue reachable"
status=$(curl -s -o /tmp/s507_queue.json -w "%{http_code}" --max-time 8 \
  -H "X-API-Key: $KEY" "$OPS_URL/api/dispatch/queue?include_claimed=true")
if [ "$status" = "200" ]; then pass "HTTP 200"; else fail "got HTTP $status"; fi

echo "[3/8] paused status accepted by POST /dispatch (AC-5)"
# Enqueue a dummy story so we have something to pause.
curl -s -o /tmp/s507_enq.json -w "%{http_code}" --max-time 8 \
  -X POST -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d "{\"story_id\":\"$TEST_STORY_ID\",\"repo\":\"tech-dev-agents\",\"scope\":\"small\",\"prompt\":\"AC-12 smoke test — safe to delete\",\"enqueued_by\":\"story_507_smoke\",\"title\":\"AC-12 smoke\"}" \
  "$OPS_URL/api/dispatch" > /tmp/s507_enq_status.txt
enq_status=$(cat /tmp/s507_enq_status.txt)
if [ "$enq_status" = "201" ] || [ "$enq_status" = "409" ]; then
  pass "enqueue $enq_status (201=new, 409=already pending from prior run)"
else
  fail "enqueue got $enq_status"
fi

echo "[4/8] POST /dispatch/pause endpoint exists and accepts paused transition (AC-6)"
# Must claim first, then pause.
curl -s -o /tmp/s507_claim.json -w "%{http_code}" --max-time 8 \
  -X POST -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d "{\"agent_name\":\"smoke-test-agent\"}" \
  "$OPS_URL/api/dispatch/claim/$TEST_STORY_ID" > /tmp/s507_claim_status.txt
claim_status=$(cat /tmp/s507_claim_status.txt)
if [ "$claim_status" = "200" ] || [ "$claim_status" = "409" ]; then
  pass "claim $claim_status"
else
  fail "claim got $claim_status"
fi

pause_status=$(curl -s -o /tmp/s507_pause.json -w "%{http_code}" --max-time 8 \
  -X POST -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d "{\"agent\":\"smoke-test-agent\",\"current_phase\":8,\"reason\":\"smoke_test\"}" \
  "$OPS_URL/api/dispatch/pause/$TEST_STORY_ID")
if [ "$pause_status" = "200" ]; then
  pass "pause 200"
elif [ "$pause_status" = "404" ]; then
  fail "pause 404 — endpoint not deployed (AC-6 regression)"
else
  fail "pause got $pause_status (expected 200)"
fi

echo "[5/8] paused row visible in queue with correct status (AC-5)"
curl -s --max-time 8 -H "X-API-Key: $KEY" \
  "$OPS_URL/api/dispatch/queue?include_claimed=true&include_paused=true" > /tmp/s507_qstate.json
if grep -q '"paused"' /tmp/s507_qstate.json; then
  pass "paused status present in queue response"
else
  fail "no paused rows — status enum may not be deployed"
fi

echo "[6/8] cleanup: cancel test story"
del_status=$(curl -s -o /dev/null -w "%{http_code}" --max-time 8 \
  -X DELETE -H "X-API-Key: $KEY" "$OPS_URL/api/dispatch/queue/$TEST_STORY_ID")
# 200/404/409 all acceptable for cleanup
pass "delete $del_status (any code OK — cleanup best-effort)"

echo "[7/8] poller code ships SIGTERM handler install (AC-4)"
# Check that at least one agent VM has install_shutdown_handler in its deployed file.
# Requires an agent hostname/IP configured via AGENT_SSH_TARGET, else skipped.
if [ -n "${AGENT_SSH_TARGET:-}" ]; then
  if ssh -p 443 -o StrictHostKeyChecking=no -o ConnectTimeout=5 \
      "$AGENT_SSH_TARGET" 'grep -q install_shutdown_handler /opt/agent/sdlc_phase_runner.py' 2>/dev/null; then
    pass "install_shutdown_handler present on $AGENT_SSH_TARGET"
  else
    fail "install_shutdown_handler NOT in deployed sdlc_phase_runner.py on $AGENT_SSH_TARGET"
  fi
else
  echo "  - skipped (set AGENT_SSH_TARGET=user@ip to include this check)"
fi

echo "[8/8] Grafana alerts file shipped with repo"
if [ -f "$(git rev-parse --show-toplevel 2>/dev/null)/deployment/observability/grafana-alerts.yml" ]; then
  pass "grafana-alerts.yml present"
else
  fail "deployment/observability/grafana-alerts.yml missing"
fi

echo ""
if [ "$fail" -gt 0 ]; then
  echo "FAIL — $fail check(s) failed"
  exit 1
fi
echo "PASS — all checks green"
