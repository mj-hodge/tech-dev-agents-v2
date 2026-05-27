# Seed: Persona System

> Phase 1 -- Concept & Seed
> Date: 2026-03-26
> Scope: Medium
> Story: STORY-005
> Epic: Autonomous Dev Agent (v1)
> Dependencies: None

---

## Problem Statement

The autonomous dev agent needs a configurable identity. Today, the Hermes bot prototype hardcodes system prompts and tool permissions as inline strings in CLI invocations (see `docs/hermes-prompt.md`). This approach does not scale: adding a second agent means duplicating and modifying shell scripts, there is no single source of truth for what an agent can do, and changes to agent behavior require code changes rather than config changes.

We need a persona config format -- stored in the meta-repo (`tech-dev-agents`) -- that declaratively defines an agent's name, system prompt, tool permissions, behavioral rules, and model preference. A loader must read this config and translate it into Claude Code CLI flags (`--append-system-prompt`, `--allowedTools`, `--max-turns`, etc.) at container startup.

## Target User

The solo developer managing the agent fleet. They edit persona files in the meta-repo to control agent behavior without touching application code.

## Desired Outcome

When this is done:

1. A persona config schema exists and is documented.
2. At least one real persona file lives in the meta-repo (e.g., `personas/dev-agent-v1.md`).
3. A loader script/module reads that file and produces the exact CLI flags needed to invoke Claude Code with the persona applied.
4. Variable injection works -- the system prompt template can reference runtime values like repo name, story ID, and current phase.
5. Invalid configs fail fast with clear, actionable error messages.

---

## Acceptance Criteria

- [ ] Persona config format is defined with a documented schema (fields, types, required vs optional, validation rules)
- [ ] Persona config includes at minimum: `name`, `system_prompt` (template), `allowed_tools` (list), `behavioral_rules` (list of strings), `model` (preferred model tier)
- [ ] System prompt template supports variable injection using `{{variable}}` syntax (at least: `{{repo_name}}`, `{{story_id}}`, `{{phase}}`, `{{repo_path}}`)
- [ ] At least one example persona file exists in the meta-repo at `personas/dev-agent-v1.md`
- [ ] Persona loader reads a persona config file and outputs Claude Code CLI flags: `--append-system-prompt`, `--allowedTools`, `--max-turns`
- [ ] Tool permissions in persona config map directly to `--allowedTools` flag values (e.g., `Read`, `Write`, `Edit`, `Bash(git *)`)
- [ ] Invalid persona config (missing required fields, malformed YAML frontmatter, unknown tool names) produces clear error messages with file path, field name, and expected format
- [ ] Persona format is compatible with the existing `.sdlc/agents/` markdown-with-YAML-frontmatter convention used in this repo

---

## Constraints

- **Meta-repo is source of truth.** Persona files live in `tech-dev-agents`, not in target repos.
- **Claude Code CLI is the execution layer.** The persona system produces CLI flags -- it does not wrap or replace the CLI. Key flags: `--append-system-prompt`, `--allowedTools`, `--max-turns`, `--output-format`.
- **Markdown with YAML frontmatter.** Follow the pattern already established in `.sdlc/agents/` (e.g., `phase-1-seed.md`). Structured config in frontmatter, prose system prompt in the markdown body.
- **No external dependencies for the loader.** The loader must work with Node.js standard library plus a YAML parser (already available in the project).
- **Deterministic output.** Given the same persona file and the same variables, the loader must produce identical CLI flags every time.

## Existing Patterns

| Pattern | Location | Relevance |
|---------|----------|-----------|
| SDLC agent personas | `.sdlc/agents/*.md` | YAML frontmatter + markdown body; proven format in this repo |
| Hermes CLI invocation | `docs/hermes-prompt.md` lines 296-314 | Shows `--allowedTools` and `--append-system-prompt` usage |
| Claude Code agent definitions | `.claude/agents/*.md` | Claude Code's own agent format; worth aligning with |

---

## Scope Confirmation: Medium

**Rationale:** This is a config format definition + a loader utility. No infrastructure, no external services, no database. The schema design requires careful thought (Medium, not Small) because it sets the contract for all future agents, but there is no novel technology or integration risk.

**Phase path:** 1 -> 4 -> 6 -> [6b, 6c, 6d] -> 7 -> 8 -> 8b -> 11 -> Done

---

## Out of Scope

- **Persona versioning / A/B testing.** Future work. v1 loads the current file on disk.
- **Runtime persona switching.** An agent gets one persona at startup. Changing persona means restarting.
- **Persona performance metrics.** No tracking of which persona produces better outcomes. That is a Phase 9/10 concern for a later story.
- **Multi-persona orchestration.** One persona per agent instance. Fleet coordination is a separate story.
- **Persona inheritance / composition.** No "base persona + overlay" pattern in v1. Each persona is self-contained.

---

## Open Questions

1. Should the persona file format support multiple tool permission profiles (e.g., `read_only` vs `full_write`) that the loader selects at invocation time based on task type? The Hermes prototype already distinguishes these two modes.
2. Should `--max-turns` be part of the persona config, or is it always set by the orchestrator based on task complexity?
3. Should the loader output a JSON object (for programmatic consumption) or a shell command string (for direct execution)?

---

## Next Phase

Phase 4 (Analysis) -- evaluate the config format options, assess risks around schema evolution, and confirm technical feasibility of the loader approach.
