# Ops Review — STORY-304: Event-Driven Teams Presence

**Phase:** 6d — Ops Review
**Story:** STORY-304
**Date:** 2026-04-16
**Reviewer:** Ops Review Agent (Sonnet)
**Scope:** Medium

---

## Summary

STORY-304 replaces the 5-second `pgrep`-based presence polling loop with event-driven
HTTP pushes from the ops-console dispatch service to agent gateway VMs. The change
touches two deployment surfaces (ops-console VM and each agent gateway VM) and adds one
new internal endpoint. No new infrastructure is required.

**Overall verdict: APPROVED WITH CONDITIONS**

**Conditions:**
1. Deploy ops-console before agent gateway VMs (backward-compat order required).
2. Verify presence transitions in staging before promoting to production agent VMs.
3. Document `OPS_CONSOLE_API_KEY` rotation runbook in `FLEET-MAINTENANCE.md`.

---

## Areas Reviewed

### 1. Deployment Order

The ops-console `push_presence()` function fails silently on connection errors and 5xx
responses. This means the ops-console can be deployed first while old agent gateways
remain in service — failed pushes are logged at ERROR and dropped.

**Required deployment sequence:**

| Step | Action | Verification |
|------|--------|--------------|
| 1 | Deploy ops-console | Confirm dispatch routes land; check for import errors in logs |
| 2 | Deploy Dan's agent gateway | Verify Busy on claim, Available on complete in staging |
| 3 | Deploy Derrick's agent gateway | Same verification |
| 4 | Enable Morris heartbeat presence | Verify Busy/Available transitions match SDK activity |
| 5 | Monitor for 24h | No stale-Busy or stale-Available reports |

**Risk of wrong order:** If an agent gateway is deployed before ops-console, the new
endpoint is live but never called. No regressions. All orders are safe; the above order
is preferred to minimize the window where pushes fail silently.

---

### 2. No New Infrastructure

| Component | Status |
|-----------|--------|
| Redis / message bus | Not required |
| Azure Service Bus | Not required |
| New VM or container | Not required |
| New database table or column | Not required |
| New Azure Key Vault secret | Not required |
| New VNET security group rule | Not required (health server port already open) |

The `POST /internal/presence` endpoint runs on the same port as the existing Teams
webhook receiver. No firewall or NSG changes are needed.

---

### 3. Monitoring

New log lines to watch in Loki post-deploy:

| Log Pattern | Location | Meaning |
|-------------|----------|---------|
| `Pushed presence Busy to <agent>` | Ops-console | Successful push on claim |
| `Pushed presence Available to <agent>` | Ops-console | Successful push on complete/fail |
| `exhausted 3 retries` | Ops-console ERROR | Gateway unreachable — alert if > 5/hour |
| `[teams-m365] Presence set to` | Agent gateway | Graph API call succeeded |
| `presence push rejected 401` | Ops-console WARNING | API key mismatch — investigate immediately |

**Recommended alert:** Loki alert on `exhausted 3 retries` at rate > 5/hour. This indicates
a gateway is unreachable or consistently refusing connections. Presence becomes stale at
this point.

The absence of `5s poll` log lines after gateway deploy confirms the polling loop has been
successfully removed.

---

### 4. Rollback Procedure

Rollback is independent per surface:

**Ops-console rollback:**
Revert ops-console container to prior image. Presence pushes stop. Agent gateways revert
to stale polling behavior (if the old gateway image had `_presence_monitor_loop`) or to
no presence updates (if the new gateway image is in place). Either state is safe — presence
is cosmetic and does not affect dispatch queue correctness.

**Agent gateway rollback:**
Revert the agent gateway container to prior image. The new `/internal/presence` endpoint
disappears. Ops-console push calls will begin failing silently (3 retries + ERROR log).
Ops-console does not need to be rolled back.

**No database rollback required.** No schema changes were made.

---

### 5. Secret Management

`OPS_CONSOLE_API_KEY` is now used on one additional code path (`presence_endpoint.py`
validation). The secret is already present on all agent VMs and the ops-console — no new
injection is needed.

**Rotation procedure (required runbook item):**
1. Generate new key.
2. Update secret in Azure Key Vault.
3. Trigger rolling restart of ops-console VM (picks up new env var).
4. Trigger rolling restart of each agent gateway VM (picks up new env var).
5. Verify `/internal/presence` health check returns 200 with new key.
6. Verify dispatch presence pushes appear in Loki within 5 minutes of next claim.

Rotation cadence: 90 days, or immediately on personnel change with VM access.

---

### 6. Capacity and Performance

- Presence push is fire-and-forget from the dispatch route's perspective.
- Maximum 3 outbound HTTP calls per dispatch event (on retry path).
- Timeout: 5s per attempt.
- At current dispatch rate (~10 claims/day across 2 agents), presence push load is negligible.
- No queueing, no persistent state, no unbounded growth risk.

---

## Conditions Summary

| ID | Condition | Deadline | Owner |
|----|-----------|----------|-------|
| OPS-1 | Deploy ops-console before agent gateway VMs | Deploy day | Deployer |
| OPS-2 | Verify Busy/Available transitions in staging before prod agent gateway deploys | Before step 2 of rollout | Deployer |
| OPS-3 | Document `OPS_CONSOLE_API_KEY` rotation runbook in `FLEET-MAINTENANCE.md` | Within 90 days of go-live | Ops |

---

## Verdict

**APPROVED WITH CONDITIONS**

STORY-304 is operationally sound. The backward-compatible design, silent-fail push pattern,
and no-new-infrastructure constraint make this a low-risk deployment. The three conditions
above must be satisfied to ensure safe rollout and ongoing secret hygiene.
