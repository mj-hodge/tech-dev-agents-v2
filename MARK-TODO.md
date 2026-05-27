# Mark TODO — Admin Actions Required

Things that can't be done by an agent (admin-only / UI-only / credential-only).
Keep this file short and act on items as they come up.

---

## CI/CD Pipeline — one-time enablement (STORY-495/529, 2026-04-22)

Needed to make the dashboard auto-rebuild + redeploy when PR #81 (and future
frontend PRs) merge. Without these, every dashboard change requires a manual
`deploy-remote.sh` invocation against vm-ops-console-dev.

**Sequence (do in order):**

1. **Wait for STORY-529 to complete** — agent is rebasing PR #77 onto main
   and pushing force-with-lease. Watch for `PR created` / mergeable=MERGEABLE.
   Or: `gh pr view 77 --repo hpi-gorillacommerce/tech-dev-agents --json mergeable`
   — should flip from `CONFLICTING` to `MERGEABLE`.

2. **Merge PR #77.** This lands:
   - `.github/workflows/deploy-ops-console.yml` (the pipeline)
   - `deployment/ops-console/deploy.sh` + `rollback.sh`
   - healthz wiring

3. **Set the repo variable `OPS_CONSOLE_CICD_ENABLED=true`.** Admin only.
   - Go to https://github.com/hpi-gorillacommerce/tech-dev-agents/settings/variables/actions
   - Add a new Repository variable: `OPS_CONSOLE_CICD_ENABLED` = `true`
   - (Feature-flagged by design — the workflow exits silently if unset.)

4. **Merge PR #81 (STORY-515 dashboard UI).** The pipeline fires, the image
   rebuilds with the new `DispatchQueue.tsx` + `AgentCard.tsx` + `statusBadge`
   cases baked in, and `deploy.sh` ships it through the Morris → ops-console
   hop documented in `deployment/ops-console/deploy-remote.sh`.

5. **Verify on the dashboard.** Load https://tech-dev-agents.gorillacommerce.ai/
   — queue items in `in_review` and `paused` should now be visible rows
   with the new badges.

**If step 3 isn't done, steps 4–5 silently do nothing** (no dashboard update).
The workflow's first job logs `CI/CD pipeline is disabled` and exits zero so
CI stays green.

---
