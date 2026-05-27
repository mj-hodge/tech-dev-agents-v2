# Hermes Bot — Azure-hosted Teams Bot for Claude Code

## 1. Architecture Summary

```
Teams User ─→ Azure Bot Service ─→ Hermes (App Service)
                                        │
                                  Command Router
                                   ┌────┴────┐
                              Claude Code    Webhook Triggers
                              (CLI -p)       (GH Actions / ADO)
                                   │
                              Anthropic API
                                   │
                              Azure Key Vault (secrets)
```

---

## 2. Prerequisites

```bash
# Azure CLI installed and logged in
az login
az account set -s "<your-subscription-id>"

# Node.js 22 LTS
node --version  # v22.x

# You'll need these values ready:
#   - Your Anthropic API key
#   - A GitHub personal access token (for CI/CD triggers)
```

---

## 3. Full Azure CLI Provisioning Script

Save this as `provision-hermes.sh` and run it. Every Azure resource is created via CLI — no portal clicks needed.

```bash
#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# CONFIGURATION — edit these values
# ============================================================
RESOURCE_GROUP="rg-hermes"
LOCATION="eastus"
BOT_NAME="hermes-bot"                       # 4-42 chars, alphanumeric + hyphens
APP_SERVICE_PLAN="plan-hermes"
APP_SERVICE_NAME="app-hermes-bot"            # globally unique, becomes <name>.azurewebsites.net
KEYVAULT_NAME="kv-hermes"                    # 3-24 chars, globally unique
ANTHROPIC_API_KEY="sk-ant-..."               # your Anthropic API key
GITHUB_TOKEN="ghp_..."                       # for CI/CD webhook triggers
SKU="B1"                                     # B1 is fine for small teams; scale to S1/P1v2 later

# ============================================================
# STEP 1: Resource Group
# ============================================================
echo ">>> Creating resource group..."
az group create \
  --name "$RESOURCE_GROUP" \
  --location "$LOCATION"

# ============================================================
# STEP 2: Entra ID App Registration (bot identity)
# ============================================================
echo ">>> Creating Entra ID app registration..."
APP_REG=$(az ad app create \
  --display-name "$BOT_NAME" \
  --sign-in-audience "AzureADMultipleOrgs" \
  --query "{appId:appId, id:id}" \
  --output json)

APP_ID=$(echo "$APP_REG" | jq -r '.appId')
OBJECT_ID=$(echo "$APP_REG" | jq -r '.id')
echo "    App ID: $APP_ID"

# Create a client secret (valid 2 years)
APP_PASSWORD=$(az ad app credential reset \
  --id "$APP_ID" \
  --years 2 \
  --query "password" \
  --output tsv)
echo "    App password created (stored in Key Vault next)"

# ============================================================
# STEP 3: Key Vault + Secrets
# ============================================================
echo ">>> Creating Key Vault..."
az keyvault create \
  --name "$KEYVAULT_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --location "$LOCATION" \
  --enable-rbac-authorization true

# Get current user's object ID for RBAC
CURRENT_USER_OID=$(az ad signed-in-user show --query id --output tsv)

# Assign Key Vault Secrets Officer role to yourself
az role assignment create \
  --role "Key Vault Secrets Officer" \
  --assignee "$CURRENT_USER_OID" \
  --scope "$(az keyvault show --name $KEYVAULT_NAME --query id --output tsv)"

# Wait for RBAC propagation
echo "    Waiting for RBAC propagation..."
sleep 30

echo ">>> Storing secrets in Key Vault..."
az keyvault secret set --vault-name "$KEYVAULT_NAME" --name "anthropic-api-key"  --value "$ANTHROPIC_API_KEY"
az keyvault secret set --vault-name "$KEYVAULT_NAME" --name "bot-app-id"         --value "$APP_ID"
az keyvault secret set --vault-name "$KEYVAULT_NAME" --name "bot-app-password"   --value "$APP_PASSWORD"
az keyvault secret set --vault-name "$KEYVAULT_NAME" --name "github-token"       --value "$GITHUB_TOKEN"

# ============================================================
# STEP 4: App Service Plan + Web App
# ============================================================
echo ">>> Creating App Service plan..."
az appservice plan create \
  --name "$APP_SERVICE_PLAN" \
  --resource-group "$RESOURCE_GROUP" \
  --sku "$SKU" \
  --is-linux

echo ">>> Creating Web App (Node.js 22)..."
az webapp create \
  --name "$APP_SERVICE_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --plan "$APP_SERVICE_PLAN" \
  --runtime "NODE:22-lts"

# Enable system-assigned managed identity
echo ">>> Enabling managed identity..."
IDENTITY_PRINCIPAL_ID=$(az webapp identity assign \
  --name "$APP_SERVICE_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --query "principalId" \
  --output tsv)

# Grant the App Service identity access to Key Vault secrets
az role assignment create \
  --role "Key Vault Secrets User" \
  --assignee "$IDENTITY_PRINCIPAL_ID" \
  --scope "$(az keyvault show --name $KEYVAULT_NAME --query id --output tsv)"

# ============================================================
# STEP 5: Configure App Settings
# ============================================================
echo ">>> Setting app configuration..."
KV_URI="https://${KEYVAULT_NAME}.vault.azure.net"

az webapp config appsettings set \
  --name "$APP_SERVICE_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --settings \
    KEYVAULT_URI="$KV_URI" \
    MicrosoftAppId="$APP_ID" \
    MicrosoftAppPassword="@Microsoft.KeyVault(SecretUri=${KV_URI}/secrets/bot-app-password)" \
    ANTHROPIC_API_KEY="@Microsoft.KeyVault(SecretUri=${KV_URI}/secrets/anthropic-api-key)" \
    GITHUB_TOKEN="@Microsoft.KeyVault(SecretUri=${KV_URI}/secrets/github-token)" \
    WEBSITE_NODE_DEFAULT_VERSION="~22" \
    SCM_DO_BUILD_DURING_DEPLOYMENT="true" \
    REPO_PATH="/home/site/workspace"

# Set startup command
az webapp config set \
  --name "$APP_SERVICE_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --startup-file "node dist/index.js"

# ============================================================
# STEP 6: Azure Bot Service (channel registration)
# ============================================================
echo ">>> Creating Azure Bot resource..."
MESSAGING_ENDPOINT="https://${APP_SERVICE_NAME}.azurewebsites.net/api/messages"

az bot create \
  --resource-group "$RESOURCE_GROUP" \
  --name "$BOT_NAME" \
  --kind "registration" \
  --appid "$APP_ID" \
  --password "$APP_PASSWORD" \
  --endpoint "$MESSAGING_ENDPOINT" \
  --sku "F0"

# ============================================================
# STEP 7: Enable Teams Channel
# ============================================================
echo ">>> Enabling Microsoft Teams channel..."
az bot msteams create \
  --name "$BOT_NAME" \
  --resource-group "$RESOURCE_GROUP"

# ============================================================
# STEP 8: Clone your target repo into the App Service
# ============================================================
echo ">>> Setting up workspace (SSH into App Service to clone repo)..."
# Option A: Clone at startup via a custom startup script
# Option B: Mount Azure Files share with pre-cloned repo
# For now, we'll use the post-deployment SSH approach:
echo "    After deploying your bot code, SSH in and clone your target repo:"
echo "    az webapp ssh --name $APP_SERVICE_NAME --resource-group $RESOURCE_GROUP"
echo "    Then: cd /home/site && git clone <your-repo-url> workspace"

# ============================================================
# DONE
# ============================================================
echo ""
echo "============================================"
echo "  Hermes provisioning complete!"
echo "============================================"
echo "  Resource Group:    $RESOURCE_GROUP"
echo "  App Service:       https://${APP_SERVICE_NAME}.azurewebsites.net"
echo "  Bot Endpoint:      $MESSAGING_ENDPOINT"
echo "  Key Vault:         $KV_URI"
echo "  Bot App ID:        $APP_ID"
echo ""
echo "  Next steps:"
echo "    1. Deploy your bot code (see Section 4 below)"
echo "    2. Install Claude Code on the App Service"
echo "    3. Upload the Teams app manifest"
echo "    4. Test: @hermes explain the main entry point"
echo "============================================"
```

---

## 4. Deploy Bot Code to App Service

After provisioning, deploy your Hermes bot code:

```bash
# From your hermes-bot project directory:
# Build
npm run build

# Zip deploy to App Service
az webapp deployment source config-zip \
  --name "$APP_SERVICE_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --src ./deploy.zip

# Or use continuous deployment from GitHub:
az webapp deployment source config \
  --name "$APP_SERVICE_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --repo-url "https://github.com/yourorg/hermes-bot" \
  --branch "main" \
  --git-token "$GITHUB_TOKEN"
```

### Install Claude Code on the App Service

```bash
# SSH into the App Service
az webapp ssh --name "$APP_SERVICE_NAME" --resource-group "$RESOURCE_GROUP"

# Inside the SSH session:
npm install -g @anthropic-ai/claude-code@latest
claude --version   # verify installation

# Clone your target codebase
cd /home/site
git clone https://github.com/yourorg/your-repo.git workspace
```

> **Alternative:** Use a custom Docker image (see Section 9) with Claude Code
> pre-installed so you don't need to SSH in after every deploy.

---

## 5. Bot Runtime Stack

```
Runtime:     Node.js 22 LTS
Framework:   M365 Agents SDK (recommended) or botbuilder v4 (still works)
Claude Code: npm install -g @anthropic-ai/claude-code
```

---

## 6. Command Router Design

### 6a. Claude Code Tasks (primary use case)
Messages like:
```
@hermes review PR #342
@hermes refactor src/api/auth.ts to use async/await
@hermes find bugs in @src/utils/parser.ts
```

**Read-only execution:**
```bash
claude -p "<user_prompt>" \
  --bare \
  --output-format json \
  --max-turns 5 \
  --allowedTools "Read,Glob,Grep" \
  --append-system-prompt "You are Hermes, a code assistant. \
    Be concise. Format output for Teams (markdown). \
    Never modify files unless explicitly asked."
```

**Write execution** (fix/refactor/implement):
```bash
claude -p "<user_prompt>" \
  --bare \
  --output-format json \
  --max-turns 10 \
  --allowedTools "Read,Write,Edit,Bash(git diff *),Bash(git status *),Bash(git commit *)" \
  --append-system-prompt "You are Hermes. Create a branch, make changes, commit. \
    Never push to main. Always create a PR via gh CLI."
```

**Key flags:**
- `--bare` — skips hooks/MCP/CLAUDE.md discovery, ~800ms startup
- `--output-format json` — returns `{ result, cost, session_id }`
- `--max-turns` — cost control
- `--allowedTools` — read-only by default, write requires explicit intent

### 6b. CI/CD Triggers
```
@hermes deploy staging
@hermes run tests on feature/auth-refactor
```

Dispatches HTTP POST to GitHub Actions / Azure DevOps:
```javascript
await fetch(
  `https://api.github.com/repos/${owner}/${repo}/actions/workflows/${workflow}/dispatches`,
  {
    method: 'POST',
    headers: { Authorization: `token ${GITHUB_TOKEN}` },
    body: JSON.stringify({
      ref: branch,
      inputs: { triggered_by: 'hermes', ...params }
    })
  }
);
```

### 6c. Conversational (no file access needed)
Falls through to a direct Anthropic API call — no CLI overhead:
```javascript
const response = await fetch('https://api.anthropic.com/v1/messages', {
  method: 'POST',
  headers: {
    'x-api-key': process.env.ANTHROPIC_API_KEY,
    'content-type': 'application/json',
    'anthropic-version': '2023-06-01'
  },
  body: JSON.stringify({
    model: 'claude-sonnet-4-20250514',
    max_tokens: 4096,
    messages: [{ role: 'user', content: userMessage }],
    system: 'You are Hermes, a concise code assistant. Format for Teams markdown.'
  })
});
```

---

## 7. Bot Message Handler (Core Loop)

```javascript
// src/bot.ts
import { TeamsActivityHandler, TurnContext } from 'botbuilder';
import { execFile } from 'child_process';
import { promisify } from 'util';

const exec = promisify(execFile);

export class HermesBot extends TeamsActivityHandler {
  constructor() {
    super();
    this.onMessage(async (context: TurnContext, next) => {
      const text = context.activity.text?.replace(/<at>.*<\/at>/g, '').trim();
      if (!text) return next();

      await context.sendActivity({ type: 'typing' });

      const intent = classifyIntent(text);

      switch (intent.type) {
        case 'code_task':
          await this.handleCodeTask(context, text, intent);
          break;
        case 'cicd_trigger':
          await this.handleCICDTrigger(context, text, intent);
          break;
        default:
          await this.handleConversation(context, text);
      }

      return next();
    });
  }

  private async handleCodeTask(ctx: TurnContext, prompt: string, intent: any) {
    const tools = intent.requiresWrite
      ? 'Read,Write,Edit,Bash(git diff *),Bash(git status *),Bash(git commit *)'
      : 'Read,Glob,Grep';

    try {
      const { stdout } = await exec('claude', [
        '-p', prompt,
        '--bare',
        '--output-format', 'json',
        '--max-turns', intent.requiresWrite ? '10' : '5',
        '--allowedTools', tools,
        '--append-system-prompt',
        'You are Hermes. Be concise. Format for Teams markdown.'
      ], {
        env: { ...process.env },
        cwd: process.env.REPO_PATH,
        timeout: 120_000
      });

      const result = JSON.parse(stdout);
      await ctx.sendActivity(
        `**Hermes** (cost: $${result.cost?.total_cost?.toFixed(4) ?? '?'})\n\n${result.result}`
      );
    } catch (err: any) {
      await ctx.sendActivity(`⚠️ Claude Code error: ${err.message}`);
    }
  }

  private async handleCICDTrigger(ctx: TurnContext, text: string, intent: any) {
    // Extract workflow + branch from text, POST to GitHub Actions
    // ... (see Section 6b)
    await ctx.sendActivity('Pipeline triggered.');
  }

  private async handleConversation(ctx: TurnContext, text: string) {
    // Direct Anthropic API call (see Section 6c)
    // ... parse response and send back
  }
}
```

---

## 8. Intent Classification

```javascript
function classifyIntent(text: string) {
  const lower = text.toLowerCase();

  if (/\b(deploy|pipeline|trigger|run tests|build)\b/.test(lower))
    return { type: 'cicd_trigger', requiresWrite: false };

  if (/\b(fix|refactor|create|add|update|modify|implement|write)\b/.test(lower))
    return { type: 'code_task', requiresWrite: true };

  if (/\b(review|analyze|find bugs|check|explain|look at|inspect)\b/.test(lower))
    return { type: 'code_task', requiresWrite: false };

  return { type: 'conversation' };
}
```

---

## 9. Docker Alternative (for ACI or custom App Service)

If you prefer a container with Claude Code pre-baked:

```dockerfile
FROM node:22-slim

RUN npm install -g @anthropic-ai/claude-code@latest && \
    apt-get update && apt-get install -y git gh && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY package*.json ./
RUN npm ci --production
COPY dist/ ./dist/

ENV REPO_PATH=/workspace
ENV PORT=3978
EXPOSE 3978
CMD ["node", "dist/index.js"]
```

```bash
# Build and push to Azure Container Registry
ACR_NAME="acrhermes"

az acr create --name "$ACR_NAME" --resource-group "$RESOURCE_GROUP" --sku Basic --admin-enabled true
az acr build --registry "$ACR_NAME" --image hermes-bot:latest .

# Deploy as Azure Container Instance
az container create \
  --resource-group "$RESOURCE_GROUP" \
  --name "hermes-aci" \
  --image "${ACR_NAME}.azurecr.io/hermes-bot:latest" \
  --registry-login-server "${ACR_NAME}.azurecr.io" \
  --registry-username "$(az acr credential show --name $ACR_NAME --query username -o tsv)" \
  --registry-password "$(az acr credential show --name $ACR_NAME --query 'passwords[0].value' -o tsv)" \
  --ports 3978 \
  --dns-name-label "hermes-bot" \
  --environment-variables \
    KEYVAULT_URI="https://${KEYVAULT_NAME}.vault.azure.net" \
    MicrosoftAppId="$APP_ID" \
    REPO_PATH="/workspace" \
  --secure-environment-variables \
    MicrosoftAppPassword="$APP_PASSWORD" \
    ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
    GITHUB_TOKEN="$GITHUB_TOKEN"

# Update bot endpoint to point at ACI
ACI_FQDN=$(az container show --name "hermes-aci" --resource-group "$RESOURCE_GROUP" --query "ipAddress.fqdn" -o tsv)
az bot update \
  --name "$BOT_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --endpoint "https://${ACI_FQDN}/api/messages"
```

---

## 10. Teams App Manifest

Save as `manifest.json` inside an `appPackage/` folder alongside `color.png` (192x192) and `outline.png` (32x32):

```json
{
  "$schema": "https://developer.microsoft.com/json-schemas/teams/v1.17/MicrosoftTeams.schema.json",
  "manifestVersion": "1.17",
  "version": "1.0.0",
  "id": "{{BOT_APP_ID}}",
  "developer": {
    "name": "Your Org",
    "websiteUrl": "https://yourorg.com",
    "privacyUrl": "https://yourorg.com/privacy",
    "termsOfUseUrl": "https://yourorg.com/terms"
  },
  "name": { "short": "Hermes", "full": "Hermes — Claude Code for Teams" },
  "description": {
    "short": "Run Claude Code tasks from Teams",
    "full": "CLI-style coding tasks, PR reviews, CI/CD triggers — powered by Claude."
  },
  "icons": { "color": "color.png", "outline": "outline.png" },
  "accentColor": "#7F77DD",
  "bots": [
    {
      "botId": "{{BOT_APP_ID}}",
      "scopes": ["personal", "team", "groupChat"],
      "commandLists": [
        {
          "commands": [
            { "title": "review", "description": "Review a PR or file" },
            { "title": "fix", "description": "Fix a bug or issue" },
            { "title": "deploy", "description": "Trigger a deployment" },
            { "title": "test", "description": "Run tests on a branch" },
            { "title": "explain", "description": "Explain code or architecture" }
          ]
        }
      ]
    }
  ]
}
```

### Upload to Teams

```bash
# Zip the app package
cd appPackage
zip -r ../appPackage.zip manifest.json color.png outline.png
cd ..

# Then either:
# A) Upload via Teams Admin Center (admin.teams.microsoft.com → Manage apps → Upload)
# B) Sideload in Teams client (Apps → Manage your apps → Upload a custom app)
```

---

## 11. Session Continuity

Map Teams conversation threads to Claude Code sessions for multi-turn context:

```javascript
// In-memory store (swap for Redis / Cosmos for persistence)
const sessionMap = new Map<string, string>();

async function runClaude(prompt: string, threadId: string, opts: any) {
  const args = ['-p', prompt, '--bare', '--output-format', 'json', ...opts];

  // Resume existing session if we have one for this thread
  const existingSession = sessionMap.get(threadId);
  if (existingSession) {
    args.push('--session-id', existingSession);
  }

  const { stdout } = await exec('claude', args, {
    env: { ...process.env },
    cwd: process.env.REPO_PATH,
    timeout: 120_000
  });

  const result = JSON.parse(stdout);

  // Store session for future messages in this thread
  if (result.session_id) {
    sessionMap.set(threadId, result.session_id);
  }

  return result;
}
```

---

## 12. Security & Guardrails

| Concern | Mitigation |
|---------|------------|
| API cost runaway | `--max-turns 5`/`10`, per-user daily budget in app logic |
| Repo safety | Read-only tools by default; write requires intent keywords |
| Secrets | Key Vault with managed identity; Key Vault refs in App Settings |
| Branch protection | Only commit to feature branches; PRs require human approval |
| Teams auth | Entra ID validates tokens on every inbound message |
| Timeout | 120s hard limit on child process; typing indicator in Teams |
| Audit trail | Log command + cost + user to App Insights |

---

## 13. Cost Estimation

| Operation | Approx. cost |
|-----------|-------------|
| Simple review (read-only, 3 turns) | ~$0.02–0.05 |
| Refactor + PR (write, 8 turns) | ~$0.15–0.40 |
| Conversational question (API direct) | ~$0.005–0.02 |
| CI/CD trigger (webhook only) | $0 (no LLM call) |
| App Service B1 | ~$13/month |
| Key Vault (secrets) | ~$0.03/10k operations |
| Bot Service (Teams channel) | Free |

Set daily budget cap per user (e.g. $2/day), alert at 80%.

---

## 14. Scaling to Multi-Bot (Future-Proofing)

> **Today's scope:** Single bot, single repo, single team. But the following
> decisions are made now so you don't have to rearchitect later.

### 14a. What to Parameterize Now

The provisioning script already uses variables at the top. To make it
repeatable for N bots, extract these into a config file per instance:

```bash
# hermes-instances/marketing-bot.env
BOT_NAME="hermes-marketing"
APP_SERVICE_NAME="app-hermes-marketing"
REPO_URL="https://github.com/yourorg/marketing-site"
TEAMS_CHANNEL_SCOPE="team"        # personal | team | groupChat
ALLOWED_TOOLS="Read,Glob,Grep"    # per-bot tool policy
MAX_TURNS_READ=5
MAX_TURNS_WRITE=10
DAILY_BUDGET_PER_USER=2.00
```

Then wrap the provisioning script to accept a config path:

```bash
./provision-hermes.sh --config hermes-instances/marketing-bot.env
```

**Do this now:** Keep all bot-specific values in the config block at the top
of `provision-hermes.sh` — never hardcode them deeper in the script.
The refactor to config-file-driven is a 20-minute job when you need it.

### 14b. Shared vs. Dedicated Resources

| Resource | Strategy | Why |
|----------|----------|-----|
| **Resource Group** | One per bot | Clean isolation, easy teardown |
| **App Service Plan** | Shared across bots | Cost savings — B1/S1 can host multiple apps on the same plan |
| **Key Vault** | Shared, one secret prefix per bot | e.g. `hermes-marketing-api-key`, `hermes-eng-api-key` |
| **Entra ID App Reg** | One per bot | Each bot needs its own identity for Teams |
| **Bot Service** | One per bot | Channel registrations are 1:1 with bot identities |
| **App Insights** | Shared workspace | Central observability; filter by `bot_name` custom dimension |

To use a shared App Service Plan across multiple bots:

```bash
# First bot creates the plan
az appservice plan create --name "plan-hermes-shared" --resource-group "rg-hermes-shared" --sku S1 --is-linux

# Subsequent bots reference the same plan
az webapp create \
  --name "app-hermes-eng" \
  --resource-group "rg-hermes-eng" \
  --plan "/subscriptions/<sub>/resourceGroups/rg-hermes-shared/providers/Microsoft.Web/serverfarms/plan-hermes-shared" \
  --runtime "NODE:22-lts"
```

### 14c. Bot Registry Pattern

When you hit 3+ bots, you'll want a lightweight registry to track what's deployed.
Don't build a database — a single JSON file in a shared repo is enough:

```jsonc
// hermes-registry.json
{
  "bots": [
    {
      "name": "hermes-eng",
      "appId": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
      "appServiceName": "app-hermes-eng",
      "repo": "https://github.com/yourorg/main-app",
      "teamScope": "Engineering",
      "dailyBudget": 5.00,
      "status": "active",
      "createdAt": "2026-03-26"
    },
    {
      "name": "hermes-marketing",
      "appId": "yyyyyyyy-yyyy-yyyy-yyyy-yyyyyyyyyyyy",
      "appServiceName": "app-hermes-marketing",
      "repo": "https://github.com/yourorg/marketing-site",
      "teamScope": "Marketing",
      "dailyBudget": 2.00,
      "status": "active",
      "createdAt": "2026-03-28"
    }
  ]
}
```

This becomes the input for both the dashboard and any automation tooling.

### 14d. Dashboard (Concept Only — Build When Needed)

When you need visibility across multiple bots, here's the minimal viable dashboard:

**Data sources (already available):**
- App Insights → query cost, latency, error rate, requests per bot
- Key Vault → secret expiry dates
- `hermes-registry.json` → bot inventory

**MVP dashboard shape:**

```
┌─────────────────────────────────────────────────────┐
│  Hermes Fleet Dashboard                             │
├──────────┬──────────┬───────┬────────┬──────────────┤
│ Bot      │ Status   │ Today │ Budget │ Errors (24h) │
├──────────┼──────────┼───────┼────────┼──────────────┤
│ eng      │ ● Active │ $1.24 │ $5.00  │ 0            │
│ marketing│ ● Active │ $0.38 │ $2.00  │ 2            │
│ data-eng │ ○ Paused │ $0.00 │ $3.00  │ —            │
└──────────┴──────────┴───────┴────────┴──────────────┘
```

**Implementation options (pick one when the time comes):**
1. **Cheapest:** Azure Workbook in App Insights — KQL queries, zero code, auto-refreshes
2. **Low-code:** A single React artifact powered by App Insights REST API
3. **Fullest:** Dedicated dashboard App Service reading from the registry + App Insights

**The KQL query you'll need** (works today in App Insights → Logs):

```kusto
customEvents
| where name == "hermes_command"
| where timestamp > ago(24h)
| summarize
    totalCost = sum(todouble(customDimensions.cost)),
    requests = count(),
    errors = countif(customDimensions.status == "error")
  by botName = tostring(customDimensions.bot_name)
| order by totalCost desc
```

**What to instrument now (so the dashboard has data later):**
Add this to every command execution in `bot.ts`:

```javascript
// At the top of bot.ts
import { TelemetryClient } from 'applicationinsights';
const telemetry = new TelemetryClient(process.env.APPINSIGHTS_CONNECTION_STRING);

// After each command completes
telemetry.trackEvent({
  name: 'hermes_command',
  properties: {
    bot_name: process.env.BOT_NAME ?? 'hermes',
    user_id: context.activity.from.aadObjectId,
    intent: intent.type,
    requires_write: String(intent.requiresWrite),
    status: 'success',           // or 'error'
    session_id: result.session_id ?? '',
  },
  measurements: {
    cost: result.cost?.total_cost ?? 0,
    turns: result.num_turns ?? 0,
    duration_ms: elapsed,
  }
});
```

### 14e. Future Scaling Milestones

| Milestone | Trigger | Action |
|-----------|---------|--------|
| **2nd bot** | Another team asks for Hermes | Extract config file, rerun provisioning script |
| **5+ bots** | Managing manually gets annoying | Build registry JSON + shared App Service Plan |
| **10+ bots** | Need visibility | Stand up Azure Workbook dashboard (KQL-based) |
| **20+ bots** | Provisioning needs to be self-service | Wrap script in an Azure Function or GitHub Action triggered by PR to registry |
| **Enterprise** | Compliance/audit requirements | Add RBAC per bot, centralized logging, cost allocation tags |

**The key principle:** Don't build the platform until you feel the pain.
Everything above is designed so that the single-bot setup you have today
becomes one entry in a multi-bot fleet with minimal refactoring.

---

## 15. Teardown (if needed)

```bash
# Delete everything in one shot
az group delete --name "$RESOURCE_GROUP" --yes --no-wait

# Clean up the Entra ID app registration separately
az ad app delete --id "$APP_ID"
```
