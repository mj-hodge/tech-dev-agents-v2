"""
Airflow REST API client (Airflow 2.x).
Credentials pulled from /cybertronics/airflow in Secrets Manager.
"""

from __future__ import annotations

import time

import requests
from requests.auth import HTTPBasicAuth

from tools.secrets import get_secret_cached

# DAG run states
STATE_QUEUED  = "queued"
STATE_RUNNING = "running"
STATE_SUCCESS = "success"
STATE_FAILED  = "failed"

_TERMINAL_STATES = {STATE_SUCCESS, STATE_FAILED, "upstream_failed"}


class AirflowClient:
    """Client for the Airflow REST API v1."""

    def __init__(self) -> None:
        creds         = get_secret_cached("/cybertronics/airflow")
        base_url      = creds["url"].rstrip("/")
        self._api     = f"{base_url}/api/v1"
        self._auth    = HTTPBasicAuth(creds["username"], creds["password"])
        self._session = requests.Session()
        self._session.auth = self._auth

    def _get(self, path: str, params: dict | None = None) -> dict:
        resp = self._session.get(f"{self._api}{path}", params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, payload: dict) -> dict:
        resp = self._session.post(f"{self._api}{path}", json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def _patch(self, path: str, payload: dict) -> dict:
        resp = self._session.patch(f"{self._api}{path}", json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    def get_health(self) -> dict:
        """Check scheduler and metadatabase health."""
        return self._get("/health")

    def is_healthy(self) -> bool:
        """Return True if both scheduler and metadb report healthy."""
        health = self.get_health()
        return (
            health.get("scheduler", {}).get("status") == "healthy"
            and health.get("metadatabase", {}).get("status") == "healthy"
        )

    # ------------------------------------------------------------------
    # DAGs
    # ------------------------------------------------------------------

    def list_dags(self, limit: int = 100) -> list[dict]:
        return self._get("/dags", params={"limit": limit}).get("dags", [])

    def get_dag(self, dag_id: str) -> dict:
        return self._get(f"/dags/{dag_id}")

    def pause_dag(self, dag_id: str) -> dict:
        return self._patch(f"/dags/{dag_id}", {"is_paused": True})

    def unpause_dag(self, dag_id: str) -> dict:
        return self._patch(f"/dags/{dag_id}", {"is_paused": False})

    # ------------------------------------------------------------------
    # DAG Runs
    # ------------------------------------------------------------------

    def trigger_dag(self, dag_id: str, conf: dict | None = None, logical_date: str | None = None) -> dict:
        """
        Trigger a DAG run.

        Args:
            dag_id:       The DAG ID to trigger.
            conf:         Optional JSON config passed to the DAG run.
            logical_date: Optional ISO8601 execution date override.

        Returns:
            DAG run object (includes dag_run_id).
        """
        payload: dict = {"conf": conf or {}}
        if logical_date:
            payload["logical_date"] = logical_date
        return self._post(f"/dags/{dag_id}/dagRuns", payload)

    def get_dag_run(self, dag_id: str, dag_run_id: str) -> dict:
        return self._get(f"/dags/{dag_id}/dagRuns/{dag_run_id}")

    def list_dag_runs(self, dag_id: str, limit: int = 10, state: str | None = None) -> list[dict]:
        params: dict = {"limit": limit, "order_by": "-execution_date"}
        if state:
            params["state"] = state
        return self._get(f"/dags/{dag_id}/dagRuns", params=params).get("dag_runs", [])

    def get_latest_dag_run(self, dag_id: str) -> dict | None:
        runs = self.list_dag_runs(dag_id, limit=1)
        return runs[0] if runs else None

    def wait_for_dag_run(
        self,
        dag_id: str,
        dag_run_id: str,
        poll_seconds: int = 30,
        timeout_seconds: int = 7200,
    ) -> str:
        """
        Poll until a DAG run reaches a terminal state.

        Returns:
            'success', 'failed', or 'upstream_failed'

        Raises:
            TimeoutError if the run doesn't complete within timeout_seconds.
        """
        elapsed = 0
        while elapsed < timeout_seconds:
            run = self.get_dag_run(dag_id, dag_run_id)
            state = run["state"]
            if state in _TERMINAL_STATES:
                return state
            time.sleep(poll_seconds)
            elapsed += poll_seconds
        raise TimeoutError(
            f"DAG run {dag_run_id} for {dag_id} did not complete within {timeout_seconds}s"
        )

    def trigger_and_wait(
        self,
        dag_id: str,
        conf: dict | None = None,
        poll_seconds: int = 30,
        timeout_seconds: int = 7200,
    ) -> tuple[str, str]:
        """
        Trigger a DAG and block until it completes.

        Returns:
            (dag_run_id, state) where state is 'success' or 'failed'
        """
        run = self.trigger_dag(dag_id, conf=conf)
        dag_run_id = run["dag_run_id"]
        state = self.wait_for_dag_run(dag_id, dag_run_id, poll_seconds=poll_seconds, timeout_seconds=timeout_seconds)
        return dag_run_id, state

    # ------------------------------------------------------------------
    # Task Instances
    # ------------------------------------------------------------------

    def list_task_instances(self, dag_id: str, dag_run_id: str) -> list[dict]:
        return self._get(f"/dags/{dag_id}/dagRuns/{dag_run_id}/taskInstances").get("task_instances", [])

    def get_failed_tasks(self, dag_id: str, dag_run_id: str) -> list[dict]:
        """Return only task instances that failed in a given run."""
        tasks = self.list_task_instances(dag_id, dag_run_id)
        return [t for t in tasks if t.get("state") == "failed"]
