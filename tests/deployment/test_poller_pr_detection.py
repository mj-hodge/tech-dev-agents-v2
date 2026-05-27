"""STORY-903: Deterministic PR detection in dispatch_poller_v2.

Tests for the three-level resolution chain:
  1. Regex fast path  (_extract_pr_number_from_output)
  2. GitHub REST fallback (_lookup_pr_by_branch)
  3. Null → needs_info

Groups:
  A — Regex fast path wins; REST NOT called
  B — Fallback path: regex misses, REST returns PR
  C — Both miss → needs_info
  D — Error handling (4xx / 5xx / network errors)
  E — Branch derivation from story_id
  F — REST response parsing (most-recent-PR selection)
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "deployment" / "hermes"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_claim(**kwargs):
    from deployment.hermes.dispatch_poller_v2 import _ActiveClaim
    defaults = dict(
        job_id="job-903",
        lease_token="token-903",
        expires_at="2099-01-01T00:00:00Z",
        repo="tech-dev-agents",
        story_id="STORY-903",
        prompt="implement the fix",
        scope="small",
        branch=None,
    )
    defaults.update(kwargs)
    return _ActiveClaim(**defaults)


def _fake_response(body: list | dict) -> MagicMock:
    """Return a mock that mimics urllib.request.urlopen context manager."""
    encoded = json.dumps(body).encode()
    mock_resp = MagicMock()
    mock_resp.read.return_value = encoded
    mock_resp.__enter__ = lambda self: self
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


# ---------------------------------------------------------------------------
# Group A — Regex fast path wins; REST NOT called
# ---------------------------------------------------------------------------

class TestRegexFastPath:
    """When output contains 'PR #N', regex fires; REST is never invoked."""

    def test_regex_match_returns_submitted(self):
        """TC-1: regex detects PR #341 → event_type submitted, pr_number=341."""
        from deployment.hermes import dispatch_poller_v2 as mod

        with patch.object(mod, "_lookup_pr_by_branch") as mock_rest:
            event_type, event_data = mod._success_transition_payload(
                "All done. Opened PR #341 for review.",
                story_id="STORY-903",
                repo="tech-dev-agents",
            )

        assert event_type == "submitted"
        assert event_data["pr_number"] == 341
        assert "output_summary" in event_data
        mock_rest.assert_not_called()

    def test_regex_match_pr_number_no_hash(self):
        """Regex also matches 'PR 99' (no hash)."""
        from deployment.hermes import dispatch_poller_v2 as mod

        with patch.object(mod, "_lookup_pr_by_branch") as mock_rest:
            event_type, event_data = mod._success_transition_payload(
                "Created PR 99 successfully.",
                story_id="STORY-903",
                repo="tech-dev-agents",
            )

        assert event_type == "submitted"
        assert event_data["pr_number"] == 99
        mock_rest.assert_not_called()

    def test_regex_match_no_story_id_still_works(self):
        """Regex path requires no story_id/repo — backward compat."""
        from deployment.hermes import dispatch_poller_v2 as mod

        event_type, event_data = mod._success_transition_payload(
            "Opened PR #200."
        )
        assert event_type == "submitted"
        assert event_data["pr_number"] == 200


# ---------------------------------------------------------------------------
# Group B — Fallback path: regex misses, REST returns PR
# ---------------------------------------------------------------------------

class TestGhRestFallback:
    """When regex misses, _lookup_pr_by_branch is called and its result wins."""

    def test_gh_fallback_called_when_regex_misses(self):
        """TC-2: bare GitHub URL in output → regex misses → fallback returns 278."""
        from deployment.hermes import dispatch_poller_v2 as mod

        output = (
            "All phases complete.\n"
            "https://github.com/hpi-gorillacommerce/tech-dev-agents/pull/278\n"
        )
        with patch.object(mod, "_lookup_pr_by_branch", return_value=278) as mock_rest:
            event_type, event_data = mod._success_transition_payload(
                output,
                story_id="STORY-903",
                repo="tech-dev-agents",
            )

        assert event_type == "submitted"
        assert event_data["pr_number"] == 278
        mock_rest.assert_called_once()

    def test_gh_fallback_uses_claim_branch_when_set(self):
        """TC-3: explicit branch passed through to _lookup_pr_by_branch."""
        from deployment.hermes import dispatch_poller_v2 as mod

        with patch.object(mod, "_lookup_pr_by_branch", return_value=99) as mock_rest:
            mod._success_transition_payload(
                "no PR text here",
                story_id="STORY-99",
                repo="tech-dev-agents",
                branch="story-99/some-slug",
            )

        call_kwargs = mock_rest.call_args
        # branch must be forwarded exactly
        assert call_kwargs.kwargs.get("branch") == "story-99/some-slug" or (
            len(call_kwargs.args) >= 3 and call_kwargs.args[2] == "story-99/some-slug"
        )

    def test_fallback_not_called_without_story_id(self):
        """No story_id → no REST call → needs_info."""
        from deployment.hermes import dispatch_poller_v2 as mod

        with patch.object(mod, "_lookup_pr_by_branch") as mock_rest:
            event_type, _ = mod._success_transition_payload(
                "Phase 2 complete. No PR yet.",
                repo="tech-dev-agents",
                # no story_id
            )

        mock_rest.assert_not_called()
        assert event_type == "needs_info"

    def test_fallback_not_called_without_repo(self):
        """No repo → no REST call → needs_info."""
        from deployment.hermes import dispatch_poller_v2 as mod

        with patch.object(mod, "_lookup_pr_by_branch") as mock_rest:
            event_type, _ = mod._success_transition_payload(
                "Phase 2 complete. No PR yet.",
                story_id="STORY-903",
                # no repo
            )

        mock_rest.assert_not_called()
        assert event_type == "needs_info"


# ---------------------------------------------------------------------------
# Group C — Both miss → needs_info
# ---------------------------------------------------------------------------

class TestBothMiss:
    """When regex and REST both return None, event is needs_info."""

    def test_both_miss_returns_needs_info(self):
        """TC-4: regex + REST both miss → needs_info, reason=missing_pr_linkage."""
        from deployment.hermes import dispatch_poller_v2 as mod

        with patch.object(mod, "_lookup_pr_by_branch", return_value=None):
            event_type, event_data = mod._success_transition_payload(
                "Phase 2 complete. Research done.",
                story_id="STORY-903",
                repo="tech-dev-agents",
            )

        assert event_type == "needs_info"
        assert event_data["reason"] == "missing_pr_linkage"
        assert "question" in event_data
        assert "output_summary" in event_data

    def test_needs_info_has_output_summary(self):
        """output_summary is present even on needs_info path."""
        from deployment.hermes import dispatch_poller_v2 as mod

        with patch.object(mod, "_lookup_pr_by_branch", return_value=None):
            _, event_data = mod._success_transition_payload(
                "x" * 600,
                story_id="STORY-903",
                repo="tech-dev-agents",
            )

        assert len(event_data["output_summary"]) <= 500


# ---------------------------------------------------------------------------
# Group D — Error handling (4xx / 5xx / network)
# ---------------------------------------------------------------------------

class TestRestErrorHandling:
    """_lookup_pr_by_branch must never crash the poller on API errors."""

    def test_http_403_returns_none(self):
        """TC-5: 403 Forbidden → None, no exception."""
        from deployment.hermes import dispatch_poller_v2 as mod

        exc = urllib.error.HTTPError(
            url="https://api.github.com/repos/x/y/pulls",
            code=403,
            msg="Forbidden",
            hdrs=None,
            fp=None,
        )
        with patch("urllib.request.urlopen", side_effect=exc), \
             patch.dict("os.environ", {"GITHUB_TOKEN": "test-token"}):
            result = mod._lookup_pr_by_branch(
                story_id="STORY-903",
                repo="tech-dev-agents",
            )

        assert result is None

    def test_http_404_returns_none(self):
        """TC-5b: 404 Not Found → None."""
        from deployment.hermes import dispatch_poller_v2 as mod

        exc = urllib.error.HTTPError(
            url="https://api.github.com/repos/x/y/pulls",
            code=404,
            msg="Not Found",
            hdrs=None,
            fp=None,
        )
        with patch("urllib.request.urlopen", side_effect=exc), \
             patch.dict("os.environ", {"GITHUB_TOKEN": "test-token"}):
            result = mod._lookup_pr_by_branch(
                story_id="STORY-903",
                repo="tech-dev-agents",
            )

        assert result is None

    def test_http_500_returns_none(self):
        """TC-7: 500 Server Error → None."""
        from deployment.hermes import dispatch_poller_v2 as mod

        exc = urllib.error.HTTPError(
            url="https://api.github.com/repos/x/y/pulls",
            code=500,
            msg="Internal Server Error",
            hdrs=None,
            fp=None,
        )
        with patch("urllib.request.urlopen", side_effect=exc), \
             patch.dict("os.environ", {"GITHUB_TOKEN": "test-token"}):
            result = mod._lookup_pr_by_branch(
                story_id="STORY-903",
                repo="tech-dev-agents",
            )

        assert result is None

    def test_network_error_returns_none(self):
        """TC-6: OSError (connection refused) → None."""
        from deployment.hermes import dispatch_poller_v2 as mod

        with patch("urllib.request.urlopen", side_effect=OSError("Connection refused")), \
             patch.dict("os.environ", {"GITHUB_TOKEN": "test-token"}):
            result = mod._lookup_pr_by_branch(
                story_id="STORY-903",
                repo="tech-dev-agents",
            )

        assert result is None

    def test_missing_github_token_returns_none(self):
        """No GITHUB_TOKEN → skip REST call, return None."""
        from deployment.hermes import dispatch_poller_v2 as mod

        with patch("urllib.request.urlopen") as mock_urlopen, \
             patch.dict("os.environ", {}, clear=True):
            # Remove GITHUB_TOKEN from env
            import os
            env_without_token = {k: v for k, v in os.environ.items() if k != "GITHUB_TOKEN"}
            with patch.dict("os.environ", env_without_token, clear=True):
                result = mod._lookup_pr_by_branch(
                    story_id="STORY-903",
                    repo="tech-dev-agents",
                )

        assert result is None
        # Should not even attempt the HTTP call when no token
        mock_urlopen.assert_not_called()


# ---------------------------------------------------------------------------
# Group E — Branch derivation
# ---------------------------------------------------------------------------

class TestBranchDerivation:
    """Branch name used in the GitHub REST query."""

    def test_branch_derived_from_story_id(self):
        """TC-8: STORY-123 → query uses story-123/work when no branch given."""
        from deployment.hermes import dispatch_poller_v2 as mod

        captured_url = []

        def fake_urlopen(req, timeout=None):
            captured_url.append(req.full_url if hasattr(req, "full_url") else str(req))
            return _fake_response([{"number": 50, "created_at": "2026-01-01T00:00:00Z"}])

        with patch("urllib.request.urlopen", side_effect=fake_urlopen), \
             patch.dict("os.environ", {"GITHUB_TOKEN": "tok"}):
            result = mod._lookup_pr_by_branch(
                story_id="STORY-123",
                repo="tech-dev-agents",
            )

        assert result == 50
        assert captured_url, "HTTP call was not made"
        assert "story-123" in captured_url[0]

    def test_explicit_branch_overrides_derived(self):
        """TC-10: explicit branch overrides story_id-derived branch."""
        from deployment.hermes import dispatch_poller_v2 as mod

        captured_url = []

        def fake_urlopen(req, timeout=None):
            captured_url.append(req.full_url if hasattr(req, "full_url") else str(req))
            return _fake_response([{"number": 77, "created_at": "2026-01-01T00:00:00Z"}])

        with patch("urllib.request.urlopen", side_effect=fake_urlopen), \
             patch.dict("os.environ", {"GITHUB_TOKEN": "tok"}):
            result = mod._lookup_pr_by_branch(
                story_id="STORY-123",
                repo="tech-dev-agents",
                branch="story-123/poller-pr-detection",
            )

        assert result == 77
        assert "story-123%2Fpoller-pr-detection" in captured_url[0] or \
               "story-123/poller-pr-detection" in captured_url[0]
        # Must NOT use the fallback story-123/work
        assert "story-123%2Fwork" not in captured_url[0] and \
               "story-123/work" not in captured_url[0]

    def test_no_story_number_skips_rest(self):
        """TC-9: story_id with no STORY-N pattern → None without HTTP call."""
        from deployment.hermes import dispatch_poller_v2 as mod

        with patch("urllib.request.urlopen") as mock_urlopen, \
             patch.dict("os.environ", {"GITHUB_TOKEN": "tok"}):
            result = mod._lookup_pr_by_branch(
                story_id="unknown-task",
                repo="tech-dev-agents",
            )

        assert result is None
        mock_urlopen.assert_not_called()


# ---------------------------------------------------------------------------
# Group F — REST response parsing
# ---------------------------------------------------------------------------

class TestRestResponseParsing:
    """_lookup_pr_by_branch parses the GitHub API response correctly."""

    def test_picks_most_recent_pr_by_created_at(self):
        """TC-11: returns highest created_at PR's number."""
        from deployment.hermes import dispatch_poller_v2 as mod

        body = [
            {"number": 10, "created_at": "2026-01-01T00:00:00Z"},
            {"number": 20, "created_at": "2026-02-01T00:00:00Z"},
            {"number": 5,  "created_at": "2025-12-01T00:00:00Z"},
        ]

        with patch("urllib.request.urlopen", return_value=_fake_response(body)), \
             patch.dict("os.environ", {"GITHUB_TOKEN": "tok"}):
            result = mod._lookup_pr_by_branch(
                story_id="STORY-903",
                repo="tech-dev-agents",
            )

        assert result == 20

    def test_empty_list_returns_none(self):
        """TC-12: empty PR list → None (no PRs on branch)."""
        from deployment.hermes import dispatch_poller_v2 as mod

        with patch("urllib.request.urlopen", return_value=_fake_response([])), \
             patch.dict("os.environ", {"GITHUB_TOKEN": "tok"}):
            result = mod._lookup_pr_by_branch(
                story_id="STORY-903",
                repo="tech-dev-agents",
            )

        assert result is None

    def test_single_pr_returned_correctly(self):
        """Single PR in list → its number returned."""
        from deployment.hermes import dispatch_poller_v2 as mod

        body = [{"number": 341, "created_at": "2026-05-01T00:00:00Z"}]

        with patch("urllib.request.urlopen", return_value=_fake_response(body)), \
             patch.dict("os.environ", {"GITHUB_TOKEN": "tok"}):
            result = mod._lookup_pr_by_branch(
                story_id="STORY-903",
                repo="tech-dev-agents",
            )

        assert result == 341

    def test_malformed_response_returns_none(self):
        """Non-list response → None without crash."""
        from deployment.hermes import dispatch_poller_v2 as mod

        with patch("urllib.request.urlopen", return_value=_fake_response({"unexpected": "dict"})), \
             patch.dict("os.environ", {"GITHUB_TOKEN": "tok"}):
            result = mod._lookup_pr_by_branch(
                story_id="STORY-903",
                repo="tech-dev-agents",
            )

        assert result is None
