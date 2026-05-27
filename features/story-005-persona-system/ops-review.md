# Ops Review: Persona System (STORY-005)

> Phase 6d — Operational Readiness Review
> Date: 2026-03-26
> Story: STORY-005
> Reviewer: Ops Subagent

---

## Findings

### FINDING-O1: Startup Failure Mode — No Fallback Behavior Defined (High Severity)

The loader raises `PersonaError` (or subclass) on any parse or validation failure. The spec does not define what the runner (STORY-003) should do when the persona fails to load. If the runner crashes on `PersonaError`, the entire agent container fails silently or with a Python traceback — there is no structured error output, no alert, and no fallback persona.

**Scenario:** A developer edits `dev-agent-v1.md` and introduces a YAML syntax error. The next agent startup silently fails. The developer does not notice until they check container logs manually.

**Recommendation:** STORY-003 (Runner) must catch `PersonaError` at startup, log the structured error to stdout (JSON format, matching `--output-format json`), and exit with a non-zero status code (e.g., `exit(2)` for config error vs `exit(1)` for runtime error). The persona system itself does not need to change — this is a runner responsibility. Document the expected exit codes in the spec so STORY-003 implementors know what to handle.

---

### FINDING-O2: Schema Versioning — Forward Compatibility Gap (Medium Severity)

The spec defines `schema_version: "1"` as required and specifies that any value other than `"1"` raises an error with "migration guidance." However, no migration guidance is defined, and there is no schema evolution path documented.

**Scenario:** Future story adds a new required field `timeout_seconds`. Persona files written for schema v1 lack this field. When the loader is upgraded to validate v2, all existing personas fail to load until manually updated — a potentially fleet-wide outage if the meta-repo is not updated atomically with the loader.

**Recommendation:** Define the versioning contract now:
1. New optional fields are always backward compatible — add with defaults, no version bump needed.
2. New required fields OR breaking changes to existing field semantics require a version bump.
3. The loader should support loading the previous version with a deprecation warning for one release cycle before hard-failing.

Add a `CHANGELOG.md` or a `## Schema History` section in `personas/README.md` that documents each version's changes.

---

### FINDING-O3: Persona Hot-Reload — Not Supported, Not Documented (Medium Severity)

The spec states that persona changes require a container restart (from `seed.md`: "Runtime persona switching is out of scope"). The loader does not cache state — each call to `loader.load()` re-reads from disk — but since STORY-003 calls `load()` once at startup, a file change takes no effect until restart.

This is an intentional design decision, but it is not documented in the spec as an operational constraint, and there is no mechanism to signal the runner to reload.

**Recommendation:** Explicitly document the restart-required behavior in `personas/README.md` and in the loader's module docstring. For v1 this is acceptable. As a future-proofing note: the design is compatible with hot-reload later (the loader is stateless, no in-memory cache to invalidate) — document this so the future implementor does not need to re-architect.

---

### FINDING-O4: No CI/CD Validation for Persona Files (Medium Severity)

The spec defines a rich validation layer in the loader, but there is no mechanism to run validation in CI. A persona file with a syntax error or missing required field can be merged to the meta-repo and will only fail at container startup — after the deployment is already underway.

**Recommendation:** Add a `make validate-personas` target (or equivalent) that runs:

```bash
python -m tech_dev_agents.persona personas/*.md --validate
```

and exits non-zero on any error. Add this to the CI pipeline (GitHub Actions or equivalent) so every PR that touches `personas/` is validated before merge. This is a one-liner once the `--validate` CLI flag exists (see UX review U1). The `PersonaLoader` API already supports this — only the CLI entrypoint needs to be added.

---

### FINDING-O5: No Observability on Successful Persona Load (Low Severity)

When the loader succeeds, it returns a `Persona` object. The runner receives it and calls `to_cli_args()`. There is no structured log output from the loader indicating which persona was loaded, which profile was selected, and what the effective tool list is. This makes debugging agent behavior difficult after the fact.

**Recommendation:** Add a structured log event (Python `logging` module at `INFO` level) at the end of `PersonaLoader.load()`:

```
INFO persona_loader: Loaded persona 'dev-agent-v1' schema_version=1 model=sonnet max_turns=25 tools=3
```

Similarly, `to_cli_args()` should log at `DEBUG` level:
```
DEBUG persona_loader: Resolved profile='write' tools=11 max_turns=15 (override from persona ceiling 25)
```

These logs are visible in container stdout when the runner is invoked and make post-hoc debugging tractable.

---

### FINDING-O6: Persona File Not Pinned to Code Version (Low Severity)

The `personas/dev-agent-v1.md` file lives in the meta-repo alongside the loader code. There is no mechanism to ensure a given persona file version is compatible with the loader version that reads it — if the meta-repo is deployed from a different commit than the container image, version skew is possible.

**Recommendation:** For v1, document that the meta-repo commit used to build the container image determines the persona schema version in use. Use the `version` field in the persona frontmatter as a human-readable audit trail. A more rigorous solution (schema contract testing in CI) is a Phase 9/10 concern.

---

## Summary

| ID | Issue | Severity | Owner |
|----|-------|----------|-------|
| O1 | No startup failure fallback or structured error output | High | STORY-003 (Runner) |
| O2 | Schema versioning evolution path not defined | Medium | STORY-005 (this story) |
| O3 | Hot-reload not documented as intentionally unsupported | Medium | STORY-005 (this story) |
| O4 | No CI/CD linting for persona files | Medium | STORY-005 + CI config |
| O5 | No structured log on successful persona load | Low | STORY-005 (this story) |
| O6 | Persona file not pinned to loader version | Low | Ops / deployment story |

## Recommendation

The persona system is operationally viable for single-developer use in v1. The highest-priority gap is O1 — this is a runner concern but must be specified here so STORY-003 implementors know the expected error handling contract. O4 (CI validation) is the highest-value work within this story's scope: it prevents the most common class of production failure (bad persona merged to main). Implement O4 alongside the `--validate` CLI entrypoint (UX finding U1) — they are the same feature from different perspectives. O2 (schema versioning contract) should be documented in `personas/README.md` before Phase 8 begins, as a one-paragraph addition, not a code change.
