from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any

import yaml


class PersonaError(Exception):
    """Base exception for persona loader errors."""


class PersonaParseError(PersonaError):
    """Raised when markdown frontmatter cannot be parsed."""


class PersonaValidationError(PersonaError):
    """Raised when persona data fails schema validation."""


class PersonaProfileError(PersonaError):
    """Raised when a requested tool profile does not exist."""


class PersonaVariableError(PersonaError):
    """Raised when a template variable is missing from the render context."""


FRONTMATTER_DELIMITER = "---"
VARIABLE_PATTERN = re.compile(r"\{\{(\w+)\}\}")
NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
PROFILE_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")
VARIABLE_NAME_PATTERN = re.compile(r"^\w+$")
KNOWN_TOOLS = {
    "Read",
    "Write",
    "Edit",
    "Glob",
    "Grep",
    "MultiEdit",
    "Task",
    "WebSearch",
    "WebFetch",
    "NotebookRead",
    "NotebookEdit",
    "TodoWrite",
}
MAX_TURNS_LOWER_BOUND = 1
MAX_TURNS_UPPER_BOUND = 200
SCHEMA_VERSION = "1"


def _error(path: Path, field: str, message: str) -> str:
    return f"{path}: {field}: {message}"


def _parse_yaml_value(path: Path, yaml_text: str) -> dict[str, Any]:
    try:
        loaded = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:  # pragma: no cover - exercised in tests
        line_hint = ""
        problem_mark = getattr(exc, "problem_mark", None)
        if problem_mark is not None:
            line_hint = f" at line {problem_mark.line + 1}"
        raise PersonaParseError(
            f"{path}: YAML frontmatter parse error{line_hint}: {exc}"
        ) from exc

    if not isinstance(loaded, dict):
        raise PersonaParseError(
            _error(path, "frontmatter", f"YAML frontmatter must be a mapping, got {type(loaded).__name__}")
        )
    return loaded


def _split_frontmatter(content: str, path: Path) -> tuple[str, str]:
    lines = content.splitlines()
    if not lines or lines[0].strip() != FRONTMATTER_DELIMITER:
        raise PersonaParseError(
            f"{path}: File must begin with YAML frontmatter delimiter '---'"
        )

    closing_index: int | None = None
    for index in range(1, len(lines)):
        if lines[index].strip() == FRONTMATTER_DELIMITER:
            closing_index = index
            break

    if closing_index is None:
        raise PersonaParseError(f"{path}: YAML frontmatter closing delimiter '---' not found")

    yaml_text = "\n".join(lines[1:closing_index]).strip()
    body = "\n".join(lines[closing_index + 1 :]).lstrip("\n")
    return yaml_text, body


def _validate_allowed_tool(tool: Any, path: Path, field: str) -> str:
    if not isinstance(tool, str) or not tool.strip():
        raise PersonaValidationError(
            _error(path, field, "'allowed_tools' must be a non-empty list of strings.")
        )

    tool = tool.strip()

    if tool.startswith("Bash("):
        if not tool.endswith(")"):
            raise PersonaValidationError(
                _error(
                    path,
                    field,
                    f"Unbalanced parentheses in tool name: '{tool}'",
                )
            )
        inner = tool[len("Bash(") : -1].strip()
        if not inner:
            raise PersonaValidationError(
                _error(
                    path,
                    field,
                    f"Unbalanced parentheses in tool name: '{tool}'",
                )
            )
        if inner == "*":
            raise PersonaValidationError(
                _error(path, field, "Bash(*) is not supported; use a scoped command pattern instead.")
            )
        if tool.count("(") != tool.count(")"):
            raise PersonaValidationError(
                _error(
                    path,
                    field,
                    f"Unbalanced parentheses in tool name: '{tool}'",
                )
            )
        return tool

    if tool not in KNOWN_TOOLS:
        raise PersonaValidationError(
            _error(path, field, f"Unknown tool name: '{tool}'.")
        )
    return tool


def _validate_tool_list(raw_tools: Any, path: Path, field: str) -> list[str]:
    if not isinstance(raw_tools, list) or not raw_tools:
        raise PersonaValidationError(
            _error(path, field, "'allowed_tools' must be a non-empty list of strings.")
        )
    return [_validate_allowed_tool(tool, path, field) for tool in raw_tools]


def _validate_profile_map(raw_profiles: Any, path: Path) -> dict[str, list[str]]:
    if raw_profiles is None:
        return {}
    if not isinstance(raw_profiles, dict):
        raise PersonaValidationError(
            _error(path, "tool_profiles", "'tool_profiles' must be a mapping of profile names to tool lists.")
        )

    validated: dict[str, list[str]] = {}
    for profile_name, tools in raw_profiles.items():
        if not isinstance(profile_name, str) or not PROFILE_NAME_PATTERN.fullmatch(profile_name):
            raise PersonaValidationError(
                _error(
                    path,
                    "tool_profiles",
                    f"Invalid tool profile name: '{profile_name}'. Must match kebab-case.",
                )
            )
        validated[profile_name] = _validate_tool_list(tools, path, "tool_profiles")
    return validated


def _validate_string_list(raw_values: Any, path: Path, field: str) -> list[str]:
    if raw_values is None:
        return []
    if not isinstance(raw_values, list):
        raise PersonaValidationError(
            _error(path, field, f"'{field}' must be a list of strings.")
        )
    values: list[str] = []
    for value in raw_values:
        if not isinstance(value, str) or not value.strip():
            raise PersonaValidationError(
                _error(path, field, f"'{field}' must contain only non-empty strings.")
            )
        values.append(value.strip())
    return values


def _validate_required_string(frontmatter: dict[str, Any], path: Path, field: str) -> str:
    value = frontmatter.get(field)
    if not isinstance(value, str) or not value.strip():
        raise PersonaValidationError(
            _error(path, field, f"Missing required field '{field}'.")
        )
    return value.strip()


def _extract_variables(template: str) -> list[str]:
    seen: set[str] = set()
    variables: list[str] = []
    for match in VARIABLE_PATTERN.finditer(template):
        name = match.group(1)
        if name not in seen:
            seen.add(name)
            variables.append(name)
    return variables


def _inject_variables(template: str, context: dict[str, str], path: Path) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in context:
            available = ", ".join(sorted(context))
            raise PersonaVariableError(
                f"{path}: Undefined variable '{{{{{key}}}}}' in persona template. Available variables: {available}"
            )
        return str(context[key])

    return VARIABLE_PATTERN.sub(replace, template)


def _render_rules(rules: list[str]) -> str:
    if not rules:
        return ""
    numbered = "\n".join(f"{index}. {rule}" for index, rule in enumerate(rules, start=1))
    return f"\n\n## Rules\n\n{numbered}"


@dataclass(frozen=True)
class PersonaRenderResult:
    append_system_prompt: str
    allowed_tools_csv: str
    max_turns: int

    def to_cli_args(self) -> list[str]:
        return [
            "--append-system-prompt",
            self.append_system_prompt,
            "--allowedTools",
            self.allowed_tools_csv,
            "--max-turns",
            str(self.max_turns),
        ]


@dataclass(frozen=True)
class Persona:
    schema_version: str
    name: str
    model: str
    max_turns: int
    allowed_tools: list[str]
    version: str = "1.0.0"
    description: str = ""
    tool_profiles: dict[str, list[str]] = field(default_factory=dict)
    behavioral_rules: list[str] = field(default_factory=list)
    variables: list[str] = field(default_factory=list)
    system_prompt_template: str = ""
    path: Path | None = None

    @property
    def template_variables(self) -> list[str]:
        return _extract_variables(self.system_prompt_template)

    def render(
        self,
        context: dict[str, str],
        profile: str | None = None,
        max_turns_override: int | None = None,
    ) -> PersonaRenderResult:
        resolved_context = {"agent_name": self.name, **context}
        rendered_prompt = _inject_variables(
            self.system_prompt_template, resolved_context, self.path or Path(self.name)
        )
        rendered_prompt += _render_rules(self.behavioral_rules)
        allowed_tools = self._resolve_tools(profile)
        max_turns = self._resolve_max_turns(max_turns_override)
        return PersonaRenderResult(
            append_system_prompt=rendered_prompt,
            allowed_tools_csv=",".join(allowed_tools),
            max_turns=max_turns,
        )

    def to_cli_args(
        self,
        context: dict[str, str],
        profile: str | None = None,
        max_turns_override: int | None = None,
    ) -> list[str]:
        return self.render(
            context,
            profile=profile,
            max_turns_override=max_turns_override,
        ).to_cli_args()

    def _resolve_tools(self, profile: str | None) -> list[str]:
        if profile is None:
            return self.allowed_tools
        if profile not in self.tool_profiles:
            available = ", ".join(sorted(self.tool_profiles))
            raise PersonaProfileError(
                f"{self.path or self.name}: Unknown tool profile '{profile}' for persona '{self.name}'. Available profiles: {available}"
            )
        return self.tool_profiles[profile]

    def _resolve_max_turns(self, max_turns_override: int | None) -> int:
        if max_turns_override is None:
            return self.max_turns
        if not isinstance(max_turns_override, int) or isinstance(max_turns_override, bool):
            raise PersonaValidationError(
                _error(self.path or Path(self.name), "max_turns_override", "Must be an integer.")
            )
        if max_turns_override < MAX_TURNS_LOWER_BOUND:
            raise PersonaValidationError(
                _error(
                    self.path or Path(self.name),
                    "max_turns_override",
                    f"Must be between {MAX_TURNS_LOWER_BOUND}-{self.max_turns}.",
                )
            )
        if max_turns_override > self.max_turns:
            raise PersonaValidationError(
                _error(
                    self.path or Path(self.name),
                    "max_turns_override",
                    f"{max_turns_override} exceeds persona ceiling ({self.max_turns}).",
                )
            )
        return max_turns_override


class PersonaLoader:
    """Load and validate persona markdown files with YAML frontmatter."""

    def load(self, persona_path: str | Path) -> Persona:
        path = Path(persona_path)
        if not path.exists():
            raise FileNotFoundError(f"{path}: file does not exist")

        content = path.read_text(encoding="utf-8")
        yaml_text, body = _split_frontmatter(content, path)
        frontmatter = _parse_yaml_value(path, yaml_text)
        return self._build_persona(path, frontmatter, body)

    def _build_persona(self, path: Path, frontmatter: dict[str, Any], body: str) -> Persona:
        schema_version = str(frontmatter.get("schema_version", "")).strip()
        if schema_version != SCHEMA_VERSION:
            raise PersonaValidationError(
                _error(
                    path,
                    "schema_version",
                    f"Invalid value for 'schema_version': {schema_version!r}. Must equal '{SCHEMA_VERSION}'.",
                )
            )

        name = _validate_required_string(frontmatter, path, "name")
        if not NAME_PATTERN.fullmatch(name):
            raise PersonaValidationError(
                _error(
                    path,
                    "name",
                    f"Invalid value for 'name': {name!r}. Must match pattern ^[a-z][a-z0-9-]{{0,63}}$ (kebab-case, starts with letter, max 64 chars).",
                )
            )

        model = _validate_required_string(frontmatter, path, "model")
        if model not in {"opus", "sonnet", "haiku"}:
            raise PersonaValidationError(
                _error(
                    path,
                    "model",
                    f"Invalid value for 'model': {model!r}. Must be one of: opus, sonnet, haiku",
                )
            )

        raw_max_turns = frontmatter.get("max_turns")
        if not isinstance(raw_max_turns, int) or isinstance(raw_max_turns, bool):
            raise PersonaValidationError(
                _error(
                    path,
                    "max_turns",
                    f"Invalid value for 'max_turns': {raw_max_turns!r}. Must be a positive integer between {MAX_TURNS_LOWER_BOUND}-{MAX_TURNS_UPPER_BOUND}",
                )
            )
        if not (MAX_TURNS_LOWER_BOUND <= raw_max_turns <= MAX_TURNS_UPPER_BOUND):
            raise PersonaValidationError(
                _error(
                    path,
                    "max_turns",
                    f"Invalid value for 'max_turns': {raw_max_turns!r}. Must be a positive integer between {MAX_TURNS_LOWER_BOUND}-{MAX_TURNS_UPPER_BOUND}",
                )
            )

        allowed_tools = _validate_tool_list(frontmatter.get("allowed_tools"), path, "allowed_tools")
        tool_profiles = _validate_profile_map(frontmatter.get("tool_profiles"), path)
        behavioral_rules = _validate_string_list(frontmatter.get("behavioral_rules"), path, "behavioral_rules")
        variables = _validate_string_list(frontmatter.get("variables"), path, "variables")

        version = frontmatter.get("version", "1.0.0")
        if version is None:
            version = "1.0.0"
        if not isinstance(version, str):
            version = str(version)

        description = frontmatter.get("description", "")
        if description is None:
            description = ""
        if not isinstance(description, str):
            description = str(description)

        if body is None:
            body = ""

        return Persona(
            schema_version=schema_version,
            name=name,
            model=model,
            max_turns=raw_max_turns,
            allowed_tools=allowed_tools,
            version=version,
            description=description,
            tool_profiles=tool_profiles,
            behavioral_rules=behavioral_rules,
            variables=variables,
            system_prompt_template=body,
            path=path,
        )
