# STORY-802 — Drive Foundry spend to ≤ \$20/day by completing the Haiku/Sonnet migration

> **Driver:** Mark, 2026-05-01. Foundry today (so far): **\$19.99 — 100% Opus**. Last 7 days: **\$702.12, of which Opus is \$701.67 (99.94%)**, Sonnet \$0.08, Haiku \$0.37. The Sonnet/Haiku migration described in `docs/azure-foundry-billing.md` "Optimization History" never actually stuck — Opus is still doing essentially all of the work. Cost ceiling for the fleet is **\$20/day total**.

## Scope

Medium. Cross-VM config + verification work, no new app code. Touches all 5 hermes VMs (morris, dan, derrick, devon, daisy) plus the dispatch poller / phase runner SDK invocation.

**Frontend:** false

## Phase Path

`1 → 4 → 6 → 7 → 8 → Done` (skip Phase 2/3/5 — research and selection are already in `docs/azure-foundry-billing.md`).

## Background — what was supposed to happen and didn't

`docs/azure-foundry-billing.md` "Optimization History" (lines 565–579) records these changes as applied 2026-04-23:

| Change | Recorded "after" state |
|---|---|
| All 16 active hermes cron jobs → Sonnet | Sonnet |
| `auxiliary.compression.model` → Haiku | Haiku |
| Morris's main `hermes config set model` → Sonnet | Sonnet |
| Compression `threshold` 0.5 → 0.85 | 0.85 |
| Pause "Morris Fleet Health (30min)" cron | paused |
| State Sync + Heartbeat `*/15` → `*/30` | every 30 min |

Combined expected effect was **\$1,200/day → \$50–100/day**. Actual landing is closer: 7-day daily mean is ~\$100/day. But the daily shape is *all Opus*. So either (a) those config changes never propagated to the running services, (b) they got reverted by a subsequent service restart that reloaded a stale config, or (c) the SDK invocation path (Claude Code SDK used by `dispatch_poller.py` / `sdlc_phase_runner.py`) ignores the hermes model config entirely and is hitting Opus directly.

Hypothesis (c) is likely the root cause — agents run via `claude_sdk_tool.py` (per memory: never change SDK invocation), and that tool may pass `--model claude-opus-4-7` regardless of the per-agent or per-job hermes config.

## Acceptance criteria

- **AC-1.** Audit each VM's actual running model selection. Confirm what runs through hermes (cron + Teams) vs. what runs through the SDK tool (dispatch_poller phase work). Document in `audit.md`.
- **AC-2.** For hermes side: verify `/home/hermes/.hermes/config.yaml` `model:` and `auxiliary.compression.model:` settings on all 5 VMs. If any VM is on Opus, switch to Sonnet (main) / Haiku (compression) per the doc. Verify by running a small probe and reading state.db `model` column.
- **AC-3.** For the SDK side: identify the model used by `dispatch_poller.py` / `claude_sdk_tool.py` for each phase. Phases 1, 9, 10 must stay on Opus (per `CLAUDE.md` model policy). Phases 2–8 default Sonnet. Implement a per-phase model selector that reads the policy table from `CLAUDE.md`, with a config override.
- **AC-4.** Compression cascade neutralization: confirm `compression.threshold ≥ 0.85` on every VM. If lower, raise it. Re-verify by triggering a >50%-context cron job and checking that fewer than 3 compression sub-sessions follow it (vs. 5–10 baseline from the doc).
- **AC-5.** Per-job audit: walk `/home/hermes/.hermes/cron/jobs.json` on every VM. Any job whose `model: null` (inheriting default) AND whose actual runs are on Opus needs a per-job override to Sonnet (or Haiku for low-stakes summaries).
- **AC-6.** Cost gate: 7 days after deploy, the daily Foundry spend must be ≤ \$20/day. The PR must include a follow-up task that verifies this on 2026-05-08 and dispatches a fix story if the gate is missed.
- **AC-7.** Add a daily cron alert (in addition to the per-30-min monitor in the doc) that pages Mark if today's Foundry spend exceeds \$20 by 18:00 UTC.

## Hard constraints

- **Must NOT change SDK invocation in a way that breaks the `claude_sdk_tool.py` contract.** Per fleet memory, switching to `claude -p` directly broke the entire fleet on 2026-04-20. Any model-selection change goes through the existing tool's parameter, not by replacing it.
- **Phase 1, 9, 10 MUST remain Opus** (per `CLAUDE.md` Model Policy). This story's optimization is for Phases 2–8 + hermes cron + compression.
- **Test on one VM first** (suggest: derrick, since it's the noisiest at \$277 today) before fleet rollout.
- **One change at a time, with a 24h soak.** A simultaneous flip of all 5 VMs + SDK + compression is too risky — if cost spikes, we want to know which lever did it.

## Done = 

- All 7 ACs satisfied
- 7-day daily Foundry mean ≤ \$20 verified post-deploy
- `audit.md` + `verification.md` committed under `features/story-802-haiku-migration-cost-cut/`
- Daily \$20 alarm wired up and tested with a synthetic cost spike

## Test Criteria

- Unit tests pass for model-selection logic in `deployment/hermes/sdlc_phase_runner.py` (Phase 6 scope-aware behavior and Opus/Sonnet routing).
- Unit tests pass for dispatch retry policy in `deployment/hermes/dispatch_poller.py`, including `phase8_no_commits` classification and single-retry cap.
- Integration check confirms resumed needs_info claims propagate `answer_text` into phase prompts without relying on local `ANSWER.md`.
- Manual dry-run on one VM (derrick first) verifies no regression in SDK invocation contract (`claude_sdk_tool.py` remains the execution path).

## Validation

- Run targeted tests:
  - `pytest tests/test_sdlc_framework_compliance.py -q`
  - `pytest tests/deployment -q`
- Run local static checks for touched files:
  - `python -m py_compile deployment/hermes/sdlc_phase_runner.py deployment/hermes/dispatch_poller.py`
- Post-deploy verification:
  - Confirm `dispatch-poller` logs show Phase 6 using Opus only for medium/large scopes.
  - Confirm a synthetic no-commit Phase 8 path emits `phase8_no_commits` and retries once, then fails (not needs_info).

## Reference docs

- `docs/azure-foundry-billing.md` — particularly "Optimization History" (565–579) and "How to neutralize the cascade" (449–465)
- `state/morris/incident-2026-04-30-foundry-auth.md` — context on the auth rotation (separate issue, but Morris VM credentials may need rotation before this work lands)

— Mark, 2026-05-01
