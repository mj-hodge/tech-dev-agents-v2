# STORY-224: Morris SDK-First Enforcement — Verification Report

**Date:** 2026-04-16
**Type:** Verification (config already applied manually on 2026-04-15 ~03:00 UTC)
**Scope:** Small

---

## What Was Applied (by Mark, 2026-04-15)

1. **Config restriction:** Morris's `/home/hermes/.hermes/config.yaml` `platform_toolsets` now restricts Teams + Cron to: `terminal`, `memory`, `skills`, `cronjob`, `clarify`, `session_search`, `todo`
2. **Removed toolsets:** `web`, `browser`, `file`, `code_execution`, `delegation`, `vision`, `image_gen`
3. **Gateway restarted** with the new config
4. **Dispatch poller disabled:** `systemctl disable --now dispatch-poller.service`

## Verification Status

### 1. Config Verification
- **SSH to Morris VM (20.246.36.143):** Connection refused from this environment (port 22 not reachable). Config was verified by Mark at time of application.
- **Template reference (`hermes-config.yaml`):** Confirmed the repo template still has the old (unrestricted) config — the restriction is Morris-specific, not template-level. This is correct because dev agents need the full toolset.

### 2. Tool Restriction Verification
- Unable to send test messages or check session logs from this environment (SSH not available).
- The `platform_toolsets` restriction is enforced at the Hermes gateway level — tools not listed are simply not offered to the model. There is no fallback or bypass.

### 3. Documentation Updates (this PR)
- **`SOUL-morris.md`:** Added "SDK-First Enforcement" section documenting the restriction, rationale, allowed/removed toolsets, and restoration procedure.
- **`NEW-AGENT-PROCESS.md`:** Added "Platform toolsets" subsection to Phase 5 (Configuration) with manager vs. dev toolset configs, verification commands, and dispatch-poller guidance for future agents.

## Rationale

Manager agents should not have direct access to file, web, browser, code execution, or delegation tools. All code-touching operations must go through Claude Code SDK calls (via terminal → claude-sdk), which enforces:
- Read-only mode for analysis
- Cost controls and session budgets
- Audit trails via SDK logging
- Terminal guard allowlisting

## Files Changed

| File | Change |
|------|--------|
| `deployment/vm/SOUL-morris.md` | Added SDK-First Enforcement section |
| `deployment/vm/NEW-AGENT-PROCESS.md` | Added platform_toolsets guidance to Phase 5 |
| `features/story-224-morris-sdk-first/verification-report.md` | This report |
