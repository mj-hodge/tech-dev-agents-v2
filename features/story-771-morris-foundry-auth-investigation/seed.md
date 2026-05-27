# STORY-771 — Investigate + Fix Morris Foundry Auth Failure (Teams Bridge Path)

## Overview
| Field | Value |
|-------|-------|
| Mode | feature_update |
| Scope | small |
| Feature Name | Morris Foundry auth fix |
| Phase Path | 1 → 7 → 8 → Done |
| Repo | tech-dev-agents |
| Frontend | false |

## Problem Statement

When Mark messages Morris via Teams, the response path returns:
```
HTTP 500: Failed to authenticate to backend endpoint.
RequestId = req_011CaZ4z3HFT3ypYAu5yS3si
```

This is **NOT** the Claude OAuth on Morris's VM (separate issue, also expired but tracked separately). This is the **Foundry / Anthropic-via-Foundry endpoint** auth that Morris's M365/Teams bridge uses.

**Evidence captured 2026-04-30:**
- The error message is "Failed to authenticate to **backend endpoint**" — not "model unavailable" — indicating an OAuth/credential problem against the Foundry side.
- Morris's `sdk_dispatch_post_ratelimit.py` script visible on his VM has a fallback that wraps tasks in `claude --permission-mode bypassPermissions -p '<task>'` — invoked when normal Foundry routing fails. This is consistent with Foundry being out.
- Morris's heartbeat cron is `inactive` overnight by design (STORY-734). When it resumes at 12:00 UTC, will it work? Unknown until we know what specific credential/token expired.

## Target User / Use Case

**User:** Mark + Morris.
**Today:** Mark messages Morris via Teams → HTTP 500. Morris cannot respond. Fleet shepherding via Morris's Teams interaction is broken.
**After this story:** Foundry auth restored. Mark can talk to Morris via Teams again. Morris's M365 bridge functions normally.

## Success Criteria

1. **SC-1 — Identify the failing credential.** Locate the Foundry endpoint Morris uses (likely an Azure AI Foundry inference endpoint), the API key / OAuth token / managed identity it presents, and confirm that credential is what's failing. Document in `features/story-771-*/diagnosis.md`.
2. **SC-2 — Determine renewal mechanism.** Is the credential a static API key, an OAuth token with refresh, a managed identity, or a system-assigned identity? Document the renewal/rotation procedure.
3. **SC-3 — Restore auth.** Apply the appropriate renewal — rotate the key in Azure / re-grant managed-identity scope / refresh OAuth — until Mark can successfully Teams-message Morris and get a non-500 response.
4. **SC-4 — Add expiry alerting.** Whatever credential expired, add a Morris fleet-vigilance check (or Azure Monitor alarm) for "expires in < 7 days" so this doesn't silently fail again. Tie into STORY-767's Check 16 if 767 is shipping in parallel.
5. **SC-5 — Document the post-mortem.** When did the credential expire? Did rotation cron exist? Why didn't it run? Documented in `state/morris/incident-2026-04-30-foundry-auth.md`.
6. **SC-6 — Test end-to-end.** Mark sends Morris a Teams message ("test ping"), receives a non-error response. Documented in PR body with timestamps.

## Verification Plan

| SC | Command | Expected |
|----|---------|----------|
| SC-1 | `cat features/story-771-*/diagnosis.md` shows specific credential type + endpoint URL + last successful auth timestamp | File exists, content specific |
| SC-3 | Mark's Teams test ping returns a Morris reply (not HTTP 500) | Documented timestamp + screenshot in PR body |
| SC-4 | New fleet-vigilance check or Azure alarm config exists | grep / az command output |
| SC-5 | post-mortem md exists | grep -c "STORY-771" returns 1+ |

## Test Criteria

This story is primarily diagnostic + ops, NOT code-heavy. The test criterion is: **Mark + Morris Teams round-trip works, end-to-end, after the fix.**

If the fix involves a new code path or auth handler, unit tests for that handler are required (mock the Foundry response).

## Validation

| Step | Command | Pass |
|------|---------|------|
| 1 | `pytest tests/ -x --ignore=tests/e2e -q` | Zero regressions |
| 2 | Mark Teams-messages Morris with "Status?" | Reply received (non-500) |
| 3 | Morris's overnight heartbeat at 12:00 UTC works | Loki shows successful heartbeat run |

## Acceptance Criteria

- [ ] AC-1: `diagnosis.md` identifies the failing credential by name + Azure resource path + endpoint URL.
- [ ] AC-2: Renewal procedure documented in `state/morris/foundry-auth-runbook.md`.
- [ ] AC-3: Renewal applied; Teams round-trip with Morris works.
- [ ] AC-4: Expiry alarm added (fleet-vigilance check OR Azure Monitor metric alert).
- [ ] AC-5: Post-mortem written with: when did it expire, why didn't auto-renewal work, what to change.
- [ ] AC-6: If a code change is part of the fix (e.g., new refresh handler), tests pass with zero regressions.
- [ ] AC-7: Logging — when Foundry auth fails, the failure log includes the specific credential name + a one-line "renew via" pointer to the runbook.

## Constraints
| Constraint | Value |
|------------|-------|
| Budget | small — diagnostic + ops + maybe small code change |
| Timeline | URGENT — Morris Teams interaction is a daily tool; broken since at least 2026-04-30 morning |
| Mark-required steps | YES — Azure portal access, secret rotation, Teams testing |

## Security Constraints
- [ ] Rotated credentials MUST be stored in Azure Key Vault (existing pattern).
- [ ] Old expired credentials revoked, not just superseded.
- [ ] Runbook does NOT include the actual secret values — only the procedure.
- [ ] Expiry alarm has a recipient (Mark via Teams or email).

## Operational Lifecycle
- **Monitoring:** new alarm fires when credential expires in < 7 days.
- **Rotation cadence:** documented in runbook; quarterly minimum, monthly preferred for production-critical creds.
- **Incident response:** post-mortem in state/morris/.

## Boundaries

| Always Do | Ask First | Never Do |
|-----------|-----------|----------|
| Diagnose first — don't rotate blindly | Whether to migrate Foundry auth to managed identity vs current pattern | Print full secret values in PR or commits |
| Document which credential failed | Whether to add a 24/7 monitor (vs daily) — current scope: 7-day-out alarm | Roll back the script's bypassPermissions fallback as part of this story (separate concern) |
| Add an alarm so it doesn't silently fail again | Whether to migrate from Foundry to direct Anthropic API (architectural change — separate story) | Skip the post-mortem |
| Verify Morris's overnight heartbeat works after fix | Whether the broken auth caused other Morris cron failures (likely yes — investigate) | Manually edit Morris's runtime config without committing the equivalent change to repo |

## Files to Modify

- `features/story-771-*/diagnosis.md` — **new**, the diagnostic findings.
- `state/morris/foundry-auth-runbook.md` — **new**, renewal procedure.
- `state/morris/incident-2026-04-30-foundry-auth.md` — **new**, post-mortem.
- `deployment/morris/scripts/check-foundry-auth.py` — **new** (if AC-4 implements as a Python health check) OR Azure ARM/CLI snippet.
- `deployment/vm/skills/fleet-vigilance/SKILL.md` — add Check 16 or extend existing auth-drift check.
- `.project`, `backlog.md` — tracking.

## Files to NOT Modify

- `sdk_dispatch_post_ratelimit.py` — the fallback path; harden separately if needed.
- Existing Foundry deployment configs unless the fix requires them.
- Mark's local `.env` files.

## Done Looks Like

```
$ # Mark sends Morris in Teams: "ping"
[Morris Teams response]
"Pong. Fleet status: 4 agents healthy, 0 stuck, queue=N pending."

$ cat features/story-771-*/diagnosis.md | head -10
Failed credential: <name>
Endpoint: <url>
Failed since: 2026-04-30T01:57:50Z (approx)
Renewal procedure: see state/morris/foundry-auth-runbook.md
```

## Escalation Contract

1. **Credential is Mark's personal Azure identity** — Mark is the only one who can rotate; this story documents and Mark executes.
2. **Foundry endpoint itself is deprecated** — escalate to Mark; consider migration to direct Anthropic API as a separate larger story.
3. **Auth renewal requires service principal creation Mark hasn't authorized** — STOP; ask before creating new Azure resources.
4. **The fix involves disabling Foundry entirely and going direct** — that's an architectural change; needs Mark's explicit go-ahead, not a unilateral fix.

**Default if no story-specific rule fires:** if more than 1 turn is spent guessing, write QUESTION.md and pause.

## Codebase Context

| Aspect | Details |
|--------|---------|
| Affected systems | Morris's M365/Teams bridge, Foundry endpoint auth |
| Reference incident | 2026-04-30 — Mark's Teams round-trip with Morris returns HTTP 500. RequestId in error message links to Anthropic-side trace. |
| Symptom | "Failed to authenticate to backend endpoint" |
| Likely cause | Foundry / Azure AI auth credential expired or rotated externally |
| Test pattern | Manual end-to-end Teams ping. Optionally mock-tested Python health check. |

## Out of Scope

- Migration from Foundry to direct Anthropic API (architectural; separate story).
- Hardening of `sdk_dispatch_post_ratelimit.py` fallback (separate small story).
- Morris's Claude OAuth expiry on hermes user (separate issue, Mark-only fix via `claude /login`).

## Notes for Implementer

- Start with the diagnosis. Do NOT rotate before identifying which credential failed.
- Mark's earlier observation: "the error specifically says foundry" — that's the lead. Foundry endpoint URL likely visible in Morris's M365 bridge config (probably `~/.hermes/scripts/` or `/opt/agent/.env`).
- Many Foundry creds have a default 90-day expiry. If that's it, set up a quarterly rotation cron tied to Azure Key Vault.
- If the fix is "Mark rotates the credential in Azure portal", document it but Mark executes the rotation himself.
