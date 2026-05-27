"""STORY-511 regression: gate-rejected completions must not loop.

On 2026-04-22 the server's completion gate legitimately rejected
STORY-521 with ``422: SDLC deliverables missing`` (the agent wrote
``features/story-521/seed.md`` but the gate checks
``features/story-521-*/seed.md``). The poller then added STORY-521 to
``_LOCALLY_COMPLETED`` unconditionally, so every subsequent poll
returned the still-pending story, saw it in the local set, and
``continue``d — an infinite "already completed locally — skipping"
loop that ate ~10% of each agent's 5-hour billing block before we
caught it.

This file tests the two-part fix:
  (a) ``_report_complete`` returns the HTTP status code.
  (b) The caller only adds to ``_LOCALLY_COMPLETED`` on 200, and
      converts 422 to a ``_report_fail`` call so auto-retry can run.

Mock-only; does not require real HTTP or a postgres.
"""

from __future__ import annotations

import importlib.util
import inspect
import pathlib

import pytest


@pytest.fixture(scope="module")
def poller():
    """Load dispatch_poller.py as a module for inspection."""
    repo_root = pathlib.Path(__file__).resolve().parent.parent.parent
    mod_path = repo_root / "deployment" / "hermes" / "dispatch_poller.py"
    spec = importlib.util.spec_from_file_location("_poller_test", mod_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_report_complete_returns_status_code(poller):
    """Signature change: _report_complete returns int, not None.

    Callers rely on this to decide whether to mark the story locally-
    complete. If the return type drifts back to None, the caller's
    ``if complete_status == 200`` branch always fails → old loop bug.
    """
    sig = inspect.signature(poller._report_complete)
    # The module uses ``from __future__ import annotations`` so the
    # annotation comes through as the string ``'int'``. Accept either.
    ann = sig.return_annotation
    assert ann == int or ann == "int", (
        f"_report_complete return annotation is {ann!r} — must be "
        "`int` (the HTTP status code) so callers can branch on it"
    )


def test_report_complete_422_logs_body_for_diagnosis(poller):
    """422 response body must be logged so operators can diagnose why
    the gate rejected the completion (missing deliverables, bad SHA,
    etc.). Regressing this would turn a useful error into a silent one.
    """
    source = inspect.getsource(poller._report_complete)
    assert "422 body:" in source, (
        "_report_complete no longer logs the 422 response body — "
        "restore the print so gate-rejection reasons stay visible"
    )


def test_poller_branches_on_complete_status(poller):
    """The caller (start_story or equivalent) must branch on the return
    value of _report_complete. Specifically:
      - 200 → add to _LOCALLY_COMPLETED
      - 422 → call _report_fail
      - other → log but do NOT add to _LOCALLY_COMPLETED
    """
    # Search the module source for the branching. It's easier to grep
    # than to reconstruct the full control flow with fakes.
    source = inspect.getsource(poller)
    assert "complete_status == 200" in source or "complete_status==200" in source, (
        "poller does not branch on complete_status == 200 — the 422 "
        "rejection would be ignored and the locally-completed set "
        "would get polluted again"
    )
    assert "complete_status == 422" in source or "complete_status==422" in source, (
        "poller does not handle complete_status == 422 — gate "
        "rejections must convert to _report_fail so the story stops "
        "appearing as 'pending' on the server"
    )


def test_422_path_calls_report_fail(poller):
    """On 422, the story must be failed via _report_fail so the
    dispatch queue's auto-retry mechanism can drive a next attempt
    (possibly after a spec fix). Without this the story stays pending
    forever on the server and loops on every agent that claims it."""
    source = inspect.getsource(poller)
    # Find the 422 branch and confirm _report_fail is in its body.
    # Simple heuristic: the 422 branch should appear near a _report_fail
    # call in the same function.
    assert source.count("_report_fail") >= 2, (
        "Expected _report_fail to be called in the 422 branch in "
        "addition to the existing failure paths"
    )
    # Confirm the 422 branch specifically mentions fail conversion
    assert "rejected by gate" in source or "converting to fail" in source, (
        "422 branch missing the gate-rejection → fail conversion "
        "comment and path"
    )


def test_no_base_url_path_preserves_prior_behavior(poller):
    """Test-mode or single-agent runs with no base_url set must still
    add to _LOCALLY_COMPLETED so a local-only retry doesn't re-run the
    same work. Only the server-reporting path added the new gating."""
    source = inspect.getsource(poller)
    assert "single-agent test" in source or "No base_url" in source or "No base_url/api_key" in source, (
        "The fallback path (no base_url) must keep the historical "
        "behavior of marking locally-completed, documented inline"
    )
