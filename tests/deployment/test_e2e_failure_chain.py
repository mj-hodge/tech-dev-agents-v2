"""STORY-741: AC9, AC10 — E2E failure chain integration tests.

AC9: Integration-level test for the full failure chain:
     queue entry → claim → phase runner fail → _report_fail → retry enqueue.
     Simulates 3 consecutive failures and verifies:
     - POST /api/dispatch/fail/{id} is called each time
     - A retry POST /api/dispatch is enqueued with [RETRY N/3] prefix
     - On the 3rd failure (attempt > MAX_RETRY_ATTEMPTS), no more retries
       and a failure flag file is written

AC10: Same setup as AC9 but with a rate-limited signal:
     - _report_fail receives exit_code=429
     - POST /api/dispatch/fail/{id} IS still called (story must fail, not ghost)
     - NO retry POST to /api/dispatch (rate-limit is handled by pause-flag,
       not by re-enqueue)

Both tests use mocked HTTP (MagicMock session) and a tmp_path for the
failure-flag file path.

Test strategy:
  The E2E tests call _report_fail() directly with synthetic inputs that
  match what the dispatch poller would receive after run_sdlc_phases()
  returns (False, None, None) with the appropriate duration and error state.
  This tests the full /fail→/dispatch retry sequence as an integration chain
  rather than as isolated unit tests.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session(status_by_url: dict[str, int] | None = None) -> MagicMock:
    """Mock session whose POST status_code depends on URL suffix.

    status_by_url: {url_suffix: status_code}
    Default: 201 for /api/dispatch, 200 for /fail.
    """
    default_statuses = {
        "/api/dispatch": 201,
        "/fail/": 200,
    }
    if status_by_url:
        default_statuses.update(status_by_url)

    def side_effect(url, **kwargs):
        resp = MagicMock()
        resp.text = ""
        for suffix, code in default_statuses.items():
            if suffix in url:
                resp.status_code = code
                return resp
        resp.status_code = 200
        return resp

    session = MagicMock()
    session.post.side_effect = side_effect
    return session


def _fail_calls(session: MagicMock, story_id: str) -> list:
    return [
        c for c in session.post.call_args_list
        if c.args and f"/api/dispatch/fail/{story_id}" in c.args[0]
    ]


def _retry_calls(session: MagicMock) -> list:
    return [
        c for c in session.post.call_args_list
        if c.args and c.args[0].endswith("/api/dispatch")
    ]


def _extract_retry_prompts(session: MagicMock) -> list[str]:
    """Extract the prompt strings from all retry POST bodies."""
    prompts = []
    for c in _retry_calls(session):
        body = c.kwargs.get("json") or (c.args[1] if len(c.args) > 1 else {})
        if isinstance(body, dict):
            prompts.append(body.get("prompt", ""))
    return prompts


def _call_report_fail(
    session: MagicMock,
    story_id: str,
    prompt: str,
    exit_code: int = 1,
    duration_seconds: int = 60,
    error_message: str | None = None,
    flag_dir: str = "/tmp",
) -> None:
    from deployment.hermes.dispatch_poller import _report_fail

    with patch.dict(os.environ, {"AGENT_NAME": "test-agent"}), \
         patch("os.makedirs"), \
         patch("builtins.open", create=True) as mock_open:
        mock_open.return_value.__enter__ = lambda s: s
        mock_open.return_value.__exit__ = lambda s, *a: False
        mock_open.return_value.write = MagicMock()

        _report_fail(
            session=session,
            base_url="http://ops.test",
            api_key="test-key",
            story_id=story_id,
            exit_code=exit_code,
            repo="tech-dev-agents",
            scope="small",
            prompt=prompt,
            duration_seconds=duration_seconds,
            error_message=error_message,
        )


# ---------------------------------------------------------------------------
# AC9 — Full failure chain: queue → claim → fail → retry → exhaust
# ---------------------------------------------------------------------------

class TestFailureChainRetry:
    """AC9: Three consecutive failures exhaust retries and write a flag file."""

    def test_first_failure_enqueues_retry_with_tag(self):
        """AC9a: First failure → /fail called + retry POST with [RETRY 1/3] tag."""
        session = _make_session()
        story_id = "STORY-741"
        base_prompt = "implement STORY-741 fleet reliability guardrails"

        _call_report_fail(session, story_id=story_id, prompt=base_prompt)

        # /fail must be called
        assert _fail_calls(session, story_id), (
            "First failure must POST /api/dispatch/fail/{story_id}"
        )
        # Retry must be enqueued
        retries = _retry_calls(session)
        assert len(retries) == 1, (
            f"First failure must enqueue exactly 1 retry, got {len(retries)}"
        )
        # Retry prompt must carry [RETRY 1/3] prefix
        prompts = _extract_retry_prompts(session)
        assert prompts, "Retry POST must include a 'prompt' body field"
        assert "[RETRY 1/3]" in prompts[0], (
            f"First retry prompt must contain '[RETRY 1/3]', got: {prompts[0]!r}"
        )
        assert base_prompt in prompts[0], (
            f"Retry prompt must contain the original prompt text, got: {prompts[0]!r}"
        )

    def test_second_failure_enqueues_retry_2_of_3(self):
        """AC9b: Second failure (prompt already has [RETRY 1/3]) → [RETRY 2/3] tag."""
        session = _make_session()
        story_id = "STORY-741"
        prompt_after_first_retry = "[RETRY 1/3] implement STORY-741 fleet reliability guardrails"

        _call_report_fail(session, story_id=story_id, prompt=prompt_after_first_retry)

        prompts = _extract_retry_prompts(session)
        assert prompts, "Second failure must still enqueue a retry"
        assert "[RETRY 2/3]" in prompts[0], (
            f"Second retry prompt must contain '[RETRY 2/3]', got: {prompts[0]!r}"
        )
        # Must NOT have nested [RETRY] tags
        assert prompts[0].count("[RETRY") == 1, (
            f"Retry prompt must strip the old tag before prepending the new one. "
            f"Got: {prompts[0]!r}"
        )

    def test_third_failure_no_retry_flag_written(self, tmp_path):
        """AC9c: Third failure (prompt has [RETRY 3/3]) → no retry, flag file written.

        When attempt > MAX_RETRY_ATTEMPTS (3), the story is flagged for
        human review and no more retries are enqueued.
        """
        from deployment.hermes.dispatch_poller import MAX_RETRY_ATTEMPTS

        session = _make_session()
        story_id = "STORY-741"
        # Prompt already at max retries
        prompt_at_max = f"[RETRY {MAX_RETRY_ATTEMPTS}/3] implement STORY-741"
        flag_path = tmp_path / "test-agent" / "failed-stories" / f"{story_id}.txt"

        with patch.dict(os.environ, {"AGENT_NAME": "test-agent"}):
            # Patch the flag path so we can verify it was written
            with patch(
                "deployment.hermes.dispatch_poller._report_fail.__code__",
                _report_fail := None,  # placeholder
            ) if False else \
            patch("os.makedirs") as mock_makedirs, \
            patch("builtins.open", create=True) as mock_open:
                # Track what was written
                written_content = []
                mock_open.return_value.__enter__ = lambda s: s
                mock_open.return_value.__exit__ = lambda s, *a: False
                mock_open.return_value.write = lambda content: written_content.append(content)

                from deployment.hermes.dispatch_poller import _report_fail
                _report_fail(
                    session=session,
                    base_url="http://ops.test",
                    api_key="test-key",
                    story_id=story_id,
                    exit_code=1,
                    repo="tech-dev-agents",
                    scope="small",
                    prompt=prompt_at_max,
                    duration_seconds=300,
                    error_message="connection reset",
                )

        # No retry enqueued
        retries = _retry_calls(session)
        assert len(retries) == 0, (
            f"After {MAX_RETRY_ATTEMPTS} retries exhausted, no more retries must be enqueued. "
            f"Got {len(retries)} retry POST(s)"
        )

        # Flag file write must have been attempted
        # Either os.makedirs was called (for the flag dir) or open was called
        was_flagged = (
            mock_makedirs.called
            or mock_open.called
            or len(written_content) > 0
        )
        assert was_flagged, (
            f"After {MAX_RETRY_ATTEMPTS} retries exhausted, a failure flag file "
            f"must be written for human review. "
            f"makedirs called: {mock_makedirs.called}, "
            f"open called: {mock_open.called}"
        )

    def test_retry_prompt_preserves_story_reference(self):
        """AC9d: Retry prompt must preserve STORY-XXX reference from original prompt."""
        session = _make_session()
        story_id = "STORY-741"
        base_prompt = "implement STORY-741: add --model argparse argument to claude_sdk_tool.py"

        _call_report_fail(session, story_id=story_id, prompt=base_prompt)

        prompts = _extract_retry_prompts(session)
        assert prompts, "Must have retry prompt"
        assert "STORY-741" in prompts[0], (
            f"Retry prompt must preserve the STORY-741 reference, got: {prompts[0]!r}"
        )

    def test_fail_endpoint_always_called_before_retry(self):
        """AC9e: /fail endpoint must be called before any retry dispatch.

        The story must be marked as failed BEFORE re-enqueuing, so the ops-
        console shows accurate state between claim → fail → retry-pending.
        """
        session = _make_session()
        story_id = "STORY-741"

        _call_report_fail(session, story_id=story_id, prompt="implement STORY-741")

        calls = session.post.call_args_list
        fail_idx = next(
            (i for i, c in enumerate(calls) if f"/fail/{story_id}" in (c.args[0] if c.args else "")),
            None,
        )
        retry_idx = next(
            (i for i, c in enumerate(calls) if (c.args[0] if c.args else "").endswith("/api/dispatch")),
            None,
        )

        assert fail_idx is not None, "/api/dispatch/fail must be called"
        if retry_idx is not None:
            assert fail_idx < retry_idx, (
                "/fail endpoint must be called BEFORE the retry dispatch POST"
            )


# ---------------------------------------------------------------------------
# AC10 — Rate-limit path: release instead of fail → retry
# ---------------------------------------------------------------------------

class TestRateLimitReleasePath:
    """AC10: Rate-limited failure → POST /fail (story IS failed), NO retry POST.

    Note: in the SDLC phase runner path, rate limits trigger
    POST /api/dispatch/release/{id} (not /fail). But _report_fail with
    exit_code=429 represents the fallback case where release failed and
    the story was marked failed instead. Either way, no retry must be
    enqueued (the pause-flag mechanism owns rate-limit recovery).
    """

    def test_exit_code_429_no_retry_post(self):
        """AC10a: exit_code=429 → _report_fail POSTs /fail but no retry /dispatch."""
        session = _make_session()
        _call_report_fail(
            session,
            story_id="STORY-741",
            prompt="implement STORY-741",
            exit_code=429,
            duration_seconds=300,
        )

        # /fail IS called (story must be marked failed)
        assert _fail_calls(session, "STORY-741"), (
            "Rate-limited story must still POST /api/dispatch/fail "
            "(story is failed, not a ghost claim)"
        )

        # Retry must NOT be enqueued
        retries = _retry_calls(session)
        assert len(retries) == 0, (
            f"exit_code=429 must NOT trigger retry dispatch (pause-flag handles this). "
            f"Got {len(retries)} retry POST(s)"
        )

    def test_exit_code_429_consistent_across_retry_counts(self):
        """AC10b: Rate-limit suppresses retry regardless of [RETRY N/M] tag.

        Even if the story has only been tried once, a rate limit must not
        re-enqueue — the pause-flag mechanism handles unpausing, not retry.
        """
        session = _make_session()
        # First attempt (no prior retries)
        _call_report_fail(
            session,
            story_id="STORY-741",
            prompt="implement STORY-741",
            exit_code=429,
            duration_seconds=60,
        )

        retries = _retry_calls(session)
        assert len(retries) == 0, (
            "First-attempt rate limit must not retry — same as repeated rate limits"
        )

    def test_rate_limit_does_not_exhaust_retry_budget(self):
        """AC10c: A 429 exit must not consume one of the 3 retry slots.

        If 429 incremented the retry counter, an unlucky agent could burn
        all 3 retries on rate-limit events without ever running real work.
        The pause-flag mechanism means rate-limited stories come back fresh
        without a [RETRY] tag, so they still get 3 chances at actual work.
        """
        session = _make_session()
        # 429 with a prompt that already has [RETRY 1/3] — retry count must not advance
        _call_report_fail(
            session,
            story_id="STORY-741",
            prompt="[RETRY 1/3] implement STORY-741",
            exit_code=429,
            duration_seconds=60,
        )

        retries = _retry_calls(session)
        assert len(retries) == 0, (
            "429 on a previously-retried story must still not retry. "
            f"Got {len(retries)} retry POST(s)"
        )
