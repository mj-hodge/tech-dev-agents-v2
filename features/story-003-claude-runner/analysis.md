# Analysis: Claude Code Runner (STORY-003)

> Phase 4 — Analysis
> Date: 2026-03-26
> Scope: Medium
> Analyst: Phase 4 Analysis Agent

---

## 1. Invocation Strategy Comparison

Three strategies exist for invoking Claude from Node.js: the CLI (`claude -p`), the Agent SDK (`@anthropic-ai/agent-sdk` for Python/JS), and the direct Anthropic Messages API. The seed has already pre-selected the CLI with `execFile`. This analysis validates that choice and identifies the edge cases that the design must handle.

### 1.1 Comparison Matrix

| Dimension | Claude Code CLI (`claude -p`) | Agent SDK (JS/Python) | Direct Anthropic API |
|-----------|-------------------------------|-----------------------|----------------------|
| **Zero-dependency** | Yes — already in container (STORY-001) | No — adds npm dependency, ~10MB | No — adds npm dependency + all tool-use logic |
| **Headless operation** | Native (`-p` flag) | Native | Native |
| **Session management** | `--resume <session-id>` flag; session ID in JSON output | SDK manages session object internally | Must implement manually: store `messages[]` array |
| **Tool permission control** | `--allowedTools` flag per invocation | Config at SDK client level; per-call override supported | Must implement manually: define tool schemas, filter in prompt |
| **Output parsing** | Single JSON blob via `--output-format json`; no streaming in v1 | Structured SDK response objects; streaming supported | Structured response; streaming supported |
| **Cost tracking** | `result.cost.input_tokens`, `result.cost.output_tokens`, `result.cost.total_cost` in JSON output | Available in SDK response metadata | Available in API response `usage` field |
| **Model selection** | CLI default (set by `claude` binary version); `--model` flag available | Explicit per-call | Explicit per-call |
| **`--bare` flag** | Supported — skips hooks/MCP/CLAUDE.md (~800ms saving) | N/A — SDK is not hook-aware | N/A |
| **Version stability** | Output schema tied to CLI version; pin via `package.json` | SDK API is versioned; breaking changes tracked | API versioned via `anthropic-version` header |
| **Process isolation** | Each invocation is a child process — crash in Claude does not crash the host | SDK runs in-process — exception leaks to caller | In-process — exception leaks to caller |
| **Startup overhead** | ~800ms without `--bare`; ~200ms with `--bare` (per hermes-prompt.md §6a) | Near-zero (in-process) | Near-zero (in-process) |
| **Tool execution environment** | Claude Code handles file I/O tools natively with OS integration | SDK tools must be implemented by caller | Caller implements all tools |
| **Complexity cost** | Low — thin wrapper around argv | Medium — SDK abstraction, but adds dependency surface | High — must reimplement tool use, file I/O, session tracking |

### 1.2 Recommendation: CLI is the Right Choice

The CLI is the correct primary strategy for STORY-003. Rationale:

1. **Zero additional dependency.** The CLI is installed as part of STORY-001. Adding the Agent SDK introduces a `package.json` dependency that must be versioned, updated, and audited — for no functional gain in this context.
2. **Tool permission model is already built.** The `--allowedTools` flag gives per-invocation tool scoping at the shell level. The SDK and direct API require the caller to implement equivalent filtering in code.
3. **Process isolation is a feature, not a cost.** A misbehaving Claude Code invocation (infinite loop, OOM) cannot corrupt the host Node.js process. The child process exits; the runner surfaces a typed error.
4. **The `--bare` flag.** This flag, specific to the CLI, skips CLAUDE.md discovery, MCP registration, and git hooks on startup. The savings (~800ms per call) compound across long SDLC phases with many sub-invocations.
5. **Session resumption is already designed.** The `--resume <session-id>` flag with the session ID returned in JSON output covers all session continuation needs for v1.

**When to revisit:** If STORY-006 (SDLC Execution Engine) requires streaming output for real-time phase progress reporting, the Agent SDK becomes more attractive. This is explicitly deferred to v2 by the seed.

---

## 2. Process Management: `execFile` vs. `spawn`

### 2.1 `execFile` (selected in seed)

`execFile` buffers all stdout/stderr in memory and resolves/rejects when the process exits. The promisified form (`util.promisify(execFile)`) returns `{ stdout, stderr }`.

**Advantages for v1:**
- Output arrives as a single string — trivially passed to `JSON.parse()`.
- The `timeout` option in `execFile` options object triggers `ETIMEDOUT` on the child process after the specified milliseconds.
- No stream event wiring; simpler unit testing with mock.
- The seed explicitly requires `--output-format json` with no streaming, making buffered output the natural match.

**Risk: buffer overflow.** `execFile` has a default `maxBuffer` of 1 MB. For large SDLC phase outputs (e.g., a Phase 8 implementation producing many file writes), the JSON result blob could exceed this. Mitigation: set `maxBuffer` to 10 MB explicitly in runner options. The JSON output is the Claude result text, not the modified file content — so 10 MB is a conservative upper bound.

### 2.2 `spawn` (alternative, not selected)

`spawn` returns a `ChildProcess` with `stdout`/`stderr` as streams. Output must be accumulated manually. Required for streaming (SSE), but adds complexity for the v1 use case.

**Use `spawn` when:** v2 adds streaming output. The runner interface can be extended with an optional `onChunk` callback that triggers a `spawn`-based path while `execFile` handles the default JSON path.

### 2.3 Decision

Use `execFile` with explicit `maxBuffer: 10 * 1024 * 1024` (10 MB). The timeout option (`timeoutMs` in `RunOptions`) maps directly to the `timeout` option in `execFile`. This matches hermes-prompt.md §7 exactly and satisfies all v1 acceptance criteria.

---

## 3. JSON Output Schema

The CLI `--output-format json` response (as observed in hermes-prompt.md §7 and §11) has the following shape:

```json
{
  "result": "<assistant response text>",
  "session_id": "<uuid>",
  "num_turns": 3,
  "cost": {
    "input_tokens": 1240,
    "output_tokens": 380,
    "total_cost": 0.0187
  }
}
```

**Key observations:**

1. **Snake_case keys.** The CLI uses `session_id`, `num_turns`, `total_cost`. The `RunResult` TypeScript interface should use camelCase internally and map from snake_case on parse.
2. **`cost` may be absent or `null`.** If the CLI exits before completing a turn (e.g., auth failure), the cost field may be missing. The parser must handle `cost ?? { input_tokens: 0, output_tokens: 0, total_cost: 0 }`.
3. **`session_id` may be absent on first turn.** Some CLI versions only return `session_id` after a successful multi-turn exchange. The parser must handle `session_id ?? null` and callers must handle a null session ID.
4. **`result` is the final assistant text only.** Tool call traces and intermediate turns are not included in the JSON output. If debugging is needed, `--verbose` mode (not used in production) writes tool traces to stderr.
5. **Schema version instability.** The JSON schema is not formally versioned by Anthropic. The CLI npm package version (`@anthropic-ai/claude-code`) must be pinned in `package.json`. A `parseOutput()` function should validate the top-level keys and throw `RunnerError` with `code: 'PARSE_ERROR'` if the schema has changed unexpectedly.

**Parse guard implementation:**

```typescript
function parseOutput(stdout: string): RunResult {
  let raw: unknown;
  try {
    raw = JSON.parse(stdout);
  } catch {
    throw new RunnerError('PARSE_ERROR', 'stdout is not valid JSON');
  }
  if (typeof raw !== 'object' || raw === null || typeof (raw as any).result !== 'string') {
    throw new RunnerError('PARSE_ERROR', `Unexpected output shape: ${stdout.slice(0, 200)}`);
  }
  const r = raw as Record<string, unknown>;
  return {
    result: r.result as string,
    sessionId: typeof r.session_id === 'string' ? r.session_id : null,
    numTurns: typeof r.num_turns === 'number' ? r.num_turns : 0,
    cost: {
      inputTokens: (r.cost as any)?.input_tokens ?? 0,
      outputTokens: (r.cost as any)?.output_tokens ?? 0,
      totalCost: (r.cost as any)?.total_cost ?? 0,
    },
  };
}
```

---

## 4. Error Taxonomy

The runner must classify all failure modes into a typed `RunnerError`. The following taxonomy covers all known exit paths:

| Error Code | Trigger | Detection Method |
|-----------|---------|-----------------|
| `TIMEOUT` | Child process exceeds `timeoutMs` | `execFile` throws with `err.code === 'ETIMEDOUT'` or `err.killed === true` |
| `MAX_TURNS` | Claude reaches `--max-turns` limit without completing | CLI exits with code 0 but `result` contains a specific message, OR CLI exits non-zero. Heuristic: check `num_turns >= maxTurns` in parsed output. |
| `CLI_ERROR` | Non-zero exit code for any reason other than the above | `err.code !== 'ETIMEDOUT'` and exit code !== 0; attach `err.stderr` |
| `PARSE_ERROR` | stdout is not valid JSON or missing required fields | Thrown by `parseOutput()` |
| `AUTH_ERROR` | Missing or invalid `ANTHROPIC_API_KEY` | CLI exits non-zero; stderr contains "authentication" or "401"; subtype of `CLI_ERROR` |
| `RATE_LIMIT` | Anthropic API 429 | CLI exits non-zero; stderr contains "rate limit" or "429"; subtype of `CLI_ERROR` |

**Design decision:** `AUTH_ERROR` and `RATE_LIMIT` are subtypes of `CLI_ERROR` with distinct `message` content rather than separate `code` values. The seed defines four codes; adding more codes makes the caller's switch statement more complex with no practical benefit — callers that need to distinguish auth errors can inspect `err.message`. This is documented in the spec.

**MAX_TURNS detection nuance:** The CLI exits with code 0 when max-turns is reached (not an error from the CLI's perspective). The runner must detect this post-parse by comparing `result.numTurns >= effectiveMaxTurns`. If true, throw `RunnerError('MAX_TURNS')` with the partial result attached. This allows callers to inspect the partial output if needed.

```typescript
// After successful parse:
if (parsed.numTurns >= effectiveMaxTurns) {
  const err = new RunnerError('MAX_TURNS', `Reached ${parsed.numTurns} turns`);
  err.partialResult = parsed; // attach for caller inspection
  throw err;
}
```

---

## 5. Session Management Analysis

### 5.1 The `--resume` Flag

When `sessionId` is provided in `RunOptions`, the runner passes `--resume <sessionId>`. The CLI then re-establishes conversation context with the Anthropic API using that session ID.

**Critical finding:** Session IDs are server-side references to conversation history stored by Anthropic. They are not locally reconstructable. If a session ID has expired or been garbage-collected server-side, the CLI exits non-zero with an error indicating the session cannot be found. This is caught as `CLI_ERROR`. The caller (STORY-006 state manager) must handle this by starting a fresh session.

### 5.2 Session Expiry

Based on the CLI documentation and hermes-prompt.md §11 patterns:
- Sessions appear to persist for the duration of a conversation but have no guaranteed TTL published by Anthropic.
- In practice, sessions should be treated as ephemeral within a single SDLC phase run. Cross-day or cross-restart resumption should not be assumed.
- **Mitigation already in seed:** `sessionId` is caller-owned. STORY-006 is responsible for deciding whether to resume or start fresh. The runner never retries with a fresh session automatically — that would hide a resume failure.

### 5.3 Multi-Turn Within a Phase

For SDLC phases that require multiple runner invocations (e.g., Phase 8 implementation where the agent iterates file-by-file):
- STORY-006 passes the `sessionId` from invocation N as the `sessionId` for invocation N+1.
- Each invocation has its own timeout (default 120s). The total phase duration is not bounded by the runner — only the per-invocation duration is.
- The 120s per-invocation timeout is appropriate: complex multi-file operations fit within this window; anything longer suggests an infinite loop in Claude's tool use, which the timeout correctly terminates.

### 5.4 Session Persistence Strategy

The runner itself is stateless (per seed design). Session state is managed by callers:

| Caller | Storage | Strategy |
|--------|---------|---------|
| STORY-006 SDLC Engine | In-memory phase state object | Pass `sessionId` forward within a phase run; discard when phase completes |
| STORY-002 Teams conversation handler | In-memory `Map<threadId, sessionId>` (hermes-prompt.md §11 pattern) | Persist session per Teams thread; expire when thread is idle >1 hour |

---

## 6. Tool Permission String Analysis

The three modes defined in the seed map to these `--allowedTools` strings:

```
read-only: "Read,Glob,Grep"
write:     "Read,Write,Edit,Bash(git diff *),Bash(git status *),Bash(git add *),Bash(git commit *)"
custom:    <caller-supplied string>
```

**Observations:**

1. **Bash tool scoping.** `Bash(git diff *)` is a prefix-scoped Bash permission — Claude Code only allows `bash` calls starting with `git diff`. This is a significant security boundary: write mode cannot run arbitrary shell commands.
2. **`git push` is absent from write mode.** Correct. Push is handled by STORY-008's git workflow, which uses its own shell commands (not Claude Code), after PR creation via `gh` CLI.
3. **`Write` vs. `Edit`.** Both are needed in write mode: `Write` creates new files, `Edit` modifies existing ones. Read-only mode correctly excludes both.
4. **`Bash(git add *)` scope.** The `*` here matches any argument to `git add`. This is intentionally broad — Claude Code needs to stage arbitrary paths. A narrower scope would prevent staging new files.
5. **`custom` mode validation.** The runner must validate that `allowedTools` is provided when `mode === 'custom'` and throw at construction time (not invocation time) if it is missing. This prevents silent no-tools-allowed invocations.
6. **Missing tool consideration for SDLC phases.** Phase 8 (implementation) may need `Bash(npm *)` or `Bash(npx *)` for test execution. These are not in the current write mode. STORY-006 will need to use `custom` mode with an expanded tool set for test-running phases. This is acceptable — write mode is a safe default, not a universal capability.

---

## 7. Long-Running Phase Timeout Analysis

The 120s default timeout is per-invocation. SDLC phases can take 5–30 minutes total, but should be decomposed into sub-invocations. Analysis of expected durations:

| Phase | Typical Claude Code Task per Invocation | Expected Duration |
|-------|----------------------------------------|-------------------|
| Phase 1 (Seed) | Generate seed.md from problem statement | 30–60s |
| Phase 6 (Design) | Generate feature-spec.md (large document) | 60–90s |
| Phase 7 (Test Design) | Generate test stubs | 30–60s |
| Phase 8 (Implementation) | Implement one function/endpoint | 45–90s |
| Phase 8 (long) | Implement a complex module with multiple files | 90–180s |

**Finding:** Phase 8 for complex modules may exceed 120s for a single invocation. The seed's constraint says "STORY-006 will chunk phases into sub-invocations." However, if the chunking boundary is at the function level (per CLAUDE.md guidance: "Never implement more than one function/endpoint per prompt"), the 120s budget is adequate for the vast majority of cases.

**Recommendation:** Expose `timeoutMs` as a configurable per-call option (already in the seed interface). STORY-006 should increase this to 300s (5 minutes) for Phase 6 design generation and Phase 8 complex module implementation. The runner's default of 120s remains appropriate for short-read operations.

**Hard cap consideration:** Add a `MAX_TIMEOUT_MS` constant (e.g., 600s / 10 minutes) to prevent callers from setting infinite timeouts. Any `timeoutMs > MAX_TIMEOUT_MS` should be clamped and logged as a warning.

---

## 8. Claude Code Version Pinning Strategy

The JSON output schema (§3) is tied to the installed CLI version. Version drift between development, CI, and the container is the primary schema-stability risk.

**Recommended strategy:**

1. **Pin the version** in the container's `package.json`:
   ```json
   "dependencies": {
     "@anthropic-ai/claude-code": "1.x.x"
   }
   ```
   Use a patch-level pin (`1.2.3`, not `^1.2.3`) until the schema is proven stable across minor versions.

2. **Runtime version assertion.** At runner module initialization, execute `claude --version` and compare against the expected version string stored in a `CLAUDE_CODE_VERSION` environment variable or config constant. Log a warning (not an error) if there is a mismatch. This catches container deployments where the global `claude` binary version diverges from expectations.

3. **Schema parse guard.** The `parseOutput()` function (§3) throws `PARSE_ERROR` on unexpected shape. This is the last-resort protection when a version bump changes the output schema.

4. **Update policy.** Version upgrades should go through a dedicated PR that updates the pin, runs the full test suite with the new CLI version, and verifies the JSON output shape has not changed. Not a routine dependency bump.

---

## 9. Business Case Summary

| Option | Cost Overhead | Maintenance Burden | Control Level | Verdict |
|--------|--------------|---------------------|---------------|---------|
| **CLI (`claude -p`)** | Zero (already installed) | Low — one version pin | High (flags, tools, sessions) | **Selected** |
| **Agent SDK** | Medium (adds npm dep, in-process execution) | Medium — SDK updates, breaking changes | Higher (streaming, model selection) | Defer to v2 if streaming needed |
| **Direct Anthropic API** | Zero dependency overhead | High — must reimplement tool use, file I/O | Maximum | Appropriate for conversational-only calls (hermes-prompt.md §6c); not suitable here |

The CLI is the optimal choice for v1. It provides process isolation, native tool permission enforcement, built-in session management, and cost tracking — all without adding dependencies or reimplementing tool use. The Agent SDK is the natural upgrade path if STORY-006 requires streaming phase output.

---

## 10. Risk Register (Phase 4 Additions)

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| `--output-format json` schema changes in a CLI minor version | Medium | High — silently wrong data | Pin to patch version; `parseOutput()` parse guard; runtime version assertion |
| `maxBuffer` overflow on large phase outputs | Low | Medium — CLI_ERROR with truncated stdout | Set `maxBuffer: 10 * 1024 * 1024`; log stdout length on parse errors |
| Session ID expired on resume | Medium | Low — caller starts fresh session | Document in interface; `CLI_ERROR` is the correct error code; caller handles |
| MAX_TURNS exit code 0 misclassified as success | High | Medium — caller treats incomplete work as done | Post-parse `numTurns >= effectiveMaxTurns` check; throw `MAX_TURNS` error |
| Phase 8 complex module exceeds 120s default | Medium | Low — configurable per STORY-006 | Expose `timeoutMs`; STORY-006 uses 300s for write mode; 600s hard cap |
| `custom` mode called without `allowedTools` string | Medium | Low — silent no-tools invocation | Validate at call site; throw `RunnerError('CLI_ERROR')` before spawning |
| `Bash(git add *)` allows staging unintended files | Low | Medium — unintended git state | Scope is bounded by write mode only; STORY-008 controls branch/push |

---

## 11. Key Decisions for Phase 6 (Design)

The following decisions emerge from this analysis and must be encoded in `feature-spec.md`:

1. **`execFile` with `maxBuffer: 10MB`** — not `spawn`; deferred streaming for v2.
2. **`parseOutput()` as a standalone function** — testable in isolation; called only after successful process exit.
3. **Post-parse MAX_TURNS detection** — compare `numTurns >= effectiveMaxTurns` after parse; throw `MAX_TURNS` before returning to caller.
4. **`sessionId` typed as `string | null`** — not `string`; callers must handle null on first turn.
5. **Configurable `timeoutMs` with 600s hard cap** — STORY-006 overrides to 300s for design/implementation phases.
6. **`custom` mode pre-flight validation** — fail fast before spawning if `allowedTools` is absent.
7. **Runtime version assertion at module init** — log warning on mismatch; do not throw (degrade gracefully).
8. **`partialResult` on `MAX_TURNS` error** — attach parsed output so STORY-006 can inspect what was completed.

---

## 12. Recommendation

**Proceed to Phase 6 (Design).** The CLI-based approach is technically sound and well-validated by the existing hermes-prompt.md patterns. No alternatives need further evaluation. The Phase 6 feature-spec should focus on:

- The `parseOutput()` contract and parse guard
- The `RunnerError` class hierarchy with `partialResult` attachment
- The `maxBuffer` and `timeoutMs` option defaults and hard caps
- The `custom` mode pre-flight validation logic
- The version assertion at module initialization
- Unit test surface area (mock `execFile`, cover all error code paths including the MAX_TURNS exit-code-0 case)
