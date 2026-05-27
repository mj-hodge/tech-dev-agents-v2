"""Contract test: `dispatch_poller` and `sdlc_phase_runner` must agree on
`run_sdlc_phases` kwargs.

The 2026-04-24 incident: dispatch_poller.py started passing
`resumed_question_path=...` to run_sdlc_phases. The phase runner's
signature didn't accept the kwarg. Every call raised TypeError, the
poller caught it as "Phase runner error: ... falling back to single-shot"
and degraded silently. Every small-scope story that day ran only Phase 1.

Root cause: the two files are deployed together but reviewed
independently. A Phase-8 implementation added the poller side without
updating the runner signature.

This test locks the call-site kwargs against the signature so any future
drift fails CI immediately, not in silent production.
"""
from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "deployment" / "hermes"))


# ---------------------------------------------------------------------------
# Helpers to extract kwargs at run_sdlc_phases call sites in dispatch_poller
# ---------------------------------------------------------------------------


def _find_run_sdlc_phases_call_kwargs(source: str) -> set[str]:
    """Walk dispatch_poller.py AST; collect every keyword name used in any
    `run_sdlc_phases(...)` call."""
    tree = ast.parse(source)
    kwargs: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # The call can be either `run_sdlc_phases(...)` or
        # `sdlc_phase_runner.run_sdlc_phases(...)` — match both.
        name = None
        func = node.func
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        if name != "run_sdlc_phases":
            continue
        for kw in node.keywords:
            if kw.arg is not None:  # ignore **kwargs splats
                kwargs.add(kw.arg)
    return kwargs


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------


class TestPhaseRunnerKwargContract:

    def test_every_kwarg_passed_by_poller_is_in_runner_signature(self):
        """This is THE test that would have caught the
        `resumed_question_path` TypeError before any agent ran it."""
        from sdlc_phase_runner import run_sdlc_phases

        poller_source = (REPO_ROOT / "deployment" / "hermes" / "dispatch_poller.py").read_text()
        poller_kwargs = _find_run_sdlc_phases_call_kwargs(poller_source)
        runner_params = set(inspect.signature(run_sdlc_phases).parameters.keys())

        missing = poller_kwargs - runner_params
        assert not missing, (
            f"dispatch_poller.py passes kwargs to run_sdlc_phases that the "
            f"runner's signature does NOT accept: {sorted(missing)}.\n"
            f"\n"
            f"This is the 2026-04-24 class of bug: the poller TypeErrors, "
            f"catches it as 'falling back to single-shot', and every dispatch "
            f"runs only Phase 1 silently. Fix by adding {sorted(missing)} to "
            f"the run_sdlc_phases signature in deployment/hermes/"
            f"sdlc_phase_runner.py."
        )

    def test_run_sdlc_phases_accepts_resumed_question_path(self):
        """Regression lock: the kwarg that burned us on 2026-04-24 stays in
        the signature. If someone removes it, this test fails."""
        from sdlc_phase_runner import run_sdlc_phases
        params = inspect.signature(run_sdlc_phases).parameters
        assert "resumed_question_path" in params, (
            "run_sdlc_phases must accept resumed_question_path — the poller "
            "passes it when /api/dispatch/claim returns a needs_info_path "
            "for a /resume-ed story. Without this kwarg, every resume "
            "dispatch TypeErrors and degrades to single-shot mode."
        )
        # And it must have a sensible default so old call sites don't break
        assert params["resumed_question_path"].default is None

    def test_poller_passes_resumed_question_path_call_site_exists(self):
        """Contract: the poller DOES call run_sdlc_phases with
        resumed_question_path in at least one code path. If this kwarg
        is removed from the poller entirely, the feature (auto-delete
        stale QUESTION.md on resume) silently disappears."""
        poller_source = (REPO_ROOT / "deployment" / "hermes" / "dispatch_poller.py").read_text()
        poller_kwargs = _find_run_sdlc_phases_call_kwargs(poller_source)
        assert "resumed_question_path" in poller_kwargs, (
            "dispatch_poller.py no longer passes resumed_question_path to "
            "run_sdlc_phases. The stale-QUESTION-on-resume auto-delete "
            "feature is now dead code on the runner side."
        )

    def test_consume_resumed_question_helper_exists(self):
        """The helper the docstring references must actually exist."""
        import sdlc_phase_runner
        assert hasattr(sdlc_phase_runner, "_consume_resumed_question"), (
            "sdlc_phase_runner._consume_resumed_question missing — the "
            "poller docstring references this helper and the signature "
            "accepts the kwarg, but the implementation is gone."
        )


# ---------------------------------------------------------------------------
# Behavior: _consume_resumed_question itself
# ---------------------------------------------------------------------------


class TestConsumeResumedQuestion:

    def test_noop_on_none_path(self, tmp_path):
        from sdlc_phase_runner import _consume_resumed_question
        # Must not raise
        _consume_resumed_question(str(tmp_path), None)

    def test_noop_on_missing_file(self, tmp_path):
        from sdlc_phase_runner import _consume_resumed_question
        _consume_resumed_question(str(tmp_path), "features/story-nonexistent/QUESTION.md")

    def test_deletes_existing_file_relative_path(self, tmp_path):
        from sdlc_phase_runner import _consume_resumed_question
        folder = tmp_path / "features" / "story-575-test"
        folder.mkdir(parents=True)
        q = folder / "QUESTION.md"
        q.write_text("stale question from prior run")
        _consume_resumed_question(str(tmp_path),
                                   "features/story-575-test/QUESTION.md")
        assert not q.exists(), "Stale QUESTION.md should have been deleted"

    def test_deletes_existing_file_absolute_path(self, tmp_path):
        from sdlc_phase_runner import _consume_resumed_question
        q = tmp_path / "QUESTION.md"
        q.write_text("stale")
        _consume_resumed_question(str(tmp_path), str(q))
        assert not q.exists()
