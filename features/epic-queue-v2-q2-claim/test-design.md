# Test Design — Story Q2: Atomic claim-next + Lease Token + Worker Version Contract

**Phase:** 7 (Test Design — RED state)
**Story:** Q2 / Epic-Queue-v2 Wave 2
**Test file:** `tests/test_epic_queue_v2_q2.py`

---

## Test Strategy

All DB-touching tests are skipped when `ops_console_test` PostgreSQL is unreachable (same pattern as Q1). Pure unit/contract tests run without DB.

Migration 050 must be applied before these tests run (same fixture as Q1). Tests that add Q2-specific data (leases, redispatch rows) clean up via TRUNCATE in fixture teardown.

### RED state rationale
- `routes/dispatch_v2.py` does not yet exist → all HTTP-level tests fail with ImportError or 404.
- `dispatch_v2_service.py` does not yet have `atomic_claim_next`, `heartbeat`, `release_lease`, `transition`, `claim_by_id`, `redispatch`, `list_queue`, `get_lineage` methods → service tests fail with AttributeError.
- Worker version middleware does not yet exist → version tests fail.

---

## Test Classes

### AC1 — Atomic claim-next (100-thread fuzz, 0 duplicates)
`TestAtomicClaimNextFuzz`
- `test_no_duplicates_100_threads_10k_claims`: spin 100 threads, each calling claim-next in a loop until exhausted. Collect all returned job_ids. Assert len(set(job_ids)) == len(job_ids) (zero duplicates).

### AC2 — Stale lease token returns 409
`TestStaleLeastToken`
- `test_heartbeat_with_wrong_token_returns_409`
- `test_release_with_wrong_token_returns_409`
- `test_transition_with_wrong_token_returns_409`
- `test_correct_token_returns_200`

### AC3 — Worker version middleware
`TestWorkerVersionMiddleware`
- `test_below_min_version_returns_426_with_min_required`
- `test_exact_min_version_passes`
- `test_above_min_version_passes`
- `test_missing_version_header_returns_426` (or 400 — document behavior)
- `test_poller_v2_exits_2_on_426` (pure unit test — mock HTTP, verify sys.exit(2))

### AC4 — claim-by-id MANAGER/AGENT roles
`TestClaimByIdRoleGating`
- `test_manager_role_can_claim_by_id`
- `test_agent_role_claim_by_id_returns_403`
- `test_unauthenticated_claim_by_id_returns_401`

### AC6 — Eligibility predicate <50ms p95
`TestEligibilityPerformance`
- `test_eligibility_p95_under_50ms_on_200_row_queue`: seed 200 jobs in work_queue, time 100 calls to claim-next (or the service method), assert p95 < 50ms.

### RT1 — Redispatch idempotency
`TestRedispatchIdempotency`
- `test_same_idempotency_key_3x_creates_1_job`

### RT2 — Concurrent redispatch same correlation key
`TestRedispatchCorrelationUniqueness`
- `test_concurrent_redispatch_same_repo_pr_creates_1_active_child`
- `test_second_concurrent_redispatch_returns_409`

### RT3 — PR head SHA mismatch
`TestRedispatchHeadShaMismatch`
- `test_head_sha_mismatch_returns_422_and_emits_attention_event`

### RT4 — Child does not mutate parent
`TestRedispatchParentImmutability`
- `test_child_cancel_does_not_change_parent_terminal_state`
- `test_child_complete_does_not_change_parent_terminal_state`

### RT5 — claim-next not starved by duplicate correlation keys
`TestClaimNextNotStarvedByRedispatch`
- `test_non_redispatch_jobs_not_blocked_by_duplicate_correlation_key`

### RT6 — Parent non-terminal state guard
`TestRedispatchParentStateGuard`
- `test_redispatch_with_parent_in_progress_returns_409`
- `test_redispatch_force_cancel_parent_requires_manager_role`
- `test_redispatch_force_cancel_parent_as_manager_succeeds_and_cancels_parent`

### RT7 — Cross-repo dependency resolution
`TestCrossRepoDependency`
- `test_cross_repo_dep_unblocks_job_when_dep_completes`

### RT8 — Lineage endpoint
`TestLineageEndpoint`
- `test_lineage_returns_ordered_chain_with_attempt_counts`
- `test_lineage_includes_descendants`
- `test_lineage_includes_ancestors`

---

## Key Design Decisions

1. **HTTP tests use FastAPI TestClient** with mocked DB pool (same as existing dispatch route tests), not a real running server. DB-heavy tests (fuzz, performance) use the real test DB.
2. **Fuzz test uses threading** not asyncio — spawn 100 daemon threads each making real DB calls. Each thread retries until 204 (empty) or hits 10k total claims.
3. **Worker version comparison**: semver-style numeric comparison — `"1.9" < "2.0"` means parse as major.minor integers.
4. **Redispatch correlation key format**: `repo:<repo>|pr:<pr_number>` or `repo:<repo>|branch:<branch_name>`.
5. **Lineage chain order**: ancestors first (root → parent → job), then descendants (child → grandchild). Each entry carries attempt_number (position in the lineage chain starting from 1).
