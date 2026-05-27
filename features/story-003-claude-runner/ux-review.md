# UX Review: Claude Code Runner (STORY-003)

> Phase 6c — UX Review
> Date: 2026-03-26
> Reviewer: Phase 6c UX Analyst

---

## 1. Error Message Clarity by Error Code

| Code | Current Message | Problem | Recommended Message |
|------|----------------|---------|---------------------|
| `TIMEOUT` | "Process timed out after 120000ms" | Raw ms is not human-readable | "Claude CLI timed out after 120s (limit: 2m). Increase timeoutMs or reduce maxTurns." |
| `AUTH` | "Authentication failed" | No actionable guidance | "Authentication failed. Verify ANTHROPIC_API_KEY is set and valid." |
| `RATE_LIMIT` | "Rate limited by Anthropic API" | No retry hint | "Rate limited by Anthropic API. Wait before retrying. Check stderr for retry-after details." |
| `PARSE_ERROR` | "stdout is not valid JSON: <excerpt>" | Excerpt may be cryptic noise | Include `numTurns` context if available; suggest checking CLI version. |
| `MAX_TURNS` | "Reached 10 turns (limit: 10)" | Caller may not know how to respond | "Reached turn limit (10/10). Partial result is available in err.partialResult. Increase maxTurns or simplify the prompt." |
| `CLI_ERROR` | "CLI exited with code 1" | Exit code alone is not actionable | "Claude CLI exited with code 1. See err.stderr for details. Check that the 'claude' binary is on PATH." |

**Action:** Update `classifyExecError` and `parseOutput` messages to match the recommended text above.

---

## 2. Cost Visibility

**Finding:** `totalCost` is returned as a raw float (e.g., `0.0187`). Callers displaying this to users must format it themselves; there is no utility or convention provided.

**Action:** Add a `formatCost(cost: CostInfo): string` utility export that returns `"$0.019 (1240 in / 380 out tokens)"`. This is optional for callers but prevents each caller from reimplementing formatting.

**Finding:** When cost fields are missing and silently zeroed (§3.3), callers have no reliable signal that cost data is unavailable vs. genuinely zero. The spec's suggested check (`inputTokens === 0 && outputTokens === 0`) is easy to miss.

**Action:** Add a `costAvailable` boolean field to `CostInfo` (or a `CostInfo.isZero()` helper) so callers can distinguish "we have no data" from "zero cost" without a multi-field check.

---

## 3. Timeout Behavior from Caller Perspective

**Finding:** When a timeout fires, the caller receives `TIMEOUT` with elapsed time in the message but has no way to know how many turns were completed before the kill. `partialResult` is only attached to `MAX_TURNS`, not `TIMEOUT`.

**Action:** Capture and attach any stdout buffered before the kill to `RunnerError` on `TIMEOUT` (as a `rawOutput?: string` field). This gives callers visibility into partial progress.

**Finding:** The `timeoutMs` clamping warning is emitted to `console.warn` but not surfaced to the caller. If a caller sets `timeoutMs: 1_800_000` expecting 30 minutes, they silently get 10 minutes.

**Action:** Either throw a validation error when `timeoutMs > MAX_TIMEOUT_MS`, or include the effective timeout in `RunResult` so callers can detect the clamp after the fact.

---

## 4. Debugging Failed Runs

**Finding:** On `PARSE_ERROR`, the error message includes only the first 200 characters of stdout. For failures caused by mid-stream corruption (large responses near the 10 MB buffer limit), 200 chars is insufficient to diagnose the issue.

**Action:** Include `stdout.length` and `MAX_BUFFER` in the `PARSE_ERROR` message so the caller can immediately see if a buffer overflow was the cause: "stdout is not valid JSON (length: 10485760, buffer cap: 10485760). Buffer may be full."

**Finding:** No structured logging is emitted for successful runs. Callers integrating with STORY-006 or STORY-002 cannot trace which invocations ran, with what options, or what the cost was without wrapping every call.

**Action:** Emit a single structured debug log line on success (guarded by a `DEBUG` env var or a logger injection option): `[ClaudeRunner] run complete — turns: 3, cost: $0.019, sessionId: abc-123, cwd: /repo`.
