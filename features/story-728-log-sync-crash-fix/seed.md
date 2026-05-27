# STORY-728: Fix Log-Sync Crash Loop on Agent VMs

**Frontend:** false

## Summary
The `gateway-log-sync.service` and `dispatch-log-sync.service` units on the
derrick agent VM entered a tight crash-restart loop on 2026-04-26 (~12:04
UTC) because the bash redirect target `/tmp/hermes-combined.log` returned
`Permission denied` and systemd restarted both units every 5 seconds with no
`StartLimit*` cap. The flood (~16,678 failed-exec lines on a single boot,
contributing to a 134 MB `syslog.1`) consumed disk/IO, the box rebooted at
12:47 UTC, and there is no logrotate or `journald` size cap to contain
recurrence. This story hardens the two log-sync units (start-limit + tee
fallback), adds a logrotate rule for the combined log, caps `journald` disk
use, and adds a smoke test that imports the unit files and asserts the
required hardening directives are present.

## Scope
small

## Root Cause
1. **Crash trigger**: `gateway-log-sync.service` ExecStart is
   `bash -c 'journalctl -u hermes-gateway -f ... >> /tmp/hermes-combined.log'`.
   When `/tmp/hermes-combined.log` is owned by another uid or has been
   recreated 0644 by a different process (today the file was ultimately
   `hermes:hermes 0644`, but during the failure window the redirect
   returned `Permission denied`), bash exits rc=1 immediately. The `>>`
   redirect happens *before* the journalctl pipe runs, so there is no
   self-healing path.
2. **Loop amplifier**: both `gateway-log-sync.service` and
   `dispatch-log-sync.service` use `Restart=on-failure` with
   `RestartSec=5` and **no** `StartLimitIntervalSec`/`StartLimitBurst`,
   so systemd restarts them ~12 times/minute per service — indefinitely.
3. **No disk back-pressure**: `journald.conf` ships with all
   `SystemMaxUse=` limits commented out (default ~10% of /). Log archives
   grew to 218 MB and `/var/log/syslog.1` reached 134 MB before rotation,
   leaving no slack for the failure flood.
4. **No file rotation**: `/tmp/hermes-combined.log` has no logrotate
   policy. It grows unbounded and Promtail reads it forever.
5. **Foot-gun in repo**: `deployment/vm/hermes-log-sync.service` (the
   on-disk template that ships to new VMs) uses
   `ExecStart=/bin/bash -c "while true; do ... ; sleep 30; done"` with
   `Restart=always` and *no* start-limit, so any new VM provisioned
   from this file inherits the same loop.

## Codebase Context
- Files to modify:
  - `deployment/vm/hermes-log-sync.service` — add `StartLimitIntervalSec`,
    `StartLimitBurst`, change `Restart=always` → `Restart=on-failure`,
    use `tee -a` (continues on partial-write errors) instead of bare `>>`,
    add `ExecStartPre` that `touch`es the target and `chmod`s it.
  - `deployment/vm/hermes-log-sync.sh` and
    `deployment/promtail/hermes-log-sync.sh` — guard each `journalctl >>`
    with a writable-target check; fall back to `logger -t hermes-log-sync`
    if the file cannot be opened so the loop never crashes.
  - `deployment/vm/dispatch-poller.service` — add
    `StartLimitIntervalSec=300` and `StartLimitBurst=5` so a misbehaving
    poller cannot cause the same flood.
  - `deployment/vm/cloud-init.yaml` — install the new logrotate file and
    journald drop-in on first boot.
  - `deployment/vm/SETUP-CHECKLIST.md` and
    `deployment/vm/MORRIS-SETUP-LOG.md` — document the new ownership +
    permission requirement on `/tmp/hermes-combined.log`.
- New files:
  - `deployment/vm/etc/logrotate.d/hermes-combined` — daily, 7-day
    retention, compress, missingok, notifempty, copytruncate.
  - `deployment/vm/etc/systemd/journald.conf.d/50-agent-cap.conf` —
    `SystemMaxUse=500M`, `SystemKeepFree=1G`, `SystemMaxFileSize=50M`.
  - `deployment/vm/log-sync.service.tmpl` (or fold into the existing
    `hermes-log-sync.service`) with hardened directives.
  - `tests/deployment/test_log_sync_hardening.py` — parses the unit and
    rotation files and asserts the required directives are present.

## Acceptance Criteria
- [ ] `deployment/vm/hermes-log-sync.service` (and any
      `dispatch-log-sync.service` / `gateway-log-sync.service` template
      shipped from this repo) declare
      `StartLimitIntervalSec=300` and `StartLimitBurst=5`.
- [ ] The same units use `Restart=on-failure` with `RestartSec=10` (not
      5) so a transient permission error does not produce >6 restarts in
      60 seconds.
- [ ] The bash command that writes the combined log uses `tee -a`
      (or an equivalent that survives a single failed write) and is
      preceded by an `ExecStartPre=` that creates the file with the
      correct owner and mode (`0664 hermes:hermes`).
- [ ] A logrotate rule at `deployment/vm/etc/logrotate.d/hermes-combined`
      exists with: `daily`, `rotate 7`, `compress`, `missingok`,
      `notifempty`, `copytruncate`, `maxsize 100M`.
- [ ] A journald drop-in at
      `deployment/vm/etc/systemd/journald.conf.d/50-agent-cap.conf`
      exists with `SystemMaxUse=500M`, `SystemKeepFree=1G`,
      `SystemMaxFileSize=50M`.
- [ ] `deployment/vm/cloud-init.yaml` installs both new files on first
      boot and runs `systemctl restart systemd-journald` once.
- [ ] `tests/deployment/test_log_sync_hardening.py` parses the four
      modified/added files (service unit, logrotate, journald drop-in,
      cloud-init) and asserts the required keys/values; it must run
      under `pytest -q` with no system dependencies.
- [ ] `deployment/vm/SETUP-CHECKLIST.md` and `MORRIS-SETUP-LOG.md` add a
      one-line note: "Log-sync units must run as the same uid that owns
      `/tmp/hermes-combined.log` — see STORY-728."
- [ ] `git grep "Restart=always" deployment/vm/*.service` returns no
      matches for any `*log-sync*.service` template.

## Test Criteria
1. `pytest tests/deployment/test_log_sync_hardening.py -q` — all assertions
   green; the test reads each modified file via `pathlib` and matches with
   simple regex / `configparser` so it requires no systemd or root.
2. Local smoke check (manual, not gated): `systemd-analyze verify
   deployment/vm/hermes-log-sync.service` returns 0.
3. Failure-injection script (manual, on a scratch VM): `chmod 000
   /tmp/hermes-combined.log && systemctl restart gateway-log-sync &&
   sleep 60 && systemctl is-active gateway-log-sync` — expected: the
   service enters `failed` state within ~60s (5 restarts × 10s) and
   stays there instead of looping; `journalctl -u gateway-log-sync
   --since "1 min ago" | wc -l` < 30.
4. `git grep -l "RestartSec=5" deployment/vm/*.service` shows no
   `*log-sync*` matches.

## Validation
1. After deploy, on the derrick VM:
   - `systemctl show gateway-log-sync.service | grep -E
     '^(StartLimit|Restart=|RestartSec)'` must show
     `StartLimitIntervalSec=300`, `StartLimitBurst=5`, `Restart=on-failure`,
     `RestartSec=10s`.
   - `cat /etc/logrotate.d/hermes-combined` matches the repo file.
   - `journalctl --disk-usage` returns < 500M after first rotation.
   - `ls -l /tmp/hermes-combined.log` shows owner `hermes:hermes` and
     mode `-rw-rw-r--`.
2. 24h after deploy:
   - `journalctl -u gateway-log-sync.service --since "24h ago" |
     grep -c "Permission denied"` is 0.
   - `du -sh /var/log/syslog*` total < 50 MB.
3. Failure-injection rerun on a non-prod agent VM matches step 3 of Test
   Criteria.

## Out of Scope
- Migrating `/tmp/hermes-combined.log` to `/var/log/morris/` (tracked
  separately; would require Promtail config rewrite on every agent).
- Replacing the `journalctl -f | tee` shim with a proper Promtail
  systemd_journal source (separate refactor).
