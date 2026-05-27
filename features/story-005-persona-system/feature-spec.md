# Feature Spec: Persona System (STORY-005)

> Phase 6 -- Design
> Date: 2026-03-26
> Story: STORY-005
> Epic: Autonomous Dev Agent (v1)
> Scope: Medium

---

## 1. Persona Config Schema

Persona files use markdown with YAML frontmatter, matching the `.sdlc/agents/*.md` convention already established in this repo. Structured config lives in the YAML frontmatter; the system prompt template occupies the markdown body.

### 1.1 YAML Frontmatter Fields

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `schema_version` | `str` | Yes | -- | Schema version for forward compatibility. Must be `"1"` for v1. |
| `name` | `str` | Yes | -- | Unique persona identifier. Kebab-case, e.g., `dev-agent-v1`. |
| `version` | `str` | No | `"1.0.0"` | Persona content version (semver). Informational only; not enforced by the loader. |
| `description` | `str` | No | `""` | One-line summary of the persona's purpose. |
| `model` | `str` | Yes | -- | Preferred model tier. One of: `opus`, `sonnet`, `haiku`. |
| `max_turns` | `int` | Yes | -- | Turn ceiling for cost control. The orchestrator may override downward but never upward. |
| `allowed_tools` | `list[str]` | Yes | -- | Default tool permission list. Each entry matches Claude Code `--allowedTools` syntax exactly. |
| `tool_profiles` | `dict[str, list[str]]` | No | `{}` | Named tool permission profiles. Keys are profile names (e.g., `write`, `read_only`). Values are tool lists. When a profile is selected at invocation, it replaces `allowed_tools`. |
| `behavioral_rules` | `list[str]` | No | `[]` | Behavioral constraints appended to the system prompt as a numbered list. Each entry is one rule in plain English. |
| `variables` | `list[str]` | No | `[]` | Declared variable names that this persona's template expects. Used for documentation and optional strict validation. If non-empty, the loader warns on undeclared variables found in the template. |

### 1.2 Markdown Body

The markdown body is the system prompt template. It is passed verbatim (after variable injection) to Claude Code via `--append-system-prompt`.

The body may contain:
- Standard markdown formatting (headings, lists, code blocks, bold/italic)
- Variable placeholders using `{{variable_name}}` syntax
- Behavioral rules are automatically appended after the body content by the loader (the author does not need to manually include them)

### 1.3 Required vs Optional Fields

**Required (loader raises `PersonaValidationError` if missing):**
- `schema_version`
- `name`
- `model`
- `max_turns`
- `allowed_tools`

**Optional (loader applies defaults):**
- `version` (default: `"1.0.0"`)
- `description` (default: `""`)
- `tool_profiles` (default: `{}`)
- `behavioral_rules` (default: `[]`)
- `variables` (default: `[]`)

### 1.4 Field Validation Rules

| Field | Validation |
|-------|-----------|
| `schema_version` | Must equal `"1"`. Any other value raises an error with migration guidance. |
| `name` | Must match `^[a-z][a-z0-9-]*$` (kebab-case, starts with letter). Max 64 chars. |
| `model` | Must be one of `opus`, `sonnet`, `haiku`. |
| `max_turns` | Must be a positive integer, 1-200. |
| `allowed_tools` | Non-empty list. Each entry must be a non-empty string. Scoped tools (e.g., `Bash(git *)`) must have balanced parentheses. |
| `tool_profiles` | If present, each key must be a non-empty kebab-case string. Each value must be a non-empty list of tool strings with the same validation as `allowed_tools`. |
| `behavioral_rules` | Each entry must be a non-empty string. |
| `variables` | Each entry must match `^\w+$` (alphanumeric + underscore). |

### 1.5 Complete Example Persona File

```markdown
---
schema_version: "1"
name: dev-agent-v1
version: "1.0.0"
description: Primary autonomous development agent for SDLC execution
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
    - Glob
    - Grep
    - Bash(git diff *)
    - Bash(git status)
    - Bash(git add *)
    - Bash(git commit *)
    - Bash(git checkout -b *)
    - Bash(git push *)
    - Bash(gh pr create *)
  read_only:
    - Read
    - Glob
    - Grep
    - Bash(git log *)
    - Bash(git diff *)
    - Bash(git status)
behavioral_rules:
  - Never push to the main or master branch
  - Always create a feature branch before making changes
  - Commit after each logical unit of work with a descriptive message
  - Never modify files outside the assigned repository
  - Stop and report if you encounter a failing test you did not expect
  - Follow the SDLC phase path for the assigned scope classification
variables:
  - repo_name
  - repo_path
  - story_id
  - phase
  - branch_name
---

# Dev Agent v1

You are a senior software engineer working autonomously on the **{{repo_name}}** repository.

## Current Assignment

- **Story:** {{story_id}}
- **Phase:** {{phase}}
- **Branch:** {{branch_name}}
- **Repo path:** {{repo_path}}

## How You Work

You follow the SDLC process defined in the repository's `.sdlc/` directory. For each phase, you:

1. Read the agent persona for that phase from `.sdlc/agents/`
2. Produce the required deliverables in `features/<story-folder>/`
3. Update tracking documents (`.project`, `backlog.md`, `development-tasks.md`)
4. Commit your work to the feature branch

## Quality Standards

- Read existing code and tests before making changes
- Write tests before implementation (TDD where applicable)
- Keep functions small and focused
- Use descriptive variable and function names
- Add comments only when the "why" is not obvious from the code
- Follow the repository's existing code style and conventions

## Communication

When you reach a phase gate or need approval, output a clear summary of:
- What you completed
- Key decisions made
- What comes next
- Any blockers or questions
```

---

## 2. Directory Structure

```
tech-dev-agents/
  personas/
    dev-agent-v1.md          # Primary dev agent (first persona, shipped with this story)
    README.md                # Brief directory purpose + schema reference
  tech_dev_agents/
    persona.py               # PersonaLoader class + Persona dataclass
  tests/
    test_persona.py          # Unit tests for loader, validation, variable injection
```

### Naming Convention

Persona files follow: `<role>-v<N>.md`

- `role`: Kebab-case description of the agent's function (e.g., `dev-agent`, `review-agent`, `ops-agent`)
- `v<N>`: Integer version suffix, allowing side-by-side evolution without breaking existing deployments
- Extension: `.md` (always)

Examples:
- `dev-agent-v1.md` -- primary development agent
- `dev-agent-v2.md` -- next iteration (future)
- `review-agent-v1.md` -- PR review specialist (future)

### One File Per Persona

Each persona is fully self-contained in a single file. The frontmatter + body format eliminates synchronization problems that arise from splitting config across multiple files.

---

## 3. Variable Injection

### 3.1 Supported Variables

These are the built-in variables the orchestrator (STORY-006) provides at invocation time:

| Variable | Source | Example Value |
|----------|--------|---------------|
| `repo_name` | Target repository name | `my-web-app` |
| `repo_path` | Absolute path to cloned repo in container | `/workspace/my-web-app` |
| `story_id` | Story identifier from task tracker | `STORY-042` |
| `phase` | Current SDLC phase number | `8` |
| `branch_name` | Git branch for this work | `feature/story-042-auth-flow` |
| `agent_name` | Name field from the persona itself | `dev-agent-v1` |

### 3.2 Injection Regex Pattern

```python
VARIABLE_PATTERN = re.compile(r"\{\{(\w+)\}\}")
```

The injection function replaces every `{{variable_name}}` occurrence in the markdown body with the corresponding value from the context dictionary.

```python
def inject_variables(template: str, context: dict[str, str]) -> str:
    def replacer(match: re.Match) -> str:
        key = match.group(1)
        if key not in context:
            raise PersonaVariableError(
                f"Undefined variable '{{{{key}}}}' in persona template. "
                f"Available variables: {sorted(context.keys())}"
            )
        return str(context[key])
    return VARIABLE_PATTERN.sub(replacer, template)
```

Key properties:
- `\w+` restricts variable names to `[a-zA-Z0-9_]`, blocking injection via special characters
- `re.sub` processes left-to-right in a single pass -- no double-substitution risk even if a variable value contains `{{`
- Missing variables raise immediately with a clear error naming the variable and listing available ones

### 3.3 Error Behavior for Missing/Undefined Variables

| Scenario | Behavior |
|----------|----------|
| Variable in template not in context | `PersonaVariableError` with variable name + available variables |
| Variable in context not in template | Silently ignored (no error). Context may contain extra variables. |
| Variable value contains `{{` or `}}` | Injected as-is. No double-substitution due to single-pass regex. |
| Variable value contains newlines | Injected as-is. Newlines are valid in system prompts. |
| Variable value is empty string | Injected as empty string. This is valid (e.g., `branch_name` may be empty before branch creation). |

### 3.4 Reserved Variable Names

The following variable names are reserved for system use and must not be set by the caller:

| Reserved Name | Injected By | Value |
|---------------|-------------|-------|
| `agent_name` | Loader (from frontmatter `name` field) | e.g., `dev-agent-v1` |

The loader injects `agent_name` automatically from the persona's `name` field before processing caller-provided variables. If the caller also provides `agent_name`, the caller's value takes precedence (override allowed).

---

## 4. Persona Loader

### 4.1 Module Location

`tech_dev_agents/persona.py`

### 4.2 Dependencies

- `pyyaml` -- YAML frontmatter parsing (standard Python YAML library, no additional install in most environments)
- `re` -- Variable injection regex (stdlib)
- `dataclasses` -- Persona data structure (stdlib)
- `pathlib` -- File path handling (stdlib)

No template engine. No frontmatter-specific library. The frontmatter delimiter (`---`) is simple enough to split manually, avoiding a `python-frontmatter` dependency.

### 4.3 Public API

```python
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Persona:
    """Loaded and validated persona configuration."""

    # From frontmatter
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

    # From markdown body (raw template, before injection)
    system_prompt_template: str = ""

    def to_cli_args(
        self,
        context: dict[str, str],
        profile: str | None = None,
        max_turns_override: int | None = None,
    ) -> list[str]:
        """
        Produce Claude Code CLI argument list.

        Args:
            context: Variable values for template injection.
                     Keys are variable names, values are strings.
            profile: Optional tool profile name. If provided, uses
                     tool_profiles[profile] instead of allowed_tools.
                     Raises PersonaProfileError if profile not found.
            max_turns_override: Optional turn limit. Must be <= self.max_turns.
                                If None, uses persona's max_turns.

        Returns:
            List of CLI argument strings, e.g.:
            ["--append-system-prompt", "...", "--allowedTools", "Read,Write,Edit",
             "--max-turns", "25"]
        """
        ...

    def to_env(self, context: dict[str, str]) -> dict[str, str]:
        """
        Produce environment variable overrides (if any).

        Currently returns an empty dict. Reserved for future use where
        persona config needs to set environment variables (e.g., model
        selection environment variables for the Claude Code runner).

        Args:
            context: Variable values (same as to_cli_args).

        Returns:
            Dict of env var name -> value. Empty in v1.
        """
        ...


class PersonaLoader:
    """Loads and validates persona files from disk."""

    def load(self, persona_path: str | Path) -> Persona:
        """
        Parse a persona markdown file, validate all fields, and return
        a Persona instance.

        Args:
            persona_path: Absolute or relative path to a persona .md file.

        Returns:
            Validated Persona instance.

        Raises:
            FileNotFoundError: File does not exist.
            PersonaParseError: YAML frontmatter cannot be parsed.
            PersonaValidationError: Required field missing or field
                                    value fails validation.
        """
        ...
```

### 4.4 `to_cli_args` Output Mapping

Given a persona with this frontmatter and a context dict, `to_cli_args` produces the following CLI flags:

| Persona Source | CLI Flag | Format |
|----------------|----------|--------|
| Markdown body (after variable injection + behavioral rules appended) | `--append-system-prompt` | Single string. Behavioral rules appended as `\n\n## Rules\n\n1. Rule one\n2. Rule two\n...` |
| `allowed_tools` (or `tool_profiles[profile]` if profile specified) | `--allowedTools` | Comma-separated string: `"Read,Write,Edit,Bash(git diff *)"` |
| `max_turns` (or `max_turns_override` if provided and valid) | `--max-turns` | Integer as string: `"25"` |

The return value is a flat `list[str]` ready to be spread into a `subprocess` args list:

```python
args = ["claude", "-p", prompt, "--bare", "--output-format", "json"]
args.extend(persona.to_cli_args(context, profile="write"))
# Result:
# ["claude", "-p", prompt, "--bare", "--output-format", "json",
#  "--append-system-prompt", "<full system prompt>",
#  "--allowedTools", "Read,Write,Edit,Bash(git diff *)",
#  "--max-turns", "25"]
```

### 4.5 Behavioral Rules Injection

Behavioral rules from the frontmatter are appended to the end of the rendered system prompt body as a numbered markdown section:

```
<rendered markdown body>

## Rules

1. Never push to the main or master branch
2. Always create a feature branch before making changes
3. Commit after each logical unit of work with a descriptive message
```

This keeps rules visible in the system prompt while allowing the persona author to write the main prompt body without worrying about rule formatting.

If `behavioral_rules` is empty, no `## Rules` section is appended.

### 4.6 Tool Profile Selection

```python
# Default tools (no profile)
persona.to_cli_args(context)
# -> uses persona.allowed_tools

# Named profile
persona.to_cli_args(context, profile="write")
# -> uses persona.tool_profiles["write"]

# Unknown profile
persona.to_cli_args(context, profile="admin")
# -> raises PersonaProfileError:
#    "Unknown tool profile 'admin' for persona 'dev-agent-v1'. "
#    "Available profiles: read_only, write"
```

### 4.7 Max Turns Override

```python
# Persona ceiling (max_turns=25)
persona.to_cli_args(context)
# -> "--max-turns", "25"

# Override downward (allowed)
persona.to_cli_args(context, max_turns_override=10)
# -> "--max-turns", "10"

# Override upward (rejected)
persona.to_cli_args(context, max_turns_override=50)
# -> raises PersonaValidationError:
#    "max_turns_override (50) exceeds persona ceiling (25)"
```

### 4.8 Frontmatter Parsing Strategy

The loader parses frontmatter manually rather than using a library:

```python
def _parse_frontmatter(self, content: str, file_path: Path) -> tuple[dict, str]:
    """Split file into YAML frontmatter dict and markdown body string."""
    content = content.strip()
    if not content.startswith("---"):
        raise PersonaParseError(
            f"{file_path}: File must begin with YAML frontmatter delimiter '---'"
        )
    # Find closing delimiter
    end = content.index("---", 3)
    yaml_str = content[3:end].strip()
    body = content[end + 3:].strip()
    frontmatter = yaml.safe_load(yaml_str)
    if not isinstance(frontmatter, dict):
        raise PersonaParseError(
            f"{file_path}: YAML frontmatter must be a mapping, got {type(frontmatter).__name__}"
        )
    return frontmatter, body
```

If the second `---` delimiter is not found, `ValueError` is caught and re-raised as `PersonaParseError` with the file path.

### 4.9 Validation Rules

Validation runs immediately after parsing, before returning the `Persona`. Every validation error includes:

1. **File path** -- which persona file
2. **Field name** -- which field failed
3. **Expected format** -- what was expected
4. **Actual value** (where safe to display) -- what was found

Example error messages:

```
PersonaValidationError: personas/dev-agent-v1.md: Missing required field 'model'.
    Required fields: schema_version, name, model, max_turns, allowed_tools

PersonaValidationError: personas/dev-agent-v1.md: Invalid value for 'model': 'gpt4'.
    Must be one of: opus, sonnet, haiku

PersonaValidationError: personas/dev-agent-v1.md: Invalid value for 'max_turns': -5.
    Must be a positive integer between 1 and 200

PersonaValidationError: personas/dev-agent-v1.md: Invalid value for 'name': 'Dev Agent'.
    Must match pattern ^[a-z][a-z0-9-]*$ (kebab-case, starts with letter, max 64 chars)

PersonaValidationError: personas/dev-agent-v1.md: 'allowed_tools' must be a non-empty list of strings.
    Got: []

PersonaValidationError: personas/dev-agent-v1.md: Unbalanced parentheses in tool name: 'Bash(git *'
    Tool names with scoped patterns must have balanced parentheses, e.g., 'Bash(git *)'

PersonaParseError: personas/dev-agent-v1.md: YAML parse error at line 5: mapping values are not allowed here
```

### 4.10 Exception Hierarchy

```python
class PersonaError(Exception):
    """Base exception for all persona loader errors."""
    pass

class PersonaParseError(PersonaError):
    """YAML frontmatter parsing failed."""
    pass

class PersonaValidationError(PersonaError):
    """Schema validation failed (missing field, invalid value)."""
    pass

class PersonaProfileError(PersonaError):
    """Requested tool profile does not exist."""
    pass

class PersonaVariableError(PersonaError):
    """Template variable not found in context."""
    pass
```

---

## 5. Example Persona: `personas/dev-agent-v1.md`

The complete file content shown in Section 1.5 above is the first persona shipped with this story. It defines the primary autonomous development agent with:

- **Default tools:** Read-only (Read, Glob, Grep) for safe exploration
- **Write profile:** Full development toolkit including git operations and PR creation
- **Read-only profile:** Analysis mode with git log and diff but no write capability
- **25-turn ceiling:** Sufficient for a single SDLC phase; the orchestrator can reduce for simple tasks
- **Sonnet model:** Cost-efficient for routine development work; orchestrator escalates to Opus for design phases
- **6 behavioral rules:** Safety constraints that prevent destructive operations
- **System prompt:** Oriented around SDLC execution with clear structure for assignment context, work process, quality standards, and communication expectations

The `personas/README.md` file will contain a brief description of the directory purpose, a link to this spec for schema reference, and a one-line summary of each persona file.

---

## 6. Implementation Plan

### Ordered File List

| Order | File | Description | Complexity | Dependencies |
|-------|------|-------------|------------|-------------|
| 1 | `tech_dev_agents/persona.py` | PersonaLoader class, Persona dataclass, variable injection, validation, CLI arg generation, exception hierarchy | Medium | `pyyaml` |
| 2 | `tests/test_persona.py` | Unit tests: valid load, missing fields, bad values, variable injection (happy + error paths), tool profile selection, max_turns override, CLI arg output format, frontmatter parse errors | Medium | `persona.py` |
| 3 | `personas/dev-agent-v1.md` | First real persona file (content from Section 1.5) | Low | None |
| 4 | `personas/README.md` | Directory purpose + schema reference link | Low | None |

### Dependency Notes

- `pyyaml` is the only external dependency. It is ubiquitous in Python environments and likely already installed. If not, add to `requirements.txt` or `pyproject.toml`.
- No other stories block this work. STORY-005 has zero dependencies.
- STORY-003 (Claude Code Runner) and STORY-006 (SDLC Execution Engine) are downstream consumers. They will import `PersonaLoader` and call `persona.to_cli_args()` to build the Claude Code invocation.

### Integration Point

The Claude Code Runner (STORY-003) will use the persona loader like this:

```python
from tech_dev_agents.persona import PersonaLoader

loader = PersonaLoader()
persona = loader.load("personas/dev-agent-v1.md")

context = {
    "repo_name": "my-web-app",
    "repo_path": "/workspace/my-web-app",
    "story_id": "STORY-042",
    "phase": "8",
    "branch_name": "feature/story-042-auth-flow",
}

cli_args = persona.to_cli_args(context, profile="write", max_turns_override=15)

# cli_args is now a list ready for subprocess:
# ["--append-system-prompt", "...", "--allowedTools", "Read,Write,...", "--max-turns", "15"]
```

### `--bare` Flag Note

The persona loader does not emit `--bare`. Whether Claude Code runs in bare mode (skipping hooks/MCP/CLAUDE.md discovery) is a runner concern, not a persona concern. The runner (STORY-003) adds `--bare` and `--output-format json` to its own base arg list. The persona's `to_cli_args()` output is appended to that base.
