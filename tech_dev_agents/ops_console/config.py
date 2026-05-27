"""Configuration for the Agent Operations Console — Pydantic Settings from env vars."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """All configuration from environment variables."""

    # App
    app_name: str = "Agent Operations Console"
    app_version: str = "0.1.0"
    debug: bool = False

    # Auth
    ops_console_api_key: str  # Required, no default (legacy — treated as manager role by auth.py)

    # STORY-514: Role-scoped API keys. Agent-scoped key gets 403 on destructive
    # endpoints (DELETE, priority mutations). Manager-scoped gets full access.
    # Empty strings disable the role; callers presenting those keys fall through
    # to the legacy key check. Populate all three in production.
    agent_role_api_key: str = ""
    manager_role_api_key: str = ""
    admin_role_api_key: str = ""

    # Entra ID SSO
    entra_tenant_id: str = ""  # Azure AD tenant ID
    entra_client_id: str = ""  # App registration client ID

    # Agent Registry
    agent_registry_path: str = "deployment/vm/agent-registry.json"

    # Loki
    loki_url: str = "https://grafana.gorillacommerce.ai"
    loki_api_key: str  # Required
    loki_timeout_seconds: int = 30

    # Azure Cost Management (optional — subscription_id required, credential auto-discovered)
    azure_subscription_id: str | None = None
    # User-assigned managed identity client ID (optional — omit for system-assigned or auto-discovery)
    azure_managed_identity_client_id: str | None = None

    # Azure Resource Group → Agent Name mapping (JSON string)
    azure_agent_map: str = '{"rg-agent-dan": "dan", "rg-agent-derrick": "derrick"}'

    # Monday.com (per-agent, JSON string of {agent_name: {api_token, board_id}})
    monday_config: str = "{}"

    # Agent Communication
    agent_api_key: str  # For authenticating to agent VMs

    # Cache TTLs
    health_cache_ttl: int = 30
    cost_cache_ttl: int = 300
    monday_cache_ttl: int = 300
    fleet_cache_ttl: int = 60

    # Server
    host: str = "127.0.0.1"
    port: int = 8005

    # Microsoft Graph API (Teams messaging)
    graph_api_token: str = ""  # Graph API bearer token (static fallback)
    graph_api_base_url: str = "https://graph.microsoft.com/v1.0"

    # MSAL client credentials for auto-refresh (STORY-228)
    graph_tenant_id: str = ""  # Azure AD tenant ID
    graph_client_id: str = ""  # App registration client ID
    graph_client_secret: str = ""  # App registration client secret

    # GitHub (for work history PR status)
    github_token: str = ""  # GitHub PAT with repo read access

    # Dispatch Queue Database (STORY-028, STORY-032: optional — empty string skips DB)
    database_url: str = ""

    # JSON dispatch queue file path (fallback when database_url is empty)
    # Docker uses /var/lib/ops-console; bare-metal uses data/ relative to CWD
    dispatch_queue_path: str = "data/dispatch-queue.json"

    # CORS (for dev mode)
    cors_origins: str = ""  # Comma-separated origins, empty = no CORS

    # Presence probe (STORY-426)
    presence_ssh_timeout: int = 5
    presence_cache_ttl: int = 30
    presence_ssh_user: str = "agent"
    presence_poller_service: str = "dispatch-poller"
    presence_pause_flag_path: str = "/var/run/dispatch-poller-paused-until"

    # Daily budget for ops spending (STORY-480)
    daily_budget_usd: float = 50.0

    # Feature flag: Dashboard Overhaul (STORY-480)
    dashboard_overhaul_enabled: bool = False

    # Feature flag: Dispatch Claim Sync (STORY-494)
    dispatch_claim_sync_enabled: bool = False

    # Feature flag: Agent Quota Endpoint (STORY-496)
    # Gates GET /api/agents/{name}/quota — returns 404 when false
    agent_quota_enabled: bool = False

    # Feature flag: Dispatch Review Transition (STORY-496)
    # Gates POST /api/dispatch/review/{story_id} — returns 404 when false
    dispatch_review_enabled: bool = False

    # Feature flag: Dispatch Pause/Resume (STORY-507)
    dispatch_pause_enabled: bool = False

    # Feature flag: Dispatch Needs Info (STORY-532)
    # Gates POST /api/dispatch/needs-info/{story_id} and POST /api/dispatch/resume/{story_id}
    dispatch_needs_info_enabled: bool = Field(False, alias="OPS_DISPATCH_NEEDS_INFO_ENABLED")

    # Feature flag: Grafana Webhook Receiver (STORY-508)
    # Gates POST /api/alerts/grafana and GET /api/alerts/active — returns 404 when false
    grafana_webhook_enabled: bool = False

    # Feature flag: Dispatch Priority API (STORY-508)
    # Gates POST /api/dispatch/priority — returns 404 when false
    dispatch_priority_enabled: bool = False

    @property
    def azure_enabled(self) -> bool:
        return bool(self.azure_subscription_id)

    model_config = {"env_prefix": "OPS_", "env_file": ".env"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
