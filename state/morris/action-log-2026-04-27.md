# Action Log — 2026-04-27 (PR Review Session ~22:00-22:30Z)

## PRs Reviewed This Session (5 total)

### PR #216 — tech-dev-agents — STORY-740: Dispatch State-Machine Contract Consolidation
- **Verdict:** REQUEST_CHANGES
- **Review posted:** https://github.com/hpi-gorillacommerce/tech-dev-agents/pull/216#issuecomment-4330788454
- **Key findings:**
  - H-1 (BLOCKING): Migration 008 edited in-place — `CREATE UNIQUE INDEX IF NOT EXISTS` is a no-op on deployed DBs; need migration 009
  - CI failure: seed.md missing `## Test Criteria`, `## Validation`, `Frontend: false`
  - L-1: TRANSITIONS matrix not contract-tested
  - L-3: AC-10 unsatisfied (PR body missing checklist)
- **Size:** Medium (+643/-7, 9 files)
- **Rework dispatch:** ❌ OPS_CONSOLE_API_KEY unavailable — escalate to Mark

### PR #267 — advertising-amazon — EPIC-008 Seed: SP-API Notifications Archive
- **Verdict:** APPROVE (Phase 1 seed, do not merge until pre-merge checklist)
- **Review posted:** https://github.com/hpi-gorillacommerce/advertising-amazon/pull/267#issuecomment-4330788549
- **Author:** markoreta-gc (human)
- **Key findings:** Excellent seed quality; tracking docs (.project, backlog.md, development-tasks.md) not updated for EPIC-008 — required before merge. Low-severity recommendations on story sizing and OQ prioritization.
- **Size:** Medium (+669/-0, 1 file)
- **Note:** Branch protection on advertising-amazon — Mark must merge after pre-merge checklist resolved

### PR #268 — advertising-amazon — feat(STORY-094): cache-first SP keyword bids + batch sync
- **Verdict:** REQUEST_CHANGES
- **Review posted:** https://github.com/hpi-gorillacommerce/advertising-amazon/pull/268#issuecomment-4330804854
- **Key findings:**
  - CRIT-1 (BLOCKING): Bundled PR — STORY-094 (keyword cache) + STORY-217 (write guard hardening) mixed on one branch; must split into two separate PRs
  - CRIT-2: Branch is CONFLICTING (merge conflicts)
  - CRIT-3: No CI checks have run (0 status checks)
  - H-1: Duplicate `sp_list_keywords` entry in `test_all_get_tools_cached.py`
  - STORY-217 implementation architecturally sound (ContextVar scoped bypass, hard cap floor)
- **Size:** Large (+1826/-84, 16 files)
- **Rework dispatch:** ❌ OPS_CONSOLE_API_KEY unavailable — escalate to Mark

### PR #48 — tech-gc-knowledgebase — STORY-585: Multi-Agent Orchestration Patterns Research
- **Verdict:** Prior APPROVE stands (2026-04-26) — only blocker is merge conflicts
- **Status comment posted:** https://github.com/hpi-gorillacommerce/tech-gc-knowledgebase/pull/48#issuecomment-4330804903
- **Action needed:** Rebase `story-585-agentic-multi-agent-orchestration` against main; content is production-ready

### PR #19 — tech-project-mapping — feat(story-505): Cross-Project Monitoring & Teams Alerting
- **Verdict:** REQUEST_CHANGES (4th nudge, same 3 blockers, no new commits)
- **Status comment posted:** https://github.com/hpi-gorillacommerce/tech-project-mapping/pull/19#issuecomment-4330804958
- **Blockers (outstanding since 2026-04-24):**
  1. `monitoring-deploy.yml` references missing `infra/parameters/monitoring.json`
  2. `--delete-orphans` silent no-op CLI flag
  3. Branch CONFLICTING
- **Rework dispatch:** ❌ OPS_CONSOLE_API_KEY unavailable — escalate to Mark

## Key Blockers for Mark

1. **OPS_CONSOLE_API_KEY unavailable** — can't dispatch rework stories for PR #216, #268, #19 from this session. Mark should either:
   - Run dispatch for STORY-740-rework, STORY-094-rework, STORY-217-rework, STORY-505-rework
   - OR export the key to the hermes environment
2. **advertising-amazon branch protection** — PR #267 ready to merge once tracking docs updated; PR #268 needs split+rebase first
3. **PR #19 (STORY-505) aging** — 4 days old with no agent response; may need manual re-queue
4. **Codex adversarial reviews** — `codex` CLI requires terminal approval in this session; adversarial review for PR #216 running via Claude Code as fallback


## Adversarial Security Reviews (Dual-Review Gate)

### PR #216 — Adversarial Pass
- **Posted:** https://github.com/hpi-gorillacommerce/tech-dev-agents/pull/216#issuecomment-4330903086
- **New blockers found (not in first-pass review):**
  - H-1: SQL contract test regex matches commented-out DDL (no comment stripping) — can mask real constraints
  - H-2: Constraint regex `\w*status\w*` not table-scoped — future migrations on other tables will corrupt the test
  - M-2: `TRANSITIONS` matrix already wrong at day 1 — `force_claim()` and `resume_after_answer()` do transitions not listed in TRANSITIONS
- **Block-merge list expanded:** 5 items (original H-1 + 3 new blockers + CI)

### PR #267 — Adversarial Pass
- **Posted:** https://github.com/hpi-gorillacommerce/advertising-amazon/pull/267#issuecomment-4330919779
- **Verdict updated:** APPROVE → REQUEST_CHANGES (verdict update: https://github.com/hpi-gorillacommerce/advertising-amazon/pull/267#issuecomment-4330936143)
- **5 HIGH findings:**
  - H-1: ORDER_CHANGE/TRANSACTION_UPDATE contain PII; "never delete" lifecycle violates GDPR/ToS
  - H-2: KMS policy verbatim from Amazon docs uses `Resource: *` — grants SP-API principal decrypt on ALL CMKs in account
  - H-3: New AWS account with no stated SCPs/CloudTrail/GuardDuty
  - H-4: SQS `sqs:SendMessage` granted to full SP-API root account with no `aws:SourceArn` condition (injection risk)
  - H-5: MCP write adapter doesn't cover `sqs.delete_message`/`s3.PutObject`; test-mode behavior unspecified

### PRs Not Adversarially Reviewed
- **PR #268:** Adversarial review would be redundant given CRIT bundle/conflict blockers; run after split into #094 and #217 branches
- **PR #48, #19:** No new code changes; adversarial review skipped (prior reviews still current)

## PRs Auto-Merged This Session

None — all PRs have blockers preventing merge.
