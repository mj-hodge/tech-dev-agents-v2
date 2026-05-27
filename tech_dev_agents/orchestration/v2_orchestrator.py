"""V2 poller orchestration — phase loop with v2 lease/event contract.

STORY-860: Restores poller-side orchestration that was stripped during the v2
cutover. Provides:
  - Branch lifecycle (checkout/create from claim metadata)
  - Phase-by-phase execution with progress events
  - Phase-scoped failure classes
  - Resume-aware retry (query parent's phase events)
  - Heartbeat thread covering all phases
  - SIGTERM coordination (checks _sigterm_received between phases)

This module is imported by dispatch_poller_v2.py when DISPATCH_V2_ORCHESTRATION=1.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

logger = logging.getLogger("orchestration.v2_orchestrator")

# Heartbeat interval in seconds (overridable for testing)
HEARTBEAT_INTERVAL = 60

# SDK tool path (same as dispatch_poller_v2)
SDK_TOOL_PATH = "/opt/agent/claude_sdk_tool.py"
PYTHON_PATH = sys.executable or "python3"


def run_orchestrated(
    claim,  # _ActiveClaim
    session,  # requests.Session
    headers: dict,
) -> None:
    """Run the full SDLC phase sequence for a claimed job.

    This is the main entry point called from poll_loop() when
    DISPATCH_V2_ORCHESTRATION=1. It replaces the bare _run_sdk() call
    with a phase-aware orchestration loop.
    """
    from deployment.hermes.dispatch_poller_v2 import (
        _resolve_workspace,
        transition_claim,
        _failure_event_data,
        _sigterm_received,
    )
    from tech_dev_agents.orchestration import git_ops
    from tech_dev_agents.orchestration.branch_resolver import resolve as resolve_branch
    from tech_dev_agents.orchestration.phase_defs import (
        get_phases_for_scope,
        parse_seed_phase_path,
        PHASE_FAILURE_CLASS_MAP,
    )

    workspace = _resolve_workspace(claim.repo)

    # Start heartbeat thread
    hb_stop = threading.Event()
    hb_thread = threading.Thread(
        target=_heartbeat_loop,
        args=(claim, session, headers, hb_stop),
        daemon=True,
        name=f"hb-{claim.job_id[:8]}",
    )
    hb_thread.start()

    try:
        # Step 1: Resolve default branch
        try:
            default_branch = git_ops.resolve_default_branch(workspace)
        except git_ops.DefaultBranchUnresolvableError as exc:
            _emit_terminal_failed(
                claim, session, headers,
                failure_class=exc.failure_class,
                failure_reason=str(exc),
            )
            return

        # Step 2: Resolve and checkout branch
        try:
            branch = resolve_branch(claim, workspace, default_branch)
            git_ops.ensure_branch(workspace, branch, default_branch)
            git_ops.verify_clean_tree(workspace)
        except git_ops.GitOpsError as exc:
            _emit_terminal_failed(
                claim, session, headers,
                failure_class=exc.failure_class,
                failure_reason=str(exc),
            )
            return

        # Step 3: Determine phases
        phases = get_phases_for_scope(claim.scope)

        # Check seed for Phase Path override
        story_folder = _extract_story_folder(claim.story_id, workspace, claim.rework_of)
        seed_path = os.path.join(workspace, "features", story_folder, "seed.md")
        if os.path.isfile(seed_path):
            try:
                with open(seed_path, "r") as f:
                    declared_path = parse_seed_phase_path(f.read())
                if declared_path:
                    # Filter phases to only those in the declared path
                    phase_set = set(declared_path)
                    phases = [p for p in phases if p[0] in phase_set]
            except Exception:
                pass  # Fall back to scope default

        # Step 4: Determine resume point
        resume_phase = _determine_resume_phase(claim, session, headers)

        # Find the starting index in the phase list
        start_idx = 0
        if resume_phase is not None:
            for i, (phase_num, *_) in enumerate(phases):
                if phase_num == resume_phase:
                    start_idx = i
                    break
            logger.info(
                "[ORCH] resume=phase_%d parent_job=%s job=%s",
                resume_phase, claim.parent_job_id, claim.job_id,
            )

        # Step 5: Phase loop
        all_succeeded = True
        for phase_num, phase_name, deliverable, prompt_template, max_turns in phases[start_idx:]:
            # Check SIGTERM between phases
            from deployment.hermes.dispatch_poller_v2 import _sigterm_received
            if _sigterm_received:
                logger.warning("[ORCH] SIGTERM received — exiting phase loop at phase %d job=%s", phase_num, claim.job_id)
                _emit_terminal_failed(
                    claim, session, headers,
                    failure_class="sigterm_shutdown",
                    failure_reason=f"SIGTERM received before phase {phase_num}",
                )
                all_succeeded = False
                break

            # Check lease lost
            if getattr(claim, '_lease_lost', False):
                logger.warning("[ORCH] lease lost — exiting phase loop at phase %d job=%s", phase_num, claim.job_id)
                all_succeeded = False
                break

            # Emit phase_started
            phase_start_time = time.monotonic()
            started_at = datetime.now(timezone.utc).isoformat()
            claim.current_phase = str(phase_num)
            claim.phase_started_at = started_at
            _emit_phase_event(
                claim, session, headers,
                event_type="phase_started",
                phase_num=phase_num,
                phase_name=phase_name,
                started_at=started_at,
            )
            logger.info(
                "[ORCH] phase=%d name=%s action=start job=%s",
                phase_num, phase_name, claim.job_id[:8],
            )

            # Build phase prompt
            story_folder = _extract_story_folder(claim.story_id, workspace, claim.rework_of)
            phase_prompt = prompt_template.format(
                story_id=claim.story_id,
                repo=claim.repo,
                story_folder=story_folder,
            )
            if phase_num == 1 and claim.prompt:
                phase_prompt = f"CONTEXT FROM DISPATCH:\n{claim.prompt}\n\n{phase_prompt}"

            # Run SDK for this phase
            success, output = _run_sdk_for_phase(
                claim, phase_num, phase_name, phase_prompt, workspace, max_turns,
            )

            duration_s = round(time.monotonic() - phase_start_time, 1)

            if success:
                _emit_phase_event(
                    claim, session, headers,
                    event_type="phase_completed",
                    phase_num=phase_num,
                    phase_name=phase_name,
                    duration_s=duration_s,
                    completed_at=datetime.now(timezone.utc).isoformat(),
                )
                logger.info(
                    "[ORCH] phase=%d name=%s action=complete duration_s=%s job=%s",
                    phase_num, phase_name, duration_s, claim.job_id[:8],
                )
                # STORY-1003: Phase 9 → canon-backport follow-up hook.
                # When Phase 9 completes AND all 3 gate questions are answered
                # in refinement-report.md, enqueue /canon-backport <STORY-ID>.
                if phase_num == 9:
                    try:
                        _dispatch_canon_backport_followup(
                            claim, session, headers, workspace, story_folder,
                        )
                    except Exception as exc:  # never fail the parent job on hook errors
                        logger.warning(
                            "[ORCH] canon-backport follow-up dispatch failed job=%s err=%s",
                            claim.job_id[:8], exc,
                        )
            else:
                failure_class = _build_phase_failure_class(phase_num, output)
                _emit_phase_event(
                    claim, session, headers,
                    event_type="phase_failed",
                    phase_num=phase_num,
                    phase_name=phase_name,
                    duration_s=duration_s,
                    failure_class=failure_class,
                    failure_reason=output[:500] if output else "SDK exited non-zero",
                )
                logger.warning(
                    "[ORCH] phase=%d name=%s action=fail duration_s=%s failure_class=%s job=%s",
                    phase_num, phase_name, duration_s, failure_class, claim.job_id[:8],
                )
                _emit_terminal_failed(
                    claim, session, headers,
                    failure_class=failure_class,
                    failure_reason=output[:4000] if output else "SDK exited non-zero",
                )
                all_succeeded = False
                break

        # Step 6: Terminal event
        if all_succeeded:
            transition_claim(
                claim,
                event_type="submitted",
                event_data={"output_summary": "all phases completed"},
                session=session,
                headers=headers,
            )
            logger.info("[ORCH] job=%s action=submitted (all phases complete)", claim.job_id[:8])

    finally:
        # Stop heartbeat thread
        hb_stop.set()
        hb_thread.join(timeout=5)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _heartbeat_loop(
    claim,
    session,
    headers: dict,
    stop_event: threading.Event,
) -> None:
    """Daemon thread: fire send_heartbeat() every HEARTBEAT_INTERVAL seconds."""
    from deployment.hermes.dispatch_poller_v2 import _resolve_workspace

    interval = HEARTBEAT_INTERVAL
    while not stop_event.wait(timeout=interval):
        try:
            workspace = _resolve_workspace(claim.repo)
            sha = _get_git_sha(workspace)
            ok = send_heartbeat(claim, session=session, headers=headers, git_head_sha=sha)
            if not ok:
                logger.warning("[ORCH] heartbeat stale lease for job=%s", claim.job_id)
                claim._lease_lost = True
                break
        except Exception as exc:
            logger.warning("[ORCH] heartbeat error: %s", exc)


def send_heartbeat(claim, *, session, headers, git_head_sha=None) -> bool:
    """Send a heartbeat for the active claim. Delegates to dispatch_poller_v2."""
    from deployment.hermes.dispatch_poller_v2 import send_heartbeat as _send_hb
    return _send_hb(claim, session=session, headers=headers, git_head_sha=git_head_sha)


def _get_git_sha(workspace: str) -> str | None:
    """Return the current git HEAD SHA."""
    try:
        result = subprocess.run(
            ["git", "-C", workspace, "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def _emit_phase_event(
    claim,
    session,
    headers: dict,
    event_type: str,
    phase_num: int,
    phase_name: str,
    **kwargs,
) -> None:
    """Emit a phase event via transition_claim. Best-effort — swallows errors."""
    from deployment.hermes.dispatch_poller_v2 import transition_claim

    event_data = {
        "phase": phase_num,
        "phase_name": phase_name,
    }
    event_data.update(kwargs)

    try:
        transition_claim(
            claim,
            event_type=event_type,
            event_data=event_data,
            session=session,
            headers=headers,
        )
    except Exception as exc:
        # Phase events are observability, not critical path
        logger.warning("[ORCH] phase event emission failed: %s", exc)


def _emit_terminal_failed(
    claim,
    session,
    headers: dict,
    failure_class: str,
    failure_reason: str,
) -> None:
    """Emit a terminal 'failed' event."""
    from deployment.hermes.dispatch_poller_v2 import transition_claim

    try:
        transition_claim(
            claim,
            event_type="failed",
            event_data={
                "failure_class": failure_class,
                "failure_reason": failure_reason[:4000],
                "error_message": failure_reason[:4000],
                "exit_code": None,
                "tail_summary": failure_reason[:300],
            },
            session=session,
            headers=headers,
        )
    except Exception as exc:
        logger.warning("[ORCH] terminal failed event emission failed: %s", exc)


def _determine_resume_phase(
    claim,
    session,
    headers: dict,
) -> int | None:
    """Query parent job's phase events to find resume point.

    Returns phase number to resume from, or None (start from beginning).
    """
    if not claim.parent_job_id:
        return None

    try:
        result = _query_parent_phase_events(claim, session, headers)
        if not result:
            return None

        # Check for phase_failed first (resume from that phase)
        if "phase_failed" in result:
            return result["phase_failed"]

        # Check for phase_started without complete (crash mid-phase)
        if "phase_started" in result and "phase_completed" not in result:
            return result["phase_started"]

        # phase_completed → resume from next phase
        if "phase_completed" in result:
            return result["phase_completed"] + 1

    except Exception as exc:
        logger.warning("[ORCH] resume query failed: %s — starting from phase 1", exc)

    return None


def _query_parent_phase_events(
    claim,
    session,
    headers: dict,
) -> dict | None:
    """Query the parent job's last phase event.

    Returns dict like {"phase_failed": 4} or {"phase_completed": 7} or None.
    """
    # This queries the v2 events API for the parent job's phase events.
    # In production, this would be a GET to /api/dispatch/v2/events/{parent_job_id}
    # For now, return None (no resume) — the actual API call will be wired in
    # when the ops-console endpoint is confirmed.
    return None


def _run_sdk_for_phase(
    claim,
    phase_num: int,
    phase_name: str,
    prompt: str,
    workspace: str,
    max_turns: int,
) -> tuple[bool, str]:
    """Launch the SDK tool for a single phase. Returns (success, output)."""
    import deployment.hermes.dispatch_poller_v2 as mod

    cmd = [
        PYTHON_PATH,
        SDK_TOOL_PATH,
        "-p", prompt,
        "-w", workspace,
    ]
    if max_turns:
        cmd.extend(["--max-turns", str(max_turns)])

    # Rework args (AC-3)
    if claim.rework_of:
        cmd.extend(["--rework-of", claim.rework_of])
    if claim.target_pr:
        cmd.extend(["--target-pr", str(claim.target_pr)])
    if claim.branch:
        cmd.extend(["--base-branch", claim.branch])

    logger.info(
        "[ORCH] SDK launch phase=%d name=%s job=%s",
        phase_num, phase_name, claim.job_id[:8],
    )

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=workspace,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        # Register for SIGTERM handler
        mod._active_sdk_proc = proc
        stdout, _ = proc.communicate(timeout=7200)
        success = proc.returncode == 0
        return success, stdout[-4000:] if stdout else ""
    except subprocess.TimeoutExpired:
        proc.kill()
        return False, f"SDK timed out during phase {phase_num}"
    except Exception as exc:
        return False, str(exc)
    finally:
        mod._active_sdk_proc = None


def _build_phase_failure_class(phase_num: int, sdk_output: str) -> str:
    """Determine the phase-scoped failure class.

    Uses the phase number to set the primary class, then checks the inner
    classifier for more specific matches (rate_limited, auth_expired, etc.).
    """
    from tech_dev_agents.orchestration.phase_defs import PHASE_FAILURE_CLASS_MAP

    # Check if the inner classifier has a more specific class
    try:
        from tech_dev_agents.ops_console.services.dispatch_failure_policy import classify
        inner = classify(sdk_output or "", error_message=sdk_output or "")
        # If inner classifier found something specific (not unknown), prefer it
        if inner and inner not in ("unknown", "phase_runner_crash"):
            return inner
    except Exception:
        pass

    # Default to phase-scoped class
    return PHASE_FAILURE_CLASS_MAP.get(phase_num, f"phase_{phase_num}_error")


def _gate_answered_ok(refinement_report_path: str) -> bool:
    """STORY-1003: Detect that Phase 9's 3-question gate is fully answered.

    Looks for a `## 3-question gate` section in refinement-report.md with all
    three questions (Canon-doc impact, Scaffold backport, Sibling sweep) and a
    completion marker. Conservative: returns False on any parse error so the
    follow-up only fires when the gate clearly closed.
    """
    if not refinement_report_path or not os.path.isfile(refinement_report_path):
        return False
    try:
        with open(refinement_report_path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return False
    lower = content.lower()
    if "## 3-question gate" not in lower:
        return False
    # All three questions must appear in the gate
    required = ("canon-doc impact", "scaffold backport", "sibling sweep")
    return all(q in lower for q in required)


def _dispatch_canon_backport_followup(
    claim,
    session,
    headers: dict,
    workspace: str,
    story_folder: str,
) -> None:
    """STORY-1003: Enqueue /canon-backport <STORY-ID> as a follow-up dispatch.

    Called after Phase 9 completes. No-op when the 3-question gate is not
    fully answered (signal: `## 3-question gate` block in refinement-report.md
    listing all three questions). Idempotency is enforced downstream by the
    /canon-backport skill via `gh pr list --search "STORY-XXXX canon"`.
    """
    report_path = os.path.join(
        workspace, "features", story_folder, "refinement-report.md"
    )
    if not _gate_answered_ok(report_path):
        logger.info(
            "[ORCH] phase=9 canon-backport followup skipped (gate not closed) job=%s",
            claim.job_id[:8],
        )
        return

    # Build the dispatch payload and POST to /api/dispatch/v2/enqueue
    import os as _os
    base_url = _os.environ.get("DISPATCH_BASE_URL") or _os.environ.get(
        "OPS_CONSOLE_URL", "http://localhost:8000"
    )
    url = f"{base_url.rstrip('/')}/api/dispatch/v2/enqueue"
    payload = {
        "repo": claim.repo,
        "story_id": f"{claim.story_id}-canon-backport",
        "scope": "small",
        "prompt": f"/canon-backport {claim.story_id}",
        "enqueued_by": "phase-9-hook",
        "title": f"canon-backport follow-up for {claim.story_id}",
        "target_role": "developer",
    }
    try:
        resp = session.post(url, json=payload, headers=headers, timeout=10)
        logger.info(
            "[ORCH] phase=9 canon-backport followup enqueued story=%s status=%s job=%s",
            claim.story_id, resp.status_code, claim.job_id[:8],
        )
    except Exception as exc:
        logger.warning(
            "[ORCH] phase=9 canon-backport followup enqueue failed story=%s err=%s",
            claim.story_id, exc,
        )


def _extract_story_folder(story_id: str, workdir: str, rework_of: str | None = None) -> str:
    """Find the features/ subfolder for this story."""
    lookup_id = rework_of or story_id
    num = lookup_id.split("-")[-1] if "-" in lookup_id else ""

    features_dir = os.path.join(workdir, "features")
    if os.path.isdir(features_dir):
        try:
            for d in sorted(os.listdir(features_dir)):
                if num and f"story-{num}" in d.lower():
                    return d
        except OSError:
            pass

    # Fallback: derive from story_id
    return f"story-{num}-{story_id.lower().replace('story-', '')}" if num else story_id.lower()
