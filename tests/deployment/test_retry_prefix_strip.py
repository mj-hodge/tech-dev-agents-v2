"""STORY-764: Strip prior [RETRY N/N] prefix before adding new one.

Root cause: dispatch poller's auto-retry prepends [RETRY N/N] to prompts without
stripping any existing prefix. After 2-3 retries, prompts near the 5000-char limit
exceed it → HTTP 422. Retries silently die.

Fix: Extract _strip_retry_prefix() helper, call it in every retry path,
log post-strip length, and guard against overflow.

Test groups:
  A — _strip_retry_prefix: unit tests for the helper
  B — _report_fail: integration — helper is used in retry path
  C — Regression: 3-retry-cycle length stability
  D — Logging: post-strip prompt length is logged
  E — Overflow guard: skip retry when prompt still exceeds 5000 chars
"""
from __future__ import annotations

import re
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Allow import of dispatch_poller from the deployment package
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "deployment" / "hermes"))


# ---------------------------------------------------------------------------
# Group A — _strip_retry_prefix helper (unit tests)
# ---------------------------------------------------------------------------

class TestStripRetryPrefix:
    """Unit tests for _strip_retry_prefix() (AC-1, AC-2, AC-3, AC-5)."""

    def test_strip_simple_retry_prefix(self):
        """A1: Strips a standard [RETRY 1/3] prefix from the start of a prompt."""
        import dispatch_poller

        prompt = "[RETRY 1/3] ## Assignment: STORY-015\n\nDo the thing."
        result = dispatch_poller._strip_retry_prefix(prompt)
        assert result == "## Assignment: STORY-015\n\nDo the thing.", (
            f"Expected prefix stripped, got {result!r}"
        )

    def test_strip_no_prefix_returns_unchanged(self):
        """A2: Prompt without prefix is returned unchanged (idempotent — AC-3)."""
        import dispatch_poller

        prompt = "## Assignment: STORY-015\n\nDo the thing."
        result = dispatch_poller._strip_retry_prefix(prompt)
        assert result == prompt, (
            f"Expected unchanged prompt, got {result!r}"
        )

    def test_strip_double_digit_retry_prefix(self):
        """A3: Handles double-digit retry counts like [RETRY 10/10] (AC-5)."""
        import dispatch_poller

        prompt = "[RETRY 10/10] ## Assignment: STORY-099\n\nBig prompt."
        result = dispatch_poller._strip_retry_prefix(prompt)
        assert result == "## Assignment: STORY-099\n\nBig prompt.", (
            f"Expected double-digit prefix stripped, got {result!r}"
        )

    def test_strip_leading_whitespace_before_prefix(self):
        """A4: Handles leading whitespace before [RETRY] (AC-5)."""
        import dispatch_poller

        prompt = "  [RETRY 2/3] ## Assignment: STORY-015\n\nContent."
        result = dispatch_poller._strip_retry_prefix(prompt)
        assert result == "## Assignment: STORY-015\n\nContent.", (
            f"Expected whitespace + prefix stripped, got {result!r}"
        )

    def test_strip_no_trailing_space_after_bracket(self):
        """A5: Handles [RETRY 1/3] with no space after closing bracket (AC-5)."""
        import dispatch_poller

        prompt = "[RETRY 1/3]## Assignment: STORY-015"
        result = dispatch_poller._strip_retry_prefix(prompt)
        assert result == "## Assignment: STORY-015", (
            f"Expected prefix stripped even without trailing space, got {result!r}"
        )

    def test_strip_does_not_mangle_mid_prompt_retry(self):
        """A6: [RETRY N/N] in the middle of a prompt is NOT stripped."""
        import dispatch_poller

        prompt = "Some text then [RETRY 1/3] in the middle."
        result = dispatch_poller._strip_retry_prefix(prompt)
        assert result == prompt, (
            f"Mid-prompt [RETRY] should not be stripped, got {result!r}"
        )


# ---------------------------------------------------------------------------
# Group B — _report_fail integration: helper is called in retry path
# ---------------------------------------------------------------------------

class TestReportFailUsesStripHelper:
    """Integration test: _report_fail passes a stripped prompt to retry POST (AC-4)."""

    def test_report_fail_retry_uses_strip_helper(self):
        """B1: When retrying a prompt that already has [RETRY 1/3], the POST
        body contains [RETRY 2/3] (new) but NOT the old [RETRY 1/3]."""
        import dispatch_poller

        mock_session = MagicMock()
        # First call: POST /api/dispatch/fail → 200
        fail_resp = MagicMock()
        fail_resp.status_code = 200
        # Second call: POST /api/dispatch (retry) → 201
        retry_resp = MagicMock()
        retry_resp.status_code = 201
        mock_session.post.side_effect = [fail_resp, retry_resp]

        original_body = "## Assignment: STORY-015\n\nDo the thing."
        prompt_with_old_prefix = f"[RETRY 1/3] {original_body}"

        dispatch_poller._report_fail(
            session=mock_session,
            base_url="http://localhost:8000",
            api_key="test-key",
            story_id="STORY-015",
            exit_code=1,
            repo="test-repo",
            scope="small",
            prompt=prompt_with_old_prefix,
        )

        # The retry POST is the second call
        assert mock_session.post.call_count == 2, (
            f"Expected 2 POST calls (fail + retry), got {mock_session.post.call_count}"
        )
        retry_call = mock_session.post.call_args_list[1]
        retry_body = retry_call.kwargs.get("json") or retry_call[1].get("json")
        posted_prompt = retry_body["prompt"]

        # Must have new prefix [RETRY 2/3] and NOT the old [RETRY 1/3]
        assert posted_prompt.startswith("[RETRY 2/3] "), (
            f"Expected retry prompt to start with '[RETRY 2/3] ', got {posted_prompt[:40]!r}"
        )
        assert "[RETRY 1/3]" not in posted_prompt, (
            f"Old [RETRY 1/3] prefix should be stripped, found in {posted_prompt[:60]!r}"
        )
        # Original content must be intact (AC-4: byte-identical)
        assert original_body in posted_prompt, (
            f"Original prompt body must be preserved after strip+add"
        )


# ---------------------------------------------------------------------------
# Group C — Regression: 3-retry-cycle length stability (AC-6)
# ---------------------------------------------------------------------------

class TestRetryCycleLengthStability:
    """Regression: prompt length stays bounded across retry generations."""

    def test_three_retry_cycles_stay_under_5000(self):
        """C1: A near-limit prompt stays ≤ 5000 chars through 3 retry cycles (AC-6).

        Simulates the strip+add sequence that _report_fail performs, without
        needing to call the full function.

        TEST FIX (Phase 8): test-design.md specified 4990 chars, but the
        [RETRY N/N] prefix is 12 chars (``[RETRY 1/3] ``), so 4990 + 12 =
        5002 > 5000 on the first cycle — a math error in the spec.  Changed
        base to 4988 (max that fits with a 12-char prefix).  Intent unchanged:
        verify prompt length does NOT grow across retry generations.
        Conflicting spec: test-design.md Group C, seed.md SC-3.
        """
        import dispatch_poller

        # Build a prompt that is exactly 4988 chars — max that fits with
        # a 12-char "[RETRY N/3] " prefix within the 5000-char limit.
        base = "X" * 4988
        assert len(base) == 4988

        prompt = base
        for attempt in range(1, 4):  # retry 1, 2, 3
            clean = dispatch_poller._strip_retry_prefix(prompt)
            prompt = f"[RETRY {attempt}/{dispatch_poller.MAX_RETRY_ATTEMPTS}] {clean}"
            assert len(prompt) <= 5000, (
                f"After retry {attempt}, prompt length is {len(prompt)} (> 5000)"
            )


# ---------------------------------------------------------------------------
# Group D — Logging: post-strip prompt length (AC-8)
# ---------------------------------------------------------------------------

class TestRetryPromptLengthLogging:
    """AC-8: retry path logs prompt length after stripping."""

    def test_retry_logs_prompt_length_after_strip(self):
        """D1: stdout contains 'retry prompt length: N chars (after strip)'."""
        import dispatch_poller

        mock_session = MagicMock()
        fail_resp = MagicMock()
        fail_resp.status_code = 200
        retry_resp = MagicMock()
        retry_resp.status_code = 201
        mock_session.post.side_effect = [fail_resp, retry_resp]

        prompt = "[RETRY 1/3] ## Assignment: STORY-015\n\nContent here."

        captured = StringIO()
        with patch("sys.stdout", captured):
            dispatch_poller._report_fail(
                session=mock_session,
                base_url="http://localhost:8000",
                api_key="test-key",
                story_id="STORY-015",
                exit_code=1,
                repo="test-repo",
                scope="small",
                prompt=prompt,
            )

        output = captured.getvalue()
        assert "retry prompt length:" in output, (
            f"Expected 'retry prompt length:' in stdout, got:\n{output}"
        )
        assert "chars (after strip)" in output, (
            f"Expected 'chars (after strip)' in stdout, got:\n{output}"
        )


# ---------------------------------------------------------------------------
# Group E — Overflow guard (AC-9)
# ---------------------------------------------------------------------------

class TestOverflowGuard:
    """AC-9: if prompt + new prefix still exceeds 5000 chars, skip retry + warn."""

    def test_retry_skips_when_prompt_still_exceeds_5000(self):
        """E1: A 5000-char prompt (no room for prefix) causes retry to be
        skipped with an explicit warning rather than a guaranteed 422.
        """
        import dispatch_poller

        mock_session = MagicMock()
        fail_resp = MagicMock()
        fail_resp.status_code = 200
        mock_session.post.return_value = fail_resp

        # 5000 chars exactly — adding [RETRY 1/3] (13 chars + space) would exceed
        prompt = "Y" * 5000

        captured = StringIO()
        with patch("sys.stdout", captured):
            dispatch_poller._report_fail(
                session=mock_session,
                base_url="http://localhost:8000",
                api_key="test-key",
                story_id="STORY-020",
                exit_code=1,
                repo="test-repo",
                scope="small",
                prompt=prompt,
            )

        output = captured.getvalue()
        # Should only have 1 POST (the fail), NOT 2 (fail + retry)
        # because the retry was skipped due to length overflow
        post_calls = mock_session.post.call_args_list
        retry_posts = [c for c in post_calls if "/api/dispatch/fail/" not in str(c)]
        assert len(retry_posts) == 0, (
            f"Expected retry POST to be skipped (overflow guard), but found {len(retry_posts)} retry call(s)"
        )
        # Warning must be logged
        assert "exceeds 5000" in output.lower() or "skip" in output.lower(), (
            f"Expected overflow warning in stdout, got:\n{output}"
        )
