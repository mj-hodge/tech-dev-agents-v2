# Personas

This directory contains persona definitions for the autonomous dev agent system.

Persona files use markdown with YAML frontmatter so they stay compatible with the existing `.sdlc/agents/*.md` convention in this repo.

## Schema Reference

- See `features/story-005-persona-system/feature-spec.md` for the full schema and validation rules.
- Loader entrypoint: `tech_dev_agents.persona.PersonaLoader`
- Rendered output: `--append-system-prompt`, `--allowedTools`, `--max-turns`

## Shipped Personas

| File | Purpose |
|------|---------|
| `dev-agent-v1.md` | Primary development persona used by STORY-005 |

## Operational Note

Persona changes take effect the next time the loader reads the file. The runner should restart to pick up edits.

