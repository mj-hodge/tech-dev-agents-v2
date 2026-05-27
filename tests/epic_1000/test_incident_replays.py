"""STORY-1000 Epic -- Incident replay tests.

Verifies that the gates introduced across STORY-1007..STORY-1010 would have
caught or resolved the incidents in the 2026-05-04..05-18 audit window.

STORY-1007 addition (PR #244 replay):
  PR #244 (STORY-766) shipped `assert 48 in url or True` (dispatch_795.py:18).
  Morris's old "Has tests" check was satisfied because the test file existed.
  The new assertion checker must flag this as OR_TRUE_BYPASS.

STORY-1010 additions:
  SC-6: REPLAY-3 -- simulating /requeue-failed skill unavailable, the fallback
        registry returns runnable SQL.
  SC-3: STORY-919-class replay -- a new dispatch attempting in_review without
        pr_number is rejected with 422.

RED reasons (until Phase 8):
  - ops_skill_fallback_registry module does not exist
  - pr_number enforcement gate not added to transition()
"""

from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tech_dev_agents.morris.review_helpers.assertion_checker import (
    AntiPatternType,
    check_new_behavior_assertions,
)
from tech_dev_agents.ops_console.ops_skill_fallback_registry import (
    get_fallback,
    render_fallback,
)


# ---------------------------------------------------------------------------
# STORY-1007 / PR #244 replay: `assert 48 in url or True` caught
# ---------------------------------------------------------------------------

# The exact diff pattern from PR #244 (STORY-766) that prompted STORY-795 rework.
# Source: dispatch_795.py:18 evidence.
_PR_244_DIFF = '''\
diff --git a/tests/test_lookback.py b/tests/test_lookback.py
new file mode 100644
--- /dev/null
+++ b/tests/test_lookback.py
@@ -0,0 +1,25 @@
+"""Tests for lookback window configuration."""
+import pytest
+from sp_api.api import Orders
+
+
+class TestLookbackWindow:
+    """Lookback window configuration tests."""
+
+    def test_lookback_window_configurable(self):
+        """Verify lookback window parameter is respected."""
+        url = "https://api.example.com/orders?lookback=48"
+        # BAD: this assertion always passes regardless of url content
+        assert 48 in url or True
+
+    def test_default_lookback(self):
+        """Verify default lookback is 24 hours."""
+        url = "https://api.example.com/orders?lookback=24"
+        assert "lookback=24" in url
'''


def test_pr_244_or_true_antipattern_caught():
    """REPLAY -- PR #244 (STORY-766): `assert 48 in url or True` must be caught.

    This is the incident that shipped a no-op test through Morris review.
    The rework was hand-dispatched as STORY-795 (dispatch_795.py:18).

    Acceptance:
    - check_new_behavior_assertions flags at least 1 finding
    - The finding type is OR_TRUE_BYPASS
    - The finding references test_lookback_window_configurable
    - test_default_lookback (which has a real assertion) is NOT flagged
    """
    pr_files = ["tests/test_lookback.py"]
    findings = check_new_behavior_assertions(_PR_244_DIFF, pr_files)

    # Must find the anti-pattern
    assert len(findings) >= 1, (
        "PR #244 `assert 48 in url or True` was NOT caught -- "
        "this is the exact incident that caused STORY-795 rework"
    )

    # Must be classified as OR_TRUE_BYPASS
    or_true_findings = [
        f for f in findings if f.anti_pattern == AntiPatternType.OR_TRUE_BYPASS
    ]
    assert len(or_true_findings) >= 1, (
        f"Expected OR_TRUE_BYPASS classification, got: "
        f"{[f.anti_pattern for f in findings]}"
    )

    # Must reference the correct function
    flagged_funcs = {f.function_name for f in findings}
    assert "test_lookback_window_configurable" in flagged_funcs

    # Must NOT flag the legitimate assertion in test_default_lookback
    assert "test_default_lookback" not in flagged_funcs, (
        "test_default_lookback has a legitimate assertion and should not be flagged"
    )


# ---------------------------------------------------------------------------
# REPLAY-3: /requeue-failed skill unavailable -- fallback registry unblocks
# ---------------------------------------------------------------------------


class TestReplay3RequeueFailed:
    """REPLAY-3: Simulating skill unavailable, the fallback registry returns
    runnable SQL within 60s."""

    def test_replay_3_requeue_failed_fallback_unblocks(self):
        """When /requeue-failed skill is unavailable, operator can:
        1. Look up the fallback via get_fallback()
        2. Get a rendered SQL block within 60s
        3. The SQL is a valid requeue command
        """
        # Step 1: Skill is "unavailable" -- operator queries the registry
        fb = get_fallback("requeue-failed")
        assert fb is not None, "requeue-failed must be registered"

        # Step 2: Render the fallback -- must complete within 60s
        start = time.monotonic()
        rendered = render_fallback("requeue-failed")
        elapsed = time.monotonic() - start
        assert elapsed < 60, f"render_fallback took {elapsed:.1f}s -- must be < 60s"

        # Step 3: The rendered output contains runnable SQL
        assert rendered is not None
        lower = rendered.lower()
        assert "update" in lower or "select" in lower, "Must contain a SQL command"
        assert "dispatch" in lower, "Must reference dispatch tables"
        assert "enqueued" in lower or "requeue" in lower, "Must requeue to 'enqueued'"

        # Step 4: The fallback also includes a runbook URL
        assert "http" in rendered, "Must include a runbook URL"

    def test_replay_3_all_five_skills_have_fallbacks(self):
        """All 5 operator skills have documented fallback paths -- no more
        undocumented skill outages."""
        skills = [
            "requeue-failed",
            "dispatch-recovery",
            "release-stale-claim",
            "dead-letter-purge",
            "force-claim",
        ]
        for skill in skills:
            fb = get_fallback(skill)
            assert fb is not None, f"Operator skill {skill} has no fallback registered"
            rendered = render_fallback(skill)
            assert rendered is not None, f"render_fallback({skill}) returned None"
            assert len(rendered) > 50, f"render_fallback({skill}) too short to be useful"


# ---------------------------------------------------------------------------
# STORY-919-class replay: new dispatch -> in_review without pr_number = 422
# ---------------------------------------------------------------------------


def _rec(data: dict) -> MagicMock:
    r = MagicMock()
    r.__getitem__ = lambda self, k: data[k]
    r.get = lambda k, d=None: data.get(k, d)
    for k, v in data.items():
        setattr(r, k, v)
    return r


class TestStory919ClassReplay:
    """A new dispatch attempting in_review without pr_number is rejected."""

    ROLLOUT = "2026-05-18T00:00:00Z"

    @pytest.mark.asyncio
    async def test_new_dispatch_without_pr_number_rejected_422(self):
        """Reproduces the STORY-919 bug class: agent opens PR but fails to
        stamp pr_number on dispatch_jobs before transitioning to in_review.

        With the new gate, this returns 422 for new dispatches."""
        import asyncpg

        from tech_dev_agents.ops_console.services.dispatch_v2_service import (
            DispatchV2Service,
            InvalidEventDataError,
        )

        created_at = datetime(2026, 5, 19, tzinfo=timezone.utc)

        conn = MagicMock(spec=asyncpg.Connection)
        txn = MagicMock()
        txn.__aenter__ = AsyncMock(return_value=txn)
        txn.__aexit__ = AsyncMock(return_value=False)
        conn.transaction = MagicMock(return_value=txn)

        async def mock_fetchrow(query, *args):
            q = query.lower()
            if "dispatch_leases" in q:
                return _rec({"lease_token": "valid-token"})
            elif "dispatch_state_current" in q:
                return _rec({"state": "leased"})
            elif "dispatch_jobs" in q:
                return _rec({
                    "repo": "test-repo",
                    "pr_number": None,
                    "created_at": created_at,
                })
            elif "dispatch_v2_events" in q:
                return _rec({"event_id": 42})
            return None

        conn.fetchrow = mock_fetchrow
        conn.execute = AsyncMock()

        svc = DispatchV2Service(conn)

        with patch.dict(os.environ, {"OPS_PR_NUMBER_ENFORCEMENT_ROLLOUT": self.ROLLOUT}):
            with pytest.raises(InvalidEventDataError, match="in_review requires pr_number"):
                await svc.transition(
                    job_id=str(uuid.uuid4()),
                    lease_token="valid-token",
                    event_type="submitted",
                    event_data={},
                )
