# STORY-227: Analysis (Phase 4)

**Date:** 2026-04-15
**Scope:** Medium
**Analyst:** Morris

---

## 1. Current State Audit

### 1.1 Client Secret Credential Usage

**`tech_dev_agents/ops_console/services/azure_cost_client.py`** (lines 21-37):
The `AzureCostClient.__init__` accepts `tenant_id`, `client_id`, `client_secret` as constructor args. The `_get_token()` method (lines 39-61) performs a manual OAuth2 client_credentials grant against `login.microsoftonline.com`, caching the token until 5 min before expiry.

**`tech_dev_agents/ops_console/config.py`** (lines 33-36):
Settings declare `azure_tenant_id`, `azure_client_id`, `azure_client_secret`, `azure_subscription_id` as optional strings. The `azure_enabled` property (line 80-88) requires ALL four to be truthy.

**`tech_dev_agents/ops_console/main.py`** (lines 78-89):
Instantiates `AzureCostClient` with `settings.azure_tenant_id`, `settings.azure_client_id`, `settings.azure_client_secret`, `settings.azure_subscription_id`.

**`deployment/ops-console/docker-compose.yml`** (lines 60-63):
Passes `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, `AZURE_SUBSCRIPTION_ID` from host env into the container.

### 1.2 Dependency Status

| Package | Required | Installed | Status |
|---------|----------|-----------|--------|
| `azure-identity` | >= 1.15.0 | 1.25.3 | OK |
| `azure-core` | (transitive) | installed | OK |

`azure-identity` is installed system-wide but **not listed in `requirements.txt`**. Must be added.

### 1.3 Test Coverage

Existing tests in `tests/ops_console/test_azure_cost_client.py` (T28-T32):
- T28: `_get_token` sends client_credentials grant
- T29: Token caching
- T30: Resource group mapping
- T31: Empty response handling
- T32: Single agent filtering

All 5 tests use the old constructor signature (`tenant_id`, `client_id`, `client_secret`). These must be updated for the new credential-injected interface.

---

## 2. Migration Analysis

### 2.1 DefaultAzureCredential Chain

`DefaultAzureCredential` tries credentials in order:
1. `EnvironmentCredential` (AZURE_CLIENT_ID + AZURE_CLIENT_SECRET + AZURE_TENANT_ID)
2. `WorkloadIdentityCredential`
3. `ManagedIdentityCredential` (IMDS endpoint at 169.254.169.254)
4. `AzureDeveloperCliCredential`
5. `AzureCliCredential` (`az login`)

**On Azure VMs with user-assigned managed identity:** resolves at step 3.
**On local dev with `az login`:** resolves at step 5.
**Fallback for existing SP creds in env:** resolves at step 1 (zero-change migration path).

### 2.2 User-Assigned vs System-Assigned

| Aspect | System-Assigned | User-Assigned |
|--------|----------------|---------------|
| Lifecycle | Tied to VM | Independent |
| Sharing | 1:1 with VM | N VMs can share |
| Deletion | Auto with VM | Manual |
| Identity mgmt | Per-VM | Centralized |

**Recommendation: User-assigned.** One identity `id-ops-console-cost-reader` with `Cost Management Reader` role, assigned to all VMs running the ops console. Simpler to manage than N system-assigned identities.

### 2.3 Required Azure RBAC

| Identity | Role | Scope |
|----------|------|-------|
| `id-ops-console-cost-reader` | `Cost Management Reader` | Subscription `sub-tech-dev-agents-dev` |

This is the minimum privilege. No write access, no resource modification.

---

## 3. Migration Strategy

### 3.1 Approach: Credential Injection

Replace the manual OAuth2 flow with a `TokenCredential` interface injection:

```python
# Before: manual client_credentials grant
class AzureCostClient:
    def __init__(self, tenant_id, client_id, client_secret, subscription_id, http_client, ...):
        # ... manual _get_token via httpx POST

# After: credential injection
from azure.identity import DefaultAzureCredential
from azure.core.credentials import TokenCredential

class AzureCostClient:
    def __init__(self, credential: TokenCredential, subscription_id: str, http_client, ...):
        # ... uses credential.get_token("https://management.azure.com/.default")
```

**Key benefits:**
- Testable: inject mock `TokenCredential` in tests
- Flexible: works with managed identity, CLI, SP creds, anything
- No manual token management: azure-identity handles caching, refresh, retry

### 3.2 Migration Order

1. **Phase 6 (Design):** Exact diffs + az CLI commands for Mark
2. **Phase 7 (Tests):** Write tests for new interface (RED)
3. **Phase 8 (Implementation):**
   a. Update `azure_cost_client.py` (~10 lines changed)
   b. Update `config.py` to remove client_secret, add managed_identity_client_id
   c. Update `main.py` to instantiate DefaultAzureCredential
   d. Update `docker-compose.yml` to remove AZURE_CLIENT_SECRET
   e. Add `azure-identity` to requirements.txt
   f. Update `deploy-agent.sh` with identity attach block
   g. Create `NEW-AGENT-PROCESS.md` section
4. **Post-deploy:** Mark runs az CLI to create identity + assign to VMs
5. **48h verification:** Delete old SP

### 3.3 Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| DefaultAzureCredential fallback mask errors | Low | Medium | Log which credential type succeeded |
| IMDS timeout on non-Azure env | Low | Low | DefaultAzureCredential handles gracefully |
| Existing tests break | Certain | Low | Update constructor calls — straightforward |
| docker-compose env removal breaks staging | Low | Medium | EnvironmentCredential fallback covers this |

### 3.4 Backward Compatibility

**Zero-downtime migration:** `DefaultAzureCredential` tries `EnvironmentCredential` FIRST. Existing VMs with `AZURE_CLIENT_SECRET` in `.env` will continue working unchanged. The managed identity path activates only after env vars are removed.

This means we can deploy the code change first, then migrate VMs one at a time.

---

## 4. Recommendation

**Proceed with credential injection approach.** The change is:
- ~10 lines in `azure_cost_client.py` (remove manual OAuth2, inject TokenCredential)
- ~5 lines in `config.py` (remove client_secret, add managed_identity_client_id)
- ~3 lines in `main.py` (instantiate DefaultAzureCredential)
- ~10 lines in `deploy-agent.sh` (identity create/assign block)
- 1 line in `requirements.txt` (add azure-identity)
- Remove `AZURE_CLIENT_SECRET` from docker-compose.yml

Total: ~30 lines changed across 5 files, plus new tests and documentation.

**No blockers. Advance to Phase 6.**
