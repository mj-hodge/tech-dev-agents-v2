#!/usr/bin/env python3
"""Morris Foundry credential expiry health check.

Probes the Azure AD app registration to determine when the service-principal
client secret expires.  Returns exit codes:
  0 = OK      (credential valid, >7 days until expiry)
  1 = WARN    (credential valid, <=7 days until expiry)
  2 = CRIT    (credential expired or missing)

When expiry is imminent or past, sends a Teams DM to Mark.

Auth
----
Uses the same SP credentials as foundry_pace_check.py:
  OPS_AZURE_TENANT_ID, OPS_AZURE_CLIENT_ID, OPS_AZURE_CLIENT_SECRET

Graph query reads the app's own passwordCredentials via MS Graph.

STORY-771 — created by Phase 8.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ENV_FILE = "/home/hermes/.hermes/.env"
WARN_THRESHOLD_DAYS = 7
RUNBOOK_PATH = "state/morris/foundry-auth-runbook.md"

LOG_FILE = Path("/home/hermes/state/morris/foundry-auth-check.log")
ALERT_USER_EMAIL = os.environ.get("FOUNDRY_AUTH_ALERT_TO", "moreta@gorillacommerce.co")


def _log(line: str) -> None:
    """Append a timestamped line to the log AND stdout."""
    ts = datetime.now(timezone.utc).isoformat()
    msg = f"{ts} {line}"
    print(msg, flush=True)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a") as f:
            f.write(msg + "\n")
    except Exception:
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


def _get_graph_token() -> str:
    """OAuth2 client-credentials → access token for graph.microsoft.com."""
    body = urllib.parse.urlencode({
        "client_id":     os.environ["OPS_AZURE_CLIENT_ID"],
        "client_secret": os.environ["OPS_AZURE_CLIENT_SECRET"],
        "scope":         "https://graph.microsoft.com/.default",
        "grant_type":    "client_credentials",
    }).encode()
    req = urllib.request.Request(
        f"https://login.microsoftonline.com/{os.environ['OPS_AZURE_TENANT_ID']}/oauth2/v2.0/token",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())["access_token"]


def _get_credential_expiry(token: str, app_id: str) -> list[dict]:
    """Query MS Graph for the app registration's passwordCredentials."""
    url = f"https://graph.microsoft.com/v1.0/applications?$filter=appId eq '{app_id}'&$select=passwordCredentials"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
    })
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read())
    apps = data.get("value", [])
    if not apps:
        return []
    return apps[0].get("passwordCredentials", [])


def days_until_expiry(expiry_iso: str) -> int:
    """Return whole days until the given ISO-8601 expiry datetime.

    Uses floor (truncation toward zero for positive, away from zero for
    negative) so '10 days and 3 hours from now' returns 10.
    """
    expiry = datetime.fromisoformat(expiry_iso)
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    delta = expiry - datetime.now(timezone.utc)
    total_seconds = delta.total_seconds()
    return int(total_seconds // 86400)


def check_credentials(credentials: list[dict]) -> tuple[str, int, str]:
    """Evaluate a list of passwordCredential dicts and return (status, rc, message).

    Uses the credential with the latest expiry date (most runway).
    """
    if not credentials:
        return ("CRIT", 2, "No credentials found on app registration")

    best_days = None
    best_name = "unknown"
    for cred in credentials:
        end_raw = cred.get("endDateTime")
        if not end_raw:
            continue
        try:
            d = days_until_expiry(end_raw)
        except (ValueError, TypeError):
            continue
        if best_days is None or d > best_days:
            best_days = d
            best_name = cred.get("displayName", "unknown")

    if best_days is None:
        return ("CRIT", 2, "No valid expiry dates found in credentials")

    if best_days < 0:
        return ("CRIT", 2, f"Credential '{best_name}' expired {abs(best_days)} days ago")
    if best_days <= WARN_THRESHOLD_DAYS:
        return ("WARN", 1, f"Credential '{best_name}' expires in {best_days} days")
    return ("OK", 0, f"Credential '{best_name}' valid for {best_days} days")


def _send_teams_alert(message: str) -> bool:
    """Send a Teams DM to Mark via m365 CLI. Returns True on success."""
    try:
        result = subprocess.run(
            [
                "m365", "teams", "chat", "message", "send",
                "--userEmails", ALERT_USER_EMAIL,
                "--message", message,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            _log(f"[ALERT-FAIL] m365 send failed: {result.stderr.strip()}")
            return False
        return True
    except Exception as exc:
        _log(f"[ALERT-FAIL] Teams alert exception: {exc}")
        return False


def main() -> int:
    """Entry point: load env, check credential expiry, alert if needed."""
    # Load env
    try:
        _load_env_file(ENV_FILE)
    except Exception as exc:
        _log(f"[ERROR] Failed to load env file: {exc}")

    # Verify required env vars
    required = ("OPS_AZURE_TENANT_ID", "OPS_AZURE_CLIENT_ID",
                "OPS_AZURE_CLIENT_SECRET")
    missing = [v for v in required if not os.environ.get(v)]
    if missing:
        _log(f"[ERROR] Missing required env vars: {', '.join(missing)}. "
             f"Renew via {RUNBOOK_PATH}")
        return 1

    app_id = os.environ["OPS_AZURE_CLIENT_ID"]

    # Get Graph token
    try:
        token = _get_graph_token()
    except Exception as exc:
        _log(f"[ERROR] Failed to get Graph token for credential '{app_id}': {exc}. "
             f"Renew via {RUNBOOK_PATH}")
        return 1

    # Query credential expiry
    try:
        creds = _get_credential_expiry(token, app_id)
    except Exception as exc:
        _log(f"[ERROR] Failed to query credential expiry for '{app_id}': {exc}. "
             f"Renew via {RUNBOOK_PATH}")
        return 1

    # Evaluate
    status, rc, msg = check_credentials(creds)
    _log(f"[{status}] {msg}")

    # Alert on WARN or CRIT
    if status in ("WARN", "CRIT"):
        alert_msg = (
            f"⚠️ Foundry Auth Check: {status}\n"
            f"{msg}\n"
            f"Runbook: {RUNBOOK_PATH}"
        )
        ok = _send_teams_alert(alert_msg)
        if not ok:
            _log("[WARN] Failed to send Teams alert (non-fatal)")

    return rc


if __name__ == "__main__":
    sys.exit(main())
