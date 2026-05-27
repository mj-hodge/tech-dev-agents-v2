# STORY-301 — SharePoint client helper for autonomous agents

**Scope:** Small–Medium
**Phase path:** `1 → 7 → 8 → Done`
**Depends on:** Service principal `tech-dev-agents-knowledgebase` already provisioned (app_id `4928d18f-1e09-4d6e-bca0-36805287855a`) and its client secret stored in Key Vault `kv-tech-dev-agents-dev` as `knowledgebase-sp-secret`.
**Feeds into:** `tech-gc-knowledgebase` STORY-012 (SharePoint ingest) — cannot re-dispatch until this merges.

---

## Goal

Give dispatched agents a clean, reusable, non-interactive way to search and read SharePoint / OneDrive content via the Microsoft Graph API, using the dedicated `tech-dev-agents-knowledgebase` service principal. Today agents cannot access SharePoint — the only Graph auth on agent VMs is for Teams messaging (delegated, Chat scopes only). This story adds application-auth-based SharePoint read access.

---

## Deliverables

### 1. `tools/sharepoint_client.py`

Python module with the following API:

```python
from sharepoint_client import SharePointClient

client = SharePointClient()  # auto-loads creds from Key Vault or env

# Search
results = client.search(
    query="SOP",
    file_type="docx",           # optional
    before="2026-04-01",        # optional ISO date
    after="2024-01-01",         # optional ISO date
    limit=50,
)
# Each result dict contains AT MINIMUM:
#   name, web_url, site, path, created_date_time, last_modified_date_time,
#   created_by, last_modified_by, size, content_snippet

# Read full content
content = client.read(uri)  # uri from search result
# Returns: {body_text, metadata (all dates, authors), content_type}

# Folder navigation
folders = client.search_folders(name="Technology")
items = client.list_folder(folder_uri)
```

**Hard requirements:**

- **Every search/read response MUST include `created_date_time` and `last_modified_date_time`** — this is a user-required field for detecting stale source material. Do not strip these.
- **Author fields** (`created_by`, `last_modified_by`) must include email where available.
- Returns raw UTF-8 text for docx/pptx/xlsx/pdf when possible (use `python-docx`, `python-pptx`, `openpyxl`, `pypdf` or the Graph "convert to PDF/text" endpoint).
- Graceful degradation: if a binary format can't be extracted, return metadata + note `body_text: None, extraction_error: "<reason>"`.

### 2. Credential loading

Priority order:
1. **Key Vault** (`kv-tech-dev-agents-dev`, secret `knowledgebase-sp-credentials` — full JSON blob) using `DefaultAzureCredential` — works when agent VM has managed identity with `Key Vault Secrets User` role
2. **Environment variable** `KNOWLEDGEBASE_SP_CREDENTIALS` — raw JSON, for CI/local testing
3. **File path** `$HOME/.agent-ops/knowledgebase-sp-secret.json` — fallback for dev

Token acquired via client credentials flow, cached in memory for 55 minutes, auto-refreshed on expiry.

### 3. CLI wrapper

```bash
# Install: pip install -e tools/
sharepoint-search "SOP" --limit 10 --format json
sharepoint-read file:///{driveId}/{itemId}
sharepoint-folder "Technology"
```

Outputs JSON so agents can pipe into their own processing.

### 4. Managed identity setup

Update the infra that provisions agent VMs (look under `deploy/` or `deployment/`) to:
- Assign managed identity to each agent VM if not already done
- Grant the VM's managed identity `Key Vault Secrets User` role on `kv-tech-dev-agents-dev` scoped to just the `knowledgebase-sp-*` secrets
- Document the setup in `docs/sharepoint-access.md`

If the current VM provisioning doesn't support per-secret scoping easily, vault-wide Secrets User is acceptable as a pragmatic starting point, but flag it as technical debt.

### 5. Tests

- Unit tests with mocked Graph responses
- Integration test (optional, tagged `@integration`) that hits real Graph with a service principal — must be runnable locally with the secret file
- Test that `created_date_time` / `last_modified_date_time` are ALWAYS present in results (golden-path assertion)

### 6. Docs

- `docs/sharepoint-access.md` — how agents acquire credentials, how to use the client, what the SPN can and cannot access (e.g., cannot read personal OneDrives that aren't shared at the site level)
- Update the main agent-facing README or CLAUDE.md to point to the new helper

---

## Success Criteria

- [ ] `SharePointClient` class implemented in `tools/sharepoint_client.py`
- [ ] Client credentials flow works with cached token refresh
- [ ] `search()`, `read()`, `search_folders()`, `list_folder()` methods implemented
- [ ] Every result includes `created_date_time` and `last_modified_date_time` (asserted in tests)
- [ ] Credentials load from Key Vault via `DefaultAzureCredential`, falling back to env and file
- [ ] CLI wrappers (`sharepoint-search`, `sharepoint-read`, `sharepoint-folder`) work
- [ ] Managed identity RBAC grant script or IaC exists for agent VMs
- [ ] `docs/sharepoint-access.md` written
- [ ] Integration test passes against real Graph (can be skipped in CI, runnable locally)
- [ ] Unit tests ≥ 15 covering all client methods and credential loading paths

## Non-goals

- Don't implement writing to SharePoint — read-only
- Don't implement OneDrive for personal users (only tenant-level Sites.Read.All — personal drives require different permissions)
- Don't include any specific ingestion logic — this is just the plumbing; STORY-012 consumes it
- Don't add caching beyond token caching — result caching is the caller's problem

## Risk: credential rotation

Secret expires 2028-04-15. Add a calendar reminder or document a rotation runbook.

## Cross-references

- Service principal: `access-control.yaml` in `tech-project-mapping` (entry: `tech-dev-agents-knowledgebase`)
- Key Vault: `kv-tech-dev-agents-dev` in `rg-tech-dev-agents-dev` subscription `d0f0feff-78ef-4425-9d51-07f5e0f0bcba`
- Permissions (application, admin-consented): `Sites.Read.All`, `Files.Read.All`, `User.Read.All`
- Related existing helper: `tools/agent-ops-mcp/auth/graph-token.sh` (delegated Teams auth — distinct from this SPN, do not reuse its app_id)
