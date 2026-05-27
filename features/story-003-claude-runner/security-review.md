# Security Review: Claude Code Runner (STORY-003)

> Phase 6b — Security Review
> Date: 2026-03-26
> Reviewer: Phase 6b Security Analyst

---

## 1. Command Injection via Prompt / Options

**Finding (MEDIUM):** `buildArgs` passes `prompt` directly as the value of `-p`. Because `execFile` is used (not `exec`/shell), the prompt string cannot escape into shell metacharacter injection. This is safe as long as `execFile` is never replaced with `spawn({ shell: true })` or `exec()`.

**Action:** Add an ESLint rule or code comment at `build-args.ts` explicitly banning `exec`/shell mode. Document the invariant in `constants.ts`.

**Finding (LOW):** `systemPrompt` and `model` are also passed as argument values via `execFile`. No additional sanitization is needed, but callers using `custom` mode pass `allowedTools` as a raw string. A caller could pass `"Read,Bash(*)"` to grant unrestricted Bash.

**Action:** Validate `allowedTools` in `resolveOptions` to reject patterns matching `Bash(*)` without a subcommand constraint (i.e., block bare `Bash(*)` or `Bash` with no parenthesized prefix). Log a warning and throw `CLI_ERROR` before spawn.

---

## 2. Tool Permission Escalation

**Finding (MEDIUM):** `custom` mode forwards the caller's `allowedTools` string verbatim to `--allowedTools`. The runner spec states this is the caller's responsibility, but there is no defense in depth inside the runner itself.

**Action:** Maintain a denylist of unconditionally blocked tool patterns (e.g., `Bash(rm *)`, `Bash(curl *)`, `Bash(wget *)`). Apply it even in `custom` mode and throw `CLI_ERROR` with a descriptive message if a denied pattern is detected.

**Finding (LOW):** The spec explicitly ignores `allowedTools` when `mode` is not `custom` (§6.3 rule 2). This is correct. Verify by test that a caller supplying `allowedTools` alongside `mode: 'write'` never sees those tools propagated to the CLI args.

---

## 3. Environment Variable Leakage to Child Process

**Finding (HIGH):** `env: { ...process.env }` copies the full parent environment into the child process. This includes all secrets present in the parent — database URLs, OAuth tokens, internal service keys — not just `ANTHROPIC_API_KEY`. The CLI subprocess receives every secret the Node process holds.

**Action:** Define an explicit allowlist of env vars passed to the child. At minimum: `ANTHROPIC_API_KEY`, `PATH`, `HOME`, `TMPDIR`, `TERM`, `LANG`, `LC_ALL`. Strip everything else. This is the single highest-impact security change in the spec.

---

## 4. Secret Exposure in CLI Output / Logs

**Finding (MEDIUM):** `parseOutput` includes up to 200 characters of raw stdout in `PARSE_ERROR` messages. If stdout contains partial JSON with embedded user data or API keys reflected from a prompt, those appear in error messages that may be logged.

**Action:** Scrub known secret patterns (hex strings > 32 chars, strings matching `sk-ant-*`) from the 200-char excerpt before embedding in the error message.

**Finding (LOW):** `stderr` is attached verbatim to `AUTH`, `RATE_LIMIT`, and `CLI_ERROR`. The CLI may echo the API key in an error message (e.g., "invalid key: sk-ant-...").

**Action:** Apply the same scrub to `stderr` before storing it on `RunnerError`.

---

## 5. Working Directory Access Boundaries

**Finding (MEDIUM):** `resolveCwd` accepts an arbitrary caller-supplied path and passes it directly to `execFile` as `cwd`. There is no validation that the path is within an expected project root, is not `/`, and actually exists as a directory. The CLI inherits this cwd and uses it as the root for all file operations.

**Action:** In `resolveOptions`, validate that `cwd` (after resolution):
1. Is an absolute path.
2. Exists and is a directory (`fs.statSync`).
3. Is within `REPO_PATH` (if set) or the project root (configurable allowlist). Reject paths traversing outside with `CLI_ERROR`.
