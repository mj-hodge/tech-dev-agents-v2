# Analysis: Persona System (STORY-005)

> Phase 4 — Technical, Business, and Risk Analysis
> Date: 2026-03-26
> Story: STORY-005
> Epic: Autonomous Dev Agent (v1)
> Scope: Medium

---

## Recommendation Summary

**Adopt markdown with YAML frontmatter as the persona config format**, following the exact pattern already established in `.sdlc/agents/*.md`. Use `{{variable}}` Mustache-style injection for system prompt templates, implemented via simple string replacement in a Node.js module (no template engine dependency). The loader should output a structured JSON object rather than a shell string.

| Decision | Recommendation |
|----------|---------------|
| Config format | Markdown + YAML frontmatter |
| Variable injection | Mustache-style `{{var}}` via `String.replace()` |
| Loader output | JSON object (programmatic), not shell string |
| Loader type | Node.js module (`persona-loader.js`) |
| Storage structure | `personas/` directory, one file per persona |
| Tool profiles | Single required profile + optional named profiles |

---

## 1. Technical Analysis

### 1.1 Config Format Options

Four formats were evaluated against the requirements: readability, schema validation, variable injection support, and Claude Code compatibility. The constraint that the format must align with `.sdlc/agents/*.md` is a hard constraint from `seed.md` — it is not a preference.

#### Option A: Markdown + YAML Frontmatter (current `.sdlc/agents/` pattern)

```
---
name: dev-agent-v1
model: sonnet
max_turns: 20
allowed_tools:
  - Read
  - Write
  - Edit
  - Bash(git *)
behavioral_rules:
  - Never push to main
  - Always create a feature branch
---

# Dev Agent v1

You are a senior software engineer working autonomously on {{repo_name}}.
Your current task is {{story_id}} in phase {{phase}}.

[... full system prompt prose ...]
```

**Strengths:**
- Already the established pattern in this codebase — zero new convention to learn
- The markdown body is a natural container for multi-paragraph system prompt prose without escaping requirements
- YAML frontmatter is well-supported by `js-yaml` (already available in Node.js ecosystem) and `gray-matter` (the de facto standard for frontmatter parsing)
- Human-readable and Git-diffable; a solo developer can edit it in any text editor
- The separation of structured config (frontmatter) from prose (body) is architecturally sound — structured fields validate cleanly, prose body handles the complex natural-language content
- Compatible with Claude Code's own `.claude/agents/*.md` format, which uses the same markdown+YAML convention

**Weaknesses:**
- Schema validation requires a separate validator step (no built-in schema enforcement in YAML frontmatter parsers)
- Whitespace sensitivity in YAML can cause parsing errors that are not always obvious

**Verdict: Strongly recommended.** The consistency value alone justifies this choice, and it has no meaningful weaknesses that alternatives solve better.

#### Option B: Pure YAML

```yaml
name: dev-agent-v1
model: sonnet
max_turns: 20
allowed_tools:
  - Read
  - Write
system_prompt: |
  You are a senior software engineer working on {{repo_name}}.
  Your current task is {{story_id}} in phase {{phase}}.
  [multi-paragraph prose...]
behavioral_rules:
  - Never push to main
```

**Strengths:**
- Strict schema validation via `ajv` or JSON Schema (YAML is a superset of JSON)
- Single file, single format — no frontmatter parser needed

**Weaknesses:**
- Multi-line string blocks (`|` or `>`) are awkward for complex system prompts that may include markdown formatting, code examples, or special characters
- System prompt prose loses its natural writing environment — YAML indentation requirements make editing error-prone
- Departs from the `.sdlc/agents/` convention with no benefit that justifies the break
- System prompts often contain YAML-special characters (`:`  `#` `-`) requiring careful quoting

**Verdict: Not recommended.** The multi-line string problem is a real DX friction point for system prompts, and it adds nothing over the frontmatter approach.

#### Option C: Pure JSON

```json
{
  "name": "dev-agent-v1",
  "model": "sonnet",
  "max_turns": 20,
  "allowed_tools": ["Read", "Write"],
  "system_prompt": "You are a senior software engineer on {{repo_name}}.\nYour task is {{story_id}} in phase {{phase}}."
}
```

**Strengths:**
- Native to Node.js (`JSON.parse`, no dependency)
- Strict schema validation via JSON Schema / `ajv`
- Machine-readable first

**Weaknesses:**
- Completely hostile to editing multi-paragraph system prompts — newlines must be escaped as `\n`, quotes require escaping
- No comments allowed — cannot annotate config fields
- Poor Git diff readability for prose changes
- Entirely inconsistent with the human-readable agent file convention in this repo

**Verdict: Rejected.** JSON is appropriate for machine-generated configs. Persona files are human-authored and human-maintained. The DX cost is prohibitive.

#### Option D: TOML

```toml
name = "dev-agent-v1"
model = "sonnet"
max_turns = 20
allowed_tools = ["Read", "Write", "Edit"]
behavioral_rules = ["Never push to main"]

[system_prompt]
text = """
You are a senior software engineer on {{repo_name}}.
Your task is {{story_id}} in phase {{phase}}.
"""
```

**Strengths:**
- Better multi-line string support than JSON
- Readable for structured config

**Weaknesses:**
- No TOML parser in Node.js standard library; requires adding `@iarna/toml` or similar
- Still awkward for long prose system prompts compared to markdown body
- Zero precedent in this codebase
- Less known than YAML in the typical developer toolchain

**Verdict: Rejected.** Adds a dependency and a new convention with no advantage over the YAML frontmatter approach.

#### Format Comparison Matrix

| Criterion | MD+YAML | Pure YAML | JSON | TOML |
|-----------|---------|-----------|------|------|
| Codebase consistency | ++ | - | -- | -- |
| System prompt editability | ++ | - | -- | + |
| Schema validation | + | ++ | ++ | + |
| Git diff readability | ++ | + | - | + |
| Node.js dependency | js-yaml / gray-matter | js-yaml | none | @iarna/toml |
| Claude Code agent alignment | ++ | - | - | - |
| Special char handling in prompts | ++ | - | -- | + |

---

### 1.2 Variable Injection Approaches

The seed requires `{{variable}}` syntax with at minimum: `{{repo_name}}`, `{{story_id}}`, `{{phase}}`, `{{repo_path}}`.

#### Option A: Simple `String.replace()` with regex (recommended)

```javascript
function injectVariables(template, vars) {
  return template.replace(/\{\{(\w+)\}\}/g, (match, key) => {
    if (!(key in vars)) throw new Error(`Missing variable: ${key}`);
    return String(vars[key]);
  });
}
```

**Strengths:**
- Zero dependencies — pure Node.js
- Deterministic: same input always produces same output (seed requirement)
- Fail-fast on missing variables (seed requirement: "Invalid configs fail fast")
- `\w+` pattern naturally restricts variable names to safe alphanumeric+underscore, blocking injection attempts that use special characters
- Trivially auditable — the entire implementation fits in 5 lines

**Weaknesses:**
- No nested variables, filters, or conditionals (not needed for v1; explicitly out of scope)
- No partial application or template inheritance (also out of scope)

#### Option B: Mustache / Handlebars library

```javascript
import Mustache from 'mustache';
const result = Mustache.render(template, vars);
```

**Strengths:**
- Industry-standard syntax that matches the `{{var}}` requirement exactly
- Supports conditionals and loops if needed in future

**Weaknesses:**
- Adds an external dependency for functionality that `String.replace()` handles in 5 lines
- Mustache's default behavior silently renders missing variables as empty string — this is the opposite of fail-fast
- Over-engineered for v1 requirements; scope explicitly excludes conditionals and loops

#### Option C: Jinja2-style (Python) or template literals (JS)

Not applicable. The loader is Node.js. Python-style `{var}` conflicts with JSON/YAML syntax. ES6 template literals require `eval` or `Function` constructor to evaluate runtime strings — both are security anti-patterns.

**Verdict: Option A (simple `String.replace()`) is the correct choice.** It satisfies all v1 requirements, introduces no dependencies, and is the most auditable implementation. The fail-fast behavior on missing variables is a feature, not a limitation.

---

### 1.3 Persona Loader Design

#### Option A: Node.js Module (`src/persona-loader.ts`) — Recommended

A TypeScript module that exports a `loadPersona(filePath, vars)` function. Called at container startup by the Claude Code runner (STORY-003).

```typescript
export interface PersonaConfig {
  name: string;
  model: 'sonnet' | 'opus' | 'haiku';
  max_turns: number;
  allowed_tools: string[];
  behavioral_rules: string[];
  tool_profiles?: Record<string, string[]>;
}

export interface LoadedPersona {
  flags: {
    appendSystemPrompt: string;
    allowedTools: string;
    maxTurns: number;
  };
  meta: PersonaConfig;
}

export function loadPersona(filePath: string, vars: Record<string, string>): LoadedPersona;
```

**Output JSON → Claude Code CLI flag mapping:**

| Persona field | Claude Code flag | Example |
|--------------|------------------|---------|
| `system_prompt` (body, after injection) | `--append-system-prompt` | `--append-system-prompt "You are..."` |
| `allowed_tools` | `--allowedTools` | `--allowedTools "Read,Write,Edit,Bash(git *)"` |
| `max_turns` | `--max-turns` | `--max-turns 20` |
| `model` | (passed to STORY-003 runner for model selection) | N/A — not a direct Claude Code flag |

Note: `--allowedTools` expects a comma-separated string. The loader joins the array: `allowed_tools.join(',')`.

**Strengths:**
- Strongly typed interface enforces schema at compile time
- Importable by STORY-003 (Claude Code Runner) — clean integration boundary
- Unit-testable in isolation
- Does not shell out — no process spawn overhead

**Weaknesses:**
- Requires TypeScript compilation as part of build (already true for the project)

#### Option B: Shell script wrapper

```bash
#!/usr/bin/env bash
# load-persona.sh <persona-file> [key=value ...]
```

**Weaknesses:**
- Variable injection in bash is error-prone (quoting, special chars, newlines in system prompts)
- Cannot produce structured output easily — would need to print flags as a string for `eval`
- `eval`-ing shell strings with user-provided content is a security concern
- Not testable with the Node.js test suite
- STORY-003 is a Node.js module; a shell script creates an unnecessary subprocess boundary

**Verdict: Rejected.**

#### Option C: Shell function (in `.bashrc` or similar)

**Verdict: Rejected.** Not portable across containers, not testable, not importable.

#### Loader Output: JSON Object vs. Shell String

The seed asks: "Should the loader output a JSON object (for programmatic consumption) or a shell command string (for direct execution)?"

**Recommendation: JSON object.**

Reasons:
1. STORY-003 (Claude Code Runner) calls Claude Code via `execFile` (not shell string evaluation), passing an args array. A JSON object maps directly to this pattern.
2. Shell command strings require either `eval` (security risk) or a secondary parsing step to extract individual flags.
3. A JSON object allows the caller to inspect individual values (e.g., log the `allowedTools` for audit), which a shell string does not.
4. The Hermes bot prototype in `docs/hermes-prompt.md` already uses `execFile` with an args array (see lines 407-417). The loader output should match this pattern.

---

### 1.4 Storage Structure

**`personas/` directory in the meta-repo root**, one file per persona:

```
personas/
  dev-agent-v1.md          # primary dev agent persona
  dev-agent-readonly.md    # read-only analysis mode (future)
  review-agent-v1.md       # future: PR review specialist
```

**Naming convention:** `<agent-name>-v<N>.md` where:
- `agent-name` is kebab-case, descriptive of the agent's role
- `v<N>` is an integer version suffix, allowing side-by-side persona evolution without breaking existing deployments

**One file per persona** (not split files). The frontmatter + body format is self-contained. Splitting config from prompt creates a synchronization problem.

---

### 1.5 Tool Permission Profiles

The seed asks whether to support multiple tool permission profiles per persona (e.g., `read_only` vs. `full_write`) that the loader selects at invocation time.

**Recommendation: Yes, support optional named profiles in the frontmatter.**

Rationale: The Hermes prototype already distinguishes `read_only` and `full_write` modes (see `hermes-prompt.md` lines 295-315). The persona should encode this knowledge, not the caller. A `tool_profiles` map in the frontmatter allows the orchestrator to pass a profile name at invocation time, keeping the decision logic in config rather than code.

```yaml
allowed_tools:  # default profile
  - Read
  - Glob
  - Grep
tool_profiles:
  write:
    - Read
    - Write
    - Edit
    - Bash(git diff *)
    - Bash(git status *)
    - Bash(git commit *)
    - Bash(git checkout *)
    - Bash(gh pr create *)
```

The loader accepts an optional `profile` parameter. If provided, it uses `tool_profiles[profile]` instead of `allowed_tools`. This answers open question #1 from the seed affirmatively.

---

### 1.6 The `--max-turns` Question

The seed asks: should `max_turns` be in the persona, or set by the orchestrator?

**Recommendation: Both — persona sets a ceiling, orchestrator can override downward.**

The persona defines a `max_turns` ceiling appropriate for the agent's role. The orchestrator (STORY-006) may pass a lower value for simple tasks. This prevents cost runaway from autonomous loops while allowing task-specific tuning. The loader exposes the persona's `max_turns` in its output; the caller decides whether to use it or substitute a lower value.

---

## 2. Business Analysis

### 2.1 Developer Experience (DX)

The target user is a solo developer editing persona files in a text editor. DX is the primary business concern.

**Markdown + YAML frontmatter wins on DX for the following reasons:**

- **Editing system prompts is the most frequent operation.** The developer will iterate on system prompt prose far more often than on tool lists. The markdown body makes this natural — no escaping, no indentation constraints, syntax highlighting in any editor.
- **YAML frontmatter is familiar.** It is used in Jekyll, Hugo, Obsidian, and GitHub markdown. Any developer has likely encountered it.
- **Git diffs are legible.** When the developer changes behavioral rules or adds a tool, the diff shows exactly what changed. JSON diffs for prose changes are unreadable.
- **Validation errors are actionable.** A YAML parse error includes a line number. A missing required field can be caught by the loader with a clear message naming the field and the file.

**Comparison to the SDLC agent files:** The `.sdlc/agents/*.md` files (e.g., `phase-1-seed.md`) demonstrate this pattern at scale — 30+ files, all consistent, all human-editable. The pattern has proven itself in this codebase. Persona files are the same category of artifact.

### 2.2 Extensibility

The following extensions are explicitly out of scope for v1 but should not be foreclosed by the format choice:

| Future capability | Format impact |
|------------------|---------------|
| Persona versioning | `v<N>` suffix in filename handles this; frontmatter `version` field can be added |
| Persona inheritance | Could be added as `extends: base-agent.md` in frontmatter — no format change needed |
| A/B testing | Two persona files, external selection logic — format is neutral |
| Fleet registry | Separate `registry.json` references persona filenames — format is neutral |
| Additional CLI flags | New frontmatter fields map to new flags — backward compatible if optional |

The YAML frontmatter format is highly extensible because new optional fields can be added without breaking existing persona files (the loader ignores unknown fields or validates against a versioned schema).

### 2.3 Alignment with Existing Convention

The `.sdlc/agents/` convention is the dominant pattern in this repo. Any departure from it requires justification. There is no technical justification for a different format in `personas/` — the use case is identical: a markdown file with structured metadata in YAML frontmatter and prose in the body.

Using the same format means:
- One mental model for developers reading and editing agent/persona files
- Potentially shared tooling for validation and rendering
- The `personas/` directory is immediately recognizable to anyone who has worked with `.sdlc/agents/`

---

## 3. Risk Analysis

### 3.1 Prompt Injection via Persona Config

**Risk:** A malicious actor who gains write access to a persona file could inject adversarial instructions into the system prompt, manipulating the agent's behavior in unpredictable ways.

**Threat model:**
- The meta-repo (`tech-dev-agents`) controls all personas. Write access to this repo grants control over agent behavior.
- Variable values injected at runtime (e.g., `{{repo_name}}`) could contain prompt injection payloads if sourced from untrusted input.

**Mitigations:**
1. **Repo access control is the primary control.** The meta-repo should have restricted write access. This is an operational control, not a code control.
2. **Variable injection uses `\w+` key matching only** — this restricts variable names to safe alphanumeric identifiers. The variable *value* is still free-form text.
3. **Variable values should be sanitized at the injection site.** The loader should reject values containing `}}` (which could break out of the template syntax) and optionally strip markdown heading characters (`#`) that could restructure the prompt.
4. **`--append-system-prompt` is an additive flag** — it does not replace Claude's core constraints, only extends them. Injected content cannot override Anthropic's built-in safety training.

**Residual risk:** Medium-low. The primary threat (compromised meta-repo) is mitigated by access control outside the persona system itself.

### 3.2 Schema Drift

**Risk:** As personas evolve, new required fields are added or field semantics change, breaking existing persona files silently.

**Mitigations:**
1. **Explicit schema validation at load time.** The loader must validate all required fields on startup, not lazily. Missing required fields produce a startup error, not a runtime error.
2. **Schema versioning in frontmatter.** Add an optional `schema_version: "1"` field. When the schema changes, increment the version and add a migration check in the loader.
3. **Required vs. optional field discipline.** Document clearly which fields are required and which are optional with defaults. The loader applies defaults for optional fields, never silently omitting them.
4. **Test fixtures for schema validation.** Phase 7 must include test cases for malformed personas (missing `name`, missing `allowed_tools`, unknown field values) to ensure error messages are actionable.

**Residual risk:** Low if schema versioning is implemented from the start.

### 3.3 Claude Code CLI Flag Compatibility

**Risk:** Claude Code CLI flags (`--append-system-prompt`, `--allowedTools`, `--max-turns`) may change between versions, breaking the loader output.

**Current flag inventory (from `hermes-prompt.md`):**

| Flag | Status | Risk |
|------|--------|------|
| `--append-system-prompt` | Documented in Hermes design | Stable |
| `--allowedTools` | Documented in Hermes design | Stable |
| `--max-turns` | Documented in Hermes design | Stable |
| `--output-format json` | Documented in Hermes design | Stable |
| `--bare` | Documented in Hermes design | Stable |

**Mitigations:**
1. **Pin Claude Code CLI version** in the container image. Do not use `@latest` in production containers.
2. **Loader emits the flags as named fields in JSON output**, not as a pre-constructed CLI string. If a flag name changes, only the loader needs updating, not the persona files.
3. **Integration test the full flag set** in Phase 7 — invoke `claude` with a test persona and verify the flags are accepted.
4. **The `--allowedTools` format (comma-separated string with parenthetical patterns)** is the highest-risk flag — its syntax for scoped tools like `Bash(git *)` could change. Document the exact format in the schema specification.

**Residual risk:** Medium. Mitigated by version pinning and the JSON output abstraction layer.

### 3.4 Variable Injection Edge Cases

**Risk:** Missing variables, nested references, special characters in variable values, or malformed template syntax cause loader failures.

| Edge case | Behavior without mitigation | Recommended behavior |
|-----------|---------------------------|---------------------|
| Missing variable (e.g., `{{phase}}` not provided) | Silent empty string (Mustache default) | Throw error: `Missing required variable: phase` |
| Variable value contains `{{` | Could cause double-substitution in naive implementations | `String.replace()` with `/g` flag processes left-to-right once — no double substitution risk |
| Variable value contains newlines | Injected as-is into the system prompt | Acceptable; no special handling needed |
| Variable value contains YAML special chars | N/A — variables are injected into the markdown body, not the YAML frontmatter | Safe; markdown body is not re-parsed as YAML |
| Unknown variable in template (e.g., `{{undefined_var}}`) | Silent passthrough | Configurable: warn (for optional vars) or throw (for required vars) |
| Frontmatter YAML parse error | js-yaml throws; loader crashes | Catch and re-throw with file path and line number |

**Mitigations:**
1. Define a canonical list of required variables in the loader (or in the persona frontmatter as `required_vars: [repo_name, story_id, phase]`).
2. Validate all required variables are present before injection.
3. Wrap YAML parse errors with file path context.

**Residual risk:** Low if the loader implements explicit required-variable validation.

---

## 4. Open Questions — Resolved

| Question (from seed) | Resolution |
|----------------------|------------|
| Multiple tool permission profiles per persona? | Yes — `tool_profiles` map in frontmatter, optional, selected by caller at invocation time |
| `--max-turns` in persona or orchestrator? | Both — persona sets ceiling, orchestrator may override downward |
| Loader output: JSON or shell string? | JSON object — maps directly to `execFile` args array; no eval needed |

---

## 5. Recommended Approach

### Summary

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Config format | Markdown + YAML frontmatter | Codebase consistency; best DX for prose system prompts |
| Variable injection | `String.replace()` with `{{var}}` regex | Zero deps; deterministic; fail-fast on missing vars |
| Loader output | JSON object (`{ flags: {...}, meta: {...} }`) | Clean integration with STORY-003's `execFile` pattern |
| Loader type | TypeScript module in `src/persona-loader.ts` | Typed; unit-testable; importable; no subprocess boundary |
| Storage | `personas/` directory, one `.md` file per persona | One file per persona; `v<N>` suffix for versioning |
| Tool profiles | `allowed_tools` (default) + `tool_profiles` map | Encodes Hermes read/write mode distinction in config |
| Max turns | Persona ceiling + orchestrator override | Cost control; task-appropriate flexibility |
| Schema validation | Explicit required-field check on load; fail-fast | Satisfies seed AC: "Invalid configs fail fast with clear errors" |

### Key Implementation Notes for Phase 6

1. **The persona file schema must document all fields** with types, required/optional status, and example values. This is the primary deliverable of Phase 6 for this story.

2. **The `allowed_tools` format must match Claude Code's `--allowedTools` syntax exactly** — including scoped tool patterns like `Bash(git commit *)`. The schema must document which scoped patterns are supported and validated.

3. **The loader's error messages are a first-class concern.** Each error must include: file path, field name, expected format, and (where possible) the actual value that caused the error.

4. **The `--bare` flag interaction needs a decision.** The Hermes prototype uses `--bare` to skip hook/MCP discovery. If the persona loader is used in bare mode, `--append-system-prompt` still applies. If not in bare mode, the system prompt may interact with CLAUDE.md. Document which invocation mode personas are designed for.

5. **`gray-matter` is the recommended frontmatter parser** (used by Jekyll, Gatsby, Astro; battle-tested). It handles edge cases in frontmatter parsing more robustly than writing a custom YAML-delimiter splitter. If `gray-matter` is not already in the project, its addition is justified given the central role of frontmatter parsing in this story.
