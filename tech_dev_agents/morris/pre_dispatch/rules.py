"""Declarative rule sets for the pre-dispatch validator.

Adding a new required section to a seed is a change to the dispatch contract
for every story in the fleet — coordinate before touching this file.
"""

from __future__ import annotations

# Canonical key → list of accepted heading aliases (case-insensitive, substring match).
REQUIRED_SECTIONS: dict[str, tuple[str, ...]] = {
    "problem":              ("problem",),
    "goal":                 ("goal",),
    "success_criteria":     ("success criteria", "acceptance criteria"),
    "files_to_modify":      ("files to modify", "files modified"),
    "files_to_not_modify":  ("files to not modify", "files not to modify"),
    "verification_plan":    ("verification plan",),
    "boundaries":           ("boundaries",),
    "done_looks_like":      ("done looks like",),
    "escalation_contract":  ("escalation contract", "escalation"),
}

# Additional rules applied when scope ∈ {medium, large, new_project}.
MEDIUM_PLUS_SCOPES: frozenset[str] = frozenset({"medium", "large", "new_project", "new-project"})

# Additional rules applied when build_type == "pipeline" (or repo matches the
# pipeline regex set used as the inference fallback while STORY-1001 is in flight).
PIPELINE_REPO_PATTERNS: tuple[str, ...] = (
    "-v2",                 # walmart-supplier-v2 etc.
    "api-advertising-amazon",
)

# Substrings expected in pipeline seeds.
PIPELINE_REQUIRED_REFS: tuple[str, ...] = (
    "gc-data-v2/sources/",
    "gc-data-v2/platform/",
)

# Verification-plan must contain at least one of these markers on Medium+ stories.
VERIFICATION_COMMAND_MARKERS: tuple[str, ...] = (
    "pytest",
    "curl",
    "gh ",     # gh CLI invocation
    "```bash",
    "```sh",
    "```shell",
)

# Required payload fields enforced by validate_dispatch_seed() (STORY-1009).
DISPATCH_PAYLOAD_REQUIRED_FIELDS: tuple[str, ...] = (
    "do_not_do",
    "verification_plan",
    "red_test_paths",
    "acceptance_criteria",
)

# Extra required field for the /v2/rework endpoint.
REWORK_PAYLOAD_REQUIRED_FIELDS: tuple[str, ...] = (
    "failure_list",
)
