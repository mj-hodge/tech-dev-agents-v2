"""STORY-701: Failure classification + Morris DLQ triage tests.

Phase 7: RED state — tests written before implementation.

Test groups:
  1 — Migration is idempotent (011_dispatch_failure_reason.sql)
  2 — _classify_failure_reason: table-driven (all 6 categories from taxonomy)
  3 — _classify_failure_reason with unknown/None inputs → 'unknown', no exception
  4 — _report_fail passes failure_reason in POST body to /dispatch/fail
  5 — /dispatch/fail route accepts and persists failure_reason
  6 — Morris DLQ triage query returns correct shape
  7 — Triage cron is idempotent (running twice doesn't break)
"""
from __future__ import annotations

import sys
from io import StringIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# Allow import of dispatch_poller and dlq_triage from their deployment paths
sys.path.insert(0, str(REPO_ROOT / "deployment" / "hermes"))
sys.path.insert(0, str(REPO_ROOT / "deployment" / "morris" / "scripts"))


# ---------------------------------------------------------------------------
# Group 1 — Migration idempotency
# ---------------------------------------------------------------------------


class TestMigration011:
    """T1: 011_dispatch_failure_reason.sql must be idempotent."""

    def test_migration_file_exists(self):
        """T1a: Migration file exists at expected path."""
        migration = REPO_ROOT / "scripts" / "migrations" / "011_dispatch_failure_reason.sql"
        assert migration.exists(), (
            f"Migration file missing: {migration}"
        )

    def test_migration_uses_if_not_exists(self):
        """T1b: Migration must use ADD COLUMN IF NOT EXISTS for idempotency."""
        migration = REPO_ROOT / "scripts" / "migrations" / "011_dispatch_failure_reason.sql"
        sql = migration.read_text().lower()
        assert "if not exists" in sql, (
            "Migration must use 'IF NOT EXISTS' to be safe for re-runs"
        )

    def test_migration_targets_dispatch_items(self):
        """T1c: Migration must target the dispatch_items table."""
        migration = REPO_ROOT / "scripts" / "migrations" / "011_dispatch_failure_reason.sql"
        sql = migration.read_text().lower()
        assert "dispatch_items" in sql, (
            "Migration must reference 'dispatch_items' table"
        )

    def test_migration_adds_failure_reason_column(self):
        """T1d: Migration must add failure_reason column."""
        migration = REPO_ROOT / "scripts" / "migrations" / "011_dispatch_failure_reason.sql"
        sql = migration.read_text().lower()
        assert "failure_reason" in sql, (
            "Migration must add the 'failure_reason' column"
        )

    def test_migration_column_is_varchar_40(self):
        """T1e: Column must be VARCHAR(40) to match the taxonomy."""
        migration = REPO_ROOT / "scripts" / "migrations" / "011_dispatch_failure_reason.sql"
        sql = migration.read_text().lower()
        assert "varchar(40)" in sql, (
            "failure_reason column must be VARCHAR(40)"
        )


# ---------------------------------------------------------------------------
# Group 2 — _classify_failure_reason: table-driven for all categories
# ---------------------------------------------------------------------------


class TestClassifyFailureReason:
    """T2: Table-driven tests for all categories in the taxonomy."""

    def _classify(self, exit_code, error_text, retry_count):
        import dispatch_poller
        return dispatch_poller._classify_failure_reason(exit_code, error_text, retry_count)

    def test_rate_limit_exhausted(self):
        """T2a: exit_code=429 + retry_count>=MAX → rate_limit_exhausted."""
        result = self._classify(429, "You've hit your limit", 3)
        assert result == "rate_limit_exhausted", (
            f"exit_code=429 with retry_count>=MAX must return 'rate_limit_exhausted', got {result!r}"
        )

    def test_rate_limit_not_exhausted_not_classified(self):
        """T2b: exit_code=429 but retry_count<MAX → should NOT be rate_limit_exhausted."""
        result = self._classify(429, "You've hit your limit", 1)
        # With exit_code=429 and retry_count<MAX, should fall through to next check.
        # Since no other pattern matches, it will be 'unknown' (not rate_limit_exhausted).
        assert result != "rate_limit_exhausted", (
            "rate_limit_exhausted requires retry_count >= MAX_RETRY_ATTEMPTS"
        )

    def test_branch_setup_failed(self):
        """T2c: 'branch_setup_failed' in error_text → branch_setup_failed."""
        result = self._classify(1, "branch_setup_failed: git ls-remote failed", 0)
        assert result == "branch_setup_failed", (
            f"'branch_setup_failed' in error_text must return 'branch_setup_failed', got {result!r}"
        )

    def test_cross_story_validation_missing(self):
        """T2d: exit_code=422 + 'Prompt references' in error → cross_story_validation_missing."""
        result = self._classify(422, "Prompt references STORY-500 which does not exist", 0)
        assert result == "cross_story_validation_missing", (
            f"exit_code=422 with 'Prompt references' must return 'cross_story_validation_missing', got {result!r}"
        )

    def test_gate_rejection_with_exit_422(self):
        """T2e: exit_code=422 without 'Prompt references' → gate_rejection."""
        result = self._classify(422, "Acceptance Diff missing for Phase 8", 0)
        assert result == "gate_rejection", (
            f"exit_code=422 (not cross-story) must return 'gate_rejection', got {result!r}"
        )

    def test_excessive_retries(self):
        """T2f: retry_count>=MAX without other cause → excessive_retries."""
        import dispatch_poller
        result = self._classify(1, "some generic error", dispatch_poller.MAX_RETRY_ATTEMPTS)
        assert result == "excessive_retries", (
            f"retry_count>=MAX with no special exit_code must return 'excessive_retries', got {result!r}"
        )

    def test_unknown_default(self):
        """T2g: No matching pattern → 'unknown'."""
        result = self._classify(1, "random unclassified error", 0)
        assert result == "unknown", (
            f"Unmatched input must return 'unknown', got {result!r}"
        )

    def test_needs_info_unanswered(self):
        """T2h: 'needs_info' or 'QUESTION.md' in error_text → needs_info_unanswered."""
        result = self._classify(1, "story stuck: QUESTION.md unanswered for 7 days", 0)
        assert result == "needs_info_unanswered", (
            f"needs_info signal must return 'needs_info_unanswered', got {result!r}"
        )
        result2 = self._classify(1, "needs_info timeout reached", 0)
        assert result2 == "needs_info_unanswered"

    def test_agent_died(self):
        """T2i: 'agent_died' or 'heartbeat' in error_text → agent_died."""
        result = self._classify(1, "agent_died: no heartbeat for 15 min", 0)
        assert result == "agent_died", (
            f"agent_died signal must return 'agent_died', got {result!r}"
        )
        result2 = self._classify(1, "heartbeat stale — auto-releasing", 0)
        assert result2 == "agent_died"

    def test_table_driven_all_categories(self):
        """T2j: Table-driven sweep — all 8 expected categories are reachable."""
        import dispatch_poller
        MAX = dispatch_poller.MAX_RETRY_ATTEMPTS
        cases = [
            (429, "rate limit", MAX, "rate_limit_exhausted"),
            (1, "branch_setup_failed happened", 0, "branch_setup_failed"),
            (422, "Prompt references STORY-400", 0, "cross_story_validation_missing"),
            (422, "deliverable gate failed", 0, "gate_rejection"),
            (1, "needs_info unanswered QUESTION.md", 0, "needs_info_unanswered"),
            (1, "agent_died: no heartbeat", 0, "agent_died"),
            (1, "random error", MAX, "excessive_retries"),
            (1, "unknown problem", 0, "unknown"),
        ]
        for exit_code, error_text, retry_count, expected in cases:
            result = self._classify(exit_code, error_text, retry_count)
            assert result == expected, (
                f"_classify_failure_reason({exit_code!r}, {error_text!r}, {retry_count!r}) "
                f"expected {expected!r}, got {result!r}"
            )


# ---------------------------------------------------------------------------
# Group 3 — Unknown / None inputs → 'unknown', no exception
# ---------------------------------------------------------------------------


class TestClassifyFailureReasonUnknownInputs:
    """T3: Classifier must handle edge cases without raising."""

    def _classify(self, exit_code, error_text, retry_count):
        import dispatch_poller
        return dispatch_poller._classify_failure_reason(exit_code, error_text, retry_count)

    def test_none_error_text_returns_unknown(self):
        """T3a: None error_text must not raise and must return 'unknown'."""
        result = self._classify(1, None, 0)
        assert result == "unknown", f"None error_text must return 'unknown', got {result!r}"

    def test_empty_error_text_returns_unknown(self):
        """T3b: Empty string error_text must return 'unknown'."""
        result = self._classify(1, "", 0)
        assert result == "unknown", f"Empty error_text must return 'unknown', got {result!r}"

    def test_zero_retry_and_zero_exit_returns_unknown(self):
        """T3c: exit_code=0 (unusual) and retry_count=0 must return 'unknown'."""
        result = self._classify(0, "some text", 0)
        assert result == "unknown"

    def test_classify_never_raises(self):
        """T3d: Classifier must never raise regardless of inputs."""
        import dispatch_poller
        import traceback
        for args in [(None, None, None), (999, None, -1), (422, None, 0)]:
            try:
                dispatch_poller._classify_failure_reason(*args)
            except Exception as e:
                pytest.fail(
                    f"_classify_failure_reason{args!r} raised {type(e).__name__}: {e}"
                )


# ---------------------------------------------------------------------------
# Group 4 — _report_fail passes failure_reason in POST body
# ---------------------------------------------------------------------------


class TestReportFailIncludesFailureReason:
    """T4: _report_fail must include failure_reason in the /fail POST body."""

    def _make_session(self, status=200):
        session = MagicMock()
        session.post.return_value = MagicMock(status_code=status, text="")
        return session

    def _get_fail_call_json(self, session, story_id="STORY-701"):
        """Extract the JSON body from the /fail POST call."""
        for c in session.post.call_args_list:
            if c.args and f"/api/dispatch/fail/{story_id}" in c.args[0]:
                return c.kwargs.get("json") or (c.args[1] if len(c.args) > 1 else None)
        return None

    def test_fail_post_includes_failure_reason_key(self):
        """T4a: POST body to /fail must contain 'failure_reason' key."""
        import dispatch_poller
        session = self._make_session()

        dispatch_poller._report_fail(
            session=session,
            base_url="http://ops",
            api_key="testkey",
            story_id="STORY-701",
            exit_code=1,
            repo="tech-dev-agents",
            scope="small",
            prompt="implement STORY-701",
            duration_seconds=300,
            error_message="some error",
        )

        body = self._get_fail_call_json(session)
        assert body is not None, "POST to /fail was not called"
        assert "failure_reason" in body, (
            f"POST body to /dispatch/fail must include 'failure_reason'. Got: {body}"
        )

    def test_fail_post_failure_reason_is_not_none(self):
        """T4b: failure_reason in POST body must be a non-None string."""
        import dispatch_poller
        session = self._make_session()

        dispatch_poller._report_fail(
            session=session,
            base_url="http://ops",
            api_key="testkey",
            story_id="STORY-701",
            exit_code=422,
            repo="tech-dev-agents",
            scope="small",
            prompt="implement STORY-701",
            duration_seconds=300,
            error_message="gate rejected deliverable",
        )

        body = self._get_fail_call_json(session)
        assert body is not None
        assert body.get("failure_reason") is not None, (
            "failure_reason must be a non-None string (not 'unknown' is still a string)"
        )

    def test_fail_post_gate_rejection_reason(self):
        """T4c: exit_code=422 → failure_reason='gate_rejection' in POST body."""
        import dispatch_poller
        session = self._make_session()

        dispatch_poller._report_fail(
            session=session,
            base_url="http://ops",
            api_key="testkey",
            story_id="STORY-701",
            exit_code=422,
            repo="tech-dev-agents",
            scope="small",
            prompt="implement STORY-701",
            duration_seconds=300,
            error_message="Acceptance Diff missing",
        )

        body = self._get_fail_call_json(session)
        assert body is not None
        assert body.get("failure_reason") == "gate_rejection", (
            f"exit_code=422 error must produce failure_reason='gate_rejection'. Got: {body}"
        )

    def test_fail_post_unknown_reason_for_unclassified(self):
        """T4d: Unclassified error → failure_reason='unknown' in POST body."""
        import dispatch_poller
        session = self._make_session()

        dispatch_poller._report_fail(
            session=session,
            base_url="http://ops",
            api_key="testkey",
            story_id="STORY-701",
            exit_code=1,
            repo="tech-dev-agents",
            scope="small",
            prompt="implement STORY-701",
            duration_seconds=300,
            error_message="unrecognized failure mode",
        )

        body = self._get_fail_call_json(session)
        assert body is not None
        assert body.get("failure_reason") == "unknown", (
            f"Unclassified error must produce failure_reason='unknown'. Got: {body}"
        )

    def test_fail_post_always_includes_exit_code(self):
        """T4e: Regression — exit_code must still be in the POST body alongside failure_reason."""
        import dispatch_poller
        session = self._make_session()

        dispatch_poller._report_fail(
            session=session,
            base_url="http://ops",
            api_key="testkey",
            story_id="STORY-701",
            exit_code=5,
            repo="tech-dev-agents",
            scope="small",
            prompt="implement STORY-701",
            duration_seconds=300,
            error_message="some error",
        )

        body = self._get_fail_call_json(session)
        assert body is not None
        assert "exit_code" in body, "exit_code must still be present in POST body"
        assert body.get("exit_code") == 5, "exit_code value must be preserved"


# ---------------------------------------------------------------------------
# Group 5 — /dispatch/fail route accepts and persists failure_reason
# ---------------------------------------------------------------------------


class TestFailRouteAcceptsFailureReason:
    """T5: Backend route must extract failure_reason and pass to db_svc.fail()."""

    def test_db_svc_fail_signature_accepts_failure_reason(self):
        """T5a: dispatch_db_service.DispatchDbService.fail must accept failure_reason kwarg."""
        import inspect
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        from tech_dev_agents.ops_console.services.dispatch_db_service import DispatchDBService
        sig = inspect.signature(DispatchDBService.fail)
        assert "failure_reason" in sig.parameters, (
            f"DispatchDBService.fail must accept 'failure_reason' parameter. "
            f"Got parameters: {list(sig.parameters.keys())}"
        )

    def test_db_svc_fail_failure_reason_defaults_none(self):
        """T5b: failure_reason parameter must default to None (backward compat)."""
        import inspect
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        from tech_dev_agents.ops_console.services.dispatch_db_service import DispatchDBService
        sig = inspect.signature(DispatchDBService.fail)
        param = sig.parameters.get("failure_reason")
        assert param is not None, "failure_reason parameter must exist"
        assert param.default is None, (
            f"failure_reason must default to None for backward compat, got {param.default!r}"
        )

    def test_route_parses_failure_reason_from_body(self):
        """T5c: Route must read failure_reason from request body.get('failure_reason')."""
        import ast
        route_file = REPO_ROOT / "tech_dev_agents" / "ops_console" / "routes" / "dispatch.py"
        source = route_file.read_text()
        # Check that the route reads failure_reason from the body
        assert "failure_reason" in source, (
            "dispatch.py route must reference 'failure_reason'"
        )
        assert 'body.get("failure_reason")' in source or "body.get('failure_reason')" in source, (
            "Route must extract failure_reason from request body via body.get('failure_reason')"
        )

    def test_route_passes_failure_reason_to_db_svc(self):
        """T5d: Route must pass failure_reason to db_svc.fail(...)."""
        route_file = REPO_ROOT / "tech_dev_agents" / "ops_console" / "routes" / "dispatch.py"
        source = route_file.read_text()
        # The fail call should pass failure_reason
        assert "failure_reason=failure_reason" in source, (
            "Route must pass failure_reason=failure_reason to db_svc.fail()"
        )

    def test_db_svc_fail_writes_failure_reason_to_db(self):
        """T5e: db_svc.fail() must issue SQL UPDATE including failure_reason column.

        Verifies the WITH-failure_reason branch in dispatch_db_service.DispatchDBService.fail()
        by patching _resolve_row and capturing conn.fetchrow SQL.
        """
        import asyncio
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        import tech_dev_agents.ops_console.services.dispatch_db_service as dbs_mod
        from tech_dev_agents.ops_console.services.dispatch_db_service import DispatchDBService

        captured_sql = []
        fake_row = MagicMock()
        fake_row.__getitem__ = lambda self, k: {"id": 1, "status": "claimed"}.get(k)
        fake_row.get = lambda k, d=None: {"id": 1, "status": "claimed"}.get(k, d)

        async def run():
            conn = MagicMock()
            txn = MagicMock()
            conn.transaction.return_value.__aenter__ = AsyncMock(return_value=txn)
            conn.transaction.return_value.__aexit__ = AsyncMock(return_value=False)

            async def fake_fetchrow(sql, *args, **kwargs):
                captured_sql.append(sql)
                return fake_row
            conn.fetchrow = fake_fetchrow

            pool = MagicMock()
            pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
            pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

            svc = DispatchDBService(pool)
            # Patch _resolve_row to return a claimed row directly
            with patch.object(dbs_mod, "_resolve_row", AsyncMock(return_value=fake_row)):
                with patch.object(dbs_mod, "_row_to_dict", lambda r: {"status": "failed"}):
                    await svc.fail("STORY-701", repo="tech-dev-agents",
                                   failure_reason="gate_rejection")

        asyncio.run(run())

        # The conn.fetchrow SQL must reference failure_reason (the WITH-reason branch)
        assert any("failure_reason" in sql for sql in captured_sql), (
            f"db_svc.fail() with failure_reason must issue SQL containing 'failure_reason'. "
            f"Got SQL calls: {captured_sql}"
        )


# ---------------------------------------------------------------------------
# Group 6 — Morris DLQ triage query returns correct shape
# ---------------------------------------------------------------------------


class TestDlqTriageQuery:
    """T6: DLQ triage must return correct column shape from fixture data."""

    EXPECTED_KEYS = {"story_id", "repo", "failure_reason", "claimed_by", "failed_at", "prompt"}

    def test_dlq_triage_file_exists(self):
        """T6a: dlq_triage.py must exist in deployment/morris/scripts/."""
        script = REPO_ROOT / "deployment" / "morris" / "scripts" / "dlq_triage.py"
        assert script.exists(), f"Missing: {script}"

    def test_dlq_triage_has_main_function(self):
        """T6b: dlq_triage.py must define an async main() function."""
        script = REPO_ROOT / "deployment" / "morris" / "scripts" / "dlq_triage.py"
        source = script.read_text()
        assert "async def main" in source, "dlq_triage.py must define 'async def main'"

    def test_dlq_triage_query_contains_required_columns(self):
        """T6c: The SQL QUERY must select all required columns."""
        script = REPO_ROOT / "deployment" / "morris" / "scripts" / "dlq_triage.py"
        source = script.read_text()
        required_columns = ["story_id", "repo", "failure_reason", "claimed_by", "prompt"]
        for col in required_columns:
            assert col in source, (
                f"QUERY in dlq_triage.py must select column '{col}'"
            )

    def test_dlq_triage_filters_by_failure_reason_not_null(self):
        """T6d: Query must filter for failure_reason IS NOT NULL to exclude unclassified."""
        script = REPO_ROOT / "deployment" / "morris" / "scripts" / "dlq_triage.py"
        source = script.read_text().lower()
        assert "failure_reason is not null" in source, (
            "QUERY must include 'failure_reason IS NOT NULL' filter"
        )

    def test_dlq_triage_filters_status_failed(self):
        """T6e: Query must filter for status='failed'."""
        script = REPO_ROOT / "deployment" / "morris" / "scripts" / "dlq_triage.py"
        source = script.read_text().lower()
        assert "status = 'failed'" in source or "status='failed'" in source, (
            "QUERY must filter by status = 'failed'"
        )

    def test_dlq_triage_orders_by_completed_at_desc(self):
        """T6f: Query must order by completed_at DESC (newest failures first)."""
        script = REPO_ROOT / "deployment" / "morris" / "scripts" / "dlq_triage.py"
        source = script.read_text().lower()
        assert "order by completed_at desc" in source, (
            "QUERY must ORDER BY completed_at DESC"
        )

    def test_dlq_triage_result_shape_with_mock_db(self):
        """T6g: main() outputs JSON with correct column shape when given fixture rows."""
        import asyncio
        import json

        # Build a mock asyncpg row (dict-like)
        import datetime
        fake_dt = datetime.datetime(2026, 4, 25, 10, 0, 0, tzinfo=datetime.timezone.utc)
        mock_rows = [
            {
                "story_id": "STORY-701",
                "repo": "tech-dev-agents",
                "failure_reason": "gate_rejection",
                "claimed_by": "hermes",
                "failed_at": fake_dt,
                "prompt": "implement STORY-701",
            }
        ]

        # asyncpg rows behave like dicts — mock conn.fetch to return them
        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[mock_rows[0]])
        mock_conn.close = AsyncMock()

        captured = StringIO()

        async def run():
            import dlq_triage
            with patch("asyncpg.connect", AsyncMock(return_value=mock_conn)):
                with patch.dict("os.environ", {"DATABASE_URL": "postgresql://fake"}):
                    with patch("sys.stdout", captured):
                        await dlq_triage.main()

        asyncio.run(run())

        output = captured.getvalue()
        assert output.strip(), "dlq_triage.main() must produce output"
        result = json.loads(output)
        assert isinstance(result, list), f"Output must be a JSON array, got: {type(result)}"
        assert len(result) == 1, f"Expected 1 row, got {len(result)}"
        row = result[0]
        for key in ("story_id", "repo", "failure_reason", "claimed_by", "prompt"):
            assert key in row, f"Result row must contain '{key}'. Got keys: {list(row.keys())}"
        assert row["story_id"] == "STORY-701"
        assert row["failure_reason"] == "gate_rejection"

    def test_dlq_triage_converts_datetime_to_iso(self):
        """T6h: Datetime fields (failed_at) must be converted to ISO strings."""
        import asyncio
        import json
        import datetime

        fake_dt = datetime.datetime(2026, 4, 25, 10, 0, 0, tzinfo=datetime.timezone.utc)
        mock_row = {
            "story_id": "STORY-701",
            "repo": "tech-dev-agents",
            "failure_reason": "unknown",
            "claimed_by": "hermes",
            "failed_at": fake_dt,
            "prompt": "x",
        }

        mock_conn = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[mock_row])
        mock_conn.close = AsyncMock()

        captured = StringIO()

        async def run():
            import dlq_triage
            with patch("asyncpg.connect", AsyncMock(return_value=mock_conn)):
                with patch.dict("os.environ", {"DATABASE_URL": "postgresql://fake"}):
                    with patch("sys.stdout", captured):
                        await dlq_triage.main()

        asyncio.run(run())

        result = json.loads(captured.getvalue())
        assert len(result) == 1
        failed_at = result[0].get("failed_at")
        assert isinstance(failed_at, str), (
            f"failed_at must be an ISO string, got {type(failed_at)}: {failed_at!r}"
        )
        assert "2026-04-25" in failed_at, (
            f"failed_at ISO string must contain the date. Got: {failed_at!r}"
        )


# ---------------------------------------------------------------------------
# Group 7 — Triage cron is idempotent (running twice doesn't break)
# ---------------------------------------------------------------------------


class TestDlqTriageIdempotent:
    """T7: Running dlq_triage.main() twice must produce identical results."""

    def test_running_triage_twice_produces_same_output(self):
        """T7a: Two successive calls to main() must produce identical JSON."""
        import asyncio
        import json
        import datetime

        fake_dt = datetime.datetime(2026, 4, 26, 10, 0, 0, tzinfo=datetime.timezone.utc)
        mock_rows = [
            {
                "story_id": "STORY-600",
                "repo": "tech-dev-agents",
                "failure_reason": "excessive_retries",
                "claimed_by": "devon",
                "failed_at": fake_dt,
                "prompt": "implement STORY-600",
            },
            {
                "story_id": "STORY-601",
                "repo": "tech-dev-agents",
                "failure_reason": "gate_rejection",
                "claimed_by": "daisy",
                "failed_at": fake_dt,
                "prompt": "implement STORY-601",
            },
        ]

        outputs = []

        async def run():
            import dlq_triage
            mock_conn = AsyncMock()
            mock_conn.fetch = AsyncMock(return_value=mock_rows)
            mock_conn.close = AsyncMock()

            captured = StringIO()
            with patch("asyncpg.connect", AsyncMock(return_value=mock_conn)):
                with patch.dict("os.environ", {"DATABASE_URL": "postgresql://fake"}):
                    with patch("sys.stdout", captured):
                        await dlq_triage.main()
            outputs.append(json.loads(captured.getvalue()))

        asyncio.run(run())
        asyncio.run(run())

        assert len(outputs) == 2, "Should have run twice"
        assert outputs[0] == outputs[1], (
            "Running dlq_triage.main() twice must produce identical results"
        )

    def test_empty_result_set_is_valid_json(self):
        """T7b: When there are no failed rows, output must be an empty JSON array."""
        import asyncio
        import json

        async def run():
            import dlq_triage
            mock_conn = AsyncMock()
            mock_conn.fetch = AsyncMock(return_value=[])
            mock_conn.close = AsyncMock()

            captured = StringIO()
            with patch("asyncpg.connect", AsyncMock(return_value=mock_conn)):
                with patch.dict("os.environ", {"DATABASE_URL": "postgresql://fake"}):
                    with patch("sys.stdout", captured):
                        await dlq_triage.main()
            return json.loads(captured.getvalue())

        result = asyncio.run(run())
        assert result == [], f"Empty DB must produce '[]', got: {result!r}"

    def test_triage_closes_connection_on_second_run(self):
        """T7c: conn.close() must be called on each run — no connection leak."""
        import asyncio

        async def run():
            import dlq_triage
            mock_conn = AsyncMock()
            mock_conn.fetch = AsyncMock(return_value=[])
            mock_conn.close = AsyncMock()

            with patch("asyncpg.connect", AsyncMock(return_value=mock_conn)):
                with patch.dict("os.environ", {"DATABASE_URL": "postgresql://fake"}):
                    with patch("sys.stdout", StringIO()):
                        await dlq_triage.main()
            mock_conn.close.assert_awaited_once()

        asyncio.run(run())
        asyncio.run(run())  # second run — each run creates a fresh mock via the patch
