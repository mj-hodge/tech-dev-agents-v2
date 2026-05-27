#!/usr/bin/env bash
# Morris's controlled-write path for the hermes crontab.
#
# Why this exists: Morris's terminal guard blocks `crontab -e` and other
# direct edits. Even when allowed, direct edits leave no audit trail. This
# script wraps every change with: validation, atomic snapshot, JSONL audit
# log, and rollback support. Hermes can edit hermes's own crontab — no
# sudo required.
#
# Usage (run as hermes):
#   manage-crontab.sh list
#   manage-crontab.sh add "<schedule>" "<command>" ["<comment>"]
#   manage-crontab.sh disable <job_name_or_substring>
#   manage-crontab.sh enable  <job_name_or_substring>
#   manage-crontab.sh remove  <job_name_or_substring>
#   manage-crontab.sh history [N]                   # last N changes (default 20)
#   manage-crontab.sh rollback <snapshot_id>        # restore a prior snapshot
#
# Schedules and commands MUST be quoted; this script does NOT parse cron
# fields beyond passing them through. After every applied change, the
# script triggers cron_inventory.py so cron-inventory.md reflects the
# new state immediately.

set -euo pipefail

STATE_DIR="${HOME}/state/morris"
HISTORY_DIR="${STATE_DIR}/crontab-history"
CHANGES_LOG="${STATE_DIR}/crontab-changes.jsonl"
INVENTORY_SCRIPT="${HOME}/.hermes/scripts/cron_inventory.py"

mkdir -p "$HISTORY_DIR" "$STATE_DIR"

now_iso() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
now_compact() { date -u +"%Y%m%dT%H%M%SZ"; }

current_crontab() {
  # crontab -l exits 1 if no crontab — treat as empty
  crontab -l 2>/dev/null || true
}

snapshot() {
  local id="$1"
  local path="${HISTORY_DIR}/${id}.crontab"
  current_crontab > "$path"
  echo "$path"
}

audit() {
  # JSON one-liner audit append
  local action="$1" detail="$2" snap="$3"
  python3 - "$action" "$detail" "$snap" "$CHANGES_LOG" <<'PY'
import json, os, sys
action, detail, snap, log = sys.argv[1:5]
import datetime as dt
entry = {
    "ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    "action": action,
    "detail": detail,
    "snapshot": os.path.basename(snap) if snap else None,
    "user": os.environ.get("USER", "?"),
}
with open(log, "a") as f:
    f.write(json.dumps(entry) + "\n")
PY
}

apply() {
  # apply takes the *new* crontab on stdin, validates, swaps it in
  local action="$1" detail="$2"
  local id; id=$(now_compact)
  local before_snap; before_snap=$(snapshot "before_${id}")
  local new_tmp; new_tmp=$(mktemp)
  trap 'rm -f "$new_tmp"' RETURN

  cat > "$new_tmp"

  # Basic validation: every non-comment, non-empty line should have at least 6 whitespace-separated fields.
  local bad
  bad=$(awk 'BEGIN{rc=0} /^[^#]/ && NF>0 && NF<6 { print NR": "$0; rc=1 } END{exit rc}' "$new_tmp" || true)
  if [ -n "$bad" ]; then
    echo "ERROR: malformed cron lines:" >&2
    echo "$bad" >&2
    rm -f "$new_tmp"
    return 2
  fi

  # crontab itself will validate field semantics on load
  if ! crontab "$new_tmp"; then
    echo "ERROR: crontab rejected the new file. Snapshot at $before_snap" >&2
    return 3
  fi

  local after_snap; after_snap=$(snapshot "after_${id}")
  audit "$action" "$detail" "$after_snap"
  echo "Applied: $action / $detail"
  echo "Snapshot before: $before_snap"
  echo "Snapshot after:  $after_snap"

  # Refresh the inventory so cron-inventory.md is up-to-date right away
  if [ -x "$INVENTORY_SCRIPT" ]; then
    "$INVENTORY_SCRIPT" >/dev/null 2>&1 || true
  fi
}

cmd_list() {
  current_crontab
}

cmd_add() {
  local schedule="$1" command="$2" comment="${3:-}"
  if [ -z "$schedule" ] || [ -z "$command" ]; then
    echo "Usage: manage-crontab.sh add \"<schedule>\" \"<command>\" [\"<comment>\"]" >&2
    return 64
  fi
  local payload
  payload=$(current_crontab)
  {
    echo "$payload"
    echo
    if [ -n "$comment" ]; then echo "# $comment"; fi
    echo "$schedule $command"
  } | apply "add" "schedule=[$schedule] command=[$command]"
}

cmd_disable() {
  local match="$1"
  if [ -z "$match" ]; then
    echo "Usage: manage-crontab.sh disable <job_name_or_substring>" >&2
    return 64
  fi
  current_crontab | python3 - "$match" <<'PY' | apply "disable" "match=$1"
import sys
match = sys.argv[1]
out = []
hits = 0
for line in sys.stdin:
    line = line.rstrip("\n")
    if (not line.startswith("#")) and (match in line) and line.strip():
        out.append("#PAUSED# " + line)
        hits += 1
    else:
        out.append(line)
if hits == 0:
    sys.stderr.write(f"no matches for {match!r}\n")
    sys.exit(5)
sys.stdout.write("\n".join(out) + "\n")
PY
}

cmd_enable() {
  local match="$1"
  if [ -z "$match" ]; then
    echo "Usage: manage-crontab.sh enable <job_name_or_substring>" >&2
    return 64
  fi
  current_crontab | python3 - "$match" <<'PY' | apply "enable" "match=$1"
import sys
match = sys.argv[1]
out = []
hits = 0
for line in sys.stdin:
    line = line.rstrip("\n")
    stripped = line[len("#PAUSED# "):] if line.startswith("#PAUSED# ") else None
    if stripped is not None and match in stripped:
        out.append(stripped)
        hits += 1
    else:
        out.append(line)
if hits == 0:
    sys.stderr.write(f"no #PAUSED# matches for {match!r}\n")
    sys.exit(5)
sys.stdout.write("\n".join(out) + "\n")
PY
}

cmd_remove() {
  local match="$1"
  if [ -z "$match" ]; then
    echo "Usage: manage-crontab.sh remove <job_name_or_substring>" >&2
    return 64
  fi
  current_crontab | python3 - "$match" <<'PY' | apply "remove" "match=$1"
import sys
match = sys.argv[1]
out = []
hits = 0
for line in sys.stdin:
    line = line.rstrip("\n")
    if line.strip() and not line.lstrip().startswith("# ") and match in line:
        # Active or #PAUSED# job line — drop it
        hits += 1
        continue
    out.append(line)
if hits == 0:
    sys.stderr.write(f"no matches for {match!r}\n")
    sys.exit(5)
sys.stdout.write("\n".join(out) + "\n")
PY
}

cmd_history() {
  local n="${1:-20}"
  if [ ! -f "$CHANGES_LOG" ]; then
    echo "(no changes recorded yet)"
    return 0
  fi
  tail -n "$n" "$CHANGES_LOG" | python3 -c '
import json, sys
for line in sys.stdin:
    line = line.strip()
    if not line: continue
    try:
        d = json.loads(line)
        print(f"{d.get(\"ts\",\"?\"):<22} {d.get(\"action\",\"?\"):<10} {d.get(\"detail\",\"\")[:80]:<80} snap={d.get(\"snapshot\")}")
    except Exception:
        print(line)
'
}

cmd_rollback() {
  local snap_id="$1"
  if [ -z "$snap_id" ]; then
    echo "Usage: manage-crontab.sh rollback <snapshot_id>" >&2
    echo "Available snapshots:" >&2
    ls -1t "$HISTORY_DIR" 2>/dev/null | head -20 >&2
    return 64
  fi
  local snap_path="${HISTORY_DIR}/${snap_id}"
  [ -f "$snap_path" ] || snap_path="${HISTORY_DIR}/${snap_id}.crontab"
  if [ ! -f "$snap_path" ]; then
    echo "ERROR: snapshot not found: $snap_id (looked in $HISTORY_DIR)" >&2
    return 1
  fi
  cat "$snap_path" | apply "rollback" "snapshot=$snap_id"
}

usage() {
  sed -n '2,30p' "$0"
}

main() {
  local action="${1:-}"
  shift || true
  case "$action" in
    list)     cmd_list "$@" ;;
    add)      cmd_add "$@" ;;
    disable)  cmd_disable "$@" ;;
    enable)   cmd_enable "$@" ;;
    remove)   cmd_remove "$@" ;;
    history)  cmd_history "$@" ;;
    rollback) cmd_rollback "$@" ;;
    ""|-h|--help) usage ;;
    *) echo "unknown action: $action" >&2; usage; exit 64 ;;
  esac
}

main "$@"
