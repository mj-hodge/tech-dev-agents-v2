# UX Review: Persona System (STORY-005)

> Phase 6c — Developer Experience Review
> Date: 2026-03-26
> Story: STORY-005
> Reviewer: UX Subagent

---

## Context

Target user: solo developer managing the agent fleet. Primary operation: editing persona files to change agent behavior. Evaluated against the feature-spec design.

---

## Findings

### FINDING-U1: No Feedback Loop for Variable Injection Debugging (High Friction)

A developer writing or modifying a persona template has no way to know at edit time which variables will be available when the loader runs. The `variables` frontmatter field is optional and documentation-only — the loader only warns on undeclared variables, it does not block or surface the mismatch during authoring.

**Scenario:** Developer adds `{{sprint_number}}` to a template. STORY-006 never passes `sprint_number`. The error surfaces only at agent runtime: `PersonaVariableError: Undefined variable 'sprint_number'`. By then the developer may not be watching the log.

**Improvement:** Add a `--dry-run` or `--validate` mode to the loader CLI (even a simple `python -m tech_dev_agents.persona personas/dev-agent-v1.md --validate`) that accepts a sample context JSON file and prints the rendered system prompt. This gives the developer immediate feedback without starting a container. Include this command in the `personas/README.md` quickstart.

---

### FINDING-U2: Error Messages Are Good, But Missing Line Numbers for Frontmatter Fields (Medium Friction)

The validation error format is strong — file path, field name, expected format, actual value. However, field-level validation errors do not include the line number within the YAML frontmatter. YAML parse errors from `pyyaml` include line numbers, but the custom validation layer (`PersonaValidationError`) does not carry position information.

**Scenario:** A 100-line persona file has `model: gpt4` at line 6. The error says `Invalid value for 'model': 'gpt4'` but not `line 6`. For short files this is minor; for evolved personas with many frontmatter fields it adds friction.

**Improvement:** When building the `frontmatter` dict from `yaml.safe_load`, use `yaml.compose()` to retain node position information and attach line numbers to field validation errors. Even `(approx. line N)` from scanning the raw YAML string for the field name would be a significant improvement over no line number.

---

### FINDING-U3: Persona Creation — No Scaffold / Template (Medium Friction)

The spec defines `personas/README.md` as containing "a brief description of the directory purpose, a link to this spec for schema reference, and a one-line summary of each persona file." It does not include a starter template or scaffold command for creating a new persona.

**Scenario:** Developer wants to create `review-agent-v1.md`. They must read the spec, manually copy the required frontmatter fields, remember the schema_version, and get the kebab-case name right. The probability of forgetting a required field on first attempt is high.

**Improvement:** Include a `personas/_template.md` file with all required fields filled in with placeholder values and optional fields commented out with their defaults. The `README.md` should point to this template explicitly: "To create a new persona, copy `_template.md` and rename it." This is zero-code work that eliminates the most common first-time authoring error.

---

### FINDING-U4: `PersonaProfileError` Message Is Excellent — Extend to to_cli_args Entirely (Low Friction)

The profile error message is well-designed: it names the unknown profile and lists available profiles. This pattern should be verified to be consistent across all error types. The spec shows good examples for `PersonaValidationError` but the `PersonaVariableError` message format is: `"Undefined variable '{{key}}' in persona template. Available variables: {sorted(context.keys())}"`. Listing available variables is correct and helpful, but the error does not name the persona file. When the runner handles multiple personas, the developer cannot tell which persona file triggered the error.

**Improvement:** Include the file path in `PersonaVariableError`, consistent with how `PersonaValidationError` is formatted.

---

### FINDING-U5: Consistency With `.sdlc/agents/` Format (Positive Finding)

The persona format (markdown + YAML frontmatter, `---` delimiters, kebab-case names) is fully consistent with the established `.sdlc/agents/*.md` pattern. A developer familiar with SDLC agent files will have immediate recognition. No friction introduced here.

---

### FINDING-U6: behavioral_rules Author Experience (Low Friction)

Rules are declared in YAML as a list, but the developer cannot see how they will appear in the rendered system prompt until runtime. The loader appends them as `## Rules\n\n1. Rule one\n...`. The spec documents this format, but a developer who does not read the spec may be surprised by the auto-formatted section.

**Improvement:** The `personas/README.md` or `_template.md` should include a rendered example showing what the final system prompt looks like after rule injection. One paragraph with a "before and after" is sufficient.

---

### FINDING-U7: `schema_version: "1"` Must Be a String in YAML (Low Friction)

YAML will parse `schema_version: 1` (without quotes) as an integer, not a string. The loader then checks `schema_version == "1"` and fails with a confusing `Invalid value for 'schema_version': 1` error. This is a common YAML authoring mistake.

**Improvement:** Normalize the value with `str(frontmatter.get("schema_version", ""))` before comparison, and document the quoting requirement prominently in the template.

---

## Summary

| ID | Issue | Impact | Effort to Fix |
|----|-------|--------|---------------|
| U1 | No variable injection dry-run / validation CLI | High | Medium |
| U2 | Validation errors lack line numbers | Medium | Medium |
| U3 | No starter template for new personas | Medium | Low |
| U4 | PersonaVariableError omits file path | Low | Low |
| U5 | Format consistency with .sdlc/agents/ | None — positive | N/A |
| U6 | Behavioral rules rendered form not shown in docs | Low | Low |
| U7 | schema_version integer vs string YAML trap | Low | Low |

## Recommendation

The design is developer-friendly. The top priority improvement is U3 (add `_template.md`) — it is the lowest effort, highest payoff change, eliminating the most common first-time authoring error. U1 (dry-run validation CLI) is the highest-value feature but can be a fast-follow in a subsequent micro-task. U7 (schema_version string normalization) should be fixed in Phase 8 implementation — it is a one-line code change that prevents a confusing error.
