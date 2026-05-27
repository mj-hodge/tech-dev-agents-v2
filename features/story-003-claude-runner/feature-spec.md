# Feature Spec: Claude Code Runner (STORY-003)

> Phase 6 — Design
> Date: 2026-03-26
> Scope: Medium
> Architect: Phase 6 Systems Architect

---

## 1. Module Interface

The runner exposes a single function and four types. All types live in `src/runner/types.ts`; the function lives in `src/runner/index.ts`.

### 1.1 RunOptions

```typescript
export type RunMode = 'read-only' | 'write' | 'custom';

export interface RunOptions {
  /** Tool permission mode. Default: 'read-only'. */
  mode?: RunMode;

  /** Required when mode === 'custom'. Comma-separated tool list passed to --allowedTools. */
  allowedTools?: string;

  /** Resume an existing conversation. Passed as --resume <sessionId>. */
  sessionId?: string;

  /** Max agentic turns. Default: 5 (read-only), 10 (write/custom). Capped at MAX_TURNS_CAP. */
  maxTurns?: number;

  /** Per-invocation timeout in milliseconds. Default: 120_000. Clamped to MAX_TIMEOUT_MS. */
  timeoutMs?: number;

  /** Working directory for the CLI process. Default: process.env.REPO_PATH ?? process.cwd(). */
  cwd?: string;

  /** Additional system prompt text. Passed via --append-system-prompt. */
  systemPrompt?: string;

  /** Model override. Passed via --model if provided. Default: CLI default (not specified). */
  model?: string;
}
```

### 1.2 RunResult

```typescript
export interface RunResult {
  /** Final assistant response text. */
  result: string;

  /** Session ID for resumption. Null on first turn if the CLI omits it. */
  sessionId: string | null;

  /** Number of agentic turns consumed. */
  numTurns: number;

  /** Token and cost data for this invocation. */
  cost: CostInfo;
}
```

### 1.3 CostInfo

```typescript
export interface CostInfo {
  inputTokens: number;
  outputTokens: number;
  /** USD cost for this invocation. */
  totalCost: number;
}
```

### 1.4 RunnerError

```typescript
export type RunnerErrorCode =
  | 'TIMEOUT'
  | 'AUTH'
  | 'RATE_LIMIT'
  | 'PARSE_ERROR'
  | 'MAX_TURNS'
  | 'CLI_ERROR';

export class RunnerError extends Error {
  readonly code: RunnerErrorCode;

  /** Raw stderr from the CLI process, when available. */
  readonly stderr?: string;

  /** Partial parsed output, attached only for MAX_TURNS errors. */
  readonly partialResult?: RunResult;

  constructor(code: RunnerErrorCode, message: string, options?: {
    stderr?: string;
    partialResult?: RunResult;
  }) {
    super(message);
    this.name = 'RunnerError';
    this.code = code;
    this.stderr = options?.stderr;
    this.partialResult = options?.partialResult;
  }
}
```

### 1.5 Exported Function

```typescript
// src/runner/index.ts
export async function run(prompt: string, options?: RunOptions): Promise<RunResult>;
```

No class instantiation, no factory. A single stateless function. Callers import `{ run }` and optionally the types.

---

## 2. CLI Invocation

### 2.1 Flag Construction

The `run` function builds an argument array from `RunOptions` and passes it to `execFile`. The argument array is constructed as follows:

```typescript
function buildArgs(prompt: string, opts: Required<ResolvedOptions>): string[] {
  const args: string[] = [
    '-p', prompt,
    '--bare',
    '--output-format', 'json',
    '--max-turns', String(opts.maxTurns),
    '--allowedTools', opts.allowedTools,
  ];

  if (opts.sessionId) {
    args.push('--resume', opts.sessionId);
  }

  if (opts.systemPrompt) {
    args.push('--append-system-prompt', opts.systemPrompt);
  }

  if (opts.model) {
    args.push('--model', opts.model);
  }

  return args;
}
```

**Invariant flags (always present):**
- `-p <prompt>` -- headless prompt mode
- `--bare` -- skip CLAUDE.md, hooks, MCP discovery (~800ms savings)
- `--output-format json` -- structured output, no streaming
- `--max-turns <n>` -- cost control
- `--allowedTools <tools>` -- permission scoping

**Conditional flags:**
- `--resume <sessionId>` -- only when `opts.sessionId` is truthy
- `--append-system-prompt <text>` -- only when `opts.systemPrompt` is truthy
- `--model <model>` -- only when `opts.model` is truthy

### 2.2 execFile Options

```typescript
const execFileOpts: ExecFileOptions = {
  cwd: resolvedCwd,
  timeout: resolvedTimeoutMs,
  maxBuffer: MAX_BUFFER,         // 10 * 1024 * 1024 (10 MB)
  env: { ...process.env },       // inherit full env; ANTHROPIC_API_KEY comes from process.env
  killSignal: 'SIGTERM',         // graceful shutdown signal
};
```

**Key constants:**

| Constant | Value | Rationale |
|----------|-------|-----------|
| `MAX_BUFFER` | `10 * 1024 * 1024` (10 MB) | CLI JSON output is result text only (no file content), but large phase outputs can reach several MB. 10 MB is a conservative upper bound. |
| `DEFAULT_TIMEOUT_MS` | `120_000` (2 min) | Adequate for single-function invocations per hermes-prompt.md section 12. |
| `MAX_TIMEOUT_MS` | `600_000` (10 min) | Hard cap. Any `timeoutMs > MAX_TIMEOUT_MS` is clamped and a warning is logged. |
| `DEFAULT_MAX_TURNS_READ` | `5` | Cost-safe default for read-only operations. |
| `DEFAULT_MAX_TURNS_WRITE` | `10` | Write operations need more turns for edit-test cycles. |
| `MAX_TURNS_CAP` | `Number(process.env.CLAUDE_MAX_TURNS_CAP) \|\| 20` | Environment-overridable hard cap. |

### 2.3 Working Directory Resolution

```typescript
function resolveCwd(opts: RunOptions): string {
  if (opts.cwd) return opts.cwd;
  if (process.env.REPO_PATH) return process.env.REPO_PATH;
  return process.cwd();
}
```

Priority: explicit `cwd` option > `REPO_PATH` env var > `process.cwd()`.

### 2.4 Environment Setup

The runner does NOT set `ANTHROPIC_API_KEY` or any other secret as a CLI flag. It inherits the full `process.env` via `{ ...process.env }` in the `execFile` options. The container (STORY-001) is responsible for injecting `ANTHROPIC_API_KEY` into the process environment.

No environment variables are added, removed, or mutated by the runner.

---

## 3. Output Parsing

### 3.1 Expected JSON Schema

The CLI `--output-format json` produces:

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

All keys are snake_case. The runner maps them to camelCase in `RunResult`.

### 3.2 parseOutput Function

`parseOutput` is a standalone, exported function (testable in isolation). It performs three validations:

1. **JSON.parse guard:** If `stdout` is not valid JSON, throw `PARSE_ERROR`.
2. **Shape guard:** If the parsed object lacks a `result` field of type `string`, throw `PARSE_ERROR`.
3. **Defensive field extraction:** All other fields use nullish coalescing to handle missing/null values.

```typescript
export function parseOutput(stdout: string): RunResult {
  let raw: unknown;
  try {
    raw = JSON.parse(stdout);
  } catch {
    throw new RunnerError('PARSE_ERROR', `stdout is not valid JSON: ${stdout.slice(0, 200)}`);
  }

  if (typeof raw !== 'object' || raw === null) {
    throw new RunnerError('PARSE_ERROR', `Expected JSON object, got: ${typeof raw}`);
  }

  const obj = raw as Record<string, unknown>;

  if (typeof obj.result !== 'string') {
    throw new RunnerError('PARSE_ERROR', `Missing or non-string "result" field: ${stdout.slice(0, 200)}`);
  }

  const cost = obj.cost as Record<string, unknown> | undefined | null;

  return {
    result: obj.result as string,
    sessionId: typeof obj.session_id === 'string' ? obj.session_id : null,
    numTurns: typeof obj.num_turns === 'number' ? obj.num_turns : 0,
    cost: {
      inputTokens: typeof cost?.input_tokens === 'number' ? cost.input_tokens : 0,
      outputTokens: typeof cost?.output_tokens === 'number' ? cost.output_tokens : 0,
      totalCost: typeof cost?.total_cost === 'number' ? cost.total_cost : 0,
    },
  };
}
```

### 3.3 Missing Field Handling

| Field | Missing Behavior |
|-------|------------------|
| `result` | **Throw PARSE_ERROR.** This is the only required field. A response without result text is structurally broken. |
| `session_id` | Return `null`. Callers must handle null (first turn, or CLI version that omits it). |
| `num_turns` | Return `0`. Prevents MAX_TURNS false positive (0 < any maxTurns threshold). |
| `cost` | Return `{ inputTokens: 0, outputTokens: 0, totalCost: 0 }`. Callers see zero cost, not undefined. |
| `cost.input_tokens` | Return `0`. Individual cost field absence does not fail the parse. |
| `cost.output_tokens` | Return `0`. |
| `cost.total_cost` | Return `0`. |

---

## 4. Error Taxonomy

Six error codes, each with deterministic detection logic. The detection order matters: earlier checks take priority.

### 4.1 Detection Flow

```
execFile rejects
  ├─ err.killed === true OR err.code === 'ETIMEDOUT'
  │    → TIMEOUT
  ├─ stderr contains "authentication" or "401" (case-insensitive)
  │    → AUTH
  ├─ stderr contains "rate limit" or "429" (case-insensitive)
  │    → RATE_LIMIT
  └─ any other non-zero exit
       → CLI_ERROR

execFile resolves (exit code 0)
  ├─ parseOutput throws
  │    → PARSE_ERROR
  └─ parsed.numTurns >= effectiveMaxTurns
       → MAX_TURNS (with partialResult attached)
```

### 4.2 Error Code Reference

| Code | Exit Code | Detection | RunnerError Properties |
|------|-----------|-----------|----------------------|
| `TIMEOUT` | N/A (killed) | `err.killed === true` OR `err.code === 'ETIMEDOUT'` | `message`: "Process timed out after {n}ms" |
| `AUTH` | Non-zero | `stderr` matches `/authenticat(ion\|e)\|401/i` | `message`: "Authentication failed", `stderr`: raw stderr |
| `RATE_LIMIT` | Non-zero | `stderr` matches `/rate.?limit\|429/i` | `message`: "Rate limited by Anthropic API", `stderr`: raw stderr |
| `CLI_ERROR` | Non-zero | Fallback for any other non-zero exit | `message`: "CLI exited with code {n}", `stderr`: raw stderr |
| `PARSE_ERROR` | 0 | `parseOutput()` throws | `message`: details from parseOutput |
| `MAX_TURNS` | 0 | `parsed.numTurns >= effectiveMaxTurns` | `message`: "Reached {n} turns (limit: {m})", `partialResult`: the parsed RunResult |

### 4.3 Implementation

```typescript
async function run(prompt: string, options?: RunOptions): Promise<RunResult> {
  const opts = resolveOptions(options);
  validateOptions(opts);

  const args = buildArgs(prompt, opts);
  const execOpts = buildExecOptions(opts);

  let stdout: string;
  let stderr: string;

  try {
    const result = await execFileAsync('claude', args, execOpts);
    stdout = result.stdout;
    stderr = result.stderr;
  } catch (err: unknown) {
    throw classifyExecError(err, opts);
  }

  const parsed = parseOutput(stdout);

  if (parsed.numTurns >= opts.maxTurns) {
    throw new RunnerError('MAX_TURNS',
      `Reached ${parsed.numTurns} turns (limit: ${opts.maxTurns})`,
      { partialResult: parsed }
    );
  }

  return parsed;
}

function classifyExecError(err: unknown, opts: ResolvedOptions): RunnerError {
  const e = err as ExecFileError;

  // TIMEOUT: process was killed due to timeout
  if (e.killed || e.code === 'ETIMEDOUT') {
    return new RunnerError('TIMEOUT',
      `Process timed out after ${opts.timeoutMs}ms`);
  }

  const stderr = e.stderr ?? '';

  // AUTH: authentication failure
  if (/authenticat(?:ion|e)|401/i.test(stderr)) {
    return new RunnerError('AUTH',
      'Authentication failed', { stderr });
  }

  // RATE_LIMIT: Anthropic API rate limiting
  if (/rate.?limit|429/i.test(stderr)) {
    return new RunnerError('RATE_LIMIT',
      'Rate limited by Anthropic API', { stderr });
  }

  // CLI_ERROR: everything else
  return new RunnerError('CLI_ERROR',
    `CLI exited with code ${e.code ?? 'unknown'}`, { stderr });
}
```

---

## 5. Session Management

### 5.1 Flow

Sessions are entirely caller-owned. The runner is stateless.

```
Caller                           Runner                        CLI
  │                                │                            │
  │  run(prompt, {})               │                            │
  │ ──────────────────────────────>│                            │
  │                                │  claude -p ... --bare ...  │
  │                                │ ──────────────────────────>│
  │                                │  { session_id: "abc-123" } │
  │                                │ <──────────────────────────│
  │  RunResult { sessionId: "abc-123" }                         │
  │ <──────────────────────────────│                            │
  │                                │                            │
  │  run(prompt2, { sessionId: "abc-123" })                     │
  │ ──────────────────────────────>│                            │
  │                                │  claude -p ... --resume abc-123
  │                                │ ──────────────────────────>│
  │                                │  { session_id: "abc-123" } │
  │                                │ <──────────────────────────│
  │  RunResult { sessionId: "abc-123" }                         │
  │ <──────────────────────────────│                            │
```

### 5.2 Rules

1. **No persistence in the runner.** The runner does not store session IDs in memory, on disk, or in any database. It receives `sessionId` as input and returns it as output.
2. **Null session ID is valid.** The first invocation in a conversation may return `sessionId: null` if the CLI omits it. Callers must handle this.
3. **Expired sessions produce CLI_ERROR.** If a session ID has expired server-side, the CLI exits non-zero. The runner classifies this as `CLI_ERROR`. The caller is responsible for starting a fresh session.
4. **No auto-retry on session failure.** The runner never silently retries with a different (or no) session ID. That would mask resume failures from the caller.

### 5.3 Caller Responsibilities (Out of Runner Scope)

| Caller | Where Sessions Live | Lifecycle |
|--------|-------------------|-----------|
| STORY-006 SDLC Engine | In-memory phase state object | Created at phase start, discarded at phase end |
| STORY-002 Teams handler | `Map<threadId, sessionId>` (hermes-prompt.md section 11) | Created per Teams thread, expired on idle timeout |

---

## 6. Tool Permission Modes

### 6.1 Mode to --allowedTools Mapping

```typescript
const TOOL_SETS: Record<Exclude<RunMode, 'custom'>, string> = {
  'read-only': 'Read,Glob,Grep',
  'write':     'Read,Write,Edit,Bash(git diff *),Bash(git status *),Bash(git add *),Bash(git commit *)',
};

function resolveAllowedTools(opts: RunOptions): string {
  if (opts.mode === 'custom') {
    if (!opts.allowedTools) {
      throw new RunnerError('CLI_ERROR',
        'allowedTools is required when mode is "custom"');
    }
    return opts.allowedTools;
  }
  const mode = opts.mode ?? 'read-only';
  return TOOL_SETS[mode];
}
```

### 6.2 Mode Defaults

| Mode | --allowedTools value | Default maxTurns |
|------|---------------------|-----------------|
| `read-only` (default) | `Read,Glob,Grep` | 5 |
| `write` | `Read,Write,Edit,Bash(git diff *),Bash(git status *),Bash(git add *),Bash(git commit *)` | 10 |
| `custom` | Caller-supplied string | 10 |

### 6.3 Validation Rules

1. **`custom` mode requires `allowedTools`.** If `mode === 'custom'` and `allowedTools` is falsy, throw `RunnerError('CLI_ERROR')` before spawning the process. Fail fast.
2. **`allowedTools` is ignored for non-custom modes.** If the caller provides `allowedTools` alongside `mode: 'read-only'`, the runner uses the predefined read-only tool set and ignores the caller's string. This prevents accidental privilege escalation.
3. **No empty tool set.** The runner always passes a non-empty `--allowedTools` value. An empty string would give Claude Code unrestricted tool access.

### 6.4 Security Notes

- `Bash(git diff *)` is prefix-scoped: the CLI only permits bash commands starting with `git diff`. Arbitrary shell execution is blocked.
- `git push` is deliberately absent from write mode. Push is handled by STORY-008's git workflow layer.
- STORY-006 may use `custom` mode with expanded tool sets (e.g., adding `Bash(npm test *)` for test execution phases). The runner does not restrict custom tool strings; that responsibility belongs to the caller.

---

## 7. Cost Tracking

### 7.1 Extraction

Cost data is extracted from the CLI JSON output during `parseOutput()`. The `cost` object in the raw JSON uses snake_case keys; the runner maps them to camelCase in `CostInfo`.

```json
// Raw CLI output
{ "cost": { "input_tokens": 1240, "output_tokens": 380, "total_cost": 0.0187 } }
```

```typescript
// Mapped RunResult.cost
{ inputTokens: 1240, outputTokens: 380, totalCost: 0.0187 }
```

### 7.2 Missing Cost Data

If the `cost` object or any of its fields is missing/null/non-numeric, the runner returns zero values (not undefined, not null). This means:

- Callers can always access `result.cost.totalCost` without null checks.
- Zero cost is distinguishable from "cost data unavailable" only by convention: a real invocation with zero tokens is impossible. Callers that need the distinction should check `result.cost.inputTokens === 0 && result.cost.outputTokens === 0`.

### 7.3 Cost on MAX_TURNS Error

When the runner throws `MAX_TURNS`, the `partialResult` includes cost data. Callers should still account for this cost even though the invocation is treated as an error:

```typescript
try {
  const result = await run(prompt, opts);
  trackCost(result.cost);
} catch (err) {
  if (err instanceof RunnerError && err.code === 'MAX_TURNS' && err.partialResult) {
    trackCost(err.partialResult.cost); // still incurred cost
  }
}
```

### 7.4 Cost Budget Enforcement

The runner does NOT enforce cost budgets. It reports cost. Budget enforcement (daily caps, per-user limits, alerts) is an application-level concern handled by callers (STORY-006, STORY-002).

---

## 8. Version Pin and Runtime Assertion

### 8.1 Version Pin

The `@anthropic-ai/claude-code` package is pinned to an exact patch version in `package.json`:

```json
{
  "dependencies": {
    "@anthropic-ai/claude-code": "1.0.32"
  }
}
```

No caret (`^`), no tilde (`~`). Exact pin. Version upgrades require a dedicated PR with full test suite verification.

### 8.2 Runtime Version Assertion

At module initialization (top-level side effect when `src/runner/index.ts` is first imported), the runner executes `claude --version` and compares the output against the expected version.

```typescript
const EXPECTED_CLI_VERSION = '1.0.32';

let versionChecked = false;

async function assertVersion(): Promise<void> {
  if (versionChecked) return;
  versionChecked = true;

  try {
    const { stdout } = await execFileAsync('claude', ['--version'], {
      timeout: 5_000,
    });
    const installed = stdout.trim();
    if (!installed.includes(EXPECTED_CLI_VERSION)) {
      console.warn(
        `[ClaudeRunner] Version mismatch: expected ${EXPECTED_CLI_VERSION}, got "${installed}". ` +
        'JSON output schema may have changed.'
      );
    }
  } catch {
    console.warn('[ClaudeRunner] Unable to verify claude CLI version.');
  }
}
```

**Behavior:**
- Log a warning on mismatch. Do NOT throw. The runner should degrade gracefully.
- Called once, on first `run()` invocation. Subsequent calls skip the check (`versionChecked` flag).
- 5-second timeout for the version check itself. If it fails, warn and continue.

---

## 9. Option Resolution

All option defaults and clamping are handled in a single `resolveOptions` function:

```typescript
interface ResolvedOptions {
  mode: RunMode;
  allowedTools: string;
  sessionId: string | undefined;
  maxTurns: number;
  timeoutMs: number;
  cwd: string;
  systemPrompt: string | undefined;
  model: string | undefined;
}

function resolveOptions(opts?: RunOptions): ResolvedOptions {
  const mode = opts?.mode ?? 'read-only';
  const allowedTools = resolveAllowedTools(opts ?? {});

  const defaultMaxTurns = mode === 'read-only' ? DEFAULT_MAX_TURNS_READ : DEFAULT_MAX_TURNS_WRITE;
  const maxTurns = Math.min(opts?.maxTurns ?? defaultMaxTurns, MAX_TURNS_CAP);

  const timeoutMs = Math.min(opts?.timeoutMs ?? DEFAULT_TIMEOUT_MS, MAX_TIMEOUT_MS);
  if (opts?.timeoutMs && opts.timeoutMs > MAX_TIMEOUT_MS) {
    console.warn(
      `[ClaudeRunner] timeoutMs ${opts.timeoutMs} exceeds MAX_TIMEOUT_MS (${MAX_TIMEOUT_MS}). Clamped.`
    );
  }

  return {
    mode,
    allowedTools,
    sessionId: opts?.sessionId,
    maxTurns,
    timeoutMs,
    cwd: resolveCwd(opts ?? {}),
    systemPrompt: opts?.systemPrompt,
    model: opts?.model,
  };
}
```

---

## 10. Implementation Plan

Ordered file list. Each file is a single unit of work, implementable in one prompt.

| Order | File | Description |
|-------|------|-------------|
| 1 | `src/runner/types.ts` | All type definitions: `RunMode`, `RunOptions`, `RunResult`, `CostInfo`, `RunnerErrorCode`, `RunnerError` class. No logic, only types. |
| 2 | `src/runner/constants.ts` | All constants: `MAX_BUFFER`, `DEFAULT_TIMEOUT_MS`, `MAX_TIMEOUT_MS`, `DEFAULT_MAX_TURNS_READ`, `DEFAULT_MAX_TURNS_WRITE`, `MAX_TURNS_CAP`, `EXPECTED_CLI_VERSION`, `TOOL_SETS`. |
| 3 | `src/runner/parse-output.ts` | `parseOutput(stdout: string): RunResult` function. Standalone, no dependencies beyond types. |
| 4 | `src/runner/build-args.ts` | `buildArgs(prompt, opts): string[]` and `resolveAllowedTools(opts): string`. Flag construction logic. |
| 5 | `src/runner/resolve-options.ts` | `resolveOptions(opts?): ResolvedOptions`. Default application, clamping, validation. |
| 6 | `src/runner/classify-error.ts` | `classifyExecError(err, opts): RunnerError`. Error taxonomy detection logic. |
| 7 | `src/runner/version-check.ts` | `assertVersion(): Promise<void>`. One-time CLI version assertion. |
| 8 | `src/runner/index.ts` | `run(prompt, options): Promise<RunResult>`. Orchestrates all the above. Single public export plus re-exports of types. |

### Test Files (Phase 7)

| Order | File | Covers |
|-------|------|--------|
| T1 | `tests/runner/parse-output.test.ts` | Valid JSON, missing fields, malformed JSON, missing result field |
| T2 | `tests/runner/build-args.test.ts` | All flag combinations, session resume, system prompt, model override |
| T3 | `tests/runner/resolve-options.test.ts` | Defaults, clamping, custom mode validation |
| T4 | `tests/runner/classify-error.test.ts` | All 6 error codes, stderr pattern matching |
| T5 | `tests/runner/run.test.ts` | Integration: mocked execFile, full happy path, all error paths, MAX_TURNS detection |

---

## 11. Module Boundary Summary

```
src/runner/
  index.ts            ← public API: run(), re-exports types
  types.ts            ← RunOptions, RunResult, CostInfo, RunnerError
  constants.ts        ← MAX_BUFFER, timeouts, turn caps, tool sets
  parse-output.ts     ← parseOutput()
  build-args.ts       ← buildArgs(), resolveAllowedTools()
  resolve-options.ts  ← resolveOptions()
  classify-error.ts   ← classifyExecError()
  version-check.ts    ← assertVersion()
```

**Dependency graph (internal):**

```
index.ts
  ├── types.ts
  ├── constants.ts
  ├── parse-output.ts      ← depends on types.ts
  ├── build-args.ts        ← depends on types.ts, constants.ts
  ├── resolve-options.ts   ← depends on types.ts, constants.ts, build-args.ts
  ├── classify-error.ts    ← depends on types.ts
  └── version-check.ts     ← depends on constants.ts
```

No circular dependencies. Every internal module depends only on `types.ts` and/or `constants.ts`. The `index.ts` orchestrator is the only file that imports all others.

**External dependencies:** `node:child_process` (execFile), `node:util` (promisify). No third-party packages.

---

## 12. Constraints and Non-Goals (Restated for Clarity)

**In scope:**
- Single `run()` function with typed input/output
- CLI invocation via `execFile` with `--bare --output-format json`
- Deterministic error classification into 6 codes
- Session ID pass-through (caller-owned)
- Cost extraction from JSON output
- Version pin and runtime assertion
- Pre-flight validation (custom mode, timeout clamping)

**Out of scope (unchanged from seed):**
- Streaming output (SSE/chunked) -- v2
- Retry logic -- caller responsibility
- Parallel invocation queue -- STORY-006
- Model selection policy -- STORY-005 personas
- Cost budget enforcement -- application layer
- MCP tool registration -- future story
- Conversation history beyond sessionId -- STORY-006
