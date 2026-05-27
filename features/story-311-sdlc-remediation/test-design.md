# Test Design — STORY-311: SDLC Remediation

**Phase:** 7 — Test Design
**Story:** STORY-311
**Date:** 2026-04-16
**Scope:** Medium

---

## Overview

STORY-311 is a documentation-only story. There is no application code to unit-test or integration-test. Verification consists of confirming that all required deliverable files exist on the correct branches, are non-empty, and contain the required top-level sections.

---

## Verification Approach

### Method: `git ls-tree` file-existence checks

For each target branch, use `git ls-tree --name-only <branch> -- <path>` to verify a file exists in the tree. This is branch-safe (does not require checkout) and can be run in CI.

### Required sections check

Each markdown deliverable must contain:
- A `# Title` heading
- A `**Story:**` or equivalent metadata line
- A `## Summary` or `## Overview` or `## Problem` section
- A verdict or conclusion section

---

## Test Cases

### TC-1: story-304/retry — predeploy-gate.md exists

```bash
git ls-tree --name-only story-304/retry -- features/story-304-event-driven-presence/predeploy-gate.md
# Expected: "features/story-304-event-driven-presence/predeploy-gate.md"
```

### TC-2: story-305/fix-cost-display — story-305 folder complete

```bash
for f in seed.md analysis.md feature-spec.md security-review.md ux-review.md ops-review.md test-design.md code-review.md predeploy-gate.md; do
  git ls-tree --name-only story-305/fix-cost-display -- "features/story-305-fix-cost-display/$f"
done
# Expected: each file path echoed back (9 lines)
```

### TC-3: story-305/fix-cost-display — story-229 gaps filled

```bash
for f in security-review.md ux-review.md ops-review.md; do
  git ls-tree --name-only story-305/fix-cost-display -- "features/story-229-bot-teams-messaging/$f"
done
# Expected: each file path echoed back (3 lines)
```

### TC-4: story-311/sdlc-remediation — own deliverables present

```bash
for f in seed.md analysis.md feature-spec.md security-review.md ux-review.md ops-review.md test-design.md code-review.md predeploy-gate.md bundling-rationale.md; do
  git ls-tree --name-only story-311/sdlc-remediation -- "features/story-311-sdlc-remediation/$f"
done
# Expected: each file path echoed back (10 lines)
```

### TC-5: No empty files

```bash
# On each branch, verify file sizes > 0 for all new deliverables
git -C . show story-304/retry:features/story-304-event-driven-presence/predeploy-gate.md | wc -c
# Expected: > 100 bytes
```

---

## Pass Criteria

All `git ls-tree` queries return the expected file paths. All file size checks return > 100 bytes. No test requires running application code.

---

---

## Path Traversal Fix — terminal_guard.py (PR #38 review feedback)

### Vulnerability

The safe-read carve-out in `check_command()` allows `cat`/`head`/`tail` on operational paths (`/home/hermes/state/`, `/home/hermes/.hermes/`, `/var/log/`, `/tmp/`). A `..` path traversal bypasses the `startswith` check:

```
cat /home/hermes/state/../dev/repo/secret.py
```

Raw path starts with `/home/hermes/state/` → safe-read = True → deny pattern skipped → **source file readable**.

### Fix

`os.path.normpath(rest)` before the `startswith` check. Normalizes `../` segments so the path resolves to its true location.

### Test Cases (pytest — tests/deployment/test_terminal_guard.py::TestPathTraversal)

| ID | Command | Expected | Validates |
|----|---------|----------|-----------|
| PT-1 | `cat /home/hermes/state/../dev/repo/secret.py` | DENIED | Traversal out of /state/ to .py source |
| PT-2 | `cat /home/hermes/.hermes/../../dev/repo/app.py` | DENIED | Traversal out of /.hermes/ to .py source |
| PT-3 | `cat /var/log/../../home/hermes/dev/repo/config.json` | DENIED | Traversal out of /var/log/ to .json source |
| PT-4 | `cat /tmp/../home/hermes/dev/repo/main.py` | DENIED | Traversal out of /tmp/ to .py source |
| PT-5 | `cat /home/hermes/state/../../dev/repo/secret.yaml` | DENIED | Double traversal to .yaml source |
| PT-6 | `cat /home/hermes/state/dispatch.json` (+ head, tail, cat on safe paths) | ALLOWED | Legitimate safe reads unaffected |
| PT-7 | `head -20 /home/hermes/state/../dev/repo/secret.py` | DENIED | head with traversal |
| PT-8 | `tail -50 /var/log/../../home/hermes/dev/repo/schema.sql` | DENIED | tail with traversal |

---

## Notes

- Documentation verification (TC-1 through TC-5): manual `git ls-tree` checks
- Path traversal tests (PT-1 through PT-8): automated pytest in `tests/deployment/test_terminal_guard.py::TestPathTraversal`
- All 96 tests GREEN after fix
