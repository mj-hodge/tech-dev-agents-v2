# Seed

## Overview
| Field | Value |
|-------|-------|
| Mode | feature_update |
| Scope | large |
| Criticality | important |
| Feature Name | claude-sdk-tool-agent-sdk-migration |
| Frontend | false |

## Problem Statement

The agent fleet's foundational invocation layer — `deployment/vm/claude_sdk_tool.py` — wraps the `claude -p` CLI as a subprocess per phase. Two structural problems with this:

1. **Cost.** `claude -p` always bills to API regardless of subscription state (confirmed 2026-05-14 via remote test and via [GitHub issue #37686](https://github.com/anthropics/claude-code/issues/37686) — a Max subscriber burned $1,818 in 2 days from cron'd `claude -p`). Even with Foundry passthrough, every call pays Sonnet/Opus rates for jobs Haiku can do. STORY-802 (merged 2026-05-12) made a partial pass at tiering but per-call model selection is still ad-hoc — not driven by config — and prompt caching is not used aggressively.
2. **Coming policy change (2026-06-15).** Anthropic announced a separate monthly Agent SDK credit for paid plans (Pro $20, Max 5x $100, Max 20x $200, etc.) covering `claude -p` and Agent SDK at standard API rates. Subscriptions no longer absorb programmatic usage in interactive limits. Whether we end up subscription-bundled or direct-API, **the SDK is the canonical surface** — we should not be tied to a CLI subprocess that may itself become deprecated or rate-limited at the OS layer.

The fleet's actual code-writing work (Phases 7 and 8) needs to keep Sonnet quality. The classifier, rebase detector, queue triage, KB summarization, stall-reason picker, outcome classifier, and similar pattern-matching jobs are paying Sonnet rates for Haiku-class work. Today's `claude_sdk_tool.py` has no clean way to make that distinction.

Today's 5-leak queue-stability fixes shipped earlier today and the queue is materially healthier (50 → 4 active rows, 38 stale rows cancelled, no new zombies). With queue health stable, this story is the next-most-leveraged single change: it cuts cost without changing what the agents do, and it gets us onto the canonical SDK before any further CLI policy changes hit.

## Target User / Use Case

**Fleet operators (Mark) and the agent fleet itself (Dan/Derrick/Daisy/Devon/Morris):** every phase invocation across the fleet routes through the new SDK-based tool. Operators get predictable per-call cost, configurable model selection without code changes, and observability into cache hit rates and per-task spend. The agents themselves get faster startup (no subprocess fork), structured error surfaces (typed exceptions instead of CLI exit codes), and equal-or-better quality (Sonnet stays on code phases, Haiku absorbs the orchestration overhead).

## Success Criteria

### A — Core SDK migration

- [ ] New module `deployment/vm/claude_agent_sdk_tool.py` replaces `claude_sdk_tool.py` as the primary invocation surface. Old file retained for one release cycle as `claude_sdk_tool_legacy.py` for fallback during rollout.
- [ ] Uses `anthropic` Python SDK (`anthropic>=0.40` or current at story start) for direct API calls. No subprocess; no `subprocess.run(["claude", "-p", ...])`.
- [ ] Implements the agentic loop in Python: prompt → response → tool use → tool result → response → ... up to `max_turns`. Mirrors current `claude -p --max-turns N` semantics.
- [ ] Supports the same tools the CLI exposes today: `Read`, `Write`, `Edit`, `Bash` (with allowlist), `Grep`, `Glob`. Tool implementations live in a new `deployment/vm/agent_tools/` directory; one file per tool; ≤200 lines each.
- [ ] Tool execution honors the existing terminal-guard (`deployment/vm/terminal_guard.py`) and bwrap sandbox profile. Bash commands route through the same whitelist as today.
- [ ] System prompt and tool definitions are sent with `cache_control: {"type": "ephemeral"}` so subsequent calls within the 5-min window get cache reads (~10% of input cost).
- [ ] Per-phase model selection driven by `deployment/vm/canonical-state.yaml`. New config block:
  ```yaml
  models:
    phase_1_seed: claude-opus-4-7
    phase_7_test_design: claude-sonnet-4-6
    phase_8_implementation: claude-sonnet-4-6
    phase_8b_code_review: claude-sonnet-4-6
    phase_6_design_large: claude-opus-4-7
    classifier: claude-haiku-4-5
    rebase_detector: claude-haiku-4-5
    queue_triage: claude-haiku-4-5
    kb_summarizer: claude-haiku-4-5
    outcome_classifier: claude-haiku-4-5
    fallback: claude-sonnet-4-6
  ```
- [ ] Auth: tool reads `ANTHROPIC_API_KEY` from `/opt/agent/.env`. After 2026-06-15, if a `CLAUDE_SUBSCRIPTION_TOKEN` is present, the tool may bundle calls under the Agent SDK credit (deferred — separate story if needed).

### B — Prompt caching aggressively applied

- [ ] System prompt cached (one shared cache breakpoint per agent identity).
- [ ] Tool definitions cached (one breakpoint).
- [ ] Per-phase persona system prompt cached.
- [ ] Morris's review-context bundle (~50K tokens) cached when invoked through this tool (Path B's Routine migration handles Morris separately, but if any worker invokes a review path, caching applies).
- [ ] Cache hit rate exposed as a metric (see C).
- [ ] Unit test asserts cache_control is present on system + tools for every call.

### C — Confidence-based escalation for tiered calls

- [ ] Haiku-tier calls (classifier, rebase_detector, queue_triage, etc.) emit structured output via `tool_use` (e.g., a `report_classification` tool that the LLM must call with `failure_class` + `confidence` fields).
- [ ] If `confidence < 0.7`, the tool transparently retries the call on the `fallback` model (Sonnet). Logged as `tier_escalation`.
- [ ] Escalation rate is a metric (see D). > 25% escalation rate on a task means the Haiku assignment was wrong — alert in observability.

### D — Quality monitoring and cost observability

- [ ] New metric: `claude_agent_sdk_call_total{task, model, cache_hit, escalated}` — counter.
- [ ] New metric: `claude_agent_sdk_tokens_total{task, model, kind=input|output|cache_read|cache_write}` — counter.
- [ ] New metric: `claude_agent_sdk_cost_usd_total{task, model}` — sum of price-table-converted tokens.
- [ ] Per-story cost rollup written to `dispatch_jobs.cost_usd_total` (new column, migration 060).
- [ ] Dashboard panels (Grafana) for: cache hit rate by task, model distribution by phase, escalation rate by tiered task, daily cost by agent.
- [ ] Alert: cache hit rate below 50% on Phase 8 calls (cache being invalidated unexpectedly).
- [ ] Alert: escalation rate above 25% on any tiered task (tiering misconfigured).
- [ ] Pre-shipping baseline: capture 1 week of pre-migration cost-per-story so post-migration regression detection has a comparison.

### E — Tooling, deploy, and rollout

- [ ] `deployment/vm/push-code.sh` smoke test updated: new step verifies `python3 -c "from anthropic import Anthropic; Anthropic().models.list()"` succeeds (auth check) before declaring deploy success.
- [ ] Canary rollout: Dan first, Derrick second, Daisy third, Devon stays masked. Each canary runs 24h before next promote.
- [ ] Rollback: env var `USE_LEGACY_CLAUDE_SDK_TOOL=1` falls back to the retained legacy module without code change.
- [ ] Manifest (`deployment/vm/.deploy-manifest.json`) extends with new tool path + checksum.

### F — Documentation

- [ ] Update `.claude/skills/dispatch/SKILL.md` and `deployment/vm/MORRIS-CRITICAL-FUNCTIONS.md` to reference the new tool name. (Pure rename references — semantics unchanged from caller perspective.)
- [ ] New runbook `data-remediations/claude-sdk-rollback.md` documenting the `USE_LEGACY_CLAUDE_SDK_TOOL=1` lever.
- [ ] Brief design doc captured in `features/story-920-.../architecture.md` summarizing the agent loop, tool execution, caching strategy, and observability.

## Constraints
| Constraint | Value |
|------------|-------|
| Language | Python 3.12 |
| SDK | `anthropic` official Python SDK (not `anthropic-bedrock` unless explicit) |
| HTTP transport | SDK default; no httpx wrappers |
| Sandbox | Existing bwrap + terminal_guard. No new sandbox layer. |
| Auth | `ANTHROPIC_API_KEY` from `/opt/agent/.env` (current path) |
| Tool surface | Match current CLI tools exactly: Read, Write, Edit, Bash, Grep, Glob. Do not add new tools in this story. |
| Backwards compatibility | One-release transition with `USE_LEGACY_CLAUDE_SDK_TOOL=1` rollback lever |
| Test infra | Unit tests stub the Anthropic client; integration tests against a real key are gated by `RUN_INTEGRATION=1` env var |
| Mutation guard | Do NOT modify `dispatch_poller_v2.py`, `dispatch_v2_service.py`, or any sweeper. The tool is the only changed surface. |
| Coordination | Lands AFTER 2026-05-12's queue-stability fixes (already deployed). Lands BEFORE STORY-921 (Morris → Routines) which will reference the new metrics format. |
| Model defaults | Phase 7/8/8b stay on Sonnet. Phase 1/6 large/9/10 stay on Opus. Haiku ONLY on the tasks listed in the model config table above. |

## Security Constraints (Non-Negotiable)

- [ ] `ANTHROPIC_API_KEY` never logged, never echoed to stdout/stderr, never included in error messages or exception strings.
- [ ] Tool execution preserves the existing terminal_guard whitelist. Removing or weakening the guard is out of scope.
- [ ] Cache writes do not include user-tenant data (story prompts, agent identities) — only system prompts and tool definitions.
- [ ] Cost metrics do not include prompt content; only token counts and model identifiers.
- [ ] Rollback lever (`USE_LEGACY_CLAUDE_SDK_TOOL=1`) does not lower any safety guard.

## Test Criteria

### Unit (no live API)

- [ ] T01 — agent loop: prompt → response with `tool_use` → tool execution → response with `end_turn` exits cleanly with the final assistant text.
- [ ] T02 — agent loop: max_turns enforced (loop terminates after N turns even if model keeps requesting tools).
- [ ] T03 — system prompt is always sent with `cache_control: ephemeral`.
- [ ] T04 — tool definitions are sent with `cache_control: ephemeral`.
- [ ] T05 — per-phase model selection: passing `phase="phase_7_test_design"` selects the configured Sonnet model; `phase="classifier"` selects Haiku.
- [ ] T06 — confidence escalation: tool emits `confidence: 0.5` → second call invoked on fallback Sonnet; first call's response is discarded.
- [ ] T07 — confidence escalation: tool emits `confidence: 0.9` → no second call; response accepted.
- [ ] T08 — terminal_guard rejection: Bash tool input containing a non-whitelist command (e.g., `curl evil.com`) → tool returns error result, loop continues, no shell invoked.
- [ ] T09 — auth error: missing `ANTHROPIC_API_KEY` → exit code 78 (existing convention), no API call attempted, no key leak in error.
- [ ] T10 — metric emission: every call increments `claude_agent_sdk_call_total` with correct labels.

### Integration (`RUN_INTEGRATION=1`)

- [ ] T11 — round-trip with real API key against Haiku: classifier task with a known input returns the expected `failure_class`.
- [ ] T12 — cache observed: two back-to-back calls with the same system prompt show non-zero `cache_read_input_tokens` on the second.
- [ ] T13 — end-to-end phase: simulated Phase 7 invocation on a fixture seed produces a non-empty test-design output and emits the expected metrics.

### Regression

- [ ] T14 — legacy fallback: setting `USE_LEGACY_CLAUDE_SDK_TOOL=1` routes to `claude_sdk_tool_legacy.py` and a phase invocation succeeds with the old code path.
- [ ] T15 — current `dispatch_poller_v2.py` invokes the new tool without code changes (only the import name changes via a shim or the file is named the same as the legacy by the rollout commit).
- [ ] T16 — Morris's existing invocations (anything that shells to `claude -p` from review-prs) work against the legacy path until STORY-921 ships.

### Quality monitoring (post-deploy)

- [ ] T17 — 7 days post-deploy, average cache hit rate on Phase 8 calls is ≥50%.
- [ ] T18 — 7 days post-deploy, escalation rate on every tiered task is ≤25%.
- [ ] T19 — 7 days post-deploy, per-story cost regression is ≤120% of pre-migration baseline (allowing 20% headroom for measurement noise). Cost SHOULD drop, not rise — anything over 100% is a red flag.

## Validation

- Run a 24-hour canary on Dan. Confirm:
  - All claimed stories complete via the new tool.
  - Metrics emit correctly to Prometheus / wherever the cost-collector ships them today.
  - No SDK auth errors in journalctl.
  - Cost per story is ≤ Dan's pre-migration baseline (collected during T19 baseline week).
- Compare a representative Phase 8 output (test-design.md + code) between legacy and new tool on the same seed — quality should be indistinguishable.
- Confirm the `USE_LEGACY_CLAUDE_SDK_TOOL=1` rollback actually rolls back (set the env, restart poller, run one story).

## Codebase Context

| Aspect | Details |
|--------|---------|
| Affected files (primary) | `deployment/vm/claude_agent_sdk_tool.py` (new), `deployment/vm/agent_tools/*.py` (new), `deployment/vm/canonical-state.yaml` (add `models` block), `deployment/vm/push-code.sh` (auth check), `deployment/vm/.deploy-manifest.json` (extend), `scripts/migrations/060_dispatch_jobs_cost_usd.sql` (new column) |
| Affected files (rename/move) | `deployment/vm/claude_sdk_tool.py` → `claude_sdk_tool_legacy.py` |
| Affected callers | `deployment/hermes/dispatch_poller_v2.py`, `deployment/hermes/dispatch_poller.py`, the sdlc_phase_runner inside the .sdlc submodule, Morris's review-prs (via the legacy path until STORY-921) |
| Reference patterns | Anthropic Python SDK quickstart, `anthropic.Anthropic().messages.create(...)` with `tools=[...]` and the standard tool-use loop |
| Reference for caching | Anthropic docs on prompt caching — `cache_control` parameter, ephemeral vs persistent breakpoints, 5-minute TTL |
| Reference for tool implementation | Existing `terminal_guard.py` for Bash routing, `patch_anthropic_adapter.py` for the current SDK-side hooks |
| Pairs with | STORY-921 (Morris → Routines, lands after this) — Morris's worker may keep using the legacy path for one cycle if needed, but the new tool's metrics are what STORY-921's dashboards consume |
| References STORY-802 | The Haiku/Sonnet tiering it landed is ad-hoc per call site; this story makes that config-driven and adds caching + observability |
| Pre-existing memory notes | `never_change_sdk_invocation` — this story is the SDK invocation change, so the memory note's "test on VM first" requirement applies: canary on Dan for 24h before any other agent. |
| Out of scope | Subscription-credit-bundled auth (defer until June 15 lands and we see actual Agent SDK credit behavior); changing the dispatch protocol; adding new tools beyond the existing Read/Write/Edit/Bash/Grep/Glob set; migrating Morris (that's STORY-921). |
