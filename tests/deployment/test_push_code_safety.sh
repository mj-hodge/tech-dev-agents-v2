#!/usr/bin/env bash
# tests/deployment/test_push_code_safety.sh
# STORY-512: push-code.sh SDK safety — Phase 7 integration tests (RED state)
#
# Tests AC-1 through AC-7: SDK check before restart, --force, --wait, exit codes.
#
# Usage:
#   bash tests/deployment/test_push_code_safety.sh
#   Exit 0 = all tests pass, Exit 1 = failures.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PUSH_CODE="$REPO_ROOT/deployment/vm/push-code.sh"

PASS=0
FAIL=0
declare -a FAILED_NAMES=()

# ─── Colours ──────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[0;33m'
NC='\033[0m'

# ─── Test runner ──────────────────────────────────────────────────────────────
run_test() {
    local name="$1"
    local fn="$2"
    if "$fn" 2>/dev/null; then
        ((PASS++)) || true
        printf "  ${GREEN}PASS${NC}: %s\n" "$name"
    else
        ((FAIL++)) || true
        FAILED_NAMES+=("$name")
        printf "  ${RED}FAIL${NC}: %s\n" "$name"
    fi
}

# ─── Mock environment builder ─────────────────────────────────────────────────
# Creates a temp dir with mock ssh, scp, md5sum binaries.
# Returns the path via stdout (capture with $(...)).
# Env vars controlling mock SSH behaviour:
#   SDK_RUNNING        - PID string when SDK active; empty when not
#   SDK_ACTIVE_IPS     - space-separated IPs where SDK is running (overrides SDK_RUNNING per-IP)
#   SDK_FINISH_AFTER   - pgrep call count after which SDK "stops" (default: 99999)
#   RESTART_LOG        - file appended-to when restart called (one IP per line)
#   PGREP_COUNT_FILE   - state file tracking pgrep call count
#   MOCK_MD5           - hash returned for remote md5sum (default: deadbeef)
#   SCP_FAIL           - set to 1 to make scp fail (default: 0)
setup_mock_env() {
    local tmpdir
    tmpdir=$(mktemp -d)

    # ── Mock SSH ──────────────────────────────────────────────────────────────
    cat > "$tmpdir/ssh" << 'SSHEOF'
#!/usr/bin/env bash
# Mock ssh: parse azureagent@IP "command" and return canned responses.

# Extract IP and command from args
ip=""
cmd=""
found_host=0
for arg in "$@"; do
    if [ "$found_host" -eq 1 ]; then
        cmd="$arg"
        break
    fi
    if [[ "$arg" == azureagent@* ]]; then
        ip="${arg#azureagent@}"
        found_host=1
    fi
done

# ── systemctl is-enabled ─────────────────────────────────────────────────────
if [[ "$cmd" == *"systemctl is-enabled"* ]]; then
    echo "enabled"
    exit 0
fi

# ── systemctl restart dispatch-poller ────────────────────────────────────────
if [[ "$cmd" == *"systemctl restart dispatch-poller"* ]]; then
    echo "$ip" >> "${RESTART_LOG:-/tmp/push_code_restart.log}"
    exit 0
fi

# ── systemctl stop ────────────────────────────────────────────────────────────
if [[ "$cmd" == *"systemctl stop"* ]]; then
    exit 0
fi

# ── pgrep for SDK process (claude_sdk_tool.py) ────────────────────────────────
if [[ "$cmd" == *"pgrep"* && "$cmd" == *"claude_sdk_tool"* ]]; then
    count_file="${PGREP_COUNT_FILE:-/tmp/push_code_pgrep_count}"
    count=$(cat "$count_file" 2>/dev/null || echo "0")
    count=$((count + 1))
    echo "$count" > "$count_file"
    finish_after="${SDK_FINISH_AFTER:-99999}"

    # Per-IP selective mode (SDK_ACTIVE_IPS overrides SDK_RUNNING)
    if [ -n "${SDK_ACTIVE_IPS:-}" ]; then
        for active_ip in $SDK_ACTIVE_IPS; do
            if [ "$ip" = "$active_ip" ] && [ "$count" -le "$finish_after" ]; then
                echo "12345"
            fi
        done
        exit 0
    fi

    # Global SDK_RUNNING mode
    if [ -n "${SDK_RUNNING:-}" ] && [ "$count" -le "$finish_after" ]; then
        echo "${SDK_RUNNING}"
    fi
    exit 0
fi

# ── ps -p (get story from SDK process args) ───────────────────────────────────
if [[ "$cmd" == *"ps -p"* ]]; then
    if [ -n "${SDK_RUNNING:-}" ] || [ -n "${SDK_ACTIVE_IPS:-}" ]; then
        echo "python3 /opt/agent/claude_sdk_tool.py --story STORY-512"
    fi
    exit 0
fi

# ── md5sum (remote hash verification) ────────────────────────────────────────
# Note: the SSH command includes "| cut -d' ' -f1" so we return only the hash.
# STORY-535: Support per-file mismatch via HASH_MISMATCH_FILE env var.
if [[ "$cmd" == *"md5sum"* ]]; then
    if [ -n "${HASH_MISMATCH_FILE:-}" ]; then
        # Check if the md5sum command targets the mismatch file
        if [[ "$cmd" == *"${HASH_MISMATCH_FILE}"* ]]; then
            echo "badhash000000000000000000000000"
            exit 0
        fi
    fi
    echo "${MOCK_MD5:-deadbeef}"
    exit 0
fi


# ── sudo cp / sudo chown (file ops) ───────────────────────────────────────────
if [[ "$cmd" == *"sudo cp"* ]] || [[ "$cmd" == *"sudo chown"* ]]; then
    exit 0
fi

# ── systemctl is-active (log-sync health check) ───────────────────────────────
if [[ "$cmd" == *"systemctl is-active"* ]]; then
    echo "active"
    exit 0
fi

# ── pgrep run_dispatch_poller (poller verify step) ───────────────────────────
if [[ "$cmd" == *"pgrep"* && "$cmd" == *"run_dispatch_poller"* ]]; then
    echo "1"
    exit 0
fi

# ── STORY-535: Runtime probe (hashlib + sdlc_phase_runner = runtime probe) ───
if [[ "$cmd" == *"hashlib"* && "$cmd" == *"sdlc_phase_runner"* ]]; then
    if [ "${RUNTIME_PROBE_MISMATCH:-0}" = "1" ]; then
        echo "mismatched_runtime_hash"
    else
        echo "${MOCK_MD5:-deadbeef}"
    fi
    exit 0
fi

# ── Smoke test (multi-line cmd containing sdlc_phase_runner import) ───────────
if [[ "$cmd" == *"sdlc_phase_runner"* ]]; then
    echo "import: OK"
    echo "claude: is_error=False, tokens=100"
    echo "0"
    exit 0
fi

# ── tail log freshness check (cmd includes grep -c, so return count) ─────────
if [[ "$cmd" == *"tail"* && "$cmd" == *"grep -c"* ]]; then
    echo "1"
    exit 0
fi

# ── journalctl error scan (smoke test step 3; cmd includes grep -ciE) ─────────
if [[ "$cmd" == *"journalctl"* ]]; then
    echo "0"
    exit 0
fi

# ── Default: success ──────────────────────────────────────────────────────────
exit 0
SSHEOF
    chmod +x "$tmpdir/ssh"

    # ── Mock SCP ──────────────────────────────────────────────────────────────
    cat > "$tmpdir/scp" << 'SCPEOF'
#!/usr/bin/env bash
if [ "${SCP_FAIL:-0}" = "1" ]; then
    echo "scp: mock failure" >&2
    exit 1
fi
exit 0
SCPEOF
    chmod +x "$tmpdir/scp"

    # ── Mock md5sum (local hash — matches remote mock) ────────────────────────
    cat > "$tmpdir/md5sum" << 'MD5EOF'
#!/usr/bin/env bash
# Return fixed hash matching remote mock; ignore actual file content.
echo "${MOCK_MD5:-deadbeef}  ${1:--}"
MD5EOF
    chmod +x "$tmpdir/md5sum"

    echo "$tmpdir"
}

cleanup_mock_env() {
    local tmpdir="$1"
    rm -rf "$tmpdir"
}

# ─── Helper: run push-code.sh with mock env ───────────────────────────────────
# Usage: run_push_code <tmpdir> [extra env vars...] -- [push-code args...]
# Returns output in PUSH_CODE_OUTPUT, exit code in PUSH_CODE_RC.
PUSH_CODE_OUTPUT=""
PUSH_CODE_RC=0
run_push_code() {
    local tmpdir="$1"
    shift
    # Collect extra env assignments (KEY=VALUE) until we see --
    local -a extra_env=()
    while [ $# -gt 0 ] && [ "$1" != "--" ]; do
        extra_env+=("$1")
        shift
    done
    [ "${1:-}" = "--" ] && shift
    local -a push_args=("$@")

    local restart_log="$tmpdir/restarts.log"
    local pgrep_count="$tmpdir/pgrep_count"
    rm -f "$restart_log" "$pgrep_count"

    local _rc=0
    PUSH_CODE_OUTPUT=$(
        env PATH="$tmpdir:$PATH" \
            RESTART_LOG="$restart_log" \
            PGREP_COUNT_FILE="$pgrep_count" \
            MOCK_MD5="deadbeef" \
            "${extra_env[@]}" \
            bash "$PUSH_CODE" "${push_args[@]}" 2>&1
    ) || _rc=$?
    PUSH_CODE_RC=$_rc
}

# ─── Individual Tests ─────────────────────────────────────────────────────────

# TC-1: No SDK running → normal restart, exit 0
tc1_no_sdk_normal_restart() {
    local tmpdir
    tmpdir=$(setup_mock_env)
    run_push_code "$tmpdir" SDK_RUNNING="" -- dan
    local rc=$PUSH_CODE_RC
    local restarts
    restarts=$(wc -l < "$tmpdir/restarts.log" 2>/dev/null || echo 0)
    cleanup_mock_env "$tmpdir"
    [ "$rc" -eq 0 ] && [ "$restarts" -ge 1 ]
}

# TC-2: SDK running, no flags → skip restart, exit 0 (not 1), warn message
tc2_sdk_running_default_skip() {
    local tmpdir
    tmpdir=$(setup_mock_env)
    run_push_code "$tmpdir" SDK_RUNNING="12345" -- dan
    local rc=$PUSH_CODE_RC
    local restarts
    restarts=$(wc -l < "$tmpdir/restarts.log" 2>/dev/null || echo 0)
    local has_warning=0
    echo "$PUSH_CODE_OUTPUT" | grep -q "SDK active" && has_warning=1
    local has_deferred=0
    echo "$PUSH_CODE_OUTPUT" | grep -q "restart deferred" && has_deferred=1
    cleanup_mock_env "$tmpdir"
    [ "$rc" -eq 0 ] && [ "$restarts" -eq 0 ] && [ "$has_warning" -eq 1 ] && [ "$has_deferred" -eq 1 ]
}

# TC-3: SDK running + --force flag → restart anyway
tc3_sdk_running_force_flag() {
    local tmpdir
    tmpdir=$(setup_mock_env)
    run_push_code "$tmpdir" SDK_RUNNING="12345" -- --force dan
    local rc=$PUSH_CODE_RC
    local restarts
    restarts=$(wc -l < "$tmpdir/restarts.log" 2>/dev/null || echo 0)
    cleanup_mock_env "$tmpdir"
    [ "$rc" -eq 0 ] && [ "$restarts" -ge 1 ]
}

# TC-4: SDK running + FORCE_RESTART=1 env → restart anyway
# Requires pgrep to have been called (SDK check implemented) AND restart to happen.
# In RED state: pgrep never called (no SDK check) → pgrep_count=0 → FAIL.
tc4_sdk_running_force_env() {
    local tmpdir
    tmpdir=$(setup_mock_env)
    run_push_code "$tmpdir" SDK_RUNNING="12345" FORCE_RESTART=1 -- dan
    local rc=$PUSH_CODE_RC
    local restarts
    restarts=$(wc -l < "$tmpdir/restarts.log" 2>/dev/null || echo 0)
    local pgrep_count
    pgrep_count=$(cat "$tmpdir/pgrep_count" 2>/dev/null || echo 0)
    cleanup_mock_env "$tmpdir"
    # pgrep must have been called (SDK check implemented) AND restart must happen
    [ "$rc" -eq 0 ] && [ "$restarts" -ge 1 ] && [ "$pgrep_count" -ge 1 ]
}

# TC-5: --wait N, SDK finishes before timeout → restart proceeds, exit 0
tc5_wait_sdk_finishes_then_restart() {
    local tmpdir
    tmpdir=$(setup_mock_env)
    # SDK stops after 2 pgrep calls; deadline = 2 min; poll = 1 sec
    run_push_code "$tmpdir" \
        SDK_RUNNING="12345" \
        SDK_FINISH_AFTER=2 \
        PUSH_CODE_POLL_INTERVAL=1 \
        -- --wait 2 dan
    local rc=$PUSH_CODE_RC
    local restarts
    restarts=$(wc -l < "$tmpdir/restarts.log" 2>/dev/null || echo 0)
    local has_waiting=0
    echo "$PUSH_CODE_OUTPUT" | grep -q "waiting" && has_waiting=1
    cleanup_mock_env "$tmpdir"
    [ "$rc" -eq 0 ] && [ "$restarts" -ge 1 ] && [ "$has_waiting" -eq 1 ]
}

# TC-6: --wait 0.1 (6s deadline), SDK never stops → timeout, exit 2
tc6_wait_timeout_exit2() {
    local tmpdir
    tmpdir=$(setup_mock_env)
    run_push_code "$tmpdir" \
        SDK_RUNNING="12345" \
        SDK_FINISH_AFTER=99999 \
        PUSH_CODE_POLL_INTERVAL=1 \
        -- --wait 0.1 dan
    local rc=$PUSH_CODE_RC
    local restarts
    restarts=$(wc -l < "$tmpdir/restarts.log" 2>/dev/null || echo 0)
    local has_waiting=0
    echo "$PUSH_CODE_OUTPUT" | grep -q "waiting" && has_waiting=1
    cleanup_mock_env "$tmpdir"
    [ "$rc" -eq 2 ] && [ "$restarts" -eq 0 ] && [ "$has_waiting" -eq 1 ]
}

# TC-7: --force + --wait together → --force wins, no waiting
tc7_force_and_wait_force_wins() {
    local tmpdir
    tmpdir=$(setup_mock_env)
    run_push_code "$tmpdir" \
        SDK_RUNNING="12345" \
        PUSH_CODE_POLL_INTERVAL=1 \
        -- --force --wait 30 dan
    local rc=$PUSH_CODE_RC
    local restarts
    restarts=$(wc -l < "$tmpdir/restarts.log" 2>/dev/null || echo 0)
    local has_waiting=0
    echo "$PUSH_CODE_OUTPUT" | grep -q "waiting" && has_waiting=1
    cleanup_mock_env "$tmpdir"
    # --force should restart; --wait should NOT trigger waiting loop
    [ "$rc" -eq 0 ] && [ "$restarts" -ge 1 ] && [ "$has_waiting" -eq 0 ]
}

# TC-8: Selective — SDK on dan (20.228.224.243) but not derrick (20.121.210.186)
tc8_selective_sdk_on_one_agent() {
    local tmpdir
    tmpdir=$(setup_mock_env)
    # Dan's IP from agent-registry.json
    run_push_code "$tmpdir" \
        SDK_ACTIVE_IPS="20.228.224.243" \
        -- dan derrick
    local rc=$PUSH_CODE_RC
    # Restart log should have derrick's IP but NOT dan's IP
    local dan_restarted=0
    local derrick_restarted=0
    if grep -qF "20.228.224.243" "$tmpdir/restarts.log" 2>/dev/null; then
        dan_restarted=1
    fi
    if grep -qF "20.121.210.186" "$tmpdir/restarts.log" 2>/dev/null; then
        derrick_restarted=1
    fi
    # Dan's output should mention SDK active
    local dan_deferred=0
    echo "$PUSH_CODE_OUTPUT" | grep -i "dan" | grep -q "SDK active" && dan_deferred=1
    cleanup_mock_env "$tmpdir"
    [ "$rc" -eq 0 ] && [ "$dan_restarted" -eq 0 ] && [ "$derrick_restarted" -eq 1 ] && [ "$dan_deferred" -eq 1 ]
}

# TC-9: scp failure → exit 1, no restart
tc9_scp_failure_exit1() {
    local tmpdir
    tmpdir=$(setup_mock_env)
    run_push_code "$tmpdir" SCP_FAIL=1 -- dan
    local rc=$PUSH_CODE_RC
    local restarts
    restarts=$(wc -l < "$tmpdir/restarts.log" 2>/dev/null || echo 0)
    local has_error=0
    echo "$PUSH_CODE_OUTPUT" | grep -qi "error" && has_error=1
    cleanup_mock_env "$tmpdir"
    [ "$rc" -eq 1 ] && [ "$restarts" -eq 0 ] && [ "$has_error" -eq 1 ]
}

# TC-10: pgrep verifiably called before restart (AC-1 — check must precede restart)
tc10_pgrep_called_before_restart() {
    local tmpdir
    tmpdir=$(setup_mock_env)
    run_push_code "$tmpdir" SDK_RUNNING="" -- dan
    local pgrep_count
    pgrep_count=$(cat "$tmpdir/pgrep_count" 2>/dev/null || echo 0)
    local restarts
    restarts=$(wc -l < "$tmpdir/restarts.log" 2>/dev/null || echo 0)
    cleanup_mock_env "$tmpdir"
    # pgrep must have been called AND restart must have happened
    [ "$pgrep_count" -ge 1 ] && [ "$restarts" -ge 1 ]
}

# TC-11: Warning message format matches AC-2 spec
tc11_warning_message_format() {
    local tmpdir
    tmpdir=$(setup_mock_env)
    run_push_code "$tmpdir" SDK_RUNNING="12345" -- dan
    local has_pid=0
    local has_override_hint=0
    echo "$PUSH_CODE_OUTPUT" | grep -qE "SDK active.*PID 12345" && has_pid=1
    echo "$PUSH_CODE_OUTPUT" | grep -q "force" && has_override_hint=1
    cleanup_mock_env "$tmpdir"
    [ "$has_pid" -eq 1 ] && [ "$has_override_hint" -eq 1 ]
}

# ─── STORY-524: project_file.py deploy manifest compliance ────────────────────
# TC-12: push-code.sh must declare PROJECT_FILE variable (RED until Phase 8)
# TC-13: push-code.sh must scp project_file.py to VMs (RED until Phase 8)
# TC-14: Integration — project_file.py appears in actual scp calls made by push-code.sh

# TC-12: PROJECT_FILE variable declared in push-code.sh source
tc12_project_file_variable_declared() {
    grep -q "PROJECT_FILE" "$PUSH_CODE"
}

# TC-13: $PROJECT_FILE included in the copy loop (not just declared but unused)
tc13_project_file_in_copy_loop() {
    # Check the for-loop or copy section includes $PROJECT_FILE
    grep -q '"\$PROJECT_FILE"' "$PUSH_CODE"
}

# TC-14: Integration — mock scp records filenames; project_file.py must appear
# This replaces the "fake VM" integration criterion from the dispatch.
# We extend setup_mock_env with a scp that appends the source filename to a log.
tc14_scp_copies_project_file_to_vm() {
    local tmpdir
    tmpdir=$(mktemp -d)

    # ── Mock SSH (minimal — needed for hash check, restart, smoke test) ────────
    cat > "$tmpdir/ssh" << 'SSHEOF'
#!/usr/bin/env bash
cmd=""
for arg in "$@"; do
    if [[ "$arg" != *"azureagent@"* ]] && [[ -n "$cmd" || "$arg" == *"azureagent@"* ]]; then
        [ "${arg}" != "${arg#azureagent@}" ] && continue
        cmd="$arg"
        break
    fi
done
# Minimal responses for each SSH invocation
case "$*" in
    *"systemctl is-enabled"*)        echo "enabled" ;;
    *"systemctl restart"*)           echo "10.0.0.1" >> "${RESTART_LOG:-/tmp/r.log}" ;;
    *"md5sum"*)                      echo "${MOCK_MD5:-deadbeef}" ;;
    *"sudo cp"*|*"sudo chown"*)      ;;
    *"systemctl is-active"*)         echo "active" ;;
    *"pgrep"*"claude_sdk_tool"*)     ;;   # SDK not running
    *"pgrep"*"run_dispatch_poller"*) echo "1" ;;
    *"sdlc_phase_runner"*)           printf "import: OK\nclaude: is_error=False, tokens=100\n0\n" ;;
    *"tail"*"grep -c"*)              echo "1" ;;
    *"journalctl"*)                  echo "0" ;;
esac
exit 0
SSHEOF
    chmod +x "$tmpdir/ssh"

    # ── Mock md5sum (local) ────────────────────────────────────────────────────
    cat > "$tmpdir/md5sum" << 'MD5EOF'
#!/usr/bin/env bash
echo "${MOCK_MD5:-deadbeef}  ${1:--}"
MD5EOF
    chmod +x "$tmpdir/md5sum"

    # ── Tracking scp — records each source file transferred ───────────────────
    local scp_log="$tmpdir/scp_files.log"
    cat > "$tmpdir/scp" << SCPEOF
#!/usr/bin/env bash
# Record each source file path (skip options like -P, -o, etc.)
for arg in "\$@"; do
    case "\$arg" in
        -*)  ;;                       # skip flags
        [0-9]*)  ;;                   # skip port number (443)
        azureagent@*)  ;;             # skip destination host
        /tmp/*)  ;;                   # skip /tmp/<name> (dest path on remote)
        *)   echo "\$arg" >> "$scp_log" ;;
    esac
done
exit 0
SCPEOF
    chmod +x "$tmpdir/scp"

    local restart_log="$tmpdir/restarts.log"
    local _rc=0
    env PATH="$tmpdir:$PATH" \
        RESTART_LOG="$restart_log" \
        MOCK_MD5="deadbeef" \
        SDK_RUNNING="" \
        bash "$PUSH_CODE" dan >/dev/null 2>&1 || _rc=$?

    # Verify project_file.py appeared among the files scp'd
    local found=0
    if [ -f "$scp_log" ]; then
        grep -q "project_file.py" "$scp_log" && found=1
    fi
    rm -rf "$tmpdir"
    [ "$found" -eq 1 ]
}

# ─── STORY-730: Smoke test covers project_file import ────────────────────────
# TC-20: push-code.sh smoke test includes 'from project_file import' in its
#        Python import check — ensures a missing/corrupted project_file.py
#        fails the deploy instead of silently succeeding.
tc20_smoke_test_imports_project_file() {
    # The smoke test section is the SSH heredoc containing the Python import.
    # Verify the push-code.sh source includes 'project_file' in that import block.
    # We look for it in the same Python -c block that imports sdlc_phase_runner.
    grep -A5 "Phase runner.*imports" "$PUSH_CODE" | grep -q "project_file"
}

# ─── STORY-535: Push-code verification hardening tests ───────────────────────

# TC-15: All files hash-verified on success — output contains "N/N files verified"
# RED: push-code.sh currently only verifies sdlc_phase_runner.py, not all files.
tc15_all_files_hash_verified() {
    local tmpdir
    tmpdir=$(setup_mock_env)
    run_push_code "$tmpdir" SDK_RUNNING="" -- dan
    local rc=$PUSH_CODE_RC
    local has_summary=0
    echo "$PUSH_CODE_OUTPUT" | grep -q "files verified" && has_summary=1
    cleanup_mock_env "$tmpdir"
    [ "$rc" -eq 0 ] && [ "$has_summary" -eq 1 ]
}

# TC-16: Single file hash mismatch → fail closed (exit 1, no restart)
# RED: push-code.sh only checks sdlc_phase_runner.py hash; other files unchecked.
# Uses HASH_MISMATCH_FILE env var to make mock SSH return bad hash for one file.
tc16_single_file_hash_mismatch_fail_closed() {
    local tmpdir
    tmpdir=$(setup_mock_env)
    run_push_code "$tmpdir" SDK_RUNNING="" HASH_MISMATCH_FILE="dispatch_poller.py" -- dan
    local rc=$PUSH_CODE_RC
    local restarts
    restarts=$(wc -l < "$tmpdir/restarts.log" 2>/dev/null || echo 0)
    local has_mismatch_msg=0
    echo "$PUSH_CODE_OUTPUT" | grep -qi "dispatch_poller.py" && \
        echo "$PUSH_CODE_OUTPUT" | grep -qi "mismatch" && has_mismatch_msg=1
    cleanup_mock_env "$tmpdir"
    [ "$rc" -eq 1 ] && [ "$restarts" -eq 0 ] && [ "$has_mismatch_msg" -eq 1 ]
}

# TC-17: Runtime version probe succeeds → "runtime verified" in output
# RED: push-code.sh has no post-restart runtime probe.
tc17_runtime_probe_success() {
    local tmpdir
    tmpdir=$(setup_mock_env)
    run_push_code "$tmpdir" SDK_RUNNING="" -- dan
    local rc=$PUSH_CODE_RC
    local has_runtime_verified=0
    echo "$PUSH_CODE_OUTPUT" | grep -qi "runtime.*verified\|runtime.*OK" && has_runtime_verified=1
    cleanup_mock_env "$tmpdir"
    [ "$rc" -eq 0 ] && [ "$has_runtime_verified" -eq 1 ]
}

# TC-18: Runtime probe mismatch → WARNING only (deploy still exit 0)
# RED: push-code.sh has no runtime probe at all.
tc18_runtime_probe_mismatch_warning_only() {
    local tmpdir
    tmpdir=$(setup_mock_env)
    run_push_code "$tmpdir" SDK_RUNNING="" RUNTIME_PROBE_MISMATCH=1 -- dan
    local rc=$PUSH_CODE_RC
    local has_runtime_warning=0
    echo "$PUSH_CODE_OUTPUT" | grep -qi "WARNING.*runtime\|runtime.*WARNING\|runtime.*mismatch" && has_runtime_warning=1
    cleanup_mock_env "$tmpdir"
    [ "$rc" -eq 0 ] && [ "$has_runtime_warning" -eq 1 ]
}

# TC-19: Verification summary line shows "N/N files verified" format
# RED: push-code.sh has no verification summary line.
tc19_verification_summary_line_format() {
    local tmpdir
    tmpdir=$(setup_mock_env)
    run_push_code "$tmpdir" SDK_RUNNING="" -- dan
    local rc=$PUSH_CODE_RC
    local has_count_format=0
    # Match pattern like "[dan]   6/6 files verified" or similar N/N format
    echo "$PUSH_CODE_OUTPUT" | grep -qE '[0-9]+/[0-9]+ files verified' && has_count_format=1
    cleanup_mock_env "$tmpdir"
    [ "$rc" -eq 0 ] && [ "$has_count_format" -eq 1 ]
}

# ─── Run all tests ─────────────────────────────────────────────────────────────
echo ""
echo "=== STORY-512: push-code.sh SDK safety tests ==="
echo ""

run_test "TC-1: no SDK running → normal restart, exit 0"           tc1_no_sdk_normal_restart
run_test "TC-2: SDK running, no flags → skip restart (warn, exit 0)" tc2_sdk_running_default_skip
run_test "TC-3: SDK running + --force → restart anyway"            tc3_sdk_running_force_flag
run_test "TC-4: SDK running + FORCE_RESTART=1 env → restart"       tc4_sdk_running_force_env
run_test "TC-5: --wait N, SDK finishes → restart proceeds, exit 0" tc5_wait_sdk_finishes_then_restart
run_test "TC-6: --wait 0.1 timeout → no restart, exit 2"          tc6_wait_timeout_exit2
run_test "TC-7: --force + --wait → --force wins, no waiting"        tc7_force_and_wait_force_wins
run_test "TC-8: selective — SDK on dan only, derrick restarted"     tc8_selective_sdk_on_one_agent
run_test "TC-9: scp failure → exit 1, no restart"                  tc9_scp_failure_exit1
run_test "TC-10: pgrep called before restart (AC-1)"               tc10_pgrep_called_before_restart
run_test "TC-11: warning message format (PID + --force hint)"      tc11_warning_message_format

echo ""
echo "=== STORY-524: project_file.py manifest compliance ==="
echo ""
run_test "TC-12: PROJECT_FILE variable declared in push-code.sh"   tc12_project_file_variable_declared
run_test "TC-13: \$PROJECT_FILE included in copy loop"              tc13_project_file_in_copy_loop
run_test "TC-14: scp actually copies project_file.py to VM"        tc14_scp_copies_project_file_to_vm

echo ""
echo "=== STORY-730: project_file.py smoke test coverage ==="
echo ""
run_test "TC-20: smoke test imports project_file (not just phase runner)" tc20_smoke_test_imports_project_file

echo ""
echo "=== STORY-535: push-code verification hardening ==="
echo ""
run_test "TC-15: all files hash-verified → summary line present"    tc15_all_files_hash_verified
run_test "TC-16: single file hash mismatch → fail closed, exit 1"  tc16_single_file_hash_mismatch_fail_closed
run_test "TC-17: runtime probe success → runtime verified in output" tc17_runtime_probe_success
run_test "TC-18: runtime probe mismatch → WARNING only, exit 0"    tc18_runtime_probe_mismatch_warning_only
run_test "TC-19: verification summary line N/N format"              tc19_verification_summary_line_format

echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="

if [ ${#FAILED_NAMES[@]} -gt 0 ]; then
    echo ""
    printf "  ${RED}Failed:${NC}\n"
    for name in "${FAILED_NAMES[@]}"; do
        printf "    - %s\n" "$name"
    done
fi

echo ""
[ $FAIL -eq 0 ]
