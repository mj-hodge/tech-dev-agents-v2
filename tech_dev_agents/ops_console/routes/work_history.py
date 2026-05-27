"""Work history route — shows completed/in-progress stories with PR status."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Query, Request

from tech_dev_agents.ops_console.models.responses import (
    CompletedStory,
    WorkHistoryResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# Repos to scan for agent PRs
REPOS = [
    "hpi-gorillacommerce/tech-dev-agents",
    "hpi-gorillacommerce/product-health-dashboard",
    "hpi-gorillacommerce/advertising-amazon",
    "hpi-gorillacommerce/tech-datawarehouse",
    "hpi-gorillacommerce/sourcing-warning-labels",
    "hpi-gorillacommerce/tech-project-mapping",
    "hpi-gorillacommerce/tech-dataimport-monday",
]

# GitHub PAT for reading PRs (read-only)
_GH_TOKEN: str | None = None


def _get_gh_token(request: Request) -> str | None:
    global _GH_TOKEN
    if _GH_TOKEN is None:
        _GH_TOKEN = getattr(request.app.state.settings, "github_token", "") or ""
    return _GH_TOKEN or None


async def _fetch_prs(repo: str, token: str | None, state: str = "all") -> list[dict]:
    """Fetch PRs from a GitHub repo."""
    url = f"https://api.github.com/repos/{repo}/pulls?state={state}&per_page=30&sort=updated&direction=desc"
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                return resp.json()
            logger.warning("GitHub API %s returned %d", repo, resp.status_code)
            return []
    except Exception as exc:
        logger.warning("Failed to fetch PRs for %s: %s", repo, exc)
        return []


def _extract_agent(pr: dict) -> str | None:
    """Guess the agent from PR branch or author."""
    branch = pr.get("head", {}).get("ref", "")
    author = (pr.get("user") or {}).get("login", "")
    if "dan" in author.lower() or "dan" in branch.lower():
        return "dan"
    if "derrick" in author.lower() or "derrick" in branch.lower():
        return "derrick"
    # Check for bot authors
    if "bot-dan" in author.lower():
        return "dan"
    if "bot-derrick" in author.lower():
        return "derrick"
    return None


def _extract_story_id(pr: dict) -> str:
    """Extract STORY-XXX from PR title or branch."""
    import re
    title = pr.get("title", "")
    branch = pr.get("head", {}).get("ref", "")
    for text in [title, branch]:
        m = re.search(r"STORY-(\d+)", text, re.IGNORECASE)
        if m:
            return f"STORY-{m.group(1)}"
        m = re.search(r"story-(\d+)", text)
        if m:
            return f"STORY-{m.group(1)}"
    return pr.get("title", "unknown")[:60]


@router.get("/api/work-history", response_model=WorkHistoryResponse)
async def work_history(
    request: Request,
    agent: Optional[str] = Query(None, description="Filter by agent name"),
    since: Optional[str] = Query(None, description="Filter by date (YYYY-MM-DD), inclusive"),
) -> WorkHistoryResponse:
    """Return recent agent work across all repos with PR status.

    STORY-480: Supports ?agent= and ?since= query params when
    DASHBOARD_OVERHAUL_ENABLED is True.
    """
    # Feature flag check (STORY-480)
    settings = getattr(request.app.state, "settings", None)
    flag_enabled = getattr(settings, "dashboard_overhaul_enabled", False)
    if (agent is not None or since is not None) and not flag_enabled:
        raise HTTPException(
            status_code=404,
            detail="Dashboard overhaul features are not enabled (DASHBOARD_OVERHAUL_ENABLED=false)",
        )

    token = _get_gh_token(request)
    stories: list[CompletedStory] = []

    for repo in REPOS:
        prs = await _fetch_prs(repo, token, state="all")
        repo_short = repo.split("/")[-1]

        for pr in prs:
            pr_agent = _extract_agent(pr)
            if not pr_agent:
                continue

            pr_state = "merged" if pr.get("merged_at") else pr.get("state", "open")
            stories.append(
                CompletedStory(
                    story_id=_extract_story_id(pr),
                    repo=repo_short,
                    branch=pr.get("head", {}).get("ref"),
                    pr_number=pr.get("number"),
                    pr_url=pr.get("html_url"),
                    pr_state=pr_state,
                    agent=pr_agent,
                    completed_at=pr.get("merged_at") or pr.get("updated_at"),
                    summary=pr.get("title"),
                    total_cost_usd=None,  # STORY-480: populated from cost tracking in future
                )
            )

    # STORY-480: Apply agent filter
    if agent is not None:
        stories = [s for s in stories if s.agent == agent]

    # STORY-480: Apply since filter
    if since is not None:
        try:
            since_dt = datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            filtered = []
            for s in stories:
                if s.completed_at:
                    try:
                        # Parse ISO timestamp, make timezone-aware
                        ca = s.completed_at.replace("Z", "+00:00")
                        ca_dt = datetime.fromisoformat(ca)
                        if ca_dt >= since_dt:
                            filtered.append(s)
                    except ValueError:
                        filtered.append(s)  # keep if unparseable
                # stories with no completed_at are excluded when since filter is active
            stories = filtered
        except ValueError:
            logger.warning("Invalid since date format: %s (expected YYYY-MM-DD)", since)

    # Sort by most recent first
    stories.sort(key=lambda s: s.completed_at or "", reverse=True)

    return WorkHistoryResponse(
        stories=stories,
        total=len(stories),
        fetched_at=datetime.now(timezone.utc).isoformat(),
    )
