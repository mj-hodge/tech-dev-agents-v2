"""Phase 7 tests — Epic-Queue-v2 Story Q2: Atomic claim-next + Lease Token + Worker Version.

RED state: written before implementation exists. DB tests skip when PostgreSQL
is unreachable.  HTTP-level tests use FastAPI TestClient against the real router
once it exists; until then, import failures cause expected collection errors.

ACs covered:
  AC1: claim-next is atomic — 100-thread fuzz, 0 duplicates over 10k claims
  AC2: stale lease tokens return structured 409
  AC3: worker version below minimum → 426 with min_required; poller v2 exits 2
  AC4: claim-by-id MANAGER role works; 403 for AGENT
  AC6: eligibility predicate <50ms p95 on 200-row queue

RT1–RT8: Redispatch contract tests

DB: ops_console_test @ postgresql://ops_console:ops_console@localhost/ops_console_test
Migration 050 applied by the raw_conn fixture (inherited from Q1 test pattern).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest
import pytest_asyncio

# ---------------------------------------------------------------------------
# Paths / DB config
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent
MIGRATION_050 = REPO_ROOT / "scripts" / "migrations" / "050_dispatch_v2_schema.sql"
MIGRATION_051 = REPO_ROOT / "scripts" / "migrations" / "051_dispatch_v2_dependencies.sql"
MIGRATION_056 = REPO_ROOT / "scripts" / "migrations" / "056_dispatch_v2_correlation_index_fix.sql"

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://ops_console:ops_console@localhost/ops_console_test",
)

# ---------------------------------------------------------------------------
# DB availability check
# ---------------------------------------------------------------------------


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
# Fixtures — raw DB connection with migration 050 applied
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def raw_conn():
    """Raw asyncpg connection with migration 050 applied and v2 tables truncated."""
    conn = await asyncpg.connect(TEST_DATABASE_URL)

    migration_sql = MIGRATION_050.read_text()
    clean_sql = re.sub(r"--[^\n]*", "", migration_sql)
    await conn.execute(clean_sql)

    migration_051_sql = MIGRATION_051.read_text()
    clean_051 = re.sub(r"--[^\n]*", "", migration_051_sql)
    await conn.execute(clean_051)

    migration_056_sql = MIGRATION_056.read_text()
    clean_056 = re.sub(r"--[^\n]*", "", migration_056_sql)
    await conn.execute(clean_056)

    # Truncate all v2 tables (CASCADE handles FK ordering)
    await conn.execute(
        "TRUNCATE dispatch_v2_quarantine, dispatch_dependencies, "
        "dispatch_state_current, dispatch_leases, dispatch_v2_events, dispatch_jobs CASCADE"
    )

    yield conn

    await conn.execute(
        "TRUNCATE dispatch_v2_quarantine, dispatch_dependencies, "
        "dispatch_state_current, dispatch_leases, dispatch_v2_events, dispatch_jobs CASCADE"
    )
    await conn.close()


@pytest_asyncio.fixture
async def svc(raw_conn):
    """DispatchV2Service backed by raw_conn."""
    from tech_dev_agents.ops_console.services.dispatch_v2_service import DispatchV2Service
    return DispatchV2Service(raw_conn)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


async def _insert_job(
    conn,
    *,
    repo: str = "tech-dev-agents",
    story_id: str = "STORY-Q2",
    scope: str = "small",
    prompt: str = "test prompt",
    target_role: str = "developer",
    enqueued_by: str = "test",
    rework_of: str | None = None,
    correlation_key: str | None = None,
) -> str:
    row = await conn.fetchrow(
        """INSERT INTO dispatch_jobs
               (repo, story_id, scope, prompt, target_role, enqueued_by, rework_of, correlation_key)
           VALUES ($1, $2, $3, $4, $5, $6, $7::uuid, $8)
           RETURNING job_id""",
        repo, story_id, scope, prompt, target_role, enqueued_by,
        rework_of, correlation_key,
    )
    return str(row["job_id"])


async def _insert_event(
    conn,
    job_id: str,
    event_type: str,
    event_data: dict | None = None,
    actor: str = "test",
) -> int:
    data = event_data or {}
    row = await conn.fetchrow(
        """INSERT INTO dispatch_v2_events (job_id, event_type, event_data, actor)
           VALUES ($1::uuid, $2, $3::jsonb, $4)
           RETURNING event_id""",
        job_id, event_type, json.dumps(data), actor,
    )
    return row["event_id"]


async def _enqueue_job_in_work_queue(conn, **kwargs) -> str:
    """Insert a job and emit an enqueued event so it lands in work_queue."""
    job_id = await _insert_job(conn, **kwargs)
    await _insert_event(conn, job_id, "enqueued")
    return job_id


# ---------------------------------------------------------------------------
# FastAPI TestClient helper
# ---------------------------------------------------------------------------


def _make_test_app(
    *,
    min_worker_version: str = "2.0",
    agent_role_api_key: str = "agent-key-test",
    manager_role_api_key: str = "manager-key-test",
    db_pool=None,
    mock_svc=None,
):
    """Create a minimal FastAPI test app with dispatch_v2 router mounted.

    Returns the app and the TestClient.  Requires the router module to exist.
    If mock_svc is provided, it is injected via app.dependency_overrides for
    get_v2_service.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from tech_dev_agents.ops_console.routes.dispatch_v2 import get_v2_service, router as v2_router

    app = FastAPI()

    # Minimal settings mock
    settings = MagicMock()
    settings.ops_console_api_key = "legacy-key-test"
    settings.agent_role_api_key = agent_role_api_key
    settings.manager_role_api_key = manager_role_api_key
    settings.admin_role_api_key = "admin-key-test"
    settings.entra_tenant_id = ""
    settings.entra_client_id = ""

    app.state.settings = settings
    app.state.db_pool = db_pool or MagicMock()
    app.state.min_worker_version = min_worker_version

    app.include_router(v2_router, prefix="/api/dispatch/v2")

    if mock_svc is not None:
        async def _override_svc():
            return mock_svc
        app.dependency_overrides[get_v2_service] = _override_svc

    return app, TestClient(app, raise_server_exceptions=False)


AGENT_HEADERS = {
    "X-API-Key": "agent-key-test",
    "X-Worker-Version": "2.0",
    "X-Agent-Name": "dan",
    "X-Agent-Role": "developer",
}

MANAGER_HEADERS = {
    "X-API-Key": "manager-key-test",
    "X-Worker-Version": "2.0",
    "X-Agent-Name": "morris",
    "X-Agent-Role": "manager",
}


# ---------------------------------------------------------------------------
# AC1: Atomic claim-next — 100-thread fuzz, 0 duplicates over 10k claims
# ---------------------------------------------------------------------------


@_pg_skip
class TestAtomicClaimNextFuzz:
    """AC1: Prove claim-next is atomic under concurrent load."""

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_service_atomic_claim_10k_no_duplicates(self, raw_conn):
        """Seed 10k jobs, run 100 concurrent tasks claiming them — zero duplicates."""
        from tech_dev_agents.ops_console.services.dispatch_v2_service import DispatchV2Service

        N_JOBS = 10_000
        N_WORKERS = 100

        # Seed jobs via a pool (raw_conn is a single connection — need a real pool)
        pool = await asyncpg.create_pool(TEST_DATABASE_URL, min_size=5, max_size=N_WORKERS + 5)
        try:
            # Apply migrations and truncate
            migration_sql = MIGRATION_050.read_text()
            clean_sql = re.sub(r"--[^\n]*", "", migration_sql)
            migration_051_sql = MIGRATION_051.read_text()
            clean_051 = re.sub(r"--[^\n]*", "", migration_051_sql)
            async with pool.acquire() as conn:
                await conn.execute(clean_sql)
                await conn.execute(clean_051)
                await conn.execute(
                    "TRUNCATE dispatch_v2_quarantine, dispatch_dependencies, "
                    "dispatch_state_current, dispatch_leases, "
                    "dispatch_v2_events, dispatch_jobs CASCADE"
                )

            # Seed N_JOBS jobs
            async with pool.acquire() as conn:
                for i in range(N_JOBS):
                    job_id = await _insert_job(
                        conn,
                        story_id=f"STORY-FUZZ-{i:05d}",
                        scope="small",
                    )
                    await _insert_event(conn, job_id, "enqueued")

            # Concurrent claim workers
            claimed_ids: list[str] = []
            claim_lock = asyncio.Lock()

            svc = DispatchV2Service(pool)

            async def _worker(worker_id: int) -> None:
                while True:
                    result = await svc.atomic_claim_next(
                        agent_name=f"worker-{worker_id}",
                        agent_role="developer",
                        preferred_scope="small",
                    )
                    if result is None:
                        break
                    async with claim_lock:
                        claimed_ids.append(result["job_id"])

            await asyncio.gather(*[_worker(i) for i in range(N_WORKERS)])

            # Assert zero duplicates
            assert len(claimed_ids) == len(set(claimed_ids)), (
                f"Duplicate job_ids detected: "
                f"{len(claimed_ids) - len(set(claimed_ids))} duplicates "
                f"in {len(claimed_ids)} claims"
            )
            # All jobs should have been claimed
            assert len(claimed_ids) == N_JOBS, (
                f"Expected {N_JOBS} claims, got {len(claimed_ids)}"
            )
        finally:
            async with pool.acquire() as conn:
                await conn.execute(
                    "TRUNCATE dispatch_v2_quarantine, dispatch_dependencies, "
                    "dispatch_state_current, dispatch_leases, "
                    "dispatch_v2_events, dispatch_jobs CASCADE"
                )
            await pool.close()

    def test_service_has_atomic_claim_next_method(self):
        """Service layer must expose atomic_claim_next."""
        from tech_dev_agents.ops_console.services.dispatch_v2_service import DispatchV2Service
        assert hasattr(DispatchV2Service, "atomic_claim_next"), (
            "DispatchV2Service must have atomic_claim_next method"
        )


# ---------------------------------------------------------------------------
# AC2: Stale lease token returns structured 409
# ---------------------------------------------------------------------------


class TestStaleLeastToken:
    """AC2: Operations with wrong/expired lease token return 409 with structured body."""

    def test_heartbeat_with_wrong_token_returns_409(self):
        """POST /heartbeat with wrong lease_token → 409."""
        from tech_dev_agents.ops_console.services.dispatch_v2_service import StaleLeaseError
        mock_svc = AsyncMock()
        mock_svc.heartbeat.side_effect = StaleLeaseError("token mismatch")
        app, client = _make_test_app(mock_svc=mock_svc)

        resp = client.post(
            "/api/dispatch/v2/heartbeat",
            json={"job_id": str(uuid.uuid4()), "lease_token": str(uuid.uuid4())},
            headers=AGENT_HEADERS,
        )

        assert resp.status_code == 409, f"Expected 409, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert "detail" in body

    def test_release_with_wrong_token_returns_409(self):
        """POST /release with wrong lease_token → 409."""
        from tech_dev_agents.ops_console.services.dispatch_v2_service import StaleLeaseError
        mock_svc = AsyncMock()
        mock_svc.release_lease.side_effect = StaleLeaseError("token mismatch")
        app, client = _make_test_app(mock_svc=mock_svc)

        resp = client.post(
            "/api/dispatch/v2/release",
            json={
                "job_id": str(uuid.uuid4()),
                "lease_token": str(uuid.uuid4()),
                "reason": "test",
            },
            headers=AGENT_HEADERS,
        )

        assert resp.status_code == 409, f"Expected 409, got {resp.status_code}: {resp.text}"

    def test_transition_with_wrong_token_returns_409(self):
        """POST /transition with wrong lease_token → 409."""
        from tech_dev_agents.ops_console.services.dispatch_v2_service import StaleLeaseError
        mock_svc = AsyncMock()
        mock_svc.transition.side_effect = StaleLeaseError("token mismatch")
        app, client = _make_test_app(mock_svc=mock_svc)

        resp = client.post(
            "/api/dispatch/v2/transition",
            json={
                "job_id": str(uuid.uuid4()),
                "lease_token": str(uuid.uuid4()),
                "event_type": "submitted",
                "event_data": {},
            },
            headers=AGENT_HEADERS,
        )

        assert resp.status_code == 409, f"Expected 409, got {resp.status_code}: {resp.text}"

    def test_correct_token_heartbeat_returns_200(self):
        """POST /heartbeat with correct lease_token → 200 with expires_at."""
        mock_svc = AsyncMock()
        mock_svc.heartbeat.return_value = {"expires_at": "2099-01-01T00:00:00+00:00"}
        app, client = _make_test_app(mock_svc=mock_svc)

        resp = client.post(
            "/api/dispatch/v2/heartbeat",
            json={"job_id": str(uuid.uuid4()), "lease_token": str(uuid.uuid4())},
            headers=AGENT_HEADERS,
        )

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        assert "expires_at" in resp.json()

    def test_stale_lease_error_importable(self):
        """StaleLeaseError must be importable from service module."""
        from tech_dev_agents.ops_console.services.dispatch_v2_service import StaleLeaseError
        assert StaleLeaseError is not None


# ---------------------------------------------------------------------------
# AC3: Worker version middleware
# ---------------------------------------------------------------------------


class TestWorkerVersionMiddleware:
    """AC3: X-Worker-Version below MIN_WORKER_VERSION returns 426."""

    def test_below_min_version_returns_426_with_min_required(self):
        """X-Worker-Version: 1.5 with MIN_WORKER_VERSION=2.0 → 426."""
        app, client = _make_test_app(min_worker_version="2.0")

        resp = client.post(
            "/api/dispatch/v2/claim-next",
            json={"capabilities": [], "preferred_scope": "small"},
            headers={
                "X-API-Key": "agent-key-test",
                "X-Worker-Version": "1.5",
                "X-Agent-Name": "dan",
                "X-Agent-Role": "developer",
            },
        )
        assert resp.status_code == 426, f"Expected 426, got {resp.status_code}: {resp.text}"
        body = resp.json()
        # FastAPI wraps HTTPException detail under "detail" key
        detail = body.get("detail", body)
        assert "min_required" in detail, f"Response must contain min_required: {body}"
        assert detail["min_required"] == "2.0"
        assert "got" in detail
        assert detail["got"] == "1.5"

    def test_exact_min_version_passes(self):
        """X-Worker-Version: 2.0 with MIN_WORKER_VERSION=2.0 → not 426."""
        mock_svc = AsyncMock()
        mock_svc.atomic_claim_next.return_value = None  # 204
        app, client = _make_test_app(min_worker_version="2.0", mock_svc=mock_svc)

        resp = client.post(
            "/api/dispatch/v2/claim-next",
            json={"capabilities": [], "preferred_scope": "small"},
            headers={
                "X-API-Key": "agent-key-test",
                "X-Worker-Version": "2.0",
                "X-Agent-Name": "dan",
                "X-Agent-Role": "developer",
            },
        )
        assert resp.status_code != 426, f"Min version should pass, got {resp.status_code}"

    def test_above_min_version_passes(self):
        """X-Worker-Version: 3.1 with MIN_WORKER_VERSION=2.0 → not 426."""
        mock_svc = AsyncMock()
        mock_svc.atomic_claim_next.return_value = None
        app, client = _make_test_app(min_worker_version="2.0", mock_svc=mock_svc)

        resp = client.post(
            "/api/dispatch/v2/claim-next",
            json={"capabilities": [], "preferred_scope": "small"},
            headers={
                "X-API-Key": "agent-key-test",
                "X-Worker-Version": "3.1",
                "X-Agent-Name": "dan",
                "X-Agent-Role": "developer",
            },
        )
        assert resp.status_code != 426, f"Above-min version should pass, got {resp.status_code}"

    def test_missing_version_header_returns_426(self):
        """Missing X-Worker-Version header on v2 endpoint → 426."""
        app, client = _make_test_app(min_worker_version="2.0")

        resp = client.post(
            "/api/dispatch/v2/claim-next",
            json={"capabilities": [], "preferred_scope": "small"},
            headers={
                "X-API-Key": "agent-key-test",
                "X-Agent-Name": "dan",
                "X-Agent-Role": "developer",
                # No X-Worker-Version
            },
        )
        assert resp.status_code == 426, f"Expected 426 for missing version, got {resp.status_code}"

    def test_poller_v2_exits_2_on_426(self):
        """dispatch_poller_v2 handles 426 response by exiting with code 2."""
        import importlib.util as ilu

        poller_path = REPO_ROOT / "deployment" / "hermes" / "dispatch_poller_v2.py"
        assert poller_path.exists(), (
            "dispatch_poller_v2.py must exist at deployment/hermes/dispatch_poller_v2.py"
        )
        spec = ilu.spec_from_file_location("dispatch_poller_v2", poller_path)
        mod = ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)

        with pytest.raises(SystemExit) as exc_info:
            mod.handle_426_response(min_required="2.0", got="1.5")
        assert exc_info.value.code == 2, f"Expected exit code 2, got {exc_info.value.code}"


# ---------------------------------------------------------------------------
# AC4: claim-by-id role gating
# ---------------------------------------------------------------------------


class TestClaimByIdRoleGating:
    """AC4: claim-by-id works for MANAGER, 403 for AGENT."""

    def test_manager_role_can_claim_by_id(self):
        """MANAGER role → 200 from claim-by-id."""
        mock_svc = AsyncMock()
        mock_svc.claim_by_id.return_value = {
            "job_id": str(uuid.uuid4()),
            "lease_token": str(uuid.uuid4()),
            "expires_at": "2099-01-01T00:00:00+00:00",
        }
        app, client = _make_test_app(mock_svc=mock_svc)

        resp = client.post(
            "/api/dispatch/v2/claim-by-id",
            json={
                "job_id": str(uuid.uuid4()),
                "agent_name": "morris",
                "reason": "manual redispatch",
            },
            headers=MANAGER_HEADERS,
        )
        assert resp.status_code == 200, f"Expected 200 for MANAGER, got {resp.status_code}: {resp.text}"

    def test_agent_role_claim_by_id_returns_403(self):
        """AGENT role → 403 from claim-by-id (insufficient role)."""
        app, client = _make_test_app()

        resp = client.post(
            "/api/dispatch/v2/claim-by-id",
            json={
                "job_id": str(uuid.uuid4()),
                "agent_name": "dan",
                "reason": "test",
            },
            headers=AGENT_HEADERS,
        )
        assert resp.status_code == 403, f"Expected 403 for AGENT role, got {resp.status_code}: {resp.text}"

    def test_unauthenticated_claim_by_id_returns_401(self):
        """No auth → 401 from claim-by-id."""
        app, client = _make_test_app()

        resp = client.post(
            "/api/dispatch/v2/claim-by-id",
            json={
                "job_id": str(uuid.uuid4()),
                "agent_name": "dan",
                "reason": "test",
            },
        )
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}"


# ---------------------------------------------------------------------------
# AC5: v2 endpoint surface — no v1 path patterns
# ---------------------------------------------------------------------------


class TestV2EndpointSurface:
    """AC5: v2 router does not expose v1 endpoint patterns."""

    def test_no_v1_next_endpoint(self):
        """GET /api/dispatch/v2/next must not exist."""
        app, client = _make_test_app()
        resp = client.get("/api/dispatch/v2/next", headers=AGENT_HEADERS)
        assert resp.status_code == 404, f"Expected 404 for /next, got {resp.status_code}"

    def test_no_v1_claim_by_id_path_pattern(self):
        """POST /api/dispatch/v2/claim/{id} (v1 pattern) must not exist."""
        app, client = _make_test_app()
        resp = client.post(
            f"/api/dispatch/v2/claim/{uuid.uuid4()}",
            headers=AGENT_HEADERS,
        )
        assert resp.status_code == 404, f"Expected 404 for /claim/{{id}}, got {resp.status_code}"

    def test_no_v1_release_by_id_path_pattern(self):
        """POST /api/dispatch/v2/release/{id} (v1 pattern) must not exist."""
        app, client = _make_test_app()
        resp = client.post(
            f"/api/dispatch/v2/release/{uuid.uuid4()}",
            headers=AGENT_HEADERS,
        )
        assert resp.status_code == 404, f"Expected 404 for /release/{{id}}, got {resp.status_code}"

    def test_claim_next_endpoint_exists(self):
        """POST /api/dispatch/v2/claim-next must be routed (not 404/405)."""
        app, client = _make_test_app()
        # Missing version header → 426, not 404
        resp = client.post(
            "/api/dispatch/v2/claim-next",
            json={"capabilities": [], "preferred_scope": "small"},
            headers={"X-API-Key": "agent-key-test"},
        )
        # 426 means the route exists but version gate fired — that's fine
        assert resp.status_code != 404, f"claim-next route must exist, got 404"

    def test_heartbeat_endpoint_exists(self):
        """POST /api/dispatch/v2/heartbeat must exist."""
        app, client = _make_test_app()
        resp = client.post(
            "/api/dispatch/v2/heartbeat",
            json={"job_id": str(uuid.uuid4()), "lease_token": str(uuid.uuid4())},
            headers=AGENT_HEADERS,
        )
        assert resp.status_code != 404, "heartbeat route must exist"

    def test_release_endpoint_exists(self):
        """POST /api/dispatch/v2/release must exist."""
        app, client = _make_test_app()
        resp = client.post(
            "/api/dispatch/v2/release",
            json={"job_id": str(uuid.uuid4()), "lease_token": str(uuid.uuid4()), "reason": "x"},
            headers=AGENT_HEADERS,
        )
        assert resp.status_code != 404, "release route must exist"

    def test_queue_endpoint_exists(self):
        """GET /api/dispatch/v2/queue must exist."""
        app, client = _make_test_app()
        resp = client.get("/api/dispatch/v2/queue", headers=AGENT_HEADERS)
        assert resp.status_code != 404, "queue route must exist"

    def test_lineage_endpoint_exists(self):
        """GET /api/dispatch/v2/lineage/{job_id} must exist."""
        app, client = _make_test_app()
        resp = client.get(
            f"/api/dispatch/v2/lineage/{uuid.uuid4()}",
            headers=AGENT_HEADERS,
        )
        assert resp.status_code != 404, "lineage route must exist"


# ---------------------------------------------------------------------------
# AC6: Eligibility predicate <50ms p95 on 200-row queue
# ---------------------------------------------------------------------------


@_pg_skip
class TestEligibilityPerformance:
    """AC6: claim-next eligibility on 200-row queue runs in <50ms p95."""

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_eligibility_p95_under_50ms_on_200_row_queue(self, raw_conn):
        """Seed 200 jobs, run 100 claim-next calls, measure p95 latency."""
        from tech_dev_agents.ops_console.services.dispatch_v2_service import DispatchV2Service

        # Seed 200 jobs in work_queue
        pool = await asyncpg.create_pool(TEST_DATABASE_URL, min_size=2, max_size=5)
        try:
            migration_sql = MIGRATION_050.read_text()
            clean_sql = re.sub(r"--[^\n]*", "", migration_sql)
            migration_051_sql = MIGRATION_051.read_text()
            clean_051 = re.sub(r"--[^\n]*", "", migration_051_sql)
            async with pool.acquire() as conn:
                await conn.execute(clean_sql)
                await conn.execute(clean_051)
                await conn.execute(
                    "TRUNCATE dispatch_v2_quarantine, dispatch_dependencies, "
                    "dispatch_state_current, dispatch_leases, "
                    "dispatch_v2_events, dispatch_jobs CASCADE"
                )
                for i in range(200):
                    job_id = await _insert_job(
                        conn, story_id=f"STORY-PERF-{i:03d}", scope="small"
                    )
                    await _insert_event(conn, job_id, "enqueued")

            svc = DispatchV2Service(pool)

            # Measure 100 claim calls (they will consume jobs, then return None)
            latencies_ms = []
            for _ in range(100):
                t0 = time.perf_counter()
                await svc.atomic_claim_next(
                    agent_name="perf-worker",
                    agent_role="developer",
                    preferred_scope="small",
                )
                t1 = time.perf_counter()
                latencies_ms.append((t1 - t0) * 1000)

            latencies_ms.sort()
            p95 = latencies_ms[int(len(latencies_ms) * 0.95)]
            assert p95 < 50.0, (
                f"p95 eligibility latency {p95:.1f}ms exceeds 50ms budget"
            )
        finally:
            async with pool.acquire() as conn:
                await conn.execute(
                    "TRUNCATE dispatch_v2_quarantine, dispatch_dependencies, "
                    "dispatch_state_current, dispatch_leases, "
                    "dispatch_v2_events, dispatch_jobs CASCADE"
                )
            await pool.close()


# ---------------------------------------------------------------------------
# RT1: Redispatch idempotency
# ---------------------------------------------------------------------------


class TestRedispatchIdempotency:
    """RT1: Same idempotency_key 3x → exactly 1 job created."""

    def test_same_idempotency_key_3x_creates_1_job(self):
        """Calling /redispatch 3x with the same idempotency_key is idempotent."""
        parent_job_id = str(uuid.uuid4())
        idem_key = "idem-key-rt1"
        created_job_id = str(uuid.uuid4())

        call_count = 0

        async def _idempotent_redispatch(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"job_id": created_job_id, "idempotent": False, "correlation_key": "repo:tech-dev-agents|pr:42"}
            else:
                return {"job_id": created_job_id, "idempotent": True, "correlation_key": "repo:tech-dev-agents|pr:42"}

        mock_svc = AsyncMock()
        mock_svc.redispatch.side_effect = _idempotent_redispatch
        app, client = _make_test_app(mock_svc=mock_svc)

        body = {
            "parent_job_id": parent_job_id,
            "repo": "tech-dev-agents",
            "pr_number": 42,
            "prompt": "fix the thing",
            "expected_pr_head_sha": "abc123",
            "idempotency_key": idem_key,
        }

        results = []
        for _ in range(3):
            resp = client.post(
                "/api/dispatch/v2/redispatch",
                json=body,
                headers=MANAGER_HEADERS,
            )
            assert resp.status_code == 200, f"redispatch should return 200, got {resp.status_code}: {resp.text}"
            results.append(resp.json()["job_id"])

        # All 3 responses must return the same job_id
        assert len(set(results)) == 1, f"Expected 1 unique job_id, got {set(results)}"
        assert results[0] == created_job_id

    def test_service_has_redispatch_method(self):
        from tech_dev_agents.ops_console.services.dispatch_v2_service import DispatchV2Service
        assert hasattr(DispatchV2Service, "redispatch"), (
            "DispatchV2Service must have redispatch method"
        )


# ---------------------------------------------------------------------------
# RT2: Concurrent redispatch same correlation key → 1 active child
# ---------------------------------------------------------------------------


class TestRedispatchCorrelationUniqueness:
    """RT2: Two concurrent redispatches for same repo+pr → 1 active child, second 409."""

    def test_second_concurrent_redispatch_returns_409(self):
        """When an active job already exists for the correlation key → 409."""
        from tech_dev_agents.ops_console.services.dispatch_v2_service import (
            CorrelationConflictError,
        )
        existing_job_id = str(uuid.uuid4())
        mock_svc = AsyncMock()
        mock_svc.redispatch.side_effect = CorrelationConflictError(
            f"Active job already exists: {existing_job_id}",
            existing_job_id=existing_job_id,
        )
        app, client = _make_test_app(mock_svc=mock_svc)

        resp = client.post(
            "/api/dispatch/v2/redispatch",
            json={
                "parent_job_id": str(uuid.uuid4()),
                "repo": "tech-dev-agents",
                "pr_number": 99,
                "prompt": "fix",
                "expected_pr_head_sha": "abc123",
                "idempotency_key": "unique-key-rt2",
            },
            headers=MANAGER_HEADERS,
        )
        assert resp.status_code == 409, f"Expected 409, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert "job_id" in body or "existing_job_id" in body or "detail" in body

    def test_correlation_conflict_error_importable(self):
        from tech_dev_agents.ops_console.services.dispatch_v2_service import CorrelationConflictError
        assert CorrelationConflictError is not None

    @_pg_skip
    @pytest.mark.asyncio
    async def test_terminal_prior_with_same_correlation_allows_new_redispatch(self, raw_conn, svc):
        """A prior terminal attempt must not permanently block same repo+PR redispatch."""
        parent = await _enqueue_job_in_work_queue(raw_conn, repo="tech-dev-agents", story_id="STORY-RT2-PARENT")
        await _insert_event(raw_conn, parent, "failed", {
            "failure_class": "git_rebase_failed",
            "failure_reason": "old attempt",
        })

        first = await svc.redispatch(
            parent_job_id=parent,
            repo="tech-dev-agents",
            pr_number=4242,
            prompt="first redispatch",
            expected_pr_head_sha="sha1",
            idempotency_key="rt2-first",
            title="first",
        )
        await _insert_event(raw_conn, first["job_id"], "failed", {
            "failure_class": "git_rebase_failed",
            "failure_reason": "first child terminal",
        })

        second = await svc.redispatch(
            parent_job_id=parent,
            repo="tech-dev-agents",
            pr_number=4242,
            prompt="second redispatch",
            expected_pr_head_sha="sha2",
            idempotency_key="rt2-second",
            title="second",
        )
        assert second["job_id"] != first["job_id"]
        assert second["idempotent"] is False

    @_pg_skip
    @pytest.mark.asyncio
    async def test_unknown_failed_parent_requires_triage_before_redispatch(self, raw_conn, svc):
        """Unknown parent failures must be triaged, not blindly redispatched."""
        parent = await _enqueue_job_in_work_queue(raw_conn, repo="tech-dev-agents", story_id="STORY-RT2-UNKNOWN")
        await _insert_event(raw_conn, parent, "failed", {
            "failure_class": "unknown",
            "failure_reason": "unclassified",
        })

        with pytest.raises(ValueError, match="failure_class='unknown'"):
            await svc.redispatch(
                parent_job_id=parent,
                repo="tech-dev-agents",
                pr_number=9876,
                prompt="retry unknown",
                expected_pr_head_sha="sha",
                idempotency_key="rt2-unknown-guard",
                title="unknown guard",
            )


# ---------------------------------------------------------------------------
# RT3: PR head SHA mismatch → 422 + attention event
# ---------------------------------------------------------------------------


class TestRedispatchHeadShaMismatch:
    """RT3: PR head SHA mismatch → 422 and emits attention event."""

    def test_head_sha_mismatch_returns_422(self):
        """When current PR HEAD != expected_pr_head_sha → 422."""
        from tech_dev_agents.ops_console.services.dispatch_v2_service import (
            HeadShaMismatchError,
        )
        mock_svc = AsyncMock()
        mock_svc.redispatch.side_effect = HeadShaMismatchError(
            "PR head drift: expected abc123, got def456"
        )
        app, client = _make_test_app(mock_svc=mock_svc)

        resp = client.post(
            "/api/dispatch/v2/redispatch",
            json={
                "parent_job_id": str(uuid.uuid4()),
                "repo": "tech-dev-agents",
                "pr_number": 42,
                "prompt": "fix",
                "expected_pr_head_sha": "abc123",
                "idempotency_key": "key-rt3",
            },
            headers=MANAGER_HEADERS,
        )
        assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"

    def test_head_sha_mismatch_error_importable(self):
        from tech_dev_agents.ops_console.services.dispatch_v2_service import HeadShaMismatchError
        assert HeadShaMismatchError is not None


# ---------------------------------------------------------------------------
# RT4: Child cancel/complete does not mutate parent terminal state
# ---------------------------------------------------------------------------


@_pg_skip
class TestRedispatchParentImmutability:
    """RT4: Child lifecycle events do not change parent terminal state."""

    @pytest.mark.asyncio
    async def test_child_complete_does_not_change_parent_terminal_state(self, raw_conn, svc):
        """Completing a child job leaves the parent's terminal state untouched."""
        # Create parent job that is completed (terminal)
        parent_id = await _insert_job(raw_conn, story_id="STORY-RT4-PARENT")
        await _insert_event(raw_conn, parent_id, "enqueued")
        await _insert_event(raw_conn, parent_id, "leased", {
            "agent": "dan", "lease_token": str(uuid.uuid4()), "expires_at": "2099-01-01T00:00:00Z"
        })
        await _insert_event(raw_conn, parent_id, "submitted")
        await _insert_event(raw_conn, parent_id, "accepted")

        # Verify parent is terminal/completed
        parent_state_before = await raw_conn.fetchrow(
            "SELECT state FROM dispatch_state_current WHERE job_id = $1::uuid", parent_id
        )
        assert parent_state_before["state"] == "completed"

        # Create child job (rework_of parent)
        child_id = await _insert_job(
            raw_conn,
            story_id="STORY-RT4-CHILD",
            rework_of=parent_id,
        )
        await _insert_event(raw_conn, child_id, "enqueued")
        await _insert_event(raw_conn, child_id, "leased", {
            "agent": "derrick", "lease_token": str(uuid.uuid4()), "expires_at": "2099-01-01T00:00:00Z"
        })
        await _insert_event(raw_conn, child_id, "submitted")
        await _insert_event(raw_conn, child_id, "accepted")

        # Parent state must still be completed, not modified
        parent_state_after = await raw_conn.fetchrow(
            "SELECT state FROM dispatch_state_current WHERE job_id = $1::uuid", parent_id
        )
        assert parent_state_after["state"] == "completed", (
            f"Parent state should remain 'completed', got '{parent_state_after['state']}'"
        )

    @pytest.mark.asyncio
    async def test_child_cancel_does_not_change_parent_terminal_state(self, raw_conn, svc):
        """Cancelling a child job leaves the parent's terminal state untouched."""
        parent_id = await _insert_job(raw_conn, story_id="STORY-RT4-CANCEL-PARENT")
        await _insert_event(raw_conn, parent_id, "enqueued")
        await _insert_event(raw_conn, parent_id, "leased", {
            "agent": "dan", "lease_token": str(uuid.uuid4()), "expires_at": "2099-01-01T00:00:00Z"
        })
        await _insert_event(raw_conn, parent_id, "submitted")
        await _insert_event(raw_conn, parent_id, "accepted")

        parent_state_before = await raw_conn.fetchrow(
            "SELECT state FROM dispatch_state_current WHERE job_id = $1::uuid", parent_id
        )
        assert parent_state_before["state"] == "completed"

        # Child is cancelled
        child_id = await _insert_job(
            raw_conn, story_id="STORY-RT4-CHILD-CANCEL", rework_of=parent_id
        )
        await _insert_event(raw_conn, child_id, "enqueued")
        await _insert_event(raw_conn, child_id, "cancelled")

        # Parent state unchanged
        parent_state_after = await raw_conn.fetchrow(
            "SELECT state FROM dispatch_state_current WHERE job_id = $1::uuid", parent_id
        )
        assert parent_state_after["state"] == "completed", (
            f"Parent must stay 'completed' after child cancel, got '{parent_state_after['state']}'"
        )


# ---------------------------------------------------------------------------
# RT5: claim-next not starved by duplicate correlation keys
# ---------------------------------------------------------------------------


@_pg_skip
class TestClaimNextNotStarvedByRedispatch:
    """RT5: Non-redispatch jobs are claimable even if duplicate correlation key jobs exist."""

    @pytest.mark.asyncio
    async def test_non_redispatch_jobs_claimable_alongside_redispatch(self, raw_conn):
        """Jobs without correlation_key are never starved by correlation-key enforcement."""
        from tech_dev_agents.ops_console.services.dispatch_v2_service import DispatchV2Service

        # Add a normal job (no correlation key)
        normal_job_id = await _enqueue_job_in_work_queue(
            raw_conn, story_id="STORY-RT5-NORMAL"
        )

        # Claim it — must succeed
        svc = DispatchV2Service(raw_conn)
        result = await svc.atomic_claim_next(
            agent_name="worker",
            agent_role="developer",
            preferred_scope="small",
        )
        assert result is not None, "Normal job must be claimable"
        assert str(result["job_id"]) == normal_job_id, (
            f"Expected {normal_job_id}, got {result['job_id']}"
        )


# ---------------------------------------------------------------------------
# RT6: Parent non-terminal state guard
# ---------------------------------------------------------------------------


class TestRedispatchParentStateGuard:
    """RT6: Redispatch with parent in non-terminal state → 409 unless force_cancel_parent=true (MANAGER)."""

    def test_redispatch_with_parent_in_progress_returns_409(self):
        """Parent still leased/active → 409."""
        from tech_dev_agents.ops_console.services.dispatch_v2_service import (
            ParentNotTerminalError,
        )
        mock_svc = AsyncMock()
        mock_svc.redispatch.side_effect = ParentNotTerminalError("Parent job still active")
        app, client = _make_test_app(mock_svc=mock_svc)

        resp = client.post(
            "/api/dispatch/v2/redispatch",
            json={
                "parent_job_id": str(uuid.uuid4()),
                "repo": "tech-dev-agents",
                "pr_number": 10,
                "prompt": "fix",
                "expected_pr_head_sha": "abc",
                "idempotency_key": "key-rt6a",
                "force_cancel_parent": False,
            },
            headers=MANAGER_HEADERS,
        )
        assert resp.status_code == 409, f"Expected 409, got {resp.status_code}: {resp.text}"

    def test_redispatch_force_cancel_parent_requires_manager_role(self):
        """force_cancel_parent=true from AGENT role → 403."""
        app, client = _make_test_app()

        resp = client.post(
            "/api/dispatch/v2/redispatch",
            json={
                "parent_job_id": str(uuid.uuid4()),
                "repo": "tech-dev-agents",
                "pr_number": 10,
                "prompt": "fix",
                "expected_pr_head_sha": "abc",
                "idempotency_key": "key-rt6b",
                "force_cancel_parent": True,
            },
            headers=AGENT_HEADERS,
        )
        assert resp.status_code == 403, (
            f"force_cancel_parent from AGENT should return 403, got {resp.status_code}: {resp.text}"
        )

    def test_redispatch_force_cancel_parent_as_manager_succeeds(self):
        """force_cancel_parent=true from MANAGER → 200 and parent is cancelled."""
        parent_job_id = str(uuid.uuid4())
        child_job_id = str(uuid.uuid4())
        mock_svc = AsyncMock()
        mock_svc.redispatch.return_value = {
            "job_id": child_job_id,
            "parent_cancelled": True,
            "correlation_key": "repo:tech-dev-agents|pr:10",
            "idempotent": False,
        }
        app, client = _make_test_app(mock_svc=mock_svc)

        resp = client.post(
            "/api/dispatch/v2/redispatch",
            json={
                "parent_job_id": parent_job_id,
                "repo": "tech-dev-agents",
                "pr_number": 10,
                "prompt": "fix",
                "expected_pr_head_sha": "abc",
                "idempotency_key": "key-rt6c",
                "force_cancel_parent": True,
            },
            headers=MANAGER_HEADERS,
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert body.get("parent_cancelled") is True

    def test_parent_not_terminal_error_importable(self):
        from tech_dev_agents.ops_console.services.dispatch_v2_service import ParentNotTerminalError
        assert ParentNotTerminalError is not None


# ---------------------------------------------------------------------------
# RT7: Cross-repo dependency resolution
# ---------------------------------------------------------------------------


@_pg_skip
class TestCrossRepoDependency:
    """RT7: Cross-repo deps are resolved when the dep completes in its own repo."""

    @pytest.mark.asyncio
    async def test_cross_repo_dep_unblocks_when_dep_completes(self, raw_conn):
        """A job depending on a job in another repo becomes eligible after dep completes."""
        from tech_dev_agents.ops_console.services.dispatch_v2_service import DispatchV2Service

        svc = DispatchV2Service(raw_conn)

        # Create dependency job in another repo
        dep_job_id = await _enqueue_job_in_work_queue(
            raw_conn, repo="other-repo", story_id="STORY-RT7-DEP"
        )

        # Create main job that depends on dep_job_id
        main_job_id = await _insert_job(
            raw_conn, repo="tech-dev-agents", story_id="STORY-RT7-MAIN"
        )
        await _insert_event(raw_conn, main_job_id, "enqueued")

        # Insert dependency record
        await raw_conn.execute(
            """INSERT INTO dispatch_dependencies (job_id, depends_on_repo, depends_on_story_id, depends_on_job_id)
               VALUES ($1::uuid, $2, $3, $4::uuid)""",
            main_job_id, "other-repo", "STORY-RT7-DEP", dep_job_id,
        )

        # Before dep completes, main_job should not be claimable
        result = await svc.atomic_claim_next(
            agent_name="worker",
            agent_role="developer",
            preferred_scope="small",
        )
        # dep_job can be claimed (it has no deps), but main_job cannot yet
        if result is not None:
            assert str(result["job_id"]) != main_job_id, (
                "main_job should not be claimable before dep completes"
            )

        # Complete dep job
        await _insert_event(raw_conn, dep_job_id, "leased", {
            "agent": "worker", "lease_token": str(uuid.uuid4()), "expires_at": "2099-01-01T00:00:00Z"
        })
        await _insert_event(raw_conn, dep_job_id, "submitted")
        await _insert_event(raw_conn, dep_job_id, "accepted")

        # Now main_job should be claimable
        result2 = await svc.atomic_claim_next(
            agent_name="worker2",
            agent_role="developer",
            preferred_scope="small",
        )
        assert result2 is not None, "main_job must be claimable after dep completes"
        assert str(result2["job_id"]) == main_job_id, (
            f"Expected main_job {main_job_id}, got {result2['job_id']}"
        )

    @pytest.mark.asyncio
    async def test_dispatch_dependencies_table_exists(self, raw_conn):
        """dispatch_dependencies table must exist (created by migration or Q2 migration)."""
        exists = await raw_conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name = 'dispatch_dependencies')"
        )
        assert exists, "dispatch_dependencies table must exist"


# ---------------------------------------------------------------------------
# RT8: Lineage endpoint
# ---------------------------------------------------------------------------


class TestLineageEndpoint:
    """RT8: Lineage endpoint returns ordered ancestor+descendant chain."""

    def test_lineage_returns_chain_with_attempt_counts(self):
        """GET /lineage/{job_id} returns chain with attempt numbers."""
        job_id = str(uuid.uuid4())
        parent_id = str(uuid.uuid4())
        grandparent_id = str(uuid.uuid4())

        mock_svc = AsyncMock()
        mock_svc.get_lineage.return_value = {
            "chain": [
                {
                    "job_id": grandparent_id,
                    "attempt": 1,
                    "state": "completed",
                    "parent_job_id": None,
                    "redispatched_at": "2026-05-01T10:00:00+00:00",
                },
                {
                    "job_id": parent_id,
                    "attempt": 2,
                    "state": "failed",
                    "parent_job_id": grandparent_id,
                    "redispatched_at": "2026-05-01T11:00:00+00:00",
                },
                {
                    "job_id": job_id,
                    "attempt": 3,
                    "state": "leased",
                    "parent_job_id": parent_id,
                    "redispatched_at": "2026-05-01T12:00:00+00:00",
                },
            ]
        }
        app, client = _make_test_app(mock_svc=mock_svc)

        resp = client.get(
            f"/api/dispatch/v2/lineage/{job_id}",
            headers=AGENT_HEADERS,
        )

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert "chain" in body, "Response must have 'chain' key"
        chain = body["chain"]
        assert len(chain) == 3
        # Chain must be ordered (grandparent first)
        assert chain[0]["job_id"] == grandparent_id
        assert chain[-1]["job_id"] == job_id
        # All entries must have attempt numbers
        for entry in chain:
            assert "attempt" in entry, f"Entry must have attempt: {entry}"

    def test_lineage_of_unknown_job_returns_404(self):
        """GET /lineage/{unknown_job_id} → 404."""
        from tech_dev_agents.ops_console.services.dispatch_v2_service import JobNotFoundError
        mock_svc = AsyncMock()
        mock_svc.get_lineage.side_effect = JobNotFoundError("not found")
        app, client = _make_test_app(mock_svc=mock_svc)

        resp = client.get(
            f"/api/dispatch/v2/lineage/{uuid.uuid4()}",
            headers=AGENT_HEADERS,
        )
        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"

    def test_service_has_get_lineage_method(self):
        from tech_dev_agents.ops_console.services.dispatch_v2_service import DispatchV2Service
        assert hasattr(DispatchV2Service, "get_lineage"), (
            "DispatchV2Service must have get_lineage method"
        )

    def test_lineage_events_endpoint_returns_event_payload(self):
        """GET /lineage/{job_id}/events exposes event_data failure details."""
        job_id = str(uuid.uuid4())
        mock_svc = AsyncMock()
        mock_svc.get_job_events.return_value = {
            "job_id": job_id,
            "events": [
                {
                    "event_id": 10,
                    "event_type": "failed",
                    "event_data": {
                        "failure_class": "unknown",
                        "failure_reason": "sample failure",
                        "error_message": "sample failure",
                    },
                    "actor": "dan",
                    "created_at": "2026-05-14T19:14:10+00:00",
                }
            ],
        }
        app, client = _make_test_app(mock_svc=mock_svc)

        resp = client.get(
            f"/api/dispatch/v2/lineage/{job_id}/events",
            headers=AGENT_HEADERS,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["job_id"] == job_id
        assert body["events"][0]["event_data"]["failure_reason"] == "sample failure"


# ---------------------------------------------------------------------------
# Route module structural tests — pure unit, no DB
# ---------------------------------------------------------------------------


class TestRouteModuleStructure:
    """Verify routes/dispatch_v2.py is importable and has expected structure."""

    def test_route_module_importable(self):
        """dispatch_v2 routes module must be importable."""
        from tech_dev_agents.ops_console.routes.dispatch_v2 import router
        assert router is not None

    def test_router_is_api_router(self):
        """The router must be a FastAPI APIRouter."""
        from fastapi import APIRouter
        from tech_dev_agents.ops_console.routes.dispatch_v2 import router
        assert isinstance(router, APIRouter)

    def test_get_v2_service_dependency_exists(self):
        """get_v2_service dependency function must exist."""
        from tech_dev_agents.ops_console.routes.dispatch_v2 import get_v2_service
        assert callable(get_v2_service)


# ---------------------------------------------------------------------------
# Poller v2 module structural tests
# ---------------------------------------------------------------------------


class TestPollerV2Structure:
    """dispatch_poller_v2.py must be importable with expected structure."""

    def _load_poller(self):
        import importlib.util as ilu
        poller_path = REPO_ROOT / "deployment" / "hermes" / "dispatch_poller_v2.py"
        assert poller_path.exists(), (
            f"dispatch_poller_v2.py not found at {poller_path}"
        )
        spec = ilu.spec_from_file_location("dispatch_poller_v2", poller_path)
        mod = ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_poller_v2_importable(self):
        """dispatch_poller_v2.py must be importable."""
        mod = self._load_poller()
        assert mod is not None

    def test_poller_v2_has_handle_426_response(self):
        """dispatch_poller_v2 must have handle_426_response function."""
        mod = self._load_poller()
        assert hasattr(mod, "handle_426_response"), (
            "dispatch_poller_v2 must have handle_426_response(min_required, got)"
        )

    def test_poller_v2_has_main_poll_loop(self):
        """dispatch_poller_v2 must have a poll_loop or run_poller entry point."""
        mod = self._load_poller()
        has_entry = hasattr(mod, "poll_loop") or hasattr(mod, "run_poller") or hasattr(mod, "main")
        assert has_entry, "dispatch_poller_v2 must have poll_loop, run_poller, or main"

    def test_poller_v2_gated_by_dispatch_protocol_env(self):
        """DISPATCH_PROTOCOL=v2 env var gates the v2 poller selection."""
        mod = self._load_poller()
        # Should have a constant or function that reads DISPATCH_PROTOCOL
        source = (REPO_ROOT / "deployment" / "hermes" / "dispatch_poller_v2.py").read_text()
        assert "DISPATCH_PROTOCOL" in source, (
            "dispatch_poller_v2.py must reference DISPATCH_PROTOCOL env var"
        )

    def test_poller_v2_no_try_except_type_error(self):
        """dispatch_poller_v2.py must have zero try/except TypeError shims."""
        source = (REPO_ROOT / "deployment" / "hermes" / "dispatch_poller_v2.py").read_text()
        # Match only code-level except TypeError (indented, at start of line)
        # Not comments mentioning the pattern
        import re as _re
        matches = _re.findall(r"^\s+except\s+TypeError", source, _re.MULTILINE)
        assert len(matches) == 0, (
            f"dispatch_poller_v2.py must not contain try/except TypeError shims, "
            f"found {len(matches)}: {matches}"
        )

    def test_dispatch_v2_routes_no_try_except_type_error(self):
        """dispatch_v2.py must have zero try/except TypeError shims."""
        route_path = (
            REPO_ROOT
            / "tech_dev_agents"
            / "ops_console"
            / "routes"
            / "dispatch_v2.py"
        )
        if not route_path.exists():
            pytest.skip("dispatch_v2.py not yet implemented")
        source = route_path.read_text()
        import re as _re
        # Match only code-level except TypeError (not in comments)
        matches = _re.findall(r"^\s+except\s+TypeError", source, _re.MULTILINE)
        assert len(matches) == 0, (
            f"dispatch_v2.py must not contain try/except TypeError shims, "
            f"found {len(matches)}"
        )


# ---------------------------------------------------------------------------
# Service layer additional method tests (pure unit)
# ---------------------------------------------------------------------------


class TestDispatchV2ServiceQ2Methods:
    """DispatchV2Service must expose all Q2 methods."""

    @pytest.mark.parametrize("method_name", [
        "atomic_claim_next",
        "heartbeat",
        "release_lease",
        "transition",
        "claim_by_id",
        "redispatch",
        "list_queue",
        "get_lineage",
    ])
    def test_service_has_q2_method(self, method_name):
        from tech_dev_agents.ops_console.services.dispatch_v2_service import DispatchV2Service
        assert hasattr(DispatchV2Service, method_name), (
            f"DispatchV2Service must have method '{method_name}'"
        )

    def test_new_error_classes_importable(self):
        from tech_dev_agents.ops_console.services.dispatch_v2_service import (
            StaleLeaseError,
            CorrelationConflictError,
            HeadShaMismatchError,
            ParentNotTerminalError,
        )
        for cls in (StaleLeaseError, CorrelationConflictError, HeadShaMismatchError, ParentNotTerminalError):
            assert cls is not None


# ---------------------------------------------------------------------------
# Submitted transition PR-link guard
# ---------------------------------------------------------------------------


@_pg_skip
class TestSubmittedTransitionRequiresPrLink:
    """submitted -> in_review must not happen without a PR link."""

    @pytest.mark.asyncio
    async def test_submitted_without_pr_number_raises(self, raw_conn, svc):
        from tech_dev_agents.ops_console.services.dispatch_v2_service import InvalidEventDataError

        job_id = await _insert_job(raw_conn, story_id="STORY-Q2-PR-GUARD-1")
        lease_token = str(uuid.uuid4())
        await _insert_event(raw_conn, job_id, "enqueued")
        await _insert_event(
            raw_conn,
            job_id,
            "leased",
            {"agent": "dan", "lease_token": lease_token, "expires_at": "2099-01-01T00:00:00Z"},
        )
        await raw_conn.execute(
            """INSERT INTO dispatch_leases (job_id, lease_token, agent_name, expires_at)
               VALUES ($1::uuid, $2::uuid, 'dan', now() + interval '15 minutes')""",
            job_id,
            lease_token,
        )

        with pytest.raises(InvalidEventDataError, match="requires PR linkage"):
            await svc.transition(
                job_id=job_id,
                lease_token=lease_token,
                event_type="submitted",
                event_data={},
            )

    @pytest.mark.asyncio
    async def test_submitted_with_event_pr_number_backfills_job(self, raw_conn, svc):
        job_id = await _insert_job(raw_conn, story_id="STORY-Q2-PR-GUARD-2")
        lease_token = str(uuid.uuid4())
        await _insert_event(raw_conn, job_id, "enqueued")
        await _insert_event(
            raw_conn,
            job_id,
            "leased",
            {"agent": "dan", "lease_token": lease_token, "expires_at": "2099-01-01T00:00:00Z"},
        )
        await raw_conn.execute(
            """INSERT INTO dispatch_leases (job_id, lease_token, agent_name, expires_at)
               VALUES ($1::uuid, $2::uuid, 'dan', now() + interval '15 minutes')""",
            job_id,
            lease_token,
        )

        event_id = await svc.transition(
            job_id=job_id,
            lease_token=lease_token,
            event_type="submitted",
            event_data={"pr_number": 1234},
        )
        assert isinstance(event_id, int)

        row = await raw_conn.fetchrow(
            "SELECT pr_number, correlation_key FROM dispatch_jobs WHERE job_id = $1::uuid",
            job_id,
        )
        assert row["pr_number"] == 1234
        assert row["correlation_key"] == "repo:tech-dev-agents|pr:1234"


# ---------------------------------------------------------------------------
# Heartbeat payload validation (poller v2 spec)
# ---------------------------------------------------------------------------


class TestHeartbeatPayload:
    """Heartbeat must carry git_head_sha, current_phase, phase_started_at, last_test_status."""

    def test_heartbeat_accepts_extended_payload(self):
        """POST /heartbeat with extended fields → 200."""
        mock_svc = AsyncMock()
        mock_svc.heartbeat.return_value = {"expires_at": "2099-01-01T00:00:00+00:00"}
        app, client = _make_test_app(mock_svc=mock_svc)

        resp = client.post(
            "/api/dispatch/v2/heartbeat",
            json={
                "job_id": str(uuid.uuid4()),
                "lease_token": str(uuid.uuid4()),
                "git_head_sha": "abc123def456",
                "current_phase": "8",
                "phase_started_at": "2026-05-02T10:00:00+00:00",
                "last_test_status": "GREEN",
            },
            headers=AGENT_HEADERS,
        )
        assert resp.status_code == 200, f"Extended heartbeat should return 200, got {resp.status_code}"
