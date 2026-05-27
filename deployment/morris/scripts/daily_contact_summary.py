#!/usr/bin/env python3
"""Morris daily contact summary — posts a [BRIEFING] DM to Mark via Teams Graph API.

Run daily at 08:00 UTC Mon-Fri via cron (see install-orchestrator-cron.sh).

Usage:
    python daily_contact_summary.py --config /opt/morris/orchestrator_config.yaml

The orchestrator_config.yaml must contain (at minimum):
    mark_chat_id: "<Teams chat ID for the 1:1 DM with Mark>"
    graph_token_cmd: "m365 util accesstoken get --resource https://graph.microsoft.com"
      # OR
    graph_token: "<static token — for testing only>"

Environment variables (override config file):
    MORRIS_MARK_CHAT_ID     Teams chat ID for Mark's 1:1 DM
    MORRIS_CONTACTS_FILE    Path to contacts.json (default: /opt/morris/data/contacts.json)
    MORRIS_GRAPH_TOKEN      Bearer token (skip m365 CLI call)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import pathlib
import urllib.request
import urllib.error
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Import-path bootstrap (same as orchestrator_loop.py)
# ---------------------------------------------------------------------------
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="[daily-contact-summary] %(levelname)s %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths / constants
# ---------------------------------------------------------------------------

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
CONTACTS_FILE = pathlib.Path(
    os.environ.get("MORRIS_CONTACTS_FILE", "/opt/morris/data/contacts.json")
)


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def load_config(config_path: str) -> dict:
    """Load YAML config (pyyaml optional — falls back to basic key: value parsing)."""
    path = pathlib.Path(config_path)
    if not path.exists():
        logger.warning("Config file not found: %s — using env vars only", config_path)
        return {}

    try:
        import yaml  # type: ignore
        with open(path, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except ImportError:
        pass

    # Minimal fallback: parse "key: value" lines (no nested YAML)
    cfg: dict = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and ":" in line:
            key, _, value = line.partition(":")
            cfg[key.strip()] = value.strip()
    return cfg


# ---------------------------------------------------------------------------
# Token
# ---------------------------------------------------------------------------

def get_graph_token(config: dict) -> str:
    """Resolve Graph API bearer token."""
    # 1. Env var override
    env_token = os.environ.get("MORRIS_GRAPH_TOKEN", "").strip()
    if env_token:
        return env_token

    # 2. Static token in config (testing only)
    static = config.get("graph_token", "").strip()
    if static:
        return static

    # 3. Shell command in config
    cmd = config.get("graph_token_cmd", "").strip()
    if not cmd:
        cmd = "m365 util accesstoken get --resource https://graph.microsoft.com"

    result = subprocess.run(
        cmd, shell=True, capture_output=True, text=True, timeout=20
    )
    if result.returncode != 0:
        raise RuntimeError(f"graph_token_cmd failed: {result.stderr.strip()}")
    return result.stdout.strip().strip('"')


# ---------------------------------------------------------------------------
# Contact data
# ---------------------------------------------------------------------------

def load_contacts() -> dict:
    """Load contacts JSON, returning empty structure on missing/corrupt file."""
    if not CONTACTS_FILE.exists():
        return {"contacts": {}}
    try:
        return json.loads(CONTACTS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Could not read contacts file %s: %s", CONTACTS_FILE, exc)
        return {"contacts": {}}


def build_summary(contacts: dict) -> str:
    """Build the [BRIEFING] DM text."""
    today = datetime.now(timezone.utc).date().isoformat()
    all_contacts = contacts.get("contacts", {})

    today_contacts = [
        (email, entry)
        for email, entry in all_contacts.items()
        if entry.get("today_date") == today and entry.get("today_count", 0) > 0
    ]
    # Sort alphabetically by display_name
    today_contacts.sort(key=lambda x: x[1].get("display_name", "").lower())

    total_unique = len(all_contacts)
    new_today = sum(
        1 for _, entry in today_contacts
        if entry.get("first_seen", "")[:10] == today
    )

    lines: list[str] = [
        f"[BRIEFING] Morris Daily Contact Summary — {today}",
        "",
    ]

    if not today_contacts:
        lines.append("No one messaged Morris today.")
    else:
        lines.append("People who talked to Morris today:")
        for email, entry in today_contacts:
            name = entry.get("display_name", email)
            count = entry.get("today_count", 0)
            msg_word = "message" if count == 1 else "messages"
            lines.append(f"- {name} ({email}) — {count} {msg_word}")

    lines.append("")
    lines.append(f"Total unique contacts ever: {total_unique}")
    lines.append(f"New contacts today: {new_today}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Teams send
# ---------------------------------------------------------------------------

def send_teams_dm(chat_id: str, token: str, message: str) -> None:
    """POST a plain-text message to a Teams chat via Graph API."""
    url = f"{GRAPH_BASE}/me/chats/{chat_id}/messages"
    payload = json.dumps({"body": {"contentType": "text", "content": message}}).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            status = resp.status
            logger.info("Teams DM sent to %s… (HTTP %s)", chat_id[:20], status)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")[:400]
        raise RuntimeError(f"Graph POST failed: {exc.code} {body}") from exc


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Post daily Morris contact summary to Mark.")
    parser.add_argument(
        "--config",
        default="/opt/morris/orchestrator_config.yaml",
        help="Path to orchestrator_config.yaml",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the summary to stdout instead of sending to Teams.",
    )
    args = parser.parse_args()

    config = load_config(args.config)

    # STORY-734: Mode gate — contact summary only runs in full mode
    try:
        from mode_controller import get_mode, mode_allows
        current_mode = get_mode()
        if not mode_allows(current_mode, "contact_summary"):
            logger.info("[MODE] %s — contact summary suspended, exiting cleanly", current_mode)
            sys.exit(0)
    except ImportError:
        pass  # mode_controller not deployed yet — run unconditionally

    # Resolve Mark's chat ID
    mark_chat_id = (
        os.environ.get("MORRIS_MARK_CHAT_ID", "").strip()
        or config.get("mark_chat_id", "").strip()
    )
    if not mark_chat_id:
        logger.error(
            "mark_chat_id not set. Provide MORRIS_MARK_CHAT_ID env var "
            "or mark_chat_id in %s",
            args.config,
        )
        sys.exit(1)

    contacts = load_contacts()
    summary = build_summary(contacts)

    if args.dry_run:
        print(summary)
        return

    try:
        token = get_graph_token(config)
    except Exception as exc:
        logger.error("Could not get Graph token: %s", exc)
        sys.exit(1)

    try:
        send_teams_dm(mark_chat_id, token, summary)
        logger.info("Daily summary posted successfully.")
    except Exception as exc:
        logger.error("Failed to send summary: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
