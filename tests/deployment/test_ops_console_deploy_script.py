"""Regression tests for ``deployment/ops-console/deploy.sh``.

Two production gaps surfaced on 2026-04-24 while landing PR #116:

1. **Doubled-dir docker cp**: ``docker cp src/ container:dest/`` puts ``src``
   as a subdirectory under ``dest`` when ``dest`` already exists. The script
   was copying ``${PACKAGE_DIR}/tech_dev_agents/ops_console/`` into
   ``/app/tech_dev_agents/ops_console/`` — landing the new code at
   ``/app/tech_dev_agents/ops_console/ops_console/...``. The container kept
   importing the OLD code at the original path. ``rework_of`` plumbing was
   silently dead.

2. **No schema migrations**: ``deploy.sh`` only copied python files. Schema
   migrations under ``scripts/migrations/*.sql`` were never applied. PR #116's
   ``009_rework_of.sql`` (and the previously-unapplied
   ``008_composite_key_story_repo.sql``) had to be hand-applied via SSH.

These tests pin both fixes at the script-source level.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_SH = REPO_ROOT / "deployment" / "ops-console" / "deploy.sh"
DEPLOY_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deploy-ops-console.yml"
MIGRATIONS_DIR = REPO_ROOT / "scripts" / "migrations"


@pytest.fixture(scope="module")
def deploy_sh() -> str:
    return DEPLOY_SH.read_text()


# ---------------------------------------------------------------------------
# Fix (b) — docker cp must not double the directory
# ---------------------------------------------------------------------------


class TestDockerCpNoDoubleDir:
    def test_backend_cp_uses_trailing_dot_or_explicit_contents_form(self, deploy_sh):
        """``docker cp src/ container:dest/`` lands ``src`` as a child of
        ``dest`` when ``dest`` exists. Either (a) append ``/.`` to the
        source so the contents copy in place, or (b) ``rm -rf`` the dest
        first and copy the parent. Without one of these, every deploy
        produces ``/app/tech_dev_agents/ops_console/ops_console/...``.
        """
        # Find the backend docker cp line. Match across leading whitespace.
        backend_cp = re.search(
            r"docker\s+cp\s+\"?\$\{?PACKAGE_DIR\}?[^\"]*tech_dev_agents/ops_console[^\"\n]*\"?\s+\"?\$\{?CONTAINER\}?:[^\"\n]+\"?",
            deploy_sh,
        )
        assert backend_cp, (
            "Could not locate the backend docker cp line in deploy.sh — "
            "the structure may have changed; update this test."
        )
        line = backend_cp.group(0)

        # Acceptable forms:
        #   docker cp ${PACKAGE_DIR}/tech_dev_agents/ops_console/. ${CONTAINER}:/app/tech_dev_agents/ops_console/
        #   (or any path ending /. before the container target)
        ends_with_dot = re.search(
            r"tech_dev_agents/ops_console/\.\"?\s+\"?\$\{?CONTAINER\}",
            line,
        )

        # Or the rm-then-cp form: a rm -rf of the in-container dest right
        # before the cp, with the dest being the parent of ops_console.
        rm_then_cp = re.search(
            r"docker\s+exec[^\n]*rm\s+-rf[^\n]*ops_console[^\n]*\n[^\n]*docker\s+cp",
            deploy_sh,
        )

        assert ends_with_dot or rm_then_cp, (
            f"Backend docker cp line still uses the doubled-dir form:\n  {line}\n"
            "Fix: end the source with `/.` (e.g. "
            "`docker cp ${PACKAGE_DIR}/tech_dev_agents/ops_console/. "
            "${CONTAINER}:/app/tech_dev_agents/ops_console/`) so the contents "
            "land directly in the dest, not as a nested subdirectory."
        )


# ---------------------------------------------------------------------------
# Fix (c) — schema migrations must run as part of deploy
# ---------------------------------------------------------------------------


class TestMigrationsAutoRun:
    def test_deploy_sh_runs_migrations(self, deploy_sh):
        """deploy.sh must apply ``scripts/migrations/*.sql`` to the postgres
        container in numeric order, with errexit so a migration failure
        aborts the deploy. Without this, schema and code drift silently —
        PR #116's ``009_rework_of.sql`` had to be hand-applied.
        """
        # The migration step should:
        # - reference the migrations path under the deploy package
        # - run psql against ops_console DB
        # - iterate in sorted/numeric order (sort or shell glob expansion is fine)
        has_migrations_path = re.search(
            r"PACKAGE_DIR\}?[^\"\n]*scripts/migrations|scripts/migrations[^\"\n]*PACKAGE_DIR",
            deploy_sh,
        )
        runs_psql = re.search(
            r"psql\s+-U\s+ops_console\s+-d\s+ops_console",
            deploy_sh,
        )
        assert has_migrations_path, (
            "deploy.sh must reference scripts/migrations/ (under PACKAGE_DIR) "
            "and apply each .sql to the postgres container"
        )
        assert runs_psql, (
            "deploy.sh must invoke psql against the ops_console DB to apply "
            "migrations (e.g. `docker exec -i $POSTGRES_CONTAINER psql -U "
            "ops_console -d ops_console < $sql`)"
        )

    def test_workflow_includes_migrations_in_package(self):
        """The deploy workflow must tar ``scripts/migrations/`` into the
        deploy package; deploy.sh can't apply files that aren't shipped.
        """
        wf = DEPLOY_WORKFLOW.read_text()
        # The tar block lists each path on its own line — match that.
        tar_block = re.search(r"tar\s+czf\s+deploy-package\.tar\.gz([^\n][\s\S]+?)\n\n", wf)
        assert tar_block, "Could not locate the tar invocation in the deploy workflow"
        contents = tar_block.group(1)
        assert "scripts/migrations" in contents, (
            "deploy-ops-console.yml must include `scripts/migrations/` in the "
            "tar package — otherwise deploy.sh has nothing to apply.\n\n"
            f"Current tar block contents:\n{contents}"
        )

    def test_all_migration_files_are_idempotent(self):
        """Every ``*.sql`` in scripts/migrations/ must use ``IF NOT EXISTS``
        / ``IF EXISTS`` (or equivalent ``OR REPLACE`` / DO-block guards) so
        that re-running on every deploy is safe. The auto-run loop relies
        on idempotency.
        """
        gaps: list[str] = []
        for sql in sorted(MIGRATIONS_DIR.glob("*.sql")):
            text = sql.read_text().lower()
            # Skip files that are obviously declarative one-shots that we
            # explicitly mark as non-idempotent — none today, but leave the
            # hook for future cleanup migrations.
            if "non-idempotent" in text:
                continue
            # Heuristic: at least one of these guards must appear if the file
            # creates or alters schema. CREATE TABLE / INDEX / ALTER TABLE
            # / DROP — each must use IF NOT EXISTS / IF EXISTS / OR REPLACE
            # / DO-block guard.
            has_guard = any(
                kw in text
                for kw in (
                    "if not exists",
                    "if exists",
                    "or replace",
                    "do $$",
                    "do $do$",
                )
            )
            # Pure DELETE / UPDATE one-shots are also acceptable as long as
            # they're scoped (they no-op cleanly when re-run).
            is_pure_dml = (
                "create table" not in text
                and "alter table" not in text
                and "drop table" not in text
                and "create index" not in text
                and "create unique index" not in text
                and "drop index" not in text
            )
            if not has_guard and not is_pure_dml:
                gaps.append(sql.name)
        assert not gaps, (
            "These migrations must be idempotent (auto-run on every deploy):\n  "
            + "\n  ".join(gaps)
            + "\nAdd IF NOT EXISTS / IF EXISTS guards or wrap in a DO-block."
        )
