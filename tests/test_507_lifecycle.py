"""STORY-507: Resume-Aware Phase Runner + Dispatch Observability

Phase 7 — RED-state tests. All tests FAIL until Phase 8 implementation is complete.

Coverage:
  A. Paused status enum & DispatchItem model fields       [AC-5]
  B. DB service pause() method                            [AC-5, AC-6]
  C. DB service next_pending() / claim() with paused      [AC-6]
  D. DB service recover_stale_claims() excludes paused    [AC-6]
  F. Phase runner branch resume (_ensure_branch)          [AC-1, AC-2]
  G. Phase runner SIGTERM handler (_graceful_shutdown)    [AC-4]
  H. Phase runner per-file commits (_commit_file)         [AC-3]
  I. Phase runner structured log events (_emit_event)     [AC-8, AC-10]
  J. Dispatch poller rate-limit budget check              [AC-7]
  K. Grafana alert rules config file                      [AC-9]
  L. Migration SQL file existence and content             [AC-5, AC-13]

Route tests (Group E) are in tests/ops_console/test_507_paused_routes.py.

DB tests (Groups B–D) require PostgreSQL ops_console_test with migrations 001–004.
Apply: psql -d ops_console_test -f scripts/migrations/004_paused_status.sql
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

import asyncpg
import pytest
import pytest_asyncio

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).parent.parent
MIGRATIONS_DIR = REPO_ROOT / "scripts" / "migrations"
OBSERVABILITY_DIR = REPO_ROOT / "deployment" / "observability"
PHASE_RUNNER_DIR = REPO_ROOT / "deployment" / "hermes"

# Add phase runner directory to sys.path so we can import the modules.
# The deployment/hermes/ scripts are not a proper package; we use sys.path.
if str(PHASE_RUNNER_DIR) not in sys.path:
    sys.path.insert(0, str(PHASE_RUNNER_DIR))

# ---------------------------------------------------------------------------
# Ops-console model imports (these exist; missing fields/values → RED)
# ---------------------------------------------------------------------------
from tech_dev_agents.ops_console.models.responses import DispatchItem, DispatchStatusEnum
from tech_dev_agents.ops_console.services.dispatch_db_service import (
    DispatchDBService,
    NotFoundError,
)

# ---------------------------------------------------------------------------
# Phase runner module — import as module to avoid collection-error on missing
# functions. Individual tests use getattr() with explicit failure messages.
# ---------------------------------------------------------------------------
try:
    import sdlc_phase_runner as _runner_module
    _RUNNER_AVAILABLE = True
except ImportError as _runner_import_err:
    _runner_module = None  # type: ignore[assignment]
    _RUNNER_AVAILABLE = False

# ---------------------------------------------------------------------------
# Dispatch poller module
# ---------------------------------------------------------------------------
try:
    import dispatch_poller as _poller_module
    _POLLER_AVAILABLE = True
except ImportError:
    _poller_module = None  # type: ignore[assignment]
    _POLLER_AVAILABLE = False

# ---------------------------------------------------------------------------
# PostgreSQL availability check (same pattern as test_dispatch_db_service.py)
# ---------------------------------------------------------------------------
TEST_DATABASE_URL = "postgresql://ops_console:ops_console@localhost/ops_console_test"


def _pg_is_reachable() -> bool:
    async def _check() -> bool:
        try:
            conn = await asyncpg.connect(TEST_DATABASE_URL, timeout=3)
            await conn.close()
            return True
        except Exception:
            return False

    try:
        return asyncio.run(_check())
    except Exception:
        return False


_PG_AVAILABLE = _pg_is_reachable()
_pg_skip = pytest.mark.skipif(
    not _PG_AVAILABLE, reason="PostgreSQL ops_console_test not reachable"
)

# ---------------------------------------------------------------------------
# Helper: build a minimal enqueue payload
# ---------------------------------------------------------------------------
def _enqueue_kwargs(
    story_id: str = "STORY-507",
    repo: str = "tech-dev-agents",
    scope: str = "large",
    prompt: str = "Phase 7 RED test payload",
    enqueued_by: str = "hermes",
) -> dict:
    return {
        "story_id": story_id,
        "repo": repo,
        "scope": scope,
        "prompt": prompt,
        "enqueued_by": enqueued_by,
    }


# ===========================================================================
# Group A — Paused Status Enum & DispatchItem Model Fields [AC-5]
# ===========================================================================

class TestPausedStatusEnum:
    """AC-5: DispatchStatusEnum must include PAUSED and DispatchItem must have
    paused_at, current_phase, and phase_started_at fields.

    All tests RED because the enum and model are not yet updated.
    """

    def test_paused_enum_value(self):
        """DispatchStatusEnum.PAUSED must equal the string 'paused'."""
        assert DispatchStatusEnum.PAUSED == "paused", (
            "DispatchStatusEnum.PAUSED not defined — add PAUSED = 'paused' to enum "
            "(tech_dev_agents/ops_console/models/responses.py)"
        )

    def test_paused_enum_is_str_subclass(self):
        """DispatchStatusEnum.PAUSED must be a str subclass (for JSON serialisation)."""
        assert isinstance(DispatchStatusEnum.PAUSED, str), (
            "DispatchStatusEnum.PAUSED is not a str — enum must inherit from str"
        )

    def test_paused_in_enum_members(self):
        """'paused' must appear in the set of valid status values."""
        values = {e.value for e in DispatchStatusEnum}
        assert "paused" in values, (
            f"'paused' not in DispatchStatusEnum values: {values}"
        )

    def test_dispatch_item_has_paused_at_field(self):
        """DispatchItem must accept paused_at as an optional str field."""
        item = DispatchItem(
            story_id="STORY-507",
            repo="tech-dev-agents",
            scope="large",
            prompt="test",
            enqueued_at="2026-04-21T00:00:00Z",
            enqueued_by="hermes",
            paused_at="2026-04-21T12:00:00Z",  # must not raise ValidationError
        )
        assert item.paused_at == "2026-04-21T12:00:00Z", (
            "DispatchItem.paused_at field not present — add paused_at: str | None = None "
            "to DispatchItem (tech_dev_agents/ops_console/models/responses.py)"
        )

    def test_dispatch_item_has_current_phase_field(self):
        """DispatchItem must accept current_phase as an optional int field."""
        item = DispatchItem(
            story_id="STORY-507",
            repo="tech-dev-agents",
            scope="large",
            prompt="test",
            enqueued_at="2026-04-21T00:00:00Z",
            enqueued_by="hermes",
            current_phase=6,
        )
        assert item.current_phase == 6, (
            "DispatchItem.current_phase field not present — add current_phase: int | None = None"
        )

    def test_dispatch_item_has_phase_started_at_field(self):
        """DispatchItem must accept phase_started_at as an optional str field."""
        item = DispatchItem(
            story_id="STORY-507",
            repo="tech-dev-agents",
            scope="large",
            prompt="test",
            enqueued_at="2026-04-21T00:00:00Z",
            enqueued_by="hermes",
            phase_started_at="2026-04-21T11:00:00Z",
        )
        assert item.phase_started_at == "2026-04-21T11:00:00Z", (
            "DispatchItem.phase_started_at field not present — add phase_started_at: str | None = None"
        )

    def test_dispatch_item_paused_at_defaults_to_none(self):
        """paused_at must default to None when not provided."""
        item = DispatchItem(
            story_id="STORY-507",
            repo="tech-dev-agents",
            scope="large",
            prompt="test",
            enqueued_at="2026-04-21T00:00:00Z",
            enqueued_by="hermes",
        )
        assert item.paused_at is None  # type: ignore[attr-defined]
        assert item.current_phase is None  # type: ignore[attr-defined]
        assert item.phase_started_at is None  # type: ignore[attr-defined]


# ===========================================================================
# Group B — DB Service: pause() Method [AC-5, AC-6]
# ===========================================================================

@pytest_asyncio.fixture
async def db_pool():
    """asyncpg pool to ops_console_test; truncate between tests."""
    pool = await asyncpg.create_pool(TEST_DATABASE_URL, min_size=1, max_size=3)
    async with pool.acquire() as conn:
        await conn.execute("TRUNCATE dispatch_items, agents RESTART IDENTITY CASCADE")
    yield pool
    await pool.close()


@pytest_asyncio.fixture
async def svc(db_pool) -> DispatchDBService:
    return DispatchDBService(db_pool)


@_pg_skip
class TestDispatchDBPause:
    """AC-5/AC-6: DispatchDBService.pause() transitions claimed → paused.

    All tests RED: pause() method not yet implemented.
    """

    @pytest.mark.asyncio
    async def test_pause_transitions_claimed_to_paused(self, svc):
        """pause() must update status from 'claimed' to 'paused'."""
        await svc.enqueue(**_enqueue_kwargs())
        await svc.claim("STORY-507", "daisy")

        assert hasattr(svc, "pause"), (
            "DispatchDBService.pause() not defined — add pause() method to "
            "dispatch_db_service.py"
        )
        result = await svc.pause("STORY-507", "daisy")
        assert result["status"] == "paused", (
            f"Expected status='paused', got {result['status']!r}"
        )

    @pytest.mark.asyncio
    async def test_pause_sets_paused_at_timestamp(self, svc):
        """pause() must set paused_at to a timestamp within the last 5 seconds."""
        await svc.enqueue(**_enqueue_kwargs())
        await svc.claim("STORY-507", "daisy")

        assert hasattr(svc, "pause"), "DispatchDBService.pause() not defined"
        before = datetime.now(timezone.utc)
        result = await svc.pause("STORY-507", "daisy")
        after = datetime.now(timezone.utc)

        paused_at_raw = result.get("paused_at")
        assert paused_at_raw is not None, "pause() result missing 'paused_at'"
        paused_at = datetime.fromisoformat(str(paused_at_raw).replace("Z", "+00:00"))
        assert before <= paused_at <= after, (
            f"paused_at {paused_at} not in expected range [{before}, {after}]"
        )

    @pytest.mark.asyncio
    async def test_pause_stores_current_phase(self, svc):
        """pause(story_id, agent, current_phase=6) must persist the phase number."""
        await svc.enqueue(**_enqueue_kwargs())
        await svc.claim("STORY-507", "daisy")

        assert hasattr(svc, "pause"), "DispatchDBService.pause() not defined"
        result = await svc.pause("STORY-507", "daisy", current_phase=6)
        assert result.get("current_phase") == 6, (
            f"Expected current_phase=6, got {result.get('current_phase')!r}"
        )

    @pytest.mark.asyncio
    async def test_pause_rejects_pending_item(self, svc):
        """pause() on a pending item must raise ValueError (item was never claimed)."""
        await svc.enqueue(**_enqueue_kwargs())

        assert hasattr(svc, "pause"), "DispatchDBService.pause() not defined"
        with pytest.raises((ValueError, Exception)) as exc_info:
            await svc.pause("STORY-507", "daisy")
        assert "pending" in str(exc_info.value).lower() or "claimed" in str(exc_info.value).lower(), (
            f"Expected error mentioning state mismatch, got: {exc_info.value}"
        )

    @pytest.mark.asyncio
    async def test_pause_raises_not_found_for_unknown(self, svc):
        """pause() on an unknown story_id must raise NotFoundError."""
        assert hasattr(svc, "pause"), "DispatchDBService.pause() not defined"
        with pytest.raises(NotFoundError):
            await svc.pause("STORY-NONEXISTENT", "daisy")

    @pytest.mark.asyncio
    async def test_pause_rejects_wrong_agent(self, svc):
        """pause() with wrong agent (not the claimer) must raise ValueError."""
        await svc.enqueue(**_enqueue_kwargs())
        await svc.claim("STORY-507", "daisy")

        assert hasattr(svc, "pause"), "DispatchDBService.pause() not defined"
        with pytest.raises((ValueError, Exception)):
            await svc.pause("STORY-507", "derrick")  # wrong agent — daisy claimed it


# ===========================================================================
# Group C — DB Service: next_pending() and claim() With Paused Items [AC-6]
# ===========================================================================

@_pg_skip
class TestNextPendingWithPaused:
    """AC-6: next_pending() must return paused items alongside pending items."""

    @pytest.mark.asyncio
    async def test_next_pending_returns_paused_items(self, svc):
        """next_pending() must return a paused story when no pending items exist."""
        await svc.enqueue(**_enqueue_kwargs())
        await svc.claim("STORY-507", "daisy")

        assert hasattr(svc, "pause"), "DispatchDBService.pause() not defined"
        await svc.pause("STORY-507", "daisy")

        result = await svc.next_pending()
        assert result is not None, "next_pending() returned None — paused items not included"
        assert result["story_id"] == "STORY-507"
        assert result["status"] == "paused", (
            f"Expected status='paused', got {result['status']!r}"
        )

    @pytest.mark.asyncio
    async def test_next_pending_preserves_fifo_order(self, svc):
        """FIFO: paused story enqueued at T1 returned before pending story at T2."""
        # Enqueue STORY-507 first (becomes paused)
        await svc.enqueue(**_enqueue_kwargs(story_id="STORY-507"))
        await svc.claim("STORY-507", "daisy")
        assert hasattr(svc, "pause"), "DispatchDBService.pause() not defined"
        await svc.pause("STORY-507", "daisy")

        # Enqueue STORY-508 after (pending)
        await svc.enqueue(**_enqueue_kwargs(story_id="STORY-508"))

        result = await svc.next_pending()
        assert result is not None
        assert result["story_id"] == "STORY-507", (
            f"Expected STORY-507 (earlier enqueue_at), got {result['story_id']!r}"
        )

    @pytest.mark.asyncio
    async def test_next_pending_returns_none_when_all_completed(self, svc):
        """next_pending() still returns None when queue is empty of pending+paused."""
        result = await svc.next_pending()
        assert result is None


@_pg_skip
class TestClaimPausedItem:
    """AC-6: claim() must accept paused → claimed transitions."""

    @pytest.mark.asyncio
    async def test_claim_accepts_paused_to_claimed(self, svc):
        """claim() must transition paused → claimed without error."""
        await svc.enqueue(**_enqueue_kwargs())
        await svc.claim("STORY-507", "daisy")
        assert hasattr(svc, "pause"), "DispatchDBService.pause() not defined"
        await svc.pause("STORY-507", "daisy")

        # Re-claim the paused story (possibly by a different agent)
        result = await svc.claim("STORY-507", "derrick")
        assert result["status"] == "claimed", (
            f"Expected status='claimed' after re-claim, got {result['status']!r}"
        )
        assert result["claimed_by"] == "derrick"

    @pytest.mark.asyncio
    async def test_claim_paused_preserves_paused_at(self, svc):
        """Re-claiming a paused item must preserve the original paused_at for audit."""
        await svc.enqueue(**_enqueue_kwargs())
        await svc.claim("STORY-507", "daisy")
        assert hasattr(svc, "pause"), "DispatchDBService.pause() not defined"
        paused_result = await svc.pause("STORY-507", "daisy")
        original_paused_at = paused_result.get("paused_at")

        reclaimed = await svc.claim("STORY-507", "derrick")
        # paused_at should be preserved (not cleared)
        assert reclaimed.get("paused_at") == original_paused_at, (
            "paused_at was cleared on re-claim — it should be preserved for audit trail"
        )


# ===========================================================================
# Group D — DB Service: recover_stale_claims() Excludes Paused [AC-6]
# ===========================================================================

@_pg_skip
class TestRecoverStaleClaimsExcludesPaused:
    """AC-6: Paused items must NOT be recovered to pending by stale-claim recovery."""

    @pytest.mark.asyncio
    async def test_recover_stale_claims_skips_paused(self, svc):
        """Items in 'paused' status must not be reverted to 'pending' by recovery."""
        await svc.enqueue(**_enqueue_kwargs())
        await svc.claim("STORY-507", "daisy")
        assert hasattr(svc, "pause"), "DispatchDBService.pause() not defined"
        await svc.pause("STORY-507", "daisy")

        # Run recovery with timeout=0 (should recover stale claims immediately)
        recovered = await svc.recover_stale_claims(timeout_seconds=0)

        # The paused item must NOT be in the recovered list
        recovered_ids = recovered if isinstance(recovered, list) else []
        assert "STORY-507" not in recovered_ids, (
            "recover_stale_claims() recovered a paused item — paused items must be excluded"
        )

        # Confirm status is still paused
        item = await svc.get("STORY-507")
        assert item["status"] == "paused", (
            f"Paused item was changed to {item['status']!r} by recover_stale_claims()"
        )

    @pytest.mark.asyncio
    async def test_list_queue_includes_paused_items(self, svc):
        """list_queue() must include paused items alongside pending and claimed."""
        await svc.enqueue(**_enqueue_kwargs(story_id="STORY-507"))
        await svc.claim("STORY-507", "daisy")
        assert hasattr(svc, "pause"), "DispatchDBService.pause() not defined"
        await svc.pause("STORY-507", "daisy")

        # Also enqueue a pending item
        await svc.enqueue(**_enqueue_kwargs(story_id="STORY-508"))

        queue = await svc.list_queue()
        story_ids = {item["story_id"] for item in queue}
        assert "STORY-507" in story_ids, (
            "list_queue() did not include the paused item STORY-507"
        )
        statuses = {item["status"] for item in queue}
        assert "paused" in statuses, (
            f"list_queue() results have no 'paused' status items; statuses: {statuses}"
        )


# ===========================================================================
# Phase runner fixtures
# ===========================================================================

@pytest.fixture
def runner_module():
    """Return the sdlc_phase_runner module, or skip if not importable."""
    if not _RUNNER_AVAILABLE:
        pytest.skip(f"sdlc_phase_runner not importable: {_runner_import_err if not _RUNNER_AVAILABLE else ''}")
    return _runner_module


@pytest.fixture
def tmp_git_repo(tmp_path: Path) -> Path:
    """Initialize a minimal git repo in a temp directory for branch-resume tests."""
    env = {**os.environ, "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@test.com",
           "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@test.com"}
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True, env=env)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "test@test.com"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"],
                   check=True, capture_output=True)
    (tmp_path / "README.md").write_text("# Test\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-m", "initial"],
                   check=True, capture_output=True, env=env)
    return tmp_path


# ===========================================================================
# Group F — Phase Runner: Branch Resume [AC-1, AC-2]
# ===========================================================================

class TestPhaseRunnerBranchResume:
    """AC-1, AC-2: _ensure_branch() must check for remote branches and resume."""

    def test_ensure_branch_function_exists(self, runner_module):
        """_ensure_branch must be defined in sdlc_phase_runner."""
        assert hasattr(runner_module, "_ensure_branch"), (
            "_ensure_branch not found in sdlc_phase_runner — branch resume requires "
            "this function to be enhanced (see feature-spec.md section 1a)"
        )

    def test_ensure_branch_calls_ls_remote(self, runner_module, tmp_git_repo):
        """_ensure_branch() must call 'git ls-remote --heads origin story-NNN/*'."""
        _ensure_branch = getattr(runner_module, "_ensure_branch", None)
        if _ensure_branch is None:
            pytest.fail("_ensure_branch not defined in sdlc_phase_runner")

        ls_remote_calls = []

        def mock_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stdout = ""
            if "ls-remote" in cmd:
                ls_remote_calls.append(cmd)
            return result

        with patch("subprocess.run", side_effect=mock_run):
            try:
                _ensure_branch(str(tmp_git_repo), "STORY-507", "507")
            except Exception:
                pass  # We only care that ls-remote was called

        assert any("ls-remote" in str(c) for c in ls_remote_calls), (
            "_ensure_branch() did not call 'git ls-remote' — branch resume requires "
            "checking origin for existing story branches before creating a new one"
        )

    def test_ensure_branch_fetches_remote_when_found(self, runner_module, tmp_git_repo):
        """When ls-remote returns a branch, _ensure_branch() must fetch it."""
        _ensure_branch = getattr(runner_module, "_ensure_branch", None)
        if _ensure_branch is None:
            pytest.fail("_ensure_branch not defined in sdlc_phase_runner")

        fetch_calls = []
        fake_ref = "abc123\trefs/heads/story-507/story-507-resume-aware-phase-runner\n"

        def mock_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            if "ls-remote" in cmd:
                result.stdout = fake_ref
            elif "fetch" in cmd:
                fetch_calls.append(cmd)
                result.stdout = ""
            else:
                result.stdout = ""
            return result

        with patch("subprocess.run", side_effect=mock_run):
            try:
                _ensure_branch(str(tmp_git_repo), "STORY-507", "507")
            except Exception:
                pass

        assert any("fetch" in str(c) for c in fetch_calls), (
            "_ensure_branch() found a remote branch via ls-remote but did not call "
            "'git fetch origin <branch>' — branch resume requires fetching the remote branch"
        )

    def test_ensure_branch_checkouts_existing_remote(self, runner_module, tmp_git_repo):
        """After fetch, _ensure_branch() must checkout the remote branch."""
        _ensure_branch = getattr(runner_module, "_ensure_branch", None)
        if _ensure_branch is None:
            pytest.fail("_ensure_branch not defined in sdlc_phase_runner")

        checkout_calls = []
        fake_ref = "abc123\trefs/heads/story-507/story-507-resume-aware-phase-runner\n"

        def mock_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            if "ls-remote" in cmd:
                result.stdout = fake_ref
            elif "checkout" in cmd:
                checkout_calls.append(list(cmd))
                result.stdout = ""
            else:
                result.stdout = ""
            return result

        with patch("subprocess.run", side_effect=mock_run):
            try:
                _ensure_branch(str(tmp_git_repo), "STORY-507", "507")
            except Exception:
                pass

        remote_branch = "story-507/story-507-resume-aware-phase-runner"
        origin_ref = f"origin/{remote_branch}"
        checked_out_remote = any(
            remote_branch in str(c) or origin_ref in str(c)
            for c in checkout_calls
        )
        assert checked_out_remote, (
            f"_ensure_branch() did not checkout the remote branch '{remote_branch}' — "
            f"checkout calls were: {checkout_calls}"
        )

    def test_ensure_branch_creates_new_when_no_remote(self, runner_module, tmp_git_repo):
        """When ls-remote returns empty, _ensure_branch() creates a new branch."""
        _ensure_branch = getattr(runner_module, "_ensure_branch", None)
        if _ensure_branch is None:
            pytest.fail("_ensure_branch not defined in sdlc_phase_runner")

        checkout_b_calls = []

        def mock_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            if "ls-remote" in cmd:
                result.stdout = ""  # No remote branch found
            elif "checkout" in cmd and "-b" in cmd:
                checkout_b_calls.append(list(cmd))
                result.stdout = ""
            else:
                result.stdout = ""
            return result

        with patch("subprocess.run", side_effect=mock_run):
            try:
                _ensure_branch(str(tmp_git_repo), "STORY-507", "507")
            except Exception:
                pass

        assert len(checkout_b_calls) > 0, (
            "_ensure_branch() did not create a new branch when ls-remote returned empty "
            "(greenfield path must still work)"
        )

    def test_ensure_branch_emits_branch_resume_event(self, runner_module, tmp_git_repo, capsys):
        """When resuming, _ensure_branch() must emit a 'branch_resume' structured event."""
        _ensure_branch = getattr(runner_module, "_ensure_branch", None)
        if _ensure_branch is None:
            pytest.fail("_ensure_branch not defined in sdlc_phase_runner")

        fake_ref = "abc123\trefs/heads/story-507/story-507-resume-aware-phase-runner\n"

        def mock_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stdout = fake_ref if "ls-remote" in cmd else ""
            return result

        with patch("subprocess.run", side_effect=mock_run), \
             patch.dict(os.environ, {"AGENT_NAME": "test-agent"}):
            try:
                _ensure_branch(str(tmp_git_repo), "STORY-507", "507")
            except Exception:
                pass

        captured = capsys.readouterr()
        stdout_lines = [line for line in captured.out.splitlines() if line.strip()]
        json_lines = []
        for line in stdout_lines:
            try:
                obj = json.loads(line)
                json_lines.append(obj)
            except json.JSONDecodeError:
                pass

        resume_events = [e for e in json_lines if e.get("event") == "branch_resume"]
        assert len(resume_events) > 0, (
            "No 'branch_resume' structured event emitted by _ensure_branch() — "
            "add _emit_event('branch_resume', ...) call after successful remote checkout"
        )


# ===========================================================================
# Group G — Phase Runner: SIGTERM Graceful Shutdown [AC-4]
# ===========================================================================

class TestPhaseRunnerSIGTERM:
    """AC-4: SIGTERM handler must commit, push, call pause API, and exit 143."""

    def test_graceful_shutdown_function_exists(self, runner_module):
        """_graceful_shutdown must be defined in sdlc_phase_runner."""
        assert hasattr(runner_module, "_graceful_shutdown"), (
            "_graceful_shutdown not defined in sdlc_phase_runner — "
            "add signal handler (see feature-spec.md section 1b)"
        )

    def test_shutdown_event_exists(self, runner_module):
        """_shutdown_requested threading.Event must be module-level in sdlc_phase_runner."""
        import threading
        event = getattr(runner_module, "_shutdown_requested", None)
        assert event is not None, (
            "_shutdown_requested not defined in sdlc_phase_runner — "
            "add _shutdown_requested = threading.Event() at module level"
        )
        assert isinstance(event, threading.Event), (
            f"_shutdown_requested must be threading.Event, got {type(event)}"
        )

    def test_graceful_shutdown_calls_save_partial_work(self, runner_module):
        """_graceful_shutdown must call _save_partial_work() before exiting."""
        _graceful_shutdown = getattr(runner_module, "_graceful_shutdown", None)
        if _graceful_shutdown is None:
            pytest.fail("_graceful_shutdown not defined in sdlc_phase_runner")

        save_calls = []

        def mock_save(*args, **kwargs):
            save_calls.append(args)

        def mock_exit(code):
            raise SystemExit(code)

        # Set the global context variables that the handler reads
        if hasattr(runner_module, "_current_story_id"):
            runner_module._current_story_id = "STORY-507"
        if hasattr(runner_module, "_current_workdir"):
            runner_module._current_workdir = "/tmp/fake-workdir"
        if hasattr(runner_module, "_current_phase_num"):
            runner_module._current_phase_num = 8
        if hasattr(runner_module, "_current_phase_name"):
            runner_module._current_phase_name = "Implementation"

        with patch.object(runner_module, "_save_partial_work", side_effect=mock_save), \
             patch("sys.exit", side_effect=mock_exit), \
             patch("urllib.request.urlopen", return_value=MagicMock()):
            try:
                _graceful_shutdown(15, None)
            except SystemExit:
                pass
            except Exception:
                pass

        assert len(save_calls) > 0, (
            "_graceful_shutdown() did not call _save_partial_work() — "
            "partial work must be committed before exiting"
        )

    def test_graceful_shutdown_calls_pause_api(self, runner_module):
        """_graceful_shutdown must POST to /dispatch/pause/{story_id}."""
        _graceful_shutdown = getattr(runner_module, "_graceful_shutdown", None)
        if _graceful_shutdown is None:
            pytest.fail("_graceful_shutdown not defined in sdlc_phase_runner")

        urlopen_calls = []

        def mock_urlopen(req, *args, **kwargs):
            urlopen_calls.append(req)
            return MagicMock()

        if hasattr(runner_module, "_current_story_id"):
            runner_module._current_story_id = "STORY-507"
        if hasattr(runner_module, "_current_phase_num"):
            runner_module._current_phase_num = 8

        with patch.object(runner_module, "_save_partial_work", return_value=None), \
             patch("sys.exit", side_effect=SystemExit), \
             patch("urllib.request.urlopen", side_effect=mock_urlopen):
            try:
                _graceful_shutdown(15, None)
            except SystemExit:
                pass
            except Exception:
                pass

        assert len(urlopen_calls) > 0, (
            "_graceful_shutdown() did not call urlopen (pause API) — "
            "handler must POST to /dispatch/pause/{story_id}"
        )
        # Verify it's targeting the pause endpoint
        urls = [getattr(r, "full_url", str(r)) for r in urlopen_calls]
        assert any("pause" in url for url in urls), (
            f"Pause API not called — urlopen URLs were: {urls}"
        )

    def test_graceful_shutdown_exit_code_143(self, runner_module):
        """_graceful_shutdown must exit with code 143 (128 + SIGTERM=15)."""
        _graceful_shutdown = getattr(runner_module, "_graceful_shutdown", None)
        if _graceful_shutdown is None:
            pytest.fail("_graceful_shutdown not defined in sdlc_phase_runner")

        exit_codes = []

        def mock_exit(code):
            exit_codes.append(code)
            raise SystemExit(code)

        with patch.object(runner_module, "_save_partial_work", return_value=None), \
             patch("sys.exit", side_effect=mock_exit), \
             patch("urllib.request.urlopen", return_value=MagicMock()):
            with pytest.raises(SystemExit) as exc_info:
                _graceful_shutdown(15, None)

        assert exc_info.value.code == 143, (
            f"Expected exit code 143 (128+15), got {exc_info.value.code} — "
            "SIGTERM handler must call sys.exit(143)"
        )

    def test_shutdown_event_is_set_by_handler(self, runner_module):
        """_graceful_shutdown must set _shutdown_requested to signal the phase loop."""
        _graceful_shutdown = getattr(runner_module, "_graceful_shutdown", None)
        _shutdown_requested = getattr(runner_module, "_shutdown_requested", None)
        if _graceful_shutdown is None:
            pytest.fail("_graceful_shutdown not defined in sdlc_phase_runner")
        if _shutdown_requested is None:
            pytest.fail("_shutdown_requested not defined in sdlc_phase_runner")

        _shutdown_requested.clear()

        with patch.object(runner_module, "_save_partial_work", return_value=None), \
             patch("sys.exit", side_effect=SystemExit), \
             patch("urllib.request.urlopen", return_value=MagicMock()):
            try:
                _graceful_shutdown(15, None)
            except SystemExit:
                pass

        assert _shutdown_requested.is_set(), (
            "_shutdown_requested event was not set by _graceful_shutdown — "
            "the phase loop checks this event between phases"
        )


# ===========================================================================
# Group H — Phase Runner: Per-File Commits [AC-3]
# ===========================================================================

class TestPhaseRunnerPerFileCommits:
    """AC-3: _commit_file() helper must stage and commit individual files."""

    def test_commit_file_function_exists(self, runner_module):
        """_commit_file must be defined in sdlc_phase_runner."""
        assert hasattr(runner_module, "_commit_file"), (
            "_commit_file not defined in sdlc_phase_runner — "
            "add _commit_file(workdir, filepath, story_id) helper (see feature-spec.md 1c)"
        )

    def test_commit_file_stages_and_commits(self, runner_module, tmp_git_repo):
        """_commit_file(workdir, filepath, story_id) must run git add + git commit."""
        _commit_file = getattr(runner_module, "_commit_file", None)
        if _commit_file is None:
            pytest.fail("_commit_file not defined in sdlc_phase_runner")

        # Create a real file in the tmp repo
        target = tmp_git_repo / "implementation.py"
        target.write_text("def foo(): pass\n")

        # Call with real subprocess (not mocked) to verify actual git behaviour
        result = _commit_file(str(tmp_git_repo), str(target), "STORY-507")

        # Verify commit was created
        log = subprocess.run(
            ["git", "-C", str(tmp_git_repo), "log", "--oneline", "-1"],
            capture_output=True, text=True,
        )
        assert "STORY-507" in log.stdout or "phase-8" in log.stdout, (
            f"No commit with STORY-507 in message found — git log: {log.stdout!r}"
        )

    def test_commit_file_returns_true_on_success(self, runner_module, tmp_git_repo):
        """_commit_file must return True when the commit succeeds."""
        _commit_file = getattr(runner_module, "_commit_file", None)
        if _commit_file is None:
            pytest.fail("_commit_file not defined in sdlc_phase_runner")

        target = tmp_git_repo / "feature.py"
        target.write_text("x = 1\n")
        result = _commit_file(str(tmp_git_repo), str(target), "STORY-507")
        assert result is True, (
            f"_commit_file() returned {result!r}, expected True on success"
        )

    def test_commit_file_returns_false_on_failure(self, runner_module, tmp_git_repo):
        """_commit_file must return False when the commit fails (e.g., nothing staged)."""
        _commit_file = getattr(runner_module, "_commit_file", None)
        if _commit_file is None:
            pytest.fail("_commit_file not defined in sdlc_phase_runner")

        # Pass a non-existent file path — git add will succeed (no-op), commit will fail
        nonexistent = str(tmp_git_repo / "does_not_exist.py")
        result = _commit_file(str(tmp_git_repo), nonexistent, "STORY-507")
        assert result is False, (
            f"_commit_file() returned {result!r} for nonexistent file, expected False"
        )

    def test_phase8_commit_cadence_env_var_defined(self, runner_module):
        """PHASE8_COMMIT_CADENCE constant must be defined with default 'per_file'."""
        cadence = getattr(runner_module, "PHASE8_COMMIT_CADENCE", None)
        assert cadence is not None, (
            "PHASE8_COMMIT_CADENCE not defined in sdlc_phase_runner — "
            "add PHASE8_COMMIT_CADENCE = os.environ.get('PHASE8_COMMIT_CADENCE', 'per_file')"
        )
        # When the env var is not set, default must be 'per_file'
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PHASE8_COMMIT_CADENCE", None)
            # Reload the constant (it's read at module import time)
            default_value = os.environ.get("PHASE8_COMMIT_CADENCE", "per_file")
            assert default_value == "per_file"


# ===========================================================================
# Group I — Phase Runner: Structured Log Events [AC-8, AC-10]
# ===========================================================================

class TestStructuredLogEvents:
    """AC-8, AC-10: _emit_event() must write structured JSON to stdout."""

    def test_emit_event_function_exists(self, runner_module):
        """_emit_event must be defined in sdlc_phase_runner."""
        assert hasattr(runner_module, "_emit_event"), (
            "_emit_event not defined in sdlc_phase_runner — "
            "add _emit_event(event_type, story_id, **kwargs) helper (see feature-spec.md 1d)"
        )

    def test_emit_event_outputs_json_to_stdout(self, runner_module, capsys):
        """_emit_event() must write a JSON-parseable line to stdout."""
        _emit_event = getattr(runner_module, "_emit_event", None)
        if _emit_event is None:
            pytest.fail("_emit_event not defined in sdlc_phase_runner")

        with patch.dict(os.environ, {"AGENT_NAME": "test-agent"}):
            _emit_event("phase_start", story_id="STORY-507")

        captured = capsys.readouterr()
        assert captured.out.strip(), "_emit_event produced no stdout output"
        try:
            obj = json.loads(captured.out.strip().splitlines()[-1])
        except json.JSONDecodeError as e:
            pytest.fail(f"_emit_event output is not valid JSON: {e}\nOutput: {captured.out!r}")
        assert isinstance(obj, dict), f"Expected JSON object, got {type(obj)}"

    def test_emit_event_required_fields(self, runner_module, capsys):
        """Every event must include: event, story_id, agent, timestamp."""
        _emit_event = getattr(runner_module, "_emit_event", None)
        if _emit_event is None:
            pytest.fail("_emit_event not defined in sdlc_phase_runner")

        with patch.dict(os.environ, {"AGENT_NAME": "daisy"}):
            _emit_event("phase_start", story_id="STORY-507")

        captured = capsys.readouterr()
        obj = json.loads(captured.out.strip().splitlines()[-1])
        for field in ("event", "story_id", "agent", "timestamp"):
            assert field in obj, (
                f"Required field '{field}' missing from _emit_event output: {obj}"
            )
        assert obj["event"] == "phase_start"
        assert obj["story_id"] == "STORY-507"
        assert obj["agent"] == "daisy"

    def test_emit_event_phase_start_has_phase_field(self, runner_module, capsys):
        """_emit_event('phase_start', ..., phase=4) must include phase in output."""
        _emit_event = getattr(runner_module, "_emit_event", None)
        if _emit_event is None:
            pytest.fail("_emit_event not defined in sdlc_phase_runner")

        with patch.dict(os.environ, {"AGENT_NAME": "test-agent"}):
            _emit_event("phase_start", story_id="STORY-507", phase=4)

        captured = capsys.readouterr()
        obj = json.loads(captured.out.strip().splitlines()[-1])
        assert "phase" in obj, f"'phase' field missing from phase_start event: {obj}"
        assert obj["phase"] == 4

    def test_emit_event_phase_end_has_duration_and_status(self, runner_module, capsys):
        """_emit_event('phase_end', ..., duration_s=120, status='success') must include both."""
        _emit_event = getattr(runner_module, "_emit_event", None)
        if _emit_event is None:
            pytest.fail("_emit_event not defined in sdlc_phase_runner")

        with patch.dict(os.environ, {"AGENT_NAME": "test-agent"}):
            _emit_event("phase_end", story_id="STORY-507", phase=4,
                        duration_s=120, status="success")

        captured = capsys.readouterr()
        obj = json.loads(captured.out.strip().splitlines()[-1])
        assert "duration_s" in obj, f"'duration_s' missing from phase_end event: {obj}"
        assert "status" in obj, f"'status' missing from phase_end event: {obj}"
        assert obj["duration_s"] == 120
        assert obj["status"] == "success"

    def test_emit_event_phase_skip_has_reason(self, runner_module, capsys):
        """phase_skip event must include a reason field."""
        _emit_event = getattr(runner_module, "_emit_event", None)
        if _emit_event is None:
            pytest.fail("_emit_event not defined in sdlc_phase_runner")

        with patch.dict(os.environ, {"AGENT_NAME": "test-agent"}):
            _emit_event("phase_skip", story_id="STORY-507", phase=1,
                        details={"reason": "deliverable_exists"})

        captured = capsys.readouterr()
        obj = json.loads(captured.out.strip().splitlines()[-1])
        assert "reason" in obj, (
            f"'reason' field missing from phase_skip event: {obj}\n"
            "Either include 'reason' from details dict or add it as a top-level field"
        )
        assert obj["reason"] == "deliverable_exists"

    def test_emit_event_sigterm_has_phase(self, runner_module, capsys):
        """sigterm_received event must include the current phase number."""
        _emit_event = getattr(runner_module, "_emit_event", None)
        if _emit_event is None:
            pytest.fail("_emit_event not defined in sdlc_phase_runner")

        with patch.dict(os.environ, {"AGENT_NAME": "test-agent"}):
            _emit_event("sigterm_received", story_id="STORY-507", phase=8)

        captured = capsys.readouterr()
        obj = json.loads(captured.out.strip().splitlines()[-1])
        assert "phase" in obj, f"'phase' missing from sigterm_received event: {obj}"
        assert obj["phase"] == 8

    def test_emit_event_timestamp_is_utc_iso8601(self, runner_module, capsys):
        """The timestamp field must be parseable as ISO 8601 UTC."""
        _emit_event = getattr(runner_module, "_emit_event", None)
        if _emit_event is None:
            pytest.fail("_emit_event not defined in sdlc_phase_runner")

        with patch.dict(os.environ, {"AGENT_NAME": "test-agent"}):
            _emit_event("phase_start", story_id="STORY-507")

        captured = capsys.readouterr()
        obj = json.loads(captured.out.strip().splitlines()[-1])
        ts = obj.get("timestamp", "")
        assert ts, "timestamp field is empty"
        # Must be parseable — accept both Z suffix and +00:00
        try:
            parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            assert parsed.tzinfo is not None, "timestamp must include timezone info"
        except ValueError as e:
            pytest.fail(f"timestamp '{ts}' is not ISO 8601: {e}")


# ===========================================================================
# Group J — Dispatch Poller: Rate-Limit Budget Check [AC-7]
# ===========================================================================

@pytest.fixture
def poller_module():
    """Return the dispatch_poller module, or skip if not importable."""
    if not _POLLER_AVAILABLE:
        pytest.skip("dispatch_poller not importable from deployment/hermes/")
    return _poller_module


class TestRateLimitBudget:
    """AC-7: Poller must skip poll cycle when <15 minutes remain in rate-limit block."""

    def _make_ccusage_output(self, resets_at_offset_s: float) -> str:
        """Build fake ccusage JSON with resets_at = now + offset_s."""
        resets_at = int(datetime.now(timezone.utc).timestamp()) + int(resets_at_offset_s)
        return json.dumps([{"resets_at": resets_at, "tokens_used": 1000}])

    def test_low_budget_under_threshold_returns_busy(self, poller_module):
        """poll_once() returns 'busy' when fewer than 900 seconds remain."""
        # Find the budget-check function (poll_once or a helper)
        poll_once = getattr(poller_module, "poll_once", None)
        if poll_once is None:
            pytest.fail("poll_once not found in dispatch_poller")

        # 500 seconds remaining — well under 900s threshold
        fake_ccusage_json = self._make_ccusage_output(500)

        def mock_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            if "ccusage" in str(cmd):
                result.stdout = fake_ccusage_json
            else:
                result.stdout = ""
            return result

        # Mock is_agent_idle to True so the idle check passes through to the
        # budget check, and _is_paused to False so only the budget check fires.
        with patch("subprocess.run", side_effect=mock_run), \
             patch("requests.get", return_value=MagicMock(status_code=204)), \
             patch.object(poller_module, "is_agent_idle", return_value=True), \
             patch.object(poller_module, "_is_paused", return_value=False), \
             patch.dict(os.environ, {"AGENT_NAME": "test-agent",
                                     "DISPATCH_API_BASE": "http://localhost:8000/api",
                                     "DISPATCH_API_KEY": "test-key"}):
            result = poll_once()

        assert result == "busy", (
            f"Expected poll_once() to return 'busy' with 500s remaining, got {result!r} — "
            "add 15-minute (900s) threshold check after ccusage parse (see feature-spec.md 2a)"
        )

    def test_budget_at_threshold_returns_busy(self, poller_module):
        """poll_once() returns 'busy' when exactly 900 seconds remain (inclusive boundary)."""
        poll_once = getattr(poller_module, "poll_once", None)
        if poll_once is None:
            pytest.fail("poll_once not found in dispatch_poller")

        fake_ccusage_json = self._make_ccusage_output(900)

        def mock_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stdout = fake_ccusage_json if "ccusage" in str(cmd) else ""
            return result

        # Mock is_agent_idle to True and _is_paused to False so only the
        # budget check determines the return value.
        with patch("subprocess.run", side_effect=mock_run), \
             patch("requests.get", return_value=MagicMock(status_code=204)), \
             patch.object(poller_module, "is_agent_idle", return_value=True), \
             patch.object(poller_module, "_is_paused", return_value=False), \
             patch.dict(os.environ, {"AGENT_NAME": "test-agent",
                                     "DISPATCH_API_BASE": "http://localhost:8000/api",
                                     "DISPATCH_API_KEY": "test-key"}):
            result = poll_once()

        assert result == "busy", (
            f"Expected 'busy' at exactly 900s remaining (inclusive), got {result!r}"
        )

    def test_sufficient_budget_does_not_block(self, poller_module):
        """poll_once() does NOT return 'busy' when 1800 seconds remain."""
        poll_once = getattr(poller_module, "poll_once", None)
        if poll_once is None:
            pytest.fail("poll_once not found in dispatch_poller")

        fake_ccusage_json = self._make_ccusage_output(1800)

        def mock_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stdout = fake_ccusage_json if "ccusage" in str(cmd) else ""
            return result

        # Mock the API to return 204 (empty queue) so poll_once returns "empty".
        # is_agent_idle=True and _is_paused=False isolates the budget check.
        with patch("subprocess.run", side_effect=mock_run), \
             patch("requests.get", return_value=MagicMock(status_code=204)), \
             patch.object(poller_module, "is_agent_idle", return_value=True), \
             patch.object(poller_module, "_is_paused", return_value=False), \
             patch.dict(os.environ, {"AGENT_NAME": "test-agent",
                                     "DISPATCH_API_BASE": "http://localhost:8000/api",
                                     "DISPATCH_API_KEY": "test-key"}):
            result = poll_once()

        assert result != "busy", (
            f"poll_once() returned 'busy' with 1800s remaining — "
            "budget check should only block when remaining < 900s, got {result!r}"
        )

    def test_missing_resets_at_does_not_block(self, poller_module):
        """Missing or malformed ccusage data must not block the poll cycle."""
        poll_once = getattr(poller_module, "poll_once", None)
        if poll_once is None:
            pytest.fail("poll_once not found in dispatch_poller")

        # ccusage returns JSON without resets_at
        fake_ccusage_json = json.dumps([{"tokens_used": 500}])

        def mock_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stdout = fake_ccusage_json if "ccusage" in str(cmd) else ""
            return result

        # is_agent_idle=True and _is_paused=False so the test only checks
        # that missing ccusage data never causes a spurious "busy" block.
        with patch("subprocess.run", side_effect=mock_run), \
             patch("requests.get", return_value=MagicMock(status_code=204)), \
             patch.object(poller_module, "is_agent_idle", return_value=True), \
             patch.object(poller_module, "_is_paused", return_value=False), \
             patch.dict(os.environ, {"AGENT_NAME": "test-agent",
                                     "DISPATCH_API_BASE": "http://localhost:8000/api",
                                     "DISPATCH_API_KEY": "test-key"}):
            result = poll_once()

        assert result != "busy", (
            "poll_once() returned 'busy' when ccusage had no resets_at — "
            "missing data must not block the poll cycle"
        )


# ===========================================================================
# Group K — Observability: Grafana Alert Config [AC-9]
# ===========================================================================

class TestGrafanaAlerts:
    """AC-9: grafana-alerts.yml must exist with 4 alert rules at correct thresholds."""

    ALERTS_FILE = OBSERVABILITY_DIR / "grafana-alerts.yml"

    def _load_alerts(self):
        """Load and parse the grafana-alerts.yml file."""
        import yaml  # type: ignore[import-untyped]
        if not self.ALERTS_FILE.exists():
            pytest.fail(
                f"deployment/observability/grafana-alerts.yml does not exist — "
                f"create it with 4 alert rules (see feature-spec.md Observability Design)"
            )
        return yaml.safe_load(self.ALERTS_FILE.read_text())

    def test_grafana_alerts_file_exists(self):
        """deployment/observability/grafana-alerts.yml must exist."""
        assert self.ALERTS_FILE.exists(), (
            f"Grafana alerts file not found at {self.ALERTS_FILE} — "
            "create deployment/observability/grafana-alerts.yml (AC-9)"
        )

    def test_grafana_alerts_parseable_yaml(self):
        """grafana-alerts.yml must be valid YAML with Grafana alert schema."""
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError:
            pytest.skip("PyYAML not installed — pip install pyyaml")
        content = self._load_alerts()
        assert isinstance(content, dict), "grafana-alerts.yml must be a YAML mapping"
        assert "groups" in content, (
            "grafana-alerts.yml missing 'groups' key — use Grafana alert provisioning schema"
        )

    def test_phase_duration_warning_alert_rule(self):
        """Alert rule 'phase-duration-warning' with threshold 1800s must exist."""
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError:
            pytest.skip("PyYAML not installed")
        content = self._load_alerts()
        rules = [
            rule
            for group in content.get("groups", [])
            for rule in group.get("rules", [])
        ]
        uids = [r.get("uid", "") for r in rules]
        assert "phase-duration-warning" in uids, (
            f"Alert 'phase-duration-warning' not found — rule UIDs: {uids}"
        )
        warning_rule = next(r for r in rules if r.get("uid") == "phase-duration-warning")
        # Verify threshold is 1800 (30 minutes)
        rule_str = str(warning_rule)
        assert "1800" in rule_str, (
            f"phase-duration-warning rule does not contain threshold 1800: {warning_rule}"
        )

    def test_phase_duration_critical_alert_rule(self):
        """Alert rule 'phase-duration-critical' with threshold 3600s must exist."""
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError:
            pytest.skip("PyYAML not installed")
        content = self._load_alerts()
        rules = [
            rule
            for group in content.get("groups", [])
            for rule in group.get("rules", [])
        ]
        uids = [r.get("uid", "") for r in rules]
        assert "phase-duration-critical" in uids, (
            f"Alert 'phase-duration-critical' not found — rule UIDs: {uids}"
        )
        critical_rule = next(r for r in rules if r.get("uid") == "phase-duration-critical")
        rule_str = str(critical_rule)
        assert "3600" in rule_str, (
            f"phase-duration-critical does not contain threshold 3600: {critical_rule}"
        )

    def test_failure_rate_alert_rule(self):
        """Alert rule 'dispatch-failure-rate' with threshold 0.2 must exist."""
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError:
            pytest.skip("PyYAML not installed")
        content = self._load_alerts()
        rules = [
            rule
            for group in content.get("groups", [])
            for rule in group.get("rules", [])
        ]
        uids = [r.get("uid", "") for r in rules]
        assert "dispatch-failure-rate" in uids, (
            f"Alert 'dispatch-failure-rate' not found — rule UIDs: {uids}"
        )
        failure_rule = next(r for r in rules if r.get("uid") == "dispatch-failure-rate")
        rule_str = str(failure_rule)
        assert "0.2" in rule_str, (
            f"dispatch-failure-rate does not contain threshold 0.2: {failure_rule}"
        )

    def test_partial_pr_alert_rule(self):
        """Alert rule 'partial-pr-rate' must exist."""
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError:
            pytest.skip("PyYAML not installed")
        content = self._load_alerts()
        rules = [
            rule
            for group in content.get("groups", [])
            for rule in group.get("rules", [])
        ]
        uids = [r.get("uid", "") for r in rules]
        assert "partial-pr-rate" in uids, (
            f"Alert 'partial-pr-rate' not found — rule UIDs: {uids}"
        )


# ===========================================================================
# Group L — Migration Files: SQL Existence and Content [AC-5, AC-13]
# ===========================================================================

class TestMigrationFiles:
    """AC-5, AC-13: Migration SQL files must exist with correct content."""

    MIGRATION_004 = MIGRATIONS_DIR / "004_paused_status.sql"
    MIGRATION_005 = MIGRATIONS_DIR / "005_cleanup_contaminated_completed.sql"

    def test_migration_004_file_exists(self):
        """scripts/migrations/004_paused_status.sql must exist."""
        assert self.MIGRATION_004.exists(), (
            f"Migration file not found: {self.MIGRATION_004} — "
            "create scripts/migrations/004_paused_status.sql (AC-5)"
        )

    def test_migration_004_adds_paused_at_column(self):
        """Migration 004 must add the paused_at TIMESTAMPTZ column."""
        if not self.MIGRATION_004.exists():
            pytest.fail(f"{self.MIGRATION_004} does not exist")
        sql = self.MIGRATION_004.read_text().lower()
        assert "paused_at" in sql, (
            "004_paused_status.sql missing 'paused_at' column — "
            "add: ALTER TABLE dispatch_items ADD COLUMN IF NOT EXISTS paused_at TIMESTAMPTZ"
        )
        assert "add column" in sql, (
            "004_paused_status.sql missing ADD COLUMN statement for paused_at"
        )

    def test_migration_004_adds_current_phase_column(self):
        """Migration 004 must add the current_phase INTEGER column."""
        if not self.MIGRATION_004.exists():
            pytest.fail(f"{self.MIGRATION_004} does not exist")
        sql = self.MIGRATION_004.read_text().lower()
        assert "current_phase" in sql, (
            "004_paused_status.sql missing 'current_phase' column — "
            "add: ALTER TABLE dispatch_items ADD COLUMN IF NOT EXISTS current_phase INTEGER"
        )

    def test_migration_004_updates_check_constraint(self):
        """Migration 004 must add 'paused' to the status CHECK constraint."""
        if not self.MIGRATION_004.exists():
            pytest.fail(f"{self.MIGRATION_004} does not exist")
        sql = self.MIGRATION_004.read_text()
        assert "'paused'" in sql or '"paused"' in sql, (
            "004_paused_status.sql does not include 'paused' in CHECK constraint — "
            "must update dispatch_items_status_check to allow 'paused'"
        )
        assert "check" in sql.lower(), (
            "004_paused_status.sql missing CHECK constraint update"
        )

    def test_migration_004_updates_unique_index(self):
        """Migration 004 must update uq_story_active_idx to include 'paused'."""
        if not self.MIGRATION_004.exists():
            pytest.fail(f"{self.MIGRATION_004} does not exist")
        sql = self.MIGRATION_004.read_text()
        # The index must cover 'paused' so a paused story blocks duplicate enqueues
        assert "uq_story_active_idx" in sql, (
            "004_paused_status.sql does not recreate uq_story_active_idx — "
            "the index must be extended to include 'paused' status"
        )
        # Verify 'paused' appears in the index definition context
        idx_section = sql[sql.lower().find("uq_story_active_idx"):]
        nearby = idx_section[:300].lower()
        assert "paused" in nearby, (
            "uq_story_active_idx in 004_paused_status.sql does not include 'paused' — "
            "the WHERE clause must be: WHERE status IN ('pending', 'claimed', 'paused')"
        )

    def test_migration_005_file_exists(self):
        """scripts/migrations/005_cleanup_contaminated_completed.sql must exist."""
        assert self.MIGRATION_005.exists(), (
            f"Migration file not found: {self.MIGRATION_005} — "
            "create scripts/migrations/005_cleanup_contaminated_completed.sql (AC-13)"
        )

    def test_migration_005_has_dry_run_select(self):
        """Migration 005 must contain a SELECT targeting completed rows with no commit_sha."""
        if not self.MIGRATION_005.exists():
            pytest.fail(f"{self.MIGRATION_005} does not exist")
        sql = self.MIGRATION_005.read_text().lower()
        assert "select" in sql, (
            "005_cleanup_contaminated_completed.sql missing SELECT statement — "
            "must include a dry-run SELECT to count affected rows"
        )
        assert "completed" in sql, (
            "005 migration does not reference 'completed' status"
        )
        assert "commit_sha" in sql, (
            "005 migration does not reference commit_sha — "
            "contaminated rows are identified by missing commit_sha"
        )

    def test_migration_005_update_is_commented_out(self):
        """Migration 005's destructive UPDATE must be commented out by default (dry-run safe)."""
        if not self.MIGRATION_005.exists():
            pytest.fail(f"{self.MIGRATION_005} does not exist")
        sql = self.MIGRATION_005.read_text()
        lines = sql.splitlines()
        uncommented_updates = [
            line.strip() for line in lines
            if line.strip().upper().startswith("UPDATE")
            and not line.strip().startswith("--")
        ]
        assert len(uncommented_updates) == 0, (
            f"005_cleanup_contaminated_completed.sql has uncommented UPDATE statements — "
            f"the UPDATE must be commented out by default (dry-run first, confirm with Mark):\n"
            + "\n".join(uncommented_updates)
        )
