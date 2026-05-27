"""Regression tests for the PR-rework dispatch path.

Observed failure (2026-04-24): 19 of 46 dispatch failures were PR reworks.
The phase runner would spin up a FRESH branch for the rework story instead
of checking out the existing PR branch, SDLC deliverables landed in a
new ``features/story-<rework-id>/`` folder instead of the base story's
folder, and the deliverable verifier rejected the run.

Root cause: no ``rework_of`` column on ``dispatch_items`` and no plumbing
to carry the base story id from ``/dispatch/next`` → poller → phase
runner. A prior handoff claimed this was fixed via STORY-336 PR #44 —
the rate-limit commits landed, the rework wiring never did.

These tests pin each boundary end-to-end:

  DB       — migration adds ``rework_of`` column
  Poller   — poll_once extracts rework_of and threads it to start_story
  Retry    — _report_fail preserves rework_of in auto-retry payload
  start    — start_story signature accepts rework_of, forwards to runner
  Runner   — run_sdlc_phases accepts rework_of, forwards to
             _ensure_branch + _extract_story_folder
  Branch   — _ensure_branch uses rework_of's branch prefix when set
  Folder   — _extract_story_folder resolves to base-story folder when set
"""

from __future__ import annotations

import inspect
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# DB — migration adds rework_of column
# ---------------------------------------------------------------------------


class TestMigrationAddsReworkOf:
    def test_migration_009_exists_and_adds_rework_of(self):
        """Migration must add a ``rework_of`` column to ``dispatch_items``.
        Idempotent (IF NOT EXISTS) per the established migration pattern.
        """
        migration = REPO_ROOT / "scripts" / "migrations" / "009_rework_of.sql"
        assert migration.exists(), (
            f"Missing migration file: {migration}. Add the column with "
            "ALTER TABLE dispatch_items ADD COLUMN IF NOT EXISTS rework_of VARCHAR(20)."
        )
        sql = migration.read_text().lower()
        assert "alter table" in sql and "dispatch_items" in sql, (
            "Migration must target dispatch_items table"
        )
        assert "rework_of" in sql, "Migration must add rework_of column"
        # Idempotency — the other migrations all use IF NOT EXISTS
        assert "if not exists" in sql, (
            "Migration must be idempotent via IF NOT EXISTS (migration-runner "
            "re-applies the file on every startup)"
        )


# ---------------------------------------------------------------------------
# Poller — poll_once extracts rework_of and threads it
# ---------------------------------------------------------------------------


class TestPollOnceExtractsReworkOf:
    def test_poll_once_passes_rework_of_to_start_story(self, tmp_path):
        """``/dispatch/next`` item carries rework_of → poll_once must
        forward it to ``start_story``. Without this, the base-story
        context is lost between claim and SDK launch.
        """
        from deployment.hermes import dispatch_poller

        # No pause flag active
        missing_flag = tmp_path / "no-such-flag"

        next_resp = MagicMock(status_code=200)
        next_resp.json.return_value = {
            "item": {
                "story_id": "STORY-570",
                "repo": "some-repo",
                "scope": "small",
                "prompt": "Fix PR #169 review comments",
                "title": "Rework",
                "rework_of": "STORY-169",
            }
        }
        claim_resp = MagicMock(status_code=200)
        claim_resp.json.return_value = {"item": {"story_id": "STORY-570"}}

        session = MagicMock()
        session.get.return_value = next_resp
        session.post.return_value = claim_resp

        captured: dict = {}

        def fake_start_story(**kwargs):
            captured.update(kwargs)

        with patch.object(dispatch_poller, "is_agent_idle", return_value=True), \
             patch.object(dispatch_poller, "PAUSE_FLAG_PATH", str(missing_flag)), \
             patch.object(dispatch_poller, "start_story", side_effect=fake_start_story):
            result = dispatch_poller.poll_once(
                session=session,
                base_url="http://ops:8000",
                api_key="k",
                agent_name="daisy",
                workspace="/tmp/ws",
            )

        assert result == "claimed"
        assert captured.get("rework_of") == "STORY-169", (
            f"poll_once must extract rework_of from item and pass to "
            f"start_story; got captured kwargs: {captured}"
        )

    def test_poll_once_passes_none_rework_of_for_normal_dispatch(self, tmp_path):
        """Absence of rework_of in the item → forward as None, never raise."""
        from deployment.hermes import dispatch_poller

        missing_flag = tmp_path / "no-such-flag"
        next_resp = MagicMock(status_code=200)
        next_resp.json.return_value = {
            "item": {
                "story_id": "STORY-600",
                "repo": "some-repo",
                "scope": "small",
                "prompt": "new story",
                # no rework_of
            }
        }
        claim_resp = MagicMock(status_code=200)
        claim_resp.json.return_value = {"item": {"story_id": "STORY-600"}}

        session = MagicMock()
        session.get.return_value = next_resp
        session.post.return_value = claim_resp

        captured: dict = {}

        def fake_start_story(**kwargs):
            captured.update(kwargs)

        with patch.object(dispatch_poller, "is_agent_idle", return_value=True), \
             patch.object(dispatch_poller, "PAUSE_FLAG_PATH", str(missing_flag)), \
             patch.object(dispatch_poller, "start_story", side_effect=fake_start_story):
            dispatch_poller.poll_once(
                session=session,
                base_url="http://ops:8000",
                api_key="k",
                agent_name="daisy",
                workspace="/tmp/ws",
            )

        assert captured.get("rework_of") is None


# ---------------------------------------------------------------------------
# Retry — _report_fail preserves rework_of in the re-enqueue payload
# ---------------------------------------------------------------------------


class TestReportFailPreservesReworkOf:
    def test_report_fail_includes_rework_of_in_retry_post(self):
        """Retry re-enqueue without rework_of was the 422-loss bug: once
        the first attempt failed, the re-enqueued row lost its base-story
        linkage and the next agent treated it as a greenfield story.
        """
        from deployment.hermes import dispatch_poller

        session = MagicMock()
        # fail POST returns 200 so we proceed to the retry branch
        session.post.return_value = MagicMock(status_code=201, text="ok")

        with patch.object(dispatch_poller, "_count_retries", return_value=0):
            dispatch_poller._report_fail(
                session=session,
                base_url="http://ops:8000",
                api_key="k",
                story_id="STORY-570",
                exit_code=1,
                repo="some-repo",
                scope="small",
                prompt="Fix PR #169",
                duration_seconds=600,  # not a phantom claim
                rework_of="STORY-169",
            )

        # Find the retry POST (the 2nd session.post call in the flow: fail, then /dispatch)
        retry_calls = [
            c for c in session.post.call_args_list
            if c.args and c.args[0].endswith("/api/dispatch")
        ]
        assert retry_calls, "Expected a retry POST to /api/dispatch"
        payload = retry_calls[-1].kwargs.get("json") or {}
        assert payload.get("rework_of") == "STORY-169", (
            f"_report_fail retry payload must preserve rework_of; got {payload}"
        )

    def test_report_fail_signature_accepts_rework_of(self):
        from deployment.hermes import dispatch_poller

        sig = inspect.signature(dispatch_poller._report_fail)
        assert "rework_of" in sig.parameters, (
            "_report_fail must accept rework_of kwarg so retries preserve it"
        )


# ---------------------------------------------------------------------------
# start_story — signature + forwarding
# ---------------------------------------------------------------------------


class TestStartStoryForwardsReworkOf:
    def test_start_story_signature_accepts_rework_of(self):
        from deployment.hermes import dispatch_poller

        sig = inspect.signature(dispatch_poller.start_story)
        assert "rework_of" in sig.parameters, (
            "start_story must accept rework_of so poll_once can pass it through"
        )


# ---------------------------------------------------------------------------
# Runner — run_sdlc_phases signature accepts rework_of
# ---------------------------------------------------------------------------


class TestRunSdlcPhasesAcceptsReworkOf:
    def test_run_sdlc_phases_signature_accepts_rework_of(self):
        from deployment.hermes import sdlc_phase_runner

        sig = inspect.signature(sdlc_phase_runner.run_sdlc_phases)
        assert "rework_of" in sig.parameters, (
            "run_sdlc_phases must accept rework_of so the phase runner can "
            "route branch/folder resolution through the base story"
        )


# ---------------------------------------------------------------------------
# Branch — _ensure_branch uses rework_of's prefix when set
# ---------------------------------------------------------------------------


class TestEnsureBranchRespectsReworkOf:
    def test_ensure_branch_signature_accepts_rework_of(self):
        from deployment.hermes import sdlc_phase_runner

        sig = inspect.signature(sdlc_phase_runner._ensure_branch)
        assert "rework_of" in sig.parameters, (
            "_ensure_branch must accept rework_of so ls-remote matches the "
            "base story's existing branch pattern, not the rework story_id's"
        )

    def test_ensure_branch_probes_rework_story_prefix(self, tmp_path):
        """When rework_of is set, ls-remote must search for the BASE story's
        branch pattern (``story-<rework_num>/*``) — not the rework's own.
        """
        from deployment.hermes import sdlc_phase_runner

        # Stub ls-remote to return a matching remote branch
        def fake_run(*args, **kwargs):
            # args[0] is the command list
            cmd = args[0] if args else kwargs.get("args", [])
            if "ls-remote" in cmd:
                # Capture the branch-glob arg so we can assert on it
                fake_run.last_glob = cmd[-1]
                return MagicMock(
                    returncode=0,
                    stdout="abc123\trefs/heads/story-169/fix-auth\n",
                    stderr="",
                )
            if "fetch" in cmd:
                return MagicMock(returncode=0, stdout="", stderr="")
            if "checkout" in cmd:
                return MagicMock(returncode=0, stdout="", stderr="")
            if "branch" in cmd and "--show-current" in cmd:
                return MagicMock(returncode=0, stdout="main\n", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")
        fake_run.last_glob = ""

        with patch.object(sdlc_phase_runner.subprocess, "run", side_effect=fake_run), \
             patch.object(sdlc_phase_runner, "_emit_event"):
            sdlc_phase_runner._ensure_branch(
                workdir=str(tmp_path),
                story_id="STORY-570",
                rework_of="STORY-169",
            )

        assert "story-169/" in fake_run.last_glob, (
            f"_ensure_branch with rework_of='STORY-169' must probe "
            f"'story-169/*', got {fake_run.last_glob!r}. The rework lands "
            "on a FRESH branch if the probe uses the rework story_id."
        )


# ---------------------------------------------------------------------------
# Folder — _extract_story_folder resolves to rework_of's folder when set
# ---------------------------------------------------------------------------


class TestExtractStoryFolderRespectsReworkOf:
    def test_extract_story_folder_signature_accepts_rework_of(self):
        from deployment.hermes import sdlc_phase_runner

        sig = inspect.signature(sdlc_phase_runner._extract_story_folder)
        assert "rework_of" in sig.parameters, (
            "_extract_story_folder must accept rework_of so SDLC deliverables "
            "land in the BASE story's folder, where the deliverable verifier "
            "actually looks"
        )

    def test_extract_story_folder_uses_rework_base_folder(self, tmp_path):
        """If ``features/story-169-fix-auth`` exists and rework_of=STORY-169,
        the resolver must return that — not ``features/story-570-*``.
        """
        from deployment.hermes import sdlc_phase_runner

        (tmp_path / "features" / "story-169-fix-auth").mkdir(parents=True)

        folder = sdlc_phase_runner._extract_story_folder(
            story_id="STORY-570",
            workdir=str(tmp_path),
            rework_of="STORY-169",
        )
        assert folder == "story-169-fix-auth", (
            f"Expected story-169-fix-auth (base story's existing folder), "
            f"got {folder!r}. The deliverable verifier looks under the base "
            "story's folder; a mismatched folder is the '422 gate rejection' "
            "pattern from the 2026-04-24 rework failures."
        )

    def test_extract_story_folder_without_rework_of_unchanged(self, tmp_path):
        """Regression guard: normal (non-rework) behavior is preserved."""
        from deployment.hermes import sdlc_phase_runner

        (tmp_path / "features" / "story-600-greenfield").mkdir(parents=True)
        folder = sdlc_phase_runner._extract_story_folder(
            story_id="STORY-600",
            workdir=str(tmp_path),
        )
        assert folder == "story-600-greenfield"
