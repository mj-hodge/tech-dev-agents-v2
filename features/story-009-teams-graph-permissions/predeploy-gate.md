# Pre-Deploy Gate: STORY-009 Teams Least-Privilege Graph Permissions

> Phase 11 — Pre-Deploy Gate
> Date: 2026-03-31
> Story: STORY-009
> Scope: Small

## Gate Checklist

| Check | Status | Notes |
|-------|--------|-------|
| All tests pass | PASS | 11/11 story tests GREEN, 137/137 full suite GREEN (0.49s) |
| No regressions | PASS | All pre-existing stories (001-008) unaffected (126→126 existing tests still pass) |
| Code review approved | PASS | Phase 8b verdict: APPROVED, no findings |
| Acceptance criteria mapped | PASS | All 7 ACs from seed.md covered by tests (T01-T11) |
| No hardcoded secrets | PASS | Module validates scope names only; no credentials involved |
| No TODO/FIXME blockers | PASS | No unresolved TODOs in graph_permissions.py |
| Immutability enforced | PASS | All dataclasses frozen; permissions stored as tuples |
| Over-privilege detection works | PASS | T08, T09 verify excess scope detection |
| Audit output serializable | PASS | T10 verifies to_audit_dict() produces JSON-compatible dict |

## Deferred Items (Non-Blocking)

| Item | Source | Reason for Deferral |
|------|--------|-------------------|
| JWT token parsing for scope extraction | Seed constraints | STORY-010 scope — this module validates pre-extracted scopes |
| Azure AD consent automation | Seed out-of-scope | Manual admin consent for v1 |
| Delegated permission validation at runtime | Analysis decision | v1 is application-only; data model supports delegated for future |
| Configurable strictness (warn vs fail) | Analysis decision | Report-only model; caller enforces policy |

## Risk Assessment

| Risk | Severity | Mitigation |
|------|----------|-----------|
| Manifest drifts from actual Azure AD registration | Low | Manifest is the source of truth; startup validation catches drift |
| New Graph API features require additional scopes | Low | Add to manifest with justification; version bump tracks change |
| Token claims use different scope format than manifest | Low | Scope names follow Microsoft's canonical format; caller normalizes if needed |

## Verdict

**CONDITIONAL PASS** — All functional gates pass. The graph permissions module is a pure library with no infrastructure dependencies. Safe to deploy as a library dependency consumed by the startup validation and webhook auth layers (STORY-010, STORY-011).
