# Feature Spec: Container Runtime & Identity

> Phase 6 — Design
> Story: STORY-001 — Container Runtime & Identity
> Date: 2026-03-26
> Scope: Medium

---

## 1. Container Image Specification

### 1.1 Dockerfile

```dockerfile
# ============================================================
# Dockerfile — Agent Runtime Container
# Target: < 1GB uncompressed, < 400MB compressed in ACR
# ============================================================
FROM node:22-slim AS base

# Layer 1: System packages (least frequently changed)
# Install git, gh CLI, and curl in a single layer; clean apt cache
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      git \
      curl \
      ca-certificates \
      gnupg && \
    # Add GitHub CLI apt repository
    curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | \
      gpg --dearmor -o /usr/share/keyrings/githubcli-archive-keyring.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] \
      https://cli.github.com/packages stable main" | \
      tee /etc/apt/sources.list.d/github-cli.list > /dev/null && \
    apt-get update && \
    apt-get install -y --no-install-recommends gh && \
    # Clean up
    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Layer 2: Claude Code CLI (changes on CLI version updates only)
RUN npm install -g @anthropic-ai/claude-code@latest && \
    npm cache clean --force

# Layer 3: Create non-root user and workspace directory
RUN groupadd -r agent && useradd -r -g agent -m -s /bin/bash agent && \
    mkdir -p /workspace && chown agent:agent /workspace

# Layer 4: Application dependencies (changes when package.json changes)
WORKDIR /app
COPY package*.json ./
RUN npm ci --production && npm cache clean --force

# Layer 5: Application source (changes on every build)
COPY dist/ ./dist/

# Ownership
RUN chown -R agent:agent /app

# Runtime configuration
ENV NODE_ENV=production
ENV PORT=3978
ENV REPO_PATH=/workspace

# Switch to non-root user (security requirement)
USER agent

EXPOSE 3978

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:3978/api/health || exit 1

CMD ["node", "dist/index.js"]
```

### 1.2 Layer Ordering Rationale

Layers are ordered from least to most frequently changed to maximize Docker cache hits:

| Layer | Content | Change Frequency |
|-------|---------|-----------------|
| 1 | Base OS + git + gh CLI + curl | Rare (tool version bumps) |
| 2 | Claude Code CLI | Occasional (CLI releases) |
| 3 | Non-root user + workspace | Never (static config) |
| 4 | npm dependencies (package.json) | Moderate (dep updates) |
| 5 | Application source (dist/) | Every build |

### 1.3 Image Size Budget

| Component | Estimated Size (uncompressed) |
|-----------|------------------------------|
| node:22-slim base | ~200MB |
| git + gh CLI + curl + gnupg | ~120MB |
| Claude Code CLI (global npm) | ~250MB |
| App dependencies (npm ci) | ~80MB |
| Application source (dist/) | ~5MB |
| **Total** | **~655MB** |

Target: < 1GB uncompressed. Compressed in ACR: ~280-350MB.

### 1.4 Entrypoint Behavior

The `dist/index.js` entrypoint follows this startup sequence:

1. **Read `KEYVAULT_URI`** from environment variable (the only config passed via env).
2. **Fetch secrets from Key Vault** using `@azure/identity` + `@azure/keyvault-secrets` with `DefaultAzureCredential`. This resolves to the ACI system-assigned managed identity in production, and to `az login` credentials in local development.
3. **Set secrets in process memory** (never in `process.env` or on disk). All downstream code accesses secrets through the `getSecret()` accessor function.
4. **Initialize Application Insights** telemetry client using the connection string fetched from Key Vault.
5. **Start the HTTP server** on `PORT` (default 3978) with the Bot Framework adapter and health endpoint.
6. **Log startup complete** with timestamp and container instance metadata (no secrets in logs).

If Key Vault fetch fails, the process exits with code 1 and a structured error log. ACI restart policy (`OnFailure`) will restart the container, retrying the secret fetch.

---

## 2. Azure Provisioning Script

The script below is adapted from `docs/hermes-prompt.md` `provision-hermes.sh`. Steps 1-3, 6-7 are reused directly. Steps 4-5 are replaced with ACR + ACI provisioning. Step 8 is removed (workspace clone happens at task assignment time, not at provisioning time).

### 2.1 Script: `provision-agent.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# CONFIGURATION — edit these values per agent
# ============================================================
AGENT_NAME="agent-dev"                        # kebab-case, used as prefix for all resources
RESOURCE_GROUP="rg-${AGENT_NAME}"
LOCATION="eastus"
ACR_NAME="acr${AGENT_NAME//[-]/}"            # ACR requires alphanumeric only, globally unique
KEYVAULT_NAME="kv-${AGENT_NAME}"             # 3-24 chars, globally unique
BOT_NAME="${AGENT_NAME}-bot"                  # Bot Service registration name

# Container sizing
ACI_CPU=1                                     # vCPU count
ACI_MEMORY=2                                  # GB RAM
ACI_DNS_LABEL="${AGENT_NAME}"                 # becomes <label>.<region>.azurecontainer.io
ACI_RESTART_POLICY="OnFailure"                # OnFailure | Always | Never
ACI_IMAGE_TAG="latest"

# Secrets — provide at provisioning time (not stored in script)
ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:?Set ANTHROPIC_API_KEY env var}"
GITHUB_TOKEN="${GITHUB_TOKEN:?Set GITHUB_TOKEN env var}"

# ============================================================
# STEP 1: Resource Group (reused from Hermes)
# ============================================================
echo ">>> [1/8] Creating resource group..."
az group create \
  --name "$RESOURCE_GROUP" \
  --location "$LOCATION" \
  --output none

# ============================================================
# STEP 2: Entra ID App Registration (reused from Hermes)
# ============================================================
echo ">>> [2/8] Creating Entra ID app registration..."
APP_REG=$(az ad app create \
  --display-name "$BOT_NAME" \
  --sign-in-audience "AzureADMultipleOrgs" \
  --query "{appId:appId, id:id}" \
  --output json)

APP_ID=$(echo "$APP_REG" | jq -r '.appId')
OBJECT_ID=$(echo "$APP_REG" | jq -r '.id')
echo "    App ID: $APP_ID"

# Create client secret (valid 2 years)
APP_PASSWORD=$(az ad app credential reset \
  --id "$APP_ID" \
  --years 2 \
  --query "password" \
  --output tsv)
echo "    App password created"

# ============================================================
# STEP 3: Key Vault + Secrets (reused from Hermes)
# ============================================================
echo ">>> [3/8] Creating Key Vault..."
az keyvault create \
  --name "$KEYVAULT_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --location "$LOCATION" \
  --enable-rbac-authorization true \
  --output none

KEYVAULT_ID=$(az keyvault show --name "$KEYVAULT_NAME" --query id --output tsv)
KV_URI="https://${KEYVAULT_NAME}.vault.azure.net"

# Grant current user Secrets Officer role (to store secrets)
CURRENT_USER_OID=$(az ad signed-in-user show --query id --output tsv)
az role assignment create \
  --role "Key Vault Secrets Officer" \
  --assignee "$CURRENT_USER_OID" \
  --scope "$KEYVAULT_ID" \
  --output none

echo "    Waiting 30s for RBAC propagation..."
sleep 30

echo ">>> Storing secrets in Key Vault..."
az keyvault secret set --vault-name "$KEYVAULT_NAME" --name "anthropic-api-key"  --value "$ANTHROPIC_API_KEY"  --output none
az keyvault secret set --vault-name "$KEYVAULT_NAME" --name "bot-app-id"         --value "$APP_ID"             --output none
az keyvault secret set --vault-name "$KEYVAULT_NAME" --name "bot-app-password"   --value "$APP_PASSWORD"       --output none
az keyvault secret set --vault-name "$KEYVAULT_NAME" --name "github-token"       --value "$GITHUB_TOKEN"       --output none

# ============================================================
# STEP 4: Azure Container Registry (NEW — replaces App Service)
# ============================================================
echo ">>> [4/8] Creating Azure Container Registry..."
az acr create \
  --name "$ACR_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --sku Basic \
  --admin-enabled true \
  --output none

echo ">>> Building and pushing container image..."
# Assumes Dockerfile is in the current directory
az acr build \
  --registry "$ACR_NAME" \
  --image "${AGENT_NAME}:${ACI_IMAGE_TAG}" \
  .

# ============================================================
# STEP 5: Azure Container Instances (NEW — replaces App Service)
# ============================================================
echo ">>> [5/8] Creating ACI container group with managed identity..."

# Get ACR credentials for image pull
ACR_SERVER="${ACR_NAME}.azurecr.io"
ACR_USERNAME=$(az acr credential show --name "$ACR_NAME" --query username --output tsv)
ACR_PASSWORD=$(az acr credential show --name "$ACR_NAME" --query 'passwords[0].value' --output tsv)

az container create \
  --resource-group "$RESOURCE_GROUP" \
  --name "${AGENT_NAME}-aci" \
  --image "${ACR_SERVER}/${AGENT_NAME}:${ACI_IMAGE_TAG}" \
  --registry-login-server "$ACR_SERVER" \
  --registry-username "$ACR_USERNAME" \
  --registry-password "$ACR_PASSWORD" \
  --cpu "$ACI_CPU" \
  --memory "$ACI_MEMORY" \
  --ports 3978 \
  --dns-name-label "$ACI_DNS_LABEL" \
  --restart-policy "$ACI_RESTART_POLICY" \
  --assign-identity \
  --environment-variables \
    KEYVAULT_URI="$KV_URI" \
    PORT="3978" \
    NODE_ENV="production" \
    BOT_NAME="$BOT_NAME" \
  --output none

# Retrieve the system-assigned managed identity principal ID
IDENTITY_PRINCIPAL_ID=$(az container show \
  --name "${AGENT_NAME}-aci" \
  --resource-group "$RESOURCE_GROUP" \
  --query "identity.principalId" \
  --output tsv)
echo "    Managed identity principal: $IDENTITY_PRINCIPAL_ID"

# Grant managed identity Key Vault Secrets User role
echo ">>> Granting managed identity Key Vault access..."
az role assignment create \
  --role "Key Vault Secrets User" \
  --assignee "$IDENTITY_PRINCIPAL_ID" \
  --scope "$KEYVAULT_ID" \
  --output none

# Retrieve the ACI FQDN
ACI_FQDN=$(az container show \
  --name "${AGENT_NAME}-aci" \
  --resource-group "$RESOURCE_GROUP" \
  --query "ipAddress.fqdn" \
  --output tsv)
echo "    ACI FQDN: $ACI_FQDN"

# ============================================================
# STEP 6: Azure Bot Service (reused from Hermes)
# ============================================================
echo ">>> [6/8] Creating Azure Bot Service registration..."
MESSAGING_ENDPOINT="https://${ACI_FQDN}/api/messages"

az bot create \
  --resource-group "$RESOURCE_GROUP" \
  --name "$BOT_NAME" \
  --kind "registration" \
  --appid "$APP_ID" \
  --password "$APP_PASSWORD" \
  --endpoint "$MESSAGING_ENDPOINT" \
  --sku "F0" \
  --output none

# ============================================================
# STEP 7: Enable Teams Channel (reused from Hermes)
# ============================================================
echo ">>> [7/8] Enabling Microsoft Teams channel..."
az bot msteams create \
  --name "$BOT_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --output none

# ============================================================
# STEP 8: Store App Insights connection string in Key Vault
# ============================================================
echo ">>> [8/8] Creating Application Insights..."
az extension add --name application-insights --yes 2>/dev/null || true

APPINSIGHTS_NAME="ai-${AGENT_NAME}"
az monitor app-insights component create \
  --app "$APPINSIGHTS_NAME" \
  --location "$LOCATION" \
  --resource-group "$RESOURCE_GROUP" \
  --output none

APPINSIGHTS_CONN=$(az monitor app-insights component show \
  --app "$APPINSIGHTS_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --query "connectionString" \
  --output tsv)

az keyvault secret set \
  --vault-name "$KEYVAULT_NAME" \
  --name "appinsights-connection-string" \
  --value "$APPINSIGHTS_CONN" \
  --output none

# ============================================================
# DONE
# ============================================================
echo ""
echo "============================================"
echo "  Agent provisioning complete!"
echo "============================================"
echo "  Resource Group:    $RESOURCE_GROUP"
echo "  ACI FQDN:          https://${ACI_FQDN}"
echo "  Bot Endpoint:      $MESSAGING_ENDPOINT"
echo "  Key Vault:         $KV_URI"
echo "  Bot App ID:        $APP_ID"
echo "  ACR Registry:      $ACR_SERVER"
echo "  App Insights:      $APPINSIGHTS_NAME"
echo ""
echo "  Next steps:"
echo "    1. Wait ~60s for RBAC to propagate to managed identity"
echo "    2. Restart the container: az container restart --name ${AGENT_NAME}-aci --resource-group $RESOURCE_GROUP"
echo "    3. Check logs: az container logs --name ${AGENT_NAME}-aci --resource-group $RESOURCE_GROUP"
echo "    4. Upload the Teams app manifest (appPackage.zip)"
echo "    5. Test: message the bot in Teams"
echo "============================================"
```

### 2.2 Per-Agent Customization

All agent-specific values are in the `CONFIGURATION` block at the top of the script. To provision a second agent, copy the script (or source an env file) and change:

| Variable | Purpose | Example (second agent) |
|----------|---------|----------------------|
| `AGENT_NAME` | Prefix for all resources | `agent-qa` |
| `LOCATION` | Azure region | `westus2` |
| `ACI_CPU` / `ACI_MEMORY` | Container sizing | `2` / `4` |
| `ACI_DNS_LABEL` | FQDN prefix | `agent-qa` |

Secrets are passed via environment variables at script execution time, never stored in the script file.

### 2.3 Redeployment (Image Update)

After a code change, rebuild and restart:

```bash
# Rebuild image in ACR
az acr build --registry "$ACR_NAME" --image "${AGENT_NAME}:${ACI_IMAGE_TAG}" .

# Restart container to pull new image
az container restart \
  --name "${AGENT_NAME}-aci" \
  --resource-group "$RESOURCE_GROUP"
```

Note: ACI does not support in-place image updates. If the image tag is `latest`, `az container restart` pulls the latest image. For immutable tags (e.g., `v1.2.3`), delete and recreate the container group:

```bash
az container delete --name "${AGENT_NAME}-aci" --resource-group "$RESOURCE_GROUP" --yes
# Then re-run the Step 5 az container create command
```

After recreation, the managed identity principal ID changes. Re-run the RBAC grant (the `az role assignment create` command in Step 5 is idempotent if the old assignment still exists, and creates the new one correctly).

---

## 3. Identity and Secrets

### 3.1 Identity Architecture

The agent uses two distinct identity mechanisms for separate concerns:

```
Entra ID App Registration (bot-app-id / bot-app-password)
  Purpose: Bot Framework channel authentication
  Scope:   Teams message send/receive via Azure Bot Service
  Stored:  Client secret in Key Vault as "bot-app-password"

System-Assigned Managed Identity (on ACI container group)
  Purpose: Azure resource access (Key Vault, ACR)
  Scope:   Key Vault Secrets User role on the vault
  Stored:  Nothing — credential is managed by Azure, auto-rotated
```

### 3.2 Entra ID App Registration

Created in Step 2 of the provisioning script:

| Property | Value |
|----------|-------|
| Display name | `{AGENT_NAME}-bot` |
| Sign-in audience | `AzureADMultipleOrgs` (required by Bot Framework) |
| Client secret validity | 2 years |
| Graph API permissions | None required for v1 (Bot Service handles Teams auth) |

The app registration provides the `appId` and `password` used by the Bot Framework SDK to authenticate the bot's outbound messages and validate inbound messages from Teams.

### 3.3 System-Assigned Managed Identity

Created automatically when ACI is provisioned with `--assign-identity`:

| Property | Value |
|----------|-------|
| Type | System-assigned |
| Lifecycle | Tied to the ACI container group |
| RBAC role | `Key Vault Secrets User` on the vault |
| Permissions granted | `get` and `list` on secrets only |
| Credential management | Automatic (Azure rotates the underlying certificate) |

The identity authenticates via the Instance Metadata Service (IMDS) at `169.254.169.254`. The `@azure/identity` SDK's `DefaultAzureCredential` resolves to `ManagedIdentityCredential` automatically in ACI.

### 3.4 Key Vault Secrets

| Secret Name | Source | Purpose | Rotation |
|-------------|--------|---------|----------|
| `anthropic-api-key` | Anthropic dashboard | Claude Code API access | Manual (no forced expiry) |
| `bot-app-id` | Entra ID app registration | Bot Framework app ID | Stable (only changes if app is recreated) |
| `bot-app-password` | Entra ID app credential | Bot Framework auth | Every 2 years (credential expiry) |
| `github-token` | GitHub PAT or App key | Git operations + PR creation | Every 90 days (PAT) or stable (App private key) |
| `appinsights-connection-string` | App Insights resource | Telemetry reporting | Stable (only changes if resource is recreated) |

### 3.5 Startup Secret Fetch Pattern

```typescript
// src/secrets.ts
import { DefaultAzureCredential } from '@azure/identity';
import { SecretClient } from '@azure/keyvault-secrets';

interface SecretStore {
  'anthropic-api-key': string;
  'bot-app-id': string;
  'bot-app-password': string;
  'github-token': string;
  'appinsights-connection-string': string;
}

const REQUIRED_SECRETS: (keyof SecretStore)[] = [
  'anthropic-api-key',
  'bot-app-id',
  'bot-app-password',
  'github-token',
  'appinsights-connection-string',
];

let secrets: Partial<SecretStore> = {};
let loaded = false;

/**
 * Fetch all required secrets from Key Vault at startup.
 * Uses DefaultAzureCredential which resolves to:
 *   - ManagedIdentityCredential in ACI (production)
 *   - AzureCliCredential in local dev (after `az login`)
 *
 * Throws if KEYVAULT_URI is not set or any secret is missing.
 */
export async function loadSecrets(): Promise<void> {
  const uri = process.env.KEYVAULT_URI;
  if (!uri) {
    throw new Error('KEYVAULT_URI environment variable is not set');
  }

  const credential = new DefaultAzureCredential();
  const client = new SecretClient(uri, credential);

  console.log(`[secrets] Fetching ${REQUIRED_SECRETS.length} secrets from Key Vault...`);
  const startTime = Date.now();

  const results = await Promise.all(
    REQUIRED_SECRETS.map(async (name) => {
      const secret = await client.getSecret(name);
      if (!secret.value) {
        throw new Error(`Secret "${name}" exists but has no value`);
      }
      return { name, value: secret.value };
    })
  );

  for (const { name, value } of results) {
    secrets[name] = value;
  }

  loaded = true;
  const elapsed = Date.now() - startTime;
  console.log(`[secrets] All secrets loaded in ${elapsed}ms`);
}

/**
 * Retrieve a previously loaded secret. Throws if secrets have not
 * been loaded yet or if the requested secret is not found.
 *
 * IMPORTANT: Never log the return value of this function.
 */
export function getSecret(name: keyof SecretStore): string {
  if (!loaded) {
    throw new Error('Secrets not loaded. Call loadSecrets() at startup.');
  }
  const val = secrets[name];
  if (!val) {
    throw new Error(`Secret "${name}" not found in store`);
  }
  return val;
}
```

### 3.6 Startup Integration

```typescript
// src/index.ts (entrypoint)
import { loadSecrets, getSecret } from './secrets';

async function main(): Promise<void> {
  // Step 1: Load secrets (must succeed before anything else)
  await loadSecrets();

  // Step 2: Initialize telemetry
  const appInsights = require('applicationinsights');
  appInsights.setup(getSecret('appinsights-connection-string'))
    .setAutoCollectRequests(true)
    .setAutoCollectExceptions(true)
    .start();

  // Step 3: Initialize Bot Framework adapter with fetched credentials
  // (STORY-002 implements the full bot; this story provides the runtime)

  // Step 4: Start HTTP server
  const port = process.env.PORT || 3978;
  // ... server setup
  console.log(`[startup] Listening on port ${port}`);
}

main().catch((err) => {
  console.error('[startup] Fatal error:', err.message);
  process.exit(1);
});
```

---

## 4. Logging

### 4.1 Container Log Streams (Built-in)

ACI captures all stdout and stderr from the container process. Access via:

```bash
# Tail logs in real time
az container logs \
  --name "${AGENT_NAME}-aci" \
  --resource-group "$RESOURCE_GROUP" \
  --follow

# Get recent logs (last 100 lines)
az container logs \
  --name "${AGENT_NAME}-aci" \
  --resource-group "$RESOURCE_GROUP" \
  --tail 100
```

ACI retains logs for the lifetime of the container group. Logs are lost when the container group is deleted.

### 4.2 Application Insights Integration

Application Insights provides persistent, structured telemetry that survives container restarts and deletions.

**Setup:** The `appinsights-connection-string` secret is fetched from Key Vault at startup and used to initialize the `applicationinsights` SDK.

**What to log:**

| Telemetry Type | When | Data |
|---------------|------|------|
| `trackEvent("agent_startup")` | Container start | Duration, secret fetch time, image version |
| `trackEvent("agent_command")` | Each bot command | Intent type, duration, cost, turn count, status |
| `trackException` | Unhandled errors | Error message, stack trace (scrubbed of secrets) |
| `trackMetric("secret_fetch_ms")` | Startup | Key Vault fetch latency |
| Auto-collected requests | Every HTTP request | Bot endpoint requests, health checks |
| Auto-collected exceptions | Runtime errors | Unhandled promise rejections, thrown errors |

**Telemetry helper:**

```typescript
// src/telemetry.ts
import * as appInsights from 'applicationinsights';

let client: appInsights.TelemetryClient | null = null;

export function initTelemetry(connectionString: string): void {
  appInsights.setup(connectionString)
    .setAutoCollectRequests(true)
    .setAutoCollectExceptions(true)
    .setAutoCollectPerformance(false)    // Not needed for a bot
    .setAutoCollectDependencies(true)    // Track Key Vault, HTTP calls
    .start();

  client = appInsights.defaultClient;

  // Add bot name as a global property on all telemetry
  client.context.tags[client.context.keys.cloudRole] = process.env.BOT_NAME || 'agent-dev';
}

export function trackEvent(
  name: string,
  properties?: Record<string, string>,
  measurements?: Record<string, number>
): void {
  client?.trackEvent({ name, properties, measurements });
}

export function trackException(error: Error): void {
  client?.trackException({ exception: error });
}

export function flush(): Promise<void> {
  return new Promise((resolve) => {
    client?.flush({ callback: () => resolve() });
  });
}
```

### 4.3 Log Hygiene Rules

These rules are enforced in code review (STORY-001 Phase 8b):

1. **Never log secret values.** Log secret names only (e.g., `[secrets] Loaded: anthropic-api-key`).
2. **Never log full request/response bodies** from Bot Framework (may contain user PII).
3. **Always use structured logging** (key-value properties in `trackEvent`, not string interpolation).
4. **Always include `bot_name`** as a property for fleet-scale filtering.
5. **Scrub stack traces** of any accidental secret leakage before sending to App Insights.

---

## 5. Networking

### 5.1 ACI Public IP and DNS

ACI provides a public IP and optional DNS label when `--dns-name-label` is specified:

| Property | Value |
|----------|-------|
| Public IP | Assigned automatically by ACI |
| DNS label | `{ACI_DNS_LABEL}.{LOCATION}.azurecontainer.io` |
| Example FQDN | `agent-dev.eastus.azurecontainer.io` |
| Port | 3978 (exposed in Dockerfile and ACI config) |
| Protocol | TCP |

The Bot Framework messaging endpoint is configured as:
```
https://{ACI_DNS_LABEL}.{LOCATION}.azurecontainer.io/api/messages
```

### 5.2 TLS

ACI does not provide built-in TLS termination for custom ports. The Bot Framework requires HTTPS for the messaging endpoint. There are two options:

**Option A (v1 recommended): Azure Bot Service handles TLS.** The Bot Service acts as a reverse proxy. Inbound Teams messages go through the Bot Service, which forwards to the ACI endpoint. The Bot Service validates the bot's identity via the app registration, not via TLS on the direct connection. For v1 with the bot-only interaction model, this is sufficient.

**Option B (if direct HTTPS is required): Add an Application Gateway or use a TLS sidecar container.** This adds cost and complexity. Defer to v2 if direct HTTPS to the container is needed.

Note: `az bot create` with `--endpoint` accepts HTTPS URLs. The ACI FQDN does serve on HTTPS for ports 443 when configured. If port 443 is used instead of 3978, ACI provides TLS termination automatically with a Microsoft-managed certificate.

**Revised approach for v1:** Expose on port 443 in ACI (mapping to container port 3978) to get automatic TLS:

```bash
# In az container create, use:
--ports 443 \
--environment-variables PORT="443" \
```

The messaging endpoint becomes `https://{ACI_DNS_LABEL}.{LOCATION}.azurecontainer.io/api/messages` on the default HTTPS port.

### 5.3 Outbound Network Access

The container requires outbound access to these endpoints:

| Destination | Port | Purpose |
|-------------|------|---------|
| `api.anthropic.com` | 443 | Claude Code API calls |
| `github.com` | 443 | Git clone/push, PR operations |
| `api.github.com` | 443 | GitHub API (gh CLI) |
| `*.vault.azure.net` | 443 | Key Vault secret fetch |
| `169.254.169.254` | 80 | IMDS (managed identity token) |
| `*.botframework.com` | 443 | Bot Framework service |
| `login.microsoftonline.com` | 443 | Entra ID token acquisition |
| `dc.services.visualstudio.com` | 443 | Application Insights telemetry |
| `registry.npmjs.org` | 443 | npm (only during image build, not runtime) |

ACI allows all outbound traffic by default. No NSG or firewall rules are needed for v1. For hardened environments (v2+), deploy ACI into a VNet with an NSG restricting outbound to the above endpoints.

---

## 6. Implementation Plan

### 6.1 Files to Create

| Order | File | Purpose | Complexity | Dependencies |
|-------|------|---------|-----------|-------------|
| 1 | `Dockerfile` | Container image definition | Low | None |
| 2 | `src/secrets.ts` | Key Vault secret fetch module | Medium | None (pure SDK usage) |
| 3 | `src/telemetry.ts` | App Insights telemetry wrapper | Low | `src/secrets.ts` (needs connection string) |
| 4 | `src/health.ts` | HTTP health check endpoint (`/api/health`) | Low | `src/secrets.ts` (reports secret load status) |
| 5 | `src/index.ts` | Entrypoint: startup orchestration | Medium | `src/secrets.ts`, `src/telemetry.ts`, `src/health.ts` |
| 6 | `provision-agent.sh` | Azure resource provisioning script | Medium | `Dockerfile` (must exist for ACR build) |
| 7 | `.dockerignore` | Exclude node_modules, .git, secrets from image | Low | None |
| 8 | `package.json` | Dependencies: `@azure/identity`, `@azure/keyvault-secrets`, `applicationinsights`, `botbuilder` | Low | None |
| 9 | `tsconfig.json` | TypeScript configuration (target ES2022, outDir dist/) | Low | None |

### 6.2 Files to Modify

None. This is the first story; all files are new.

### 6.3 Dependency Graph

```
package.json + tsconfig.json (must exist first)
        |
   .dockerignore
        |
   src/secrets.ts (standalone module, no internal deps)
        |
   src/telemetry.ts (imports getSecret for connection string)
        |
   src/health.ts (imports secrets loaded status)
        |
   src/index.ts (orchestrates all of the above)
        |
   Dockerfile (copies dist/ after build)
        |
   provision-agent.sh (runs az acr build using Dockerfile)
```

### 6.4 Implementation Order

**Phase 8 should implement in this order:**

1. **Project scaffolding:** `package.json`, `tsconfig.json`, `.dockerignore`
2. **Secret fetch module:** `src/secrets.ts` — the core pattern all other stories depend on
3. **Telemetry module:** `src/telemetry.ts` — the shared observability pattern
4. **Health endpoint:** `src/health.ts` — minimal HTTP server for ACI health checks
5. **Entrypoint:** `src/index.ts` — wires everything together, starts the server
6. **Dockerfile:** Build the image definition, verify it builds locally
7. **Provisioning script:** `provision-agent.sh` — deploy to Azure

Each file should be committed individually after its tests pass (Phase 7 will define the test suite).

### 6.5 npm Dependencies

```json
{
  "dependencies": {
    "@azure/identity": "^4.0.0",
    "@azure/keyvault-secrets": "^4.8.0",
    "applicationinsights": "^3.0.0",
    "botbuilder": "^4.23.0",
    "restify": "^11.0.0"
  },
  "devDependencies": {
    "@types/node": "^22.0.0",
    "@types/restify": "^8.5.0",
    "typescript": "^5.5.0",
    "vitest": "^2.0.0"
  }
}
```

Note: `botbuilder` is listed here because the health endpoint and server setup are part of STORY-001, but the full bot handler implementation is STORY-002. STORY-001 creates the runtime skeleton; STORY-002 adds the message handling logic.

---

## Appendix A: Teardown Script

```bash
#!/usr/bin/env bash
set -euo pipefail

AGENT_NAME="agent-dev"
RESOURCE_GROUP="rg-${AGENT_NAME}"

# Read the app ID before deleting the resource group
APP_ID=$(az ad app list --display-name "${AGENT_NAME}-bot" --query "[0].appId" --output tsv 2>/dev/null || echo "")

# Delete all Azure resources
echo ">>> Deleting resource group ${RESOURCE_GROUP}..."
az group delete --name "$RESOURCE_GROUP" --yes --no-wait

# Delete the Entra ID app registration (not in the resource group)
if [ -n "$APP_ID" ]; then
  echo ">>> Deleting Entra ID app registration..."
  az ad app delete --id "$APP_ID"
fi

echo ">>> Teardown initiated (resource group deletion is async)"
```

---

## Appendix B: Local Development

For local development without Azure, the secret fetch pattern degrades gracefully:

1. Run `az login` to authenticate the Azure CLI.
2. Set `KEYVAULT_URI` to point at a dev Key Vault (or the same one, with your user having `Key Vault Secrets User` role).
3. `DefaultAzureCredential` resolves to `AzureCliCredential` and fetches secrets using your CLI session.
4. The application code is identical between local dev and ACI production.

Alternatively, for offline development, set secrets as environment variables directly:

```bash
export KEYVAULT_URI=""  # empty string skips Key Vault
export ANTHROPIC_API_KEY="sk-ant-test-..."
export BOT_APP_ID="test-app-id"
export BOT_APP_PASSWORD="test-password"
export GITHUB_TOKEN="ghp_test..."
```

The `loadSecrets()` function should detect an empty `KEYVAULT_URI` and fall back to environment variables in development mode only (controlled by `NODE_ENV !== 'production'`).
