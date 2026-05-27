# STORY-227: Pre-Deploy Gate (Phase 11)

**Date:** 2026-04-15
**Verdict:** PASS (conditional)

---

## Gate Checks

| Check | Status | Notes |
|-------|--------|-------|
| All tests pass | PASS | 8/8 cost client tests GREEN, 208/209 ops_console (1 pre-existing failure) |
| No secret in code | PASS | `AZURE_CLIENT_SECRET` removed from config.py and docker-compose.yml |
| Dependency pinned | PASS | `azure-identity>=1.15.0,<2.0.0` in requirements.txt |
| Backward compatible | PASS | EnvironmentCredential fallback covers existing env vars |
| Runbook exists | PASS | `NEW-AGENT-PROCESS.md` created with manual and auto setup steps |
| deploy-agent.sh updated | PASS | Phase 2b adds managed identity creation and assignment |
| Code review passed | PASS | Phase 8b APPROVED |
| Security review passed | PASS | Phase 6b APPROVED |
| Ops review passed | PASS | Phase 6d APPROVED |

## Conditions for Full Deploy

1. **Mark must run az CLI commands** (feature-spec Section 3) to create the managed identity and assign it to VMs
2. **48h verification** before deleting old SP (AC-6)
3. **Monitor `/api/fleet`** returns non-zero `total_daily_spend_usd` after identity assignment

## Acceptance Criteria Status

| AC | Description | Status |
|----|-------------|--------|
| AC-1 | Auth log shows ManagedIdentityCredential | READY (pending identity assignment) |
| AC-2 | AZURE_CLIENT_SECRET removed from .env | READY (pending env cleanup) |
| AC-3 | /api/fleet returns non-zero total_daily_spend_usd | READY (pending identity assignment) |
| AC-4 | deploy-agent.sh attaches managed identity + roles | DONE |
| AC-5 | NEW-AGENT-PROCESS.md documents the pattern | DONE |
| AC-6 | SP deleted after 48h verification | PENDING (post-deploy) |

## Deploy Decision

**PASS — ready to merge.** Code changes are complete and tested. Remaining ACs require Mark's az CLI actions post-merge.
