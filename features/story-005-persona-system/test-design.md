# Test Design: Persona System (STORY-005)

> Phase 7 -- Test Design
> Date: 2026-03-26
> Story: STORY-005
> Scope: Medium

---

## Sources Used

- `seed.md`
- `analysis.md`
- `feature-spec.md`
- `security-review.md`
- `ux-review.md`
- `ops-review.md`

## Acceptance Criteria to Test Mapping

| AC | Requirement | Primary tests | Notes |
|----|-------------|---------------|-------|
| AC1 | Persona config format is defined with a documented schema and stays compatible with the repo's markdown-with-YAML-frontmatter convention | `test_load_persona_parses_frontmatter_and_body`, `test_load_persona_raises_parse_error_for_malformed_yaml_frontmatter` | Verifies `---` frontmatter parsing and markdown body extraction. |
| AC2 | Persona config includes `name`, `system_prompt` template, `allowed_tools`, `behavioral_rules`, and `model` | `test_load_persona_parses_frontmatter_and_body`, `test_load_persona_raises_validation_error_for_missing_required_field` | The markdown body is treated as the system prompt template. Missing required fields fail fast. |
| AC3 | System prompt template supports `{{variable}}` injection for `repo_name`, `story_id`, `phase`, `repo_path` | `test_load_persona_renders_cli_flags_with_variable_injection`, `test_load_persona_changes_rendered_prompt_when_context_changes`, `test_to_cli_args_raises_variable_error_for_missing_context_value` | Covers happy path, output variance, and missing variable failure. |
| AC4 | Example persona file exists at `personas/dev-agent-v1.md` | `test_load_example_persona_file_from_repo` | Uses the shipped file directly. |
| AC5 | Persona loader outputs Claude Code CLI flags: `--append-system-prompt`, `--allowedTools`, `--max-turns` | `test_load_persona_renders_cli_flags_with_variable_injection`, `test_to_cli_args_uses_requested_tool_profile`, `test_to_cli_args_rejects_max_turns_override_above_ceiling` | Checks exact flag order and value mapping. |
| AC6 | Tool permissions map directly to `--allowedTools` values, including scoped Bash patterns | `test_load_persona_renders_cli_flags_with_variable_injection`, `test_load_persona_raises_validation_error_for_unknown_tool_name` | Confirms CSV rendering and rejects unsupported tool names. |
| AC7 | Invalid configs fail fast with clear errors including file path and field name | `test_load_persona_raises_validation_error_for_missing_required_field`, `test_load_persona_raises_validation_error_for_invalid_model_and_max_turns`, `test_load_persona_raises_validation_error_for_invalid_tool_profile_definition` | Covers missing field, value bounds, and profile validation. |
| AC8 | Persona format remains compatible with existing `.sdlc/agents/` frontmatter pattern | `test_load_persona_parses_frontmatter_and_body`, `test_load_persona_raises_parse_error_for_malformed_yaml_frontmatter` | Ensures the loader follows the same delimiter-driven format as the repo's agent personas. |

## Test Inventory

| Test name | Coverage target | Expected behavior |
|-----------|-----------------|-------------------|
| `test_load_example_persona_file_from_repo` | File existence + validity | The shipped persona file loads successfully. |
| `test_load_persona_parses_frontmatter_and_body` | Parser + dataclass hydration | Frontmatter becomes validated fields and the markdown body becomes the system prompt template. |
| `test_load_persona_renders_cli_flags_with_variable_injection` | Rendering helpers | The loader emits prompt, tool CSV, and turn ceiling flags. |
| `test_load_persona_changes_rendered_prompt_when_context_changes` | Output variance gate | Different variables produce different rendered prompts. |
| `test_to_cli_args_uses_requested_tool_profile` | Tool profile selection | Named profile overrides default `allowed_tools`. |
| `test_to_cli_args_rejects_max_turns_override_above_ceiling` | Boundaries | Overriding upward is rejected. |
| `test_to_cli_args_raises_variable_error_for_missing_context_value` | Variable safety | Missing `{{var}}` raises a clear error. |
| `test_load_persona_raises_parse_error_for_malformed_yaml_frontmatter` | Parse failure path | Bad YAML/frontmatter delimiters fail with file path context. |
| `test_load_persona_raises_validation_error_for_missing_required_field` | Required field validation | Missing required metadata fails with file path and field. |
| `test_load_persona_raises_validation_error_for_invalid_model_and_max_turns` | Model and bounds validation | Invalid model or out-of-range turns fail clearly. |
| `test_load_persona_raises_validation_error_for_unknown_tool_name` | Tool validation | Unknown tool names are rejected. |
| `test_load_persona_raises_validation_error_for_invalid_tool_profile_definition` | Profile validation | Invalid profile names or tool entries are rejected. |

## Coverage Targets

- Parser: frontmatter delimiter handling, YAML parse errors, markdown body extraction
- Validation: required fields, schema version, model values, turn bounds, tool list checks, profile checks
- Rendering: template injection, rule appending, allowedTools CSV rendering, max-turn selection
- Failure mode: missing variables and invalid config errors include file path context

