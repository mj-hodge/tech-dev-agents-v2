# Analysis: Container Runtime & Identity

> Phase 4 — Technical, Business & Risk Analysis
> Story: STORY-001 — Container Runtime & Identity
> Date: 2026-03-26
> Scope: Medium

---

## Recommendation Summary

**Use Azure Container Instances (ACI) with a system-assigned managed identity and an Entra ID app registration (service principal) for the bot identity.** Store all secrets in Azure Key Vault accessed via that managed identity. Build a custom Docker image with Node.js 22 + git + gh CLI + Claude Code CLI pre-installed and pushed to Azure Container Registry.

The existing Hermes provisioning script in `docs/hermes-prompt.md` provides a near-complete foundation. It can be adapted with three targeted changes: (1) replace the App Service deployment target with ACI, (2) convert secret injection from Key Vault reference syntax (App Settings) to SDK-based fetch at runtime, and (3) harden the Dockerfile for non-root execution.

| Decision | Recommendation | Runner-up |
|----------|---------------|-----------|
| Compute platform | ACI (serverless containers) | App Service Container |
| Bot identity type | App registration + service principal | N/A (managed identity can't be a Teams user) |
| Azure resource auth | System-assigned managed identity on ACI | User-assigned managed identity |
| Key Vault access | Managed identity RBAC (Secrets User role) | Access policies (legacy) |
| Image registry | Azure Container Registry (Basic SKU) | Docker Hub (not recommended: no private registry at free tier) |

---

## 1. Technical Analysis

### 1.1 Compute Platform Comparison

#### Azure Container Instances (ACI)

ACI is serverless container hosting billed per second of CPU/memory allocation. A container group runs a single pod-equivalent unit. There is no infrastructure to manage — no OS patching, no plan SKU selection, no scaling configuration.

**Strengths for this use case:**
- Consumption billing: you pay only when the container is running. If the agent sleeps between tasks (no inbound HTTP needed 24/7), you can stop the container instance and incur $0 in idle time.
- True container isolation: each ACI container group has its own network namespace, process space, and filesystem. Perfect fit for the isolation requirement.
- Managed identity support: ACI supports system-assigned managed identities natively (preview as of late 2024, GA in 2025). The identity is bound to the container group, not to an underlying VM.
- Cold start: 15–40 seconds for a pre-built image pulled from ACR in the same region. This is within the < 60s requirement but is the primary trade-off against App Service.
- Simple provisioning: a single `az container create` command deploys the container. No plan, no app, no deployment slot.

**Weaknesses:**
- No persistent filesystem by default. A container restart loses any in-progress workspace. Azure Files mounts are supported but add latency and complexity.
- No built-in continuous deployment hook (App Service has this). Redeploy requires `az container delete` + `az container create`, or an image tag update with restart.
- Managed identity on ACI requires the container group to be created with the `--assign-identity` flag and the `Microsoft.ContainerInstance/containerGroups` resource to be configured at creation time.

#### App Service Container

App Service with a custom Docker image runs a container on a dedicated App Service Plan (Linux). The Hermes provisioning script uses this model (B1 SKU).

**Strengths:**
- Key Vault reference syntax (`@Microsoft.KeyVault(SecretUri=...)`) in App Settings resolves secrets automatically — no SDK code needed to fetch from Key Vault at runtime.
- Built-in continuous deployment from ACR via webhook.
- Persistent `/home` filesystem across restarts (Azure Blob-backed).
- Always-warm: no cold starts after the first boot.

**Weaknesses:**
- Fixed monthly cost regardless of usage. B1 = ~$13/month, S1 = ~$70/month. For a bot that is idle most of the time, this is waste.
- Shared-plan risk: other apps on the same plan compete for CPU/memory burst.
- Container isolation is weaker than ACI: while the container is isolated from other apps, the underlying VM is shared infrastructure.
- Slightly heavier operational model: plan SKU, deployment slots, scaling rules.

#### Docker on a VM (Azure VM with Docker)

Running a VM and managing Docker manually is the highest-control, highest-overhead option.

**Strengths:**
- Full control: persistent disk, custom networking, any software stack.
- No container cold start: Docker containers restart in seconds once the VM is running.

**Weaknesses:**
- Fixed cost: B2s VM (~$35/month) + OS disk storage (~$5/month) = ~$40/month minimum.
- Operational burden: OS patching, Docker daemon updates, manual scaling, SSH key management.
- No native managed identity without IMDS configuration (available but non-trivial to configure for container workloads).
- Overkill for a stateless, API-calling process. The agent does not need persistent local GPU or unusual system libraries.

#### Comparison Matrix

| Dimension | ACI | App Service Container | Docker on VM |
|-----------|-----|----------------------|--------------|
| Cost (idle) | $0 (stopped) | ~$13–70/mo fixed | ~$40/mo fixed |
| Cost (active, 8h/day) | ~$3–8/mo | ~$13–70/mo | ~$40/mo |
| Cold start | 15–40s | 0s (always warm) | 2–5s (container restart) |
| Isolation | Strong (own group) | Moderate (shared plan infra) | Strong (own VM) |
| Managed identity | Yes (system-assigned) | Yes (system-assigned) | Requires IMDS config |
| Persistent filesystem | No (Azure Files mount optional) | Yes (/home) | Yes (VM disk) |
| Provisioning complexity | Low | Medium | High |
| Solo developer ops burden | Low | Low-Medium | High |
| Fits v1 scale (1 bot) | Yes | Yes | Over-engineered |
| Fits fleet scale (3-5 bots) | Yes (independent groups) | Yes (shared plan) | Expensive |

**Decision: ACI.** The agent's workload is inherently bursty — it is active during task execution and idle between tasks. ACI's consumption billing makes it the lowest-cost option for this profile. The 15–40s cold start is acceptable given the < 60s requirement, and the agent can be kept warm by a lightweight health-check ping if needed. Workspace persistence is handled by cloning the target repo at task start (already the pattern in the Hermes design — "clone at startup").

---

### 1.2 Entra ID Identity Options

The agent needs two distinct identities that serve different purposes:

1. **Bot identity** (Teams presence): needs an email address, a Teams user/bot account, and the ability to send/receive Teams messages. This is a user-facing identity visible in the Teams directory.
2. **Azure resource identity** (Key Vault, ACR access): needs to authenticate to Azure control plane and data plane APIs without a password baked into the container.

These are separate concerns and are best served by separate identity mechanisms.

#### Option A: App Registration + Service Principal (recommended for bot identity)

An Entra ID app registration creates an application object with an associated service principal. It can be granted a client secret (password) or certificate credential. This is the standard identity for Bot Framework bots.

For the Teams presence, a separate Entra ID **user account** (a standard M365 user like `agent-dev@yourtenant.com`) is required. The app registration authenticates the bot channel; the user account provides the Teams email and presence. This is the pattern Hermes uses.

**Why not a managed identity for the bot identity?** Managed identities are Azure-resource-bound identities that exist only as long as the resource exists. They cannot be assigned an email address or given a Teams presence. They are the right tool for Azure resource access (Key Vault, ACR), not for Teams user identity.

#### Option B: System-Assigned Managed Identity (recommended for Azure resource access)

A system-assigned managed identity is automatically created when ACI is provisioned with `--assign-identity`. Its lifecycle is tied to the container group. It authenticates to Azure APIs via the Instance Metadata Service (IMDS) endpoint at `169.254.169.254` — no secret required, no rotation needed.

**Strengths:**
- Zero credential management: Azure rotates the underlying certificate automatically.
- Principal is deleted when the container group is deleted: no orphaned service principals.
- Directly grants Key Vault Secrets User role scoped to the vault.
- Supported by the `@azure/identity` SDK's `ManagedIdentityCredential` — simple to use in Node.js.

**Weaknesses:**
- Tied to one specific ACI resource. If the container group is deleted and recreated, the identity principal ID changes and RBAC assignments must be re-granted. This is a minor operational consideration for v1.

#### Option C: User-Assigned Managed Identity

A user-assigned managed identity is a standalone Azure resource. It persists independently of any compute resource and can be pre-assigned RBAC roles before the container is created.

**Strengths:**
- Identity and its RBAC assignments survive container group deletion/recreation.
- Can be shared across multiple ACI instances (useful for fleet scale).

**Weaknesses:**
- Additional resource to manage (one more Azure object per bot or per fleet).
- Slightly more complex provisioning (create identity resource, then reference it at container creation).

**Decision:** System-assigned managed identity for v1 simplicity. The risk of losing RBAC assignments on recreation is mitigated by including role assignment in the provisioning script (idempotent via `az role assignment create`). If the container group is recreated frequently, migrate to user-assigned at fleet scale.

#### Summary

| Identity Purpose | Mechanism | Notes |
|-----------------|-----------|-------|
| Teams bot (channel auth) | Entra ID app registration + client secret in Key Vault | Standard Bot Framework pattern |
| Teams user presence (email) | Entra ID user account (M365 license required) | Separate from app registration |
| Azure resource access (Key Vault, ACR) | System-assigned managed identity on ACI | Zero-credential, auto-rotated |

---

### 1.3 Key Vault Integration Patterns

#### Pattern 1: SDK Fetch at Startup (recommended for ACI)

The container fetches secrets from Key Vault at startup using the `@azure/keyvault-secrets` and `@azure/identity` SDKs. The managed identity credential is used automatically:

```typescript
import { DefaultAzureCredential } from '@azure/identity';
import { SecretClient } from '@azure/keyvault-secrets';

const credential = new DefaultAzureCredential();
const client = new SecretClient(process.env.KEYVAULT_URI, credential);

const anthropicKey = await client.getSecret('anthropic-api-key');
```

`DefaultAzureCredential` checks multiple sources in order (environment, workload identity, managed identity, CLI). In ACI with a managed identity, it resolves to `ManagedIdentityCredential` automatically.

**Why not Key Vault reference syntax (App Settings)?** That pattern is exclusive to App Service. ACI does not support the `@Microsoft.KeyVault(SecretUri=...)` syntax in environment variables. For ACI, the SDK pattern is the correct approach.

#### Pattern 2: Sidecar Container (not recommended for v1)

A separate sidecar container can fetch secrets and write them to a shared volume. This pattern is used in Kubernetes with the CSI Secrets Store driver. It is over-engineered for a single-container ACI group.

#### RBAC Configuration

The provisioning script should grant the managed identity the `Key Vault Secrets User` built-in role scoped to the vault. This grants `get` and `list` on secrets — the minimum required for the agent to read its secrets. It does not allow creating or deleting secrets.

```bash
az role assignment create \
  --role "Key Vault Secrets User" \
  --assignee <managed-identity-principal-id> \
  --scope <keyvault-resource-id>
```

This matches exactly what the Hermes provisioning script does for the App Service identity (Step 4 in the script). The same command applies for ACI with the container group's managed identity principal ID.

---

### 1.4 Container Image Strategy

#### Base Image

`node:22-slim` (Debian slim) is the correct base. It provides:
- Node.js 22 LTS (required for the bot runtime and Claude Code CLI)
- `npm` for installing Claude Code
- A minimal Debian layer (~75MB compressed) that supports `apt-get` for installing git and gh CLI

Alternatives considered:
- `node:22-alpine`: smaller (~50MB) but `gh` CLI installation is more complex on Alpine (no official Debian package). The size savings do not justify the complexity for a < 2GB target.
- `ubuntu:22.04`: larger base with more pre-installed packages, not needed.
- `node:22` (full): includes build tools not needed at runtime; adds ~200MB unnecessarily.

#### Layer Caching Strategy

Order layers from least-frequently-changed to most-frequently-changed:

1. Base OS layer (node:22-slim) — never changes
2. System packages (git, gh CLI via apt) — changes only when updating tool versions
3. Claude Code CLI (npm install -g) — changes on CLI version updates
4. `package.json` + `package-lock.json` COPY + `npm ci` — changes when dependencies change
5. Application source (COPY dist/) — changes on every build

This ordering ensures the expensive `npm install -g @anthropic-ai/claude-code` step is cached unless the layer before it changes.

#### Size Optimization

Starting from the Hermes Dockerfile in `docs/hermes-prompt.md` Section 9 and applying hardening:

```dockerfile
FROM node:22-slim

# Install system dependencies in one layer, clean apt cache
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      git \
      curl \
      gnupg && \
    curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | \
      gpg --dearmor -o /usr/share/keyrings/githubcli-archive-keyring.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] \
      https://cli.github.com/packages stable main" | \
      tee /etc/apt/sources.list.d/github-cli.list > /dev/null && \
    apt-get update && \
    apt-get install -y --no-install-recommends gh && \
    rm -rf /var/lib/apt/lists/*

# Install Claude Code CLI globally
RUN npm install -g @anthropic-ai/claude-code@latest

# Create non-root user
RUN groupadd -r agent && useradd -r -g agent -m agent

# App dependencies
WORKDIR /app
COPY package*.json ./
RUN npm ci --production && npm cache clean --force

# Application code
COPY dist/ ./dist/

# Workspace directory for repo operations
RUN mkdir -p /workspace && chown agent:agent /workspace

ENV REPO_PATH=/workspace
ENV PORT=3978

USER agent

EXPOSE 3978
CMD ["node", "dist/index.js"]
```

**What the Hermes Dockerfile is missing (hardening gaps):**
1. Non-root user — the Hermes example runs as root. The seed requires non-root.
2. `gh` CLI installation — the Hermes example uses `apt-get install -y git gh` which requires the GitHub CLI apt repository to be configured first. The above corrects this.
3. `npm cache clean --force` after `npm ci` — reduces image size.
4. `--no-install-recommends` on `apt-get` — avoids pulling in optional packages.

Expected image size: ~700–900MB uncompressed, ~300–400MB compressed in ACR. Well within the < 2GB limit.

---

### 1.5 Reuse from Hermes Provisioning Script

The `docs/hermes-prompt.md` provisioning script (`provision-hermes.sh`) provides substantial reusable infrastructure. The following table maps each step to its STORY-001 applicability:

| Script Step | Hermes Action | STORY-001 Reuse | Delta |
|-------------|--------------|-----------------|-------|
| Step 1 | Create resource group | Reuse as-is | None |
| Step 2 | Entra ID app registration + client secret | Reuse as-is | None — this is the bot identity pattern |
| Step 3 | Key Vault + RBAC + store secrets | Reuse as-is | Add `github-token` is already included |
| Step 4 | App Service Plan + Web App | Replace with ACI + ACR | Different compute target |
| Step 4 (identity) | `az webapp identity assign` | Replace with `--assign-identity` on `az container create` | ACI flag syntax differs |
| Step 4 (RBAC) | Grant App Service identity Secrets User | Same RBAC grant, different principal ID | Use ACI container group's principalId |
| Step 5 | App Settings with KV references | Remove — ACI doesn't support this | Use SDK fetch pattern instead |
| Step 6 | Azure Bot Service registration | Reuse as-is | None |
| Step 7 | Enable Teams channel | Reuse as-is | None |
| Step 8 | Workspace clone guidance | Adapt — clone in container startup, not SSH | Script startup command, not manual SSH |

**Summary:** Steps 1–3, 6–7 are direct reuse. Step 4 is replaced with ACR + ACI provisioning. Steps 5 and 8 are adapted for ACI's different model.

The Hermes Dockerfile (Section 9) provides the foundation for the container image with the hardening additions noted above.

---

## 2. Business Analysis

### 2.1 Cost Comparison at v1 Scale (1 bot, light usage)

**Assumptions:**
- Agent is active ~4 hours/day on average (task execution + standby)
- ACI sizing: 1 vCPU, 2GB RAM (sufficient for Node.js + Claude Code CLI)
- App Service B1: 1 vCPU, 1.75GB RAM (~$13.14/month in East US)

#### ACI Monthly Cost Estimate

ACI pricing (East US, as of 2026):
- vCPU: ~$0.0000135/vCPU-second
- Memory: ~$0.0000015/GB-second

At 1 vCPU + 2GB RAM running 4 hours/day:
- vCPU: 0.0000135 × 1 vCPU × (4h × 3600s) × 30 days = ~$5.83/month
- Memory: 0.0000015 × 2GB × (4h × 3600s) × 30 days = ~$1.30/month
- **ACI compute total: ~$7.13/month**

#### App Service Container Monthly Cost

- B1 plan (Linux): ~$13.14/month fixed regardless of usage
- No variable component for compute

#### Docker on VM Monthly Cost

- B2s VM (2 vCPU, 4GB RAM, East US): ~$35.04/month
- OS managed disk (32GB): ~$1.54/month
- **VM total: ~$36.58/month**

#### Shared Resources (same for all options)

| Resource | Monthly Cost |
|----------|-------------|
| Key Vault (secrets storage + operations) | ~$0.03–0.10 |
| Azure Container Registry Basic | ~$5.00 |
| Azure Bot Service (Teams channel) | Free (F0) |
| Application Insights (5GB/day free tier) | $0 for v1 volume |
| Log Analytics Workspace | $0 for < 5GB/month |

#### v1 Cost Summary

| Option | Compute | Shared | Total/Month |
|--------|---------|--------|------------|
| ACI (4h/day active) | ~$7.13 | ~$5.13 | **~$12.26** |
| ACI (8h/day active) | ~$14.26 | ~$5.13 | **~$19.39** |
| App Service B1 | ~$13.14 | ~$5.13 | **~$18.27** |
| Docker on VM | ~$36.58 | ~$5.13 | **~$41.71** |

ACI is cheapest at light usage (< ~7h/day active). Above ~10h/day active, App Service B1 becomes cost-competitive. For a solo developer's bot running light-to-moderate workloads, ACI wins.

---

### 2.2 Cost at Fleet Scale (3–5 bots)

#### ACI Fleet (3 bots, 4h/day each)

- 3 × ~$7.13 compute = ~$21.39/month
- Shared Key Vault (one vault, prefixed secrets): ~$0.10/month
- Shared ACR Basic: ~$5.00/month
- Shared App Insights workspace: ~$0/month (low volume)
- **Total: ~$26.49/month**

#### App Service Fleet (3 bots, shared S1 plan)

The Hermes design (Section 14b) recommends a shared App Service Plan. S1 (2 vCPU, 3.5GB) supports multiple apps:
- S1 shared plan: ~$69.35/month
- Shared Key Vault + ACR + App Insights: ~$5.10/month
- **Total: ~$74.45/month**

Even splitting 3 bots on a shared plan, App Service is 2.8× more expensive than ACI at this scale. ACI's per-bot isolation also means one bot's heavy load cannot starve another.

| Fleet Size | ACI Total | App Service Total | VM Total |
|-----------|-----------|-------------------|----------|
| 1 bot | ~$12.26 | ~$18.27 | ~$41.71 |
| 3 bots | ~$26.49 | ~$74.45 | ~$115.00 |
| 5 bots | ~$43.00 | ~$124.00+ | ~$190.00 |

ACI scales linearly with bot count and usage. App Service requires SKU upgrades at fleet scale (S1/P1v2) that significantly increase the cost floor.

---

### 2.3 Operational Complexity for a Solo Developer

| Concern | ACI | App Service |
|---------|-----|-------------|
| Initial provisioning | `az container create` (one command) | Multiple steps (plan, app, identity, settings) |
| Redeployment | Delete + recreate container group | `az webapp deployment source config-zip` |
| Secret updates | `az keyvault secret set` + container restart | `az keyvault secret set` (auto-refreshed by App Settings) |
| Log access | `az container logs` or Log Analytics | `az webapp log tail` or App Insights |
| SSH / exec into container | `az container exec` | `az webapp ssh` |
| Monitoring | Container metrics in Azure Monitor | App Service metrics + App Insights |
| Persistent workspace | Requires Azure Files mount config | Built-in /home persistence |

Both options are manageable for a solo developer. App Service has a slight edge in ergonomics (secret auto-refresh via KV references, built-in deployment hooks). ACI requires the developer to handle the startup secret fetch in code, but this is a one-time implementation.

The cost advantage of ACI justifies the slightly higher implementation effort for the secret fetch pattern.

---

## 3. Risk Analysis

### 3.1 Cold Start Latency Impact on User Experience

**Risk level: Medium**

ACI cold start involves: pulling the container image from ACR + allocating the container group + Node.js process startup. For a pre-built image stored in ACR in the same region:

- Image pull (first time, ~400MB compressed): 10–20 seconds
- Image pull (subsequent, cached at host): 2–5 seconds
- Container group allocation: 5–15 seconds
- Node.js startup + secret fetch: 2–5 seconds

**Total cold start estimate: 15–40 seconds** (within the < 60s requirement).

For the Teams bot use case, this cold start only occurs when the container has been stopped between tasks. The Teams Bot Framework will receive the inbound message and attempt to POST it to the bot's messaging endpoint. If the container is stopped, the POST will fail with a timeout, and the Bot Service will retry. During this window, the user sees no acknowledgment.

**Mitigations:**
1. Keep the container running (don't stop between tasks). ACI billing for a 1 vCPU/2GB instance running 24/7 is ~$43/month — a legitimate trade-off if cold start UX is unacceptable.
2. Use a "warm ping" pattern: a lightweight Azure Function or Logic App pings the bot's health endpoint every 10 minutes, keeping it in a warm state at minimal cost (~$0.20/month for a Logic App recurrence trigger).
3. Accept cold starts: if task assignments are not time-critical (the developer schedules work, not real-time requests), a 30–40 second cold start is acceptable.

**Recommendation:** Start with the container always running (24/7) for v1 simplicity. The cost (~$43/month) is acceptable for a solo developer validating the system. Optimize to stop-when-idle in v2 once the workflow is proven.

---

### 3.2 Secret Rotation and Credential Lifecycle

**Risk level: Medium**

The agent uses three categories of secrets, each with different rotation requirements:

| Secret | Type | Rotation Requirement | Risk if Expired |
|--------|------|---------------------|-----------------|
| Anthropic API key | Long-lived API key | Manual rotation; Anthropic does not expire keys on a schedule | Agent stops functioning; blocked tasks |
| GitHub Personal Access Token | PAT (90-day expiry enforced by GitHub) | Must rotate every 90 days | Git operations fail; PRs cannot be opened |
| Bot app password (client secret) | Entra ID credential (1–2 year validity) | Must rotate before expiry | Bot cannot authenticate to Teams; all messaging breaks |

**Mitigations:**

1. **GitHub PAT**: Switch from a PAT to a GitHub App installation token. GitHub Apps use short-lived tokens (1-hour JWT) generated from a private key stored in Key Vault. The private key has no built-in expiry. This eliminates the 90-day rotation ceremony. Implementation is more complex but is the correct long-term pattern.

2. **Bot app password**: Create with 2-year validity (as the Hermes script does). Set a calendar reminder or Azure Automation runbook to alert 60 days before expiry. The new password can be stored in Key Vault and the container restarted to pick it up.

3. **Anthropic API key**: No automatic rotation mechanism exists. Document the key name and location. Set a Key Vault expiry date alert (Azure Monitor alert on `SecretNearExpiry` event) as a reminder to review — even if Anthropic does not expire the key, this ensures the developer checks it periodically.

4. **Container restart on secret update**: Unlike App Service (which auto-refreshes KV references), ACI requires a container restart for the new secret to be fetched. This is a brief outage (~30-40 seconds cold start). For v1, this is acceptable. For v2, implement a SIGHUP handler or runtime secret refresh interval.

---

### 3.3 Container Persistence (Stateless vs. Workspace Persistence)

**Risk level: Medium**

The agent's container is inherently stateless in the ACI model: the ephemeral filesystem is lost on restart. This affects two categories of data:

**In-progress work (git workspace):**

The agent clones a target repo into `/workspace` at task start. If the container is restarted mid-task, the workspace is lost and the task must restart from scratch. This is a significant risk for long-running SDLC phases (Phase 8 implementation can take hours).

Mitigations:
1. **Azure Files mount**: Mount an Azure Files share to `/workspace`. This provides persistent storage that survives container restarts. Cost: ~$0.06/GB/month (Standard tier). A 5GB share = ~$0.30/month — negligible. Latency for git operations over Azure Files (SMB) is higher than local disk but acceptable for the agent's use case.
2. **Checkpoint-and-resume**: Store task state (current phase, completed steps, branch name) in an external store (Cosmos DB or a JSON file in Azure Blob Storage) after each phase. On restart, the agent reads the checkpoint and resumes. This is the correct long-term pattern (referenced in the epic seed's risk table).
3. **Accept restart risk for v1**: If the container is kept running 24/7 (recommendation from §3.1), restart events are rare (only on redeploy or ACI host maintenance). For v1, document the restart behavior and rely on the developer to re-assign interrupted tasks.

**Session state (Claude Code sessions, Teams conversation context):**

The Hermes design uses an in-memory session map (`Map<string, string>`) for conversation threading. This is lost on restart. For v1, this is acceptable — restarted conversations simply lose multi-turn context. For v2, persist the session map to Redis Cache or Azure Cosmos DB.

**Recommendation for v1:** Keep the container running 24/7 (avoiding restarts), accept restart risk, and document it. Add Azure Files workspace persistence as a fast follow in STORY-003 (Claude Code Runner), which will need workspace persistence for multi-phase execution anyway.

---

### 3.4 Entra ID Teams-Enablement Requirements

**Risk level: Low–Medium (licensing dependency)**

For the agent to have a Teams presence (be messageable by users, appear in the Teams directory), it requires either:

**Option A: M365 user account + Bot Framework app registration (recommended)**

A standard Entra ID user account (`agent@yourtenant.com`) provides the Teams presence (email, display name, profile photo). The user account must have an M365 license that includes Teams (M365 Business Basic at minimum: ~$6/user/month).

The bot authentication uses the separate app registration (client secret), not the user account credentials. The user account exists solely to provide the Teams identity and is not used for API authentication.

**Option B: Bot-only registration (no user account)**

An Azure Bot Service + app registration can create a bot that appears in Teams channels without a licensed user account. This works for bots added to channels or group chats but limits the agent to bot interactions (cannot send direct messages to users who haven't first interacted with the bot, cannot appear in the people directory).

For the v1 use case (the developer messages the bot directly, the bot sends updates back), Option B is likely sufficient and avoids the licensing cost. Teams users can search for the bot by its registered name and add it to conversations.

**Entra ID App Registration Permissions (minimum required):**

For a Bot Framework bot to receive and send Teams messages, the app registration needs:

| Permission | Type | Purpose |
|-----------|------|---------|
| `User.Read` | Delegated | Read the calling user's profile (optional, for user context) |
| None beyond Bot Service | — | The Bot Service channel handles Teams auth; the app registration is used for channel authentication only |

No elevated Graph API permissions are required for basic message send/receive. Additional permissions would be needed for future stories (e.g., reading meeting calendars for STORY-007 approval flows).

**Teams App Manifest Sideloading vs. Admin Approval:**

For a single-developer tenant, the Teams app can be sideloaded (uploaded directly in Teams client) without IT admin approval. For a corporate tenant, sideloading may be restricted by Teams Admin Center policy. If restricted, the app must be submitted to the org's Teams app catalog for admin approval before deployment.

**Action required before Phase 6:** Verify whether the target tenant allows Teams app sideloading. If not, identify the admin approval process.

---

## 4. Open Questions Resolved

| Question from Seed | Resolution |
|-------------------|-----------|
| ACI vs App Service containers — which better fits a long-running agent process with consumption billing? | ACI: better cost profile at v1 and fleet scale; acceptable cold start; consumption billing fits bursty usage |
| Should the Entra ID service account be a standard user or an app registration with service principal? | Both: app registration for bot channel auth + Azure resource access (via managed identity); optional M365 user account for Teams directory presence |
| What specific Graph API permissions does the Teams-enabled identity need at minimum? | None beyond Bot Service channel registration for basic message send/receive; no elevated Graph permissions needed for v1 |

---

## 5. Recommended Approach

### Architecture Decision

```
Azure Container Registry (Basic)
  └── hermes-bot:latest (Docker image)
        ├── node:22-slim base
        ├── git + gh CLI
        └── claude-code CLI (@latest)

Azure Container Instances
  └── Container Group: hermes-agent
        ├── System-assigned managed identity
        ├── 1 vCPU / 2 GB RAM
        ├── Port 3978 (bot messaging endpoint)
        └── KEYVAULT_URI env var (only non-secret config)

Azure Key Vault
  ├── anthropic-api-key
  ├── bot-app-id
  ├── bot-app-password
  └── github-token

Entra ID
  ├── App Registration: agent-bot (client ID + secret in KV)
  └── System-assigned managed identity on ACI → KV Secrets User role

Azure Bot Service
  └── F0 (free) Teams channel registration
        └── Endpoint: https://<aci-fqdn>/api/messages
```

### Provisioning Script Adaptation Plan

Start from `docs/hermes-prompt.md` Steps 1–8 and make these targeted changes:

1. **Replace Steps 4–5** (App Service) with:
   - `az acr create` for container registry
   - `az acr build` for image build
   - `az container create` with `--assign-identity` for ACI deployment
   - RBAC grant using ACI container group's managed identity principal ID

2. **Replace Step 5** (App Settings KV references) with:
   - Single env var: `KEYVAULT_URI` (not a secret, safe as plain env var)
   - All secrets fetched at runtime via `@azure/keyvault-secrets` + `DefaultAzureCredential`

3. **Keep Steps 1–3, 6–7** unchanged (resource group, app registration, Key Vault, Bot Service, Teams channel).

4. **Add** `--dns-name-label` to ACI for a stable FQDN.

5. **Add** managed identity principal ID lookup + RBAC grant after container group creation.

### Secret Access Implementation (Node.js)

```typescript
// src/secrets.ts
import { DefaultAzureCredential } from '@azure/identity';
import { SecretClient } from '@azure/keyvault-secrets';

let secrets: Record<string, string> = {};

export async function loadSecrets(): Promise<void> {
  const uri = process.env.KEYVAULT_URI;
  if (!uri) throw new Error('KEYVAULT_URI not set');

  const client = new SecretClient(uri, new DefaultAzureCredential());

  const names = ['anthropic-api-key', 'bot-app-id', 'bot-app-password', 'github-token'];
  const fetched = await Promise.all(names.map(n => client.getSecret(n)));

  secrets['anthropic-api-key'] = fetched[0].value!;
  secrets['bot-app-id'] = fetched[1].value!;
  secrets['bot-app-password'] = fetched[2].value!;
  secrets['github-token'] = fetched[3].value!;
}

export function getSecret(name: string): string {
  const val = secrets[name];
  if (!val) throw new Error(`Secret not loaded: ${name}`);
  return val;
}
```

Called once at container startup before the bot listener is initialized. All downstream code uses `getSecret()` rather than `process.env`.

### v1 Operating Model

| Decision | v1 Setting | Rationale |
|----------|-----------|-----------|
| Container uptime | 24/7 | Avoid cold start UX issues; simplest operational model |
| Workspace persistence | Ephemeral (restart = fresh clone) | Acceptable for v1; Azure Files mount in STORY-003 |
| Secret refresh | Restart required | Acceptable for v1; rare rotation events |
| Bot Teams presence | App registration only (no licensed user) | Avoids licensing cost; bot-mode is sufficient for v1 |
| Sideloading | Developer sideload | Single-user tenant; no admin approval needed |
