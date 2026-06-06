# Personas

This directory contains persona definitions for the autonomous dev agent system.

Persona files use markdown with YAML frontmatter so they stay compatible with the existing `.sdlc/agents/*.md` convention in this repo.

## Schema Reference

- See `features/story-005-persona-system/feature-spec.md` for the full schema and validation rules.
- Loader entrypoint: `tech_dev_agents.persona.PersonaLoader`
- Rendered output: `--append-system-prompt`, `--allowedTools`, `--max-turns`

## Agent Personas

| File | Agent | Role | SDLC Phases |
|------|-------|------|-------------|
| `the-architect.md` | The Architect | Research & Planning | 1-6 (Seed → Design) |
| `neo.md` | Neo | Implementation | 7-8 (Test Design → Build) |
| `morpheus.md` | Morpheus | Project Management | Backlog & Story Writing |
| `agent-smith.md` | Agent Smith | Adversarial Reviewer | 8b (Code Review) |

## Manager

Skynet (the manager agent) does not use a persona file — it uses `deployment/vm/SOUL-skynet.md` directly as its system prompt.

## SOUL Files (Deployment)

Full operational SOUL files for each agent live in `deployment/vm/`:

| File | Agent |
|------|-------|
| `SOUL-skynet.md` | Skynet (Manager) |
| `SOUL-architect.md` | The Architect |
| `SOUL-neo.md` | Neo |
| `SOUL-morpheus.md` | Morpheus |
| `SOUL-smith.md` | Agent Smith |

## Operational Note

Persona changes take effect the next time the loader reads the file. Restart the dispatch poller for the relevant agent to pick up edits:
```
sudo systemctl restart dispatch-poller@AGENT_NAME
```
