# Seed: Claude Code Runner (STORY-003)

> Phase 1 — Concept & Seed
> Date: 2026-03-26
> Scope: Medium
> Path: 1 → 4 → 6 → [6b, 6c, 6d] → 7 → 8 → 8b → 11 → Done

---

## Problem Statement

The agent container has Claude Code CLI installed (STORY-001), but there is no programmatic layer to invoke it reliably for autonomous work. Direct shell calls from application code need proper flag composition, output parsing, session tracking, permission scoping, and failure handling. Without this abstraction, every consumer (SDLC engine, git workflow) would re-implement the same invocation logic inconsistently — leading to cost overruns, broken sessions, and silent failures.

This story builds the `ClaudeRunner` module: a single, tested TypeScript wrapper that all other stories use to invoke Claude Code headlessly.

---

## Acceptance Criteria

- [ ] **Headless invocation:** `runner.run(prompt, options)` executes `claude -p <prompt>` with correct flags (`--bare`, `--output-format json`, `--max-turns`, `--allowedTools`, `--append-system-prompt`)
- [ ] **JSON output parsing:** Runner parses the JSON response and returns a typed result object with `{ result: string, cost: CostInfo, sessionId: string, numTurns: number }`
- [ ] **Cost tracking:** Every invocation returns `cost.inputTokens`, `cost.outputTokens`, `cost.totalCost`; caller receives these values without additional parsing
- [ ] **Session management:** Runner accepts an optional `sessionId` to resume an existing session (`--resume <session-id>`); returned `sessionId` is persisted by the caller for subsequent turns
- [ ] **Tool permission enforcement:** Runner accepts a `mode` parameter (`read-only` | `write`) that maps to pre-defined allowed tool sets:
  - `read-only`: `Read,Glob,Grep`
  - `write`: `Read,Write,Edit,Bash(git diff *),Bash(git status *),Bash(git add *),Bash(git commit *)`
  - `custom`: caller supplies explicit `allowedTools` string
- [ ] **Timeout handling:** Default timeout of 120 seconds; configurable per call; timeout produces a typed `RunnerError` with `code: 'TIMEOUT'`
- [ ] **Max-turns limit:** Default 10 turns; read-only mode defaults to 5; configurable per call; exceeded turns produces `RunnerError` with `code: 'MAX_TURNS'`
- [ ] **Exit code handling:** Non-zero exit from `claude` CLI is caught and wrapped in `RunnerError` with `code: 'CLI_ERROR'` and the raw stderr attached
- [ ] **Working directory:** Runner accepts a `cwd` parameter; defaults to `REPO_PATH` environment variable
- [ ] **System prompt injection:** Runner accepts an optional `systemPrompt` string passed via `--append-system-prompt`
- [ ] **Unit tests:** All run modes, error paths, and output parsing covered with mocked `execFile`

---

## Constraints

| Constraint | Detail |
|-----------|--------|
| **Default timeout** | 120 seconds (matches Teams UX expectation from hermes-prompt.md §12) |
| **Default max-turns (read-only)** | 5 turns |
| **Default max-turns (write)** | 10 turns |
| **Max-turns hard cap** | 20 turns (cost safety; configurable via env `CLAUDE_MAX_TURNS_CAP`) |
| **CLI flag** | Must use `--bare` to skip hook/MCP/CLAUDE.md discovery (~800ms startup saving per hermes-prompt.md §6a) |
| **Output format** | `--output-format json` only; streaming not supported in v1 |
| **Secrets** | `ANTHROPIC_API_KEY` injected from process environment; never passed as a flag |
| **Node.js** | TypeScript, Node.js 22 LTS, `child_process.execFile` (not `exec`) |
| **No retries** | Runner does not retry on failure; retry logic is the caller's responsibility |

---

## Out of Scope

- Streaming output (SSE or chunked responses) — deferred to v2
- Parallel invocations or invocation queue management — belongs in STORY-006
- Selecting which Claude model to use — hardcoded to the CLI default; model selection is a persona concern (STORY-005)
- Cost budget enforcement or daily caps — application-level concern, not runner concern
- MCP tool registration or custom tool definitions — future story
- Conversation history management beyond `sessionId` — STORY-006 concern
- Integration with Teams message threading — STORY-002 concern

---

## Key Design Decisions

**`execFile` over `exec`:** Avoids shell injection; arguments are passed as an array. Aligns with the pattern in hermes-prompt.md §7.

**Typed result and error:** Callers never need to inspect raw stdout/stderr. `RunResult` and `RunnerError` are the only exit paths.

**Mode-based tool sets, not raw strings:** Reduces misconfiguration risk in callers. The three modes (read-only, write, custom) cover all known use cases across STORY-006 and STORY-008.

**Session ID is caller-owned:** Runner returns `sessionId` but does not store it. The session map pattern (hermes-prompt.md §11) lives in the caller (STORY-006 state manager or STORY-002 conversation handler).

---

## Module Interface (Sketch)

```typescript
// src/runner/types.ts
export type RunMode = 'read-only' | 'write' | 'custom';

export interface RunOptions {
  mode?: RunMode;               // default: 'read-only'
  allowedTools?: string;        // required when mode === 'custom'
  sessionId?: string;           // resume existing session
  maxTurns?: number;            // default: 5 (read-only) or 10 (write)
  timeoutMs?: number;           // default: 120_000
  cwd?: string;                 // default: process.env.REPO_PATH
  systemPrompt?: string;        // appended to base system prompt
}

export interface CostInfo {
  inputTokens: number;
  outputTokens: number;
  totalCost: number;
}

export interface RunResult {
  result: string;
  sessionId: string;
  numTurns: number;
  cost: CostInfo;
}

export class RunnerError extends Error {
  code: 'TIMEOUT' | 'MAX_TURNS' | 'CLI_ERROR' | 'PARSE_ERROR';
  stderr?: string;
}

// src/runner/index.ts
export async function run(prompt: string, options?: RunOptions): Promise<RunResult>
```

---

## Dependencies

- **STORY-001:** Container with `claude` CLI on `PATH`, `ANTHROPIC_API_KEY` in environment
- **Downstream:** STORY-006 (SDLC engine), STORY-008 (git workflow)

---

## Risks

| Risk | Mitigation |
|------|-----------|
| `--output-format json` shape changes across CLI versions | Pin `@anthropic-ai/claude-code` version in `package.json`; add a parse-guard with fallback |
| `--resume` session IDs expiring server-side | Return `RunnerError` with `code: 'CLI_ERROR'`; caller starts a fresh session |
| 120s timeout too short for large SDLC phases | STORY-006 will chunk phases into sub-invocations; runner timeout is per-invocation, not per-phase |

---

## Next Phase

**Phase 4 (Analysis):** Evaluate the Claude Code CLI JSON output schema in detail, assess `execFile` vs. SDK alternatives, and identify edge cases in session resumption and tool permission strings. Produce `analysis.md`.
