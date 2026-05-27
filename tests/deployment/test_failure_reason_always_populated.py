"""Contract test: every fail-emitting code path must populate failure_reason.

STORY-762: Audit and enforce that every transition to status='failed' in the
dispatch pipeline includes a non-empty, structured failure_reason string.

Root cause (2026-04-29 incident, STORY-007–STORY-017 batch):
- All 11 stories reached status='failed' with failure_reason=NULL.
- dispatch_poller._report_fail() sends {"exit_code": exit_code} to the API
  but NEVER includes "failure_reason" — the route and DB service already accept
  it, the caller just never sends it.
- sdlc_phase_runner.run_sdlc_phases() returns (False, None, None) on failure,
  discarding the error context that should become failure_reason.
- recover_stale_claims() Tier 2b SQL UPDATE sets status='failed' without
  failure_reason='agent_died'.

Test groups:
  TestEveryFailCallPassesReason         — POST body + call-site contract (SC-4)
  TestBranchSetupFailedPopulatesReason  — branch_setup_failed error flows (SC-3)
  TestPhaseTimeoutPopulatesReason       — phase-failure returns carry reason
  TestSdkDiedPopulatesReason            — _run_and_complete + DB Tier 2b
  TestFailureReasonTruncated            — 1000-char cap enforced (AC-11)
  TestNoCredentialsInFailureReason      — credential sanitization (security)

Design decisions:
- Pure-Python AST scan + hasattr. No DB, no subprocess, no network.
- Target: < 1 second total.
- Allowlist for intentional omissions — entries MUST have a `reason` field.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DISPATCH_POLLER = REPO_ROOT / "deployment" / "hermes" / "dispatch_poller.py"
SDLC_PHASE_RUNNER = REPO_ROOT / "deployment" / "hermes" / "sdlc_phase_runner.py"
DISPATCH_DB_SERVICE = (
    REPO_ROOT / "tech_dev_agents" / "ops_console" / "services" / "dispatch_db_service.py"
)

# ---------------------------------------------------------------------------
# Allowlist — intentional omissions (STORY-760 pattern)
# Each entry MUST have a `reason` field. Entries without `reason` are invalid.
# ---------------------------------------------------------------------------

ALLOWLIST: list[dict] = [
    # Example (uncomment if needed):
    # {
    #     "file": "dispatch_poller.py",
    #     "line": 123,
    #     "reason": "release path, not a failure path — no failure_reason needed",
    # },
]


def _allowed_lines(filename: str) -> set[int]:
    """Return the set of line numbers allowed to omit failure_reason for `filename`."""
    for entry in ALLOWLIST:
        assert "reason" in entry, (
            f"Allowlist entry for {entry.get('file')}:{entry.get('line')} "
            "is missing a `reason` field — every allowlist entry must explain why."
        )
    return {entry["line"] for entry in ALLOWLIST if entry.get("file") == filename}


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _read_and_parse(path: Path) -> tuple[str, ast.Module]:
    source = path.read_text()
    return source, ast.parse(source)


def _find_function(tree: ast.Module, name: str) -> ast.FunctionDef | None:
    """Find a top-level or nested FunctionDef by name."""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


# ---------------------------------------------------------------------------
# Group 1: TestEveryFailCallPassesReason
# ---------------------------------------------------------------------------


class TestEveryFailCallPassesReason:
    """SC-2 / SC-4: Contract enforcement — every fail path passes failure_reason.

    Two sub-checks:
    1. The _report_fail() POST body must contain a 'failure_reason' key.
    2. Every call site that invokes _report_fail() must pass error_message=.
    """

    def test_report_fail_post_body_includes_failure_reason(self):
        """_report_fail() in dispatch_poller.py must pass failure_reason in
        its POST body dict sent to /api/dispatch/fail/{story_id}.

        Currently RED: json={"exit_code": exit_code} — no failure_reason key.
        Fix: add failure_reason=_truncate_failure_reason("...", error_message or "")
             to the json= dict in the session.post() call inside _report_fail().
        """
        source, tree = _read_and_parse(DISPATCH_POLLER)

        func_node = _find_function(tree, "_report_fail")
        assert func_node is not None, (
            "dispatch_poller.py: _report_fail function not found — "
            "has it been renamed?"
        )

        violations = []
        for node in ast.walk(func_node):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr == "post"):
                continue
            # Only match calls whose first positional arg looks like a dispatch/fail URL
            url_arg = node.args[0] if node.args else None
            url_src = ""
            if url_arg is not None:
                try:
                    url_src = ast.get_source_segment(source, url_arg) or ""
                except Exception:
                    pass
            if "dispatch/fail" not in url_src:
                continue

            # Found a session.post(...) to /dispatch/fail — check the json= kwarg
            json_kw = next(
                (kw for kw in node.keywords if kw.arg == "json"), None
            )
            if json_kw is None:
                violations.append(
                    f"dispatch_poller.py:{node.lineno}: session.post() to "
                    f"dispatch/fail has no json= kwarg at all"
                )
                continue

            if isinstance(json_kw.value, ast.Dict):
                keys = [
                    k.value
                    for k in json_kw.value.keys
                    if isinstance(k, ast.Constant)
                ]
                if "failure_reason" not in keys:
                    violations.append(
                        f"dispatch_poller.py:{node.lineno}: POST body to "
                        f"dispatch/fail is missing 'failure_reason' key "
                        f"(current keys: {keys})"
                    )
            else:
                # Dynamic dict — acceptable only if source contains failure_reason
                body_src = ast.get_source_segment(source, json_kw.value) or ""
                if "failure_reason" not in body_src:
                    violations.append(
                        f"dispatch_poller.py:{node.lineno}: POST body dict "
                        f"does not appear to include 'failure_reason'"
                    )

        assert not violations, (
            "Violation: dispatch_poller.py _report_fail() POST body must include "
            "'failure_reason':\n"
            + "\n".join(f"  → {v}" for v in violations)
            + "\nFix: add failure_reason=_truncate_failure_reason('prefix', error_message or '') "
            "to the json= dict."
        )

    def test_every_report_fail_call_has_error_message(self):
        """Every _report_fail() call site in dispatch_poller.py must pass
        error_message= so the failure context reaches the POST body.

        Currently RED: multiple call sites (branch_setup_failed fall-through,
        rate-limit fallback, genuine-failure else-branch, legacy finally block)
        all omit error_message.
        Fix: collect the error at each site and pass error_message=<string>.
        """
        source, tree = _read_and_parse(DISPATCH_POLLER)
        allowed = _allowed_lines("dispatch_poller.py")

        violations = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Name) and func.id == "_report_fail"):
                continue
            if node.lineno in allowed:
                continue
            kwarg_names = {kw.arg for kw in node.keywords if kw.arg}
            if "error_message" not in kwarg_names:
                src_line = source.splitlines()[node.lineno - 1].strip()
                violations.append(
                    f"dispatch_poller.py:{node.lineno}: {src_line!r} "
                    f"— missing error_message= kwarg "
                    f"(current kwargs: {sorted(kwarg_names)})"
                )

        assert not violations, (
            f"Found {len(violations)} _report_fail() call(s) without error_message=:\n"
            + "\n".join(f"  Violation: {v}" for v in violations)
            + "\nPass error_message='<prefix>: <detail>'[:1000] at each call site.\n"
            "Add to ALLOWLIST with a `reason` if a site is genuinely exempt."
        )


# ---------------------------------------------------------------------------
# Group 2: TestBranchSetupFailedPopulatesReason
# ---------------------------------------------------------------------------


class TestBranchSetupFailedPopulatesReason:
    """SC-3: The branch_setup_failed handler must not discard the error string."""

    def test_branch_setup_failed_returns_error_string(self):
        """sdlc_phase_runner.py's branch_setup_failed except-block must NOT
        return (False, None, None) — str(exc) must be included in the return.

        Currently RED: line 2411 returns (False, None, None), discarding the
        RuntimeError message that says WHY git failed (e.g. 'pathspec main did
        not match any file').
        Fix: return (False, None, f"branch_setup_failed: {str(exc)[:500]}")
        """
        source, tree = _read_and_parse(SDLC_PHASE_RUNNER)
        lines = source.splitlines()

        violations = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Return):
                continue
            val = node.value
            if not isinstance(val, ast.Tuple):
                continue
            elts = val.elts
            # Only care about 3-element tuples (False, None, None)
            if len(elts) != 3:
                continue
            if not (
                isinstance(elts[0], ast.Constant) and elts[0].value is False
                and isinstance(elts[1], ast.Constant) and elts[1].value is None
                and isinstance(elts[2], ast.Constant) and elts[2].value is None
            ):
                continue

            # Check backward context for branch_setup_failed
            lineno = node.lineno
            context_start = max(0, lineno - 15)
            context = "\n".join(lines[context_start:lineno])
            if "branch_setup_failed" in context:
                src_line = lines[lineno - 1].strip()
                violations.append(
                    f"sdlc_phase_runner.py:{lineno}: {src_line!r} "
                    f"— branch_setup_failed handler returns (False, None, None), "
                    f"discarding the error string from RuntimeError"
                )

        assert not violations, (
            "branch_setup_failed must propagate the error to the caller:\n"
            + "\n".join(f"  Violation: {v}" for v in violations)
            + '\nFix: return (False, None, f"branch_setup_failed: {str(exc)[:500]}")'
        )


# ---------------------------------------------------------------------------
# Group 3: TestPhaseTimeoutPopulatesReason
# ---------------------------------------------------------------------------


class TestPhaseTimeoutPopulatesReason:
    """Phase-failure returns (rc != 0, timeout, rate-limit) must carry a reason."""

    def test_phase_timeout_return_includes_failure_reason(self):
        """In sdlc_phase_runner.py, every (False, None, None) return within a
        phase-failure context (near rc != 0 / TIMEOUT / FAILED / rate_limited)
        must be replaced with a return that includes a failure reason string.

        Currently RED: lines 2643 (rate_limited path) and 2652 (rc != 0 path)
        return (False, None, None) — the phase number and error code are lost.
        Fix: return (False, None, f"phase_{phase_num}_failed: rc={rc} {tail[:200]}")
        """
        source, tree = _read_and_parse(SDLC_PHASE_RUNNER)
        lines = source.splitlines()

        # Keywords that identify a phase-failure context
        _PHASE_FAILURE_MARKERS = (
            "rc != 0",
            "TIMEOUT",
            "FAILED",
            "rate_limited",
            "TimeoutExpired",
            "rc == -1",
            "rc == -429",
        )

        violations = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Return):
                continue
            val = node.value
            if not isinstance(val, ast.Tuple):
                continue
            elts = val.elts
            if len(elts) != 3:
                continue
            if not (
                isinstance(elts[0], ast.Constant) and elts[0].value is False
                and isinstance(elts[1], ast.Constant) and elts[1].value is None
                and isinstance(elts[2], ast.Constant) and elts[2].value is None
            ):
                continue

            lineno = node.lineno
            context_start = max(0, lineno - 20)
            context = "\n".join(lines[context_start:lineno])
            is_phase_failure = any(marker in context for marker in _PHASE_FAILURE_MARKERS)
            if is_phase_failure:
                src_line = lines[lineno - 1].strip()
                violations.append(
                    f"sdlc_phase_runner.py:{lineno}: {src_line!r} "
                    f"returns (False, None, None) — failure reason discarded "
                    f"(context: phase-failure keywords found in prior 20 lines)"
                )

        assert not violations, (
            f"Found {len(violations)} phase-failure return(s) discarding the reason:\n"
            + "\n".join(f"  Violation: {v}" for v in violations)
            + "\nFix: include failure reason as 3rd tuple element.\n"
            'Example: return (False, None, f"phase_{phase_num}_failed: rc={rc} {tail[:200]}")'
        )


# ---------------------------------------------------------------------------
# Group 4: TestSdkDiedPopulatesReason
# ---------------------------------------------------------------------------


class TestSdkDiedPopulatesReason:
    """When SDK exits non-zero, the failure reason must reach the /fail API."""

    def test_sdk_died_report_fail_has_error_message(self):
        """Inside _run_and_complete in dispatch_poller.py, every _report_fail()
        call must include error_message= so failure_reason reaches the DB.

        Currently RED: the 'genuine failure' else-branch (not rate-limited, not
        needs_info, not already_done) calls _report_fail() with no error_message.
        Fix: capture phase_reason or build error_msg and pass error_message=.
        """
        source, tree = _read_and_parse(DISPATCH_POLLER)

        # _run_and_complete is a nested function inside start_story
        run_and_complete: ast.FunctionDef | None = None
        for top in ast.walk(tree):
            if not isinstance(top, ast.FunctionDef):
                continue
            for child in ast.walk(top):
                if (
                    isinstance(child, ast.FunctionDef)
                    and child.name == "_run_and_complete"
                ):
                    run_and_complete = child
                    break
            if run_and_complete is not None:
                break

        # Also try top-level
        if run_and_complete is None:
            run_and_complete = _find_function(tree, "_run_and_complete")

        assert run_and_complete is not None, (
            "_run_and_complete function not found in dispatch_poller.py"
        )

        violations = []
        for node in ast.walk(run_and_complete):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Name) and func.id == "_report_fail"):
                continue
            kwarg_names = {kw.arg for kw in node.keywords if kw.arg}
            if "error_message" not in kwarg_names:
                src_line = source.splitlines()[node.lineno - 1].strip()
                violations.append(
                    f"dispatch_poller.py:{node.lineno}: _report_fail() in "
                    f"_run_and_complete missing error_message — "
                    f"failure_reason=NULL will reach DB "
                    f"(current kwargs: {sorted(kwarg_names)})"
                )

        assert not violations, (
            f"Found {len(violations)} _report_fail() call(s) in _run_and_complete "
            "without error_message=:\n"
            + "\n".join(f"  Violation: {v}" for v in violations)
        )

    def test_recover_stale_claims_sets_agent_died_reason(self):
        """recover_stale_claims() Tier 2b SQL UPDATE in dispatch_db_service.py
        must set failure_reason='agent_died' in the UPDATE SET clause.

        Tier 2b: stale_release_count >= 3 → transition directly to status=failed.
        The fail() method is NOT called, so failure_reason must be in the raw SQL.

        Currently RED: the UPDATE only sets status, claim_heartbeat_at, updated_at.
        Fix: add failure_reason = 'agent_died' to the Tier 2b UPDATE SET clause.
        """
        source, _ = _read_and_parse(DISPATCH_DB_SERVICE)
        lines = source.splitlines()

        tier2b_marker = "stale_release_count >= 3"
        idx = source.find(tier2b_marker)
        assert idx != -1, (
            f"dispatch_db_service.py: could not find Tier 2b marker "
            f"'{tier2b_marker}' — has recover_stale_claims() been refactored?"
        )

        # Look in the UPDATE statement that CONTAINS this WHERE clause
        # (scan up to 30 lines before the marker)
        lineno = source[:idx].count("\n")
        context_start = max(0, lineno - 30)
        context = "\n".join(lines[context_start : lineno + 3])

        assert "failure_reason" in context, (
            f"dispatch_db_service.py recover_stale_claims() Tier 2b SQL UPDATE "
            f"(around line {lineno}) does not include failure_reason in its SET clause.\n"
            f"Context (lines {context_start}–{lineno}):\n"
            + "\n".join(f"  {lines[context_start + i]}" for i in range(min(30, lineno - context_start)))
            + "\nFix: add failure_reason = 'agent_died' to the SET clause so "
            "heartbeat-stale failures are classified in the DB."
        )


# ---------------------------------------------------------------------------
# Group 5: TestFailureReasonTruncated
# ---------------------------------------------------------------------------


class TestFailureReasonTruncated:
    """AC-11: failure_reason must be capped at 1000 characters."""

    def test_failure_reason_truncated_to_1000_chars(self):
        """A helper (_truncate_failure_reason or _build_failure_reason) must
        exist in dispatch_poller.py and cap failure_reason at 1000 chars.

        Currently RED: no such helper exists — long git stderr / SDK output
        would produce oversized failure_reason strings in the DB.
        Fix: add def _truncate_failure_reason(prefix: str, detail: str) -> str:
                 return f'{prefix}: {detail}'[:1000]
        """
        sys.path.insert(0, str(REPO_ROOT / "deployment" / "hermes"))
        import dispatch_poller  # noqa: PLC0415

        has_helper = hasattr(dispatch_poller, "_truncate_failure_reason") or hasattr(
            dispatch_poller, "_build_failure_reason"
        )
        assert has_helper, (
            "dispatch_poller.py is missing a failure_reason builder/truncator.\n"
            "Add: def _truncate_failure_reason(prefix: str, detail: str) -> str:\n"
            "         return f'{prefix}: {detail}'[:1000]\n"
            "(AC-11: failure_reason must be ≤1000 chars)"
        )

        fn = getattr(
            dispatch_poller,
            "_truncate_failure_reason",
            getattr(dispatch_poller, "_build_failure_reason", None),
        )
        assert fn is not None

        long_detail = "x" * 2000
        result = fn("sdk_died", long_detail)
        assert isinstance(result, str), (
            f"_truncate_failure_reason must return str, got {type(result).__name__}"
        )
        assert len(result) <= 1000, (
            f"failure_reason must be ≤1000 chars per AC-11, "
            f"got {len(result)} chars for a 2000-char input"
        )

    def test_failure_reason_nonempty_on_common_prefixes(self):
        """Common taxonomy prefixes must produce non-empty structured strings.

        Currently RED: helper doesn't exist yet.
        Fix: implement _truncate_failure_reason(prefix, detail).
        """
        sys.path.insert(0, str(REPO_ROOT / "deployment" / "hermes"))
        import dispatch_poller  # noqa: PLC0415

        fn = getattr(
            dispatch_poller,
            "_truncate_failure_reason",
            getattr(dispatch_poller, "_build_failure_reason", None),
        )
        assert fn is not None, (
            "Need _truncate_failure_reason or _build_failure_reason in dispatch_poller.py"
        )

        for prefix in (
            "branch_setup_failed",
            "sdk_died",
            "phase_7_failed",
            "quota_exceeded",
        ):
            result = fn(prefix, "some detail message")
            assert result, (
                f"Empty failure_reason string for prefix {prefix!r} — "
                "helper must return a non-empty string"
            )
            assert prefix in result, (
                f"Prefix {prefix!r} not found in result {result!r} — "
                "structured prefix must appear in the failure_reason string"
            )


# ---------------------------------------------------------------------------
# Group 6: TestNoCredentialsInFailureReason
# ---------------------------------------------------------------------------


class TestNoCredentialsInFailureReason:
    """Security: failure_reason strings must not expose credentials or tokens."""

    def test_no_credentials_in_failure_reason(self):
        r"""The failure_reason building function must strip credential-like
        patterns (API_KEY=value, Bearer <token>) from the detail string.

        Currently RED: no helper / sanitizer exists in dispatch_poller.py.
        Fix: in _truncate_failure_reason or _build_failure_reason, apply
        re.sub(r'[A-Z_]{6,}=[^\s]{4,}', '<redacted>', detail) or similar
        before constructing the string.
        """
        sys.path.insert(0, str(REPO_ROOT / "deployment" / "hermes"))
        import dispatch_poller  # noqa: PLC0415

        fn = getattr(
            dispatch_poller,
            "_sanitize_failure_reason",
            getattr(
                dispatch_poller,
                "_truncate_failure_reason",
                getattr(dispatch_poller, "_build_failure_reason", None),
            ),
        )
        assert fn is not None, (
            "dispatch_poller.py must export a failure_reason builder/sanitizer.\n"
            "Fix: add _truncate_failure_reason(prefix, detail) that strips "
            "credential patterns (e.g. ENV_VAR=secret, Bearer <token>) "
            "from the detail string before including it in failure_reason."
        )

        # Simulate a git stderr containing an env-var-like credential pattern
        dirty_detail = "OPS_CONSOLE_API_KEY=s3cr3t_value fatal: authentication failed"

        try:
            # Two-arg form: _truncate_failure_reason(prefix, detail)
            result = fn("sdk_died", dirty_detail)
        except TypeError:
            # One-arg form: _sanitize_failure_reason(detail)
            result = fn(dirty_detail)

        assert "s3cr3t_value" not in result, (
            f"Credential value 's3cr3t_value' must be redacted from failure_reason.\n"
            f"Got: {result!r}\n"
            f"Fix: strip ENV_VAR=<value> patterns before including stderr/output "
            "in failure_reason (security constraint in seed.md)."
        )
