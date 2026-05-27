# Phase 10 Site Reliability — STORY-005 Persona System

Date: 2026-03-31
Scope: Medium

---

## 1. Operational Readiness

This story delivers the **persona configuration system** — a Python module that loads, validates, and renders agent persona files into Claude Code CLI arguments. It is a library module consumed by STORY-003 (Claude Code Runner) and STORY-006 (SDLC Execution Engine). It does not deploy infrastructure or run as a standalone service.

### What This Story Ships

| Component | Type | Operational Impact |
|-----------|------|-------------------|
| `tech_dev_agents/persona.py` | Python library module (431 lines) | None (no runtime process) |
| `tests/test_persona.py` | Test suite (14 tests) | CI pipeline — runs in <0.1s |
| `personas/dev-agent-v1.md` | Agent persona config file | Loaded at agent startup by runner |
| `personas/README.md` | Directory documentation | None |

### What This Story Specifies (for downstream consumption)

| Component | Consumer | Integration Point |
|-----------|----------|-------------------|
| `PersonaLoader.load()` | STORY-003 Runner | Loads persona at agent startup |
| `Persona.to_cli_args()` | STORY-003 Runner | Generates `--append-system-prompt`, `--allowedTools`, `--max-turns` flags |
| `Persona.render()` | STORY-006 Engine | Gets structured render result for orchestration decisions |
| Exception hierarchy | STORY-003/006 | `PersonaError` subclasses for structured error handling |

---

## 2. Ops Review Gap Resolution

The Phase 6d ops-review.md identified 6 findings. Status:

| Finding | Severity | Resolution | Status |
|---------|----------|-----------|--------|
| O1: No startup failure fallback | High | Runner responsibility (STORY-003). Persona loader raises structured exceptions; runner must catch and handle. | Documented, deferred to STORY-003 |
| O2: Schema versioning evolution path | Medium | Documented in personas/README.md. New optional fields are backward compatible; new required fields need version bump. | Documented |
| O3: Hot-reload not documented | Medium | Loader is stateless — each `load()` re-reads from disk. Restart-required behavior is documented. Design is compatible with future hot-reload. | Documented |
| O4: No CI/CD validation for persona files | Medium | `PersonaLoader.load()` validates everything. CI integration (`make validate-personas`) deferred until GitHub Actions pipeline is established. | Documented, deferred to CI story |
| O5: No structured log on successful load | Low | Loader is a library, not a service. Logging is a runner concern. The runner can log persona metadata after `load()` returns. | Documented, deferred to STORY-003 |
| O6: Persona file not pinned to code version | Low | Meta-repo commit determines persona schema version. `version` field in frontmatter provides audit trail. | Documented |

**Assessment:** All 6 findings are appropriately addressed or deferred. The persona loader's responsibility ends at parsing, validation, and rendering. Runtime concerns (startup failure handling, logging, hot-reload signaling) belong to the runner (STORY-003) and engine (STORY-006).

---

## 3. Monitoring & Observability

### Current State (Library Module)

- **Test metrics:** 14 tests, 0.10s execution time, 100% pass rate
- **CI visibility:** Tests run in the full suite (114 tests total)
- **Smoke test equivalent:** `test_load_example_persona_file_from_repo` validates the shipped persona file

### Future State (After Integration with Runner)

Per ops-review.md O5, the runner should log:

| Signal | Source | Level |
|--------|--------|-------|
| Persona loaded successfully | Runner after `PersonaLoader.load()` | INFO |
| Persona load failed | Runner catching `PersonaError` | ERROR |
| Profile selected | Runner after `to_cli_args()` | DEBUG |
| Effective tool count | Runner after `to_cli_args()` | DEBUG |

---

## 4. Failure Modes

| Failure | Detection | Recovery |
|---------|-----------|----------|
| Persona file missing | `FileNotFoundError` at `load()` | Fix file path or create persona file |
| Malformed YAML frontmatter | `PersonaParseError` with line number hint | Fix YAML syntax in persona file |
| Missing required field | `PersonaValidationError` with field name + file path | Add missing field to persona file |
| Invalid field value (model, max_turns) | `PersonaValidationError` with expected values | Correct field value in persona file |
| Unknown tool name | `PersonaValidationError` listing known tools | Use tool name from KNOWN_TOOLS set |
| Unknown tool profile at render time | `PersonaProfileError` listing available profiles | Use valid profile name |
| Missing context variable at render time | `PersonaVariableError` listing available variables | Provide all required variables in context |
| max_turns override above ceiling | `PersonaValidationError` with ceiling value | Use override <= persona's max_turns |

All failures are deterministic, produce actionable error messages, and are recoverable by fixing input. No transient or infrastructure-dependent failures exist in this module.

---

## 5. Capacity & Scaling

- **Load time:** <1ms per persona file (file read + YAML parse + validation)
- **Memory:** Persona dataclass is small (~1KB). No caching, no accumulation.
- **Scaling model:** One `PersonaLoader.load()` call per agent startup. No concurrent access concerns (stateless loader).
- **Persona file count:** Currently 1 (`dev-agent-v1.md`). The system scales linearly — each persona is loaded independently.

---

## 6. Runbook Summary

### Adding a New Persona

1. Create `personas/<role>-v<N>.md` following the schema in `feature-spec.md` section 1
2. Run `python3 -c "from tech_dev_agents.persona import PersonaLoader; PersonaLoader().load('personas/<file>.md')"` to validate
3. Add a test in `tests/test_persona.py` that loads the new file (like `test_load_example_persona_file_from_repo`)
4. Commit to meta-repo

### Modifying an Existing Persona

1. Edit the persona file in `personas/`
2. Run `python3 -m pytest tests/test_persona.py -v` to verify the file still loads
3. If changing tool permissions: verify the new tool list is accepted by validation
4. Restart any running agents to pick up changes (no hot-reload in v1)

### Debugging Persona Load Failures

1. Check the error message — it includes the file path and specific field that failed
2. If `PersonaParseError`: fix YAML syntax (check for missing colons, incorrect indentation)
3. If `PersonaValidationError`: check the field value against the documented constraints
4. If `PersonaVariableError`: ensure the context dict includes all variables used in the template

---

## Verdict

APPROVED — No operational blockers. The persona system is a well-scoped library module with no runtime operational surface. All ops review findings are documented with actionable paths for downstream stories. The module is ready for integration with STORY-003 (Runner) and STORY-006 (SDLC Engine).
