## 2026-04-22 01:40Z — Morris acknowledges STORY-516/514/511 operational changes
- SDLC framework wired (slash commands, personas per phase, 17 compliance tests)
- Role-scoped API keys: MANAGER role for Morris, AGENT keys on VMs (no DELETE)
- DELETE /dispatch/queue/{id} gated: Mark-enqueued stories = ADMIN only, reason≥10 chars required, kill-cancel-audit.md logged
- Session resume (STORY-511): phases reuse SDK sessions, watch for crash-context bleed
- sdlc-framework-sync skill: Sunday 06:00 UTC submodule pull + compliance test + VM verify
- Monitoring: persona evidence in PRs, kill-cancel-audit.md self-review, phantom-claim <30s guard, rate-limit recovery at 2am UTC, no over-action on Teams requests
- NEVER: edit skills on VMs, edit PHASE_MAP prompts, proxy destructive cmds via agents, cancel Mark-dispatched stories, deploy VMs without submodule init
