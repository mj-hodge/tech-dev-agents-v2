# STORY-730: hermes-log-sync — proper RuntimeDirectory fix

**Frontend:** false

## Summary
Replace the ExecStartPre ownership workaround in hermes-log-sync.service with
systemd's RuntimeDirectory= directive. This lets systemd create and own
/run/hermes-log-sync/ at service start, eliminating the permission race that
caused the 2026-04-26 crash-loop.

## Scope
small

## Background
STORY-728 added StartLimitBurst=5 and ExecStartPre as an emergency safety belt.
The ExecStartPre guard works but is fragile: it creates the file in /tmp/ which
is cleared on reboot, and the ownership check runs in a subshell that could fail
silently. RuntimeDirectory= is the correct systemd-native solution.

## Implementation
In hermes-log-sync.service [Service] section:
- Replace: ExecStartPre=... (the /tmp ownership workaround)
- Add: RuntimeDirectory=hermes-log-sync
- Add: RuntimeDirectoryMode=0755
- Change log path from /tmp/hermes-combined.log to /run/hermes-log-sync/combined.log
- Update hermes-log-sync.sh to write to /run/hermes-log-sync/combined.log
- Update Promtail config to read from /run/hermes-log-sync/combined.log

## Codebase Context
- deployment/vm/hermes-log-sync.service — service file
- deployment/vm/hermes-log-sync.sh (if exists) — log sync script
- deployment/vm/cloud-init.yaml — Promtail config references

## Acceptance Criteria
- [ ] hermes-log-sync.service uses RuntimeDirectory=hermes-log-sync
- [ ] No ExecStartPre workaround needed
- [ ] /run/hermes-log-sync/ created by systemd with correct ownership on service start
- [ ] Service survives reboot without permission errors
- [ ] Promtail still picks up logs from new path

## Test Criteria
- systemctl start hermes-log-sync on a clean VM (no pre-existing files): service starts, /run/hermes-log-sync/combined.log created, owned by hermes
- systemctl stop + start: no errors, file recreated cleanly
- Reboot: service auto-starts, no permission errors in journalctl

## Validation
journalctl -u hermes-log-sync --since "1 min ago" shows no permission errors after reboot.
