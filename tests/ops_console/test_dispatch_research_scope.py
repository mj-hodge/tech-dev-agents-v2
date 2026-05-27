"""STORY-539: Research Dispatch Scope — ops console API tests.

RED state summary
-----------------
Group A: test_dispatch_accepts_scope_research FAILS — scope regex only allows
         small|medium|large. Phase 8 must extend to include "research".

Group B: test_completion_gate_accepts_research_without_pr FAILS — _SDLC_REQUIRED
         has no "research" key; falls back to "small" (requires seed.md, not research.md).
         test_completion_gate_rejects_research_with_missing_research_md FAILS —
         error message mentions seed.md, not research.md.

Group C: test_research_seed_is_optional FAILS — PHASE_MAP.get("research", PHASE_MAP["small"])
         returns PHASES_SMALL → Phase 1 runs instead of Phase 2.

Note: This file uses its own Settings/app/client fixtures rather than the conftest's
      test_settings to avoid a pre-existing pydantic alias issue introduced by
      STORY-532 Phase 8 (dispatch_needs_info_enabled alias vs populate_by_name).

Group A — Scope validation (no PG required)
  A-01: test_dispatch_accepts_scope_research
  A-02: test_dispatch_rejects_unknown_scope (regression guard — already PASSES)
  A-03: test_queue_preserves_research_scope

Group B — Completion gate for research scope (mocked DB + GitHub)
  B-01: test_completion_gate_accepts_research_without_pr
  B-02: test_completion_gate_rejects_research_with_missing_research_md

Group C — Seed is optional for research scope (phase runner unit test)
  C-01: test_research_seed_is_optional
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio

from tests.ops_console.conftest import TEST_API_KEY, inject_mock_services
from tech_dev_agents.ops_console.config import Settings
from tech_dev_agents.ops_console.main import create_app
from tech_dev_agents.ops_console.services.dispatch_service import DispatchFallbackService

# ---------------------------------------------------------------------------
# Phase runner loader (for Group C)
# ---------------------------------------------------------------------------

REPO_ROOT = pathlib.Path(__file__).parents[2]
PHASE_RUNNER_SRC = REPO_ROOT / "deployment" / "hermes" / "sdlc_phase_runner.py"

_phase_runner_cache: dict = {}


def _get_phase_runner():
    """Load sdlc_phase_runner as a module (cached)."""
    if "module" in _phase_runner_cache:
        return _phase_runner_cache["module"]
    hermes_dir = str(PHASE_RUNNER_SRC.parent)
    if hermes_dir not in sys.path:
        sys.path.insert(0, hermes_dir)
    spec = importlib.util.spec_from_file_location(
        "sdlc_phase_runner_ops_test", str(PHASE_RUNNER_SRC)
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _phase_runner_cache["module"] = mod
    return mod


# ---------------------------------------------------------------------------
# Local fixtures — independent of conftest's broken test_settings
# (STORY-532 Phase 8 added alias="OPS_DISPATCH_NEEDS_INFO_ENABLED" to
#  dispatch_needs_info_enabled without populate_by_name=True, which breaks
#  Settings(dispatch_needs_info_enabled=True) in the conftest.)
# ---------------------------------------------------------------------------

VALID_SHA = "a" * 40  # 40-char lowercase hex — passes CompleteRequest validation


@pytest.fixture
def research_settings(tmp_path):
    """Minimal Settings for research-scope tests — no broken alias fields."""
    registry = tmp_path / "agent-registry.json"
    registry.write_text(json.dumps([
        {"name": "dan", "host": "10.0.1.10", "port": 8080, "role": "developer", "enabled": True}
    ]))
    return Settings(
        ops_console_api_key=TEST_API_KEY,
        loki_api_key="test-loki-key",
        agent_api_key="test-agent-key",
        agent_registry_path=str(registry),
        azure_subscription_id=None,
        database_url="",  # JSON fallback
        dispatch_queue_path=str(tmp_path / "dispatch-queue.json"),
        dispatch_pause_enabled=True,
        dispatch_priority_enabled=True,
        grafana_webhook_enabled=True,
    )
    # dispatch_needs_info_enabled intentionally omitted — its alias breaks the constructor


@pytest_asyncio.fixture
async def research_app(research_settings):
    """FastAPI app for research-scope tests."""
    application = create_app(settings=research_settings)
    yield application


@pytest_asyncio.fixture
async def research_client(research_app):
    """Authenticated httpx AsyncClient for research-scope route tests."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=research_app), base_url="http://test"
    ) as c:
        c.headers["X-API-Key"] = TEST_API_KEY
        yield c


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _inject_dispatch(app, tmp_path):
    """Inject a JSON-backed DispatchFallbackService and return it."""
    svc = DispatchFallbackService(str(tmp_path / "dispatch-queue.json"))
    inject_mock_services(app, dispatch_db_service=svc)
    return svc


def _enqueue_payload(scope: str = "small", story_id: str = "STORY-539") -> dict:
    return {
        "story_id": story_id,
        "repo": "tech-dev-agents",
        "scope": scope,
        "prompt": "Research question: how does the Loki quota aggregator work?",
        "enqueued_by": "mark",
    }


def _make_mock_db_svc(scope: str = "research", story_id: str = "STORY-539") -> AsyncMock:
    """Build an AsyncMock DispatchDBService with a pre-claimed research story."""
    mock = AsyncMock()
    base_row = {
        "story_id": story_id,
        "repo": "tech-dev-agents",
        "scope": scope,
        "prompt": "Research question",
        "enqueued_at": "2026-04-23T00:00:00+00:00",
        "enqueued_by": "mark",
        "claimed_by": "devon",
        "claimed_at": "2026-04-23T00:01:00+00:00",
        "title": None,
        "priority": 50,
        "paused_at": None,
        "needs_info_path": None,
        "pr_number": None,
        "commit_sha": None,
        "completed_at": None,
        "cancelled_at": None,
        "failed_at": None,
    }
    mock.get.return_value = base_row
    mock.complete.return_value = {
        **base_row,
        "completed_at": "2026-04-23T00:10:00+00:00",
        "commit_sha": VALID_SHA,
        "pr_number": None,
    }
    return mock


def _make_mock_http_client(tree_paths: list[str]) -> AsyncMock:
    """Build an AsyncMock HTTP client whose .get() returns a GitHub-tree-style response.

    tree_paths: list of file paths to include in the simulated tree
                e.g. ["features/story-539-research-dispatch-scope/research.md"]
    """
    mock_http = AsyncMock()
    tree_resp = MagicMock()
    tree_resp.status_code = 200
    tree_resp.json.return_value = {
        "tree": [{"path": p, "type": "blob"} for p in tree_paths]
    }
    mock_http.get.return_value = tree_resp
    return mock_http


# ---------------------------------------------------------------------------
# Group A — Scope validation
# ---------------------------------------------------------------------------


class TestDispatchScopeValidation:
    """A-01 through A-03: POST /dispatch scope validation."""

    @pytest.mark.asyncio
    async def test_dispatch_accepts_scope_research(
        self, research_client, research_app, tmp_path
    ):
        """A-01: POST /dispatch with scope='research' → 201.

        RED: Currently returns 422 because DispatchRequest.scope has pattern
             r"^(small|medium|large)$" which does not include 'research'.

        Phase 8 fix: change pattern to r"^(small|medium|large|research)$"
        in tech_dev_agents/ops_console/models/responses.py (DispatchRequest).
        """
        _inject_dispatch(research_app, tmp_path)

        resp = await research_client.post(
            "/api/dispatch", json=_enqueue_payload(scope="research")
        )

        assert resp.status_code == 201, (
            f"POST /dispatch with scope='research' returned {resp.status_code}: {resp.text}.\n\n"
            "Currently FAILS with 422 — Pydantic validation rejects 'research' scope.\n"
            "Phase 8 must update tech_dev_agents/ops_console/models/responses.py:\n"
            '  scope: str = Field("small", pattern=r"^(small|medium|large|research)$")'
        )
        data = resp.json()
        # POST /dispatch returns DispatchItemResponse: {"item": {...}, "queue_depth": N}
        # scope is nested under "item", not at top level.
        # Test fix (Phase 8): Phase 7 incorrectly checked data["scope"] — corrected to data["item"]["scope"].
        assert data["item"]["scope"] == "research", (
            f"Response item.scope is {data.get('item', {}).get('scope')!r}, expected 'research'."
        )

    @pytest.mark.asyncio
    async def test_dispatch_rejects_unknown_scope(
        self, research_client, research_app, tmp_path
    ):
        """A-02: POST /dispatch with scope='tiny' → 422 (unchanged — regression guard).

        This test already PASSES and must continue to pass after Phase 8.
        Adding 'research' must not open the door to arbitrary scope values.
        """
        _inject_dispatch(research_app, tmp_path)

        resp = await research_client.post(
            "/api/dispatch", json=_enqueue_payload(scope="tiny")
        )

        assert resp.status_code == 422, (
            f"POST /dispatch with scope='tiny' should return 422 (unknown scope), "
            f"got {resp.status_code}."
        )

    @pytest.mark.asyncio
    async def test_queue_preserves_research_scope(
        self, research_client, research_app, tmp_path
    ):
        """A-03: GET /dispatch/queue returns a research story with scope='research'.

        RED: Cannot enqueue a research story until A-01 is fixed.
        After Phase 8: both enqueue and queue listing work for scope='research'.
        """
        _inject_dispatch(research_app, tmp_path)

        # Enqueue the story first — this will fail until A-01 fix lands
        enqueue_resp = await research_client.post(
            "/api/dispatch", json=_enqueue_payload(scope="research")
        )
        assert enqueue_resp.status_code == 201, (
            f"Cannot run A-03: enqueue returned {enqueue_resp.status_code}. "
            "Fix A-01 first (scope regex must accept 'research')."
        )

        queue_resp = await research_client.get("/api/dispatch/queue")
        assert queue_resp.status_code == 200
        data = queue_resp.json()

        pending_scopes = [item["scope"] for item in data.get("pending", [])]
        assert "research" in pending_scopes, (
            f"GET /dispatch/queue pending items do not include scope='research'.\n"
            f"Found scopes: {pending_scopes}"
        )


# ---------------------------------------------------------------------------
# Group B — Completion gate for research scope
# ---------------------------------------------------------------------------


class TestCompletionGateResearch:
    """B-01 through B-02: POST /dispatch/complete/{story_id} for scope=research.

    Both tests use mocked DB service and mocked GitHub HTTP client —
    no PostgreSQL required.
    """

    @pytest.mark.asyncio
    async def test_completion_gate_accepts_research_without_pr(
        self, research_client, research_app
    ):
        """B-01: Complete scope=research story with research.md present → 200.

        No PR number is required for research scope. The completion gate must
        check only that research.md exists in the story's features/ folder.

        RED: Currently fails because _SDLC_REQUIRED has no "research" key and
             falls back to "small" (requires seed.md). The tree has research.md
             but not seed.md → 422 "SDLC deliverables missing".

        Phase 8 fix: add "research": ["research.md"] to _SDLC_REQUIRED in
        tech_dev_agents/ops_console/routes/dispatch.py, and add a guard:
          if scope == "research": skip PR requirement (no pr_number needed)
        """
        story_id = "STORY-539"
        story_num = "539"

        mock_db = _make_mock_db_svc(scope="research", story_id=story_id)
        # Tree includes research.md but NOT seed.md — research scope never produces one
        mock_http = _make_mock_http_client([
            f"features/story-{story_num}-research-dispatch-scope/research.md",
        ])

        inject_mock_services(research_app, dispatch_db_service=mock_db, http_client=mock_http)
        research_app.state.settings.github_token = "ghp_test_token_research_539"

        with patch(
            "tech_dev_agents.ops_console.routes.dispatch._github_commit_exists",
            new=AsyncMock(return_value=True),
        ):
            resp = await research_client.post(
                f"/api/dispatch/complete/{story_id}",
                json={"commit_sha": VALID_SHA},
            )

        assert resp.status_code == 200, (
            f"Expected 200 for scope=research completion with research.md present, "
            f"got {resp.status_code}: {resp.text}.\n\n"
            "Currently FAILS: _SDLC_REQUIRED falls back to 'small' (requires seed.md), "
            "which is NOT present in a research story's tree → 422.\n\n"
            "Phase 8 fix: add to _SDLC_REQUIRED in routes/dispatch.py:\n"
            '  "research": ["research.md"],\n'
            "\nAlso add `if scope == \"research\":` gate that skips the PR requirement check."
        )
        data = resp.json()
        assert data["completed"] is True, (
            f"Response 'completed' field is {data.get('completed')!r}, expected True."
        )

    @pytest.mark.asyncio
    async def test_completion_gate_rejects_research_with_missing_research_md(
        self, research_client, research_app
    ):
        """B-02: Complete scope=research story with research.md absent → 422.

        The error response must specifically mention "research.md" so the agent
        knows exactly which deliverable is missing.

        RED: Currently FAILS for two reasons:
          1. _SDLC_REQUIRED fallback to "small" requires seed.md, not research.md
          2. The 422 body mentions "seed.md", not "research.md"
             → assertion `"research.md" in resp.text` fails

        Phase 8 fix: same as B-01 — adding "research": ["research.md"] to
        _SDLC_REQUIRED causes the missing-deliverable check to name research.md.
        """
        story_id = "STORY-539"
        story_num = "539"

        mock_db = _make_mock_db_svc(scope="research", story_id=story_id)
        # Tree is empty — no research.md
        mock_http = _make_mock_http_client([
            f"features/story-{story_num}-research-dispatch-scope/CLAUDE.md",  # unrelated file
        ])

        inject_mock_services(research_app, dispatch_db_service=mock_db, http_client=mock_http)
        research_app.state.settings.github_token = "ghp_test_token_research_539"

        with patch(
            "tech_dev_agents.ops_console.routes.dispatch._github_commit_exists",
            new=AsyncMock(return_value=True),
        ):
            resp = await research_client.post(
                f"/api/dispatch/complete/{story_id}",
                json={"commit_sha": VALID_SHA},
            )

        assert resp.status_code == 422, (
            f"Expected 422 when research.md is absent (scope=research), "
            f"got {resp.status_code}: {resp.text}."
        )
        assert "research.md" in resp.text, (
            f"422 response body should mention 'research.md' but got:\n{resp.text}\n\n"
            "Currently FAILS: without 'research' in _SDLC_REQUIRED, the error mentions "
            "'seed.md' (the small-scope fallback) instead of 'research.md'.\n"
            "Phase 8 fix: add 'research': ['research.md'] to _SDLC_REQUIRED — then "
            "the missing-deliverable message will correctly name research.md."
        )


# ---------------------------------------------------------------------------
# Group C — Seed is optional for research scope
# ---------------------------------------------------------------------------


class TestResearchSeedIsOptional:
    """C-01: run_sdlc_phases with scope=research starts at Phase 2, not Phase 1.

    The dispatch prompt IS the research question. No seed.md is produced or
    required before Phase 2 runs.

    RED: PHASE_MAP.get("research", PHASE_MAP["small"]) → PHASES_SMALL (phases 1,7,8)
         → Phase 1 runs first instead of Phase 2.
    """

    @pytest.mark.asyncio
    async def test_research_seed_is_optional(self, tmp_path):
        """C-01: scope=research → only Phase 2 runs; Phase 1 never called.

        Verifies that:
          - Phase 1 (Seed) is never invoked — seed.md is NOT required
          - Phase 2 (Research) runs as the sole phase
          - The phase runner starts at Phase 2, not Phase 1
        """
        mod = _get_phase_runner()

        # Workdir WITHOUT seed.md — a research story never has one pre-committed
        story_dir = tmp_path / "features" / "story-539-research-dispatch-scope"
        story_dir.mkdir(parents=True)
        (tmp_path / "CLAUDE.md").write_text("# Test repo\n")
        (tmp_path / ".git").mkdir()

        called_phases: list[int] = []

        def _fake_run_phase_sdk(**kwargs):
            phase_num = kwargs.get("phase_num", -1)
            called_phases.append(phase_num)
            # Write deliverable so the phase is considered complete
            deliverable_map = {
                1: "seed.md",
                2: "research.md",
                4: "analysis.md",
                6: "feature-spec.md",
                7: "test-design.md",
            }
            if phase_num in deliverable_map:
                out_file = story_dir / deliverable_map[phase_num]
                out_file.write_text(f"# Phase {phase_num} deliverable\n")
            return (0, "")

        with (
            patch.object(mod, "_run_phase_sdk", side_effect=_fake_run_phase_sdk),
            patch.object(mod, "_notify_teams"),
            patch.object(mod, "_ensure_branch"),

            patch("subprocess.run"),
        ):
            mod.run_sdlc_phases(
                story_id="STORY-539",
                repo="tech-dev-agents",
                scope="research",
                prompt=(
                    "How does the Loki quota aggregator reconcile with the SDK's own "
                    "rate-limit response body? Document the flow, surface inconsistencies, "
                    "recommend whether they should be merged into one source of truth."
                ),
                workdir=str(tmp_path),
                env=os.environ.copy(),
            )

        assert 1 not in called_phases, (
            f"Phase 1 (Seed) ran for scope=research — phases called: {called_phases}.\n\n"
            "For research scope, Phase 1 MUST never run. The dispatch prompt IS the "
            "research question — no Seed phase is needed.\n\n"
            "Currently FAILS: PHASE_MAP.get('research', PHASE_MAP['small']) returns "
            "PHASES_SMALL → starts with Phase 1.\n\n"
            "Phase 8 fix: add PHASES_RESEARCH = [(2, 'Research', 'research.md', "
            "'/phase-2 story_id={story_id} repo={repo} story_folder={story_folder}', 50)] "
            'and set PHASE_MAP["research"] = PHASES_RESEARCH.'
        )
        assert 2 in called_phases, (
            f"Phase 2 (Research) was never called — phases called: {called_phases}.\n"
            "research scope must run Phase 2 as its sole phase."
        )
        assert called_phases == [2], (
            f"Expected only Phase 2 for scope=research, got: {called_phases}.\n"
            "research scope must run exactly one phase: Phase 2 (Research)."
        )
