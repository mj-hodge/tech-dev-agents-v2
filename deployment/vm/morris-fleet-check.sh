#!/usr/bin/env bash
# morris-fleet-check.sh — lean fleet-vigilance wrapper.
#
# Two-stage design (token-efficient):
#   Stage 1 (bash): collect ALL fleet data natively — queue state,
#                   agent SDK counts, pause flags, disk/mem, PR list,
#                   Graph errors, ghost-completion audit. ~5s runtime.
#   Stage 2 (SDK): pass the collected JSON to `claude -p` ONCE with a
#                  tight reasoning prompt. The LLM decides: silent / log /
#                  DM Mark / auto-remediate. Target: ≤5 turns.
#
# The fleet-vigilance SKILL.md still defines the semantics (CRIT/WARN/OK,
# when to DM Mark, remediation playbooks). This wrapper just pre-loads
# the data so the LLM doesn't spend 90 turns on bash exploration.
#
# Cost budget: one run ≤ 10 turns.
#
# Cron cadence (STORY-508): reduced from */30 (every 30 min, 48 runs/day) to
#   0 */2 * * *  (top of every 2nd hour, 12 runs/day — saves ~72% token cost).
# Rationale: Grafana now handles real-time alerting; fleet-check is a sanity-check
# fallback only.  Update /etc/cron.d/morris-fleet-check on the Morris VM accordingly.
#
# Old cron: */30 * * * * hermes /opt/agent/morris-fleet-check.sh
# New cron: 0 */2 * * * hermes /opt/agent/morris-fleet-check.sh
#
# Deployed to /opt/agent/morris-fleet-check.sh on Morris VM only.
# Cron: 0 */2 * * * hermes /opt/agent/morris-fleet-check.sh
#       (system cron in /etc/cron.d/morris-fleet-check)
#
# STORY-323 hardening:
#   - Master timeout (300s) so no run can outlive the 15min cron interval
#   - Stale-lock detection removes locks from hung/dead processes
#   - SSH probes wrapped in per-host timeout (30s) to prevent hangs

# ---- Master timeout (STORY-323) ---------------------------------------------
# Re-exec ourselves under timeout if not already wrapped.  This guarantees the
# lock FD is closed when timeout kills us, releasing flock for the next tick.
if [ -z "${_FLEET_CHECK_WRAPPED:-}" ]; then
    export _FLEET_CHECK_WRAPPED=1
    exec timeout --signal=TERM --kill-after=30 300 "$0" "$@"
fi

set -u
LOG=/var/log/morris-fleet-check.log
STATE=/home/hermes/state/morris/fleet-health.md
TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)

# ---- Stale-lock protection (STORY-323) --------------------------------------
# If the lock file is >10 min old and no fleet-check process is alive, the
# previous run hung without releasing flock. Remove the stale lock so this
# tick can proceed.
LOCK=/var/run/morris-fleet-check.lock
STALE_MINUTES=10

if [ -f "$LOCK" ]; then
    LOCK_AGE=$(( ( $(date +%s) - $(stat -c %Y "$LOCK" 2>/dev/null || echo 0) ) / 60 ))
    if [ "$LOCK_AGE" -gt "$STALE_MINUTES" ]; then
        # Check if any fleet-check process (other than us) is actually running
        OTHER_PIDS=$(pgrep -f "morris-fleet-check\.sh" | grep -v "$$" || true)
        if [ -z "$OTHER_PIDS" ]; then
            echo "$TS WARN: removing stale lock (age=${LOCK_AGE}m, no live process)" >> "$LOG"
            rm -f "$LOCK"
        fi
    fi
fi

# Single-fire lock so overlapping cron ticks don't trample each other
exec 200>"$LOCK" || exit 0
flock -n 200 || { echo "$TS skip — previous still running" >> "$LOG"; exit 0; }

OPS_URL="${OPS_CONSOLE_URL:-https://tech-dev-agents.gorillacommerce.ai}"
OPS_KEY="$(sudo grep -oP '(?<=OPS_CONSOLE_API_KEY=).+' /opt/agent/.env | head -1)"

DATA=$(mktemp /tmp/fleet-data.XXXXXX.json)
# CURL_CFG holds the API key so it never appears in curl argv (F-08)
CURL_CFG=$(mktemp /tmp/fleet-curl.XXXXXX)
printf 'header = "X-API-Key: %s"\n' "$OPS_KEY" > "$CURL_CFG"
trap "rm -f $DATA $CURL_CFG" EXIT

echo "$TS === fleet-check START ===" >> "$LOG"

# ---- Stage 1: data collection (native, no SDK tokens burned) --------------

# Queue
QUEUE=$(curl -sf --config "$CURL_CFG" "$OPS_URL/api/dispatch/queue?include_claimed=true" || echo '{"pending":[],"claimed":[]}')

# Per-agent health via SSH (parallel via temp files)
agent_probe() {
    local name=$1 ip=$2 outfile=$3
    ssh -p 443 -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/home/hermes/.ssh/known_hosts_agents -o ConnectTimeout=8 -o BatchMode=yes azureagent@$ip "
        SDK=\$(ps aux | grep -c '[c]laude_sdk_tool.py')
        PAUSED=\$([ -f /var/run/dispatch-poller-paused-until ] && cat /var/run/dispatch-poller-paused-until || echo '')
        POLLER=\$(sudo systemctl is-active dispatch-poller 2>/dev/null || echo 'unknown')
        AUTH=\$(sudo -u hermes claude auth status 2>&1 | grep -oE '\"loggedIn\": (true|false)' | head -1 | cut -d':' -f2 | tr -d ' ')
        QUEUE=\$(sudo -u hermes python3 -c 'import sys; sys.path.insert(0,\"/opt/agent\"); from work_queue import WorkQueue; import json; print(json.dumps(WorkQueue().list()))' 2>/dev/null || echo '[]')
        DISK=\$(df -h / | awk 'NR==2{print \$5}' | tr -d '%')
        MEM=\$(free -m | awk '/^Mem:/{printf \"%d\",\$3*100/\$2}')
        GRAPH_ERRS=\$(sudo journalctl -u hermes-gateway --since '30 min ago' --no-pager 2>/dev/null | grep -cE '502|504|Bad Gateway|Gateway Timeout')
        USAGE=\$(sudo -u hermes ccusage --period weekly --format json 2>/dev/null | tail -1 || echo '{}')
        RATE_CHURN=\$(grep -c 'RATE LIMITED' /tmp/hermes-combined.log 2>/dev/null || echo 0)
        echo \"{\\\"name\\\":\\\"$name\\\",\\\"sdk\\\":\$SDK,\\\"paused\\\":\\\"\$PAUSED\\\",\\\"poller\\\":\\\"\$POLLER\\\",\\\"auth\\\":\\\"\$AUTH\\\",\\\"queue\\\":\$QUEUE,\\\"disk_pct\\\":\$DISK,\\\"mem_pct\\\":\$MEM,\\\"graph_errs_30m\\\":\$GRAPH_ERRS,\\\"usage\\\":\\\"\$USAGE\\\",\\\"rate_limit_churn\\\":\$RATE_CHURN}\"
    " > "$outfile" 2>/dev/null || echo "{\"name\":\"$name\",\"error\":\"unreachable\"}" > "$outfile"
}

DAN_TMP=$(mktemp /tmp/fleet-dan.XXXXXX)
DERRICK_TMP=$(mktemp /tmp/fleet-derrick.XXXXXX)
DAISY_TMP=$(mktemp /tmp/fleet-daisy.XXXXXX)
DEVON_TMP=$(mktemp /tmp/fleet-devon.XXXXXX)
trap "rm -f $DATA $CURL_CFG $DAN_TMP $DERRICK_TMP $DAISY_TMP $DEVON_TMP" EXIT

agent_probe dan 20.228.224.243 "$DAN_TMP" &
agent_probe derrick 20.121.210.186 "$DERRICK_TMP" &
agent_probe daisy 20.98.231.234 "$DAISY_TMP" &
agent_probe devon 20.186.26.130 "$DEVON_TMP" &
wait

DAN_JSON=$(cat "$DAN_TMP")
DERRICK_JSON=$(cat "$DERRICK_TMP")
DAISY_JSON=$(cat "$DAISY_TMP")
DAN_JSON=${DAN_JSON:-'{"name":"dan","error":"unreachable"}'}
DERRICK_JSON=${DERRICK_JSON:-'{"name":"derrick","error":"unreachable"}'}
DAISY_JSON=${DAISY_JSON:-'{"name":"daisy","error":"unreachable"}'}
DEVON_JSON=$(cat "$DEVON_TMP")
DEVON_JSON=${DEVON_JSON:-'{"name":"devon","error":"unreachable"}'}

# Ghost-completion audit (duplicate SHA in last 2h)
GHOST_SQL="SELECT json_agg(row_to_json(t)) FROM (
  SELECT commit_sha, array_agg(story_id) AS stories, count(*) AS dupes
  FROM dispatch_items
  WHERE commit_sha IS NOT NULL AND completed_at > now() - interval '2 hours'
  GROUP BY commit_sha HAVING count(*) > 1
) t;"
GHOST=$(ssh -p 443 -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/home/hermes/.ssh/known_hosts_agents -o ConnectTimeout=8 azureagent@tech-dev-agents.gorillacommerce.ai \
    "sudo docker exec ops-console-postgres psql -U ops_console -d ops_console -tA -c \"$GHOST_SQL\"" 2>/dev/null || echo 'null')

# Failed stories not retried
FAILED_SQL="SELECT json_agg(row_to_json(t)) FROM (
  SELECT story_id, claimed_by, completed_at::text, prompt
  FROM dispatch_items WHERE status='failed' AND completed_at > now() - interval '6 hours'
) t;"
FAILED=$(ssh -p 443 -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/home/hermes/.ssh/known_hosts_agents -o ConnectTimeout=8 azureagent@tech-dev-agents.gorillacommerce.ai \
    "sudo docker exec ops-console-postgres psql -U ops_console -d ops_console -tA -c \"$FAILED_SQL\"" 2>/dev/null || echo 'null')

# Open PRs across repos — quick pass, only titles + age
PRS=$(for R in tech-dev-agents advertising-amazon product-health-dashboard tech-datawarehouse tech-gc-knowledgebase; do
    sudo -u hermes gh pr list --repo hpi-gorillacommerce/$R --state open \
        --json number,title,author,createdAt,reviewDecision 2>/dev/null | \
        python3 -c "import sys,json; [print(json.dumps({**p,'repo':'$R'})) for p in json.load(sys.stdin)]" 2>/dev/null
done | python3 -c "import sys,json; arr=[json.loads(l) for l in sys.stdin if l.strip()]; print(json.dumps(arr))")

# Morris VM health
MORRIS_DISK=$(df -h / | awk 'NR==2{print $5}' | tr -d '%')
MORRIS_MEM=$(free -m | awk '/^Mem:/{printf "%d",$3*100/$2}')

# Assemble one consolidated JSON blob
python3 - >"$DATA" <<PYEOF
import json, sys
data = {
    "ts": "$TS",
    "queue": json.loads('''$QUEUE'''),
    "dan": json.loads('''$DAN_JSON'''),
    "derrick": json.loads('''$DERRICK_JSON'''),
    "daisy": json.loads('''$DAISY_JSON'''),
    "devon": json.loads('''$DEVON_JSON'''),
    "morris": {"name": "morris", "disk_pct": $MORRIS_DISK, "mem_pct": $MORRIS_MEM},
    "ghost_completions_2h": json.loads('''$GHOST''' or 'null'),
    "failed_stories_6h": json.loads('''$FAILED''' or 'null'),
    "open_prs": json.loads('''$PRS''' or '[]'),
}
json.dump(data, sys.stdout, indent=2)
PYEOF

echo "$TS data-collection done, $(wc -c <"$DATA") bytes" >> "$LOG"

# ---- STORY-734: Mode gate — skip Claude invocation in light/minimal modes ---
FLEET_MODE_ALLOWED=$(python3 -c "
import sys; sys.path.insert(0, '/opt/morris')
from mode_controller import get_mode, mode_allows
print('yes' if mode_allows(get_mode(), 'fleet_check_llm') else 'no')
" 2>/dev/null || echo "yes")

if [ "$FLEET_MODE_ALLOWED" = "no" ]; then
    FLEET_CURRENT_MODE=$(python3 -c "
import sys; sys.path.insert(0, '/opt/morris')
from mode_controller import get_mode
print(get_mode())
" 2>/dev/null || echo "unknown")
    echo "$TS [MODE] $FLEET_CURRENT_MODE — fleet-check Claude call suppressed" >> "$LOG"
    echo "$TS === fleet-check DONE (mode-gated) ===" >> "$LOG"
    exit 0
fi

# ---- Stage 2: SDK reasoning — 1 consolidated prompt, tight turn cap -------

SKILL_FILE=/home/hermes/.hermes/skills/management/fleet-vigilance/SKILL.md

# Run from hermes home so sandbox includes home dir.
# --add-dir grants access to /tmp (data blob) and state dir (output).
cd /home/hermes
sudo -u hermes timeout 180 claude -p \
    --model claude-sonnet-4-6 \
    "You are Morris running the fleet-vigilance skill. The wrapper has already
collected all fleet data in $DATA — read that file ONCE, then apply the
fleet-vigilance skill rules (loaded from $SKILL_FILE)
to classify each check as CRIT/WARN/OK.

Then do EXACTLY these things and nothing else:
  1. Write a timestamped snapshot to $STATE (overwrite; the skill file
     specifies the format). ONLY if status changed from last check — skip
     if nothing changed.
  2. If any check is CRIT, compose a concise Teams DM to Mark (1-2 short
     paragraphs — what happened, what you did, what's left). Post it via
     the Teams adapter. If Teams is unreachable, write the draft to
     /home/hermes/state/morris/pending-dm.md.
  3. For any auto-remediable issue (stale local queue entry on a dev
     agent, for example), execute the remediation per the skill.

HARD RULES:
- Do NOT re-collect any data — the wrapper already gathered everything.
- Do NOT ssh into agents again. Do NOT query the queue API. Work from \$DATA.
- NEVER run git add, git commit, or git push. Fleet health is NOT source code.
- If everything is OK and nothing changed, exit immediately with zero turns.

If everything is OK, just update the state file and exit silently." \
    --max-turns 5 \
    --permission-mode bypassPermissions \
    --add-dir /tmp \
    --add-dir /home/hermes/state/morris \
    --add-dir /home/hermes/.hermes/skills/management/fleet-vigilance \
    >>"$LOG" 2>&1
RC=$?

echo "$TS === fleet-check DONE (rc=$RC) ===" >> "$LOG"
