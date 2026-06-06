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

    # Role-scoped API keys. Agent-scoped key gets 403 on destructive endpoints.
    # Manager-scoped gets full access. Empty strings disable the role.
    agent_role_api_key: str = ""
    manager_role_api_key: str = ""
    admin_role_api_key: str = ""

    # Agent Registry
    agent_registry_path: str = "deployment/vm/agent-registry.json"

    # Anthropic
    anthropic_api_key: str = ""

    # Agent Communication
    agent_api_key: str = ""  # For authenticating agent-to-console calls

    # Cache TTLs
    health_cache_ttl: int = 30
    cost_cache_ttl: int = 300
    fleet_cache_ttl: int = 60

    # Server
    host: str = "127.0.0.1"
    port: int = 8005

    # GitHub (for work history PR status)
    github_token: str = ""  # GitHub PAT with repo read access

    # Dispatch Queue Database (optional — empty string skips DB)
    database_url: str = ""

    # JSON dispatch queue file path (fallback when database_url is empty)
    dispatch_queue_path: str = "data/dispatch-queue.json"

    # CORS (for dev mode)
    cors_origins: str = ""  # Comma-separated origins, empty = no CORS

    # Agent process management (single-machine)
    agent_workspace_root: str = "/home/agents"
    dispatch_poller_service_template: str = "dispatch-poller@{name}"

    # Daily budget for ops spending
    daily_budget_usd: float = 50.0

    # Feature flags
    dispatch_pause_enabled: bool = False
    dispatch_needs_info_enabled: bool = Field(False, alias="OPS_DISPATCH_NEEDS_INFO_ENABLED")
    dispatch_priority_enabled: bool = False
    dispatch_review_enabled: bool = False
    agent_quota_enabled: bool = False

    model_config = {"env_prefix": "OPS_", "env_file": ".env"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
