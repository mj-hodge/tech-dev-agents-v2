# SharePoint Access for Autonomous Agents

## Overview

Agents access SharePoint document libraries via Microsoft Graph API using the
`tech-dev-agents-knowledgebase` service principal (SPN) with application-level
permissions. All access is **read-only**.

## Service Principal

| Field | Value |
|-------|-------|
| Display Name | `tech-dev-agents-knowledgebase` |
| Application (client) ID | `4928d18f-1e09-4d6e-bca0-36805287855a` |
| Object ID | `dcee5988-4ad5-4e37-aa37-89dea413f94e` |
| Tenant ID | `1060148b-e4f2-4e64-880e-b8b05958e6fe` |

### Permissions (Application, Admin-Consented)

| Permission | Type | Status |
|-----------|------|--------|
| Sites.Read.All | Application | Consented 2026-04-15 |
| Files.Read.All | Application | Consented 2026-04-15 |
| User.Read.All | Application | Consented 2026-04-15 |

**Note:** Application permissions access all SharePoint sites in the tenant.
Personal OneDrives are NOT accessible through this SPN.

## Credential Loading

The `SharePointClient` loads credentials in priority order:

1. **Azure Key Vault** — `kv-tech-dev-agents-dev` / secret `knowledgebase-sp-credentials`
   - Uses `DefaultAzureCredential` (managed identity on agent VMs)
   - Preferred in production

2. **Environment variable** — `KNOWLEDGEBASE_SP_CREDENTIALS`
   - JSON string: `{"appId": "...", "password": "...", "tenant": "..."}`
   - Useful for CI/CD pipelines

3. **Local file** — `~/.agent-ops/knowledgebase-sp-secret.json`
   - Same JSON format as env var
   - For local development only — never commit this file

## Usage

### Python API

```python
from tech_dev_agents.sharepoint_client import SharePointClient, load_credentials

creds = load_credentials()
client = SharePointClient(
    app_id=creds["appId"],
    client_secret=creds["password"],
    tenant_id=creds["tenant"],
)

# Search for documents
results = client.search("quarterly report")
for item in results:
    print(f"{item.name} — Modified: {item.last_modified_date_time}")

# Read a document
doc = client.read(site_id="...", drive_id="...", item_id="...")
print(doc.body_text)

# List folder contents
items = client.list_folder(site_id="...", drive_id="...", folder_id="root")

# Search for folders
folders = client.search_folders(site_id="...", drive_id="...", query="ProjectDocs")
```

### CLI

```bash
# Search
sharepoint-search "quarterly report"
sharepoint-search "handbook" --json

# Read a document
sharepoint-read --site-id SITE --drive-id DRIVE --item-id ITEM
sharepoint-read --site-id SITE --drive-id DRIVE --item-id ITEM --json

# List folder
sharepoint-folder --site-id SITE --drive-id DRIVE list
sharepoint-folder --site-id SITE --drive-id DRIVE list --folder-id FOLDER_ID

# Search folders
sharepoint-folder --site-id SITE --drive-id DRIVE search "ProjectDocs"
```

## Content Extraction

The client extracts text from common document formats:

| Format | Library | Extension |
|--------|---------|-----------|
| Plain text | Built-in | .txt, .md, .csv |
| Word | python-docx | .docx |
| PowerPoint | python-pptx | .pptx |
| Excel | openpyxl | .xlsx |
| PDF | pypdf | .pdf |

Unsupported formats return `body_text=None` with an `extraction_error` message.
Metadata (including date fields) is always returned regardless of extraction success.

## Date Fields (Stale-Source Detection)

Every API response includes `created_date_time` and `last_modified_date_time`.
These fields are **required** — the test suite asserts their presence. This supports
the stale-source detection requirement across all ingest stories.

## Key Vault RBAC Setup

Agent VMs need `Key Vault Secrets User` role on the knowledgebase secrets:

```bash
# Grant managed identity access to the knowledgebase secret
az role assignment create \
  --role "Key Vault Secrets User" \
  --assignee-object-id <VM_MANAGED_IDENTITY_OBJECT_ID> \
  --scope "/subscriptions/<SUB_ID>/resourceGroups/<RG>/providers/Microsoft.KeyVault/vaults/kv-tech-dev-agents-dev/secrets/knowledgebase-sp-credentials"
```

## Security Notes

- Client secret is stored in Key Vault, NOT in code or config files
- Token is cached in memory for ~55 minutes (5-minute buffer before expiry)
- All Graph requests use HTTPS
- The SPN has read-only permissions — no write access to SharePoint
