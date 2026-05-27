#!/usr/bin/env bash
# sdlc-pull-cron.sh — daily SDLC submodule refresh for Morris VM.
# Keeps .sdlc/ (the sdlc-framework submodule) in sync with upstream.
# No LLM invocation.

set -u
LOG=/var/log/morris-sdlc-pull.log
TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
REPO=/home/hermes/workspace/tech-dev-agents

echo "$TS === sdlc-pull START ===" >> "$LOG"

# Pull main repo (fast-forward only — never auto-merge diverged state)
git -C "$REPO" pull --ff-only origin main >> "$LOG" 2>&1
RC_PULL=$?

# Update the .sdlc submodule to whatever SHA main now points to
git -C "$REPO" submodule update --init --recursive >> "$LOG" 2>&1
RC_SUB=$?

if [[ $RC_PULL -eq 0 && $RC_SUB -eq 0 ]]; then
    echo "$TS sdlc-pull OK" >> "$LOG"
else
    echo "$TS sdlc-pull FAIL (pull=$RC_PULL sub=$RC_SUB)" >> "$LOG"
fi

echo "$TS === sdlc-pull DONE ===" >> "$LOG"
