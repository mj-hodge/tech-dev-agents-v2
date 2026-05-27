#!/usr/bin/env bash
# push-code.sh — push updated code to running agent VMs and restart services.
#
# This is the LIGHTWEIGHT deploy for code changes on existing VMs.
# For full VM provisioning, use deploy-agent.sh instead.
#
# Usage:
#   ./push-code.sh [--force] [--wait [N]] <agent-name|all> [agent-name...]
#
#   ./push-code.sh dan              # push to Dan only (safe: skips restart if SDK active)
#   ./push-code.sh all              # push to all dev agents
#   ./push-code.sh dan derrick      # push to specific agents
#   ./push-code.sh --force dan      # force restart even if SDK mid-story
#   ./push-code.sh --wait 5 dan     # wait up to 5 min for SDK to finish, then restart
#   ./push-code.sh --wait dan       # wait up to 30 min (default) for SDK to finish
#
# Modes:
#   Default (safe):  If claude_sdk_tool.py is running on an agent, skip the
#                    restart and warn. Files are still copied — the next natural
#                    restart picks up the new code.
#   --force:         Restart even if SDK is running. Use for emergency hotfixes.
#                    Also: FORCE_RESTART=1 env var.
#   --wait [N]:      Wait up to N minutes (default 30) for SDK to finish, then
#                    restart. Polls every PUSH_CODE_POLL_INTERVAL seconds (default 60).
#                    Also: WAIT_FOR_IDLE=N env var.
#
# Exit codes:
#   0  — all target VMs deployed + restarted cleanly (or files-only with warning)
#   1  — actual failures (scp error, hash mismatch, smoke test failure)
#   2  — --wait timeout on one or more VMs (SDK still running at deadline)
#
# What it does:
#   1. Copies phase runner + dispatch poller to /opt/agent/
#   2. Verifies file hashes match source
#   3. Checks for active SDK process (claude_sdk_tool.py) — see modes above
#   4. Restarts the dispatch-poller (systemd or nohup fallback)
#   5. Waits for fresh startup log line
#   6. Runs smoke test (import + claude CLI + error scan)
#   7. Reports success/failure per agent
#
# WHY THIS EXISTS (2026-04-20 post-mortem):
# On 2026-04-18 we scp'd new files but never restarted the pollers.
# The running Python processes kept the old cached modules. Both agents
# burned their entire weekly token allotment on the old 7-phase code
# (including the rate-limit retry bug we'd already fixed in the new code).
# This script ensures deploy = copy + restart + verify. No exceptions.
#
# WHY THE SDK CHECK EXISTS (2026-04-21 post-mortem):
# On 2026-04-21, two deploys killed Daisy mid-story (STORY-507, STORY-508)
# by sending SIGTERM to the running claude_sdk_tool.py subprocess. STORY-507's
# SIGTERM handler (AC-4) saved partial state and resume mode recovered — but
# each interruption burned 5+ minutes on re-claim/re-exploration and inflated
# the apparent dispatch failure rate. Default mode now checks before restarting.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

SSH_OPTS="-p 443 -o StrictHostKeyChecking=no -o ConnectTimeout=10 -o ServerAliveInterval=5"

# ---------------------------------------------------------------------------
# Deploy-source guards (added 2026-04-25 after Morris incident)
#
# On 2026-04-25 01:02 UTC, Morris's autonomous review cycle had just done
# `pull origin main` then `checkout fix/phase-gate-retry-skip` (a stale
# branch). Some part of his pipeline then ran push-code.sh from that
# working tree. The stale branch's dispatch_poller.py overwrote PR #116
# code on Daisy/Devon/Derrick — silently dropping the rework_of plumbing
# and reintroducing the cross_story_reference 422 retry loop.
#
# Refuse to run when:
#   1. HEAD is not on main (you're on a feature/review branch)
#   2. The working tree has uncommitted changes
#
# Override either with ALLOW_DIRTY=1 — for the rare case of a deliberate
# one-agent test of an unreleased branch before merge. Leaves a trace in
# the deploy log so the override is visible.
# ---------------------------------------------------------------------------

if [ "${ALLOW_DIRTY:-0}" != "1" ]; then
    _branch="$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
    if [ "$_branch" != "main" ]; then
        echo "[push-code] ABORT: branch guard — HEAD is on '$_branch', not 'main'." >&2
        echo "[push-code] To deploy from a feature branch (testing only), set ALLOW_DIRTY=1." >&2
        echo "[push-code] Background: 2026-04-25 01:02 UTC Morris incident — stale-branch deploy" >&2
        echo "[push-code] silently overwrote PR #116 code on three agents." >&2
        exit 1
    fi
    if [ -n "$(git -C "$REPO_ROOT" status --porcelain 2>/dev/null)" ]; then
        echo "[push-code] ABORT: dirty tree guard — uncommitted changes detected." >&2
        echo "[push-code] Either commit/stash or set ALLOW_DIRTY=1 if intentional." >&2
        git -C "$REPO_ROOT" status --short >&2
        exit 1
    fi
else
    echo "[push-code] WARNING: ALLOW_DIRTY=1 — skipping branch+tree guards." >&2
    echo "[push-code]   branch=$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null)" >&2
    echo "[push-code]   dirty=$(git -C "$REPO_ROOT" status --porcelain 2>/dev/null | wc -l) files" >&2
fi

# STORY-920: Fleet-wide rollback — set USE_LEGACY_CLAUDE_SDK_TOOL=1 in systemd unit on all VMs.
# Usage: ROLLBACK=1 ./push-code.sh all
ROLLBACK="${ROLLBACK:-0}"

# SDK-safety controls (overridable via env or CLI flags)
FORCE_RESTART="${FORCE_RESTART:-0}"
WAIT_FOR_IDLE="${WAIT_FOR_IDLE:-}"
# Poll interval for --wait mode. Default 60s. Override in tests: PUSH_CODE_POLL_INTERVAL=1
PUSH_CODE_POLL_INTERVAL="${PUSH_CODE_POLL_INTERVAL:-60}"

# Source agent IPs from registry
declare -A AGENT_IPS
while IFS= read -r line; do
    name=$(echo "$line" | cut -d'|' -f1)
    ip=$(echo "$line" | cut -d'|' -f2)
    AGENT_IPS[$name]=$ip
done < <(python3 -c "
import json
with open('$SCRIPT_DIR/agent-registry.json') as f:
    for a in json.load(f)['agents']:
        print(f\"{a['name']}|{a['ip']}\")
" 2>/dev/null)

# Files to push
PHASE_RUNNER="$REPO_ROOT/deployment/hermes/sdlc_phase_runner.py"
DISPATCH_POLLER="$REPO_ROOT/deployment/hermes/dispatch_poller.py"
# Epic-Queue-v2: v2 poller selected by run_dispatch_poller.py when
# DISPATCH_PROTOCOL=v2. Missed initially → cutover hot-loop on 2026-05-03.
DISPATCH_POLLER_V2="$REPO_ROOT/deployment/hermes/dispatch_poller_v2.py"
ADV_REVIEWER="$REPO_ROOT/deployment/hermes/adversarial_reviewer.py"
POLLER_WRAPPER="$SCRIPT_DIR/run_dispatch_poller.py"
TERMINAL_GUARD="$SCRIPT_DIR/terminal_guard.py"
# SDK tool — STORY-511 2026-04-22: schema-robust session_id capture. Missing
# from the deploy list for ages meant SDK changes required manual scp, and the
# overnight session-resume fix (pulling [SESSION] from any message, not just
# system/init) would never have reached the fleet via push-code.sh.
SDK_TOOL="$SCRIPT_DIR/claude_sdk_tool.py"
# STORY-920: legacy SDK tool retained for USE_LEGACY_CLAUDE_SDK_TOOL=1 rollback.
SDK_TOOL_LEGACY="$SCRIPT_DIR/claude_sdk_tool_legacy.py"
# project_file — STORY-524 2026-04-22: .project tracking file updater. Must be
# deployed alongside sdlc_phase_runner.py so that the flat-path fallback import
# ('from project_file import update_story_status') resolves on agent VMs where
# sys.path is ['/opt/agent/'] and the package-style import always fails.
PROJECT_FILE="$REPO_ROOT/deployment/hermes/project_file.py"
# STORY-644: declarative VM-state convergence — converge engine + config + wrapper.
CONVERGE_PY="$SCRIPT_DIR/converge.py"
CANONICAL_STATE="$SCRIPT_DIR/canonical-state.yaml"
RUN_CONVERGE="$SCRIPT_DIR/run-converge.sh"
AGENT_REGISTRY="$SCRIPT_DIR/agent-registry.json"
# STORY-857: ExecStop drain — releases active dispatch v2 leases before
# SIGKILL so a `systemctl restart` mid-claim never orphans in-flight work.
DRAIN_LEASES="$REPO_ROOT/deployment/hermes/drain_leases.py"
# STORY-857: matching systemd unit (with ExecStop + KillMode=mixed). Deployed
# to /etc/systemd/system/dispatch-poller.service when the on-VM file does not
# already match (idempotent — daemon-reload only when content changed).
SYSTEMD_UNIT="$SCRIPT_DIR/systemd/dispatch-poller.service"

to_int() {
    # Normalize noisy command output (e.g. "0\n0") to a safe integer.
    local raw="${1:-}"
    raw="$(printf "%s" "$raw" | tr -cd '0-9')"
    if [ -z "$raw" ]; then
        echo 0
    else
        echo "$raw"
    fi
}

deploy_one() {
    local name=$1
    local ip=${AGENT_IPS[$name]:-""}
    if [ -z "$ip" ]; then
        echo "[$name] ERROR: unknown agent (not in registry)"
        return 1
    fi

    echo "[$name] Deploying to $ip..."

    # Step 0: Clear stale /tmp staging files. A prior failed deploy can leave
    # /tmp/<name>.py owned by hermes (or root) — azureagent then can't overwrite
    # it on the next scp and the run dies with "dest open: Permission denied".
    # 2026-04-26 incident: dan got stuck at file 6 of 10 because a 2026-04-19
    # /tmp/project_file.py was still hermes-owned.
    # STORY-920: ROLLBACK=1 injects USE_LEGACY_CLAUDE_SDK_TOOL=1 into systemd unit
    if [ "${ROLLBACK:-0}" == "1" ]; then
        echo "[$name] ROLLBACK=1: injecting USE_LEGACY_CLAUDE_SDK_TOOL=1 into dispatch-poller.service..."
        ssh $SSH_OPTS "azureagent@$ip" "
            sudo grep -q USE_LEGACY_CLAUDE_SDK_TOOL /etc/systemd/system/dispatch-poller.service \
                || sudo bash -c 'echo Environment=USE_LEGACY_CLAUDE_SDK_TOOL=1 >> /etc/systemd/system/dispatch-poller.service'
            sudo systemctl daemon-reload
            sudo systemctl restart dispatch-poller
        " 2>/dev/null && echo "[$name]   rollback active (USE_LEGACY_CLAUDE_SDK_TOOL=1)" \
            || echo "[$name]   WARNING: rollback inject failed (check systemd)"
        return 0
    fi

    # STORY-920: upgrade anthropic SDK before copying files (cache_control ≥0.40 required)
    echo "[$name]   Upgrading anthropic SDK..."
    ssh $SSH_OPTS "azureagent@$ip" \
        "sudo pip3 install --upgrade anthropic --break-system-packages -q 2>&1 | tail -1" 2>/dev/null \
        && echo "[$name]   anthropic SDK ready" \
        || echo "[$name]   WARNING: anthropic upgrade failed (check version on VM)"

    local _tmp_files=""
    for f in "$PHASE_RUNNER" "$DISPATCH_POLLER" "$DISPATCH_POLLER_V2" "$ADV_REVIEWER" "$POLLER_WRAPPER" "$TERMINAL_GUARD" "$SDK_TOOL" "$SDK_TOOL_LEGACY" "$PROJECT_FILE" "$CONVERGE_PY" "$CANONICAL_STATE" "$RUN_CONVERGE" "$AGENT_REGISTRY" "$DRAIN_LEASES"; do
        [ -f "$f" ] || continue
        _tmp_files="$_tmp_files /tmp/$(basename "$f")"
    done
    if [ -n "$_tmp_files" ]; then
        ssh $SSH_OPTS "azureagent@$ip" "sudo rm -f $_tmp_files" 2>/dev/null || true
    fi

    # Step 1: Copy files
    for f in "$PHASE_RUNNER" "$DISPATCH_POLLER" "$DISPATCH_POLLER_V2" "$ADV_REVIEWER" "$POLLER_WRAPPER" "$TERMINAL_GUARD" "$SDK_TOOL" "$SDK_TOOL_LEGACY" "$PROJECT_FILE" "$CONVERGE_PY" "$CANONICAL_STATE" "$RUN_CONVERGE" "$AGENT_REGISTRY" "$DRAIN_LEASES"; do
        [ -f "$f" ] || continue
        local bn=$(basename "$f")
        if ! scp -P 443 -o StrictHostKeyChecking=no "$f" "azureagent@$ip:/tmp/$bn" 2>/dev/null; then
            echo "[$name] ERROR: scp failed for $bn (SSH unreachable or /tmp not writable)"
            return 1
        fi
        ssh $SSH_OPTS "azureagent@$ip" \
            "sudo cp /tmp/$bn /opt/agent/$bn && sudo chown hermes:hermes /opt/agent/$bn" 2>/dev/null
        echo "[$name]   copied $bn"
    done

    # Step 2: Verify ALL file hashes (STORY-535: was single-file, now all-file)
    local verified_count=0
    local total_count=0
    for f in "$PHASE_RUNNER" "$DISPATCH_POLLER" "$DISPATCH_POLLER_V2" "$ADV_REVIEWER" "$POLLER_WRAPPER" "$TERMINAL_GUARD" "$SDK_TOOL" "$SDK_TOOL_LEGACY" "$PROJECT_FILE" "$CONVERGE_PY" "$CANONICAL_STATE" "$RUN_CONVERGE" "$AGENT_REGISTRY" "$DRAIN_LEASES"; do
        [ -f "$f" ] || continue
        local bn=$(basename "$f")
        ((total_count++)) || true
        local expected=$(md5sum "$f" 2>/dev/null | cut -d' ' -f1)
        local actual=$(ssh $SSH_OPTS "azureagent@$ip" "md5sum /opt/agent/$bn | cut -d' ' -f1" 2>/dev/null)
        if [ "$expected" != "$actual" ]; then
            echo "[$name] ERROR: hash mismatch for $bn! expected=$expected actual=$actual"
            return 1
        fi
        ((verified_count++)) || true
    done
    echo "[$name]   ${verified_count}/${total_count} files verified"

    # Step 2b: Install disk cleanup cron (STORY-768 — idempotent, one-time install)
    local CLEANUP_SCRIPT_SRC
    CLEANUP_SCRIPT_SRC="$(cd "$(dirname "$0")" && pwd)/scripts/agent-disk-cleanup.sh"
    local INSTALL_SCRIPT_SRC
    INSTALL_SCRIPT_SRC="$(cd "$(dirname "$0")" && pwd)/scripts/install-disk-cleanup-cron.sh"
    if [ -f "$CLEANUP_SCRIPT_SRC" ] && [ -f "$INSTALL_SCRIPT_SRC" ]; then
        scp -P 443 -o StrictHostKeyChecking=no "$CLEANUP_SCRIPT_SRC" "azureagent@$ip:/tmp/agent-disk-cleanup.sh" 2>/dev/null || true
        scp -P 443 -o StrictHostKeyChecking=no "$INSTALL_SCRIPT_SRC" "azureagent@$ip:/tmp/install-disk-cleanup-cron.sh" 2>/dev/null || true
        ssh $SSH_OPTS "azureagent@$ip" "
            sudo cp /tmp/agent-disk-cleanup.sh /opt/agent/agent-disk-cleanup.sh && \
            sudo chmod +x /opt/agent/agent-disk-cleanup.sh && \
            sudo chown hermes:hermes /opt/agent/agent-disk-cleanup.sh && \
            sudo -u hermes bash /tmp/install-disk-cleanup-cron.sh
        " 2>/dev/null && echo "[$name]   disk cleanup cron installed" || echo "[$name]   WARNING: disk cleanup cron install failed (non-fatal)"
    fi

    # Step 2c: STORY-857 — install systemd unit if it differs on the VM.
    # Compares md5(local) vs md5(remote); on diff, scp + sudo cp + daemon-reload.
    # Idempotent: a no-op when content matches.
    if [ -f "$SYSTEMD_UNIT" ]; then
        local local_unit_md5 remote_unit_md5
        local_unit_md5=$(md5sum "$SYSTEMD_UNIT" 2>/dev/null | cut -d' ' -f1)
        remote_unit_md5=$(ssh $SSH_OPTS "azureagent@$ip" \
            "sudo md5sum /etc/systemd/system/dispatch-poller.service 2>/dev/null | cut -d' ' -f1" 2>/dev/null || true)
        if [ -n "$local_unit_md5" ] && [ "$local_unit_md5" != "$remote_unit_md5" ]; then
            scp -P 443 -o StrictHostKeyChecking=no "$SYSTEMD_UNIT" \
                "azureagent@$ip:/tmp/dispatch-poller.service" 2>/dev/null || true
            ssh $SSH_OPTS "azureagent@$ip" "
                sudo cp /tmp/dispatch-poller.service /etc/systemd/system/dispatch-poller.service && \
                sudo systemctl daemon-reload
            " 2>/dev/null && echo "[$name]   systemd unit updated (ExecStop drain enabled)" \
                || echo "[$name]   WARNING: systemd unit update failed (non-fatal)"
        fi
    fi

    # Step 3: SDK safety check — don't restart if claude_sdk_tool.py is running.
    # Use the [c]laude_sdk_tool.py bracket trick so pgrep doesn't match its own
    # command line (the literal "claude_sdk_tool.py" string inside the SSH
    # command body would otherwise self-match and report a phantom PID).
    # 2026-04-26 incident: every push reported "SDK active" with no real SDK
    # running, deferring every restart unnecessarily.
    local sdk_pid
    sdk_pid=$(ssh $SSH_OPTS "azureagent@$ip" "pgrep -f '[c]laude_sdk_tool.py' | head -1" 2>/dev/null || true)

    if [ -n "$sdk_pid" ]; then
        # Get the story the SDK is working on (best-effort)
        local story
        story=$(ssh $SSH_OPTS "azureagent@$ip" \
            "ps -p $sdk_pid -o args= 2>/dev/null | grep -oE 'STORY-[0-9]+' | head -1" 2>/dev/null || true)

        if [ "$FORCE_RESTART" = "1" ]; then
            # --force: restart immediately regardless of SDK state
            echo "[$name] WARNING: SDK active (PID $sdk_pid, story ${story:-unknown}) — --force set, restarting anyway"

        elif [ -n "$WAIT_FOR_IDLE" ]; then
            # --wait N: poll until SDK finishes or deadline is reached
            local wait_seconds
            wait_seconds=$(awk "BEGIN{printf \"%d\", $WAIT_FOR_IDLE * 60}")
            local deadline=$(( $(date +%s) + wait_seconds ))

            while [ -n "$sdk_pid" ] && [ "$(date +%s)" -lt "$deadline" ]; do
                sleep "$PUSH_CODE_POLL_INTERVAL"
                local elapsed=$(( $(date +%s) - (deadline - wait_seconds) ))
                echo "[$name] waiting for SDK to finish (story ${story:-unknown}, ${elapsed}s elapsed)"
                sdk_pid=$(ssh $SSH_OPTS "azureagent@$ip" \
                    "pgrep -f '[c]laude_sdk_tool.py' | head -1" 2>/dev/null || true)
            done

            if [ -n "$sdk_pid" ]; then
                # Timed out — SDK still running
                echo "[$name] WARNING: --wait timeout after ${WAIT_FOR_IDLE}min, SDK still active (PID $sdk_pid, story ${story:-unknown})"
                echo "[$name]   Files updated but restart deferred. Use --force to override or wait manually."
                echo "[$name] DONE (files only, restart deferred by timeout)"
                return 2
            fi
            # SDK finished — fall through to restart

        else
            # Default safe mode: skip restart, warn operator
            echo "[$name] SDK active (PID $sdk_pid, story ${story:-unknown}) — files copied, restart deferred"
            echo "[$name]   Use --force to override; next poller tick picks up the new code naturally"
            echo "[$name] DONE (files only, no restart)"
            return 0
        fi
    fi

    # Step 3b: Restart poller (but NOT if masked/disabled — agent may be rate-limited)
    local restarted=false
    local svc_state
    svc_state=$(ssh $SSH_OPTS "azureagent@$ip" "systemctl is-enabled dispatch-poller 2>/dev/null" 2>/dev/null)
    if [ "$svc_state" = "masked" ] || [ "$svc_state" = "disabled" ]; then
        echo "[$name]   poller is $svc_state — NOT restarting (agent may be rate-limited)"
        echo "[$name]   files updated but poller left stopped. Run: ssh -p 443 azureagent@$ip 'sudo systemctl unmask dispatch-poller && sudo systemctl enable --now dispatch-poller' when ready"
        echo "[$name] DONE (files only)"
        return 0
    fi

    # STORY-857: pre-restart lease check. The SDK-process check above (Step 3)
    # is necessary but not sufficient — the v2 poller can hold a lease before
    # the SDK subprocess has spawned (claim → write_active_lease → Popen).
    # Ask the queue API directly: any leases owned by $name?
    # Restart while leased = orphaned lease + 3 retries → phase_runner_crash.
    # Bypass with --force (FORCE_RESTART=1).
    if [ "$FORCE_RESTART" != "1" ]; then
        local lease_check_url lease_count active_stories
        lease_check_url="${OPS_CONSOLE_URL:-https://tech-dev-agents.gorillacommerce.ai}/api/dispatch/v2/queue?limit=200"
        # Returns one story_id per line for jobs leased to this agent.
        active_stories=$(curl -sS --max-time 10 \
            -H "X-API-Key: ${OPS_CONSOLE_API_KEY:-}" \
            "$lease_check_url" 2>/dev/null \
            | jq -r --arg agent "$name" \
                '.in_progress // [] | map(select(.leased_by == $agent)) | .[].story_id' \
            2>/dev/null || true)
        # Empty stdout = no leases. Count non-empty lines.
        lease_count=$(printf "%s\n" "$active_stories" | sed '/^$/d' | wc -l | tr -d ' ')
        if [ "${lease_count:-0}" -gt 0 ]; then
            echo "[$name] ABORT: $name has $lease_count active lease(s); refusing to restart."
            echo "[$name]   active stories: $(printf '%s ' $active_stories)"
            echo "[$name]   Use --force to override (will orphan in-flight work)."
            return 1
        fi
    else
        echo "[$name]   --force set: skipping pre-restart lease check"
    fi

    if [ "$svc_state" = "enabled" ]; then
        ssh $SSH_OPTS "azureagent@$ip" "sudo systemctl restart dispatch-poller" 2>/dev/null
        echo "[$name]   restarted via systemd"
        restarted=true
    fi

    if [ "$restarted" = false ]; then
        # Nohup fallback: schedule kill+restart so SSH doesn't drop
        ssh $SSH_OPTS "azureagent@$ip" "
            nohup sudo bash -c '
                pkill -9 -f run_dispatch_poller 2>/dev/null
                sleep 2
                sudo -u hermes bash -c \"source /opt/agent/.env && export OPS_CONSOLE_URL OPS_CONSOLE_API_KEY AGENT_NAME DISPATCH_POLL_INTERVAL && export AGENT_WORKSPACE=\\\${AGENT_WORKSPACE:-/home/hermes/workspace} && nohup /usr/bin/python3 /opt/agent/run_dispatch_poller.py >> /tmp/hermes-combined.log 2>&1 &\"
            ' &>/dev/null &
        " 2>/dev/null
        sleep 3
        echo "[$name]   restarted via nohup (install systemd service for cleaner restarts)"
    fi

    # Step 3c: Verify log-sync services exist (Loki needs [name] prefix)
    local logsync=$(ssh $SSH_OPTS "azureagent@$ip" "systemctl is-active dispatch-log-sync 2>/dev/null" 2>/dev/null)
    if [ "$logsync" != "active" ]; then
        echo "[$name]   WARNING: dispatch-log-sync not active — Loki logs will have no agent prefix"
        echo "[$name]   Fix: run deploy-agent.sh or install log-sync services manually (see NEW-AGENT-PROCESS.md)"
    fi

    # Step 4: Verify running
    local count_raw count
    count_raw=$(ssh $SSH_OPTS "azureagent@$ip" "pgrep -c -f '[r]un_dispatch_poller.py' 2>/dev/null || true" 2>/dev/null)
    count=$(to_int "$count_raw")
    if [ "${count:-0}" -ge 1 ]; then
        echo "[$name]   VERIFIED: poller running"
    else
        echo "[$name]   WARNING: poller may not be running (count=$count)"
    fi

    # Step 5: Check for fresh startup in logs
    local fresh_raw fresh
    fresh_raw=$(ssh $SSH_OPTS "azureagent@$ip" "tail -10 /tmp/hermes-combined.log 2>/dev/null | grep -c 'Starting polling loop' 2>/dev/null || true" 2>/dev/null)
    fresh=$(to_int "$fresh_raw")
    if [ "${fresh:-0}" -ge 1 ]; then
        echo "[$name]   VERIFIED: fresh startup in logs"
    fi

    # Step 6: SMOKE TEST — run one phase-runner import + one claude invocation
    # to catch exactly the kind of bug that burned all tokens on 2026-04-20
    # (e.g., wrong CLI flags, missing functions, import errors).
    echo "[$name]   Running smoke test..."
    local smoke_result
    smoke_result=$(ssh $SSH_OPTS "azureagent@$ip" "
        # Test 1: Phase runner + project_file imports without errors
        # STORY-730: added project_file import — catches missing/corrupted
        # project_file.py that would cause every phase-end to warn
        # 'project_file not available on this VM'.
        sudo -u hermes python3 -c '
import sys; sys.path.insert(0, \"/opt/agent\")
from sdlc_phase_runner import run_sdlc_phases, _run_phase_sdk, _notify_teams, _check_for_questions
from project_file import update_story_status
# Epic-Queue-v2 cutover (2026-05-03): smoke must verify the v2 poller
# imports too, otherwise a missing/broken dispatch_poller_v2.py only
# surfaces at runtime when DISPATCH_PROTOCOL=v2 is flipped.
from dispatch_poller_v2 import poll_loop as _v2_poll_loop
print(\"import: OK\")
' 2>&1

        # Test 2: claude CLI accepts the flags we use (no -w!)
        sudo -u hermes claude -p 'say OK' --max-turns 1 --output-format json 2>&1 | \
            python3 -c 'import sys,json;d=json.load(sys.stdin);print(f\"claude: is_error={d.get(\"is_error\")}, tokens={d.get(\"usage\",{}).get(\"input_tokens\",0)}\")' 2>&1 || echo 'claude: PARSE_FAIL'

        # Test 3: Check for obvious errors in first 10 seconds of poller
        sleep 5
        sudo journalctl -u dispatch-poller --since '10 sec ago' --no-pager -q 2>/dev/null | \
            grep -ciE 'error|exception|traceback|NameError|ImportError|ModuleNotFound' || echo 0
    " 2>/dev/null)

    local import_ok=$(echo "$smoke_result" | grep -c "import: OK")
    local claude_ok=$(echo "$smoke_result" | grep -c "claude: is_error=False")
    local claude_rate_limited=$(echo "$smoke_result" | grep -c "claude: is_error=True, tokens=0")
    local errors=$(echo "$smoke_result" | tail -1)

    if [ "$import_ok" -ge 1 ] && [ "$claude_ok" -ge 1 ] && [ "${errors:-0}" = "0" ]; then
        echo "[$name]   SMOKE TEST PASSED (import OK, claude OK, no errors)"
    elif [ "$import_ok" -ge 1 ] && [ "$claude_rate_limited" -ge 1 ]; then
        echo "[$name]   SMOKE TEST PASSED (import OK, claude rate-limited — code is fine, tokens exhausted)"
    else
        echo "[$name]   *** SMOKE TEST FAILED ***"
        echo "$smoke_result" | sed "s/^/[$name]     /"
        echo "[$name]   ROLLBACK: stopping poller to prevent token waste"
        ssh $SSH_OPTS "azureagent@$ip" "sudo systemctl stop dispatch-poller" 2>/dev/null
        return 1
    fi

    # Step 7: Runtime version probe — verify the running process loaded new code
    # (STORY-535: detect stale Python module cache after restart)
    local runtime_hash
    runtime_hash=$(ssh $SSH_OPTS "azureagent@$ip" \
        "sudo -u hermes python3 -c 'import hashlib,sys; sys.path.insert(0,\"/opt/agent\"); import sdlc_phase_runner; print(hashlib.md5(open(sdlc_phase_runner.__file__,\"rb\").read()).hexdigest())' 2>/dev/null || echo runtime_probe_failed" 2>/dev/null)
    local expected_runtime
    expected_runtime=$(md5sum "$PHASE_RUNNER" 2>/dev/null | cut -d' ' -f1)
    if [ "$runtime_hash" = "$expected_runtime" ]; then
        echo "[$name]   runtime verified (loaded module matches deployed file)"
    else
        echo "[$name]   WARNING: runtime probe mismatch (expected=$expected_runtime, got=$runtime_hash)"
        echo "[$name]   The files on disk are correct but the running process may have stale cached modules."
        echo "[$name]   Consider a second restart if behavior is unexpected."
    fi

    echo "[$name] DONE"
}

# ─── Argument parsing ──────────────────────────────────────────────────────────
# Parse --force / --wait [N] before collecting agent names.
raw_args=("$@")
agents=()
skip_next=false
for arg in "${raw_args[@]}"; do
    if $skip_next; then
        # Previous arg was --wait; this is the optional minutes value
        # (only consume it if it looks like a number, not an agent name)
        if [[ "$arg" =~ ^[0-9]+(\.[0-9]+)?$ ]]; then
            WAIT_FOR_IDLE="$arg"
        else
            agents+=("$arg")
        fi
        skip_next=false
        continue
    fi
    case "$arg" in
        --force)
            FORCE_RESTART=1
            ;;
        --wait)
            # Use default 30 min; next arg may override if numeric
            WAIT_FOR_IDLE="${WAIT_FOR_IDLE:-30}"
            skip_next=true
            ;;
        --wait=*)
            WAIT_FOR_IDLE="${arg#--wait=}"
            ;;
        *)
            agents+=("$arg")
            ;;
    esac
done

if [ ${#agents[@]} -eq 0 ]; then
    echo "Usage: $0 [--force] [--wait [N]] <agent-name|all> [agent-name...]"
    echo "  $0 dan               # push to Dan (safe: skips restart if SDK active)"
    echo "  $0 all               # push to all dev agents"
    echo "  $0 dan derrick       # push to Dan and Derrick"
    echo "  $0 --force dan       # force restart even if SDK is mid-story"
    echo "  $0 --wait 5 dan      # wait up to 5 min for SDK to finish, then restart"
    echo "  $0 --wait dan        # wait up to 30 min (default) for SDK to finish"
    exit 1
fi

if [ "${agents[0]}" = "all" ]; then
    agents=()
    for name in "${!AGENT_IPS[@]}"; do
        # Skip morris (manager, no poller)
        [ "$name" = "morris" ] && continue
        agents+=("$name")
    done
fi

echo "=== Code Push $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
echo "Targets: ${agents[*]}"
echo "Files: sdlc_phase_runner.py, dispatch_poller.py, dispatch_poller_v2.py, drain_leases.py, adversarial_reviewer.py, terminal_guard.py, claude_sdk_tool.py, project_file.py, converge.py, canonical-state.yaml, run-converge.sh, agent-registry.json"
if [ "$FORCE_RESTART" = "1" ]; then
    echo "Mode: FORCE (restart even if SDK active)"
elif [ -n "$WAIT_FOR_IDLE" ]; then
    echo "Mode: WAIT up to ${WAIT_FOR_IDLE}min for SDK to finish"
else
    echo "Mode: SAFE (skip restart if SDK active)"
fi
echo ""

failures=0
wait_timeouts=0
for agent in "${agents[@]}"; do
    if [ "$agent" = "morris" ]; then
        echo "[$agent] Skipping (manager, no poller)"
        continue
    fi
    rc=0
    deploy_one "$agent" || rc=$?
    if [ "$rc" -eq 2 ]; then
        ((wait_timeouts++)) || true
    elif [ "$rc" -ne 0 ]; then
        ((failures++)) || true
    fi
    echo ""
done

if [ $failures -gt 0 ]; then
    echo "=== DONE with $failures failure(s) ==="
    exit 1
elif [ $wait_timeouts -gt 0 ]; then
    echo "=== DONE with $wait_timeouts --wait timeout(s) — restart deferred on those VMs ==="
    exit 2
else
    echo "=== All deploys succeeded ==="
fi

# STORY-644: Write deployment manifest so converge.py can detect code drift.
# Records md5 of each deployed file at push time. converge.py compares
# runtime md5 against this manifest; mismatch → DRIFT_DETECTED (detection only).
echo ""
echo "Writing deployment manifest to $SCRIPT_DIR/.deploy-manifest.json ..."
python3 - "$SCRIPT_DIR" <<'PYEOF'
import json, hashlib, os, sys, time
script_dir = os.path.abspath(sys.argv[1])   # SCRIPT_DIR passed explicitly (heredoc lacks __file__)
repo_root = os.path.dirname(os.path.dirname(script_dir))
files_to_track = [
    os.path.join(repo_root, "deployment", "hermes", "dispatch_poller.py"),
    os.path.join(repo_root, "deployment", "hermes", "dispatch_poller_v2.py"),
    os.path.join(repo_root, "deployment", "hermes", "sdlc_phase_runner.py"),
    os.path.join(repo_root, "deployment", "hermes", "adversarial_reviewer.py"),
    os.path.join(script_dir, "claude_sdk_tool.py"),
    os.path.join(script_dir, "run_dispatch_poller.py"),
    os.path.join(script_dir, "terminal_guard.py"),
    os.path.join(repo_root, "deployment", "hermes", "project_file.py"),
    # STORY-857: ExecStop drain script
    os.path.join(repo_root, "deployment", "hermes", "drain_leases.py"),
]
# Map local path → /opt/agent/<basename> (the deployed path on VMs)
manifest = {}
for f in files_to_track:
    if not os.path.exists(f):
        continue
    with open(f, "rb") as fh:
        digest = hashlib.md5(fh.read()).hexdigest()
    manifest[f"/opt/agent/{os.path.basename(f)}"] = digest
out = {
    "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "generated_by": "push-code.sh",
    "files": manifest,
}
out_path = os.path.join(script_dir, ".deploy-manifest.json")
with open(out_path, "w") as fh:
    json.dump(out, fh, indent=2)
    fh.write("\n")
print(f"  manifest: {len(manifest)} files → {out_path}")
PYEOF
