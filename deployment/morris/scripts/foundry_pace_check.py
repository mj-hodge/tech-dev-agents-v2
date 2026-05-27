#!/usr/bin/env python3
"""Morris hourly Foundry-spend pace check.

Runs once per hour from Morris's crontab. Polls Azure Cost Management
Query API for today's actual cost in the Foundry RG and computes
hourly pace. If pace exceeds the threshold AND the day's total has
crossed a floor, sends Mark an email via the m365 CLI.

Detection model
---------------
The Cost Management Query API is the only source that reconciles to
the Azure invoice. It is the Layer 1 / "truth" signal in
``docs/azure-foundry-billing.md``. The lag is 1-4 hours, but for
detecting sustained pace (vs catching a single bad call) that's fine —
a $30/hr pace held for 4 hours is still very visible by hour 5, and
that's earlier than a $300/day budget alert which only fires at
end-of-day.

Why not Cost Management's native budget feature?
  Native budgets only support Monthly/Quarterly/Annual time grains.
  There is no native "$N per day" budget in Azure. This script is the
  workaround.

Auth
----
Reads Azure SP credentials from /home/hermes/.hermes/.env (already used
by ops-console for the same Cost Mgmt queries):
  OPS_AZURE_TENANT_ID
  OPS_AZURE_CLIENT_ID
  OPS_AZURE_CLIENT_SECRET
  OPS_AZURE_SUBSCRIPTION_ID

Notification
------------
Sends a Teams DM to Mark via ``m365 chat message send --chatName Morris``
— the same path ``_notify_teams`` in deployment/hermes/sdlc_phase_runner.py
uses for critical alerts. Mark gets a phone push.

Why not email? The Morris bot app's Graph scope set is Teams-only
(Chat.ReadWrite, ChatMessage.Send, Presence.ReadWrite, User.Read).
``Mail.Send`` would need to be added + admin-consented to use
``m365 outlook mail send``. Tonight's ship target is detection +
notification, so we use the channel that already works.

Throttle
--------
Once an alert fires for the current UTC day, a marker at
``/home/hermes/state/morris/foundry-pace-alert-<YYYYMMDD>`` prevents
re-sending. Mark gets ONE email per day per pace breach. Removing the
marker by hand re-arms it.

Source
------
``docs/azure-foundry-billing.md`` § Real-Time Monitoring Playbook is the
recipe this implements.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ENV_FILE = "/home/hermes/.hermes/.env"
STATE_DIR = Path("/home/hermes/state/morris")
LOG_FILE = STATE_DIR / "foundry-pace.log"

# Tunable via env. Defaults match Mark's brief (2026-04-25): "alert if we
# spend more than $30/hr". Floor prevents noisy alerts in the first hour
# of the UTC day when a single $35 call gives a misleading $35/hr pace.
PACE_THRESHOLD_USD_PER_HR = float(os.environ.get("FOUNDRY_PACE_USD_PER_HR", "30"))
FLOOR_USD = float(os.environ.get("FOUNDRY_PACE_FLOOR_USD", "50"))

# RG that hosts the Foundry deployments. Sweden Central + EastUS are both
# inside rg-tech-dev-agents-dev per docs/azure-foundry-billing.md.
RG = os.environ.get("FOUNDRY_RG", "rg-tech-dev-agents-dev")

# Teams DM target: Mark's email. m365 teams chat message send --userEmails
# resolves the 1:1 chat (or creates it) automatically. More robust than
# --chatName which requires the chat to have a display name set.
ALERT_USER_EMAIL = os.environ.get("FOUNDRY_PACE_ALERT_TO", "moreta@gorillacommerce.co")


def _log(line: str) -> None:
    """Append a timestamped line to the pace log AND stdout (cron picks it up)."""
    ts = datetime.now(timezone.utc).isoformat()
    msg = f"{ts} {line}"
    print(msg, flush=True)
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a") as f:
            f.write(msg + "\n")
    except Exception:
        # Logging must never fail the deploy decision.
        pass


def _load_env_file(path: str) -> None:
    """Read ``KEY=VALUE`` pairs into os.environ (no overwrite)."""
    if not os.path.isfile(path):
        return
    with open(path) as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip().strip("'\"")
            os.environ.setdefault(k, v)


def _get_token() -> str:
    """OAuth2 client-credentials → access token for management.azure.com."""
    body = urllib.parse.urlencode({
        "client_id":     os.environ["OPS_AZURE_CLIENT_ID"],
        "client_secret": os.environ["OPS_AZURE_CLIENT_SECRET"],
        "scope":         "https://management.azure.com/.default",
        "grant_type":    "client_credentials",
    }).encode()
    req = urllib.request.Request(
        f"https://login.microsoftonline.com/{os.environ['OPS_AZURE_TENANT_ID']}/oauth2/v2.0/token",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())["access_token"]


def _today_spend_usd(token: str) -> float:
    """Sum today's actual cost in the Foundry RG.

    Implements the Layer 1 query from docs/azure-foundry-billing.md:
    Cost Management Query API, ActualCost, today's window, daily grain.
    """
    sub = os.environ["OPS_AZURE_SUBSCRIPTION_ID"]
    now = datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    end = now.strftime("%Y-%m-%dT23:59:59Z")
    body = {
        "type": "ActualCost",
        "timeframe": "Custom",
        "timePeriod": {"from": start, "to": end},
        "dataset": {
            "granularity": "Daily",
            "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}},
        },
    }
    url = (
        f"https://management.azure.com/subscriptions/{sub}/resourceGroups/{RG}"
        "/providers/Microsoft.CostManagement/query?api-version=2023-11-01"
    )
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type":  "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        resp = json.loads(r.read())
    rows = resp.get("properties", {}).get("rows", []) or []
    total = 0.0
    for row in rows:
        # Each row is [Cost, UsageDate, Currency] for Daily grain. Cost is index 0.
        if row and isinstance(row[0], (int, float)):
            total += float(row[0])
    return total


def _hours_elapsed_utc_today() -> float:
    """How many hours into the current UTC day we are. Floor at 1/60 to avoid /0."""
    now = datetime.now(timezone.utc)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return max((now - midnight).total_seconds() / 3600.0, 1.0 / 60)


def _send_teams_dm(message: str) -> bool:
    """Send a Teams DM via m365 CLI. Returns True on rc==0.

    Uses the same call shape as ``_notify_teams`` in
    deployment/hermes/sdlc_phase_runner.py — already proven on the fleet.
    """
    cmd = [
        "/usr/bin/m365", "teams", "chat", "message", "send",
        "--userEmails", ALERT_USER_EMAIL,
        "--message", message,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            _log(f"teams send FAILED rc={r.returncode} stderr={r.stderr[:300]}")
            return False
        _log(f"teams send OK to={ALERT_USER_EMAIL}")
        return True
    except Exception as exc:
        _log(f"teams send EXCEPTION {type(exc).__name__}: {exc}")
        return False


def main() -> int:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    _load_env_file(ENV_FILE)

    # Validate required vars BEFORE making any API call.
    missing = [
        v for v in (
            "OPS_AZURE_CLIENT_ID",
            "OPS_AZURE_CLIENT_SECRET",
            "OPS_AZURE_TENANT_ID",
            "OPS_AZURE_SUBSCRIPTION_ID",
        ) if not os.environ.get(v)
    ]
    if missing:
        _log(f"missing env vars: {missing} — aborting (no alert sent)")
        return 1

    try:
        token = _get_token()
    except Exception as exc:
        _log(f"token fetch FAILED {type(exc).__name__}: {exc}")
        return 1

    try:
        spend = _today_spend_usd(token)
    except Exception as exc:
        _log(f"cost query FAILED {type(exc).__name__}: {exc}")
        return 1

    elapsed = _hours_elapsed_utc_today()
    pace = spend / elapsed
    projected_eod = pace * 24

    _log(
        f"check today_spend=${spend:.2f} elapsed_h={elapsed:.2f} "
        f"pace=${pace:.2f}/h projected_eod=${projected_eod:.2f} "
        f"threshold=${PACE_THRESHOLD_USD_PER_HR:.2f}/h floor=${FLOOR_USD:.2f}"
    )

    if pace <= PACE_THRESHOLD_USD_PER_HR or spend < FLOOR_USD:
        return 0

    today_ymd = datetime.now(timezone.utc).strftime("%Y%m%d")
    alert_marker = STATE_DIR / f"foundry-pace-alert-{today_ymd}"
    if alert_marker.exists():
        _log(f"alert already sent today (marker={alert_marker.name}) — skipping")
        return 0

    message = (
        f"[fleet] Foundry pace ${pace:.2f}/hr — today already at ${spend:.2f}\n"
        f"Elapsed: {elapsed:.2f}h | EOD projected: ${projected_eod:.2f} | "
        f"Threshold: ${PACE_THRESHOLD_USD_PER_HR:.2f}/hr\n"
        f"Source: Cost Management Query API ({RG})\n"
        f"Throttle: 1 alert/UTC day. Re-arm: rm {alert_marker}\n"
        f"Likely causes (docs/azure-foundry-billing.md): Opus cron compression cascade "
        f"(~10x visible session cost), or non-hermes Foundry caller."
    )
    if _send_teams_dm(message):
        try:
            alert_marker.touch()
            _log(f"alert marker written: {alert_marker}")
        except Exception as exc:
            _log(f"could not write alert marker: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
