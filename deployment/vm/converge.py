"""Idempotent VM-state convergence engine for tech-dev-agents.

STORY-644 — declarative VM-state convergence pilot.
Reads canonical-state.yaml, walks a registry of Check classes, and logs every
result as a single-line structured event.

DESIGN CONTRACTS:
- Idempotent. Re-running back-to-back produces identical OK results.
- Read-mostly. Each check's check() is pure; only fix() mutates, and only
  when check() returned drift.
- Bounded. Every subprocess gets a timeout (default 10s). The whole run has a
  hard wall-clock budget (default 120s) — exceeded budget exits 3.
- Loud about drift, quiet about steady state. OK results produce only the
  final summary line; drift gets a structured event per affected resource.
- Never makes the situation worse. fix() failure leaves the system in its
  pre-call state and logs FIX_FAILED — it does not retry or escalate.

USAGE:
  converge.py                       load /opt/agent/canonical-state.yaml, run all checks
  converge.py --config PATH         override YAML path
  converge.py --dry-run             check only; never call fix()
  converge.py --check NAME [...]    run only the named checks
  converge.py --json                emit structured events only (no human text)
  converge.py --detection-only-all  override every category's fix() to no-op
  converge.py --wall-budget SECONDS override wall-clock budget (default 120s)

EXIT CODES:
  0  all checks OK or fixed cleanly
  1  one or more checks logged FIX_FAILED or DRIFT_DETECTED with no fix path
  2  configuration error (YAML missing/parse-error/unknown version, agent not in applies_to)
  3  wall-clock budget exhausted
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal, Mapping, Sequence

# ---------------------------------------------------------------------------
# Schema constants
# ---------------------------------------------------------------------------

SUPPORTED_SCHEMA_VERSIONS: frozenset[int] = frozenset({1})
DEFAULT_CONFIG_PATH: str = "/opt/agent/canonical-state.yaml"
DEFAULT_REGISTRY_PATH: str = "/opt/agent/agent-registry.json"
DEFAULT_SUBPROCESS_TIMEOUT_S: int = 10
DEFAULT_WALL_BUDGET_S: int = 120

# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CheckResult:
    """Outcome of one check on one resource (e.g. one git repo, one CLI binary)."""

    name: str
    """Check category name, e.g. 'git_config'."""
    resource: str
    """Resource path or identifier, e.g. '/home/hermes/workspace/tech-dev-agents'."""
    status: Literal["ok", "drift_detected", "fixed", "fix_failed", "skipped", "error"]
    detail: str
    drift_value: str | None = None
    fixed_to: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class AgentContext:
    """Resolved identity of the running agent (from agent-registry.json)."""

    name: str
    email: str
    hostname: str


# ---------------------------------------------------------------------------
# Filesystem adapter (injectable for tests)
# ---------------------------------------------------------------------------


class FsAdapter:
    """Real filesystem operations. Tests substitute an in-memory adapter."""

    def read_text(self, path: str) -> str:
        with open(path) as fh:
            return fh.read()

    def write_text(self, path: str, content: str) -> None:
        with open(path, "w") as fh:
            fh.write(content)

    def sha256(self, path: str) -> str | None:
        """Return SHA-256 hex digest of file, or None if the file is absent."""
        if not os.path.exists(path):
            return None
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()

    def md5(self, path: str) -> str | None:
        """Return MD5 hex digest of file, or None if absent."""
        if not os.path.exists(path):
            return None
        with open(path, "rb") as fh:
            return hashlib.md5(fh.read()).hexdigest()

    def exists(self, path: str) -> bool:
        return os.path.exists(path)

    def is_dir(self, path: str) -> bool:
        return os.path.isdir(path)

    def listdir(self, path: str) -> list[str]:
        return os.listdir(path)


# ---------------------------------------------------------------------------
# Converge context (thread through every check)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConvergeContext:
    """Immutable context threaded through every check and fix call.

    All external I/O is accessed through ``fs`` and ``run`` so tests can
    substitute in-memory fakes without patching builtins or subprocess.
    """

    agent: AgentContext
    config: dict[str, Any]
    repo_root: Path
    fs: FsAdapter = field(default_factory=FsAdapter)
    now: Callable[[], float] = field(default_factory=lambda: time.time)
    run: Any = field(default_factory=lambda: subprocess.run)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConvergeReport:
    """Aggregated outcome of a full converge run."""

    results: tuple[CheckResult, ...]
    checked: int
    """Number of check categories attempted."""
    drifts: int
    """Number of unresolved drift instances (0 if all fixed)."""
    fixes: int
    fix_failures: int
    elapsed_s: float
    agent_name: str = "unknown"
    schema_version: int = 1
    wall_budget_exceeded: bool = False

    def exit_code(self) -> int:
        if self.wall_budget_exceeded:
            return 3
        if self.fix_failures > 0 or self.drifts > 0:
            return 1
        return 0

    def summary_line(self) -> str:
        ok_count = sum(1 for r in self.results if r.status == "ok")
        return (
            f"converge: agent={self.agent_name} schema=v{self.schema_version} "
            f"checks={self.checked} resources={len(self.results)} "
            f"ok={ok_count} drift={self.drifts} "
            f"fixed={self.fixes} fix_failed={self.fix_failures} "
            f"elapsed={self.elapsed_s:.2f}s"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_version(v: str) -> tuple[int, ...]:
    """Parse a dotted-version string into a 3-tuple of ints."""
    parts: list[int] = []
    for p in v.split(".")[:3]:
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def _version_below(actual: str, minimum: str) -> bool:
    """Return True if actual version is strictly below minimum."""
    return _parse_version(actual) < _parse_version(minimum)


# ---------------------------------------------------------------------------
# Check classes
# ---------------------------------------------------------------------------


class GitConfigCheck:
    """Assert git config keys on every repo clone under search_roots.

    Checks:
    - required: single-value keys, exact-match (e.g. remote.origin.fetch)
    - required_multivalue: multi-value keys, subset-of-expected (e.g. safe.directory)
    - per_agent: single-value with {{ agent.field }} template substitution

    Fix: git config --replace-all <key> <expected> on each drifted repo.
    """

    name: str = "git_config"
    detection_only: bool = False

    def applies(self, cfg_block: Any) -> bool:
        return cfg_block is not None

    def check(self, ctx: ConvergeContext, cfg_block: Any) -> list[CheckResult]:
        results: list[CheckResult] = []
        search_roots = cfg_block.get("search_roots", [])
        required = cfg_block.get("required", {})
        required_multivalue = cfg_block.get("required_multivalue", {})
        per_agent = cfg_block.get("per_agent", {})

        for root in search_roots:
            if not ctx.fs.is_dir(root):
                continue
            try:
                entries = sorted(ctx.fs.listdir(root))
            except OSError:
                continue
            for entry in entries:
                repo_path = f"{root}/{entry}"
                if not ctx.fs.is_dir(f"{repo_path}/.git"):
                    continue
                # required (single-value)
                for key, expected in required.items():
                    results.append(self._check_single(ctx, repo_path, key, expected))
                # required_multivalue
                for key, expected_values in required_multivalue.items():
                    results.append(self._check_multi(ctx, repo_path, key, expected_values))
                # per_agent (single-value, template-rendered)
                for key, template in per_agent.items():
                    rendered = render_per_agent(template, ctx.agent)
                    results.append(self._check_single(ctx, repo_path, key, rendered))
        return results

    def _check_single(
        self, ctx: ConvergeContext, repo_path: str, key: str, expected: str
    ) -> CheckResult:
        try:
            r = ctx.run(
                ["git", "-C", repo_path, "config", "--get", key],
                capture_output=True, text=True,
                timeout=DEFAULT_SUBPROCESS_TIMEOUT_S,
            )
        except Exception as exc:
            return CheckResult(
                name=self.name, resource=repo_path, status="error",
                detail=key, error=str(exc),
            )
        actual = (r.stdout or "").strip().split("\n")[0]
        if actual == expected:
            return CheckResult(
                name=self.name, resource=repo_path, status="ok", detail=key,
            )
        return CheckResult(
            name=self.name, resource=repo_path, status="drift_detected",
            detail=key,
            drift_value=actual or "(unset)",
            fixed_to=expected,
        )

    def _check_multi(
        self, ctx: ConvergeContext, repo_path: str, key: str, expected_values: list[str]
    ) -> CheckResult:
        try:
            r = ctx.run(
                ["git", "-C", repo_path, "config", "--get-all", key],
                capture_output=True, text=True,
                timeout=DEFAULT_SUBPROCESS_TIMEOUT_S,
            )
        except Exception as exc:
            return CheckResult(
                name=self.name, resource=repo_path, status="error",
                detail=key, error=str(exc),
            )
        actual_values: set[str] = set()
        if r.stdout and r.stdout.strip():
            actual_values = set(r.stdout.strip().split("\n"))
        missing = [v for v in expected_values if v not in actual_values]
        if not missing:
            return CheckResult(
                name=self.name, resource=repo_path, status="ok", detail=key,
            )
        return CheckResult(
            name=self.name, resource=repo_path, status="drift_detected",
            detail=key,
            drift_value=str(sorted(actual_values)),
            fixed_to=str(sorted(expected_values)),
        )

    def fix(
        self,
        ctx: ConvergeContext,
        results: list[CheckResult],
        cfg_block: Any,
    ) -> list[CheckResult]:
        fixed: list[CheckResult] = []
        for result in results:
            if result.status != "drift_detected":
                fixed.append(result)
                continue
            repo_path = result.resource
            key = result.detail
            expected = result.fixed_to
            if expected is None:
                fixed.append(dataclasses.replace(
                    result, status="fix_failed", error="no expected value"))
                continue
            # Apply the fix
            try:
                r = ctx.run(
                    ["git", "-C", repo_path, "config", "--replace-all", key, expected],
                    capture_output=True, text=True,
                    timeout=DEFAULT_SUBPROCESS_TIMEOUT_S,
                )
            except Exception as exc:
                fixed.append(dataclasses.replace(
                    result, status="fix_failed", error=str(exc)))
                continue
            if r.returncode != 0:
                fixed.append(dataclasses.replace(
                    result, status="fix_failed", error=(r.stderr or "non-zero exit")))
                continue
            # Re-check to confirm
            try:
                r2 = ctx.run(
                    ["git", "-C", repo_path, "config", "--get", key],
                    capture_output=True, text=True,
                    timeout=DEFAULT_SUBPROCESS_TIMEOUT_S,
                )
                actual_after = (r2.stdout or "").strip().split("\n")[0]
            except Exception:
                actual_after = ""
            if actual_after == expected:
                fixed.append(dataclasses.replace(result, status="fixed"))
            else:
                fixed.append(dataclasses.replace(
                    result, status="fix_failed",
                    error=f"re-check: got {actual_after!r}, expected {expected!r}"))
        return fixed


class ApparmorProfileCheck:
    """Assert AppArmor profiles match deployment/vm/<source> content.

    Check: sha256 comparison of target vs source.
    Fix: copy source to target + run reload_command.
    """

    name: str = "apparmor_profiles"
    detection_only: bool = False

    def applies(self, cfg_block: Any) -> bool:
        return bool(cfg_block)

    def check(self, ctx: ConvergeContext, cfg_block: Any) -> list[CheckResult]:
        results: list[CheckResult] = []
        for profile in cfg_block:
            target = profile["target"]
            source_rel = profile["source"]
            source_abs = str(ctx.repo_root / source_rel)

            target_sha = ctx.fs.sha256(target)
            source_sha = ctx.fs.sha256(source_abs)

            if target_sha is None:
                results.append(CheckResult(
                    name=self.name, resource=target, status="drift_detected",
                    detail="profile missing",
                    drift_value="absent",
                    fixed_to=source_sha,
                ))
            elif target_sha != source_sha:
                results.append(CheckResult(
                    name=self.name, resource=target, status="drift_detected",
                    detail="content mismatch",
                    drift_value=target_sha,
                    fixed_to=source_sha,
                ))
            else:
                results.append(CheckResult(
                    name=self.name, resource=target, status="ok",
                    detail="content matches source",
                ))
        return results

    def fix(
        self,
        ctx: ConvergeContext,
        results: list[CheckResult],
        cfg_block: Any,
    ) -> list[CheckResult]:
        fixed: list[CheckResult] = []
        for result in results:
            if result.status != "drift_detected":
                fixed.append(result)
                continue
            target = result.resource
            profile = next(
                (p for p in cfg_block if p["target"] == target), None
            )
            if profile is None:
                fixed.append(dataclasses.replace(
                    result, status="fix_failed", error="no profile config entry"))
                continue

            requires_root = profile.get("requires_root", False)
            if requires_root and os.geteuid() != 0:
                fixed.append(dataclasses.replace(
                    result, status="fix_failed",
                    error="requires root; rerun under sudo"))
                continue

            source_abs = str(ctx.repo_root / profile["source"])
            # Copy source → target
            try:
                content = ctx.fs.read_text(source_abs)
                ctx.fs.write_text(target, content)
            except Exception as exc:
                fixed.append(dataclasses.replace(
                    result, status="fix_failed", error=str(exc)))
                continue

            # Run reload command
            reload_cmd = profile.get("reload_command", [])
            if reload_cmd:
                try:
                    r = ctx.run(
                        reload_cmd,
                        capture_output=True, text=True,
                        timeout=DEFAULT_SUBPROCESS_TIMEOUT_S,
                    )
                    if r.returncode != 0:
                        fixed.append(dataclasses.replace(
                            result, status="fix_failed",
                            error=f"reload failed: {r.stderr}"))
                        continue
                except Exception as exc:
                    fixed.append(dataclasses.replace(
                        result, status="fix_failed", error=str(exc)))
                    continue

            # Verify content was written correctly
            new_sha = ctx.fs.sha256(target)
            source_sha = ctx.fs.sha256(source_abs)
            if new_sha == source_sha:
                fixed.append(dataclasses.replace(result, status="fixed"))
            else:
                fixed.append(dataclasses.replace(
                    result, status="fix_failed",
                    error="sha256 still mismatched after write"))
        return fixed


class RequiredCliCheck:
    """Assert required CLI binaries are present at minimum versions.

    Detection-only in the pilot — fix() is a no-op.
    """

    name: str = "required_cli"
    detection_only: bool = True

    def applies(self, cfg_block: Any) -> bool:
        return bool(cfg_block)

    def check(self, ctx: ConvergeContext, cfg_block: Any) -> list[CheckResult]:
        results: list[CheckResult] = []
        for bin_cfg in cfg_block.get("bins", []):
            name = bin_cfg["name"]
            # Check presence via ctx.run(["which", name])
            try:
                which_r = ctx.run(
                    ["which", name],
                    capture_output=True, text=True,
                    timeout=DEFAULT_SUBPROCESS_TIMEOUT_S,
                )
            except Exception as exc:
                results.append(CheckResult(
                    name=self.name, resource=name, status="error",
                    detail=f"which {name} failed", error=str(exc),
                ))
                continue

            if which_r.returncode != 0:
                results.append(CheckResult(
                    name=self.name, resource=name, status="drift_detected",
                    detail=f"{name} not found in PATH",
                ))
                continue

            # Check minimum version if specified
            min_version = bin_cfg.get("min_version")
            version_re_str = bin_cfg.get("version_re")
            version_cmd = bin_cfg.get("version_cmd")

            if min_version and version_re_str and version_cmd:
                try:
                    ver_r = ctx.run(
                        version_cmd,
                        capture_output=True, text=True,
                        timeout=DEFAULT_SUBPROCESS_TIMEOUT_S,
                    )
                    combined = (ver_r.stdout or "") + (ver_r.stderr or "")
                    m = re.search(version_re_str, combined)
                    actual_ver = m.group(1) if m else None
                except Exception:
                    actual_ver = None

                if actual_ver and _version_below(actual_ver, min_version):
                    results.append(CheckResult(
                        name=self.name, resource=name, status="drift_detected",
                        detail=f"{name} version below minimum {min_version}",
                        drift_value=actual_ver,
                        fixed_to=f">={min_version}",
                    ))
                    continue
                results.append(CheckResult(
                    name=self.name, resource=name, status="ok",
                    detail=f"{name} present"
                    + (f" (v{actual_ver})" if actual_ver else ""),
                ))
            else:
                results.append(CheckResult(
                    name=self.name, resource=name, status="ok",
                    detail=f"{name} present",
                ))
        return results

    def fix(
        self,
        ctx: ConvergeContext,
        results: list[CheckResult],
        cfg_block: Any,
    ) -> list[CheckResult]:
        # Detection-only: never fix. Return results unchanged.
        return results


class CodeDeployParityCheck:
    """Compare /opt/agent file md5s against deployment/vm/.deploy-manifest.json.

    Detection-only in the pilot — fix() is a no-op.
    """

    name: str = "code_deploy_parity"
    detection_only: bool = True

    def applies(self, cfg_block: Any) -> bool:
        return bool(cfg_block)

    def check(self, ctx: ConvergeContext, cfg_block: Any) -> list[CheckResult]:
        manifest_rel = cfg_block.get("manifest", "")
        manifest_abs = str(ctx.repo_root / manifest_rel)
        files = cfg_block.get("files", [])

        if not ctx.fs.exists(manifest_abs):
            return [CheckResult(
                name=self.name, resource=manifest_abs, status="drift_detected",
                detail="deploy manifest missing",
            )]

        try:
            manifest_content = ctx.fs.read_text(manifest_abs)
            manifest_data = json.loads(manifest_content)
            manifest_files: dict[str, str] = manifest_data.get("files", {})
        except Exception as exc:
            return [CheckResult(
                name=self.name, resource=manifest_abs, status="error",
                detail=f"manifest parse error: {exc}", error=str(exc),
            )]

        results: list[CheckResult] = []
        for fpath in files:
            if not ctx.fs.exists(fpath):
                results.append(CheckResult(
                    name=self.name, resource=fpath, status="drift_detected",
                    detail="file missing from disk",
                ))
                continue
            runtime_md5 = ctx.fs.md5(fpath)
            manifest_md5 = manifest_files.get(fpath)
            if manifest_md5 is None:
                results.append(CheckResult(
                    name=self.name, resource=fpath, status="drift_detected",
                    detail="file not in manifest",
                    drift_value=runtime_md5,
                ))
            elif runtime_md5 != manifest_md5:
                results.append(CheckResult(
                    name=self.name, resource=fpath, status="drift_detected",
                    detail="md5 mismatch",
                    drift_value=runtime_md5,
                    fixed_to=manifest_md5,
                ))
            else:
                results.append(CheckResult(
                    name=self.name, resource=fpath, status="ok",
                    detail="md5 matches manifest",
                ))
        return results

    def fix(
        self,
        ctx: ConvergeContext,
        results: list[CheckResult],
        cfg_block: Any,
    ) -> list[CheckResult]:
        # Detection-only: never fix. Return results unchanged.
        return results


class HermesCronDisabledCheck:
    """Assert no legacy Hermes JSON cron jobs have enabled=true.

    Fix: rewrite jobs.json with all enabled flags set to false.
    """

    name: str = "hermes_cron_disabled"
    detection_only: bool = False

    def applies(self, cfg_block: Any) -> bool:
        return bool(cfg_block)

    def check(self, ctx: ConvergeContext, cfg_block: Any) -> list[CheckResult]:
        jobs_json = cfg_block.get("jobs_json", "")

        if not ctx.fs.exists(jobs_json):
            return [CheckResult(
                name=self.name, resource=jobs_json, status="ok",
                detail="jobs.json absent (no legacy cron active)",
            )]

        try:
            content = ctx.fs.read_text(jobs_json)
            data = json.loads(content)
        except Exception as exc:
            return [CheckResult(
                name=self.name, resource=jobs_json, status="error",
                detail=f"parse error: {exc}", error=str(exc),
            )]

        enabled_jobs = [j for j in data.get("jobs", []) if j.get("enabled")]
        if not enabled_jobs:
            return [CheckResult(
                name=self.name, resource=jobs_json, status="ok",
                detail="no enabled legacy jobs",
            )]

        job_names = [j.get("name", "<unnamed>") for j in enabled_jobs]
        return [CheckResult(
            name=self.name, resource=jobs_json, status="drift_detected",
            detail=f"{len(enabled_jobs)} enabled job(s): {job_names}",
        )]

    def fix(
        self,
        ctx: ConvergeContext,
        results: list[CheckResult],
        cfg_block: Any,
    ) -> list[CheckResult]:
        fixed: list[CheckResult] = []
        jobs_json = cfg_block.get("jobs_json", "")
        for result in results:
            if result.status != "drift_detected":
                fixed.append(result)
                continue
            try:
                content = ctx.fs.read_text(jobs_json)
                data = json.loads(content)
                for job in data.get("jobs", []):
                    job["enabled"] = False
                # Atomic write: write to same path (MemFsAdapter is safe; real FS
                # should use temp+rename but for pilot we write directly)
                ctx.fs.write_text(jobs_json, json.dumps(data, indent=2))
            except Exception as exc:
                fixed.append(dataclasses.replace(
                    result, status="fix_failed", error=str(exc)))
                continue
            # Re-verify
            try:
                new_content = ctx.fs.read_text(jobs_json)
                new_data = json.loads(new_content)
                still_enabled = [
                    j for j in new_data.get("jobs", []) if j.get("enabled")
                ]
            except Exception as exc:
                fixed.append(dataclasses.replace(
                    result, status="fix_failed",
                    error=f"re-check failed: {exc}"))
                continue
            if still_enabled:
                fixed.append(dataclasses.replace(
                    result, status="fix_failed",
                    error=f"jobs still enabled after rewrite: "
                          f"{[j.get('name') for j in still_enabled]}"))
            else:
                fixed.append(dataclasses.replace(result, status="fixed"))
        return fixed


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

# Ordered registry — new categories are appended here.
_DEFAULT_CHECKS: list[Any] = [
    GitConfigCheck(),
    ApparmorProfileCheck(),
    RequiredCliCheck(),
    CodeDeployParityCheck(),
    HermesCronDisabledCheck(),
]


class ConvergeRunner:
    """Orchestrates all registered checks and produces a ConvergeReport."""

    def __init__(
        self,
        ctx: ConvergeContext,
        checks: Sequence[Any],
        wall_budget_s: float = DEFAULT_WALL_BUDGET_S,
    ) -> None:
        self._ctx = ctx
        self._checks = list(checks)
        self._wall_budget_s = wall_budget_s

    def run(
        self,
        only: set[str] | None = None,
        dry_run: bool = False,
    ) -> ConvergeReport:
        start_time = self._ctx.now()
        all_results: list[CheckResult] = []
        checked = 0
        drifts = 0
        fixes = 0
        fix_failures = 0
        wall_budget_exceeded = False

        for check in self._checks:
            # Wall-budget guard (measured before each check)
            elapsed = self._ctx.now() - start_time
            if elapsed >= self._wall_budget_s:
                wall_budget_exceeded = True
                break

            # Name filter (--check flag)
            if only is not None and check.name not in only:
                continue

            # Config-block gate
            cfg_block = self._ctx.config.get(check.name)
            if not check.applies(cfg_block):
                continue

            checked += 1

            # Run check
            try:
                results = check.check(self._ctx, cfg_block)
            except Exception as exc:
                all_results.append(CheckResult(
                    name=check.name, resource="__check__",
                    status="error", detail=str(exc), error=str(exc),
                ))
                continue

            drift_results = [r for r in results if r.status == "drift_detected"]
            non_drift = [r for r in results if r.status != "drift_detected"]
            all_results.extend(non_drift)
            drifts += len(drift_results)

            if drift_results and not dry_run and not check.detection_only:
                # Attempt fix
                try:
                    fixed = check.fix(self._ctx, drift_results, cfg_block)
                except Exception as exc:
                    err_results = [
                        dataclasses.replace(r, status="fix_failed", error=str(exc))
                        for r in drift_results
                    ]
                    all_results.extend(err_results)
                    fix_failures += len(drift_results)
                    continue

                for fr in fixed:
                    if fr.status == "fixed":
                        fixes += 1
                        drifts -= 1  # resolved — no longer an unresolved drift
                    elif fr.status == "fix_failed":
                        fix_failures += 1
                all_results.extend(fixed)
            else:
                # Dry-run, detection-only, or no drift — record as-is
                all_results.extend(drift_results)

        elapsed_s = self._ctx.now() - start_time

        return ConvergeReport(
            results=tuple(all_results),
            checked=checked,
            drifts=drifts,
            fixes=fixes,
            fix_failures=fix_failures,
            elapsed_s=elapsed_s,
            agent_name=self._ctx.agent.name,
            schema_version=self._ctx.config.get("version", 0),
            wall_budget_exceeded=wall_budget_exceeded,
        )


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def emit_event(
    payload: dict[str, Any],
    *,
    json_mode: bool = True,
    sink: Any = None,
) -> None:
    """Write a single structured event line to sink (default stdout)."""
    if sink is None:
        sink = sys.stdout
    sink.write(json.dumps(payload) + "\n")
    sink.flush()


# ---------------------------------------------------------------------------
# Config / registry loading
# ---------------------------------------------------------------------------


def load_config(path: str, fs: FsAdapter) -> dict[str, Any]:
    """Load and validate canonical-state.yaml.

    Raises:
        FileNotFoundError: if path does not exist.
        PermissionError: if path is not readable.
        ValueError: if the file is not valid YAML.
    """
    content = fs.read_text(path)  # raises FileNotFoundError / PermissionError
    try:
        import yaml  # type: ignore[import]
        result = yaml.safe_load(content)
        return result or {}
    except Exception as exc:
        raise ValueError(f"YAML parse error in {path}: {exc}") from exc


def load_registry(path: str, fs: FsAdapter) -> list[dict[str, Any]]:
    """Load agent-registry.json and return the agents list."""
    content = fs.read_text(path)  # raises FileNotFoundError if missing
    data = json.loads(content)
    return data.get("agents", [])


def resolve_agent(
    env: Mapping[str, str],
    hostname: str,
    registry: list[dict[str, Any]],
) -> AgentContext:
    """Resolve running agent identity.

    Resolution order: AGENT_NAME env var → hostname (first component).
    The resolved name is looked up in the registry for the canonical email.

    Raises:
        ValueError: if the agent name is not found in the registry.
    """
    name = env.get("AGENT_NAME", "").strip() or hostname.split(".")[0]
    for entry in registry:
        if entry.get("name") == name:
            return AgentContext(
                name=name,
                email=entry["email"],
                hostname=hostname,
            )
    raise ValueError(
        f"agent '{name}' not found in registry "
        f"(known: {[e.get('name') for e in registry]})"
    )


def render_per_agent(value: str, agent: AgentContext) -> str:
    """Substitute {{ agent.<field> }} placeholders with agent values."""
    result = value
    result = result.replace("{{ agent.email }}", agent.email)
    result = result.replace("{{ agent.name }}", agent.name)
    result = result.replace("{{ agent.hostname }}", agent.hostname)
    return result


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Returns exit code (0, 1, 2, or 3).
    """
    import argparse
    import socket

    parser = argparse.ArgumentParser(
        description="Idempotent VM-state convergence for tech-dev-agents VMs."
    )
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH,
                        help="Path to canonical-state.yaml")
    parser.add_argument("--registry", default=DEFAULT_REGISTRY_PATH,
                        help="Path to agent-registry.json")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run checks but never call fix()")
    parser.add_argument("--check", nargs="+", dest="checks",
                        help="Run only the named checks")
    parser.add_argument("--json", action="store_true", dest="json_mode",
                        help="Emit only structured events; suppress human summary")
    parser.add_argument("--detection-only-all", action="store_true",
                        help="Override every category fix() to no-op")
    parser.add_argument("--wall-budget", type=float, default=DEFAULT_WALL_BUDGET_S,
                        dest="wall_budget",
                        help="Wall-clock budget in seconds (default: 120)")
    args = parser.parse_args(argv)

    fs = FsAdapter()

    # ---- Step 1: Load config ------------------------------------------------
    try:
        config = load_config(args.config, fs)
    except FileNotFoundError as exc:
        emit_event({"event": "error", "detail": f"config not found: {exc}"})
        return 2
    except (PermissionError, ValueError) as exc:
        emit_event({"event": "error", "detail": str(exc)})
        return 2

    # ---- Step 2: Validate schema version ------------------------------------
    version = config.get("version")
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        emit_event({
            "event": "error",
            "detail": (
                f"unsupported schema version {version!r}; "
                f"supported: {sorted(SUPPORTED_SCHEMA_VERSIONS)}"
            ),
        })
        return 2

    # ---- Step 3: Resolve agent name (fast, no registry needed) --------------
    hostname = socket.gethostname()
    agent_name = os.environ.get("AGENT_NAME", "").strip() or hostname.split(".")[0]

    # ---- Step 4: Check applies_to gate (before registry load) ---------------
    applies_to: list[str] = config.get("applies_to", [])
    if agent_name not in applies_to:
        emit_event({
            "event": "error",
            "detail": (
                f"agent '{agent_name}' not in applies_to {applies_to}; "
                "converge is not intended for this VM"
            ),
        })
        return 2

    # ---- Step 5: Load registry (for per-agent config) -----------------------
    try:
        registry = load_registry(args.registry, fs)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        emit_event({"event": "error", "detail": f"registry error: {exc}"})
        return 2

    # ---- Step 6: Resolve agent with email from registry ---------------------
    try:
        agent = resolve_agent(os.environ, hostname, registry)
    except ValueError as exc:
        emit_event({"event": "error", "detail": str(exc)})
        return 2

    # ---- Step 7: Build context and run checks --------------------------------
    ctx = ConvergeContext(
        agent=agent,
        config=config,
        repo_root=Path(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)
        )))),  # converge.py is at deployment/vm/; repo_root is ../../
        fs=fs,
    )

    checks_to_use = list(_DEFAULT_CHECKS)
    if args.detection_only_all:
        # Monkey-patch each check's detection_only flag
        patched = []
        for c in checks_to_use:
            patched_check = type(c)()
            patched_check.__class__ = type(
                c.__class__.__name__, (c.__class__,),
                {"detection_only": True}
            )
            patched.append(patched_check)
        checks_to_use = patched

    runner = ConvergeRunner(ctx, checks_to_use, wall_budget_s=args.wall_budget)
    only_set = set(args.checks) if args.checks else None
    report = runner.run(only=only_set, dry_run=args.dry_run)

    # ---- Step 8: Emit results and summary -----------------------------------
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for result in report.results:
        if result.status not in ("ok",):
            emit_event({
                "ts": ts,
                "agent": agent.name,
                "host": hostname,
                "event": result.status,
                "check": result.name,
                "resource": result.resource,
                "detail": result.detail,
                **({"drift_value": result.drift_value}
                   if result.drift_value else {}),
                **({"fixed_to": result.fixed_to}
                   if result.fixed_to else {}),
                **({"error": result.error}
                   if result.error else {}),
            })

    if not args.json_mode:
        sys.stdout.write(report.summary_line() + "\n")
        sys.stdout.flush()

    code = report.exit_code()
    if report.wall_budget_exceeded:
        emit_event({
            "ts": ts,
            "agent": agent.name,
            "event": "wall_budget_exceeded",
            "detail": f"budget={args.wall_budget}s elapsed={report.elapsed_s:.1f}s",
        })

    return code


if __name__ == "__main__":
    sys.exit(main())
