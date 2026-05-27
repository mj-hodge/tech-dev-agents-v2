# Phase 10 Site Reliability Report — STORY-003 Claude Code Runner

Date: 2026-03-31
Reviewer: Phase 10 SRE Agent

## Summary

Operational readiness review for the Claude Code Runner module. This is a library module consumed by STORY-006 (SDLC Engine) and STORY-008 (Git Workflow) — it does not deploy independently. Operational concerns are evaluated in the context of how callers will use it.

## Operational Characteristics

| Dimension | Value | Notes |
|---|---|---|
| Module type | Library (imported, not deployed) | No separate process, no network listener |
| External dependency | `claude` CLI binary on PATH | Installed by STORY-001 container |
| API dependency | Anthropic API (via CLI) | Rate limits, auth, availability |
| State | Stateless | No persistence, no caching, no connection pools |
| Concurrency | Caller-managed | Runner does not limit concurrent invocations |

## Failure Modes and Recovery

| Failure | Detection | Recovery | Owner |
|---|---|---|---|
| Claude CLI not on PATH | `subprocess.run` raises FileNotFoundError | Caller catches, reports to user | STORY-006 |
| Anthropic API auth failure | CLI exits non-zero, stderr contains "401" | RunnerError(CLI_ERROR) with stderr | Caller retries with fresh key |
| Anthropic API rate limit | CLI exits non-zero, stderr contains "429" | RunnerError(CLI_ERROR) with stderr | Caller implements backoff |
| CLI timeout | subprocess.TimeoutExpired | RunnerError(TIMEOUT) | Caller retries or escalates |
| Malformed CLI output | JSON parse failure | RunnerError(PARSE_ERROR) | Caller logs, retries |
| Max turns exhausted | num_turns >= max_turns | RunnerError(MAX_TURNS) with partial_result | Caller inspects partial, decides |

## Resource Consumption

| Resource | Per-Invocation | Notes |
|---|---|---|
| Memory | Proportional to CLI stdout size | Subprocess output buffered in memory; typically < 1 MB |
| CPU | Minimal (subprocess.run blocks, no polling) | CLI process does the work |
| Network | None (CLI handles API calls) | Runner itself makes no network calls |
| Disk | None | No temp files, no logging |
| API cost | $0.01-$0.20 per invocation (typical) | Tracked in RunResult.cost; budget enforcement is caller's job |

## Monitoring Recommendations (for STORY-006 integration)

1. **Cost tracking**: Aggregate `RunResult.cost.total_cost` per story, per phase, per day. Alert if daily cost exceeds threshold.
2. **Timeout rate**: Track ratio of TIMEOUT errors to total invocations. Spike indicates CLI performance degradation or prompt complexity increase.
3. **Turn utilization**: Track `num_turns / max_turns` ratio. Consistently hitting limits suggests maxTurns is too low or prompts need refinement.
4. **Error code distribution**: Track RunnerError codes over time. Spike in CLI_ERROR may indicate CLI version issue.

## Operational Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| CLI version drift breaks JSON output | Medium | High | Version pin in container; parse guard in parse_output |
| Orphaned subprocess on caller crash | Low | Medium | Subprocess inherits parent process group; OS cleanup on container restart |
| Cost runaway from uncontrolled invocations | Medium | High | max_turns cap, timeout cap; caller-level budget enforcement (STORY-006) |
| Rate limiting during heavy SDLC phases | Medium | Low | Caller implements exponential backoff; runner surfaces the error cleanly |

## Verdict

**APPROVED** — No operational blockers. The module is stateless, has clear failure modes with typed errors, and delegates all operational concerns (monitoring, cost budgets, retry logic) to its callers. This is appropriate for a library module.
