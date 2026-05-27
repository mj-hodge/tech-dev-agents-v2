# PR #35 Bundling Rationale — Four-Story Bundle

**Document:** Bundling Rationale
**PR:** #35
**Branch:** story-305/fix-cost-display
**Date:** 2026-04-16
**Author:** SDLC Remediation (STORY-311)

---

## Overview

PR #35 bundles four stories onto a single branch and PR. This document records the rationale, the stories included, and why joint delivery was appropriate.

---

## Stories Included

| Story | Title | Scope | Key Change |
|-------|-------|-------|-----------|
| STORY-227 | Managed Identity Azure Auth | Medium | Migrate `AzureCostClient` from API key to `DefaultAzureCredential` |
| STORY-229 | Bot-to-Bot Teams Messaging via App Token | Medium | Replace M365 CLI delegated-user auth with MSAL `ConfidentialClientApplication` |
| STORY-253 | Commit-Gated Dispatch Completion | Small | Add `commit_sha` validation to `POST /api/dispatch/complete/{story_id}` |
| STORY-305 | Fix Cost Display (foundry_cost_usd) | Small | Populate `foundry_cost_usd` field in Azure cost parser |

---

## Rationale for Bundling

### 1. Shared deployment dependency

STORY-227 and STORY-229 both require new environment variables on the agent VMs (`AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, `AZURE_TENANT_ID`, `TEAMS_CLIENT_ID`, `TEAMS_CLIENT_SECRET`, `TEAMS_TENANT_ID`). These share the same Azure App Registration and are most safely deployed together in a single coordinated rollout with a single `.env` update per VM.

### 2. Coupled test infrastructure

STORY-229's `graph_token_provider.py` is exercised by tests in `tests/test_ops_console/test_graph_token_provider.py`. STORY-227's `DefaultAzureCredential` migration touches the same `azure_cost_client.py` dependency chain. Running both test suites together ensures no cross-dependency regression.

### 3. Tiny scope of STORY-253 and STORY-305

Both STORY-253 and STORY-305 are one-to-two file changes with no infrastructure requirements. Issuing separate PRs for these micro-fixes would create unnecessary PR overhead. They were naturally included in the branch that was already open for 227/229 work.

### 4. Sequential work on same feature branch

The stories were worked sequentially on the `story-305/fix-cost-display` branch:
- STORY-227 implemented first (managed identity)
- STORY-229 implemented second (Teams app token)
- STORY-253 and STORY-305 were small fixes discovered and addressed while verifying the 227/229 changes

No story in this bundle conflicts with another; each touches distinct files.

---

## Risks Accepted

| Risk | Mitigation |
|------|-----------|
| Larger PR surface harder to review | All four stories have independent SDLC deliverable folders; reviewers can scope review per story |
| Single deployment window for two Medium stories | Deployment runbook in `ops-review.md` for each story specifies rollback steps independently |
| If one story needs revert, others are entangled | Each story's changes are in distinct files; cherry-pick revert is straightforward |

---

## Evidence of Completeness

- STORY-227: All Medium deliverables present in `features/story-227-managed-identity-azure-auth/`
- STORY-229: All Medium deliverables present in `features/story-229-bot-teams-messaging/` (security-review, ux-review, ops-review added by STORY-311 remediation)
- STORY-253: Seed + test-design present; Small scope compliant
- STORY-305: Full Medium deliverable set in `features/story-305-fix-cost-display/` (created by STORY-311 remediation)
