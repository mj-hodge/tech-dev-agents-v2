## Morris Code Review — PR #256: fix(settings): admin group check broken for Azure AD JWT users

**Size:** Large (19 files, +2873/−70) | **Verdict: REQUEST_CHANGES ⛔**

---

### Blockers

| # | Issue |
|---|-------|
| 1 | **CONFLICTING** — branch must be rebased onto `main` before CI can run |
| 2 | **No CI** — security-critical auth PR with zero CI verification cannot be merged |

---

### Multi-Story Bundling (HIGH)

PR title advertises only the settings auth fix, but ships **4 distinct changes**:

| Change | Scope |
|--------|-------|
| Settings auth fix (`settings.py` + SA-29..SA-32 tests) | Advertised |
| **STORY-210 campaign allocation cron** (`budget_guidance.py`, `budget_update_service.py`, `server.py`, 18 tests — ~300 lines) | Hidden |
| Nav reorganization (BSR Lookup moved between nav sections) | Unlabelled |
| STORY-625 design docs (Phase 6 artifacts for unimplemented story) | Unlabelled |

All content superseded by PR #261 (release bundle).

---

### Auth Fix Findings

| Severity | Finding | Location |
|----------|---------|----------|
| **HIGH** | No test for `_claim_names` (groups overage) path — Azure AD users with >200 group memberships silently get 403 even when admin. Warning fires but behavior is opaque to operators. **SA-33 test needed** before this merges anywhere. | `settings.py` |
| NIT | `_GC_TENANT_ID` duplicated across modules — pre-existing pattern, not a regression. | |

**Auth fix code is correct** — root cause (`getattr` on dict returns `None`) correctly diagnosed and fixed. Object path preserved for non-dict tokens.

---

### Recommended Action

1. **Close this PR** — superseded by PR #261
2. Before PR #261 is merged: add **SA-33 test** for `_claim_names` groups overage path
3. Update PR #261 body to explicitly call out STORY-210 campaign allocation scope
