"""Unit tests for deployment.vm.converge — declarative VM-state convergence.

STORY-644: Declarative VM-state convergence pilot
Phase 7: RED state — tests written before implementation.

All I/O is injected via FsAdapter (in-memory) and ctx.run (mock callable).
No live VM, no real filesystem, no network required.

Test IDs (18 total across 6 groups):
  Group A: git_config (T1, T2, T12)
  Group B: apparmor_profiles (T3, T4)
  Group C: required_cli (T5, T6)
  Group D: code_deploy_parity (T7, T8)
  Group E: hermes_cron_disabled (T9)
  Group F: orchestration / CLI (T10, T11, T13, T14, T15, T16, T17, T18)
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# In-memory FsAdapter for tests
# ---------------------------------------------------------------------------


class MemFsAdapter:
    """In-memory filesystem for test isolation.

    Tests populate self.files with {path: content_str} before creating the
    ConvergeContext. sha256 / md5 are computed from the stored content.
    """

    def __init__(self, files: dict[str, str] | None = None) -> None:
        import hashlib

        self._files: dict[str, str] = dict(files or {})
        self._hashlib = hashlib

    def read_text(self, path: str) -> str:
        if path not in self._files:
            raise FileNotFoundError(f"MemFsAdapter: {path} not found")
        return self._files[path]

    def write_text(self, path: str, content: str) -> None:
        self._files[path] = content

    def sha256(self, path: str) -> str | None:
        if path not in self._files:
            return None
        return self._hashlib.sha256(self._files[path].encode()).hexdigest()

    def md5(self, path: str) -> str | None:
        if path not in self._files:
            return None
        return self._hashlib.md5(self._files[path].encode()).hexdigest()

    def exists(self, path: str) -> bool:
        return path in self._files

    def is_dir(self, path: str) -> bool:
        # All paths that are prefixes of stored paths are treated as dirs.
        return any(stored.startswith(path + "/") for stored in self._files)

    def listdir(self, path: str) -> list[str]:
        prefix = path.rstrip("/") + "/"
        return list(
            {
                stored[len(prefix):].split("/")[0]
                for stored in self._files
                if stored.startswith(prefix)
            }
        )

    def get(self, path: str) -> str | None:
        return self._files.get(path)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def make_agent(name: str = "daisy") -> "AgentContext":
    from deployment.vm.converge import AgentContext

    emails = {
        "dan": "tech-agent-dan@gorillacommerce.co",
        "derrick": "tech-agent-derrick@gorillacommerce.co",
        "daisy": "tech-agent-daisy@gorillacommerce.co",
        "devon": "tech-agent-devon@gorillacommerce.co",
        "morris": "tech-agent-morris@gorillacommerce.co",
    }
    return AgentContext(
        name=name,
        email=emails.get(name, f"tech-agent-{name}@gorillacommerce.co"),
        hostname=f"vm-{name}-dev",
    )


def make_context(
    agent_name: str = "daisy",
    config: dict | None = None,
    fs: "MemFsAdapter | None" = None,
    run: Any = None,
    repo_root: str = "/repo",
) -> "ConvergeContext":
    from deployment.vm.converge import AgentContext, ConvergeContext

    if config is None:
        config = {"version": 1, "applies_to": ["daisy", "dan", "devon", "derrick", "morris"]}
    if fs is None:
        fs = MemFsAdapter()
    if run is None:
        run = MagicMock(return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""))

    return ConvergeContext(
        agent=make_agent(agent_name),
        config=config,
        repo_root=Path(repo_root),
        fs=fs,
        run=run,
    )


REGISTRY_CONTENT = {
    "agents": [
        {"name": "dan", "email": "tech-agent-dan@gorillacommerce.co",
         "vm": "vm-dan-agent-dev", "ip": "1.1.1.1", "ssh_port": 443, "status": "active"},
        {"name": "daisy", "email": "tech-agent-daisy@gorillacommerce.co",
         "vm": "vm-daisy-dev", "ip": "2.2.2.2", "ssh_port": 443, "status": "active"},
        {"name": "devon", "email": "tech-agent-devon@gorillacommerce.co",
         "vm": "vm-devon-dev", "ip": "3.3.3.3", "ssh_port": 443, "status": "active"},
        {"name": "derrick", "email": "tech-agent-derrick@gorillacommerce.co",
         "vm": "vm-derrick-dev", "ip": "4.4.4.4", "ssh_port": 443, "status": "active"},
        {"name": "morris", "email": "tech-agent-morris@gorillacommerce.co",
         "vm": "vm-morris-dev", "ip": "5.5.5.5", "ssh_port": 443, "status": "active"},
    ]
}

MINIMAL_CANONICAL_YAML = """\
version: 1
applies_to:
  - dan
  - derrick
  - daisy
  - devon
  - morris

git_config:
  search_roots:
    - /home/hermes/workspace
  required:
    remote.origin.fetch: "+refs/heads/*:refs/remotes/origin/*"
    core.autocrlf: "false"
  required_multivalue:
    safe.directory:
      - "*"
  per_agent:
    user.email: "{{ agent.email }}"
    user.name: "tech-agent-{{ agent.name }}"

apparmor_profiles:
  - target: /etc/apparmor.d/bwrap
    source: deployment/vm/apparmor-bwrap.conf
    reload_command: ["apparmor_parser", "-r", "/etc/apparmor.d/bwrap"]
    requires_root: true

required_cli:
  detection_only: true
  bins:
    - name: codex
      min_version: "0.121"
      version_cmd: ["codex", "--version"]
      version_re: "(\\d+\\.\\d+\\.\\d+)"

code_deploy_parity:
  detection_only: true
  manifest: deployment/vm/.deploy-manifest.json
  files:
    - /opt/agent/dispatch_poller.py

hermes_cron_disabled:
  detection_only: false
  jobs_json: /home/hermes/.hermes/cron/jobs.json
"""


# ---------------------------------------------------------------------------
# Group A: git_config
# ---------------------------------------------------------------------------


class TestGitConfigCheck:
    """T1, T2, T12 — git config convergence."""

    def test_git_config_drift_detected_and_fixed(self):
        """T1: repo with narrow refspec triggers drift_detected; fix() applies wildcard."""
        from deployment.vm.converge import GitConfigCheck

        # Arrange: mock repo at /home/hermes/workspace/tech-dev-agents
        fs = MemFsAdapter({
            "/home/hermes/workspace/tech-dev-agents/.git/config": "[remote \"origin\"]\n"
                "fetch = +refs/heads/main:refs/remotes/origin/main\n",
        })
        # Mock subprocess: first call returns narrow refspec, later calls succeed
        narrow = subprocess.CompletedProcess([], 0,
            stdout="+refs/heads/main:refs/remotes/origin/main\n", stderr="")
        wildcard = subprocess.CompletedProcess([], 0,
            stdout="+refs/heads/*:refs/remotes/origin/*\n", stderr="")
        git_fix = subprocess.CompletedProcess([], 0, stdout="", stderr="")

        run_calls: list[subprocess.CompletedProcess] = [narrow, git_fix, wildcard]
        call_idx = [0]

        def mock_run(cmd, **kw):
            result = run_calls[min(call_idx[0], len(run_calls) - 1)]
            call_idx[0] += 1
            return result

        ctx = make_context(fs=fs, run=mock_run, config={
            "version": 1,
            "applies_to": ["daisy"],
            "git_config": {
                "search_roots": ["/home/hermes/workspace"],
                "required": {"remote.origin.fetch": "+refs/heads/*:refs/remotes/origin/*"},
                "required_multivalue": {},
                "per_agent": {},
            },
        })

        check = GitConfigCheck()
        cfg_block = ctx.config["git_config"]
        results = check.check(ctx, cfg_block)

        # FAIL RED: stub returns [skipped]; real impl returns [drift_detected]
        assert any(
            r.status == "drift_detected" for r in results
        ), f"Expected drift_detected in {results}"

        drift_results = [r for r in results if r.status == "drift_detected"]
        fixed_results = check.fix(ctx, drift_results, cfg_block)

        # FAIL RED: stub fix() returns unchanged drift results; real impl returns [fixed]
        assert any(
            r.status == "fixed" for r in fixed_results
        ), f"Expected fixed in {fixed_results}"

    def test_git_config_already_canonical(self):
        """T2: repo already has wildcard refspec → check() returns ok (no drift)."""
        from deployment.vm.converge import GitConfigCheck

        fs = MemFsAdapter({
            "/home/hermes/workspace/tech-dev-agents/.git/config": "[remote \"origin\"]\n"
                "fetch = +refs/heads/*:refs/remotes/origin/*\n",
        })
        run = MagicMock(return_value=subprocess.CompletedProcess(
            [], 0, stdout="+refs/heads/*:refs/remotes/origin/*\n", stderr=""))

        ctx = make_context(fs=fs, run=run, config={
            "version": 1,
            "applies_to": ["daisy"],
            "git_config": {
                "search_roots": ["/home/hermes/workspace"],
                "required": {"remote.origin.fetch": "+refs/heads/*:refs/remotes/origin/*"},
                "required_multivalue": {},
                "per_agent": {},
            },
        })

        check = GitConfigCheck()
        cfg_block = ctx.config["git_config"]
        results = check.check(ctx, cfg_block)

        # FAIL RED: stub returns [skipped]; real impl returns [ok] for the matched repo
        assert results, "Expected at least one result for the git repo"
        assert all(
            r.status == "ok" for r in results
        ), f"Expected all ok, got {results}"

    def test_per_agent_email_resolution_from_registry(self):
        """T12: AGENT_NAME=daisy → git user.email is tech-agent-daisy@gorillacommerce.co."""
        from deployment.vm.converge import GitConfigCheck, resolve_agent

        # Arrange: resolve_agent resolves daisy from registry
        registry = REGISTRY_CONTENT["agents"]
        env = {"AGENT_NAME": "daisy"}
        agent = resolve_agent(env, "vm-daisy-dev", registry)

        # FAIL RED: stub resolve_agent doesn't look up registry; returns stub email
        assert agent.email == "tech-agent-daisy@gorillacommerce.co", (
            f"Expected daisy's email from registry, got {agent.email!r}"
        )
        assert agent.name == "daisy", f"Expected agent name 'daisy', got {agent.name!r}"

        # Also verify render_per_agent resolves the placeholder
        from deployment.vm.converge import render_per_agent

        rendered = render_per_agent("{{ agent.email }}", agent)
        # FAIL RED: stub returns value unchanged ('{{ agent.email }}')
        assert rendered == "tech-agent-daisy@gorillacommerce.co", (
            f"render_per_agent did not substitute placeholder: {rendered!r}"
        )


# ---------------------------------------------------------------------------
# Group B: apparmor_profiles
# ---------------------------------------------------------------------------


APPARMOR_CONTENT = """\
abi <abi/4.0>,
include <tunables/global>
profile bwrap /usr/bin/bwrap flags=(unconfined) {
  userns,
}
"""


class TestApparmorProfileCheck:
    """T3, T4 — AppArmor profile convergence."""

    def test_apparmor_profile_missing_installs_and_reloads(self):
        """T3: target absent → fix() copies source content + runs apparmor_parser -r."""
        from deployment.vm.converge import ApparmorProfileCheck

        source_path = "/repo/deployment/vm/apparmor-bwrap.conf"
        target_path = "/etc/apparmor.d/bwrap"

        fs = MemFsAdapter({source_path: APPARMOR_CONTENT})
        reload_calls: list[list] = []

        def mock_run(cmd, **kw):
            reload_calls.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        cfg_block = [
            {
                "target": target_path,
                "source": "deployment/vm/apparmor-bwrap.conf",
                "reload_command": ["apparmor_parser", "-r", target_path],
                "requires_root": False,
            }
        ]
        ctx = make_context(fs=fs, run=mock_run, repo_root="/repo")

        check = ApparmorProfileCheck()
        results = check.check(ctx, cfg_block)

        # FAIL RED: stub returns [skipped]; real impl returns [drift_detected]
        assert any(
            r.status == "drift_detected" for r in results
        ), f"Expected drift_detected for missing profile, got {results}"

        drift_results = [r for r in results if r.status == "drift_detected"]
        fixed_results = check.fix(ctx, drift_results, cfg_block)

        # FAIL RED: stub fix() does not write file; real impl writes + reloads
        assert fs.exists(target_path), "Expected target file to be written by fix()"
        assert fs.get(target_path) == APPARMOR_CONTENT, "Target content should match source"
        assert any(
            "apparmor_parser" in str(cmd) for cmd in reload_calls
        ), "Expected apparmor_parser -r to be called"
        assert any(
            r.status == "fixed" for r in fixed_results
        ), f"Expected fixed result, got {fixed_results}"

    def test_apparmor_profile_already_loaded_is_noop(self):
        """T4: target present with matching sha256 → check() returns ok; parser not called."""
        from deployment.vm.converge import ApparmorProfileCheck

        source_path = "/repo/deployment/vm/apparmor-bwrap.conf"
        target_path = "/etc/apparmor.d/bwrap"

        fs = MemFsAdapter({
            source_path: APPARMOR_CONTENT,
            target_path: APPARMOR_CONTENT,  # identical content
        })
        mock_run = MagicMock()

        cfg_block = [
            {
                "target": target_path,
                "source": "deployment/vm/apparmor-bwrap.conf",
                "reload_command": ["apparmor_parser", "-r", target_path],
                "requires_root": False,
            }
        ]
        ctx = make_context(fs=fs, run=mock_run, repo_root="/repo")

        check = ApparmorProfileCheck()
        results = check.check(ctx, cfg_block)

        # FAIL RED: stub returns [skipped]; real impl returns [ok]
        assert results, "Expected at least one result"
        assert all(
            r.status == "ok" for r in results
        ), f"Expected ok (no drift), got {results}"
        mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# Group C: required_cli
# ---------------------------------------------------------------------------


class TestRequiredCliCheck:
    """T5, T6 — required CLI binary detection (detection-only)."""

    def test_required_cli_missing_logged_no_fix(self):
        """T5: codex not in PATH → drift_detected logged; fix() never called."""
        from deployment.vm.converge import RequiredCliCheck

        # Mock: `which codex` returns empty (not found)
        def mock_run(cmd, **kw):
            if "which" in cmd or cmd[0] == "which":
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        cfg_block = {
            "detection_only": True,
            "bins": [
                {
                    "name": "codex",
                    "min_version": "0.121",
                    "version_cmd": ["codex", "--version"],
                    "version_re": r"(\d+\.\d+\.\d+)",
                }
            ],
        }
        ctx = make_context(run=mock_run)

        check = RequiredCliCheck()
        assert check.detection_only is True, "RequiredCliCheck must be detection_only=True"

        results = check.check(ctx, cfg_block)

        # FAIL RED: stub returns [skipped]; real impl returns [drift_detected]
        assert any(
            r.status == "drift_detected" for r in results
        ), f"Expected drift_detected for missing codex, got {results}"

        # Even with drift, fix() must be a no-op (detection only)
        drift_results = [r for r in results if r.status == "drift_detected"]
        fix_results = check.fix(ctx, drift_results, cfg_block)
        # No result should transition to 'fixed'
        assert not any(
            r.status == "fixed" for r in fix_results
        ), "detection-only check must not produce 'fixed' results"

    def test_required_cli_version_below_min(self):
        """T6: codex present but version 0.120.0 < 0.121 → drift_detected with version detail."""
        from deployment.vm.converge import RequiredCliCheck

        def mock_run(cmd, **kw):
            # 'which codex' succeeds
            if "which" in str(cmd):
                return subprocess.CompletedProcess(cmd, 0, stdout="/usr/local/bin/codex", stderr="")
            # 'codex --version' returns old version
            if "codex" in str(cmd):
                return subprocess.CompletedProcess(cmd, 0, stdout="0.120.0\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        cfg_block = {
            "detection_only": True,
            "bins": [
                {
                    "name": "codex",
                    "min_version": "0.121",
                    "version_cmd": ["codex", "--version"],
                    "version_re": r"(\d+\.\d+\.\d+)",
                }
            ],
        }
        ctx = make_context(run=mock_run)

        check = RequiredCliCheck()
        results = check.check(ctx, cfg_block)

        # FAIL RED: stub returns [skipped]; real impl returns [drift_detected]
        assert any(
            r.status == "drift_detected" for r in results
        ), f"Expected drift_detected for old codex version, got {results}"

        drift = next((r for r in results if r.status == "drift_detected"), None)
        assert drift is not None
        # FAIL RED: stub doesn't populate drift_value
        assert drift.drift_value is not None, "drift_value should contain actual version"
        assert "0.120" in (drift.drift_value or ""), (
            f"Expected '0.120' in drift_value, got {drift.drift_value!r}"
        )


# ---------------------------------------------------------------------------
# Group D: code_deploy_parity
# ---------------------------------------------------------------------------


class TestCodeDeployParityCheck:
    """T7, T8 — code deploy parity detection (detection-only)."""

    def test_code_deploy_parity_matches(self):
        """T7: /opt/agent file md5 matches manifest entry → all ok."""
        from deployment.vm.converge import CodeDeployParityCheck

        file_content = "# dispatch_poller stub\nprint('ok')\n"
        import hashlib
        canonical_md5 = hashlib.md5(file_content.encode()).hexdigest()

        manifest = {
            "generated_at": "2026-04-26T00:00:00Z",
            "files": {"/opt/agent/dispatch_poller.py": canonical_md5},
        }
        fs = MemFsAdapter({
            "/repo/deployment/vm/.deploy-manifest.json": json.dumps(manifest),
            "/opt/agent/dispatch_poller.py": file_content,
        })

        cfg_block = {
            "detection_only": True,
            "manifest": "deployment/vm/.deploy-manifest.json",
            "files": ["/opt/agent/dispatch_poller.py"],
        }
        ctx = make_context(fs=fs, repo_root="/repo")

        check = CodeDeployParityCheck()
        results = check.check(ctx, cfg_block)

        # FAIL RED: stub returns [skipped]; real impl returns [ok]
        assert results, "Expected at least one parity result"
        assert all(
            r.status == "ok" for r in results
        ), f"Expected all ok for matching md5s, got {results}"

    def test_code_deploy_parity_drift_detected_no_fix(self):
        """T8: md5 mismatch → drift_detected; no auto-fix in pilot."""
        from deployment.vm.converge import CodeDeployParityCheck

        manifest = {
            "generated_at": "2026-04-26T00:00:00Z",
            "files": {"/opt/agent/dispatch_poller.py": "aaaa1111bbbb2222cccc3333dddd4444"},
        }
        fs = MemFsAdapter({
            "/repo/deployment/vm/.deploy-manifest.json": json.dumps(manifest),
            "/opt/agent/dispatch_poller.py": "# different content\n",
        })

        cfg_block = {
            "detection_only": True,
            "manifest": "deployment/vm/.deploy-manifest.json",
            "files": ["/opt/agent/dispatch_poller.py"],
        }
        ctx = make_context(fs=fs, repo_root="/repo")

        check = CodeDeployParityCheck()
        assert check.detection_only is True

        results = check.check(ctx, cfg_block)

        # FAIL RED: stub returns [skipped]; real impl returns [drift_detected]
        assert any(
            r.status == "drift_detected" for r in results
        ), f"Expected drift_detected for md5 mismatch, got {results}"

        drift = next((r for r in results if r.status == "drift_detected"), None)
        assert drift is not None
        # FAIL RED: stub doesn't populate drift_value/fixed_to
        assert drift.drift_value is not None, "drift_value should contain runtime md5"
        assert drift.fixed_to is not None, "fixed_to should contain manifest md5"

        # No fix attempted
        fix_results = check.fix(ctx, [drift], cfg_block)
        assert not any(r.status == "fixed" for r in fix_results), (
            "detection-only check must not produce 'fixed' results"
        )


# ---------------------------------------------------------------------------
# Group E: hermes_cron_disabled
# ---------------------------------------------------------------------------


class TestHermesCronDisabledCheck:
    """T9 — Hermes legacy JSON cron must have no enabled jobs."""

    def test_hermes_cron_enabled_job_disabled_by_fix(self):
        """T9: jobs.json with enabled=true → fix() rewrites file with enabled=false."""
        from deployment.vm.converge import HermesCronDisabledCheck

        jobs_json_path = "/home/hermes/.hermes/cron/jobs.json"
        jobs_content = json.dumps({
            "jobs": [
                {"name": "legacy-cron", "enabled": True, "schedule": "*/5 * * * *"},
                {"name": "other-cron", "enabled": False, "schedule": "0 * * * *"},
            ]
        })

        fs = MemFsAdapter({jobs_json_path: jobs_content})
        cfg_block = {
            "detection_only": False,
            "jobs_json": jobs_json_path,
        }
        ctx = make_context(fs=fs)

        check = HermesCronDisabledCheck()
        results = check.check(ctx, cfg_block)

        # FAIL RED: stub returns [skipped]; real impl returns [drift_detected]
        assert any(
            r.status == "drift_detected" for r in results
        ), f"Expected drift_detected for enabled job, got {results}"

        drift_results = [r for r in results if r.status == "drift_detected"]
        fixed_results = check.fix(ctx, drift_results, cfg_block)

        # FAIL RED: stub doesn't rewrite file
        written = fs.get(jobs_json_path)
        assert written is not None, "Expected jobs.json to be rewritten by fix()"
        parsed = json.loads(written)
        assert all(
            not job["enabled"] for job in parsed["jobs"]
        ), f"Expected all jobs disabled after fix, got {parsed['jobs']}"

        assert any(
            r.status == "fixed" for r in fixed_results
        ), f"Expected fixed result, got {fixed_results}"


# ---------------------------------------------------------------------------
# Group F: orchestration / CLI
# ---------------------------------------------------------------------------


class TestOrchestration:
    """T10, T11, T13, T14, T15, T16, T17, T18 — runner and CLI behaviour."""

    def test_yaml_missing_exits_2(self, tmp_path):
        """T10: --config pointing to nonexistent file → main() returns 2."""
        from deployment.vm.converge import main

        nonexistent = str(tmp_path / "no-such-file.yaml")
        exit_code = main(["--config", nonexistent])

        # FAIL RED: stub main() always returns 0
        assert exit_code == 2, (
            f"Expected exit code 2 for missing YAML, got {exit_code}"
        )

    def test_yaml_unknown_version_exits_2(self, tmp_path):
        """T11: YAML with version: 99 → main() returns 2 with clear error."""
        from deployment.vm.converge import main

        config_file = tmp_path / "state.yaml"
        config_file.write_text("version: 99\napplies_to: [daisy]\n")

        exit_code = main(["--config", str(config_file), "--check", "git_config"])

        # FAIL RED: stub main() always returns 0
        assert exit_code == 2, (
            f"Expected exit code 2 for unsupported version, got {exit_code}"
        )

    def test_idempotency_two_consecutive_runs(self):
        """T13: first run fixes drift; second run on same state returns all-ok exit 0."""
        from deployment.vm.converge import (
            AgentContext,
            ConvergeContext,
            ConvergeRunner,
            HermesCronDisabledCheck,
        )

        jobs_path = "/home/hermes/.hermes/cron/jobs.json"
        fs = MemFsAdapter({
            jobs_path: json.dumps({
                "jobs": [{"name": "legacy", "enabled": True, "schedule": "*/5 * * * *"}]
            })
        })
        cfg = {
            "version": 1,
            "applies_to": ["daisy"],
            "hermes_cron_disabled": {
                "detection_only": False,
                "jobs_json": jobs_path,
            },
        }
        ctx = make_context(fs=fs, config=cfg)
        check = HermesCronDisabledCheck()
        runner = ConvergeRunner(ctx, [check])

        # First run: should detect drift and fix it
        report1 = runner.run()

        # FAIL RED: stub runner returns empty 0-drift report
        assert report1.drifts > 0 or report1.fixes > 0, (
            f"First run should detect/fix drift; got drifts={report1.drifts} fixes={report1.fixes}"
        )

        # Second run: same state (now fixed) → all ok, exit 0
        report2 = runner.run()
        assert report2.drifts == 0, (
            f"Second run should have 0 drifts after fix; got {report2.drifts}"
        )
        assert report2.exit_code() == 0, (
            f"Second run exit_code should be 0; got {report2.exit_code()}"
        )

    def test_summary_line_format(self):
        """T14: summary_line matches expected regex format."""
        import re
        from deployment.vm.converge import (
            ConvergeContext,
            ConvergeReport,
            ConvergeRunner,
            GitConfigCheck,
        )

        fs = MemFsAdapter()
        ctx = make_context(agent_name="daisy", fs=fs, config={
            "version": 1,
            "applies_to": ["daisy"],
            "git_config": {
                "search_roots": [],
                "required": {},
                "required_multivalue": {},
                "per_agent": {},
            },
        })
        runner = ConvergeRunner(ctx, [GitConfigCheck()])
        report = runner.run()

        line = report.summary_line()

        # FAIL RED: stub runner never invokes the check, so checked=0.
        # Real runner must report checked=1 for the 1 registered check.
        assert report.checked >= 1, (
            f"runner should have attempted at least 1 check category; got checked={report.checked}"
        )

        pattern = (
            r"^converge: agent=\w+ schema=v\d+ "
            r"checks=\d+ resources=\d+ ok=\d+ "
            r"drift=\d+ fixed=\d+ fix_failed=\d+ elapsed=\d+\.\d+s$"
        )
        assert re.match(pattern, line), (
            f"summary_line does not match expected format:\n  got:      {line!r}\n"
            f"  pattern:  {pattern}"
        )
        # Must include correct agent name and checked=1
        assert "agent=daisy" in line, f"summary_line should contain 'agent=daisy': {line!r}"
        assert "checks=1" in line, f"summary_line should contain 'checks=1': {line!r}"

    def test_dry_run_never_calls_fix(self):
        """T15: --dry-run flag → fix() never called even when drift is present."""
        from deployment.vm.converge import (
            CheckResult,
            ConvergeRunner,
            GitConfigCheck,
        )

        # Replace check() with one that returns a drift result
        mock_check = MagicMock(spec=GitConfigCheck)
        mock_check.name = "git_config"
        mock_check.detection_only = False
        mock_check.applies.return_value = True
        drift_result = CheckResult(
            name="git_config",
            resource="/repo",
            status="drift_detected",
            detail="remote.origin.fetch is narrow",
        )
        mock_check.check.return_value = [drift_result]
        mock_check.fix = MagicMock(return_value=[drift_result])  # would convert to fixed

        ctx = make_context(config={
            "version": 1,
            "applies_to": ["daisy"],
            "git_config": {
                "search_roots": [],
                "required": {},
                "required_multivalue": {},
                "per_agent": {},
            },
        })
        runner = ConvergeRunner(ctx, [mock_check])
        report = runner.run(dry_run=True)

        # FAIL RED: stub runner never calls check() or fix() at all
        mock_check.check.assert_called_once(), "check() must be called even in dry_run"
        mock_check.fix.assert_not_called(), "fix() must NOT be called in dry_run mode"

        # Drift detected but not fixed → exit code 1
        assert report.exit_code() == 1, (
            f"dry-run with unfixed drift should exit 1, got {report.exit_code()}"
        )

    def test_check_filter_runs_only_named(self):
        """T16: --check git_config → only git_config check invoked; others skipped."""
        from deployment.vm.converge import (
            ApparmorProfileCheck,
            ConvergeRunner,
            GitConfigCheck,
        )

        git_check = MagicMock(spec=GitConfigCheck)
        git_check.name = "git_config"
        git_check.detection_only = False
        git_check.applies.return_value = True
        git_check.check.return_value = []

        apparmor_check = MagicMock(spec=ApparmorProfileCheck)
        apparmor_check.name = "apparmor_profiles"
        apparmor_check.detection_only = False
        apparmor_check.applies.return_value = True
        apparmor_check.check.return_value = []

        ctx = make_context(config={
            "version": 1,
            "applies_to": ["daisy"],
            "git_config": {"search_roots": [], "required": {}, "required_multivalue": {}, "per_agent": {}},
        })
        runner = ConvergeRunner(ctx, [git_check, apparmor_check])

        # Run with filter: only git_config
        runner.run(only={"git_config"})

        # FAIL RED: stub runner doesn't call any checks at all
        git_check.check.assert_called_once(), "git_config check must be invoked when named"
        apparmor_check.check.assert_not_called(), (
            "apparmor_profiles check must NOT be invoked when not in --check filter"
        )

    def test_wall_budget_exhausted_exits_3(self, tmp_path):
        """T17: wall budget exceeded during checks → main() returns 3."""
        from deployment.vm.converge import (
            CheckResult,
            ConvergeRunner,
        )

        # Create a check that takes forever (or we inject a time source that jumps)
        slow_check = MagicMock()
        slow_check.name = "slow_check"
        slow_check.detection_only = True
        slow_check.applies.return_value = True

        # Simulate time jumping past budget inside check()
        call_times = [0.0, 200.0]  # time.time() returns 0, then 200 (past 120s budget)
        time_idx = [0]

        def mock_time():
            t = call_times[min(time_idx[0], len(call_times) - 1)]
            time_idx[0] += 1
            return t

        def slow_run_check(ctx, cfg_block):
            return [CheckResult(
                name="slow_check", resource="test",
                status="ok", detail="done"
            )]

        slow_check.check.side_effect = slow_run_check

        ctx = make_context(config={"version": 1, "applies_to": ["daisy"]})
        # Inject mock time into context
        from deployment.vm.converge import ConvergeContext, AgentContext, FsAdapter
        ctx_with_time = ConvergeContext(
            agent=ctx.agent,
            config=ctx.config,
            repo_root=ctx.repo_root,
            fs=ctx.fs,
            now=mock_time,
            run=ctx.run,
        )

        runner = ConvergeRunner(ctx_with_time, [slow_check], wall_budget_s=120.0)
        report = runner.run()

        # FAIL RED: stub runner returns empty report, not exit 3
        assert report.exit_code() == 3, (
            f"Expected exit_code 3 for exceeded wall budget, got {report.exit_code()}"
        )

    def test_agent_not_in_applies_to_exits_2(self, tmp_path):
        """T18: running on an unrecognised host → main() returns 2."""
        from deployment.vm.converge import main

        config_file = tmp_path / "state.yaml"
        config_file.write_text(
            "version: 1\napplies_to:\n  - dan\n  - daisy\n"
        )

        # Patch resolve_agent to return an agent not in applies_to
        with patch.dict(os.environ, {"AGENT_NAME": "random-vm"}, clear=False):
            exit_code = main(["--config", str(config_file)])

        # FAIL RED: stub main() always returns 0
        assert exit_code == 2, (
            f"Expected exit code 2 for agent not in applies_to, got {exit_code}"
        )
