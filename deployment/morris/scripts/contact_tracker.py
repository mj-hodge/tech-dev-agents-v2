"""Morris contact tracker — atomic JSON store for contact interactions.

Records every contact who messages Morris, tracks daily message counts,
and flags first-time contacts.

Data file: /opt/morris/data/contacts.json (configurable via MORRIS_CONTACTS_FILE env var)

Schema:
{
  "contacts": {
    "<email>": {
      "display_name": "...",
      "first_seen": "2026-04-26T08:00:00+00:00",
      "last_seen": "2026-04-26T08:00:00+00:00",
      "total_messages": 12,
      "today_date": "2026-04-26",
      "today_count": 3
    }
  }
}
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import tempfile
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

CONTACTS_FILE = pathlib.Path(
    os.environ.get("MORRIS_CONTACTS_FILE", "/opt/morris/data/contacts.json")
)


def load_contacts() -> dict:
    """Load contacts from disk, resetting today_count if the date has changed.

    Returns an empty contacts dict if the file does not exist or is corrupt.
    """
    if not CONTACTS_FILE.exists():
        return {"contacts": {}}

    try:
        data = json.loads(CONTACTS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("contact_tracker: could not load %s: %s — starting fresh", CONTACTS_FILE, exc)
        return {"contacts": {}}

    today = datetime.now(timezone.utc).date().isoformat()
    contacts = data.get("contacts", {})
    for entry in contacts.values():
        if entry.get("today_date") != today:
            entry["today_date"] = today
            entry["today_count"] = 0

    return {"contacts": contacts}


def save_contacts(contacts: dict) -> None:
    """Write contacts to disk atomically (write-then-rename).

    Raises OSError if the parent directory is not writable.
    """
    CONTACTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=CONTACTS_FILE.parent, prefix=".contacts_tmp_", suffix=".json"
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            json.dump(contacts, fh, indent=2, ensure_ascii=False)
        os.replace(tmp_path, CONTACTS_FILE)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def record_contact(email: str, display_name: str) -> bool:
    """Record a contact interaction.

    Args:
        email: The sender's email address (lower-cased before storage).
        display_name: The sender's display name from Teams.

    Returns:
        True if this is the first ever message from this contact, False otherwise.
    """
    email = email.lower().strip()
    if not email:
        logger.warning("contact_tracker: record_contact called with empty email — skipping")
        return False

    now = datetime.now(timezone.utc)
    today = now.date().isoformat()
    now_iso = now.isoformat()

    data = load_contacts()
    contacts = data["contacts"]

    is_new = email not in contacts

    if is_new:
        contacts[email] = {
            "display_name": display_name,
            "first_seen": now_iso,
            "last_seen": now_iso,
            "total_messages": 1,
            "today_date": today,
            "today_count": 1,
        }
        logger.info("contact_tracker: new contact %s (%s)", display_name, email)
    else:
        entry = contacts[email]
        # Reset daily counter if the date changed (may not have been caught by load)
        if entry.get("today_date") != today:
            entry["today_date"] = today
            entry["today_count"] = 0
        entry["display_name"] = display_name  # keep name current
        entry["last_seen"] = now_iso
        entry["total_messages"] = entry.get("total_messages", 0) + 1
        entry["today_count"] = entry.get("today_count", 0) + 1

    try:
        save_contacts(data)
    except OSError as exc:
        logger.error("contact_tracker: failed to save contacts: %s", exc)

    return is_new
