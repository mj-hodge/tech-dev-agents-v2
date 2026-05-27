# Phase 10 Site Reliability — STORY-001 Container Runtime & Identity

Date: 2026-03-31
Scope: Medium

---

## 1. Operational Readiness

This story delivers the **runtime identity contract** — a Python module defining the container security context, secret loading interface, health check contract, and log-safe metadata builder. It does not deploy infrastructure.

### What This Story Ships

| Component | Type | Operational Impact |
|-----------|------|-------------------|
| `runtime_identity.py` | Python library module | None (no runtime process) |
| `test_runtime_identity.py` | Test suite (15 tests) | CI pipeline — runs in <0.1s |
| `conftest.py` | Pytest configuration | Registers `@pytest.mark.smoke` |

### What This Story Specifies (for future deployment)

| Component | Specification Location | Deployed By |
|-----------|----------------------|-------------|
| Dockerfile | `feature-spec.md` §1.1 | Integration phase |
| `provision-agent.sh` | `feature-spec.md` §2.1 | Manual operator run |
| ACI container group | `feature-spec.md` §5 | `provision-agent.sh` |
| Key Vault + RBAC | `feature-spec.md` §3 | `provision-agent.sh` |

---

## 2. Ops Review Gap Resolution

The Phase 6d ops-review.md identified 4 gaps. Status:

| Gap | Severity | Resolution | Status |
|-----|----------|-----------|--------|
| G1: ACI ignores Docker HEALTHCHECK | Medium | App Insights availability test specified in ops-review. Implementation deferred to provisioning execution. | Documented |
| G2: Secret rotation needs runbook | Medium | Rotation timelines documented in feature-spec §3.4. Runbook deferred to provisioning execution. | Documented |
| G3: No alert on container crash loop | Medium | ACI restart metric alert specified in ops-review. Implementation deferred to provisioning execution. | Documented |
| G4: No container log persistence | Low | Log Analytics workspace flags specified in ops-review. Implementation deferred to provisioning execution. | Documented |

**Assessment:** All 4 gaps are correctly deferred — they require Azure infrastructure that doesn't exist until `provision-agent.sh` runs. The specifications are complete and actionable. No ops gaps remain in the code artifacts.

---

## 3. Monitoring & Observability

### Current State (Library Module)

- **Test metrics:** 15 tests, 0.10s execution time, 100% pass rate
- **CI visibility:** Tests run in the full suite (114 tests total)
- **Smoke test:** 1 smoke-marked test for pre-deploy gate validation

### Future State (After Deployment)

Per feature-spec §4, the deployed container will have:

| Signal | Source | Retention |
|--------|--------|-----------|
| Structured telemetry | Application Insights | 90 days (default) |
| Container stdout/stderr | ACI logs → Log Analytics | 30 days (free tier) |
| Health endpoint | `/api/health` → App Insights availability test | Continuous |
| Crash loop detection | ACI restart count metric alert | Real-time |

---

## 4. Failure Modes

| Failure | Detection | Recovery |
|---------|-----------|----------|
| Secret provider returns None/blank | `MissingSecretError` raised at startup | Container exits(1) → ACI `OnFailure` restarts |
| Secret provider throws | `SecretLoadError` wraps provider exception | Container exits(1) → ACI `OnFailure` restarts |
| RBAC not propagated after provisioning | Key Vault 403 → `SecretLoadError` | Wait 30s, restart container |
| Key Vault unreachable | Network timeout → `SecretLoadError` | ACI restart; check NSG/DNS |
| Secret rotated but container not restarted | Stale credential → downstream auth failure | Restart container to re-fetch |

---

## 5. Capacity & Scaling

Per feature-spec §5:
- **Per agent:** 1 vCPU, 2 GB RAM (ACI sizing)
- **Scaling model:** 1 ACI container group per agent instance
- **No shared state:** Each agent has its own Key Vault secrets and identity
- **Cost:** ~$0.04/hour per agent (ACI consumption billing)

---

## 6. Runbook Summary

### Secret Rotation (github-token — every 90 days)

1. Generate new GitHub PAT/App key
2. `az keyvault secret set --vault-name $KV --name github-token --value $NEW_TOKEN`
3. Wait 30s for propagation
4. `az container restart --name $ACI --resource-group $RG`
5. Verify: `az container logs --name $ACI --resource-group $RG --tail 10` — look for `[secrets] All secrets loaded`

### Secret Rotation (bot-app-password — every 2 years)

1. `az ad app credential reset --id $APP_ID --years 2`
2. `az keyvault secret set --vault-name $KV --name bot-app-password --value $NEW_PASSWORD`
3. Wait 30s → restart container → verify logs

### Container Image Update

1. `az acr build --registry $ACR --image agent-dev:latest .`
2. `az container restart --name $ACI --resource-group $RG`
3. Verify health endpoint responds

---

## Verdict

APPROVED — No operational blockers. All ops gaps are documented with actionable remediation paths deferred to provisioning execution. The library module itself has no runtime operational surface.
