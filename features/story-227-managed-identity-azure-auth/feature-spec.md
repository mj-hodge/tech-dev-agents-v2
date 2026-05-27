# STORY-227: Feature Spec (Phase 6)

**Date:** 2026-04-15
**Scope:** Medium
**Designer:** Morris

---

## 1. Overview

Replace manual OAuth2 client_credentials flow in `AzureCostClient` with `DefaultAzureCredential` from azure-identity SDK. Create a user-assigned managed identity for the ops-console VM with Cost Management Reader role.

---

## 2. Exact Code Changes

### 2.1 `tech_dev_agents/ops_console/services/azure_cost_client.py`

**Before (lines 1-61):** Manual OAuth2 with tenant_id, client_id, client_secret constructor args and `_get_token()` method.

**After:**

```python
"""Client for Azure Cost Management REST API."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from azure.core.credentials import TokenCredential

from tech_dev_agents.ops_console.models.responses import DailyCost

logger = logging.getLogger(__name__)


class AzureCostError(Exception):
    """Raised on Azure Cost API failures."""


class AzureCostClient:
    """Client for Azure Cost Management REST API.

    Uses any azure.core TokenCredential (DefaultAzureCredential, ManagedIdentityCredential,
    ClientSecretCredential, etc.) for authentication.
    """

    _SCOPE = "https://management.azure.com/.default"

    def __init__(
        self,
        credential: TokenCredential,
        subscription_id: str,
        http_client: httpx.AsyncClient,
        agent_map: dict[str, str] | None = None,
    ):
        self._credential = credential
        self._subscription_id = subscription_id
        self._http = http_client
        self._agent_map = agent_map or {}

    async def _get_token(self) -> str:
        """Get access token from the injected credential.

        azure-identity handles caching, refresh, and retry internally.
        """
        token = self._credential.get_token(self._SCOPE)
        return token.token

    # ... get_daily_costs, get_agent_daily_costs, _parse_cost_response unchanged ...
```

**Changes summary:**
- Remove `tenant_id`, `client_id`, `client_secret` from constructor
- Add `credential: TokenCredential` parameter
- Replace 20-line manual `_get_token()` with 2-line credential.get_token()
- Remove `import time`, `import json`; add `import logging`, `from azure.core.credentials import TokenCredential`
- Add `logger` for credential type logging
- Remove `self._token` and `self._token_expires_at` fields

### 2.2 `tech_dev_agents/ops_console/config.py`

**Remove** (lines 33-36):
```python
    azure_tenant_id: str | None = None
    azure_client_id: str | None = None
    azure_client_secret: str | None = None
```

**Add:**
```python
    # User-assigned managed identity client ID (optional — omit for system-assigned or DefaultAzureCredential auto-discovery)
    azure_managed_identity_client_id: str | None = None
```

**Update `azure_enabled` property** (lines 80-88):
```python
    @property
    def azure_enabled(self) -> bool:
        return bool(self.azure_subscription_id)
```

Only `subscription_id` is required. Credential discovery is automatic.

### 2.3 `tech_dev_agents/ops_console/main.py`

**Replace** (lines 82-89):
```python
            azure_client = AzureCostClient(
                tenant_id=settings.azure_tenant_id,
                client_id=settings.azure_client_id,
                client_secret=settings.azure_client_secret,
                subscription_id=settings.azure_subscription_id,
                http_client=http_client,
                agent_map=agent_map,
            )
```

**With:**
```python
            from azure.identity import DefaultAzureCredential

            credential_kwargs = {}
            if settings.azure_managed_identity_client_id:
                credential_kwargs["managed_identity_client_id"] = settings.azure_managed_identity_client_id
            credential = DefaultAzureCredential(**credential_kwargs)
            logger.info("Azure credential chain initialized (managed_identity_client_id=%s)",
                        settings.azure_managed_identity_client_id or "auto")

            azure_client = AzureCostClient(
                credential=credential,
                subscription_id=settings.azure_subscription_id,
                http_client=http_client,
                agent_map=agent_map,
            )
```

### 2.4 `deployment/ops-console/docker-compose.yml`

**Remove** (lines 60-62):
```yaml
      OPS_AZURE_TENANT_ID: ${AZURE_TENANT_ID:-}
      OPS_AZURE_CLIENT_ID: ${AZURE_CLIENT_ID:-}
      OPS_AZURE_CLIENT_SECRET: ${AZURE_CLIENT_SECRET:-}
```

**Replace with:**
```yaml
      OPS_AZURE_MANAGED_IDENTITY_CLIENT_ID: ${AZURE_MANAGED_IDENTITY_CLIENT_ID:-}
```

Keep `OPS_AZURE_SUBSCRIPTION_ID` unchanged.

### 2.5 `requirements.txt`

**Add:**
```
azure-identity>=1.15.0
```

### 2.6 `deployment/vm/deploy-agent.sh`

**Add after PHASE 2 (VM creation, ~line 119), before PHASE 3 (firewall):**

```bash
# ============================================================================
# PHASE 2b: Attach managed identity for Azure Cost Management
# ============================================================================
echo "[2b] Attaching managed identity..."
IDENTITY_NAME="id-ops-console-cost-reader"

# Create identity if it doesn't exist (idempotent)
run az identity create \
  --resource-group "$RESOURCE_GROUP" \
  --name "$IDENTITY_NAME" \
  --output none 2>/dev/null || true

# Get identity resource ID and client ID
IDENTITY_ID=$(az identity show --resource-group "$RESOURCE_GROUP" --name "$IDENTITY_NAME" --query id -o tsv)
IDENTITY_CLIENT_ID=$(az identity show --resource-group "$RESOURCE_GROUP" --name "$IDENTITY_NAME" --query clientId -o tsv)

# Assign identity to VM
run az vm identity assign \
  --resource-group "$RESOURCE_GROUP" \
  --name "$VM_NAME" \
  --identities "$IDENTITY_ID" \
  --output none

# Assign Cost Management Reader role (idempotent)
SUBSCRIPTION_ID=$(az account show --query id -o tsv)
run az role assignment create \
  --assignee-object-id "$(az identity show --resource-group "$RESOURCE_GROUP" --name "$IDENTITY_NAME" --query principalId -o tsv)" \
  --assignee-principal-type ServicePrincipal \
  --role "Cost Management Reader" \
  --scope "/subscriptions/${SUBSCRIPTION_ID}" \
  --output none 2>/dev/null || true

echo "  Identity: ${IDENTITY_NAME}"
echo "  Client ID: ${IDENTITY_CLIENT_ID}"
echo "  Role: Cost Management Reader on subscription ${SUBSCRIPTION_ID}"
```

### 2.7 `NEW-AGENT-PROCESS.md` (new section in existing runbook or new file)

**Add to `deployment/vm/NEW-AGENT-PROCESS.md`:**

```markdown
## Azure Managed Identity for Cost Management

Each ops-console VM uses a user-assigned managed identity for Azure Cost Management API access.
No client secrets required.

### Identity: `id-ops-console-cost-reader`

- **Type:** User-assigned managed identity
- **Role:** Cost Management Reader
- **Scope:** Subscription `sub-tech-dev-agents-dev`
- **Shared by:** All ops-console VMs

### Automatic Setup

`deploy-agent.sh` automatically:
1. Creates the identity (idempotent — skips if exists)
2. Assigns it to the new VM
3. Grants Cost Management Reader role

### Manual Setup (existing VMs)

For VMs deployed before this change:

    # Attach identity to existing VM
    az vm identity assign \
      --resource-group rg-tech-dev-agents-dev \
      --name vm-<agent>-agent-dev \
      --identities $(az identity show -g rg-tech-dev-agents-dev -n id-ops-console-cost-reader --query id -o tsv)

    # Remove old client secret from .env
    ssh azureagent@<ip> "sudo sed -i '/AZURE_CLIENT_SECRET/d' /opt/ops-console/.env"
    ssh azureagent@<ip> "sudo sed -i '/AZURE_TENANT_ID/d' /opt/ops-console/.env"
    ssh azureagent@<ip> "sudo sed -i '/AZURE_CLIENT_ID/d' /opt/ops-console/.env"

    # Add managed identity client ID
    ssh azureagent@<ip> "echo 'AZURE_MANAGED_IDENTITY_CLIENT_ID=<client-id>' | sudo tee -a /opt/ops-console/.env"

    # Restart ops-console
    ssh azureagent@<ip> "sudo systemctl restart ops-console"

### Verification

    # Check auth log shows ManagedIdentityCredential
    curl -s http://<ip>:8005/api/fleet | jq '.total_daily_spend_usd'
    # Should return non-zero value
```

---

## 3. Azure CLI Commands for Mark

**Run these once from your workstation (Mark's laptop or Azure Cloud Shell):**

```bash
# 1. Create the user-assigned managed identity
az identity create \
  --resource-group rg-tech-dev-agents-dev \
  --name id-ops-console-cost-reader

# 2. Get the identity details
IDENTITY_ID=$(az identity show -g rg-tech-dev-agents-dev -n id-ops-console-cost-reader --query id -o tsv)
IDENTITY_CLIENT_ID=$(az identity show -g rg-tech-dev-agents-dev -n id-ops-console-cost-reader --query clientId -o tsv)
IDENTITY_PRINCIPAL_ID=$(az identity show -g rg-tech-dev-agents-dev -n id-ops-console-cost-reader --query principalId -o tsv)
echo "Client ID: ${IDENTITY_CLIENT_ID}"

# 3. Grant Cost Management Reader role at subscription scope
SUBSCRIPTION_ID=$(az account show --query id -o tsv)
az role assignment create \
  --assignee-object-id "${IDENTITY_PRINCIPAL_ID}" \
  --assignee-principal-type ServicePrincipal \
  --role "Cost Management Reader" \
  --scope "/subscriptions/${SUBSCRIPTION_ID}"

# 4. Assign identity to the ops-console VM (Morris's VM)
az vm identity assign \
  --resource-group rg-tech-dev-agents-dev \
  --name vm-morris-agent-dev \
  --identities "${IDENTITY_ID}"

# 5. Verify identity is attached
az vm identity show \
  --resource-group rg-tech-dev-agents-dev \
  --name vm-morris-agent-dev

# 6. (After 48h verification) Delete the old service principal
# az ad sp delete --id <ops-console-cost-reader-sp-object-id>
```

---

## 4. Test Updates Summary

| Test ID | Description | Change |
|---------|-------------|--------|
| T28 | Token acquisition | Update to inject mock TokenCredential |
| T29 | Token caching | Remove — azure-identity handles caching |
| T30-T32 | Cost queries | Update constructor to use credential injection |
| T33 (new) | Managed identity path | Verify DefaultAzureCredential used when no env vars |
| T34 (new) | Client-secret fallback | Verify EnvironmentCredential works with env vars |
| T35 (new) | Both-absent error | Verify clear error when no credential available |

---

## 5. Rollback Plan

1. Revert `azure_cost_client.py` to manual OAuth2
2. Restore `AZURE_CLIENT_SECRET` in `.env`
3. Restart ops-console

The `EnvironmentCredential` fallback means we can also just re-add env vars without code revert.

---

## 6. Deployment Sequence

1. **Code deploy** (this PR) — zero-downtime, EnvironmentCredential covers existing env vars
2. **Mark runs az CLI** — creates identity, assigns to VM (Section 3)
3. **Remove old env vars** — `AZURE_CLIENT_SECRET`, `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`
4. **Verify** — `/api/fleet` returns cost data, auth log shows ManagedIdentityCredential
5. **48h soak** — monitor for errors
6. **Delete old SP** — `az ad sp delete`
