# Phase 10 Site Reliability — STORY-002 Teams Bot Foundation

Date: 2026-03-31
Scope: Medium

---

## 1. Operational Readiness

This story delivers the **Teams bot contract** — a Python module defining intent classification, conversation reference storage, proactive messaging, routing, and acknowledgement generation. It does not deploy infrastructure.

### What This Story Ships

| Component | Type | Operational Impact |
|-----------|------|-------------------|
| `teams_bot.py` | Python library module | None (no runtime process) |
| `test_teams_bot.py` | Test suite (6 tests) | CI pipeline — runs in <0.03s |

### What This Story Specifies (for future deployment)

| Component | Specification Location | Deployed By |
|-----------|----------------------|-------------|
| Bot Framework handler | `feature-spec.md` §1 | STORY-006/007 integration |
| Messaging endpoint (`POST /api/messages`) | `feature-spec.md` §6 | Server setup in integration phase |
| User guard middleware | `feature-spec.md` §5 | Server setup in integration phase |
| Bot Messenger (proactive send) | `feature-spec.md` §4 | STORY-007 wiring |
| Key Vault secret (`allowed-user-oid`) | `feature-spec.md` §8 | `provision-agent.sh` update |

---

## 2. Ops Review Gap Resolution

The Phase 6d ops-review.md identified 11 findings. Status:

| Finding | Severity | Resolution | Status |
|---------|----------|-----------|--------|
| F1: Health probe reports botReady despite empty store | High | Requires server-level change in integration phase | Documented |
| F2: No startup degraded-state log | Medium | Requires server startup code | Documented |
| F3: Health check omits dependency status | Medium | Requires server-level health endpoint enhancement | Documented |
| F4: Health response cacheable | Low | Header change in integration phase | Documented |
| F5: Unstructured console logging | Medium | Applies to TypeScript implementation, not Python contract | Documented |
| F6: Telemetry cost with rawText events | Low | Applies to TypeScript implementation | Documented |
| F7: No turn latency telemetry | Low | Applies to TypeScript implementation | Documented |
| F8: withRetry re-throw can crash process | Medium | Applies to TypeScript `BotMessengerImpl` | Documented |
| F9: Silent approvalHandler replacement | Low | Applies to TypeScript `BotMessengerImpl` | Documented |
| F10: Multi-replica state unsafety | Medium | Provisioning constraint (replica=1) | Documented |
| F11: Retry budget too short for ACI cold start | Low | Configuration change at deployment | Documented |

**Assessment:** All 11 findings are correctly deferred — they apply to the TypeScript server layer or Azure infrastructure, not to the Python contract module shipped in this story. The specifications are complete and actionable for the integration phase.

---

## 3. Monitoring & Observability

### Current State (Library Module)

- **Test metrics:** 6 tests, 0.03s execution time, 100% pass rate
- **CI visibility:** Tests run in the full suite (114 tests total)
- **No runtime process:** This module is consumed by downstream stories

### Future State (After STORY-006/007 Integration)

Per feature-spec §6 and ops-review, the deployed bot will have:

| Signal | Source | Retention |
|--------|--------|-----------|
| Structured telemetry | Application Insights (`trackEvent`, `trackException`) | 90 days |
| Container stdout/stderr | ACI logs → Log Analytics | 30 days |
| Health endpoint | `/api/health` → App Insights availability test | Continuous |
| Turn latency | `turn_start`/`turn_end` events (ops-review F7) | 90 days |
| Intent distribution | `intent_*` telemetry events | 90 days |

---

## 4. Failure Modes

| Failure | Detection | Recovery |
|---------|-----------|----------|
| Intent misclassification | User sends correction; logged via `trackEvent` | Tighten regex rules or add LLM fallback (STORY-006) |
| Conversation store empty after restart | Health endpoint `conversationStorePopulated: false` | Developer sends one message to re-establish reference |
| Proactive send fails (transient) | `withRetry` logs attempts; `trackException` on exhaustion | Exponential backoff (3 attempts, 1s base) |
| Proactive send fails (no reference) | `trackEvent('proactive_send_skipped')` | Wait for developer to message bot |
| Approval handler replaced silently | `console.warn` on double-registration | Document single-pending-approval constraint |
| Unauthorized user messages bot | Silent drop by user guard | No action needed; security working as designed |

---

## 5. Capacity & Scaling

Per feature-spec and ops-review:
- **Deployment model:** Single ACI container, replica count = 1 (ops-review F10)
- **User model:** Single developer user for v1
- **Conversation model:** One active conversation thread
- **Message throughput:** Well within Teams rate limits (5 msg/s per conversation)
- **Memory footprint:** `MemoryConversationStore` holds ~1 reference (~200 bytes)

---

## 6. Runbook Summary

### Bot Registration Update

1. Register bot in Azure portal → Bot Services
2. Set messaging endpoint: `https://{ACI_DNS_LABEL}.{LOCATION}.azurecontainer.io/api/messages`
3. Generate app password and store in Key Vault as `bot-app-password`
4. Note the app ID and store as `bot-app-id`

### Allowed User Configuration

1. Find developer's Entra ID object ID: Azure Portal → Entra ID → Users → Properties → Object ID
2. `az keyvault secret set --vault-name $KV --name allowed-user-oid --value $OID`
3. Restart container to pick up new secret

### Conversation Reference Recovery (After Restart)

1. Container restarts → `MemoryConversationStore` is empty
2. Proactive messaging is unavailable until developer sends a message
3. Developer sends any message in Teams → reference re-established
4. Proactive messaging resumes

### Troubleshooting: Bot Not Responding

1. Check ACI health: `az container show --name $ACI --resource-group $RG --query instanceView.state`
2. Check logs: `az container logs --name $ACI --resource-group $RG --tail 20`
3. Verify Bot Service endpoint matches ACI DNS label
4. Verify `bot-app-id` and `bot-app-password` in Key Vault match Azure Bot registration
5. Test with Bot Framework Emulator using app ID + password

---

## Verdict

APPROVED — No operational blockers. All ops findings are documented with actionable remediation paths deferred to the integration phase. The Python contract module itself has no runtime operational surface.
