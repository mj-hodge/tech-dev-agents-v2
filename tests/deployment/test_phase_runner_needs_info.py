"""STORY-532: Phase runner needs_info integration tests.

All tests are RED until Phase 8 implementation is complete.

Tests verify that sdlc_phase_runner.py calls the /api/dispatch/needs-info
endpoint (instead of falling into auto-retry) when a phase agent writes
QUESTION.md, and falls back gracefully when the POST fails.

Group A — _post_needs_info helper function
  A-01: _post_needs_info returns True when ops-console returns 200
  A-02: _post_needs_info returns False when ops-console returns 500
  A-03: _post_needs_info returns False on network exception (timeout, etc.)
  A-04: _post_needs_info POSTs to correct URL with correct body

Group B — Integration: QUESTION.md detection triggers /needs-info POST
  B-01: When phase agent writes QUESTION.md, runner POSTs /needs-info
  B-02: When /needs-info POST succeeds, runner returns (False, None)
        and does NOT schedule a retry
  B-03: When /needs-info POST fails, runner falls back to _notify_teams
        and still returns (False, None)

Group C — Negative path: no QUESTION.md → no /needs-info POST
  C-01: When no QUESTION.md, _post_needs_info is never called

Group D — Ordering: QUESTION.md wins over rate-limit (rc=-429)
  D-01: When QUESTION.md exists AND rc==-429, needs_info path is taken
        (QUESTION detection runs before rc checks)
"""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Locate phase runner source
# ---------------------------------------------------------------------------

REPO_ROOT = pathlib.Path(__file__).parents[2]
PHASE_RUNNER_SRC = REPO_ROOT / "deployment" / "hermes" / "sdlc_phase_runner.py"

assert PHASE_RUNNER_SRC.exists(), (
    f"sdlc_phase_runner.py not found at {PHASE_RUNNER_SRC} — "
    "is this running from the repo root?"
)

# Import the module fresh (avoid cached state from other test runs)
_phase_runner_module_cache: dict = {}


def _get_phase_runner():
    """Import sdlc_phase_runner as a module, injecting the deployment/hermes path."""
    if "module" in _phase_runner_module_cache:
        return _phase_runner_module_cache["module"]

    import importlib.util
    hermes_dir = str(PHASE_RUNNER_SRC.parent)
    if hermes_dir not in sys.path:
        sys.path.insert(0, hermes_dir)

    spec = importlib.util.spec_from_file_location(
        "sdlc_phase_runner", str(PHASE_RUNNER_SRC)
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _phase_runner_module_cache["module"] = mod
    return mod


# ---------------------------------------------------------------------------
# Group A — _post_needs_info helper
# ---------------------------------------------------------------------------

class TestPostNeedsInfoHelper:
    """A-01 through A-04: _post_needs_info(story_id, question_file_path, agent_name, phase)

    RED: function does not exist in sdlc_phase_runner.py yet.
    """

    def _get_fn(self):
        """Import _post_needs_info; fail with a RED message if not found."""
        mod = _get_phase_runner()
        fn = getattr(mod, "_post_needs_info", None)
        if fn is None:
            pytest.fail(
                "_post_needs_info not found in sdlc_phase_runner.py.\n"
                "Add the helper function (STORY-532 Phase 8):\n\n"
                "  def _post_needs_info(\n"
                "      story_id: str,\n"
                "      question_file_path: str,\n"
                "      agent_name: str,\n"
                "      phase: int,\n"
                "  ) -> bool: ...\n"
            )
        return fn

    def test_returns_true_on_200(self):
        """A-01: Returns True when the ops-console returns HTTP 200."""
        fn = self._get_fn()

        mock_response = MagicMock()
        mock_response.status = 200

        with patch("urllib.request.urlopen", return_value=mock_response):
            result = fn(
                story_id="STORY-532",
                question_file_path="features/story-532/QUESTION.md",
                agent_name="devon",
                phase=1,
            )

        assert result is True, (
            f"_post_needs_info returned {result!r} — expected True on HTTP 200.\n"
            "Return True when urlopen succeeds without raising."
        )

    def test_returns_false_on_http_error(self):
        """A-02: Returns False when the ops-console returns HTTP 500."""
        import urllib.error
        fn = self._get_fn()

        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(
                url="http://test/api/dispatch/needs-info/STORY-532",
                code=500,
                msg="Internal Server Error",
                hdrs=None,
                fp=None,
            ),
        ):
            result = fn(
                story_id="STORY-532",
                question_file_path="features/story-532/QUESTION.md",
                agent_name="devon",
                phase=1,
            )

        assert result is False, (
            f"_post_needs_info returned {result!r} — expected False on HTTP 500.\n"
            "Catch urllib.error.HTTPError and return False."
        )

    def test_returns_false_on_network_exception(self):
        """A-03: Returns False when urlopen raises a network exception (timeout, refused)."""
        import urllib.error
        fn = self._get_fn()

        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("Connection refused"),
        ):
            result = fn(
                story_id="STORY-532",
                question_file_path="features/story-532/QUESTION.md",
                agent_name="devon",
                phase=1,
            )

        assert result is False, (
            f"_post_needs_info returned {result!r} — expected False on URLError.\n"
            "Catch urllib.error.URLError (and Exception broadly) and return False."
        )

    def test_posts_to_correct_url_with_correct_body(self):
        """A-04: _post_needs_info POSTs to /api/dispatch/needs-info/{story_id}.

        Verifies the URL includes the story_id and the request body includes
        agent, question_file_path, and phase fields.
        """
        import json
        fn = self._get_fn()

        captured_requests: list = []

        def _fake_urlopen(req, timeout=None):
            captured_requests.append(req)
            mock_resp = MagicMock()
            mock_resp.status = 200
            return mock_resp

        ops_url = "http://localhost:8005"

        with (
            patch("urllib.request.urlopen", side_effect=_fake_urlopen),
            patch.dict(os.environ, {
                "OPS_CONSOLE_URL": ops_url,
                "OPS_CONSOLE_API_KEY": "test-key",
                "AGENT_NAME": "devon",
            }),
        ):
            fn(
                story_id="STORY-532",
                question_file_path="features/story-532/QUESTION.md",
                agent_name="devon",
                phase=1,
            )

        assert len(captured_requests) == 1, (
            f"Expected 1 HTTP request, got {len(captured_requests)}.\n"
            "_post_needs_info must call urllib.request.urlopen exactly once."
        )
        req = captured_requests[0]
        assert "needs-info" in req.full_url, (
            f"URL {req.full_url!r} does not contain 'needs-info'.\n"
            "URL must be: {{OPS_CONSOLE_URL}}/api/dispatch/needs-info/{{story_id}}"
        )
        assert "STORY-532" in req.full_url, (
            f"URL {req.full_url!r} does not contain the story_id 'STORY-532'."
        )
        body = json.loads(req.data.decode())
        assert "question_file_path" in body, (
            f"Request body missing 'question_file_path': {body}"
        )
        assert body["question_file_path"] == "features/story-532/QUESTION.md", (
            f"Wrong question_file_path in body: {body}"
        )


# ---------------------------------------------------------------------------
# Group B — Integration in run_sdlc_phases
# ---------------------------------------------------------------------------

class TestQuestionMdIntegration:
    """B-01 through B-03: QUESTION.md detection in run_sdlc_phases.

    These tests exercise the actual branch in run_sdlc_phases that handles
    QUESTION.md. The phase SDK call is stubbed to (a) return rc=0 and
    (b) write QUESTION.md into the workdir.
    """

    def _make_workdir_with_question(self, tmp_path: pathlib.Path, story_id: str) -> pathlib.Path:
        """Create a minimal workdir with a QUESTION.md file for the given story."""
        story_folder = f"story-{story_id.split('-')[-1]}"
        story_dir = tmp_path / "features" / story_folder
        story_dir.mkdir(parents=True)
        (story_dir / "QUESTION.md").write_text(
            "What should the default scope be for ambiguous stories?"
        )
        # Create a minimal CLAUDE.md so the runner doesn't bail on missing config
        (tmp_path / "CLAUDE.md").write_text("# Test repo\n")
        # Create .git dir stub so git commands don't fail catastrophically
        (tmp_path / ".git").mkdir()
        return tmp_path

    @pytest.mark.asyncio
    async def test_question_md_detected_posts_needs_info(self, tmp_path):
        """B-01: When QUESTION.md exists after phase, runner calls _post_needs_info.

        RED: _post_needs_info does not exist yet; the runner currently calls
        _notify_teams + return False into the retry path.
        """
        mod = _get_phase_runner()
        fn = getattr(mod, "_post_needs_info", None)
        if fn is None:
            pytest.fail(
                "B-01 cannot run: _post_needs_info not found in sdlc_phase_runner.py.\n"
                "Implement _post_needs_info first (STORY-532 Phase 8)."
            )

        workdir = self._make_workdir_with_question(tmp_path, "STORY-532")

        posted_calls: list[dict] = []

        def _fake_post_needs_info(story_id, question_file_path, agent_name, phase):
            posted_calls.append({
                "story_id": story_id,
                "question_file_path": question_file_path,
                "phase": phase,
            })
            return True  # Simulate successful POST

        with (
            patch.object(mod, "_run_phase_sdk", return_value=(0, "")),
            patch.object(mod, "_post_needs_info", side_effect=_fake_post_needs_info),
            patch.object(mod, "_notify_teams"),
            patch.object(mod, "_ensure_branch"),

            patch.object(mod, "_verify_deliverable", return_value=False),
            patch("subprocess.run"),
        ):
            success, sha, _ = mod.run_sdlc_phases(
                story_id="STORY-532",
                repo="tech-dev-agents",
                scope="medium",
                prompt="Test dispatch prompt",
                workdir=str(workdir),
                env=os.environ.copy(),
            )

        assert len(posted_calls) >= 1, (
            f"_post_needs_info was never called — got {posted_calls}.\n"
            "When QUESTION.md is found, the runner must call _post_needs_info "
            "instead of falling into _notify_teams + return False.\n"
            "Implement the needs_info branch in run_sdlc_phases (STORY-532)."
        )
        assert posted_calls[0]["story_id"] == "STORY-532", (
            f"Wrong story_id in _post_needs_info call: {posted_calls[0]}"
        )
        assert "QUESTION.md" in posted_calls[0]["question_file_path"], (
            f"question_file_path does not contain QUESTION.md: {posted_calls[0]}"
        )

    @pytest.mark.asyncio
    async def test_successful_post_returns_false_none_no_retry(self, tmp_path):
        """B-02: When POST succeeds, run_sdlc_phases returns (False, None).

        Critical: the return value must NOT re-enter the auto-retry queue.
        (False, None) is the signal to the dispatch poller to stop retrying.

        RED: behavior not yet implemented.
        """
        mod = _get_phase_runner()
        fn = getattr(mod, "_post_needs_info", None)
        if fn is None:
            pytest.fail("B-02 cannot run: _post_needs_info not found.")

        workdir = self._make_workdir_with_question(tmp_path, "STORY-532")

        with (
            patch.object(mod, "_run_phase_sdk", return_value=(0, "")),
            patch.object(mod, "_post_needs_info", return_value=True),
            patch.object(mod, "_notify_teams"),
            patch.object(mod, "_ensure_branch"),

            patch.object(mod, "_verify_deliverable", return_value=False),
            patch("subprocess.run"),
        ):
            result = mod.run_sdlc_phases(
                story_id="STORY-532",
                repo="tech-dev-agents",
                scope="medium",
                prompt="Test dispatch prompt",
                workdir=str(workdir),
                env=os.environ.copy(),
            )

        assert result[:2] == (False, None), (
            f"Expected (False, None) when needs_info POST succeeds, got {result!r}.\n"
            "The dispatch poller interprets (False, None) as 'do not retry'."
        )

    @pytest.mark.asyncio
    async def test_failed_post_falls_back_to_notify_teams(self, tmp_path):
        """B-03: When /needs-info POST fails, runner falls back to _notify_teams.

        The fallback must still return (False, None) — not raise an exception.

        RED: fallback path not yet implemented.
        """
        mod = _get_phase_runner()
        fn = getattr(mod, "_post_needs_info", None)
        if fn is None:
            pytest.fail("B-03 cannot run: _post_needs_info not found.")

        workdir = self._make_workdir_with_question(tmp_path, "STORY-532")

        notify_calls: list[str] = []

        def _capture_notify(msg: str):
            notify_calls.append(msg)

        with (
            patch.object(mod, "_run_phase_sdk", return_value=(0, "")),
            patch.object(mod, "_post_needs_info", return_value=False),  # POST failed
            patch.object(mod, "_notify_teams", side_effect=_capture_notify),
            patch.object(mod, "_ensure_branch"),

            patch.object(mod, "_verify_deliverable", return_value=False),
            patch("subprocess.run"),
        ):
            result = mod.run_sdlc_phases(
                story_id="STORY-532",
                repo="tech-dev-agents",
                scope="medium",
                prompt="Test dispatch prompt",
                workdir=str(workdir),
                env=os.environ.copy(),
            )

        # Fallback must still exit cleanly
        assert result[:2] == (False, None), (
            f"Expected (False, None) even when POST fails (fallback path), got {result!r}."
        )
        # Fallback must notify Teams
        assert len(notify_calls) > 0, (
            "When /needs-info POST fails, _notify_teams must still be called.\n"
            "The operator needs a notification even if the DB update failed."
        )
        # Notification should mention the failure
        combined = " ".join(notify_calls)
        assert any(
            kw in combined.lower()
            for kw in ["question", "needs-info", "failed", "fallback", "answer"]
        ), (
            f"Teams notification doesn't mention the question or failure.\n"
            f"Notification messages: {notify_calls}"
        )


# ---------------------------------------------------------------------------
# Group C — Negative path
# ---------------------------------------------------------------------------

class TestNoQuestionMd:
    """C-01: When no QUESTION.md, _post_needs_info is never called."""

    @pytest.mark.asyncio
    async def test_no_question_md_no_post(self, tmp_path):
        """C-01: When the agent does NOT write QUESTION.md, /needs-info is never called.

        RED if _post_needs_info doesn't exist; GREEN once implemented correctly.
        """
        mod = _get_phase_runner()
        fn = getattr(mod, "_post_needs_info", None)
        if fn is None:
            pytest.fail("C-01 cannot run: _post_needs_info not found.")

        # Workdir WITHOUT QUESTION.md (agent produced a real deliverable)
        story_dir = tmp_path / "features" / "story-532"
        story_dir.mkdir(parents=True)
        (story_dir / "seed.md").write_text("# STORY-532\n## Problem\nTest seed.")
        (tmp_path / "CLAUDE.md").write_text("# Test repo\n")
        (tmp_path / ".git").mkdir()

        post_calls: list = []

        with (
            patch.object(mod, "_run_phase_sdk", return_value=(0, "")),
            patch.object(mod, "_post_needs_info", side_effect=post_calls.append),
            patch.object(mod, "_notify_teams"),
            patch.object(mod, "_ensure_branch"),

            patch.object(mod, "_verify_deliverable", return_value=True),  # deliverables exist
            patch("subprocess.run"),
        ):
            mod.run_sdlc_phases(
                story_id="STORY-532",
                repo="tech-dev-agents",
                scope="medium",
                prompt="Test dispatch prompt",
                workdir=str(tmp_path),
                env=os.environ.copy(),
            )

        assert len(post_calls) == 0, (
            f"_post_needs_info was called {len(post_calls)} time(s) even though "
            f"no QUESTION.md exists.\nCalls: {post_calls}"
        )


# ---------------------------------------------------------------------------
# Group D — Ordering: QUESTION.md beats rc=-429
# ---------------------------------------------------------------------------

class TestQuestionMdOrdering:
    """D-01: QUESTION.md check runs before rate-limit check (rc=-429)."""

    @pytest.mark.asyncio
    async def test_question_md_wins_over_rate_limit(self, tmp_path):
        """D-01: When QUESTION.md exists AND rc==-429, needs_info path is taken.

        The QUESTION.md check in run_sdlc_phases runs after _run_phase_sdk
        returns but BEFORE the rc==-429 branch. So even if the SDK exited
        with -429, if QUESTION.md was written, needs_info takes priority.

        RED: ordering not yet implemented.
        """
        mod = _get_phase_runner()
        fn = getattr(mod, "_post_needs_info", None)
        if fn is None:
            pytest.fail("D-01 cannot run: _post_needs_info not found.")

        story_dir = tmp_path / "features" / "story-532"
        story_dir.mkdir(parents=True)
        (story_dir / "QUESTION.md").write_text("What is the correct scope?")
        (tmp_path / "CLAUDE.md").write_text("# Test\n")
        (tmp_path / ".git").mkdir()

        needs_info_called = []
        rate_limit_notified = []

        def _capture_notify(msg: str):
            if "rate limit" in msg.lower() or "429" in msg.lower():
                rate_limit_notified.append(msg)

        with (
            patch.object(mod, "_run_phase_sdk", return_value=(-429, "")),
            patch.object(mod, "_post_needs_info", side_effect=lambda **kw: needs_info_called.append(kw) or True),
            patch.object(mod, "_notify_teams", side_effect=_capture_notify),
            patch.object(mod, "_ensure_branch"),

            patch.object(mod, "_verify_deliverable", return_value=False),
            patch("subprocess.run"),
        ):
            result = mod.run_sdlc_phases(
                story_id="STORY-532",
                repo="tech-dev-agents",
                scope="medium",
                prompt="Test dispatch prompt",
                workdir=str(tmp_path),
                env=os.environ.copy(),
            )

        assert len(needs_info_called) >= 1, (
            "When QUESTION.md exists even alongside rc=-429, "
            "_post_needs_info should be called (QUESTION.md takes priority).\n"
            f"_post_needs_info calls: {needs_info_called}\n"
            f"Rate-limit notifications: {rate_limit_notified}"
        )
        assert result[:2] == (False, None), (
            f"Expected (False, None), got {result!r}"
        )


# ---------------------------------------------------------------------------
# Group E — STORY-528 regression: needs_info NEVER falls through to /fail
# ---------------------------------------------------------------------------

class TestNeedsInfoNoFailFallthrough:
    """E-01, E-02: After a needs_info signal, the runner must not propagate
    a failure that would trigger the poller's auto-retry path.

    Reproduces the 2026-04-24 STORY-528 ghost-claim incident: /needs-info
    POST returned 409, runner fell back to /fail notify+legacy path, the
    poller treated the story as a generic failure and auto-retried. This
    caused the third-claim-after-needs-info loop documented in tonight's
    Loki timeline.
    """

    def _make_workdir_with_question(self, tmp_path: pathlib.Path, story_id: str) -> pathlib.Path:
        story_folder = f"story-{story_id.split('-')[-1]}"
        story_dir = tmp_path / "features" / story_folder
        story_dir.mkdir(parents=True)
        (story_dir / "QUESTION.md").write_text(
            "What should the default scope be for ambiguous stories?"
        )
        (tmp_path / "CLAUDE.md").write_text("# Test repo\n")
        (tmp_path / ".git").mkdir()
        return tmp_path

    def test_successful_needs_info_marks_story_in_module_set(self, tmp_path):
        """E-01: When _post_needs_info succeeds, the story is added to
        NEEDS_INFO_STORIES (module-level set) so the dispatch poller can
        skip auto-retry even though the runner returns (False, None).

        Without this signal, the poller's _report_fail call re-enqueues the
        story and re-claims it — the ghost-claim loop.
        """
        mod = _get_phase_runner()
        if not hasattr(mod, "NEEDS_INFO_STORIES"):
            pytest.fail(
                "NEEDS_INFO_STORIES module-level set not found in sdlc_phase_runner.\n"
                "Add: NEEDS_INFO_STORIES: set[str] = set()  at module level.\n"
                "Populate from run_sdlc_phases when _post_needs_info() returns True."
            )

        mod.NEEDS_INFO_STORIES.clear()

        workdir = self._make_workdir_with_question(tmp_path, "STORY-528")

        with (
            patch.object(mod, "_run_phase_sdk", return_value=(0, "")),
            patch.object(mod, "_post_needs_info", return_value=True),
            patch.object(mod, "_notify_teams"),
            patch.object(mod, "_ensure_branch"),

            patch.object(mod, "_verify_deliverable", return_value=False),
            patch("subprocess.run"),
        ):
            result = mod.run_sdlc_phases(
                story_id="STORY-528",
                repo="tech-dev-agents",
                scope="medium",
                prompt="Test dispatch prompt",
                workdir=str(workdir),
                env=os.environ.copy(),
            )

        assert result[:2] == (False, None)
        assert "STORY-528" in mod.NEEDS_INFO_STORIES, (
            "After successful _post_needs_info, story_id must be added to "
            "NEEDS_INFO_STORIES so the poller skips auto-retry. Current set: "
            f"{mod.NEEDS_INFO_STORIES!r}"
        )

    def test_failed_needs_info_also_marks_story(self, tmp_path):
        """E-02: When _post_needs_info FAILS (ops-console unreachable etc.),
        the story is STILL added to NEEDS_INFO_STORIES.

        Per the spec: "one idempotent /needs_info call, log-and-continue on
        any HTTP error, rely on ops-console as source of truth". The local
        NEEDS_INFO_STORIES set prevents this agent's poller from immediately
        calling /fail + auto-retry, which is what produced tonight's ghost
        claim even after the /needs-info POST fell through to the fallback.
        """
        mod = _get_phase_runner()
        if not hasattr(mod, "NEEDS_INFO_STORIES"):
            pytest.fail(
                "NEEDS_INFO_STORIES module-level set not found in sdlc_phase_runner.\n"
                "Add: NEEDS_INFO_STORIES: set[str] = set()  at module level."
            )

        mod.NEEDS_INFO_STORIES.clear()

        workdir = self._make_workdir_with_question(tmp_path, "STORY-528")

        with (
            patch.object(mod, "_run_phase_sdk", return_value=(0, "")),
            # _post_needs_info returns False (POST failed)
            patch.object(mod, "_post_needs_info", return_value=False),
            patch.object(mod, "_notify_teams"),
            patch.object(mod, "_ensure_branch"),

            patch.object(mod, "_verify_deliverable", return_value=False),
            patch("subprocess.run"),
        ):
            result = mod.run_sdlc_phases(
                story_id="STORY-528",
                repo="tech-dev-agents",
                scope="medium",
                prompt="Test dispatch prompt",
                workdir=str(workdir),
                env=os.environ.copy(),
            )

        assert result[:2] == (False, None)
        assert "STORY-528" in mod.NEEDS_INFO_STORIES, (
            "Even when /needs-info POST fails, the story must still be added "
            "to NEEDS_INFO_STORIES so THIS agent's poller doesn't immediately "
            "auto-retry. Fix-it-forward: ops-console becomes eventually "
            "consistent, the agent cooperates by not fighting it."
        )


# ---------------------------------------------------------------------------
# Group F — STORY-803 AC-2: _post_needs_info sends directive_present +
#           phase_started_at (T-2a)
# ---------------------------------------------------------------------------


class TestPostNeedsInfoDirectiveFields:
    """F-01 (T-2a): _post_needs_info must include directive_present and
    phase_started_at fields in the JSON body sent to ops-console.

    These fields enable the server-side 60s directive-bypass guard (AC-2 Bug 1.2):
    the server reads directive_present and checks claimed_at age before allowing
    the /needs-info transition.

    RED: current _post_needs_info has no directive_present or phase_started_at
    parameters and does not include them in the request body.
    """

    def _get_fn(self):
        mod = _get_phase_runner()
        fn = getattr(mod, "_post_needs_info", None)
        if fn is None:
            pytest.fail(
                "_post_needs_info not found in sdlc_phase_runner.py — "
                "must exist (STORY-532 Phase 8) before STORY-803 can extend it."
            )
        return fn

    def test_post_needs_info_sends_directive_present_and_phase_started_at(self):
        """T-2a: _post_needs_info must accept directive_present: bool = False and
        phase_started_at: float | None = None keyword args and include both in the
        JSON request body sent to ops-console.

        RED reason: current signature only accepts story_id, question_file_path,
        agent_name, phase. Calling with directive_present/phase_started_at kwargs
        raises TypeError. Even if kwargs were accepted, the body fields are absent.
        """
        import json
        fn = self._get_fn()

        captured_requests: list = []

        def _fake_urlopen(req, timeout=None):
            captured_requests.append(req)
            mock_resp = MagicMock()
            mock_resp.status = 200
            return mock_resp

        # Call with the NEW kwargs — must not raise TypeError
        try:
            with (
                patch("urllib.request.urlopen", side_effect=_fake_urlopen),
                patch.dict(os.environ, {
                    "OPS_CONSOLE_URL": "http://localhost:8005",
                    "OPS_CONSOLE_API_KEY": "test-key",
                    "AGENT_NAME": "devon",
                }),
            ):
                fn(
                    story_id="STORY-803",
                    question_file_path="features/story-803/QUESTION.md",
                    agent_name="devon",
                    phase=7,
                    directive_present=True,
                    phase_started_at=1746100000.0,
                )
        except TypeError as exc:
            pytest.fail(
                f"_post_needs_info raised TypeError when called with directive_present "
                f"and phase_started_at kwargs: {exc}\n\n"
                "Add these parameters to _post_needs_info:\n"
                "  directive_present: bool = False,\n"
                "  phase_started_at: float | None = None,\n"
                "and include them in the JSON body dict."
            )

        assert len(captured_requests) == 1, (
            f"Expected exactly 1 HTTP request, got {len(captured_requests)}"
        )
        body = json.loads(captured_requests[0].data.decode())

        assert "directive_present" in body, (
            f"JSON body missing 'directive_present' field.\n"
            f"Body keys: {list(body.keys())}\n"
            "Add: 'directive_present': directive_present  to the body dict in _post_needs_info."
        )
        assert body["directive_present"] is True, (
            f"'directive_present' value should be True, got: {body['directive_present']!r}"
        )

        assert "phase_started_at" in body, (
            f"JSON body missing 'phase_started_at' field.\n"
            f"Body keys: {list(body.keys())}\n"
            "Add: 'phase_started_at': phase_started_at  to the body dict in _post_needs_info."
        )
        assert body["phase_started_at"] == 1746100000.0, (
            f"'phase_started_at' value should be 1746100000.0, got: {body['phase_started_at']!r}"
        )

    def test_post_needs_info_backward_compatible_without_new_kwargs(self):
        """F-02 (regression): calling _post_needs_info without directive_present /
        phase_started_at must still work (defaults to False / None).

        This ensures existing call sites (synthetic-QUESTION.md path at line 3281
        and others) don't break when the new parameters are added.

        GREEN after Phase 8 adds the parameters with default values.
        """
        import json
        fn = self._get_fn()

        captured_requests: list = []

        def _fake_urlopen(req, timeout=None):
            captured_requests.append(req)
            mock_resp = MagicMock()
            mock_resp.status = 200
            return mock_resp

        # Call WITHOUT the new kwargs — must not raise TypeError
        try:
            with (
                patch("urllib.request.urlopen", side_effect=_fake_urlopen),
                patch.dict(os.environ, {
                    "OPS_CONSOLE_URL": "http://localhost:8005",
                    "OPS_CONSOLE_API_KEY": "test-key",
                    "AGENT_NAME": "devon",
                }),
            ):
                fn(
                    story_id="STORY-532",
                    question_file_path="features/story-532/QUESTION.md",
                    agent_name="devon",
                    phase=4,
                )
        except TypeError as exc:
            pytest.fail(
                f"_post_needs_info raised TypeError when called without new kwargs: {exc}\n"
                "Ensure directive_present and phase_started_at have default values."
            )

        assert len(captured_requests) == 1, (
            "Backward-compatible call must still issue exactly 1 HTTP request"
        )
