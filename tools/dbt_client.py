"""
dbt Cloud API client.
Triggers and monitors dbt Cloud jobs via the REST API.
Credentials pulled from /cybertronics/dbt in Secrets Manager.
"""

from __future__ import annotations

import time

import requests

from tools.secrets import get_secret_cached

# dbt Cloud run status codes
_STATUS_QUEUED    = 1
_STATUS_STARTING  = 2
_STATUS_RUNNING   = 3
_STATUS_SUCCESS   = 10
_STATUS_ERROR     = 20
_STATUS_CANCELLED = 30

_STATUS_LABELS = {
    _STATUS_QUEUED:    "queued",
    _STATUS_STARTING:  "starting",
    _STATUS_RUNNING:   "running",
    _STATUS_SUCCESS:   "success",
    _STATUS_ERROR:     "error",
    _STATUS_CANCELLED: "cancelled",
}


class DbtCloudClient:
    """Client for the dbt Cloud Administrative API v2."""

    def __init__(self) -> None:
        creds = get_secret_cached("/cybertronics/dbt")
        self._token      = creds["service_token"]
        self._account_id = creds["account_id"]
        self._project_id = creds["project_id"]
        self._base       = f"https://cloud.getdbt.com/api/v2/accounts/{self._account_id}"
        self._headers    = {
            "Authorization": f"Token {self._token}",
            "Content-Type":  "application/json",
        }

    def _get(self, path: str, params: dict | None = None) -> dict:
        resp = requests.get(f"{self._base}{path}", headers=self._headers, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, payload: dict) -> dict:
        resp = requests.post(f"{self._base}{path}", headers=self._headers, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Jobs
    # ------------------------------------------------------------------

    def list_jobs(self) -> list[dict]:
        """List all jobs in the project."""
        data = self._get("/jobs/", params={"project_id": self._project_id})
        return data.get("data", [])

    def trigger_job(self, job_id: int, cause: str = "Agent trigger", steps_override: list[str] | None = None) -> dict:
        """
        Trigger a dbt Cloud job run.

        Args:
            job_id:         The dbt Cloud job ID.
            cause:          Human-readable reason (appears in dbt Cloud UI).
            steps_override: Optional list of dbt commands to run instead of the job's defaults.
                            e.g. ['dbt run --select staging', 'dbt test --select staging']

        Returns:
            Run object dict (includes run_id under key 'id').
        """
        payload: dict = {"cause": cause}
        if steps_override:
            payload["steps_override"] = steps_override
        data = self._post(f"/jobs/{job_id}/run/", payload)
        return data["data"]

    # ------------------------------------------------------------------
    # Runs
    # ------------------------------------------------------------------

    def get_run(self, run_id: int) -> dict:
        """Get the current state of a run."""
        return self._get(f"/runs/{run_id}/")["data"]

    def get_run_status_label(self, run_id: int) -> str:
        """Return a human-readable status string for a run."""
        run = self.get_run(run_id)
        return _STATUS_LABELS.get(run["status"], "unknown")

    def wait_for_run(
        self,
        run_id: int,
        poll_seconds: int = 30,
        timeout_seconds: int = 3600,
    ) -> str:
        """
        Poll until a run reaches a terminal state.

        Returns:
            'success', 'error', or 'cancelled'

        Raises:
            TimeoutError if the run doesn't complete within timeout_seconds.
        """
        elapsed = 0
        while elapsed < timeout_seconds:
            run = self.get_run(run_id)
            status = run["status"]
            if status == _STATUS_SUCCESS:
                return "success"
            if status == _STATUS_ERROR:
                return "error"
            if status == _STATUS_CANCELLED:
                return "cancelled"
            time.sleep(poll_seconds)
            elapsed += poll_seconds
        raise TimeoutError(
            f"dbt Cloud run {run_id} did not complete within {timeout_seconds}s"
        )

    def trigger_and_wait(
        self,
        job_id: int,
        cause: str = "Agent trigger",
        steps_override: list[str] | None = None,
        poll_seconds: int = 30,
        timeout_seconds: int = 3600,
    ) -> tuple[int, str]:
        """
        Trigger a job and block until it completes.

        Returns:
            (run_id, status) where status is 'success', 'error', or 'cancelled'
        """
        run = self.trigger_job(job_id, cause=cause, steps_override=steps_override)
        run_id = run["id"]
        status = self.wait_for_run(run_id, poll_seconds=poll_seconds, timeout_seconds=timeout_seconds)
        return run_id, status

    def get_run_artifact(self, run_id: int, artifact: str = "run_results.json") -> dict:
        """Fetch a run artifact (e.g. run_results.json, manifest.json)."""
        return self._get(f"/runs/{run_id}/artifacts/{artifact}")

    # ------------------------------------------------------------------
    # Environments
    # ------------------------------------------------------------------

    def list_environments(self) -> list[dict]:
        """List all environments in the project."""
        data = self._get("/environments/", params={"project_id": self._project_id})
        return data.get("data", [])
