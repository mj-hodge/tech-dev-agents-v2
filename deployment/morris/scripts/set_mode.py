#!/usr/bin/env python3
"""STORY-734: CLI to set Morris operating mode.

Usage:
    python set_mode.py light  --reason overnight_schedule
    python set_mode.py full   --reason overnight_end
    python set_mode.py minimal --reason manual_override --actor mark

Called from cron for overnight scheduling and from Teams DM handler for manual overrides.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

# ---------------------------------------------------------------------------
# Import-path bootstrap (same as orchestrator_loop.py — supports both repo
# and deployed /opt/morris/ layouts)
# ---------------------------------------------------------------------------
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from mode_controller import MODES, set_mode, get_mode, get_quota_pct

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [set-mode] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Set Morris operating mode (full / light / minimal)."
    )
    parser.add_argument(
        "mode",
        choices=MODES,
        help="Target operating mode.",
    )
    parser.add_argument(
        "--reason",
        required=True,
        help="Machine-readable reason (e.g. overnight_schedule, overnight_end, manual_override).",
    )
    parser.add_argument(
        "--actor",
        default="cron",
        help="Who triggered (cron, mark, admin). Default: cron.",
    )
    parser.add_argument(
        "--config",
        default=os.path.join(_SCRIPT_DIR, "orchestrator_config.yaml"),
        help="Path to orchestrator_config.yaml.",
    )

    args = parser.parse_args(argv)

    # Load config for Teams DM and quota context
    config = None
    session = None
    try:
        from orchestrator_loop import load_config, build_session
        config = load_config(args.config)
        session = build_session(config)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not load config/session for DM: %s", exc)

    # Get current quota for recording
    quota_pct = None
    if config is not None:
        quota_pct = get_quota_pct(config, session)

    old_mode = get_mode()
    if old_mode == args.mode:
        logger.info("Mode already %s — no change needed (reason=%s)", args.mode, args.reason)
        return

    set_mode(
        mode=args.mode,
        reason=args.reason,
        actor=args.actor,
        config=config,
        session=session,
        quota_pct=quota_pct,
    )
    logger.info("Mode changed: %s → %s (reason=%s, actor=%s)", old_mode, args.mode, args.reason, args.actor)


if __name__ == "__main__":
    main()
