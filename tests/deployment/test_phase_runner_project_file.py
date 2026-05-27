"""STORY-524: Tests for project_file.py deployment and .project update path.

Three mandatory test groups from dispatch:

  A — Import fallback chain: sdlc_phase_runner.py must fall back to a flat
      'from project_file import update_story_status' when the package-style
      import fails (as it always does on agent VMs where sys.path is /opt/agent/).

  B — .project update path in run_sdlc_phases: when update_story_status is
      available it is called with the right args; when it is None the warning is
      logged; an exception from it never aborts the run.

  C — Functional: calling the real update_story_status on a temp .project file
      actually appends a new phase-completion row (the "new row appended" check).

RED / GREEN legend
------------------
  RED  = will FAIL before Phase 8 implementation (tests the new behaviour)
  GREEN = already passes (regression protection for existing behaviour)
"""

from __future__ import annotations

import pathlib
import sys
import textwrap
from io import StringIO
from unittest.mock import MagicMock, patch, call
import itertools

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REPO_ROOT = pathlib.Path(__file__).parents[2]
PHASE_RUNNER_SRC = REPO_ROOT / "deployment" / "hermes" / "sdlc_phase_runner.py"
PUSH_CODE_SH = REPO_ROOT / "deployment" / "vm" / "push-code.sh"


# Minimal .project skeleton that update_story_status can read and patch.
_PROJECT_SKELETON = textwrap.dedent("""
    # Project State

    ## Phase Routing
    | Field | Value |
    |-------|-------|
    | Active Story |  |
    | Current Phase |  |
    | Current Status |  |

    ### Story Parallel Status

    | Story | Assignee | Scope | Current Phase | Status | Branch |
    |-------|----------|-------|---------------|--------|--------|

    ## Phase History
    | Phase | Name | Start | End | Status | Summary |
    |-------|------|-------|-----|--------|---------|
""").lstrip()


# ---------------------------------------------------------------------------
# Group A — Import fallback chain
# ---------------------------------------------------------------------------

class TestImportFallbackChain:
    """Group A: sdlc_phase_runner.py must have a flat-path fallback import.

    On agent VMs the Python path is just ['/opt/agent'], so the package-style
    import 'from deployment.hermes.project_file import update_story_status'
    always raises ImportError.  Phase 8 must add a second except clause that
    tries 'from project_file import update_story_status' (the flat layout).
    """

    def test_source_contains_flat_import_fallback(self):
        """TC-A1 [RED]: sdlc_phase_runner.py source must include the flat fallback.

        Verifies that the second 'except ImportError' clause (for VM layout) is
        present.  Fails until Phase 8 adds it.
        """
        source = PHASE_RUNNER_SRC.read_text(encoding="utf-8")
        assert "from project_file import update_story_status" in source, (
            "sdlc_phase_runner.py is missing the flat-path import fallback for agent VMs.\n"
            "Phase 8 must add:\n"
            "    except ImportError:\n"
            "        try:\n"
            "            from project_file import update_story_status  "
            "# flat layout on agent VMs\n"
            "        except ImportError:\n"
            "            update_story_status = None"
        )

    def test_source_has_nested_try_for_flat_fallback(self):
        """TC-A2 [RED]: The flat-path fallback must be inside a nested try/except.

        Both the package path AND the flat path are tried; only if both fail is
        update_story_status set to None.  This ensures VMs get the function even
        though the package tree doesn't exist there.
        """
        source = PHASE_RUNNER_SRC.read_text(encoding="utf-8")
        # The fallback must set update_story_status = None only after both attempts fail.
        # We check that None assignment follows the flat-path except clause.
        has_flat_import = "from project_file import update_story_status" in source
        has_none_fallback = "update_story_status = None" in source
        assert has_flat_import and has_none_fallback, (
            "Import fallback chain incomplete.  sdlc_phase_runner.py must:\n"
            "  1. try 'from deployment.hermes.project_file import update_story_status'\n"
            "  2. on ImportError, try 'from project_file import update_story_status'\n"
            "  3. on second ImportError, set update_story_status = None"
        )


# ---------------------------------------------------------------------------
# Group B — .project update path in run_sdlc_phases
# ---------------------------------------------------------------------------

class TestProjectUpdatePath:
    """Group B: The .project update block inside run_sdlc_phases.

    These tests patch the module-level 'update_story_status' attribute and
    run a single small-scope phase end-to-end (with all heavy I/O mocked) to
    verify the update contract.
    """

    @pytest.fixture
    def runner(self):
        """Import and return the sdlc_phase_runner module."""
        try:
            import deployment.hermes.sdlc_phase_runner as _runner
            return _runner
        except ImportError as exc:
            pytest.skip(f"sdlc_phase_runner not importable: {exc}")

    @pytest.fixture
    def minimal_phases(self, runner):
        """Override PHASE_MAP to run a single Seed phase only.

        A single phase with one deliverable keeps the mock side-effect list
        predictable: _verify_deliverable is called twice — once for the resume
        check (must return False so the phase runs) and once for post-phase
        verification (must return True so the run isn't aborted).
        """
        return {"small": [(1, "Seed", "seed.md", "test prompt", 10)]}

    def _run_phases_with_mocks(self, runner, tmp_path, minimal_phases,
                                mock_update, extra_patches=None):
        """Helper: run run_sdlc_phases with all heavy deps mocked out.

        _verify_deliverable side_effect alternates False/True so that the
        resume check doesn't skip the phase and the post-phase check doesn't
        abort the run.
        """
        patches = [
            patch.object(runner, "update_story_status", mock_update),
            patch.object(runner, "PHASE_MAP", minimal_phases),
            patch.object(runner, "_notify_teams"),
            patch.object(runner, "_ensure_branch"),
            patch.object(runner, "install_shutdown_handler"),
            patch.object(runner, "_extract_story_folder", return_value="story-524"),

            patch.object(runner, "_check_for_questions", return_value=None),
            patch.object(runner, "_run_phase_sdk", return_value=(0, "ok")),
            # False → phase is not skipped (resume check); True → deliverable verified (post-phase)
            patch.object(runner, "_verify_deliverable", side_effect=[False, True]),
            patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="abc123")),
            patch("os.path.isfile", return_value=True),   # seed already exists → skip auto-copy
            patch("os.path.isdir", return_value=False),   # no features dir
        ]
        if extra_patches:
            patches.extend(extra_patches)

        ctx_managers = [p.__enter__() if hasattr(p, '__enter__') else p for p in patches]
        # Use nested context manager approach
        import contextlib
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            return runner.run_sdlc_phases(
                story_id="STORY-524",
                repo="tech-dev-agents",
                scope="small",
                prompt="test prompt",
                workdir=str(tmp_path),
                env={"AGENT_NAME": "dan"},
            )

    def test_update_story_status_called_when_available(self, runner, tmp_path, minimal_phases):
        """TC-B1 [GREEN]: When update_story_status is not None, run_sdlc_phases calls it.

        Verifies the happy path: project_file.py imported and available → the
        module-level function is called once per completed phase.
        """
        mock_update = MagicMock()

        success, sha, _reason = self._run_phases_with_mocks(
            runner, tmp_path, minimal_phases, mock_update
        )

        assert mock_update.called, (
            "update_story_status was never called — .project is not being updated "
            "even though update_story_status is available"
        )

    def test_update_story_status_correct_args(self, runner, tmp_path, minimal_phases):
        """TC-B2 [GREEN]: update_story_status receives the correct keyword args.

        Verifies that project_path, story_id, scope, assignee, and current_phase
        are all forwarded correctly from the phase runner loop.
        """
        mock_update = MagicMock()

        self._run_phases_with_mocks(runner, tmp_path, minimal_phases, mock_update)

        assert mock_update.called, "update_story_status never called"
        kwargs = mock_update.call_args.kwargs
        assert kwargs["story_id"] == "STORY-524", (
            f"story_id mismatch: expected 'STORY-524', got {kwargs.get('story_id')!r}"
        )
        assert kwargs["scope"] == "small", (
            f"scope mismatch: expected 'small', got {kwargs.get('scope')!r}"
        )
        assert kwargs["assignee"] == "dan", (
            f"assignee not pulled from env AGENT_NAME: got {kwargs.get('assignee')!r}"
        )
        assert kwargs["project_path"] == str(tmp_path / ".project"), (
            f"project_path not workdir/.project: got {kwargs.get('project_path')!r}"
        )
        assert kwargs["current_phase"] == "1", (
            f"current_phase mismatch: expected '1', got {kwargs.get('current_phase')!r}"
        )

    def test_warning_logged_when_update_story_status_is_none(
        self, runner, tmp_path, minimal_phases, capsys
    ):
        """TC-B3 [GREEN]: When update_story_status is None, the warning is printed.

        This is the existing (broken) behaviour on agent VMs.  The test must
        stay GREEN after Phase 8 so that the fallback-to-None path is still
        resilient when even the flat import fails.
        """
        self._run_phases_with_mocks(
            runner, tmp_path, minimal_phases, mock_update=None
        )

        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "project_file not available" in combined, (
            "Expected '[DISPATCH] Warning: .project update failed ... project_file not available' "
            f"in output, but got:\n{combined}"
        )

    def test_update_exception_does_not_abort_phase_run(
        self, runner, tmp_path, minimal_phases
    ):
        """TC-B4 [GREEN]: An exception from update_story_status never aborts the run.

        Error resilience: even if update_story_status raises, the phase runner
        must continue and return success=True.
        """
        mock_update = MagicMock(side_effect=RuntimeError("simulated write failure"))

        success, sha, _reason = self._run_phases_with_mocks(
            runner, tmp_path, minimal_phases, mock_update
        )

        assert success is True, (
            "run_sdlc_phases returned failure — an exception from update_story_status "
            "must be caught and the run must continue"
        )


# ---------------------------------------------------------------------------
# Group C — Functional: real update_story_status appends a row
# ---------------------------------------------------------------------------

class TestRealUpdateStoryStatus:
    """Group C: Call the real update_story_status function on a temp .project file
    and verify it appends a new row for the completed phase.

    This is the 'verify new row appended for the phase' criterion from the dispatch.
    No mocking of update_story_status itself — this tests the actual module.
    """

    def test_update_story_status_appends_phase_row(self, tmp_path):
        """TC-C1 [GREEN]: update_story_status writes a new Story Status row.

        Arrange: create a temp .project file with no story rows.
        Act    : call update_story_status for STORY-524, phase 1, scope small.
        Assert : read_project parses the Story Status table and finds a row for
                 STORY-524 with the correct current_phase and branch.

        This is the 'new row appended for the phase' criterion from the dispatch.
        """
        from deployment.hermes.project_file import update_story_status, read_project

        project_path = tmp_path / ".project"
        project_path.write_text(_PROJECT_SKELETON, encoding="utf-8")

        update_story_status(
            project_path=project_path,
            story_id="STORY-524",
            assignee="dan",
            scope="small",
            current_phase="1",
            status="in_progress",
            branch="story-524/story-524",
            is_final=False,
            phase_summary="Phase 1 complete",
        )

        # Parse via the public API rather than raw string scan to avoid
        # matching Phase Routing lines that also contain STORY-524.
        parsed = read_project(project_path)
        story_rows = parsed["story_status"]
        assert story_rows, (
            ".project Story Status table is empty after update_story_status call — "
            "no row was appended"
        )
        story_row = next(
            (r for r in story_rows if "STORY-524" in r.get("story", "")),
            None,
        )
        assert story_row is not None, (
            f"No STORY-524 row in Story Status table. Rows found: {story_rows}"
        )
        assert story_row.get("current_phase") == "1", (
            f"current_phase mismatch: expected '1', got {story_row.get('current_phase')!r}"
        )
        assert story_row.get("branch") == "story-524/story-524", (
            f"branch mismatch in row: {story_row!r}"
        )

    def test_update_story_status_phase_varies_between_calls(self, tmp_path):
        """TC-C2 [GREEN]: Output-variance — different phase args produce different rows.

        Two calls with different current_phase values must produce different content
        in the .project file.  Detects any stub that writes the same data regardless
        of input.
        """
        from deployment.hermes.project_file import update_story_status

        project_path_1 = tmp_path / ".project_phase1"
        project_path_7 = tmp_path / ".project_phase7"
        project_path_1.write_text(_PROJECT_SKELETON, encoding="utf-8")
        project_path_7.write_text(_PROJECT_SKELETON, encoding="utf-8")

        update_story_status(
            project_path=project_path_1,
            story_id="STORY-524",
            assignee="dan",
            scope="small",
            current_phase="1",
            status="in_progress",
            branch="story-524/story-524",
        )
        update_story_status(
            project_path=project_path_7,
            story_id="STORY-524",
            assignee="dan",
            scope="small",
            current_phase="7",
            status="in_progress",
            branch="story-524/story-524",
        )

        content_1 = project_path_1.read_text(encoding="utf-8")
        content_7 = project_path_7.read_text(encoding="utf-8")
        assert content_1 != content_7, (
            "update_story_status produced identical files for phase=1 and phase=7 — "
            "the current_phase argument is not being used"
        )


# ---------------------------------------------------------------------------
# Group D — Push-code.sh manifest compliance
# ---------------------------------------------------------------------------

class TestPushCodeManifest:
    """Group D: project_file.py must appear in push-code.sh's deploy manifest.

    TC-D1 [RED]: PROJECT_FILE variable declared.
    TC-D2 [RED]: $PROJECT_FILE included in the scp copy loop.

    These are compliance tests — they will FAIL before Phase 8 adds the file to
    the manifest, and GREEN after.
    """

    def test_push_code_declares_project_file_variable(self):
        """TC-D1 [RED]: push-code.sh must define a PROJECT_FILE variable.

        The variable should follow the same pattern as PHASE_RUNNER, DISPATCH_POLLER,
        etc. (e.g. PROJECT_FILE="$REPO_ROOT/deployment/hermes/project_file.py").
        """
        source = PUSH_CODE_SH.read_text(encoding="utf-8")
        assert "PROJECT_FILE" in source, (
            "push-code.sh does not declare a PROJECT_FILE variable.\n"
            "Phase 8 must add:\n"
            '    PROJECT_FILE="$REPO_ROOT/deployment/hermes/project_file.py"'
        )

    def test_push_code_includes_project_file_in_scp_loop(self):
        """TC-D2 [RED]: push-code.sh must include $PROJECT_FILE in the scp copy loop.

        The for-loop that copies files must include "$PROJECT_FILE" so that
        project_file.py is actually transferred to the VM.
        """
        source = PUSH_CODE_SH.read_text(encoding="utf-8")
        assert "$PROJECT_FILE" in source, (
            "push-code.sh does not include $PROJECT_FILE in the copy loop.\n"
            "Phase 8 must add '$PROJECT_FILE' to the 'for f in ...' scp loop."
        )

    def test_push_code_banner_mentions_project_file(self):
        """TC-D3 [RED]: push-code.sh's deploy banner must list project_file.py.

        The 'echo \"Files: ...\"' line at the top of the deploy output should
        include project_file.py so operators see it in the deploy log.
        """
        source = PUSH_CODE_SH.read_text(encoding="utf-8")
        # Check for the Files: echo line to include project_file
        has_banner = "project_file.py" in source
        assert has_banner, (
            "push-code.sh does not mention project_file.py in the deploy banner.\n"
            "Phase 8 must update the 'Files:' echo line to include project_file.py."
        )
