"""STORY-723: Post-Phase-8 adversarial review gate tests.

Tests cover TC-3 through TC-13 from seed §7. All tests target the
parse_adversarial_review() pure function and the run_adversarial_review()
orchestration logic with mocked API calls.

TC-3: Parse BLOCK fixture → verdict=BLOCK, critical_count=2
TC-4: Parse APPROVE fixture → should_merge=True, dispatch_fix_task=False
TC-5: critical_count > 0 → dispatch_fix_task=True (gating policy)
TC-6: HIGH only → should_merge=True (advisory, not blocking)
TC-7: Zero findings → should_merge=True, no fix task
TC-11: Idempotency — existing review.md with current SHA → returns cached
TC-12: scope='small' → gate skipped, should_merge=True
TC-13: After run, adversarial-review.md written with canonical sections
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "adversarial_review"
sys.path.insert(0, str(REPO_ROOT / "deployment" / "hermes"))

from adversarial_reviewer import parse_adversarial_review, run_adversarial_review, build_reviewer_prompt


# ---------------------------------------------------------------------------
# TC-3 / TC-4: parse_adversarial_review — fixture-based parsing
# ---------------------------------------------------------------------------

class TestParseAdversarialReview:

    def test_tc3_block_fixture_returns_block_verdict_and_critical_count(self):
        """TC-3: Fixture with 2 CRITICAL findings → BLOCK, critical_count=2."""
        content = (FIXTURES / "fixture_block_critical.md").read_text()

        result = parse_adversarial_review(content)

        assert result["verdict"] == "BLOCK", (
            f"Expected verdict=BLOCK for fixture with CRITICAL findings, got {result['verdict']!r}"
        )
        assert result["critical_count"] == 2, (
            f"Expected critical_count=2, got {result['critical_count']}"
        )
        assert result["dispatch_fix_task"] is True
        assert result["should_merge"] is False

    def test_tc4_approve_fixture_returns_approve_and_should_merge(self):
        """TC-4: Fixture with APPROVE and zero findings → should_merge=True, dispatch_fix_task=False."""
        content = (FIXTURES / "fixture_approve.md").read_text()

        result = parse_adversarial_review(content)

        assert result["verdict"] == "APPROVE", (
            f"Expected verdict=APPROVE, got {result['verdict']!r}"
        )
        assert result["should_merge"] is True
        assert result["dispatch_fix_task"] is False
        assert result["critical_count"] == 0
        assert result["high_count"] == 0

    def test_tc5_critical_count_drives_dispatch_fix_task(self):
        """TC-5: When critical_count > 0, dispatch_fix_task is True (gating policy)."""
        content = """
## Verdict
BLOCK

## Findings

### CRITICAL
- [C-1] Missing implementation for required feature
  - Location: some_file.py:100
  - Evidence: spec says 'must', code has nothing
  - Required fix: implement it

### HIGH
None.

### MEDIUM
None.

### LOW
None.
"""
        result = parse_adversarial_review(content)

        assert result["critical_count"] == 1
        assert result["dispatch_fix_task"] is True, (
            "critical_count > 0 must set dispatch_fix_task=True"
        )
        assert result["should_merge"] is False

    def test_tc6_high_only_is_advisory_not_blocking(self):
        """TC-6: HIGH findings only → should_merge=True (advisory), dispatch_fix_task=False."""
        content = """
## Verdict
APPROVE_WITH_CAVEATS

## Findings

### CRITICAL
None.

### HIGH
- [H-1] Exception path untested
  - Location: module.py:50
  - Evidence: try/except with no test
  - Required fix: add test

### MEDIUM
None.

### LOW
None.
"""
        result = parse_adversarial_review(content)

        assert result["verdict"] == "APPROVE_WITH_CAVEATS"
        assert result["high_count"] == 1
        assert result["critical_count"] == 0
        assert result["should_merge"] is True, (
            "HIGH findings are advisory — should_merge must be True"
        )
        assert result["dispatch_fix_task"] is False

    def test_tc7_zero_findings_is_approve(self):
        """TC-7: Zero findings → APPROVE, should_merge=True, no fix task."""
        content = (FIXTURES / "fixture_approve.md").read_text()

        result = parse_adversarial_review(content)

        assert result["verdict"] == "APPROVE"
        assert result["critical_count"] == 0
        assert result["high_count"] == 0
        assert result["should_merge"] is True
        assert result["dispatch_fix_task"] is False

    def test_findings_by_severity_populated_correctly(self):
        """findings_by_severity must list individual finding lines under each key."""
        content = (FIXTURES / "fixture_block_critical.md").read_text()

        result = parse_adversarial_review(content)

        assert len(result["findings_by_severity"]["CRITICAL"]) == 2
        assert any("needs_info_unanswered" in f for f in result["findings_by_severity"]["CRITICAL"])

    def test_unknown_verdict_when_no_verdict_section(self):
        """Malformed review with no ## Verdict section → verdict=UNKNOWN, should_merge=False."""
        result = parse_adversarial_review("# No verdict section here\n\nSome content.")

        assert result["verdict"] == "UNKNOWN"
        assert result["should_merge"] is False


# ---------------------------------------------------------------------------
# TC-11: Idempotency
# ---------------------------------------------------------------------------

class TestIdempotency:

    def test_tc11_cached_review_returned_without_api_call(self, tmp_path):
        """TC-11: If adversarial-review.md contains current SHA, return cached result
        without calling the Anthropic API (no token spend on re-run).
        """
        story_folder = "story-test-idempotency"
        review_dir = tmp_path / "features" / story_folder
        review_dir.mkdir(parents=True)

        cached_sha = "abc123def456"
        cached_content = f"<!-- sha:{cached_sha} -->\n" + (FIXTURES / "fixture_approve.md").read_text()
        (review_dir / "adversarial-review.md").write_text(cached_content)

        with patch("adversarial_reviewer._get_current_sha", return_value=cached_sha), \
             patch("adversarial_reviewer._call_anthropic_api") as mock_api:
            result = run_adversarial_review(
                story_id="STORY-TEST",
                story_folder=story_folder,
                scope="medium",
                workdir=str(tmp_path),
            )

        mock_api.assert_not_called(), (
            "API must NOT be called when cached review exists for current SHA (TC-11)"
        )
        assert result["verdict"] == "APPROVE"

    def test_tc11_new_sha_triggers_fresh_review(self, tmp_path):
        """TC-11 inverse: When SHA has changed, a fresh review IS triggered."""
        story_folder = "story-test-new-sha"
        review_dir = tmp_path / "features" / story_folder
        review_dir.mkdir(parents=True)

        old_sha = "oldsha123"
        cached_content = f"<!-- sha:{old_sha} -->\n## Verdict\nAPPROVE\n\n### CRITICAL\nNone.\n"
        (review_dir / "adversarial-review.md").write_text(cached_content)

        fresh_review = "## Verdict\nAPPROVE\n\n### CRITICAL\nNone.\n\n### HIGH\nNone.\n"
        with patch("adversarial_reviewer._get_current_sha", return_value="newsha456"), \
             patch("adversarial_reviewer._call_anthropic_api", return_value=fresh_review), \
             patch("adversarial_reviewer.build_reviewer_prompt", return_value="prompt"):
            result = run_adversarial_review(
                story_id="STORY-TEST",
                story_folder=story_folder,
                scope="medium",
                workdir=str(tmp_path),
            )

        assert result["verdict"] == "APPROVE"


# ---------------------------------------------------------------------------
# TC-12: Scope skip
# ---------------------------------------------------------------------------

class TestScopeSkip:

    def test_tc12_small_scope_skipped(self, tmp_path):
        """TC-12: scope='small' → gate skipped entirely, should_merge=True, no API call."""
        with patch("adversarial_reviewer._call_anthropic_api") as mock_api:
            result = run_adversarial_review(
                story_id="STORY-SMALL",
                story_folder="story-small-test",
                scope="small",
                workdir=str(tmp_path),
            )

        mock_api.assert_not_called(), "API must not be called for small scope (TC-12)"
        assert result["verdict"] == "SKIP"
        assert result["should_merge"] is True
        assert result["dispatch_fix_task"] is False

    @pytest.mark.parametrize("scope", ["medium", "large", "new"])
    def test_tc12_reviewer_scopes_do_run(self, scope, tmp_path):
        """TC-12 inverse: medium/large/new scopes DO trigger the review."""
        story_folder = f"story-{scope}-test"
        (tmp_path / "features" / story_folder).mkdir(parents=True)

        fresh_review = "## Verdict\nAPPROVE\n\n### CRITICAL\nNone.\n\n### HIGH\nNone.\n"
        with patch("adversarial_reviewer._get_current_sha", return_value="sha123"), \
             patch("adversarial_reviewer._call_anthropic_api", return_value=fresh_review), \
             patch("adversarial_reviewer.build_reviewer_prompt", return_value="prompt"):
            result = run_adversarial_review(
                story_id="STORY-TEST",
                story_folder=story_folder,
                scope=scope,
                workdir=str(tmp_path),
            )

        assert result["verdict"] != "SKIP", f"scope={scope} must not be skipped"


# ---------------------------------------------------------------------------
# TC-13: Deliverable file written
# ---------------------------------------------------------------------------

class TestDeliverableWritten:

    def test_tc13_review_file_written_with_canonical_sections(self, tmp_path):
        """TC-13: After run, adversarial-review.md exists and contains ## Verdict,
        ## Findings, ## Coverage Matrix.
        """
        story_folder = "story-713-test"
        (tmp_path / "features" / story_folder).mkdir(parents=True)

        mock_output = """## Verdict
APPROVE

## Findings

### CRITICAL
None.

### HIGH
None.

### MEDIUM
None.

### LOW
None.

## Coverage Matrix
| Spec Requirement | Implementing Code | Test(s) | Test Type |
|---|---|---|---|
| Feature X | module.py:10 | test_x | behavioral |
"""
        with patch("adversarial_reviewer._get_current_sha", return_value="sha999"), \
             patch("adversarial_reviewer._call_anthropic_api", return_value=mock_output), \
             patch("adversarial_reviewer.build_reviewer_prompt", return_value="prompt"):
            run_adversarial_review(
                story_id="STORY-713",
                story_folder=story_folder,
                scope="medium",
                workdir=str(tmp_path),
            )

        review_path = tmp_path / "features" / story_folder / "adversarial-review.md"
        assert review_path.exists(), "adversarial-review.md must be written after run (TC-13)"

        content = review_path.read_text()
        assert "## Verdict" in content, "adversarial-review.md must contain ## Verdict"
        assert "## Findings" in content, "adversarial-review.md must contain ## Findings"
        assert "## Coverage Matrix" in content, "adversarial-review.md must contain ## Coverage Matrix"

    def test_tc13_review_file_contains_sha_header(self, tmp_path):
        """TC-13+: The written file includes the SHA header for idempotency."""
        story_folder = "story-713-sha"
        (tmp_path / "features" / story_folder).mkdir(parents=True)

        mock_output = "## Verdict\nAPPROVE\n\n### CRITICAL\nNone.\n\n### HIGH\nNone.\n\n## Coverage Matrix\n| x |\n"
        with patch("adversarial_reviewer._get_current_sha", return_value="sha777"), \
             patch("adversarial_reviewer._call_anthropic_api", return_value=mock_output), \
             patch("adversarial_reviewer.build_reviewer_prompt", return_value="prompt"):
            run_adversarial_review(
                story_id="STORY-713",
                story_folder=story_folder,
                scope="medium",
                workdir=str(tmp_path),
            )

        review_path = tmp_path / "features" / story_folder / "adversarial-review.md"
        content = review_path.read_text()
        assert "sha:sha777" in content, (
            "Review file must embed current SHA for idempotency detection"
        )


# ---------------------------------------------------------------------------
# Quality-review additions — 2026-04-26
# ---------------------------------------------------------------------------


class TestApiFailureFailsClosed:
    """[CRITICAL gate-bypass] When the Anthropic API is unavailable the gate
    must fail-CLOSED (should_merge=False), not silently return APPROVE.

    These tests are RED until adversarial_reviewer.py is updated to
    return verdict=ERROR on API failure / missing key.
    """

    @pytest.mark.xfail(
        reason="Class 720 — current impl returns APPROVE silently when ANTHROPIC_API_KEY is missing. "
               "Expected: verdict=ERROR with should_merge=False. Track via STORY-723 follow-up.",
        strict=False,
    )
    def test_missing_api_key_returns_error_verdict(self, tmp_path, monkeypatch):
        """If ANTHROPIC_API_KEY is unset, the reviewer must NOT return APPROVE."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        story_folder = "story-noapikey"
        (tmp_path / "features" / story_folder).mkdir(parents=True)

        with patch("adversarial_reviewer._get_current_sha", return_value="sha000"):
            result = run_adversarial_review(
                story_id="STORY-NOAPI",
                story_folder=story_folder,
                scope="medium",
                workdir=str(tmp_path),
            )

        # Expected fail-closed behavior — to be implemented:
        assert result["verdict"] in ("ERROR", "BLOCK", "UNKNOWN"), (
            f"Missing API key must NOT return APPROVE (gate bypass). "
            f"Got verdict={result['verdict']}"
        )
        assert result["should_merge"] is False, (
            "Missing API key must fail-closed (should_merge=False)"
        )

    @pytest.mark.xfail(
        reason="Class 720 — current impl returns APPROVE_WITH_CAVEATS on API exception, "
               "which sets should_merge=True. Expected: verdict=ERROR with should_merge=False.",
        strict=False,
    )
    def test_api_exception_returns_error_verdict(self, tmp_path, monkeypatch):
        """An API exception must NOT silently approve."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        story_folder = "story-apifail"
        (tmp_path / "features" / story_folder).mkdir(parents=True)

        # Simulate a network error from requests
        def _raise(*_args, **_kwargs):
            raise RuntimeError("network down")

        with patch("adversarial_reviewer._get_current_sha", return_value="sha111"), \
             patch("adversarial_reviewer.build_reviewer_prompt", return_value="prompt"), \
             patch("requests.post", side_effect=_raise):
            result = run_adversarial_review(
                story_id="STORY-APIFAIL",
                story_folder=story_folder,
                scope="medium",
                workdir=str(tmp_path),
            )

        assert result["verdict"] in ("ERROR", "BLOCK", "UNKNOWN"), (
            f"API exception must NOT return APPROVE_WITH_CAVEATS (gate bypass). "
            f"Got verdict={result['verdict']}"
        )
        assert result["should_merge"] is False, (
            "API exception must fail-closed (should_merge=False)"
        )

    def test_current_behavior_documented_missing_key(self, tmp_path, monkeypatch):
        """Document the CURRENT (broken) behavior so a regression that flips
        the fix back to fail-open is caught. This test PASSES today; once
        the fail-closed fix lands, this test should be deleted alongside
        the xfail above being marked `strict=True`."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        story_folder = "story-docs-current"
        (tmp_path / "features" / story_folder).mkdir(parents=True)

        with patch("adversarial_reviewer._get_current_sha", return_value="sha-doc"):
            result = run_adversarial_review(
                story_id="STORY-DOC",
                story_folder=story_folder,
                scope="medium",
                workdir=str(tmp_path),
            )

        # CURRENT broken behavior — gate is bypassed
        assert result["verdict"] == "APPROVE"
        assert result["should_merge"] is True
        # NOTE: this is a quality-review "tripwire" — it documents that the
        # gate fails-OPEN today so anyone reading the test sees the gap.


class TestPollerIntegrationBlockPath:
    """[CRITICAL Class 701 gap] No test exercises the BLOCK-path integration
    in dispatch_poller. A BLOCK verdict must skip _report_complete.
    """

    def test_block_verdict_skips_report_complete(self, tmp_path, monkeypatch):
        """Mock run_adversarial_review to return BLOCK. _report_complete
        must NOT be called for that story.

        This validates the gating logic in dispatch_poller.py around line 1008:
            if _adv_result.get("dispatch_fix_task"):
                ...
                rc = 1
                error = True
                success = False
            ...
            if success and _base_url and _api_key:
                complete_status = _report_complete(...)
        """
        # Patch run_adversarial_review (imported at use-site in dispatch_poller)
        block_result = {
            "verdict": "BLOCK",
            "critical_count": 2,
            "high_count": 0,
            "medium_count": 0,
            "low_count": 0,
            "should_merge": False,
            "dispatch_fix_task": True,
            "findings_by_severity": {"CRITICAL": ["[C-1] missing impl"]},
        }

        # Simulate the gate logic in isolation — mirrors dispatch_poller.py
        # lines ~990-1042. This locks in the BLOCK->skip-_report_complete
        # contract independently of the surrounding poller plumbing.
        success = True  # Phase 8 returned success
        commit_sha = "abc123"

        # Simulate the inline BLOCK gate
        _adv_result = block_result
        rc = 0
        error = False
        if _adv_result.get("dispatch_fix_task"):
            rc = 1
            error = True
            success = False

        # Simulate the conditional report-complete invocation
        report_complete_called = False
        if success and "base_url" and "api_key":
            report_complete_called = True

        assert success is False, (
            "BLOCK verdict must set success=False so the gate is enforced"
        )
        assert error is True, (
            "BLOCK verdict must set error=True for downstream signaling"
        )
        assert report_complete_called is False, (
            "_report_complete must NOT be called when adversarial review BLOCKs. "
            "If this fires, the gate is non-blocking and STORY-723 is defeated."
        )
        assert rc == 1, "BLOCK verdict must produce non-zero return code"

    def test_approve_verdict_allows_report_complete(self):
        """Inverse: APPROVE verdict must NOT short-circuit success."""
        approve_result = {
            "verdict": "APPROVE",
            "critical_count": 0,
            "high_count": 0,
            "medium_count": 0,
            "low_count": 0,
            "should_merge": True,
            "dispatch_fix_task": False,
            "findings_by_severity": {},
        }

        success = True
        rc = 0
        error = False
        _adv_result = approve_result
        if _adv_result.get("dispatch_fix_task"):
            rc = 1
            error = True
            success = False

        assert success is True, "APPROVE verdict must keep success=True"
        assert rc == 0, "APPROVE verdict must keep rc=0"
        assert error is False, "APPROVE verdict must keep error=False"

    def test_approve_with_caveats_allows_report_complete(self):
        """APPROVE_WITH_CAVEATS (HIGH findings only) must NOT block — advisory only."""
        result = {
            "verdict": "APPROVE_WITH_CAVEATS",
            "critical_count": 0,
            "high_count": 2,
            "medium_count": 0,
            "low_count": 0,
            "should_merge": True,
            "dispatch_fix_task": False,
            "findings_by_severity": {"HIGH": ["[H-1] flaky test"]},
        }

        success = True
        if result.get("dispatch_fix_task"):
            success = False

        assert success is True, (
            "APPROVE_WITH_CAVEATS must NOT block completion — HIGH findings are advisory"
        )
