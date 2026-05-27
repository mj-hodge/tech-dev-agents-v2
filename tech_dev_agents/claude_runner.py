from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from typing import Literal


RunMode = Literal["read-only", "write", "custom"]


@dataclass(frozen=True, slots=True)
class CostInfo:
    input_tokens: int
    output_tokens: int
    total_cost: float


@dataclass(frozen=True, slots=True)
class RunResult:
    result: str
    session_id: str | None
    num_turns: int
    cost: CostInfo


@dataclass(frozen=True, slots=True)
class RunOptions:
    mode: RunMode = "read-only"
    allowed_tools: str | None = None
    session_id: str | None = None
    max_turns: int | None = None
    timeout_seconds: int = 120
    cwd: str | None = None
    system_prompt: str | None = None


class RunnerError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        stderr: str | None = None,
        partial_result: RunResult | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.stderr = stderr
        self.partial_result = partial_result


READ_ONLY_ALLOWED_TOOLS = "Read,Glob,Grep"
WRITE_ALLOWED_TOOLS = "Read,Write,Edit,Bash(git diff *),Bash(git status *),Bash(git add *),Bash(git commit *)"


def parse_output(stdout: str) -> RunResult:
    try:
        raw = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RunnerError("PARSE_ERROR", f"stdout is not valid JSON: {stdout[:200]}") from exc

    if not isinstance(raw, dict):
        raise RunnerError("PARSE_ERROR", f"Expected JSON object, got: {type(raw).__name__}")

    result = raw.get("result")
    if not isinstance(result, str):
        raise RunnerError(
            "PARSE_ERROR",
            f'Missing or non-string "result" field: {stdout[:200]}',
        )

    cost = raw.get("cost")
    if not isinstance(cost, dict):
        cost = {}

    return RunResult(
        result=result,
        session_id=raw.get("session_id") if isinstance(raw.get("session_id"), str) else None,
        num_turns=raw.get("num_turns") if isinstance(raw.get("num_turns"), int) else 0,
        cost=CostInfo(
            input_tokens=cost.get("input_tokens") if isinstance(cost.get("input_tokens"), int) else 0,
            output_tokens=cost.get("output_tokens") if isinstance(cost.get("output_tokens"), int) else 0,
            total_cost=cost.get("total_cost") if isinstance(cost.get("total_cost"), (int, float)) else 0,
        ),
    )


def run(prompt: str, options: RunOptions | None = None) -> RunResult:
    opts = _resolve_options(options)
    args = _build_args(prompt, opts)

    try:
        completed = subprocess.run(
            args,
            cwd=opts.cwd,
            timeout=opts.timeout_seconds,
            capture_output=True,
            text=True,
            env=os.environ.copy(),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stderr = _coerce_text(exc.stderr)
        raise RunnerError(
            "TIMEOUT",
            f"Claude CLI timed out after {opts.timeout_seconds}s",
            stderr=stderr,
        ) from exc

    if completed.returncode != 0:
        raise RunnerError(
            "CLI_ERROR",
            f"Claude CLI exited with code {completed.returncode}",
            stderr=_coerce_text(completed.stderr),
        )

    parsed = parse_output(_coerce_text(completed.stdout))

    if parsed.num_turns >= opts.max_turns:
        raise RunnerError(
            "MAX_TURNS",
            f"Reached {parsed.num_turns} turns (limit: {opts.max_turns})",
            partial_result=parsed,
        )

    return parsed


def _resolve_options(options: RunOptions | None) -> RunOptions:
    opts = options or RunOptions()
    if opts.mode == "custom" and not opts.allowed_tools:
        raise RunnerError("CLI_ERROR", 'allowed_tools is required when mode is "custom"')

    max_turns = opts.max_turns
    if max_turns is None:
        max_turns = 5 if opts.mode == "read-only" else 10

    cwd = opts.cwd or os.environ.get("REPO_PATH") or os.getcwd()

    return RunOptions(
        mode=opts.mode,
        allowed_tools=opts.allowed_tools,
        session_id=opts.session_id,
        max_turns=max_turns,
        timeout_seconds=opts.timeout_seconds,
        cwd=cwd,
        system_prompt=opts.system_prompt,
    )


def _build_args(prompt: str, opts: RunOptions) -> list[str]:
    args = [
        "claude",
        "-p",
        prompt,
        "--bare",
        "--output-format",
        "json",
        "--max-turns",
        str(opts.max_turns),
        "--allowedTools",
        _resolve_allowed_tools(opts),
    ]

    if opts.session_id:
        args.extend(["--resume", opts.session_id])

    if opts.system_prompt:
        args.extend(["--append-system-prompt", opts.system_prompt])

    return args


def _resolve_allowed_tools(opts: RunOptions) -> str:
    if opts.mode == "write":
        return WRITE_ALLOWED_TOOLS
    if opts.mode == "custom":
        return opts.allowed_tools or ""
    return READ_ONLY_ALLOWED_TOOLS


def _coerce_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode()
    return str(value)


__all__ = [
    "CostInfo",
    "RunMode",
    "RunOptions",
    "RunResult",
    "RunnerError",
    "parse_output",
    "run",
]
