from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from tech_dev_agents.claude_runner import (
    CostInfo,
    RunOptions,
    RunResult,
    RunnerError,
    parse_output,
    run,
)


def test_parse_output_maps_json_to_typed_result() -> None:
    raw = (
        '{"result":"done","session_id":"sess-123","num_turns":3,"cost":'
        '{"input_tokens":1240,"output_tokens":380,"total_cost":0.0187}}'
    )

    result = parse_output(raw)

    assert result == RunResult(
        result="done",
        session_id="sess-123",
        num_turns=3,
        cost=CostInfo(input_tokens=1240, output_tokens=380, total_cost=0.0187),
    )


def test_parse_output_rejects_invalid_json() -> None:
    with pytest.raises(RunnerError) as excinfo:
        parse_output("not-json")

    assert excinfo.value.code == "PARSE_ERROR"


def test_run_builds_expected_claude_args_for_read_only(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(cmd, *, cwd, timeout, capture_output, text, env, check):  # noqa: ANN001
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        captured["timeout"] = timeout
        captured["capture_output"] = capture_output
        captured["text"] = text
        captured["env"] = env
        captured["check"] = check
        return SimpleNamespace(
            returncode=0,
            stdout='{"result":"ok","session_id":"sess-1","num_turns":2,"cost":{"input_tokens":1,"output_tokens":2,"total_cost":0.03}}',
            stderr="",
        )

    monkeypatch.setenv("REPO_PATH", "/repo")
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run("hello")

    assert result.result == "ok"
    assert captured["cmd"] == [
        "claude",
        "-p",
        "hello",
        "--bare",
        "--output-format",
        "json",
        "--max-turns",
        "5",
        "--allowedTools",
        "Read,Glob,Grep",
    ]
    assert captured["cwd"] == "/repo"
    assert captured["timeout"] == 120
    assert captured["capture_output"] is True
    assert captured["text"] is True


def test_run_uses_write_tool_set(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(cmd, *, cwd, timeout, capture_output, text, env, check):  # noqa: ANN001
        captured["cmd"] = cmd
        return SimpleNamespace(
            returncode=0,
            stdout='{"result":"ok","session_id":null,"num_turns":1,"cost":{"input_tokens":0,"output_tokens":0,"total_cost":0}}',
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    run("hello", RunOptions(mode="write"))

    assert "Read,Write,Edit,Bash(git diff *),Bash(git status *),Bash(git add *),Bash(git commit *)" in captured["cmd"]


def test_run_uses_custom_allowed_tools_when_provided(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(cmd, *, cwd, timeout, capture_output, text, env, check):  # noqa: ANN001
        captured["cmd"] = cmd
        return SimpleNamespace(
            returncode=0,
            stdout='{"result":"ok","session_id":null,"num_turns":1,"cost":{"input_tokens":0,"output_tokens":0,"total_cost":0}}',
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    run("hello", RunOptions(mode="custom", allowed_tools="Read,Write"))

    assert "--allowedTools" in captured["cmd"]
    assert "Read,Write" in captured["cmd"]


def test_run_rejects_custom_mode_without_allowed_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    def fake_run(*args, **kwargs):  # noqa: ANN001
        nonlocal called
        called = True
        raise AssertionError("subprocess.run should not be called")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(RunnerError) as excinfo:
        run("hello", RunOptions(mode="custom"))

    assert excinfo.value.code == "CLI_ERROR"
    assert called is False


def test_run_adds_resume_and_system_prompt_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(cmd, *, cwd, timeout, capture_output, text, env, check):  # noqa: ANN001
        captured["cmd"] = cmd
        return SimpleNamespace(
            returncode=0,
            stdout='{"result":"ok","session_id":"sess-2","num_turns":2,"cost":{"input_tokens":0,"output_tokens":0,"total_cost":0}}',
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    run(
        "hello",
        RunOptions(session_id="sess-1", system_prompt="be concise"),
    )

    assert "--resume" in captured["cmd"]
    assert "sess-1" in captured["cmd"]
    assert "--append-system-prompt" in captured["cmd"]
    assert "be concise" in captured["cmd"]


def test_run_defaults_cwd_from_repo_path(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(cmd, *, cwd, timeout, capture_output, text, env, check):  # noqa: ANN001
        captured["cwd"] = cwd
        return SimpleNamespace(
            returncode=0,
            stdout='{"result":"ok","session_id":null,"num_turns":1,"cost":{"input_tokens":0,"output_tokens":0,"total_cost":0}}',
            stderr="",
        )

    monkeypatch.setenv("REPO_PATH", "/repo-from-env")
    monkeypatch.setattr(subprocess, "run", fake_run)

    run("hello")

    assert captured["cwd"] == "/repo-from-env"


def test_run_returns_timeout_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*args, **kwargs):  # noqa: ANN001
        raise subprocess.TimeoutExpired(cmd=["claude"], timeout=120, output="partial", stderr="timed out")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(RunnerError) as excinfo:
        run("hello")

    assert excinfo.value.code == "TIMEOUT"


def test_run_wraps_non_zero_exit_with_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(cmd, *, cwd, timeout, capture_output, text, env, check):  # noqa: ANN001
        return SimpleNamespace(returncode=1, stdout="", stderr="boom")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(RunnerError) as excinfo:
        run("hello")

    assert excinfo.value.code == "CLI_ERROR"
    assert excinfo.value.stderr == "boom"


def test_run_raises_max_turns_with_partial_result(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(cmd, *, cwd, timeout, capture_output, text, env, check):  # noqa: ANN001
        return SimpleNamespace(
            returncode=0,
            stdout='{"result":"partial","session_id":"sess-3","num_turns":5,"cost":{"input_tokens":7,"output_tokens":8,"total_cost":0.09}}',
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(RunnerError) as excinfo:
        run("hello", RunOptions(max_turns=5))

    assert excinfo.value.code == "MAX_TURNS"
    assert excinfo.value.partial_result == RunResult(
        result="partial",
        session_id="sess-3",
        num_turns=5,
        cost=CostInfo(input_tokens=7, output_tokens=8, total_cost=0.09),
    )


def test_run_raises_parse_error_when_stdout_is_not_json(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(cmd, *, cwd, timeout, capture_output, text, env, check):  # noqa: ANN001
        return SimpleNamespace(returncode=0, stdout="not-json", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(RunnerError) as excinfo:
        run("hello")

    assert excinfo.value.code == "PARSE_ERROR"
