from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from tech_dev_agents.persona import (
    PersonaLoader,
    PersonaParseError,
    PersonaProfileError,
    PersonaValidationError,
    PersonaVariableError,
)


PERSONA_FILE = Path("personas/dev-agent-v1.md")


def write_persona(tmp_path: Path, content: str, filename: str = "persona.md") -> Path:
    persona_path = tmp_path / filename
    persona_path.write_text(dedent(content).strip() + "\n", encoding="utf-8")
    return persona_path


def sample_persona_markdown() -> str:
    return """
    ---
    schema_version: "1"
    name: dev-agent-v1
    version: 1.0.0
    description: Primary autonomous development agent
    model: sonnet
    max_turns: 25
    allowed_tools:
      - Read
      - Glob
      - Grep
    tool_profiles:
      write:
        - Read
        - Write
        - Edit
        - Bash(git diff *)
        - Bash(git status)
    behavioral_rules:
      - Never push to main
      - Always create a feature branch
    variables:
      - repo_name
      - repo_path
      - story_id
      - phase
      - branch_name
    ---

    # Dev Agent v1

    You are a senior software engineer working on {{repo_name}}.
    Story: {{story_id}}
    Phase: {{phase}}
    Repo: {{repo_path}}
    Branch: {{branch_name}}
    Agent: {{agent_name}}
    """


def sample_context() -> dict[str, str]:
    return {
        "repo_name": "tech-dev-agents",
        "repo_path": "/mnt/c/Projects/tech-dev-agents",
        "story_id": "STORY-005",
        "phase": "8",
        "branch_name": "feature/story-005-persona-system",
    }


def test_load_example_persona_file_from_repo():
    loader = PersonaLoader()

    persona = loader.load(PERSONA_FILE)

    assert persona.name == "dev-agent-v1"
    assert persona.schema_version == "1"
    assert persona.model == "sonnet"
    assert persona.allowed_tools == ["Read", "Glob", "Grep"]


def test_load_persona_parses_frontmatter_and_body(tmp_path: Path):
    loader = PersonaLoader()
    persona_path = write_persona(tmp_path, sample_persona_markdown())

    persona = loader.load(persona_path)

    assert persona.name == "dev-agent-v1"
    assert persona.version == "1.0.0"
    assert persona.description == "Primary autonomous development agent"
    assert persona.max_turns == 25
    assert persona.allowed_tools == ["Read", "Glob", "Grep"]
    assert persona.tool_profiles["write"][0] == "Read"
    assert "You are a senior software engineer" in persona.system_prompt_template
    assert "{{repo_name}}" in persona.system_prompt_template


def test_load_persona_renders_cli_flags_with_variable_injection(tmp_path: Path):
    loader = PersonaLoader()
    persona_path = write_persona(tmp_path, sample_persona_markdown())
    persona = loader.load(persona_path)

    rendered = persona.render(sample_context())

    assert rendered.append_system_prompt.startswith("# Dev Agent v1")
    assert "tech-dev-agents" in rendered.append_system_prompt
    assert "STORY-005" in rendered.append_system_prompt
    assert "## Rules" in rendered.append_system_prompt
    assert rendered.allowed_tools_csv == "Read,Glob,Grep"
    assert rendered.max_turns == 25
    assert rendered.to_cli_args() == [
        "--append-system-prompt",
        rendered.append_system_prompt,
        "--allowedTools",
        "Read,Glob,Grep",
        "--max-turns",
        "25",
    ]


def test_load_persona_changes_rendered_prompt_when_context_changes(tmp_path: Path):
    loader = PersonaLoader()
    persona_path = write_persona(tmp_path, sample_persona_markdown())
    persona = loader.load(persona_path)

    rendered_a = persona.render({**sample_context(), "repo_name": "alpha"})
    rendered_b = persona.render({**sample_context(), "repo_name": "beta"})

    assert rendered_a.append_system_prompt != rendered_b.append_system_prompt
    assert "alpha" in rendered_a.append_system_prompt
    assert "beta" in rendered_b.append_system_prompt


def test_to_cli_args_uses_requested_tool_profile(tmp_path: Path):
    loader = PersonaLoader()
    persona_path = write_persona(tmp_path, sample_persona_markdown())
    persona = loader.load(persona_path)

    args = persona.to_cli_args(sample_context(), profile="write", max_turns_override=10)

    assert args == [
        "--append-system-prompt",
        args[1],
        "--allowedTools",
        "Read,Write,Edit,Bash(git diff *),Bash(git status)",
        "--max-turns",
        "10",
    ]


def test_to_cli_args_rejects_max_turns_override_above_ceiling(tmp_path: Path):
    loader = PersonaLoader()
    persona_path = write_persona(tmp_path, sample_persona_markdown())
    persona = loader.load(persona_path)

    with pytest.raises(PersonaValidationError) as exc_info:
        persona.to_cli_args(sample_context(), max_turns_override=99)

    message = str(exc_info.value)
    assert "max_turns_override" in message
    assert "99" in message
    assert "25" in message


def test_to_cli_args_raises_variable_error_for_missing_context_value(tmp_path: Path):
    loader = PersonaLoader()
    persona_path = write_persona(tmp_path, sample_persona_markdown())
    persona = loader.load(persona_path)

    context = sample_context()
    context.pop("branch_name")

    with pytest.raises(PersonaVariableError) as exc_info:
        persona.render(context)

    message = str(exc_info.value)
    assert str(persona.path) in message
    assert "branch_name" in message


def test_load_persona_raises_parse_error_for_malformed_yaml_frontmatter(tmp_path: Path):
    loader = PersonaLoader()
    persona_path = write_persona(
        tmp_path,
        """
        ---
        schema_version: "1"
        name dev-agent-v1
        model: sonnet
        max_turns: 25
        allowed_tools:
          - Read
        ---

        Body text.
        """,
        filename="broken.md",
    )

    with pytest.raises(PersonaParseError) as exc_info:
        loader.load(persona_path)

    message = str(exc_info.value)
    assert str(persona_path) in message
    assert "YAML" in message or "frontmatter" in message


def test_load_persona_raises_validation_error_for_missing_required_field(tmp_path: Path):
    loader = PersonaLoader()
    persona_path = write_persona(
        tmp_path,
        """
        ---
        schema_version: "1"
        model: sonnet
        max_turns: 25
        allowed_tools:
          - Read
        ---

        Body text.
        """,
        filename="missing-name.md",
    )

    with pytest.raises(PersonaValidationError) as exc_info:
        loader.load(persona_path)

    message = str(exc_info.value)
    assert str(persona_path) in message
    assert "name" in message
    assert "required" in message.lower()


@pytest.mark.parametrize(
    ("field_name", "field_value", "expected_fragment"),
    [
        ("model", "gpt4", "opus, sonnet, haiku"),
        ("max_turns", 0, "1-200"),
    ],
)
def test_load_persona_raises_validation_error_for_invalid_model_and_max_turns(
    tmp_path: Path,
    field_name: str,
    field_value: object,
    expected_fragment: str,
):
    loader = PersonaLoader()
    persona_path = write_persona(
        tmp_path,
        f"""
        ---
        schema_version: "1"
        name: dev-agent-v1
        model: sonnet
        max_turns: 25
        allowed_tools:
          - Read
        ---

        Body text.
        """,
        filename=f"invalid-{field_name}.md",
    )

    content = persona_path.read_text(encoding="utf-8")
    persona_path.write_text(
        content.replace(
            "model: sonnet" if field_name == "model" else "max_turns: 25",
            f"{field_name}: {field_value}",
        ),
        encoding="utf-8",
    )

    with pytest.raises(PersonaValidationError) as exc_info:
        loader.load(persona_path)

    message = str(exc_info.value)
    assert str(persona_path) in message
    assert field_name in message
    assert expected_fragment in message


def test_load_persona_raises_validation_error_for_unknown_tool_name(tmp_path: Path):
    loader = PersonaLoader()
    persona_path = write_persona(
        tmp_path,
        """
        ---
        schema_version: "1"
        name: dev-agent-v1
        model: sonnet
        max_turns: 25
        allowed_tools:
          - Read
          - Teleport
        ---

        Body text.
        """,
        filename="invalid-tool.md",
    )

    with pytest.raises(PersonaValidationError) as exc_info:
        loader.load(persona_path)

    message = str(exc_info.value)
    assert str(persona_path) in message
    assert "Teleport" in message
    assert "allowed_tools" in message


def test_load_persona_raises_validation_error_for_invalid_tool_profile_definition(tmp_path: Path):
    loader = PersonaLoader()
    persona_path = write_persona(
        tmp_path,
        """
        ---
        schema_version: "1"
        name: dev-agent-v1
        model: sonnet
        max_turns: 25
        allowed_tools:
          - Read
        tool_profiles:
          Admin:
            - Read
          write:
            - Read
            - Bash(git *
        ---

        Body text.
        """,
        filename="invalid-profile.md",
    )

    with pytest.raises(PersonaValidationError) as exc_info:
        loader.load(persona_path)

    message = str(exc_info.value)
    assert str(persona_path) in message
    assert "tool_profiles" in message


def test_to_cli_args_rejects_unknown_tool_profile(tmp_path: Path):
    loader = PersonaLoader()
    persona_path = write_persona(tmp_path, sample_persona_markdown())
    persona = loader.load(persona_path)

    with pytest.raises(PersonaProfileError) as exc_info:
        persona.to_cli_args(sample_context(), profile="admin")

    message = str(exc_info.value)
    assert "admin" in message
    assert "Available profiles" in message

