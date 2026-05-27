"""Ops-skill fallback registry — STORY-1010.

Every operator skill registers a documented SQL-direct fallback path.
When the skill is unavailable (or the operator types `/<skill> --fallback`),
the registry produces a copy-pasteable SQL command + a runbook URL.

Usage:
    from tech_dev_agents.ops_console.ops_skill_fallback_registry import (
        REGISTRY, get_fallback, render_fallback,
    )

    fb = get_fallback("requeue-failed")
    print(render_fallback("requeue-failed", repo="my-repo"))
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Fallback:
    """A documented SQL-direct fallback for an operator skill.

    These are in-memory dataclass fields, not DB columns.
    migration-ci: ignore
    """

    skill_name: str  # migration-ci: ignore
    sql_template: str  # migration-ci: ignore
    runbook_url: str  # migration-ci: ignore
    description: str  # migration-ci: ignore
    required_params: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Registry (module-level dict populated at import time)
# ---------------------------------------------------------------------------

REGISTRY: dict[str, Fallback] = {}


def register_fallback(
    skill_name: str,  # migration-ci: ignore
    sql_template: str,  # migration-ci: ignore
    runbook_url: str,  # migration-ci: ignore
    description: str,  # migration-ci: ignore
    required_params: list[str] | None = None,
) -> Fallback:
    """Register a fallback entry. Returns the Fallback for chaining."""
    fb = Fallback(
        skill_name=skill_name,
        sql_template=sql_template,
        runbook_url=runbook_url,
        description=description,
        required_params=required_params or [],
    )
    REGISTRY[skill_name] = fb
    logger.info("ops_fallback_registry: registered fallback for %s", skill_name)
    return fb


def get_fallback(skill_name: str) -> Fallback | None:
    """Look up a fallback by skill name. Returns None if not found."""
    return REGISTRY.get(skill_name)


def render_fallback(skill_name: str, **params: str) -> str | None:
    """Produce the operator-facing block: copy-paste SQL + runbook link.

    Returns None if the skill is not registered.
    """
    fb = get_fallback(skill_name)
    if fb is None:
        return None

    # Substitute any provided params into the SQL template
    sql = fb.sql_template
    for key, value in params.items():
        sql = sql.replace(f"${key}", str(value))

    lines = [
        f"=== FALLBACK: /{fb.skill_name} ===",
        "",
        f"Description: {fb.description}",
        "",
        "SQL (copy-paste into psql or pgAdmin):",
        "```sql",
        sql,
        "```",
        "",
        f"Runbook: {fb.runbook_url}",
    ]

    if fb.required_params:
        lines.append("")
        lines.append("Parameters:")
        for p in fb.required_params:
            value = params.get(p, f"<{p}>")
            lines.append(f"  - {p}: {value}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Initial registrations — the 5 highest-leverage operator skills
# ---------------------------------------------------------------------------

register_fallback(
    skill_name="requeue-failed",
    sql_template=(
        "UPDATE dispatch_state_current\n"
        "SET state = 'enqueued', updated_at = NOW()\n"
        "WHERE state = 'failed'\n"
        "  AND job_id IN (\n"
        "    SELECT job_id FROM dispatch_jobs\n"
        "    WHERE repo = $repo\n"
        "      AND created_at > NOW() - INTERVAL '24 hours'\n"
        "  );"
    ),
    runbook_url="https://tech-gc-knowledgebase.gorillacommerce.ai/wiki/runbooks/requeue-failed.md",
    description=(
        "Requeue all failed dispatch jobs for a given repo from the last 24 hours. "
        "Use when /requeue-failed operator skill is unavailable."
    ),
    required_params=["repo"],
)

register_fallback(
    skill_name="dispatch-recovery",
    sql_template=(
        "-- Step 1: Identify stuck leased jobs (no heartbeat > 10 min)\n"
        "SELECT j.job_id, j.repo, j.story_id, sc.state, sc.updated_at\n"
        "FROM dispatch_state_current sc\n"
        "JOIN dispatch_jobs j ON j.job_id = sc.job_id\n"
        "WHERE sc.state = 'leased'\n"
        "  AND sc.updated_at < NOW() - INTERVAL '10 minutes';\n"
        "\n"
        "-- Step 2: Release stuck leases and requeue\n"
        "DELETE FROM dispatch_leases\n"
        "WHERE job_id IN (\n"
        "  SELECT job_id FROM dispatch_state_current\n"
        "  WHERE state = 'leased'\n"
        "    AND updated_at < NOW() - INTERVAL '10 minutes'\n"
        ");\n"
        "\n"
        "UPDATE dispatch_state_current\n"
        "SET state = 'enqueued', updated_at = NOW()\n"
        "WHERE state = 'leased'\n"
        "  AND updated_at < NOW() - INTERVAL '10 minutes';"
    ),
    runbook_url="https://tech-gc-knowledgebase.gorillacommerce.ai/wiki/runbooks/dispatch-recovery.md",
    description=(
        "Recover stuck dispatches by releasing stale leases and requeuing jobs. "
        "Use when agents have died without heartbeating or the /dispatch-recovery skill is unavailable."
    ),
    required_params=[],
)

register_fallback(
    skill_name="release-stale-claim",
    sql_template=(
        "DELETE FROM dispatch_leases\n"
        "WHERE lease_token = $lease_token;\n"
        "\n"
        "UPDATE dispatch_state_current\n"
        "SET state = 'enqueued', updated_at = NOW()\n"
        "WHERE job_id = $job_id\n"
        "  AND state = 'leased';"
    ),
    runbook_url="https://tech-gc-knowledgebase.gorillacommerce.ai/wiki/runbooks/release-stale-claim.md",
    description=(
        "Release a specific stale claim and requeue the job. "
        "Use when a single agent is stuck and /release-stale-claim skill is unavailable."
    ),
    required_params=["job_id", "lease_token"],
)

register_fallback(
    skill_name="dead-letter-purge",
    sql_template=(
        "-- Review dead-lettered jobs before purging\n"
        "SELECT j.job_id, j.repo, j.story_id, sc.updated_at\n"
        "FROM dispatch_state_current sc\n"
        "JOIN dispatch_jobs j ON j.job_id = sc.job_id\n"
        "WHERE sc.state = 'dead_letter'\n"
        "  AND sc.updated_at < NOW() - INTERVAL '7 days';\n"
        "\n"
        "-- Purge (mark as cancelled, preserving audit trail)\n"
        "UPDATE dispatch_state_current\n"
        "SET state = 'cancelled', updated_at = NOW()\n"
        "WHERE state = 'dead_letter'\n"
        "  AND updated_at < NOW() - INTERVAL '7 days';"
    ),
    runbook_url="https://tech-gc-knowledgebase.gorillacommerce.ai/wiki/runbooks/dead-letter-purge.md",
    description=(
        "Purge dead-lettered jobs older than 7 days by transitioning to cancelled. "
        "Use when /dead-letter-purge skill is unavailable."
    ),
    required_params=[],
)

register_fallback(
    skill_name="force-claim",
    sql_template=(
        "-- Force-claim a specific job for a specific agent\n"
        "INSERT INTO dispatch_leases (job_id, lease_token, agent_name, leased_at)\n"
        "VALUES ($job_id, gen_random_uuid(), $agent_name, NOW())\n"
        "ON CONFLICT (job_id) DO UPDATE\n"
        "  SET lease_token = gen_random_uuid(),\n"
        "      agent_name = $agent_name,\n"
        "      leased_at = NOW();\n"
        "\n"
        "UPDATE dispatch_state_current\n"
        "SET state = 'leased', updated_at = NOW()\n"
        "WHERE job_id = $job_id;"
    ),
    runbook_url="https://tech-gc-knowledgebase.gorillacommerce.ai/wiki/runbooks/force-claim.md",
    description=(
        "Force-claim a job for manual redispatch to a specific agent. "
        "Use when /force-claim skill is unavailable or claim-by-id API is down."
    ),
    required_params=["job_id", "agent_name"],
)
