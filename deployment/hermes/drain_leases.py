#!/usr/bin/env python3
"""drain_leases.py — release any active dispatch v2 leases on shutdown.

STORY-857: Invoked by systemd ExecStop on dispatch-poller.service so a
restart never orphans an in-flight lease. Reads the active-lease state file
written by dispatch_poller_v2.py and POSTs /api/dispatch/v2/release for each
lease_token. Idempotent — a stale lease (409) or missing job (404) are both
treated as "already released" and do not fail the drain.

Why this exists (2026-05-03 incident):
push-code.sh runs `systemctl restart dispatch-poller` while the v2 poller is
mid-claim. Without ExecStop drain, the lease orphans until the (broken)
expired-lease sweeper notices — meanwhile retries spin up to 3 attempts and
the story lands in attention_queue with phase_runner_crash.

Behaviour:
  - Reads ACTIVE_LEASE_PATH (default /var/run/dispatch-poller/active_lease.json,
    falls back to /tmp/dispatch-poller-active-lease.json).
  - Supports a single-lease (dict) or multi-lease (list of dicts) JSON shape.
  - POSTs /api/dispatch/v2/release for each entry.
  - Total wall-clock budget: DRAIN_TIMEOUT_SECONDS (default 30).
  - Always exits 0 (best-effort drain — never block systemd from killing the
    service if the API is unreachable; a stuck lease is no worse than the
    pre-STORY-857 state).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from typing import Any, Iterable

import requests

logger = logging.getLogger("drain_leases")

DEFAULT_PRIMARY_PATH = "/var/run/dispatch-poller/active_lease.json"
DEFAULT_FALLBACK_PATH = "/tmp/dispatch-poller-active-lease.json"
DEFAULT_TIMEOUT = 30  # seconds
V2_BASE = "/api/dispatch/v2"


def _candidate_paths() -> list[str]:
    """Return the ordered list of state-file paths to check."""
    explicit = os.environ.get("ACTIVE_LEASE_PATH")
    if explicit:
        return [explicit]
    return [DEFAULT_PRIMARY_PATH, DEFAULT_FALLBACK_PATH]


def _load_leases(paths: Iterable[str]) -> list[dict[str, Any]]:
    """Load any leases recorded in the first existing state file.

    The state file is either:
      - A single-lease dict: {"job_id": ..., "lease_token": ...}
      - A list of such dicts (future-proofing for multi-claim agents).

    Returns an empty list if no file exists, the file is empty, or it cannot
    be parsed.
    """
    for path in paths:
        if not path or not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = fh.read().strip()
            if not raw:
                return []
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("drain_leases: failed to read %s: %s", path, exc)
            continue
        if isinstance(data, dict):
            return [data]
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        logger.warning("drain_leases: unexpected JSON shape in %s: %r", path, type(data))
        return []
    return []


def _build_headers() -> dict[str, str]:
    """Build the auth + worker-version headers for the release POST."""
    return {
        "X-API-Key": os.environ.get("OPS_CONSOLE_API_KEY", ""),
        "X-Worker-Version": os.environ.get("WORKER_VERSION", "2.0"),
        "X-Agent-Name": os.environ.get("AGENT_NAME", "unknown-agent"),
        "X-Agent-Role": os.environ.get("AGENT_ROLE", "developer"),
        "Content-Type": "application/json",
    }


def _base_url() -> str:
    return os.environ.get("OPS_CONSOLE_URL", "http://localhost:8005").rstrip("/")


def release_one(
    lease: dict[str, Any],
    *,
    session: requests.Session,
    headers: dict[str, str],
    deadline: float,
) -> bool:
    """Release a single lease. Returns True on success or "already released"."""
    job_id = lease.get("job_id")
    lease_token = lease.get("lease_token")
    if not job_id or not lease_token:
        logger.warning("drain_leases: skipping malformed lease entry: %r", lease)
        return False

    remaining = max(1.0, deadline - time.monotonic())
    url = f"{_base_url()}{V2_BASE}/release"
    payload = {
        "job_id": job_id,
        "lease_token": lease_token,
        "reason": "ExecStop drain (STORY-857)",
    }
    try:
        resp = session.post(url, json=payload, headers=headers, timeout=remaining)
    except requests.RequestException as exc:
        logger.warning(
            "drain_leases: release POST failed job_id=%s: %s",
            job_id, exc,
        )
        return False

    if resp.status_code == 200:
        logger.info("drain_leases: released job_id=%s", job_id)
        return True
    # Idempotent: 409 stale lease and 404 job not found both mean "not held
    # any more" — drain succeeded for our purposes.
    if resp.status_code in (404, 409):
        logger.info(
            "drain_leases: job_id=%s already released (status=%d)",
            job_id, resp.status_code,
        )
        return True
    logger.warning(
        "drain_leases: unexpected release status %d for job_id=%s: %s",
        resp.status_code, job_id, resp.text[:200],
    )
    return False


def _delete_state_files(paths: Iterable[str]) -> None:
    """Best-effort delete of any state files we read from."""
    for path in paths:
        if not path:
            continue
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError as exc:
            logger.warning("drain_leases: could not remove %s: %s", path, exc)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s drain_leases %(levelname)s %(message)s",
    )

    timeout = float(os.environ.get("DRAIN_TIMEOUT_SECONDS", DEFAULT_TIMEOUT))
    deadline = time.monotonic() + timeout

    paths = _candidate_paths()
    leases = _load_leases(paths)
    if not leases:
        logger.info("drain_leases: no active leases recorded — nothing to do")
        return 0

    logger.info("drain_leases: draining %d lease(s) (timeout=%.0fs)", len(leases), timeout)
    session = requests.Session()
    headers = _build_headers()

    released = 0
    for lease in leases:
        if time.monotonic() >= deadline:
            logger.warning("drain_leases: deadline reached, stopping")
            break
        if release_one(lease, session=session, headers=headers, deadline=deadline):
            released += 1

    logger.info(
        "drain_leases: released %d/%d lease(s)", released, len(leases),
    )
    _delete_state_files(paths)
    # Always exit 0 — best-effort. systemd should never be blocked from
    # killing the service by a flaky API call.
    return 0


if __name__ == "__main__":
    sys.exit(main())
