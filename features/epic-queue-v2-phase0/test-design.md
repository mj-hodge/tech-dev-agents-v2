# Phase 0 Test Design

## Test Cases

### AC1 — Metrics endpoint returns non-null values

**Test:** `test_metrics_endpoint_returns_four_metrics`
- Start a test app with mocked DB service
- GET /api/dispatch/metrics with valid API key
- Assert HTTP 200
- Assert response JSON contains all 4 keys:
  - `claim_409_per_story_5m_max` (int, >= 0)
  - `head_of_line_age_seconds` (float | null)
  - `failure_reason_null_rate` (float, 0.0–1.0)
  - `claim_conflict_rate_5m` (int, >= 0)

### AC2 — dispatch_quarantine table (migration)

**Test:** Verified by migration file existence check + SQL DDL review.
Schema: `id SERIAL PK, story_id TEXT NOT NULL, repo TEXT NOT NULL, quarantined_at TIMESTAMPTZ DEFAULT now(), reason TEXT, cleared_at TIMESTAMPTZ`

### AC3 — Auto-quarantine fires after 6 409s

**Test:** `test_auto_quarantine_after_six_409s`
- Reset the in-memory `_claim_409_window` dict
- Mock `db_svc.claim()` to always raise `AlreadyClaimedError`
- Mock `db_svc.force_release()` as AsyncMock
- Mock DB pool `acquire()` context for quarantine INSERT
- POST /api/dispatch/claim/STORY-QTEST?repo=test-repo 7 times with agent_name
- Assert `db_svc.force_release()` was called (quarantine triggered)
- Assert quarantine INSERT was issued

### AC4 — Quarantined item excluded from /next

**Test:** `test_quarantined_item_excluded_from_next`
- Mock `db_svc.next_pending()` to return a story with story_id=STORY-QEXCL / repo=qtest-repo
- Insert an active quarantine row for (STORY-QEXCL, qtest-repo) into the in-memory quarantine store
- GET /api/dispatch/next
- Assert 204 (no content — story excluded)

**Alternative unit test (no DB):**
- Call `next_pending_with_quarantine_filter()` directly with a mocked pool
- Verify the SQL excludes quarantined (story_id, repo) pairs

## Test Implementation Notes

- All unit tests use the existing `httpx.AsyncClient` + `ASGITransport` pattern from `tests/ops_console/conftest.py`
- DB is mocked — no real PostgreSQL required
- In-memory window state must be cleared between tests (use module-level reset helper)
- Integration test for metrics uses the JSON fallback service path (no DB needed)
