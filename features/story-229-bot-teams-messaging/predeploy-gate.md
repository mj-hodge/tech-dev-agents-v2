# STORY-229: Pre-Deploy Gate — Bot-to-Bot Teams Messaging via App Token

**Phase:** 11 (Pre-Deploy Gate)
**Date:** 2026-04-15

## Gate Checklist

| Check | Status | Notes |
|-------|--------|-------|
| All tests GREEN | PASS | 14/14 new tests pass; 108/108 existing deployment tests pass |
| No `/me/` in runtime code | PASS | Only in docstring (explaining what was replaced) |
| No M365 CLI references in runtime | PASS | Logger tag `teams-m365` retained for log continuity |
| MSAL import guarded | PASS | `try/except ImportError` with `_MSAL_AVAILABLE` flag |
| `msal` in requirements.txt | PASS | Already present from STORY-228 |
| Secrets not committed | PASS | No env var values in code; all read from `os.environ` |
| Graceful degradation | PASS | Presence failures logged + swallowed; missing MSAL config caught by `check_teams_requirements()` |
| Backward compatibility | PASS | Same env var names as `hermes/teams.py`; new env var `TEAMS_BOT_USER_ID` already existed |

## Deployment Requirements

Before deploying to VM:

1. **Entra app registration:** Grant application permissions `Chat.ReadWrite.All`, `Presence.ReadWrite.All`, `ChatMessage.Send`
2. **Admin consent:** Required once for the tenant (application permissions always require admin consent)
3. **Environment variables:** Ensure `TEAMS_CLIENT_ID`, `TEAMS_CLIENT_SECRET`, `TEAMS_TENANT_ID`, `TEAMS_BOT_USER_ID` are set in the VM's `.env`
4. **M365 CLI:** Can be uninstalled from the VM after deployment (no longer needed)

## Verdict: CONDITIONAL PASS

Condition: Admin consent for application permissions must be completed before deployment. The code is ready to deploy.
