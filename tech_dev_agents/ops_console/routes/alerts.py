"""Alert routes — fleet-wide alert history, Grafana webhook, and active alerts.

STORY-508:
  - AC-6:  POST /api/alerts/grafana — HMAC-SHA256 validated Grafana webhook receiver
  - AC-7:  Webhook handler writes to alert-log.md for audit trail
  - AC-13: GET  /api/alerts/active — proxies active Grafana alerts (graceful degradation)

Feature flags (gated via Settings):
  - grafana_webhook_enabled: if False, /alerts/grafana and /alerts/active return 404
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from tech_dev_agents.ops_console.auth import require_auth
from tech_dev_agents.ops_console.models.responses import AlertListResponse

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_auth)])

# Default alert log path (overridable via app.state.alert_log_path for tests)
_DEFAULT_ALERT_LOG = "/home/hermes/state/morris/alert-log.md"


# ---------------------------------------------------------------------------
# Existing: GET /alerts — historical alert list
# ---------------------------------------------------------------------------


@router.get("/alerts", response_model=AlertListResponse)
async def get_alerts(
    request: Request,
    agent: Optional[str] = Query(None),
    type: Optional[str] = Query(None),
    active: Optional[bool] = Query(None),
    since: Optional[str] = Query(None),
    limit: int = Query(default=100, ge=1, le=500),
) -> AlertListResponse:
    """Alert history across all agents."""
    alert_service = request.app.state.alert_service
    return await alert_service.get_alerts(
        agent=agent,
        alert_type=type,
        active=active,
        since=since,
        limit=limit,
    )


# ---------------------------------------------------------------------------
# STORY-508 AC-6: POST /alerts/grafana — Grafana alertmanager webhook
# ---------------------------------------------------------------------------


@router.post("/alerts/grafana")
async def grafana_webhook(request: Request):
    """Receive Grafana Alertmanager webhooks with HMAC-SHA256 signature validation.

    AC-6 (STORY-508):
    - Validates X-Grafana-Signature header if GRAFANA_WEBHOOK_SECRET is set
    - When secret is unset: accepts all payloads (bootstrap/no-auth mode)
    - Returns {"accepted": N, "errors": []}
    - Feature-gated: returns 404 when grafana_webhook_enabled=False

    AC-7 (STORY-508):
    - Appends each accepted alert to the alert log (app.state.alert_log_path)
    """
    settings = request.app.state.settings
    if not getattr(settings, "grafana_webhook_enabled", False):
        raise HTTPException(404, "Grafana webhook is disabled (GRAFANA_WEBHOOK_ENABLED=false)")

    # Read raw body for HMAC verification
    body = await request.body()

    # HMAC-SHA256 signature validation
    webhook_secret = os.environ.get("GRAFANA_WEBHOOK_SECRET", "")
    if webhook_secret:
        sig_header = request.headers.get("X-Grafana-Signature", "")
        if not sig_header:
            raise HTTPException(401, "Missing X-Grafana-Signature header")

        expected = "sha256=" + hmac.new(
            webhook_secret.encode(), body, hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(sig_header, expected):
            raise HTTPException(401, "Invalid HMAC-SHA256 signature")

    # Parse payload
    import json as _json
    try:
        payload = _json.loads(body)
    except Exception:
        raise HTTPException(400, "Invalid JSON payload")

    alerts = payload.get("alerts", [])

    # AC-7: Write each alert to the audit log
    alert_log_path = getattr(request.app.state, "alert_log_path", _DEFAULT_ALERT_LOG)
    received_at = datetime.now(timezone.utc).isoformat()

    log_entries: list[str] = []
    for alert in alerts:
        labels = alert.get("labels", {})
        alert_name = labels.get("alertname", alert.get("name", "unknown"))
        severity = labels.get("severity", "unknown")
        agent_label = labels.get("agent", "unknown")
        starts_at = alert.get("startsAt", received_at)

        entry = (
            f"- received_at: {received_at}  "
            f"alertname: {alert_name}  "
            f"severity: {severity}  "
            f"agent: {agent_label}  "
            f"startsAt: {starts_at}\n"
        )
        log_entries.append(entry)

    if log_entries:
        log_path = Path(alert_log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a") as f:
            f.writelines(log_entries)

    logger.info("Grafana webhook: accepted %d alert(s)", len(alerts))
    return {"accepted": len(alerts), "errors": []}


# ---------------------------------------------------------------------------
# STORY-508 AC-13: GET /alerts/active — active Grafana alerts proxy
# ---------------------------------------------------------------------------


@router.get("/alerts/active")
async def get_active_alerts(request: Request):
    """Return currently firing Grafana alerts via the Grafana service.

    AC-13 (STORY-508):
    - Proxies request.app.state.grafana_service.get_active_alerts()
    - On ConnectionError / service unavailable: degrades gracefully with
      {"alerts": [], "count": 0, "fetched_at": "...", "error": "grafana_unreachable"}
    - Never returns 5xx — always 200 (with error field on degradation)
    - Feature-gated: returns 404 when grafana_webhook_enabled=False
    """
    settings = request.app.state.settings
    if not getattr(settings, "grafana_webhook_enabled", False):
        raise HTTPException(404, "Grafana alerts endpoint is disabled (GRAFANA_WEBHOOK_ENABLED=false)")

    fetched_at = datetime.now(timezone.utc).isoformat()
    grafana_service = getattr(request.app.state, "grafana_service", None)

    if grafana_service is None:
        # No Grafana service configured — graceful degradation
        return JSONResponse({
            "alerts": [],
            "count": 0,
            "fetched_at": fetched_at,
            "error": "grafana_unreachable",
        })

    try:
        result = await grafana_service.get_active_alerts()
        return JSONResponse(result)
    except (ConnectionError, OSError, Exception) as exc:
        logger.warning("Grafana active alerts fetch failed: %s", exc)
        return JSONResponse({
            "alerts": [],
            "count": 0,
            "fetched_at": fetched_at,
            "error": "grafana_unreachable",
        })
