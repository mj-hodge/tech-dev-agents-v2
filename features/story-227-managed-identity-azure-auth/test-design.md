# STORY-227: Test Design (Phase 7)

**Date:** 2026-04-15
**Scope:** Medium

---

## Test Strategy

Update existing tests (T28-T32) to use credential injection pattern.
Add new tests (T33-T35) for managed identity path, fallback, and error cases.

## Test Matrix

| ID | Test | Type | Priority |
|----|------|------|----------|
| T28 | `_get_token` delegates to injected credential | Unit | P0 |
| T29 | Token value from credential.get_token | Unit | P0 |
| T30 | get_daily_costs maps resource groups (with credential) | Unit | P0 |
| T31 | Empty response returns empty dict | Unit | P1 |
| T32 | Single agent filtering | Unit | P1 |
| T33 | Constructor accepts TokenCredential (no tenant/client/secret) | Unit | P0 |
| T34 | AzureCostError on credential failure | Unit | P0 |
| T35 | get_token called with correct scope | Unit | P0 |

## RED State

Tests are written against the NEW interface (credential injection) which does not exist yet.
Running tests will fail with constructor signature mismatch.
