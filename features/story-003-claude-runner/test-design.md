# Test Design: Claude Code Runner (STORY-003)

> Phase 7 — Test Design
> Date: 2026-03-26
> Scope: Medium

## Goal

Design RED tests that lock down the Python Claude runner contract before implementation.

## Test Cases

1. `test_parse_output_maps_json_to_typed_result`
   - Parses snake_case CLI JSON into `RunResult`.
   - Verifies `result`, `session_id`, `num_turns`, and nested `cost` mapping.

2. `test_parse_output_rejects_invalid_json`
   - Invalid JSON raises `RunnerError` with `code == "PARSE_ERROR"`.

3. `test_run_builds_expected_claude_args_for_read_only`
   - Calls `claude -p <prompt>` with `--bare`, `--output-format json`, `--max-turns`, `--allowedTools`.
   - Verifies default read-only tool set.

4. `test_run_uses_write_tool_set`
   - Verifies write mode uses the write tool list.

5. `test_run_uses_custom_allowed_tools_when_provided`
   - Verifies custom mode forwards caller-supplied tools.

6. `test_run_rejects_custom_mode_without_allowed_tools`
   - Fails fast with `RunnerError(code="CLI_ERROR")` before subprocess invocation.

7. `test_run_adds_resume_and_system_prompt_flags`
   - Verifies `--resume` and `--append-system-prompt` are passed when provided.

8. `test_run_defaults_cwd_from_repo_path`
   - Verifies `cwd` defaults to `REPO_PATH` when explicit cwd is absent.

9. `test_run_returns_timeout_error`
   - A subprocess timeout becomes `RunnerError(code="TIMEOUT")`.

10. `test_run_wraps_non_zero_exit_with_stderr`
    - A non-zero subprocess exit becomes `RunnerError(code="CLI_ERROR")` and includes stderr.

11. `test_run_raises_max_turns_with_partial_result`
    - Parsed `num_turns >= max_turns` raises `RunnerError(code="MAX_TURNS")` with `partial_result`.

12. `test_run_raises_parse_error_when_stdout_is_not_json`
    - Non-JSON stdout is rejected even if the process exits cleanly.

## Mocking Strategy

- Mock `subprocess.run` only.
- Use a fake completed-process object for success and non-zero exit cases.
- Use `subprocess.TimeoutExpired` for timeout behavior.

