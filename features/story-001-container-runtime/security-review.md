# Security Review: Container Runtime & Identity

> Phase 6b — Security Review
> Story: STORY-001 — Container Runtime & Identity
> Date: 2026-03-26
> Reviewer: Security Phase

---

## Summary

The design is fundamentally sound. The identity model and secrets management approach are well-considered. Four issues require attention before Phase 7.

---

## Findings

### CRITICAL

None.

---

### HIGH

#### H1 — ACR Admin Credentials Used for ACI Image Pull

**Location:** `provision-agent.sh` Step 5 — `ACR_USERNAME` / `ACR_PASSWORD` via `az acr credential show`

The script enables `--admin-enabled true` on ACR and passes the admin credentials as plaintext in the `az container create` command. ACR admin credentials are long-lived, cannot be scoped to a single container group, and are visible in the provisioning script's shell history and in the ACI resource definition.

**Mitigation:** Grant the ACI managed identity the `AcrPull` role on the registry instead. ACI supports managed identity image pull via `--acr-identity`. This eliminates the admin credential entirely:

```bash
az role assignment create \
  --role "AcrPull" \
  --assignee "$IDENTITY_PRINCIPAL_ID" \
  --scope "$(az acr show --name $ACR_NAME --query id --output tsv)"

az container create \
  ... \
  --acr-identity "$IDENTITY_RESOURCE_ID" \
  # Remove --registry-username and --registry-password
```

Disable ACR admin credentials after migration (`--admin-enabled false`).

---

### MEDIUM

#### M1 — Bot App Password Passed to `az bot create` as Plaintext

**Location:** `provision-agent.sh` Step 6

`APP_PASSWORD` (fetched from `az ad app credential reset`) is passed directly to `az bot create --password`. This value appears in shell history and Azure Activity Log. It is also the same value stored in Key Vault, so the Key Vault copy is already secured — the risk is the in-flight exposure.

**Mitigation:** The Bot Service registration only needs the password at creation time to validate the app. After creation, the password is not re-read by Bot Service — it is only used by the container at runtime (fetched from Key Vault). The shell history risk is manageable with `HISTCONTROL=ignorespace` or by sourcing secrets from a file, but this is a low-urgency cleanup. Document the risk in the script's header comment.

#### M2 — ACI Exposed on Public IP with No Inbound Restriction

**Location:** Feature spec §5.1, §5.2

The container listens on a public IP with no network-level restriction on who can POST to `/api/messages`. While the Bot Framework adapter validates the `Authorization` JWT on every inbound request (signed by Azure Bot Service), there is no IP allowlist preventing direct probing or DoS attempts against the endpoint.

**Mitigation (v1 acceptable):** The Bot Framework JWT validation is the effective authentication gate. Direct requests without a valid Bot Service token are rejected with 401. Document this explicitly in the spec.

**Mitigation (v2):** Deploy ACI into a VNet and front with Application Gateway or Azure Front Door with an IP allowlist restricted to Bot Framework service IPs (published by Microsoft at `https://aka.ms/bf-ip`).

#### M3 — System-Assigned Identity RBAC Lost on Container Group Recreation

**Location:** Analysis §1.2, feature-spec §3.3

When the ACI container group is deleted and recreated (required for image tag changes), the system-assigned managed identity principal ID changes. The new identity has no Key Vault RBAC until the script's RBAC grant step is re-run. During this window, the container starts, fails to fetch secrets, and restarts in a crash loop.

**Mitigation (v1):** The provisioning script already includes the RBAC grant step idempotently. Ensure the redeploy runbook (§2.3) explicitly includes the RBAC re-grant. Add a note to the teardown script to warn about this.

**Mitigation (v2):** Migrate to user-assigned managed identity, which retains its principal ID and RBAC assignments across container group recreation.

---

### LOW

#### L1 — `bot-app-id` Stored as a Secret in Key Vault

**Location:** feature-spec §3.4

The `bot-app-id` (the Entra ID app registration client ID) is not a secret — it is a public identifier that appears in the Bot Service configuration, Teams app manifest, and HTTPS request headers. Storing it in Key Vault alongside the password adds unnecessary secret-fetch latency and obscures the distinction between public configuration and private credentials.

**Mitigation:** Move `bot-app-id` to a plain environment variable in the ACI configuration (alongside `KEYVAULT_URI`). Remove from Key Vault. The `bot-app-password` remains in Key Vault.

#### L2 — `node:22-slim` Base Image Has No Pinned Digest

**Location:** Dockerfile `FROM node:22-slim`

Using a floating tag means a rebuild can silently pull a different base layer, including one with unpatched CVEs introduced upstream.

**Mitigation:** Pin to a specific digest for production builds (`FROM node:22-slim@sha256:<digest>`). Update the digest deliberately as part of dependency maintenance. For v1, a comment noting this risk is sufficient; pin in Phase 8 before first production deploy.

#### L3 — `@anthropic-ai/claude-code@latest` in Dockerfile

**Location:** Dockerfile Layer 2

`@latest` is an unpinned floating tag. A compromised or unintentionally breaking release would be silently pulled on the next `az acr build`.

**Mitigation:** Pin to a specific version (e.g., `@1.2.3`) and update deliberately. Same pattern applies to all global npm installs in the Dockerfile.

#### L4 — Stack Traces May Contain Secret Values in Error Logs

**Location:** feature-spec §4.3, `src/index.ts` error handler

If `DefaultAzureCredential` or the Key Vault SDK throws a structured error during secret fetch, the error object may contain the KEYVAULT_URI, partial credential details, or HTTP response bodies in the stack trace. The current error handler logs `err.message` — this is generally safe, but `err` object toString or JSON serialization could leak more.

**Mitigation:** In the `main().catch()` handler, log only `err.message`, not `err` or `JSON.stringify(err)`. Add this to the code review checklist in Phase 8b.

---

## Recommendation

**Proceed to Phase 7** after resolving H1 (ACR admin credentials). H1 is a design change that affects the provisioning script and should be captured in the feature spec before test design begins. M1–M3 can be addressed in Phase 8 implementation. L1–L4 are Phase 8 polish items.

| ID | Severity | Action | Phase |
|----|----------|--------|-------|
| H1 | High | Replace ACR admin creds with `AcrPull` RBAC on managed identity | Spec update before Phase 7 |
| M1 | Medium | Document plaintext password risk in script header | Phase 8 |
| M2 | Medium | Document Bot Framework JWT as auth gate; VNet in v2 | Phase 8 |
| M3 | Medium | Add RBAC re-grant to redeploy runbook; migrate to user-assigned MI in v2 | Phase 8 |
| L1 | Low | Move `bot-app-id` to plain env var | Phase 8 |
| L2 | Low | Pin base image digest before first prod deploy | Phase 8 |
| L3 | Low | Pin Claude Code CLI version | Phase 8 |
| L4 | Low | Scope error logging to `err.message` only | Phase 8b |
