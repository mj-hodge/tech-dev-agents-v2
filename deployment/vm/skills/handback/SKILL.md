---
name: handback
description: Hand work back to Mark when it requires admin access, credentials, manual config, or approval
version: 1.0.0
metadata:
  hermes:
    tags: [workflow, admin, handback]
    category: process
---

## When to Use

Any time you discover work that YOU cannot do because it requires:
- Global admin / Azure portal access
- DNS, VM provisioning, NSG rules
- Credentials, API keys, secrets
- External service configuration (Monday.com admin, M365 admin, GoDaddy)
- Manual approval or sign-off
- Access to systems you don't have (Azure CLI, production databases)

## Procedure

### 1. Create the TODO items

Add a `## Mark TODO` section to the **PR description** (if you have a PR) AND to the **seed.md** under `## Manual Steps (Mark)`.

Format each item as a checkbox with enough detail that Mark can act without asking follow-up questions:

```markdown
## Mark TODO

Items requiring Mark's manual action (admin access, credentials, external config):

### Azure / Infrastructure
- [ ] Create DNS A record: `tech-dev-agents.gorillacommerce.ai → 137.116.63.176` in Azure DNS zone `rg-dns-gorillacommerce`
- [ ] Open port 8005 on NSG `vm-ops-console-devNSG`: `az vm open-port --resource-group rg-tech-dev-agents-dev --name vm-ops-console-dev --port 8005`

### Credentials / Secrets
- [ ] Add `OPS_API_KEY` to `/opt/ops-console/.env` on vm-ops-console-dev (value: generate a new 64-char hex key)
- [ ] Rotate SP-API refresh token for account 12345 and update `.env`

### External Services
- [ ] Approve Monday.com webhook integration in admin panel at https://gorillacommerce.monday.com/admin/integrations
- [ ] Enable Graph API permission `Chat.ReadWrite` for app registration dc0cba0b

### Approvals
- [ ] Review and approve PR #14 — STORY-082 UAT environment changes
- [ ] Confirm the new budget threshold ($500/day) is acceptable
```

### 2. Rules for writing TODO items

- **Be specific.** Don't say "configure DNS" — say "create A record X → Y in zone Z"
- **Include the command** when possible — `az vm open-port --resource-group ...`
- **Include URLs** for admin panels — Mark shouldn't have to search for them
- **Group by type** — Azure/infra, credentials, external services, approvals
- **One action per checkbox** — don't combine "create VM and configure DNS" into one item

### 3. Create the PR (REQUIRED — even if story is incomplete)

If no PR exists yet for this branch, create one NOW. The PR is where Mark reviews your work — local commits are invisible to him.

```bash
gh pr create --title "STORY-XXX: <title>" --body "$(cat <<'EOF'
## Summary
<2-3 sentences: what was built/changed and why>

## Decisions Made
<any architectural or implementation choices and why — Mark needs this context for review>

## Unexpected Issues
<anything that didn't go as planned, workarounds used>

## Test Results
- <count> tests passing
- <any skipped or known failures>

## Mark TODO
<paste the Mark TODO checklist from step 1 here>

## What's Next
<what should happen after Mark completes the TODO items>
EOF
)"
```

**The PR body MUST contain the same detail you'd put in the Teams completion message.** This is the permanent record — Teams messages scroll away, PRs don't.

### 4. Message Mark

After creating the PR, message Mark in Teams with the PR link:

**If it blocks your story:**
> Blocked on STORY-XXX: [N] manual steps need your action.
> PR: https://github.com/hpi-gorillacommerce/<repo>/pull/<N>
> Top blocker: [most critical item in 1 sentence]

**If it doesn't block (nice-to-have):**
> STORY-XXX note: [N] items for your TODO list (non-blocking).
> PR: https://github.com/hpi-gorillacommerce/<repo>/pull/<N>

### 5. Continue or wait

- **Blocking items:** Stop working on the blocked part. If other parts of the story can proceed without the manual step, continue those. Message Mark and wait.
- **Non-blocking items:** Note them and keep working. Mark will handle them when he runs `/whats-next`.

## Pitfalls

- Don't attempt admin work yourself — even if you think you can figure it out
- Don't create vague TODOs ("set up the thing") — Mark's time is limited, be precise
- Don't put TODOs only in chat messages — they get lost. Always put them in the PR or seed where `/whats-next` can find them
- Don't combine blocking and non-blocking items without labeling which is which

## Verification

After using this skill:
- [ ] `## Mark TODO` section exists in PR description or seed.md
- [ ] Each item is a checkbox with specific action + command/URL
- [ ] Items grouped by type
- [ ] Mark messaged in Teams with blocker status
- [ ] Blocking vs non-blocking is clear
