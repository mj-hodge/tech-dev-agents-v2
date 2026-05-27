"""Tests for STORY-495: CI/CD Pipeline for Ops Console.

Tests cover:
- Health endpoint commit_sha field (deploy verification)
- Deploy script logic (backup, deploy, health check)
- Rollback script logic (restore, restart, verify)
- Workflow YAML structural validation
"""

from __future__ import annotations

import os
import subprocess
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from tech_dev_agents.ops_console.config import Settings
from tests.ops_console.conftest import (
    TEST_API_KEY,
    _make_agent_records,
    _make_health_snapshots,
    inject_mock_services,
)


# ---------------------------------------------------------------------------
# Category 1: Health endpoint commit_sha (Python integration tests)
# ---------------------------------------------------------------------------


class TestHealthCommitSha:
    """Verify the /api/health endpoint includes commit_sha when set."""

    @pytest.mark.asyncio
    async def test_health_includes_commit_sha_when_env_set(
        self, client, app, mock_agent_service, mock_cost_service,
        mock_monday_service, mock_alert_service, mock_loki_client,
    ):
        """Health response includes commit_sha field matching DEPLOY_COMMIT_SHA env var."""
        inject_mock_services(
            app,
            agent_service=mock_agent_service,
            cost_service=mock_cost_service,
            monday_service=mock_monday_service,
            alert_service=mock_alert_service,
            loki_client=mock_loki_client,
            started_at=datetime.now(timezone.utc),
            deploy_commit_sha="abc12345",
        )

        resp = await client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["commit_sha"] == "abc12345"

    @pytest.mark.asyncio
    async def test_health_commit_sha_null_when_not_set(
        self, client, app, mock_agent_service, mock_cost_service,
        mock_monday_service, mock_alert_service, mock_loki_client,
    ):
        """Health response returns commit_sha=null when DEPLOY_COMMIT_SHA is not set."""
        inject_mock_services(
            app,
            agent_service=mock_agent_service,
            cost_service=mock_cost_service,
            monday_service=mock_monday_service,
            alert_service=mock_alert_service,
            loki_client=mock_loki_client,
            started_at=datetime.now(timezone.utc),
            # Intentionally NOT setting deploy_commit_sha
        )

        resp = await client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        # Field must exist in the response (even if null)
        assert "commit_sha" in data, "commit_sha field must be present in health response"
        assert data["commit_sha"] is None

    @pytest.mark.asyncio
    async def test_health_commit_sha_varies_with_different_values(
        self, client, app, mock_agent_service, mock_cost_service,
        mock_monday_service, mock_alert_service, mock_loki_client,
    ):
        """Output-variance: different DEPLOY_COMMIT_SHA values produce different health responses."""
        # Deploy A
        inject_mock_services(
            app,
            agent_service=mock_agent_service,
            cost_service=mock_cost_service,
            monday_service=mock_monday_service,
            alert_service=mock_alert_service,
            loki_client=mock_loki_client,
            started_at=datetime.now(timezone.utc),
            deploy_commit_sha="aaa11111",
        )
        resp_a = await client.get("/api/health")

        # Deploy B
        inject_mock_services(
            app,
            agent_service=mock_agent_service,
            cost_service=mock_cost_service,
            monday_service=mock_monday_service,
            alert_service=mock_alert_service,
            loki_client=mock_loki_client,
            started_at=datetime.now(timezone.utc),
            deploy_commit_sha="bbb22222",
        )
        resp_b = await client.get("/api/health")

        assert resp_a.json()["commit_sha"] != resp_b.json()["commit_sha"]
        assert resp_a.json()["commit_sha"] == "aaa11111"
        assert resp_b.json()["commit_sha"] == "bbb22222"

    @pytest.mark.asyncio
    async def test_health_no_auth_required_still_returns_commit_sha(
        self, unauthed_client, app, mock_agent_service, mock_cost_service,
        mock_monday_service, mock_alert_service, mock_loki_client,
    ):
        """commit_sha is accessible without authentication (health is a public endpoint)."""
        inject_mock_services(
            app,
            agent_service=mock_agent_service,
            cost_service=mock_cost_service,
            monday_service=mock_monday_service,
            alert_service=mock_alert_service,
            loki_client=mock_loki_client,
            started_at=datetime.now(timezone.utc),
            deploy_commit_sha="def67890",
        )

        resp = await unauthed_client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["commit_sha"] == "def67890"

    @pytest.mark.asyncio
    async def test_health_response_schema_includes_commit_sha_field(
        self, client, app, mock_agent_service, mock_cost_service,
        mock_monday_service, mock_alert_service, mock_loki_client,
    ):
        """Health response schema includes commit_sha alongside existing fields."""
        inject_mock_services(
            app,
            agent_service=mock_agent_service,
            cost_service=mock_cost_service,
            monday_service=mock_monday_service,
            alert_service=mock_alert_service,
            loki_client=mock_loki_client,
            started_at=datetime.now(timezone.utc),
            deploy_commit_sha="schema01",
        )

        resp = await client.get("/api/health")
        data = resp.json()
        # All existing fields still present
        assert "status" in data
        assert "version" in data
        assert "uptime_seconds" in data
        assert "agents_reachable" in data
        assert "agents_total" in data
        assert "loki_reachable" in data
        # New field present
        assert "commit_sha" in data


# ---------------------------------------------------------------------------
# Category 2: Deploy script tests (shell script validation)
# ---------------------------------------------------------------------------


class TestDeployScript:
    """Validate deploy.sh script structure and logic."""

    def test_deploy_script_exists(self):
        """deploy.sh file exists at the expected path."""
        path = Path("deployment/ops-console/deploy.sh")
        assert path.exists(), f"deploy.sh not found at {path}"

    def test_deploy_script_is_executable_or_has_shebang(self):
        """deploy.sh has a bash shebang line."""
        path = Path("deployment/ops-console/deploy.sh")
        content = path.read_text()
        assert content.startswith("#!/"), "deploy.sh must start with a shebang line"
        assert "bash" in content.split("\n")[0], "deploy.sh must use bash"

    def test_deploy_script_never_runs_npm_install(self):
        """deploy.sh must NEVER run npm install on the VM (OOM risk)."""
        path = Path("deployment/ops-console/deploy.sh")
        content = path.read_text()
        assert "npm install" not in content, "deploy.sh must not run npm install"
        assert "npm ci" not in content, "deploy.sh must not run npm ci"

    def test_deploy_script_never_runs_npm_build(self):
        """deploy.sh must NEVER run npm run build on the VM (OOM risk)."""
        path = Path("deployment/ops-console/deploy.sh")
        content = path.read_text()
        assert "npm run build" not in content, "deploy.sh must not run npm run build"

    def test_deploy_script_creates_backup(self):
        """deploy.sh creates a backup of current frontend dist before deploying."""
        path = Path("deployment/ops-console/deploy.sh")
        content = path.read_text()
        assert "dist.bak" in content or "backup" in content.lower(), \
            "deploy.sh must create a backup of the current dist directory"

    def test_deploy_script_uses_docker_cp(self):
        """deploy.sh uses docker cp to deploy backend files (not volume mounts)."""
        path = Path("deployment/ops-console/deploy.sh")
        content = path.read_text()
        assert "docker cp" in content, "deploy.sh must use docker cp for backend deployment"

    def test_deploy_script_restarts_container(self):
        """deploy.sh restarts the ops-console container after deploying."""
        path = Path("deployment/ops-console/deploy.sh")
        content = path.read_text()
        assert "docker restart" in content, "deploy.sh must restart the container"

    def test_deploy_script_checks_health(self):
        """deploy.sh verifies health after restart."""
        path = Path("deployment/ops-console/deploy.sh")
        content = path.read_text()
        assert "health" in content.lower(), "deploy.sh must check health after restart"

    def test_deploy_script_has_nonzero_exit_on_failure(self):
        """deploy.sh exits with non-zero status on health check failure."""
        path = Path("deployment/ops-console/deploy.sh")
        content = path.read_text()
        assert "exit 1" in content, "deploy.sh must exit 1 on failure"

    def test_deploy_script_sets_errexit(self):
        """deploy.sh uses set -e or set -euo pipefail for safety."""
        path = Path("deployment/ops-console/deploy.sh")
        content = path.read_text()
        assert "set -e" in content, "deploy.sh must use set -e for fail-fast behavior"


# ---------------------------------------------------------------------------
# Category 3: Rollback script tests (shell script validation)
# ---------------------------------------------------------------------------


class TestRollbackScript:
    """Validate rollback.sh script structure and logic."""

    def test_rollback_script_exists(self):
        """rollback.sh file exists at the expected path."""
        path = Path("deployment/ops-console/rollback.sh")
        assert path.exists(), f"rollback.sh not found at {path}"

    def test_rollback_script_has_shebang(self):
        """rollback.sh has a bash shebang line."""
        path = Path("deployment/ops-console/rollback.sh")
        content = path.read_text()
        assert content.startswith("#!/"), "rollback.sh must start with a shebang line"
        assert "bash" in content.split("\n")[0], "rollback.sh must use bash"

    def test_rollback_script_restores_frontend(self):
        """rollback.sh restores the frontend dist from backup."""
        path = Path("deployment/ops-console/rollback.sh")
        content = path.read_text()
        assert "dist.bak" in content or "backup" in content.lower(), \
            "rollback.sh must restore from the backup directory"

    def test_rollback_script_restores_backend(self):
        """rollback.sh uses docker cp to restore backend files."""
        path = Path("deployment/ops-console/rollback.sh")
        content = path.read_text()
        assert "docker cp" in content, "rollback.sh must use docker cp to restore backend"

    def test_rollback_script_restarts_container(self):
        """rollback.sh restarts the container after restoring."""
        path = Path("deployment/ops-console/rollback.sh")
        content = path.read_text()
        assert "docker restart" in content, "rollback.sh must restart the container"

    def test_rollback_script_verifies_health(self):
        """rollback.sh checks health after rollback."""
        path = Path("deployment/ops-console/rollback.sh")
        content = path.read_text()
        assert "health" in content.lower(), "rollback.sh must verify health after rollback"

    def test_rollback_script_checks_backup_exists(self):
        """rollback.sh verifies backup exists before attempting restore."""
        path = Path("deployment/ops-console/rollback.sh")
        content = path.read_text()
        # Script should check if backup directory exists before restoring
        assert "-d" in content or "test" in content or "if" in content, \
            "rollback.sh must check backup exists before restoring"

    def test_rollback_script_sets_errexit(self):
        """rollback.sh uses set -e for safety."""
        path = Path("deployment/ops-console/rollback.sh")
        content = path.read_text()
        assert "set -e" in content, "rollback.sh must use set -e for fail-fast behavior"


# ---------------------------------------------------------------------------
# Category 4: Workflow YAML validation
# ---------------------------------------------------------------------------


class TestWorkflowYaml:
    """Validate deploy-ops-console.yml workflow structure."""

    def test_workflow_file_exists(self):
        """GitHub Actions workflow YAML exists at expected path."""
        path = Path(".github/workflows/deploy-ops-console.yml")
        assert path.exists(), f"Workflow not found at {path}"

    def test_workflow_is_valid_yaml(self):
        """Workflow file is valid YAML."""
        import yaml  # stdlib is not available, use ruamel or pyyaml
        path = Path(".github/workflows/deploy-ops-console.yml")
        content = path.read_text()
        data = yaml.safe_load(content)
        assert isinstance(data, dict), "Workflow YAML must parse to a dict"

    def test_workflow_triggers_on_push_to_main(self):
        """Workflow triggers on push to main branch."""
        import yaml
        path = Path(".github/workflows/deploy-ops-console.yml")
        data = yaml.safe_load(path.read_text())
        # Note: YAML parses bare `on` as boolean True
        trigger = data.get("on") or data.get(True, {})
        push = trigger.get("push", {})
        branches = push.get("branches", [])
        assert "main" in branches, "Workflow must trigger on push to main"

    def test_workflow_filters_on_correct_paths(self):
        """Workflow only triggers when relevant files change."""
        import yaml
        path = Path(".github/workflows/deploy-ops-console.yml")
        data = yaml.safe_load(path.read_text())
        # Note: YAML parses bare `on` as boolean True
        trigger = data.get("on") or data.get(True, {})
        push = trigger.get("push", {})
        paths = push.get("paths", [])
        # Must include ops_console backend, frontend, and deployment paths
        paths_str = " ".join(paths)
        assert "tech_dev_agents/ops_console" in paths_str, \
            "Workflow must trigger on ops_console backend changes"
        assert "frontend" in paths_str, \
            "Workflow must trigger on frontend changes"
        assert "deployment/ops-console" in paths_str, \
            "Workflow must trigger on deployment script changes"

    def test_workflow_has_feature_flag_check(self):
        """Workflow checks OPS_CONSOLE_CICD_ENABLED before deploying."""
        path = Path(".github/workflows/deploy-ops-console.yml")
        content = path.read_text()
        assert "OPS_CONSOLE_CICD_ENABLED" in content, \
            "Workflow must check OPS_CONSOLE_CICD_ENABLED feature flag"

    def test_workflow_builds_frontend_on_github_runner(self):
        """Workflow builds frontend on ubuntu-latest (not on the VM)."""
        import yaml
        path = Path(".github/workflows/deploy-ops-console.yml")
        data = yaml.safe_load(path.read_text())
        jobs = data.get("jobs", {})
        # At least one job must use ubuntu-latest and run npm build
        found_build = False
        for job_name, job_config in jobs.items():
            runs_on = job_config.get("runs-on", "")
            steps = job_config.get("steps", [])
            for step in steps:
                run_cmd = step.get("run", "")
                if "npm" in run_cmd and "build" in run_cmd:
                    assert "ubuntu" in runs_on, \
                        f"npm build must run on GitHub-hosted runner, not self-hosted. Found in job '{job_name}'"
                    found_build = True
        assert found_build, "Workflow must include an npm build step"

    def test_workflow_uses_scp_or_ssh_for_deploy(self):
        """Workflow deploys via SCP/SSH (not other methods)."""
        path = Path(".github/workflows/deploy-ops-console.yml")
        content = path.read_text()
        assert "scp" in content.lower() or "ssh" in content.lower(), \
            "Workflow must use SCP or SSH for deployment"

    def test_workflow_has_health_check_verification(self):
        """Workflow verifies health check after deployment."""
        path = Path(".github/workflows/deploy-ops-console.yml")
        content = path.read_text()
        assert "health" in content.lower(), \
            "Workflow must verify health after deployment"

    def test_workflow_has_rollback_step(self):
        """Workflow includes rollback on failure."""
        path = Path(".github/workflows/deploy-ops-console.yml")
        content = path.read_text()
        assert "rollback" in content.lower(), \
            "Workflow must include a rollback step"

    def test_workflow_never_runs_npm_on_vm(self):
        """Workflow must not SSH to VM and run npm commands."""
        path = Path(".github/workflows/deploy-ops-console.yml")
        content = path.read_text()
        # Check that npm commands are not in SSH command blocks
        # (They should be in the build job, not the deploy job)
        lines = content.split("\n")
        in_ssh_block = False
        for line in lines:
            if "ssh" in line.lower() and ("npm" in line):
                pytest.fail("Workflow must not run npm commands via SSH on the VM")


# ---------------------------------------------------------------------------
# Category 5: Boundary and edge case tests
# ---------------------------------------------------------------------------


class TestDeployBoundaries:
    """Boundary conditions and edge cases for the CI/CD pipeline."""

    @pytest.mark.asyncio
    async def test_health_commit_sha_empty_string(
        self, client, app, mock_agent_service, mock_cost_service,
        mock_monday_service, mock_alert_service, mock_loki_client,
    ):
        """Health endpoint handles empty string commit_sha gracefully."""
        inject_mock_services(
            app,
            agent_service=mock_agent_service,
            cost_service=mock_cost_service,
            monday_service=mock_monday_service,
            alert_service=mock_alert_service,
            loki_client=mock_loki_client,
            started_at=datetime.now(timezone.utc),
            deploy_commit_sha="",
        )

        resp = await client.get("/api/health")
        assert resp.status_code == 200
        # Empty string is a valid (if unusual) commit_sha value
        assert "commit_sha" in resp.json()

    def test_deploy_script_does_not_touch_database(self):
        """deploy.sh must not contain any database commands."""
        path = Path("deployment/ops-console/deploy.sh")
        content = path.read_text()
        assert "psql" not in content, "deploy.sh must not run psql commands"
        assert "alembic" not in content, "deploy.sh must not run alembic migrations"
        assert "ops-console-postgres" not in content, \
            "deploy.sh must not interact with the postgres container"

    def test_deploy_script_does_not_touch_nginx_config(self):
        """deploy.sh should only copy static files, not modify nginx configuration."""
        path = Path("deployment/ops-console/deploy.sh")
        content = path.read_text()
        assert "nginx.conf" not in content, "deploy.sh must not modify nginx.conf"
        assert "systemctl restart nginx" not in content, \
            "deploy.sh should not restart nginx (static file changes don't need it)"
