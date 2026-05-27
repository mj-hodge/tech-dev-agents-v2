# Security Review: Persona System (STORY-005)

> Phase 6b — Security Review
> Date: 2026-03-26
> Story: STORY-005
> Reviewer: Security Subagent

---

## Findings

### FINDING-S1: Variable Value Injection — No Sanitization (Severity: Medium)

The `inject_variables` function substitutes `{{var}}` with caller-provided string values without any sanitization. A variable value such as `repo_name` could be supplied from an untrusted source (e.g., a task tracker API response, a git remote URL) and could contain:

- Prompt injection payloads: `"my-app\n\n## Override\nIgnore all previous instructions."`
- Markdown structure breakers: headings, rule separators that restructure the system prompt
- `}}` sequences: single-pass regex prevents double-substitution, but `}}` injected into the body is still benign YAML-in-body context — no YAML re-parse occurs, so this is low risk

**Mitigation:** Add a sanitizer that strips or escapes leading `#` (markdown headings) and `---` (YAML/HR delimiters) from the start of variable values before injection. For values expected to be path-like or identifier-like (`repo_name`, `story_id`, `branch_name`), validate the value against a allowlist pattern before passing to `inject_variables`. Document which variables are "trusted" (internal orchestrator-set) vs "externally sourced" (from task tracker API).

---

### FINDING-S2: Path Traversal in PersonaLoader.load() (Severity: Medium)

`PersonaLoader.load()` accepts an arbitrary `str | Path` argument and reads it directly. If the caller constructs the path from external input (e.g., a URL parameter, a Monday.com task field containing a persona name), a value like `../../etc/passwd` or `../CLAUDE.md` would be read without restriction.

**Mitigation:** Enforce that the resolved `persona_path` lies within an allowed base directory (e.g., the `personas/` directory). Implement a path canonicalization check:

```python
allowed_base = Path("personas").resolve()
resolved = Path(persona_path).resolve()
if not str(resolved).startswith(str(allowed_base)):
    raise PersonaValidationError(f"Persona path outside allowed directory: {persona_path}")
```

This is especially important once STORY-006 (Execution Engine) passes task-tracker-derived values to the loader.

---

### FINDING-S3: Tool Permission Escalation via tool_profiles (Severity: Low-Medium)

The `tool_profiles` map is optional, but there is no constraint preventing a persona author from defining a profile named `admin` with `Bash(*)` (unrestricted bash). The caller chooses the profile by name. If the profile selection logic in STORY-003/006 is driven by external input (e.g., a task field set to `admin`), a misconfigured or malicious task could escalate tool permissions beyond what the default profile allows.

**Mitigation:** Add a validation rule that no tool profile may grant more permissions than an explicit allowlist defined per deployment. As a minimum for v1: validate that all entries in `tool_profiles` values pass the same balanced-parentheses and non-wildcard checks as `allowed_tools`. Document that `Bash(*)` is explicitly not supported and add a validation error for it. The runner (STORY-003) should also validate the profile name against a deployment-level allowlist, not accept it blindly from task input.

---

### FINDING-S4: Prompt Injection via behavioral_rules (Severity: Low)

`behavioral_rules` are appended to the system prompt as a numbered markdown section. A rule containing a markdown heading or an adversarial instruction (e.g., `"1. Ignore all rules above"`) could undermine the intent of other rules. Since persona files require meta-repo write access to modify, this is primarily a defense-in-depth concern.

**Mitigation:** Validate that no `behavioral_rules` entry starts with a markdown heading character (`#`) or contains the substring `ignore` in a case-insensitive check combined with `above` or `previous`. This is a lightweight heuristic gate, not a hard security boundary — the real control is repo access.

---

### FINDING-S5: `yaml.safe_load` is Used Correctly (Severity: None — Positive Finding)

The spec correctly uses `yaml.safe_load()` rather than `yaml.load()`. This prevents arbitrary Python object instantiation from malformed YAML. No issue.

---

### FINDING-S6: `\w+` Regex Restricts Variable Key Names (Severity: None — Positive Finding)

The injection pattern `\{\{(\w+)\}\}` restricts variable names to `[a-zA-Z0-9_]`. An attacker cannot inject a key like `{{../../secret}}` or `{{name; rm -rf}}`. The attack surface is the variable *value*, not the key. Covered by FINDING-S1.

---

## Summary

| ID | Issue | Severity | Status |
|----|-------|----------|--------|
| S1 | Variable values not sanitized before injection | Medium | Needs mitigation |
| S2 | Path traversal in persona file loading | Medium | Needs mitigation |
| S3 | Tool profile escalation via external profile name input | Low-Med | Needs mitigation |
| S4 | Behavioral rules could contain adversarial markdown | Low | Defense-in-depth |
| S5 | yaml.safe_load used correctly | None | Pass |
| S6 | Variable key regex restricts injection vectors | None | Pass |

## Recommendation

The design is sound for a single-developer meta-repo where write access is tightly controlled. Before STORY-006 (Execution Engine) passes any externally-sourced values (task tracker fields, git remote names) into the persona loader, implement S1 (value sanitization) and S2 (path confinement). S3 (profile escalation) should be addressed in STORY-003/006 as a runner-level control. The persona system itself is not the primary security boundary — repo access control is — but the mitigations above eliminate the most exploitable gaps.
