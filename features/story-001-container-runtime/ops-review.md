# Ops Review: Container Runtime & Identity

> Phase 6d — Operational Readiness Review
> Story: STORY-001 — Container Runtime & Identity
> Date: 2026-03-26
> Reviewer: Ops Phase

---

## Summary

The design covers the core operational requirements. Three gaps need resolution before the system is production-ready: the health check endpoint is specified but not yet wired to ACI, secret rotation causes a container crash loop, and there is no alerting on startup failure.

---

## Findings

### Gap 1 — Health Check Endpoint Not Exposed to ACI

**Location:** Dockerfile `HEALTHCHECK`, feature-spec §6.1 (`src/health.ts`)

The Dockerfile defines:
```dockerfile
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:3978/api/health || exit 1
```

This is a Docker-native health check. ACI does **not** use Docker `HEALTHCHECK` directives — they are silently ignored. ACI's container restart behavior is governed by the `--restart-policy` flag (`OnFailure`) and the container process exit code only.

If the Node.js process is running but the HTTP server is deadlocked or the Key Vault fetch has failed silently (e.g., due to a network timeout that didn't throw), the container will appear healthy to ACI while being functionally dead.

**Mitigation:** The `HEALTHCHECK` in the Dockerfile is still useful for local `docker run` testing. For ACI, the only effective health signal is process exit. Ensure `src/index.ts` exits with a non-zero code on any fatal startup failure (already specified). For runtime health monitoring, rely on Application Insights availability tests (an external ping to `/api/health` on a schedule) as the liveness signal.

**Action:** Add an App Insights availability test (URL ping to `https://{ACI_FQDN}/api/health` every 5 minutes) to the provisioning script as Step 8b. This costs ~$0.01/month and provides the missing liveness check.

---

### Gap 2 — Secret Rotation Requires Manual Restart with No Runbook

**Location:** Analysis §3.2, feature-spec §3.4

The design correctly documents that ACI requires a container restart after secret rotation. However, there is no documented runbook for the rotation event, and the restart itself has an operational risk: if RBAC propagation for the new secret value is not complete, the restarted container enters a crash loop (`OnFailure` policy restarts it repeatedly, each time failing to fetch the updated secret with a transient 403).

Three secrets have distinct rotation timelines:
- `github-token` (PAT): **every 90 days** — the most operationally urgent
- `bot-app-password`: **every 2 years** — infrequent but catastrophic if missed
- `anthropic-api-key`: **no forced expiry** — but should be reviewed periodically

**Mitigation:**
1. Document a rotation runbook in the repo (can be a section in `provision-agent.sh` comments or a `RUNBOOK.md`).
2. The runbook should include: update secret in Key Vault → wait 30s → restart container → verify logs show successful secret fetch.
3. For `github-token`: create a calendar reminder or GitHub reminder 14 days before the 90-day expiry. The analysis recommends migrating to a GitHub App private key (no expiry) — track this as a v2 item in the backlog.
4. For `bot-app-password`: set a Key Vault expiry date alert (`az keyvault secret set --expires <date>`) so Azure Monitor fires a `SecretNearExpiry` event 60 days before expiry.

---

### Gap 3 — No Alert on Container Startup Failure

**Location:** feature-spec §4.2

Application Insights is provisioned, but no alerts are configured. If the container enters a crash loop after a redeploy (e.g., due to a bad image, failed secret fetch, or RBAC gap), the developer will only notice when a Teams message goes unanswered.

**Mitigation:** Add two metric alerts to the provisioning script:

```bash
# Alert on container restart count exceeding 3 in 10 minutes
az monitor metrics alert create \
  --name "aci-restart-alert-${AGENT_NAME}" \
  --resource-group "$RESOURCE_GROUP" \
  --scopes "$(az container show --name ${AGENT_NAME}-aci --resource-group $RESOURCE_GROUP --query id --output tsv)" \
  --condition "count 'RestartCount' > 3" \
  --window-size 10m \
  --evaluation-frequency 5m \
  --action "$ACTION_GROUP_ID"
```

For v1 with a solo developer, an email action group is sufficient. The alert creates the feedback loop: redeploy → crash loop → email → developer investigates logs.

---

### Gap 4 (Minor) — No Container Log Persistence Beyond Container Lifetime

**Location:** feature-spec §4.1

`az container logs` retains logs only for the lifetime of the container group. When the container group is deleted and recreated (required for image tag changes), all prior logs are lost. Application Insights captures structured telemetry events and exceptions, but raw stdout/stderr is not forwarded there.

**Mitigation:** Route ACI container logs to a Log Analytics workspace by deploying with the `--log-analytics-workspace` and `--log-analytics-workspace-key` flags. The Log Analytics workspace is already provisioned implicitly by Application Insights (they share a workspace). This makes all stdout/stderr queryable via KQL for 30 days (free tier retention).

This is a one-line addition to `az container create`:
```bash
--log-analytics-workspace "$LAW_WORKSPACE_ID" \
--log-analytics-workspace-key "$LAW_WORKSPACE_KEY" \
```

---

## Operational Readiness Assessment

| Area | Status | Notes |
|------|--------|-------|
| Container liveness | Partial | Docker `HEALTHCHECK` ignored by ACI; process exit is the only signal; App Insights availability test needed |
| Startup failure detection | Gap | No alert configured; failure is silent until user notices |
| Logging (App Insights) | Good | Structured telemetry, exception tracking, dependency tracking configured |
| Logging (raw stdout) | Partial | Ephemeral via `az container logs`; not forwarded to Log Analytics |
| Secret rotation | Partial | Rotation process possible but no runbook or expiry alerts configured |
| Container restart behavior | Good | `OnFailure` restart policy correctly specified; process exits on fatal error |
| Workspace persistence | Documented Gap | Ephemeral by design for v1; Azure Files mount deferred to STORY-003 |
| Scaling path | Clear | Add a new ACI container group per agent; no shared infrastructure to resize |
| Disaster recovery | Good | Full reprovisioning from `provision-agent.sh` restores all Azure resources; secrets must be re-provided by operator |
| Redeploy process | Documented | §2.3 covers image rebuild + restart; RBAC re-grant after group recreation |

---

## Recommendation

**Proceed to Phase 7.** Gaps 1-4 are Phase 8 implementation items. Gap 3 (no alert on crash loop) is the highest-priority ops item — a silent crash loop could block developer work for hours. Gap 1 (App Insights availability test) and Gap 4 (Log Analytics forwarding) are low-effort additions to the provisioning script in Phase 8.

| ID | Severity | Action | Phase |
|----|----------|--------|-------|
| G1 | Medium | Add App Insights availability test (URL ping to `/api/health`) to provisioning script | Phase 8 |
| G2 | Medium | Write rotation runbook; add Key Vault expiry alerts for `bot-app-password` and `github-token` | Phase 8 |
| G3 | Medium | Add ACI restart metric alert to provisioning script | Phase 8 |
| G4 | Low | Add `--log-analytics-workspace` flags to `az container create` | Phase 8 |
