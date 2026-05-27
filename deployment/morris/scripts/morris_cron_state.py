#!/usr/bin/env python3
"""
Append cron run results to /home/hermes/state/morris/cron-status.md.
This is a STATE FILE that Morris reads on session start — NOT memory.
Keeps last 500 entries max. Older entries are trimmed automatically.

Called by run_cron.sh after each cron completes.

Usage:
    morris_cron_state.py append <job_name> <status> <summary_line>
    morris_cron_state.py trim

Failure logging: any exception inside append() / trim() is captured to
ERROR_LOG with full traceback. run_cron.sh wraps the call with `|| true`,
so without this log a silent failure leaves no trace — that was the cause
of the 2026-04-30 → 2026-05-05 cron-status.md gap.
"""
import datetime
import fcntl
import re
import sys
import traceback
from pathlib import Path

STATE_PATH = Path("/home/hermes/state/morris/cron-status.md")
LOCK_PATH = Path("/home/hermes/state/morris/cron-status.md.lock")
ERROR_LOG = Path("/home/hermes/state/morris/morris_cron_state.log")
MAX_ENTRIES = 500


def _now_utc():
    return datetime.datetime.now(datetime.timezone.utc)


def _parse_entries(text):
    """Return (header, entries) where entries are lines starting with '- '."""
    lines = text.strip().splitlines()
    header_lines = []
    entries = []
    in_entries = False
    for line in lines:
        if line.startswith("- "):
            in_entries = True
            entries.append(line)
        elif not in_entries:
            header_lines.append(line)
    return "\n".join(header_lines), entries


def _with_lock(fn):
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCK_PATH, "a+") as lockf:
        fcntl.flock(lockf, fcntl.LOCK_EX)
        try:
            return fn()
        finally:
            fcntl.flock(lockf, fcntl.LOCK_UN)


def append(job_name, status, summary):
    def _do():
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        text = STATE_PATH.read_text() if STATE_PATH.exists() else ""

        ts = _now_utc().strftime("%Y-%m-%dT%H:%MZ")
        new_entry = f"- {ts} {job_name}: [{status}] {summary}"

        header, entries = _parse_entries(text)
        if not header:
            header = f"# Cron Status — Last Updated: {ts}\n\nRecent cron run results (newest first, max {MAX_ENTRIES}):"

        # Update header timestamp
        header = re.sub(
            r"Last Updated: \S+",
            f"Last Updated: {ts}",
            header
        )

        # Prepend new entry, trim to MAX_ENTRIES
        entries.insert(0, new_entry)
        entries = entries[:MAX_ENTRIES]

        new_text = header + "\n\n" + "\n".join(entries) + "\n"
        STATE_PATH.write_text(new_text)

    _with_lock(_do)


def trim():
    def _do():
        if not STATE_PATH.exists():
            return
        text = STATE_PATH.read_text()
        header, entries = _parse_entries(text)
        if len(entries) > MAX_ENTRIES:
            entries = entries[:MAX_ENTRIES]
            new_text = header + "\n\n" + "\n".join(entries) + "\n"
            STATE_PATH.write_text(new_text)
    _with_lock(_do)


def _log_failure(cmd, args, exc):
    """Append a one-line error stanza so silent failures become visible."""
    try:
        ERROR_LOG.parent.mkdir(parents=True, exist_ok=True)
        ts = _now_utc().isoformat(timespec="seconds")
        with ERROR_LOG.open("a") as f:
            f.write(f"=== {ts} cmd={cmd} args={args!r} ===\n")
            traceback.print_exception(type(exc), exc, exc.__traceback__, file=f)
            f.write("\n")
    except Exception:
        pass  # last-ditch — never crash the wrapper


def main():
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    cmd = sys.argv[1]
    args = sys.argv[2:]
    try:
        if cmd == "append":
            if len(args) < 3:
                print("usage: morris_cron_state.py append <job_name> <status> <summary>", file=sys.stderr)
                sys.exit(2)
            append(args[0], args[1], args[2])
        elif cmd == "trim":
            trim()
        else:
            print(f"unknown command: {cmd}", file=sys.stderr)
            sys.exit(2)
    except Exception as e:
        _log_failure(cmd, args, e)
        # Re-raise with non-zero exit so even though run_cron.sh swallows it,
        # the ERROR_LOG entry is durable.
        print(f"morris_cron_state.py FAILED: {e!r}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
