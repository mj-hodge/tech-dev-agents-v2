"""SSH-based agent presence probing service — STORY-426."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from tech_dev_agents.ops_console.cache import TTLCache
from tech_dev_agents.ops_console.models.responses import (
    AgentPresence,
    AgentPresenceListResponse,
    PresenceState,
)

logger = logging.getLogger(__name__)

# Shell metacharacters that must not appear in SSH target values (host/name).
_SSH_FORBIDDEN_CHARS = frozenset(";|&`$()'\"\\\n\r\t ")


class PresenceService:
    """Probe agent VMs via SSH to determine operational presence state."""

    def __init__(
        self,
        agent_service,
        ssh_timeout: int = 5,
        cache_ttl: int = 30,
        ssh_user: str = "agent",
        poller_service: str = "dispatch-poller",
        pause_flag_path: str = "/var/run/dispatch-poller-paused-until",
    ):
        self._agent_service = agent_service
        self._ssh_timeout = ssh_timeout
        # AC3-R: bounded cache — maxsize=1 (single "all_presence" key)
        self._cache = TTLCache(cache_ttl, maxsize=1)
        self._ssh_user = ssh_user
        self._poller_service = poller_service
        self._pause_flag_path = pause_flag_path

    async def get_all_presence(self) -> AgentPresenceListResponse:
        """Return presence for all enabled agents, cached."""
        cached = self._cache.get("all_presence")
        if cached is not None:
            # AC8-R: return a new copy rather than mutating the stored entry
            return cached.model_copy(update={"cached": True})

        agents = self._agent_service.get_registry()
        enabled = [a for a in agents if a.enabled]

        results = await asyncio.gather(
            *[self._probe(a) for a in enabled],
            return_exceptions=True,
        )

        presences = []
        now = datetime.now(timezone.utc)
        for agent, result in zip(enabled, results):
            if isinstance(result, Exception):
                presences.append(AgentPresence(
                    name=agent.name,
                    state=PresenceState.OFFLINE,
                    checked_at=now,
                    detail=str(result),
                ))
            else:
                presences.append(result)

        response = AgentPresenceListResponse(
            agents=presences,
            cached=False,
            checked_at=now,
        )
        self._cache.set("all_presence", response)
        return response

    async def _probe(self, agent) -> AgentPresence:
        """SSH into a single agent VM and derive presence state."""
        now = datetime.now(timezone.utc)

        # AC2-R: validate host before interpolating into SSH command
        try:
            self._validate_ssh_target(agent.host)
        except ValueError as exc:
            return AgentPresence(
                name=agent.name,
                state=PresenceState.OFFLINE,
                checked_at=now,
                detail=f"Invalid host: {exc}",
            )

        ssh_cmd = self._build_ssh_command(agent)

        try:
            proc = await asyncio.wait_for(
                asyncio.create_subprocess_exec(
                    *ssh_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                ),
                timeout=self._ssh_timeout + 1,
            )
            # AC7-R: kill and reap the subprocess if communicate() times out
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=self._ssh_timeout,
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                raise
        except (asyncio.TimeoutError, OSError) as exc:
            return AgentPresence(
                name=agent.name,
                state=PresenceState.OFFLINE,
                checked_at=now,
                detail=f"SSH probe failed: {exc}",
            )

        if proc.returncode == 255:
            stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()
            return AgentPresence(
                name=agent.name,
                state=PresenceState.OFFLINE,
                checked_at=now,
                detail=stderr_text or "SSH connection failed",
            )

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        return self._parse_output(agent.name, stdout, now)

    def _validate_ssh_target(self, value: str) -> None:
        """Reject values containing shell metacharacters or whitespace.

        AC2-R: prevents command injection via crafted agent.host entries.

        Raises:
            ValueError: if *value* contains any forbidden character.
        """
        bad = _SSH_FORBIDDEN_CHARS.intersection(value)
        if bad:
            displayable = "".join(sorted(repr(c) for c in bad))
            raise ValueError(
                f"SSH target contains forbidden characters {displayable}: {value!r}"
            )

    def _build_ssh_command(self, agent) -> list[str]:
        """Build the SSH command list for subprocess exec."""
        host = agent.host
        port = str(getattr(agent, "ssh_port", 443))
        remote_cmd = (
            f"systemctl is-active {self._poller_service} 2>/dev/null || echo inactive; "
            f"cat {self._pause_flag_path} 2>/dev/null || echo ''; "
            f"pgrep -f claude_sdk > /dev/null 2>&1 && echo running || echo ''"
        )
        return [
            "ssh",
            "-p", port,
            "-o", "ConnectTimeout=5",
            "-o", "StrictHostKeyChecking=no",
            "-o", "BatchMode=yes",
            "-o", "LogLevel=ERROR",
            # AC1-R: enforce key-only auth — no passwords in /proc/{pid}/cmdline
            "-o", "PasswordAuthentication=no",
            "-o", "PreferredAuthentications=publickey",
            f"{self._ssh_user}@{host}",
            remote_cmd,
        ]

    def _parse_output(
        self, name: str, stdout: str, now: datetime
    ) -> AgentPresence:
        """Parse SSH output into AgentPresence."""
        lines = stdout.strip().split("\n")

        # Line 1: poller status
        poller_active = len(lines) > 0 and lines[0].strip() == "active"

        # Line 2: pause flag timestamp
        paused_until = None
        if len(lines) > 1 and lines[1].strip():
            try:
                paused_until = datetime.fromisoformat(lines[1].strip())
                if paused_until.tzinfo is None:
                    paused_until = paused_until.replace(tzinfo=timezone.utc)
            except ValueError:
                paused_until = None  # Unparseable → treat as no pause

        # Line 3: SDK process
        sdk_running = len(lines) > 2 and lines[2].strip() == "running"

        state = self._derive_state(poller_active, paused_until, sdk_running, now)
        return AgentPresence(name=name, state=state, checked_at=now, detail=None)

    @staticmethod
    def _derive_state(
        poller_active: bool,
        paused_until: datetime | None,
        sdk_running: bool,
        now: datetime,
    ) -> PresenceState:
        """Derive presence state from three probe signals."""
        if not poller_active:
            return PresenceState.OFFLINE
        if paused_until is not None and paused_until > now:
            return PresenceState.RATE_LIMITED
        if sdk_running:
            return PresenceState.WORKING
        return PresenceState.IDLE
