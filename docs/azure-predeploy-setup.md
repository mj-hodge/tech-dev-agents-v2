# Azure Predeploy Setup for Hermes Agent

This runbook covers Azure setup and gate checks before deploying the Hermes runtime from `deployment/hermes/Dockerfile`.

## 1. Prerequisites

- Azure subscription with permissions to create resource groups, ACR, Container Apps, and Key Vault
- Azure CLI logged in
- Docker (if building locally)

## 2. One-Time Azure Setup

```bash
az login
az account set -s "<subscription-id>"
az provider register --namespace Microsoft.App
az provider register --namespace Microsoft.ContainerRegistry
az provider register --namespace Microsoft.KeyVault
az provider register --namespace Microsoft.OperationalInsights
```

## 3. Create Resource Group and Registry

```bash
export RESOURCE_GROUP="rg-hermes-dev"
export LOCATION="eastus"
export ACR_NAME="acrhermesdev<unique>"

az group create --name "$RESOURCE_GROUP" --location "$LOCATION"
az acr create --name "$ACR_NAME" --resource-group "$RESOURCE_GROUP" --sku Basic --admin-enabled true
```

## 4. Build and Push Hermes Image

```bash
export IMAGE_REPO="hermes-agent"
export IMAGE_TAG_VALUE="$(date +%Y%m%d-%H%M)-main"

az acr build \
  --registry "$ACR_NAME" \
  --image "$IMAGE_REPO:$IMAGE_TAG_VALUE" \
  -f deployment/hermes/Dockerfile \
  .

export IMAGE_TAG="${ACR_NAME}.azurecr.io/${IMAGE_REPO}:${IMAGE_TAG_VALUE}"
```

## 5. Create Container Apps Environment

```bash
export ACA_ENV="acae-hermes-dev"
az containerapp env create \
  --name "$ACA_ENV" \
  --resource-group "$RESOURCE_GROUP" \
  --location "$LOCATION"
```

## 6. Create Key Vault and Secrets

```bash
export KEYVAULT_NAME="kv-hermes-dev-<unique>"
az keyvault create --name "$KEYVAULT_NAME" --resource-group "$RESOURCE_GROUP" --location "$LOCATION"

az keyvault secret set --vault-name "$KEYVAULT_NAME" --name anthropic-api-key --value "<value>"
az keyvault secret set --vault-name "$KEYVAULT_NAME" --name openai-api-key --value "<value>"
```

## 7. Deploy Hermes to Azure Container Apps

```bash
export APP_NAME="ca-hermes-agent-dev"
export ANTHROPIC_API_KEY="<value>"  # pull from KV/secret manager in real pipelines

az containerapp create \
  --name "$APP_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --environment "$ACA_ENV" \
  --image "$IMAGE_TAG" \
  --registry-server "${ACR_NAME}.azurecr.io" \
  --target-port 8080 \
  --ingress external \
  --cpu 1 --memory 2Gi \
  --env-vars HERMES_HOME=/home/hermes/.hermes ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY"
```

Note: if your Hermes gateway is messaging-only and does not expose HTTP, keep ingress internal/disabled and adapt `BASE_URL` checks to your health endpoint strategy.

## 8. Capture Runtime Endpoint and Gate Vars

```bash
FQDN=$(az containerapp show --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" --query properties.configuration.ingress.fqdn -o tsv)
export BASE_URL="https://${FQDN}"

export DATABASE_URL="postgresql+psycopg://<user>:<pass>@<host>:5432/<db>"
export REDIS_URL="redis://:<password>@<host>:6379/0"
```

## 9. Microsoft Graph API Permissions (STORY-628)

The ops-console uses the Microsoft Graph API to sync agent presence to Teams.
Before deploying code from STORY-549 (unified presence), the following
permission must be granted on the ops-console Graph app registration:

- **Permission:** `Presence.ReadWrite.All` (Application type)
- **Admin consent:** Required (must be granted by a tenant admin)

### Steps

1. In Azure Portal → App registrations → select the ops-console app
2. API permissions → Add a permission → Microsoft Graph → Application permissions
3. Search for `Presence.ReadWrite.All` → Add
4. Click "Grant admin consent for \<tenant\>"
5. Verify the permission shows "Granted" status

### Verification

```bash
# List current permissions for the app
az ad app permission list --id <app-id> --output table

# Should include the Presence.ReadWrite.All app role GUID
```

**If this permission is missing:** Teams presence pushes will receive HTTP 403
from the Graph API. The ops-console will log a warning once per agent per
process lifetime but will continue operating — the dashboard (`/api/agents/presence`)
still works since it derives state from fleet health probes, not Graph.

## 10. Predeploy Gate Tooling

Install at least one tool from each pair where predeploy runs:

- CVE scan: `trivy` or `grype`
- Secret scan: `gitleaks` or `trufflehog`
- Dependency audit: `pip-audit`

## 11. Run Predeploy Gate

```bash
export IMAGE_TAG="$IMAGE_TAG"
export BASE_URL="$BASE_URL"
export DATABASE_URL="$DATABASE_URL"
export REDIS_URL="$REDIS_URL"

bash tests/predeploy/run_all.sh
```

Deploy policy:

- all checks must be `PASS`
- any `FAIL` or `BLOCKED` means do not promote
