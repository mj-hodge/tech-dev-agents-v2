#!/usr/bin/env bash
# agent-doctor.sh — self-check for an agent VM. Runs on the VM itself (as
# hermes or azureagent; uses sudo -u hermes for user-scoped checks). Prints
# one line per check in the form: "[OK|FAIL|WARN] <check-name> <detail>".
#
# Exit codes:
#   0 — all OK (WARN is allowed)
#   1 — at least one FAIL
#
# Usage:
#   ./agent-doctor.sh              # local self-check
#   ./agent-doctor.sh --repair     # attempt to fix what's fixable (re-clone
#                                    global ~/.sdlc, recreate symlinks)
#   ./agent-doctor.sh --json       # machine-readable output for Morris
#
# Design goals:
#   * Fast (<30s total) — Morris calls this weekly and on-demand
#   * Idempotent — safe to run repeatedly
#   * No side effects without --repair
#   * Covers: framework install, skill resolution, phase runner code, SDK
#     tool code, CLI tools (gh, git, python, claude), ops-console reach,
#     submodule freshness.

set -u  # NOT -e — we want individual check failures, not whole-script exit

SDLC_REPO_URL="${SDLC_REPO_URL:-https://github.com/hpi-gorillacommerce/sdlc-framework.git}"
REPAIR=0
JSON=0

for arg in "$@"; do
  case "$arg" in
    --repair) REPAIR=1 ;;
    --json) JSON=1 ;;
    *) echo "unknown flag: $arg"; exit 2 ;;
  esac
done

FAILS=0
RESULTS=()

emit() {
  local status="$1" name="$2" detail="$3"
  RESULTS+=("$status|$name|$detail")
  if [ "$status" = "FAIL" ]; then FAILS=$((FAILS+1)); fi
  [ "$JSON" -eq 1 ] && return 0
  printf "[%-4s] %-40s %s\n" "$status" "$name" "$detail"
}

# ---- Framework install ----------------------------------------------------
SDLC="/home/hermes/.sdlc"
if sudo -u hermes test -d "$SDLC/.git"; then
  local_sha=$(sudo -u hermes git -C "$SDLC" rev-parse HEAD 2>/dev/null || echo unknown)
  sudo -u hermes git -C "$SDLC" fetch origin main >/dev/null 2>&1
  remote_sha=$(sudo -u hermes git -C "$SDLC" rev-parse origin/main 2>/dev/null || echo unknown)
  if [ "$local_sha" = "$remote_sha" ]; then
    emit OK "sdlc-global-install" "~/.sdlc at $local_sha (up to date)"
  else
    emit WARN "sdlc-global-install" "~/.sdlc at $local_sha, origin at $remote_sha — run: git -C $SDLC pull"
    if [ "$REPAIR" -eq 1 ]; then
      sudo -u hermes git -C "$SDLC" reset --hard origin/main >/dev/null 2>&1 \
        && emit OK "repair-sdlc-pull" "reset to $remote_sha" \
        || emit FAIL "repair-sdlc-pull" "reset failed"
    fi
  fi
  origin_url=$(sudo -u hermes git -C "$SDLC" remote get-url origin 2>/dev/null)
  if echo "$origin_url" | grep -q "hpi-gorillacommerce/sdlc-framework"; then
    emit OK "sdlc-remote-url" "$origin_url"
  else
    emit FAIL "sdlc-remote-url" "expected hpi-gorillacommerce/sdlc-framework, got $origin_url"
  fi
else
  emit FAIL "sdlc-global-install" "~/.sdlc missing — install with: sudo -u hermes git clone $SDLC_REPO_URL $SDLC"
  if [ "$REPAIR" -eq 1 ]; then
    sudo -u hermes git clone "$SDLC_REPO_URL" "$SDLC" >/dev/null 2>&1 \
      && { sudo chmod +x "$SDLC/bin/sdlc"; emit OK "repair-sdlc-clone" "cloned from $SDLC_REPO_URL"; } \
      || emit FAIL "repair-sdlc-clone" "clone failed"
  fi
fi

# Skill file sanity
if sudo -u hermes test -f "$SDLC/skills/phase-1/SKILL.md" && \
   sudo -u hermes test -f "$SDLC/skills/phase-8/SKILL.md" && \
   sudo -u hermes test -f "$SDLC/agents/phase-1-seed.md"; then
  emit OK "sdlc-content" "skills + personas present"
else
  emit FAIL "sdlc-content" "skills or personas missing under $SDLC — submodule not initialized?"
fi

# ---- Per-repo symlinks ----------------------------------------------------
# Enumerate as hermes because /home/hermes/dev may not be world-readable by
# azureagent. The whole check runs inside one sudo -u hermes block.
REPO_OK=0
REPO_FAIL=0
REPO_FAIL_NAMES=""
# shellcheck disable=SC2016
read_result=$(sudo -u hermes bash <<HERMES_EOF
SDLC="$SDLC"
REPAIR=$REPAIR
ok=0; fail=0; names=""
for repo_dir in /home/hermes/dev/hpi-gorillacommerce/*/; do
  repo=\$(basename "\$repo_dir")
  [ "\$repo" = "tech-dev-agents" ] && continue
  [ -d "\$repo_dir/.git" ] || continue
  if [ -f "\$repo_dir/.claude/skills/phase-1/SKILL.md" ]; then
    ok=\$((ok+1))
  else
    fail=\$((fail+1))
    names="\$names \$repo"
    if [ "\$REPAIR" = "1" ]; then
      if [ -L "\$repo_dir/.sdlc" ]; then
        t=\$(readlink "\$repo_dir/.sdlc"); [ -d "\$t" ] || rm "\$repo_dir/.sdlc"
      fi
      if [ -d "\$repo_dir/.sdlc" ] && [ -z "\$(ls -A "\$repo_dir/.sdlc" 2>/dev/null)" ]; then
        rmdir "\$repo_dir/.sdlc"
      fi
      [ -e "\$repo_dir/.sdlc" ] || ln -s "\$SDLC" "\$repo_dir/.sdlc"
      mkdir -p "\$repo_dir/.claude"
      [ -e "\$repo_dir/.claude/skills" ] || ln -s "\$SDLC/skills" "\$repo_dir/.claude/skills"
      excl="\$repo_dir/.git/info/exclude"
      touch "\$excl"
      grep -qxF '.sdlc' "\$excl" || echo '.sdlc' >> "\$excl"
      grep -qxF '.claude/skills' "\$excl" || echo '.claude/skills' >> "\$excl"
    fi
  fi
done
echo "\$ok|\$fail|\$names"
HERMES_EOF
)
IFS='|' read -r REPO_OK REPO_FAIL REPO_FAIL_NAMES <<<"$read_result"
if [ "$REPO_FAIL" -eq 0 ]; then
  emit OK "per-repo-symlinks" "$REPO_OK/$REPO_OK repos resolve phase-1 skill"
elif [ "$REPAIR" -eq 1 ]; then
  emit WARN "per-repo-symlinks" "$REPO_FAIL were broken, repaired:$REPO_FAIL_NAMES"
else
  emit FAIL "per-repo-symlinks" "$REPO_FAIL of $((REPO_OK+REPO_FAIL)) repos missing symlinks:$REPO_FAIL_NAMES — run with --repair"
fi

# ---- Deployed code files --------------------------------------------------
# Only required on dev-agent VMs (those that have the dispatch-poller unit).
# Managers (morris) run different code under /opt/agent and should not be
# required to have the phase runner.
IS_DEV_AGENT=0
systemctl list-unit-files dispatch-poller.service >/dev/null 2>&1 && IS_DEV_AGENT=1
if [ "$IS_DEV_AGENT" = "1" ]; then
  for f in sdlc_phase_runner.py dispatch_poller.py claude_sdk_tool.py terminal_guard.py run_dispatch_poller.py; do
    if sudo test -f "/opt/agent/$f"; then
      emit OK "code-$f" "present"
    else
      emit FAIL "code-$f" "/opt/agent/$f missing — run push-code.sh"
    fi
  done
else
  emit OK "code-files" "skipped (manager VM — no dispatch-poller)"
fi

# ---- CLI dependencies -----------------------------------------------------
for cmd in git python3 gh claude; do
  if command -v "$cmd" >/dev/null 2>&1; then
    ver=$("$cmd" --version 2>&1 | head -1 | tr -d '\n')
    emit OK "cli-$cmd" "$ver"
  else
    emit FAIL "cli-$cmd" "not on PATH"
  fi
done

# ---- Services -------------------------------------------------------------
# dispatch-poller only exists on dev-agent VMs (daisy/devon/dan/derrick).
# Morris is a manager and deliberately doesn't have it. Treat "not installed"
# as SKIP, not FAIL. Treat "masked" (rate-limited) as WARN.
poller_state=$(sudo systemctl is-active dispatch-poller 2>/dev/null)
poller_enabled=$(sudo systemctl is-enabled dispatch-poller 2>/dev/null)
[ -z "$poller_state" ] && poller_state="not-installed"
case "$poller_state" in
  active) emit OK "svc-dispatch-poller" "active" ;;
  not-installed) emit OK "svc-dispatch-poller" "not installed (manager-only VM — expected)" ;;
  inactive) emit WARN "svc-dispatch-poller" "inactive${poller_enabled:+ (enabled=$poller_enabled)} — rate-limit masked or stopped" ;;
  failed) emit FAIL "svc-dispatch-poller" "failed — check journalctl -u dispatch-poller" ;;
  *) emit WARN "svc-dispatch-poller" "$poller_state" ;;
esac

# Phase runner code is also dev-agent only — skip on managers
if ! systemctl list-unit-files dispatch-poller.service >/dev/null 2>&1; then
  emit OK "role" "manager (no dispatch-poller expected)"
fi

# ---- Ops console reach ----------------------------------------------------
OPS_URL=$(sudo grep -oE '^OPS_CONSOLE_URL=.*' /opt/agent/.env 2>/dev/null | cut -d= -f2)
if [ -n "$OPS_URL" ]; then
  code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 "$OPS_URL/healthz" 2>/dev/null || echo 000)
  if [ "$code" = "200" ] || [ "$code" = "204" ]; then
    emit OK "ops-console-reach" "$OPS_URL -> $code"
  else
    emit FAIL "ops-console-reach" "$OPS_URL -> $code"
  fi
else
  emit FAIL "ops-console-reach" "OPS_CONSOLE_URL missing from /opt/agent/.env"
fi

# ---- JSON out -------------------------------------------------------------
if [ "$JSON" -eq 1 ]; then
  printf '{"host":"%s","fails":%d,"checks":[' "$(hostname)" "$FAILS"
  first=1
  for r in "${RESULTS[@]}"; do
    IFS='|' read -r s n d <<< "$r"
    [ "$first" -eq 1 ] || printf ','
    # Escape quotes in detail
    d_esc=$(printf '%s' "$d" | sed 's/"/\\"/g')
    printf '{"status":"%s","name":"%s","detail":"%s"}' "$s" "$n" "$d_esc"
    first=0
  done
  printf ']}\n'
fi

exit $([ "$FAILS" -eq 0 ] && echo 0 || echo 1)
