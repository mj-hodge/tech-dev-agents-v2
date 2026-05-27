# STORY-730: Test Design — hermes-log-sync RuntimeDirectory fix

## Scope
small

## Approach
Contract tests against the service file and shell scripts verify the correct
directives are present before any VM is touched. Behaviour tests cover the
symlink backward-compatibility guarantee so existing writers (/tmp path) keep
working after the fix.

## Test Cases

### T1 — Service file has RuntimeDirectory directive
- Service file contains `RuntimeDirectory=hermes-log-sync`
- Service file contains `RuntimeDirectoryMode=0755`
- Service file does NOT contain the old ExecStartPre bash ownership workaround

### T2 — Service file symlink ExecStartPre is simpler
- ExecStartPre creates a symlink: `/tmp/hermes-combined.log → /run/hermes-log-sync/combined.log`
- ExecStartPre uses `/bin/ln -sf` not a multi-step bash install command

### T3 — log-sync shell script writes to runtime dir
- `deployment/vm/hermes-log-sync.sh` references `/run/hermes-log-sync/combined.log`
- `deployment/promtail/hermes-log-sync.sh` references `/run/hermes-log-sync/combined.log`
- Neither script references `/tmp/hermes-combined.log` as a write target

### T4 — Promtail configs updated to runtime dir path
- `deployment/vm/promtail-config.yaml` `__path__` entries point to `/run/hermes-log-sync/combined.log`
- `deployment/vm/promtail-config-derrick.yaml` same
- `deployment/promtail/promtail-config-dan.yaml` same
- `deployment/promtail/promtail-config-derrick.yaml` same

### T5 — Backward-compatibility: /tmp path not broken for other writers
- Service file ExecStartPre creates the symlink so `/tmp/hermes-combined.log`
  remains a valid write target (dispatch-poller.service, crons, watchdog all
  still append there and it arrives in Loki via the symlink)

### T6 — StartLimitBurst safety belt preserved from STORY-728
- Service file still contains `StartLimitBurst=5`
- Service file still contains `StartLimitIntervalSec=300`
- Service file still contains `Restart=on-failure` (not `always`)
