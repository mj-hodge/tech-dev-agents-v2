"""STORY-634: Post-rebase integrity — verify both main-side and branch-side changes coexist.

RED state — tests will fail until Phase 8 performs the rebase of story-560/story-560
onto current main, resolving conflicts in sdlc_phase_runner.py and dispatch_poller.py.

Groups:
  A — Main-side PR #114 preserved (_consume_resumed_question, resumed_question_path kwarg)
  B — Main-side PR #116 preserved (PAUSE_FLAG_PATH, _is_paused)
  C — Branch-side STORY-560 preserved (pr_number in run_sdlc_phases, _report_complete_with_retry)
  D — Branch-side STORY-565 preserved (_verify_frontend_gate)
"""

from __future__ import annotations

import importlib.util
import inspect
import pathlib
import sys

import pytest

# ---------------------------------------------------------------------------
# Module loading helpers (same pattern as STORY-560 tests)
# ---------------------------------------------------------------------------

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PHASE_RUNNER_SRC = REPO_ROOT / "deployment" / "hermes" / "sdlc_phase_runner.py"
POLLER_SRC = REPO_ROOT / "deployment" / "hermes" / "dispatch_poller.py"

assert PHASE_RUNNER_SRC.exists(), f"sdlc_phase_runner.py not found at {PHASE_RUNNER_SRC}"
assert POLLER_SRC.exists(), f"dispatch_poller.py not found at {POLLER_SRC}"

_cache: dict = {}


def _get_phase_runner():
    if "runner" in _cache:
        return _cache["runner"]
    hermes_dir = str(PHASE_RUNNER_SRC.parent)
    if hermes_dir not in sys.path:
        sys.path.insert(0, hermes_dir)
    spec = importlib.util.spec_from_file_location("sdlc_phase_runner_634", str(PHASE_RUNNER_SRC))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _cache["runner"] = mod
    return mod


def _get_poller():
    if "poller" in _cache:
        return _cache["poller"]
    hermes_dir = str(POLLER_SRC.parent)
    if hermes_dir not in sys.path:
        sys.path.insert(0, hermes_dir)
    spec = importlib.util.spec_from_file_location("dispatch_poller_634", str(POLLER_SRC))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _cache["poller"] = mod
    return mod


# ---------------------------------------------------------------------------
# Group A — Main-side PR #114 preserved (resumed_question_path)
# ---------------------------------------------------------------------------


class TestMainSidePR114:
    """A: PR #114 added _consume_resumed_question and resumed_question_path kwarg."""

    def test_consume_resumed_question_exists(self):
        """A-01: _consume_resumed_question function must exist in phase runner."""
        runner = _get_phase_runner()
        assert hasattr(runner, "_consume_resumed_question"), (
            "sdlc_phase_runner.py must define _consume_resumed_question() — "
            "added by PR #114 to delete stale QUESTION.md on resume. "
            "If missing, the rebase dropped main-side changes."
        )

    def test_run_sdlc_phases_accepts_resumed_question_path_kwarg(self):
        """A-02: run_sdlc_phases signature must accept resumed_question_path."""
        runner = _get_phase_runner()
        sig = inspect.signature(runner.run_sdlc_phases)
        assert "resumed_question_path" in sig.parameters, (
            "run_sdlc_phases must accept resumed_question_path kwarg — "
            "added by PR #114. If missing, the rebase dropped the kwarg "
            "from the function signature."
        )


# ---------------------------------------------------------------------------
# Group B — Main-side PR #116 preserved (rate-limit death loop guard)
# ---------------------------------------------------------------------------


class TestMainSidePR116:
    """B: PR #116 added PAUSE_FLAG_PATH and _is_paused to dispatch_poller."""

    def test_pause_flag_path_defined(self):
        """B-01: PAUSE_FLAG_PATH constant must exist in dispatch_poller."""
        poller = _get_poller()
        assert hasattr(poller, "PAUSE_FLAG_PATH"), (
            "dispatch_poller.py must define PAUSE_FLAG_PATH — added by PR #116 "
            "to guard against rate-limit death loops. If missing, the rebase "
            "dropped main-side changes."
        )

    def test_is_paused_function_exists(self):
        """B-02: _is_paused() must exist in dispatch_poller."""
        poller = _get_poller()
        assert hasattr(poller, "_is_paused"), (
            "dispatch_poller.py must define _is_paused() — added by PR #116. "
            "If missing, the rebase dropped the rate-limit pause check."
        )


# ---------------------------------------------------------------------------
# Group C — Branch-side STORY-560 preserved (pr_number + retry + sidecar)
# ---------------------------------------------------------------------------


class TestBranchSideSTORY560:
    """C: STORY-560 added pr_number capture and _report_complete_with_retry."""

    def test_run_sdlc_phases_source_has_pr_number(self):
        """C-01: run_sdlc_phases must capture and return pr_number."""
        runner = _get_phase_runner()
        source = inspect.getsource(runner.run_sdlc_phases)
        assert "pr_number" in source, (
            "run_sdlc_phases body must reference pr_number — STORY-560 added "
            "PR number capture from gh pr list / gh pr create. If missing, "
            "the rebase dropped branch-side changes."
        )

    def test_report_complete_with_retry_exists(self):
        """C-02: _report_complete_with_retry must exist in dispatch_poller."""
        poller = _get_poller()
        assert hasattr(poller, "_report_complete_with_retry"), (
            "dispatch_poller.py must define _report_complete_with_retry() — "
            "STORY-560 added 2-attempt retry with 5s backoff + sidecar on "
            "final failure. If missing, the rebase dropped branch-side changes."
        )


# ---------------------------------------------------------------------------
# Group D — Branch-side STORY-565 preserved (_verify_frontend_gate)
# ---------------------------------------------------------------------------


class TestBranchSideSTORY565:
    """D: STORY-565 added _verify_frontend_gate to phase runner."""

    def test_verify_frontend_gate_exists(self):
        """D-01: _verify_frontend_gate must exist in sdlc_phase_runner."""
        runner = _get_phase_runner()
        assert hasattr(runner, "_verify_frontend_gate"), (
            "sdlc_phase_runner.py must define _verify_frontend_gate() — "
            "added by STORY-565 on the story-560 branch. If missing, "
            "the rebase dropped branch-side commits."
        )

    def test_verify_frontend_gate_source_references_smoke(self):
        """D-02: _verify_frontend_gate source must reference @smoke or playwright."""
        runner = _get_phase_runner()
        fn = getattr(runner, "_verify_frontend_gate", None)
        assert fn is not None, "_verify_frontend_gate not found"
        source = inspect.getsource(fn)
        has_smoke = "smoke" in source.lower()
        has_playwright = "playwright" in source.lower()
        assert has_smoke or has_playwright, (
            "_verify_frontend_gate must reference @smoke tag or playwright — "
            "it gates frontend stories on Playwright smoke tests. "
            f"Source snippet: {source[:200]}"
        )
