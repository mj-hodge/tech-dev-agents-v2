# Ops Review: Claude Code Runner (STORY-003)

> Phase 6d — Operations Review
> Date: 2026-03-26
> Reviewer: Phase 6d Ops Analyst

---

## 1. Process Cleanup on Timeout (Zombie Prevention)

**Finding (HIGH):** The spec sets `killSignal: 'SIGTERM'` in `execFileOptions`. Node's `execFile` timeout sends `SIGTERM` to the direct child process. However, if the `claude` CLI spawns its own child processes (e.g., Bash tool subshells), those grandchildren are in the same process group but `SIGTERM` to the parent does not guarantee they are killed.

**Action:** After timeout, send `SIGKILL` to the process group (`process.kill(-pid, 'SIGKILL')`) using the child process PID. Switch from `execFile` timeout to a manual `setTimeout` + `child.kill('SIGTERM')` followed by a 2-second grace period and `child.kill('SIGKILL')`. This requires switching from `execFileAsync` (promisified) to a manual `spawn` wrapper.

**Finding (MEDIUM):** The spec uses `execFile` with `timeout` option. On Windows (WSL2 aside), `timeout` + `killSignal` behavior differs between Node versions. Verify behavior is consistent across Node 18/20/22.

**Action:** Add a CI matrix test that asserts the process is no longer running after a timeout error is thrown.

---

## 2. Memory Usage (10 MB Buffer)

**Finding (MEDIUM):** `MAX_BUFFER = 10 * 1024 * 1024`. `execFile` accumulates the full stdout in memory before resolving. For concurrent invocations (STORY-006 runs multiple phases in parallel), 10 MB per child × N concurrent processes can spike resident memory significantly.

**Action:** Document the concurrency budget in `constants.ts`: at 10 MB/run, 10 concurrent runs = 100 MB buffer headroom required. Expose `MAX_BUFFER` as an env-overridable constant (`CLAUDE_MAX_BUFFER_MB`) so operators can tune it for their workload without a code change.

**Finding (LOW):** There is no check that the response is near the buffer limit before parsing. A response at exactly 10 MB will be silently truncated at JSON boundary, producing a `PARSE_ERROR` with no indication that a buffer overflow was the cause.

**Action:** After `execFile` resolves, check `stdout.length >= MAX_BUFFER * 0.99` and emit a warning: "[ClaudeRunner] stdout near buffer limit — consider increasing MAX_BUFFER or reducing maxTurns."

---

## 3. CLI Version Drift Detection

**Finding (MEDIUM):** `assertVersion()` only runs once per process lifetime (`versionChecked` flag). In long-running processes (e.g., a Teams bot running for days), a `claude` binary upgrade on the host will not be detected after the first check.

**Action:** Add a TTL to the version check (e.g., re-check every 1 hour or every 100 invocations). Store `versionCheckedAt: number` and reset when stale.

**Finding (LOW):** `assertVersion` uses `console.warn` for both mismatch and check failure. In production, these warnings may be lost in log volume. There is no structured log field that monitoring systems can alert on.

**Action:** Emit the version warning as a structured JSON log line with a `type: "version_mismatch"` field so log aggregators (Datadog, CloudWatch) can trigger alerts on first occurrence.

---

## 4. Log Output for Debugging

**Finding (MEDIUM):** The runner emits nothing on the success path. When a run takes 90 seconds and uses 9 turns, there is no log output to confirm it is alive or progressing. Operators diagnosing a hung STORY-006 pipeline have no telemetry from the runner.

**Action:** Log at least two structured lines per invocation: one at spawn time (`[ClaudeRunner] spawning — mode: write, maxTurns: 10, cwd: /repo, pid: 12345`) and one at completion (`[ClaudeRunner] done — turns: 9, cost: $0.14, elapsed: 88s`). Gate both behind a `CLAUDE_RUNNER_LOG=1` env var.

---

## 5. Crash Recovery

**Finding (MEDIUM):** If the Node process crashes mid-invocation, the `claude` subprocess continues running (it is a separate process). On restart, the orphaned subprocess holds an Anthropic API connection and incurs cost with no consumer for its output.

**Action:** Write the child process PID to a temp file (`/tmp/claude-runner-<invocationId>.pid`) at spawn time. On module initialization, read all stale PID files and `SIGKILL` any surviving processes. Remove the PID file on clean exit or timeout handling.

**Finding (LOW):** `sessionId` is caller-owned and not persisted by the runner. If a caller crashes mid-session, the session is lost and the cost of prior turns is unrecoverable.

**Action:** Document explicitly in the module JSDoc that callers using resumable sessions must persist `sessionId` themselves before each subsequent `run()` call.
