"""FastAPI application factory for the Agent Operations Console."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from tech_dev_agents.agent_dashboard import AgentNotFoundError
from tech_dev_agents.ops_console.config import Settings
from tech_dev_agents.ops_console.routes import agents, alerts, context, curate, dispatch, fleet, foundry_cost, health, messages, presence, work_history
from tech_dev_agents.ops_console.routes import dispatch_v2
from tech_dev_agents.ops_console.routes import ops_fallback
from tech_dev_agents.ops_console.routes import knowledge as knowledge_routes
from tech_dev_agents.ops_console.routes import apprenticeship as apprenticeship_routes
from tech_dev_agents.ops_console.context_service import ContextService
from tech_dev_agents.ops_console.services.agent_service import AgentService
from tech_dev_agents.ops_console.services.alert_service import AlertService
from tech_dev_agents.ops_console.services.azure_cost_client import AzureCostClient
from tech_dev_agents.ops_console.services.cost_service import CostService
import asyncpg

from tech_dev_agents.ops_console.services.dispatch_db_service import DispatchDBService
from tech_dev_agents.ops_console.services.foundry_cost_service import FoundryCostService
from tech_dev_agents.ops_console.services.dispatch_service import DispatchFallbackService
from tech_dev_agents.ops_console.services.presence_service import PresenceService
from tech_dev_agents.ops_console.startup_check import run_startup_checks
from tech_dev_agents.ops_console.services.loki_client import LokiClient
from tech_dev_agents.ops_console.services.monday_service import MondayService
from tech_dev_agents.ops_console.clients.teams_client import TeamsClient
from tech_dev_agents.ops_console.graph_token_provider import create_graph_token_provider

logger = logging.getLogger(__name__)


async def _stale_claim_recovery_loop(service) -> None:
    """Recover stale dispatch claims every 60 seconds.

    STORY-027: Runs as a background asyncio task in the app lifespan.
    STORY-028: Updated for async DispatchDBService.
    Catches all exceptions except CancelledError to ensure the loop
    never crashes the application.
    """
    while True:
        try:
            recovered = await service.recover_stale_claims()
            if recovered:
                logger.info("Stale claim recovery: %s", recovered)
        except Exception:
            logger.error("Stale claim recovery failed", exc_info=True)
        await asyncio.sleep(60)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        settings: Optional Settings override (for testing).
    """
    if settings is None:
        from tech_dev_agents.ops_console.config import get_settings
        settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Startup/shutdown lifecycle."""
        logger.info("Starting ops console lifespan")
        # Create shared HTTP client
        http_client = httpx.AsyncClient(timeout=30.0)

        # Create Loki client
        loki_client = LokiClient(
            base_url=settings.loki_url,
            api_key=settings.loki_api_key,
            http_client=http_client,
        )

        # Create Azure Cost client (optional)
        azure_client: AzureCostClient | None = None
        if settings.azure_enabled:
            from azure.identity import DefaultAzureCredential

            credential_kwargs: dict[str, str] = {}
            if settings.azure_managed_identity_client_id:
                credential_kwargs["managed_identity_client_id"] = (
                    settings.azure_managed_identity_client_id
                )
            credential = DefaultAzureCredential(**credential_kwargs)
            logger.info(
                "Azure credential chain initialized (managed_identity_client_id=%s)",
                settings.azure_managed_identity_client_id or "auto",
            )

            agent_map = json.loads(settings.azure_agent_map)
            azure_client = AzureCostClient(
                credential=credential,
                subscription_id=settings.azure_subscription_id,
                http_client=http_client,
                agent_map=agent_map,
            )

        # Create services
        agent_service = AgentService(
            registry_path=settings.agent_registry_path,
            http_client=http_client,
            agent_api_key=settings.agent_api_key,
            health_cache_ttl=settings.health_cache_ttl,
            loki_client=loki_client,
        )

        cost_service = CostService(
            loki=loki_client,
            azure=azure_client,
            cost_cache_ttl=settings.cost_cache_ttl,
            fleet_cache_ttl=settings.fleet_cache_ttl,
        )

        # Monday.com clients (optional)
        monday_clients: dict = {}
        try:
            monday_config = json.loads(settings.monday_config)
            for agent_name, cfg in monday_config.items():
                from tech_dev_agents.monday_agent import AgentIdentity, AgentMondayClient
                identity = AgentIdentity(
                    agent_name=agent_name,
                    api_token=cfg["api_token"],
                    board_id=cfg["board_id"],
                )
                monday_clients[agent_name] = AgentMondayClient(identity)
        except (json.JSONDecodeError, KeyError) as exc:
            logger.warning("Failed to parse Monday.com config: %s", exc)

        monday_service = MondayService(
            clients=monday_clients,
            monday_cache_ttl=settings.monday_cache_ttl,
        )

        alert_service = AlertService(
            loki=loki_client,
            agent_service=agent_service,
            cost_service=cost_service,
        )

        # Teams client (Graph API) — STORY-228: v2 with MSAL auto-refresh
        token_provider = create_graph_token_provider(
            tenant_id=settings.graph_tenant_id,
            client_id=settings.graph_client_id,
            client_secret=settings.graph_client_secret,
        )

        # Acquire initial token from MSAL if provider is available
        initial_token = settings.graph_api_token
        if token_provider and not initial_token:
            try:
                initial_token = await token_provider()
            except Exception as exc:
                logger.warning("MSAL initial token acquisition failed: %s", exc)

        teams_client = TeamsClient(
            http_client=http_client,
            registry_path=settings.agent_registry_path,
            graph_base_url=settings.graph_api_base_url,
            access_token=initial_token,
            token_provider=token_provider,
        )

        context_service = ContextService(
            teams_client=teams_client,
            monday_service=monday_service,
            loki_client=loki_client,
        )

        # Dispatch queue service (STORY-028 DB or STORY-032 JSON fallback)
        db_pool = None
        dispatch_db_service = None
        if settings.database_url:
            try:
                db_pool = await asyncpg.create_pool(
                    settings.database_url,
                    min_size=2,
                    max_size=10,
                )
                dispatch_db_service = DispatchDBService(db_pool)
                # STORY-700: Bind dispatch_events service to the DB pool
                from tech_dev_agents.ops_console.services import dispatch_events
                dispatch_events.init(db_pool)
                logger.info("Connected to dispatch database: %s", settings.database_url.split("@")[-1])
                # Q6: Assert all protocol manifest migrations are applied before serving traffic.
                await run_startup_checks(db_pool)
            except Exception as exc:
                logger.warning(
                    "Failed to connect to dispatch database — falling back to JSON queue: %s",
                    exc,
                )
        if dispatch_db_service is None:
            dispatch_db_service = DispatchFallbackService(settings.dispatch_queue_path)
            logger.info("Using JSON file dispatch queue (no database configured)")

        # Presence service (STORY-426)
        presence_service = PresenceService(
            agent_service=agent_service,
            ssh_timeout=settings.presence_ssh_timeout,
            cache_ttl=settings.presence_cache_ttl,
            ssh_user=settings.presence_ssh_user,
            poller_service=settings.presence_poller_service,
            pause_flag_path=settings.presence_pause_flag_path,
        )

        # STORY-576: Foundry cost service (requires DB pool)
        foundry_cost_service = FoundryCostService(db_pool) if db_pool else None

        # Attach to app state
        app.state.settings = settings
        app.state.dispatch_db_service = dispatch_db_service
        app.state.db_pool = db_pool
        app.state.foundry_cost_service = foundry_cost_service
        app.state.http_client = http_client
        app.state.loki_client = loki_client
        app.state.azure_client = azure_client
        app.state.agent_service = agent_service
        app.state.cost_service = cost_service
        app.state.monday_service = monday_service
        app.state.alert_service = alert_service
        app.state.teams_client = teams_client
        app.state.context_service = context_service
        app.state.presence_service = presence_service
        app.state.started_at = datetime.now(timezone.utc)
        # STORY-495: Deploy commit SHA for health endpoint verification
        app.state.deploy_commit_sha = os.environ.get("DEPLOY_COMMIT_SHA")
        # STORY-508: Grafana alert log path (overridable in tests via inject_mock_services)
        app.state.alert_log_path = "/home/hermes/state/morris/alert-log.md"
        # STORY-508: Grafana service is None by default (injected in tests / configured in prod)
        if not hasattr(app.state, "grafana_service"):
            app.state.grafana_service = None

        # Start stale claim recovery background task (STORY-027, STORY-028)
        recovery_task = asyncio.create_task(
            _stale_claim_recovery_loop(dispatch_db_service),
            name="stale-claim-recovery",
        )

        # Q3 + Q7 + Q8 + Q9: Start background tasks for self-healing, knowledge
        # ingest, and apprenticeship rule proposal. All run as in-process
        # asyncio tasks (Option A per features/epic-queue-v2/decisions.md).
        from tech_dev_agents.ops_console.services.self_healing import (
            dispatch_dependency_watcher,
            dispatch_expired_lease_sweeper,
            dispatch_needs_info_ttl,
            dispatch_pr_link_backfill_sweeper,
            dispatch_pr_merge_sweeper,
            dispatch_stale_unlinked_in_review_sweeper,
            dispatch_stuck_agent_watcher,
        )
        from tech_dev_agents.ops_console.services.knowledge_service import (
            knowledge_ingest_worker,
        )
        from tech_dev_agents.ops_console.services.apprenticeship import (
            apprenticeship_proposer_loop,
        )
        dep_watcher_task = asyncio.create_task(
            dispatch_dependency_watcher(pool=db_pool),
            name="dispatch-dependency-watcher",
        )
        needs_info_ttl_task = asyncio.create_task(
            dispatch_needs_info_ttl(pool=db_pool),
            name="dispatch-needs-info-ttl",
        )
        # Q8: stuck-agent watcher (90s) — idle-no-progress, commit loop, phase overrun, flapping
        stuck_agent_task = asyncio.create_task(
            dispatch_stuck_agent_watcher(pool=db_pool),
            name="dispatch-stuck-agent-watcher",
        )
        # Expired-lease sweeper (60s) — DELETE expired leases + emit released events
        expired_lease_task = asyncio.create_task(
            dispatch_expired_lease_sweeper(pool=db_pool),
            name="dispatch-expired-lease-sweeper",
        )
        # STORY-901: PR-merge sweeper (300s) — emit 'accepted' for in_review rows whose PR merged
        pr_merge_sweeper_task = asyncio.create_task(
            dispatch_pr_merge_sweeper(pool=db_pool),
            name="dispatch-pr-merge-sweeper",
        )
        # Q7: knowledge ingest worker (5 min) — extract decisions/anti-patterns from completed stories
        knowledge_ingest_task = asyncio.create_task(
            knowledge_ingest_worker(pool=db_pool),
            name="knowledge-ingest-worker",
        )
        # Q9: apprenticeship pattern proposer (weekly) — surfaces candidate auto-rules to Mark
        apprenticeship_task = asyncio.create_task(
            apprenticeship_proposer_loop(pool=db_pool),
            name="apprenticeship-proposer-loop",
        )
        # STORY-902: PR link backfill (5 min) — writes pr_number on in_review rows with NULL
        pr_link_backfill_task = asyncio.create_task(
            dispatch_pr_link_backfill_sweeper(pool=db_pool),
            name="dispatch-pr-link-backfill-sweeper",
        )
        # Auto-requeue stale in_review rows that have no PR linkage.
        stale_unlinked_review_task = asyncio.create_task(
            dispatch_stale_unlinked_in_review_sweeper(pool=db_pool),
            name="dispatch-stale-unlinked-review-sweeper",
        )

        # Epic-Queue-v2 Q5 — surface the dispatch protocol mode at startup so the
        # cutover state is visible in journald. The env var is read by the
        # agent-side poller selector; the ops console serves both v1 and v2 routes
        # regardless. Logging here just makes "which protocol are agents using?"
        # answerable from a single grep.
        _dispatch_protocol = os.environ.get("DISPATCH_PROTOCOL", "v1").strip().lower()
        if _dispatch_protocol == "v2":
            logger.info(
                "Dispatch protocol: v2 (atomic claim + lease tokens) — agents on this env will use dispatch_poller_v2"
            )
        else:
            logger.info(
                "Dispatch protocol: %s (legacy /api/dispatch routes)", _dispatch_protocol or "v1"
            )

        logger.info("Ops console started successfully")
        yield

        # Shutdown
        logger.info("Shutting down ops console")
        recovery_task.cancel()
        dep_watcher_task.cancel()
        needs_info_ttl_task.cancel()
        stuck_agent_task.cancel()
        expired_lease_task.cancel()
        pr_merge_sweeper_task.cancel()
        knowledge_ingest_task.cancel()
        apprenticeship_task.cancel()
        pr_link_backfill_task.cancel()
        stale_unlinked_review_task.cancel()
        try:
            await recovery_task
        except asyncio.CancelledError:
            pass
        for task in (
            dep_watcher_task,
            needs_info_ttl_task,
            stuck_agent_task,
            expired_lease_task,
            pr_merge_sweeper_task,
            knowledge_ingest_task,
            apprenticeship_task,
            pr_link_backfill_task,
            stale_unlinked_review_task,
        ):
            try:
                await task
            except asyncio.CancelledError:
                pass
        if db_pool is not None:
            await db_pool.close()
        await http_client.aclose()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=lifespan,
    )

    # Set settings on app.state immediately (before lifespan) so auth works
    app.state.settings = settings

    # CORS middleware (only when origins are configured)
    if settings.cors_origins:
        origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
        if origins:
            app.add_middleware(
                CORSMiddleware,
                allow_origins=origins,
                allow_credentials=True,
                allow_methods=["*"],
                allow_headers=["*"],
            )
            logger.info("CORS enabled for origins: %s", origins)

    # Exception handlers
    @app.exception_handler(AgentNotFoundError)
    async def agent_not_found_handler(request, exc):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    # Register routes
    app.include_router(health.router, prefix="/api")
    app.include_router(presence.router, prefix="/api")  # Before agents — /agents/presence must not be caught by /agents/{name}
    app.include_router(agents.router, prefix="/api")
    app.include_router(fleet.router, prefix="/api")
    app.include_router(foundry_cost.router, prefix="/api")  # STORY-576
    app.include_router(alerts.router, prefix="/api")
    app.include_router(messages.router, prefix="/api")
    app.include_router(context.router, prefix="/api")
    app.include_router(dispatch.router, prefix="/api")
    app.include_router(dispatch_v2.router, prefix="/api/dispatch/v2")  # Epic-Queue-v2 Q2
    app.include_router(ops_fallback.router, prefix="/api/ops/fallback")  # STORY-1010
    app.include_router(knowledge_routes.router, prefix="/api/knowledge")  # Epic-Queue-v2 Q7
    app.include_router(apprenticeship_routes.router, prefix="/api/apprenticeship")  # Epic-Queue-v2 Q9
    app.include_router(curate.router, prefix="/api")
    app.include_router(work_history.router)

    return app
