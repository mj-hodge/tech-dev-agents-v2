# Seed: STORY-227 — User-Assigned Managed Identity for Azure Auth

**Story:** STORY-227
**Parent Epic:** EPIC-004 (Foundry Cost Observability & Morris SDK-First)
**Date:** 2026-04-15
**Scope:** Medium
**Phase Path:** 1 → 4 → 6 → [6b, 6c, 6d] → 7 → 8 → 8b → 11 → Done
**Assignee:** Dan (implementation)
**Coordinator:** Morris (manager — reviews each phase output, escalates to Mark when needed)
**Branch:** `story-227/managed-identity-azure-auth`

---

## Problem Statement

### The human-paste credential workflow is broken

Today (2026-04-15), Morris asked Mark via Teams to run `az ad sp create-for-rbac` and share the resulting `appId` / `password` / `tenant` with him so he could configure the ops-console Azure Cost Management integration. Mark (wisely) refused to paste the secret into Teams and asked Claude to handle it. What followed:

1. Claude rotated the SP credential twice (invalidating the live container's old secret in the process)
2. Claude discovered the Docker container was the authoritative runtime but the compose `.env` file lacked unprefixed keys required for `docker compose up`
3. Claude discovered the running container had lost its `agent-registry.json` mount during a prior recreate, so the fleet showed 0 agents
4. Claude discovered the ops-console `OPS_GRAPH_API_TOKEN` was empty, so the MCP `send_message` was failing silently before — meaning no agent-to-agent Teams delivery was working
5. A human had to intervene to untangle all of this; the original "please share a credential" ask cascaded into a 30-minute incident

**This will happen every time.** Dan, Derrick, and Morris all need Azure resources at various points — Cost Management (done), Foundry metrics, Storage, Log Analytics, Key Vault. Each new resource = another `az ad sp create-for-rbac` paste dance. Each one is a potential leaked secret, a potential stale credential, and a hard dependency on Mark being available to run Azure CLI commands.

### Why managed identity is the right answer

Azure VMs can be assigned a **user-assigned managed identity**. The identity has roles at the resource/subscription level. Any process running on the VM can get a short-lived OAuth token from IMDS (`http://169.254.169.254/metadata/identity/oauth2/token`) with zero stored secrets. Token rotation is automatic — Azure mints a new one every ~24h with no code on our side. Role additions are done once in Azure IAM; no redeploy required.

Python's `DefaultAzureCredential` (from `azure-identity`) auto-discovers and uses the managed identity transparently. Existing client-secret-based code continues to work as a fallback (useful for local dev), but production reads from IMDS.

### Division of labor

**Dan implements.** Morris coordinates — he does not write code, does not claim queue items, does not self-assign. Morris's role on this story:
- Reviews each phase deliverable (analysis, design, review reports, code) as Dan produces them
- Escalates any of the Mark-hands-required steps (Azure CLI commands Mark has to run) via Teams to Mark, batched
- Keeps the project tracker up to date
- Does NOT run Claude Code SDK sessions, edit files, or touch the code himself

**Dan does the work.** All substantive phases (analysis, design, test design, implementation, code review) are Dan's work — either directly in Claude Code or via the SDK when Dan needs background execution. Dan already owns STORY-224 + STORY-225 in this same epic; adding STORY-227 keeps EPIC-004 consolidated with one implementation owner.

## Goals

1. **Eliminate stored Azure client secrets on agent VMs.** No more `AZURE_CLIENT_SECRET` in any `.env` file for production. The ops-console and agent VMs authenticate to Azure via their assigned managed identity.
2. **Future-proof the provisioning process.** Any new agent VM gets the managed identity attached + the needed roles as part of the standard setup — no ad-hoc credential creation.
3. **Preserve local dev.** `DefaultAzureCredential` tries managed identity first, falls back to explicit `AZURE_CLIENT_*` env vars if IMDS isn't available. Developers running the ops console locally don't need to change anything.

## Non-goals

- Don't rotate other Azure credentials that belong to service principals we don't own (e.g., shared GitHub PAT, Loki key, Entra SSO app). Scope is Azure resources owned by this project.
- Don't change the Hermes Graph token refresh flow — that's a separate credential system (Azure AD delegated tokens for Teams) and needs its own story.
- Don't attempt to retrofit the existing `ops-console-cost-reader` SP — we'll deprecate it entirely once managed identity works, since rotation-on-every-SSH-access has no long-term value.

## Acceptance Criteria

| ID | Criterion | Measurable |
|---|---|---|
| AC-1 | Ops-console container on `vm-ops-console-dev` authenticates to Azure Cost Management API via managed identity | `azure_cost_client` log line shows `DefaultAzureCredential` using `ManagedIdentityCredential` — NOT `ClientSecretCredential` |
| AC-2 | `AZURE_CLIENT_SECRET` removed from `/opt/ops-console/.env` after migration | `grep ^AZURE_CLIENT_SECRET= /opt/ops-console/.env` returns nothing on the VM |
| AC-3 | `hermes insights`-equivalent or cost endpoint on ops-console returns real Foundry cost data after the switch | `/api/fleet` returns non-zero `total_daily_spend_usd` (at least $0.01, not missing auth) |
| AC-4 | `deployment/vm/deploy-agent.sh` attaches the managed identity + assigns the required roles to any new agent VM | Running the script on a fresh VM results in `az vm identity show` + `az role assignment list` showing the identity is assigned |
| AC-5 | `NEW-AGENT-PROCESS.md` documents the managed identity pattern in Phase 1 (provisioning) | File contains explicit checklist items for managed-identity setup |
| AC-6 | Stale `ops-console-cost-reader` service principal deleted from Azure AD after migration verified | `az ad sp list --filter "displayName eq 'ops-console-cost-reader'"` returns empty |

## Scope

### What Morris needs to do (SDK-delegated)

**Phase 1 (this seed)**: already done — you're reading it.

**Phase 4 (analysis)**: delegate to Claude Code SDK. The SDK agent needs to:
- Read `tech_dev_agents/ops_console/services/azure_cost_client.py` and trace every `ClientSecretCredential` / `DefaultAzureCredential` / environment-variable use
- Read `deployment/vm/deploy-agent.sh` to understand where VM provisioning happens
- Read `deployment/vm/NEW-AGENT-PROCESS.md` to find where to document the new step
- Check `pyproject.toml` for `azure-identity` version — verify `>=1.15.0` for IMDSv2 support on Azure VMs
- Deliver: `analysis.md` with a migration-order decision (prod first vs dev first? single-VM pilot vs all-at-once?)

**Phase 6 (design)**: delegate to Claude Code SDK.
- Design the switch inside `azure_cost_client.py` — likely one-liner to prefer `DefaultAzureCredential()` with appropriate options, fall back to explicit client-secret path when env vars present
- Design the `deploy-agent.sh` changes — `az vm identity assign` + `az role assignment create` commands
- Design the README/NEW-AGENT-PROCESS update
- Deliver: `feature-spec.md` with exact diffs proposed

**Phase 6b/6c/6d (reviews — parallel)**: delegate to SDK with review personas.
- Security review: confirm IMDS exposure is acceptable, confirm no secrets end up in logs, confirm role scope is narrow enough
- UX review: confirm `hermes insights` behavior doesn't change for users
- Ops review: confirm rollback path if managed identity fails (fall back to env vars), confirm monitoring covers auth failures

**Phase 7 (test design) + Phase 8 (implementation)**: delegate to SDK.
- Tests mock `DefaultAzureCredential` and verify the fallback chain
- Implementation should be a ~10-line change to `azure_cost_client.py`, a block in `deploy-agent.sh`, a section in the runbook

**Phase 8b (code review) + Phase 11 (pre-deploy gate)**: delegate to SDK with review personas.

### What requires Mark's hands (can't delegate)

- **Azure portal / CLI actions that require his identity**:
  1. Create the user-assigned managed identity: `az identity create --name id-gc-agents-cost-reader --resource-group RG-TECH-DEV-AGENTS-DEV`
  2. Assign Cost Management Reader role at subscription: `az role assignment create --assignee <mi-principal-id> --role "Cost Management Reader" --scope /subscriptions/<sub-id>`
  3. Attach identity to existing VMs: `az vm identity assign --ids <vm-resource-ids> --identities <mi-resource-id>`
  4. After Morris verifies AC-1 through AC-3, delete the old `ops-console-cost-reader` SP

Morris should document these exact commands in `feature-spec.md` so Mark can run them in a single batch.

### What this story does NOT do

- Does not migrate Hermes's Graph token flow to managed identity (Graph delegated permissions are a different beast)
- Does not change Claude Code SDK auth (uses its own Azure Foundry endpoint with separate token)
- Does not re-architect the ops-console's SSO (Entra ID for human users stays as-is)

## Out-of-scope but noted

- **OPS_GRAPH_API_TOKEN refresh on ops-console**: today's incident revealed that the ops-console has no automated Graph token refresh, so `mcp__agent-ops__send_message` fails silently once tokens expire. That's a separate story (STORY-228?) — Morris, please file it as a follow-up after STORY-227 is spec'd, don't fold it into this one.
- **Scope-limited tokens for bot-to-bot Teams messaging**: the MCP path needs `Chat.ReadWrite` on an app-registered bot token. Also a follow-up.

## Constraints

- **No regressions on current cost tracking.** `/api/fleet` and `/api/agents/{name}/cost` must continue to return correct Azure Cost Management data during and after the migration.
- **No downtime.** The switch must be a rolling change (new `azure_cost_client.py` accepts both auth methods; live VM switches via restart after identity is attached).
- **Backward-compatible with local dev.** Running the ops console locally without a managed identity (no IMDS) must continue to work via explicit `AZURE_CLIENT_*` env vars.
- **Test coverage for the auth fallback chain.** `tests/ops_console/test_azure_cost_client.py` (or similar) must cover: (a) managed identity path, (b) client-secret fallback path, (c) both-absent error case.

## Key Files

| File | Role |
|---|---|
| `tech_dev_agents/ops_console/services/azure_cost_client.py` | The auth switch — currently constructs `ClientSecretCredential` from env vars; needs `DefaultAzureCredential` first |
| `deployment/vm/deploy-agent.sh` | VM provisioning — needs `az vm identity assign` step |
| `deployment/vm/NEW-AGENT-PROCESS.md` | Manual runbook — needs managed identity section in Phase 1 |
| `deployment/ops-console/README.md` | Ops console docs — needs note that `AZURE_CLIENT_SECRET` is dev-only |
| `pyproject.toml` | Dependency pin for `azure-identity` |
| `tests/ops_console/test_azure_cost_client.py` (may need creating) | Auth fallback chain tests |

## Open Questions (Morris, answer these in Phase 4)

1. **Should all three agent VMs (Dan, Derrick, Morris) get the same managed identity, or one per agent?** One shared identity is simpler but makes per-agent audit impossible. Probably: one identity per agent for audit, all with the same role set.

2. **Does Cost Management Reader at subscription scope also cover the Foundry resource costs, or do we need additional role assignments?** Check `az cost` data for Foundry resource group; verify after attachment.

3. **What happens to the old `ops-console-cost-reader` SP?** Delete after migration confirmed working for 48h? Keep as a break-glass fallback? Morris's call but document the decision.

4. **Does the ops-console VM (`vm-ops-console-dev`) need a separate managed identity from the agent VMs?** Its role requirements differ (no Foundry access needed there).

## Success Criteria Rollup

- Zero `AZURE_CLIENT_SECRET` entries in any production `.env`
- `/api/fleet` returns correct cost data after migration
- `NEW-AGENT-PROCESS.md` has the managed-identity section — next new agent onboarded without any credential paste
- Old `ops-console-cost-reader` SP deleted
- Morris reports in Teams: "credential-paste workflow is extinct in this codebase"

## Version

0.1.0 (seed)

## Next Phase

Phase 4 (Analysis) — Medium scope per AGENTS.md. Delegate the analysis work to a Claude Code SDK session per Morris's SDK-first discipline.
