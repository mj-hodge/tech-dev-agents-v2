# UX Review: Container Runtime & Identity

> Phase 6c — Developer Experience Review
> Story: STORY-001 — Container Runtime & Identity
> Date: 2026-03-26
> Reviewer: UX Phase

---

## Summary

The provisioning script is well-structured for a solo developer who reads it top to bottom. Three friction points will cause real pain on first run. Container startup UX is acceptable. Debugging experience is good.

---

## Findings

### Friction Point 1 — RBAC Propagation `sleep 30` Is Invisible and Often Wrong

**Location:** `provision-agent.sh` Step 3

```bash
echo "    Waiting 30s for RBAC propagation..."
sleep 30
```

The script sleeps 30 seconds after granting the current user `Key Vault Secrets Officer` and then immediately writes secrets. Azure RBAC propagation is eventually consistent and can take 30 seconds to several minutes depending on load. A developer running this script for the first time will hit `(Forbidden) Caller is not authorized to perform action on resource` errors roughly 20% of the time, with no explanation of why.

There is a second, more dangerous RBAC gap: after Step 5 creates the container and grants the managed identity `Key Vault Secrets User`, the script instructs the developer to "wait ~60s for RBAC propagation" in the terminal output summary — but the container has already started and is already failing. The developer must manually restart it.

**Recommendation:** Replace the fixed `sleep 30` with a polling loop that retries the first `az keyvault secret set` with backoff until it succeeds (max 3 minutes). Add a visible warning banner before the "Next steps" output:

```
  IMPORTANT: The container started before RBAC propagated to the managed
  identity. You MUST restart it after ~60s:
    az container restart --name ${AGENT_NAME}-aci --resource-group $RESOURCE_GROUP
```

---

### Friction Point 2 — No Pre-Flight Validation

**Location:** `provision-agent.sh` top of script, before Step 1

The script will fail mid-run if:
- `az` CLI is not installed or not logged in
- `jq` is not installed (used for JSON parsing)
- `ANTHROPIC_API_KEY` or `GITHUB_TOKEN` are not set (caught by `:?` — this one is handled)
- `ACR_NAME` conflicts with an existing globally-unique ACR in another subscription

A mid-run failure leaves the developer with partially provisioned resources — resource group exists, app registration exists, Key Vault does not — making cleanup and retry confusing.

**Recommendation:** Add a `preflight_check()` function at the top of the script:

```bash
preflight_check() {
  echo ">>> Pre-flight checks..."
  command -v az   >/dev/null || { echo "ERROR: az CLI not found"; exit 1; }
  command -v jq   >/dev/null || { echo "ERROR: jq not found (brew install jq / apt install jq)"; exit 1; }
  az account show >/dev/null 2>&1 || { echo "ERROR: Not logged in to Azure (run: az login)"; exit 1; }
  echo "    OK"
}
preflight_check
```

---

### Friction Point 3 — ACR Name Collision Is Opaque

**Location:** `provision-agent.sh` Step 4

The derived ACR name (`acr${AGENT_NAME//[-]/}`) must be globally unique across all Azure customers. If it collides, `az acr create` fails with a generic conflict error. The developer must change `AGENT_NAME` (which cascades to all other resource names) or override `ACR_NAME` separately.

The current CONFIGURATION block does not expose `ACR_NAME` as a top-level variable with a comment explaining the uniqueness requirement.

**Recommendation:** Hoist `ACR_NAME` to the CONFIGURATION block with an explicit comment:

```bash
ACR_NAME="acr${AGENT_NAME//[-]/}$(openssl rand -hex 3)"  # globally unique; change if collision
# OR let the developer override:
# ACR_NAME="mycompanyagentdev"
```

Alternatively, append a short random suffix automatically (6 hex chars from `openssl rand`) so collisions are astronomically unlikely.

---

### Friction Point 4 (Minor) — Container Start Time Creates Silent Delay for First Teams Message

**Location:** Analysis §3.1

The spec recommends running the container 24/7, which eliminates cold start UX issues. However, the "Next steps" instructions (§2.1 end of script) say to test by messaging the bot in Teams immediately after provisioning. The container may still be in the RBAC propagation gap (failing to fetch secrets), presenting the user with a silent timeout in Teams rather than a success message.

**Recommendation:** Add a log-check step to the "Next steps" output:

```
  3. Verify startup: az container logs --name ${AGENT_NAME}-aci \
       --resource-group $RESOURCE_GROUP --tail 20
     Look for: "[secrets] All secrets loaded in XXXms"
     If you see "[startup] Fatal error" — restart after 60s and check again
```

---

### What Works Well

- **Configuration block at top of script:** All per-agent values are in one place. No hunting through the script to find what to change for a second agent. This is the right pattern.
- **Step-numbered echo statements:** `>>> [1/8] Creating resource group...` gives clear progress indication. The developer knows where they are and how much is left.
- **Secrets injected via env vars, not in script:** `ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:?...}"` pattern fails fast and never bakes secrets into the file.
- **`az container logs --follow` documented:** The debugging path is obvious. No digging through Azure Portal to find logs.
- **Teardown script provided:** Reduces fear of "what if I mess this up" — a full teardown path exists.
- **Local dev fallback in Appendix B:** The `AzureCliCredential` fallback makes the development loop viable without requiring a running ACI.

---

## Recommendation

**Proceed to Phase 7** with two minor spec additions: (1) add the pre-flight check pattern to the provisioning script spec, (2) revise the "Next steps" block to include the log verification step. Friction Points 1-3 are all Phase 8 implementation concerns within `provision-agent.sh`.

| ID | Severity | Action | Phase |
|----|----------|--------|-------|
| FP1 | Medium | Replace `sleep 30` with retry loop; add restart warning to summary | Phase 8 |
| FP2 | Medium | Add `preflight_check()` function | Phase 8 |
| FP3 | Low | Hoist `ACR_NAME` with uniqueness comment or auto-suffix | Phase 8 |
| FP4 | Low | Add log-check step to "Next steps" output | Phase 8 |
