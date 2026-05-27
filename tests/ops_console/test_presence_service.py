"""Unit tests for PresenceService — T426-01 through T426-17.

Tests are in RED state until Phase 8 creates:
  - tech_dev_agents/ops_console/services/presence_service.py
  - tech_dev_agents/ops_console/models/responses.PresenceState
  - tech_dev_agents/ops_console/models/responses.AgentPresence
  - tech_dev_agents/ops_console/models/responses.AgentPresenceListResponse

Coverage target: all four state derivations, SSH output parsing edge cases,
SSH timeout/error handling, concurrent gather fan-out, and cache behaviour.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Imports from new modules — these will raise ImportError in RED state.
# ---------------------------------------------------------------------------
from tech_dev_agents.ops_console.services.presence_service import PresenceService  # noqa: E402
from tech_dev_agents.ops_console.models.responses import (  # noqa: E402
    AgentPresence,
    AgentPresenceListResponse,
    PresenceState,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(timezone.utc)


def _make_agent(name: str = "dan", host: str = "10.0.0.1", enabled: bool = True):
    """Build a minimal AgentRecord-like object with ssh_port."""
    rec = MagicMock()
    rec.name = name
    rec.host = host
    rec.port = 8080
    rec.ssh_port = 443
    rec.enabled = enabled
    return rec


def _make_service(
    agents=None,
    ssh_timeout: int = 5,
    cache_ttl: int = 30,
) -> PresenceService:
    """Build a PresenceService backed by a mock AgentService."""
    agent_service = MagicMock()
    agent_service.get_registry.return_value = agents or [
        _make_agent("dan", "10.0.0.1"),
        _make_agent("derrick", "10.0.0.2"),
    ]
    return PresenceService(
        agent_service=agent_service,
        ssh_timeout=ssh_timeout,
        cache_ttl=cache_ttl,
    )


def _future_ts(seconds: int = 300) -> str:
    """Return an ISO-8601 timestamp N seconds in the future."""
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def _past_ts(seconds: int = 300) -> str:
    """Return an ISO-8601 timestamp N seconds in the past."""
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


# ---------------------------------------------------------------------------
# T426-01 through T426-05: _derive_state() — pure state derivation
# ---------------------------------------------------------------------------


class TestDeriveState:
    """T426-01–05: Static `_derive_state` covers all four states and expiry logic."""

    def test_derive_state_offline_when_poller_not_active(self):
        """T426-01: poller_active=False → offline (regardless of other signals)."""
        state = PresenceService._derive_state(
            poller_active=False,
            paused_until=None,
            sdk_running=True,
            now=_NOW,
        )
        assert state == PresenceState.OFFLINE

    def test_derive_state_rate_limited_when_paused_and_future(self):
        """T426-02: poller active, pause flag in the future → rate_limited."""
        future = datetime.now(timezone.utc) + timedelta(minutes=5)
        state = PresenceService._derive_state(
            poller_active=True,
            paused_until=future,
            sdk_running=False,
            now=_NOW,
        )
        assert state == PresenceState.RATE_LIMITED

    def test_derive_state_working_when_poller_active_and_sdk_running(self):
        """T426-03: poller active, no pause, SDK process running → working."""
        state = PresenceService._derive_state(
            poller_active=True,
            paused_until=None,
            sdk_running=True,
            now=_NOW,
        )
        assert state == PresenceState.WORKING

    def test_derive_state_idle_when_poller_active_no_sdk(self):
        """T426-04: poller active, no pause, no SDK process → idle."""
        state = PresenceService._derive_state(
            poller_active=True,
            paused_until=None,
            sdk_running=False,
            now=_NOW,
        )
        assert state == PresenceState.IDLE

    def test_derive_state_idle_when_pause_flag_already_expired(self):
        """T426-05: pause flag exists but timestamp < now → treated as idle (no pause)."""
        past = datetime.now(timezone.utc) - timedelta(minutes=5)
        state = PresenceService._derive_state(
            poller_active=True,
            paused_until=past,
            sdk_running=False,
            now=datetime.now(timezone.utc),
        )
        assert state == PresenceState.IDLE


# ---------------------------------------------------------------------------
# T426-06 through T426-09: _parse_output() — SSH stdout parsing
# ---------------------------------------------------------------------------


class TestParseOutput:
    """T426-06–09: SSH stdout parsing handles valid, empty, and malformed data."""

    def setup_method(self):
        self.svc = _make_service()

    def test_parse_full_working_output(self):
        """T426-06: Three valid lines — active, no pause, running → working."""
        stdout = "active\n\nrunning\n"
        result = self.svc._parse_output("dan", stdout, _NOW)

        assert result.name == "dan"
        assert result.state == PresenceState.WORKING
        assert result.detail is None

    def test_parse_idle_output(self):
        """T426-06b: poller active, no pause, no SDK → idle."""
        stdout = "active\n\n\n"
        result = self.svc._parse_output("dan", stdout, _NOW)
        assert result.state == PresenceState.IDLE

    def test_parse_rate_limited_output(self):
        """T426-06c: poller active, future pause flag → rate_limited."""
        stdout = f"active\n{_future_ts()}\n\n"
        result = self.svc._parse_output("dan", stdout, _NOW)
        assert result.state == PresenceState.RATE_LIMITED

    def test_parse_output_expired_pause_flag_is_idle(self):
        """T426-07: Pause flag timestamp in the past is treated as no pause → idle."""
        stdout = f"active\n{_past_ts()}\n\n"
        result = self.svc._parse_output("dan", stdout, _NOW)
        assert result.state == PresenceState.IDLE

    def test_parse_output_malformed_timestamp_treated_as_no_pause(self):
        """T426-08: Malformed pause timestamp → treated as no pause (no crash)."""
        stdout = "active\nnot-a-date\n\n"
        result = self.svc._parse_output("dan", stdout, _NOW)
        # Should not raise; treats malformed timestamp as absent
        assert result.state in {PresenceState.IDLE, PresenceState.WORKING}

    def test_parse_output_short_single_line_offline(self):
        """T426-09: Output with fewer than 3 lines — poller not active → offline."""
        stdout = "inactive"
        result = self.svc._parse_output("dan", stdout, _NOW)
        assert result.state == PresenceState.OFFLINE

    def test_parse_output_completely_empty_offline(self):
        """T426-09b: Completely empty output → poller not active → offline."""
        stdout = ""
        result = self.svc._parse_output("dan", stdout, _NOW)
        assert result.state == PresenceState.OFFLINE


# ---------------------------------------------------------------------------
# T426-10 through T426-12: _probe() — SSH subprocess handling
# ---------------------------------------------------------------------------


class TestProbe:
    """T426-10–12: _probe() gracefully handles timeouts, errors, and happy paths."""

    def setup_method(self):
        self.svc = _make_service()
        self.agent = _make_agent("dan", "10.0.0.1")

    @pytest.mark.asyncio
    async def test_probe_timeout_returns_offline(self):
        """T426-10: asyncio.TimeoutError during SSH → offline with detail message."""
        with patch(
            "tech_dev_agents.ops_console.services.presence_service.asyncio.wait_for",
            side_effect=asyncio.TimeoutError("probe timed out"),
        ):
            result = await self.svc._probe(self.agent)

        assert result.name == "dan"
        assert result.state == PresenceState.OFFLINE
        assert result.detail is not None
        assert "SSH probe failed" in result.detail

    @pytest.mark.asyncio
    async def test_probe_oserror_returns_offline(self):
        """T426-10b: OSError (e.g. ssh binary missing) → offline with detail."""
        with patch(
            "tech_dev_agents.ops_console.services.presence_service.asyncio.wait_for",
            side_effect=OSError("No such file or directory: ssh"),
        ):
            result = await self.svc._probe(self.agent)

        assert result.state == PresenceState.OFFLINE
        assert result.detail is not None

    @pytest.mark.asyncio
    async def test_probe_ssh_returncode_255_returns_offline(self):
        """T426-11: SSH exit code 255 (connection failure) → offline with stderr detail."""
        mock_proc = MagicMock()
        mock_proc.returncode = 255
        mock_proc.communicate = AsyncMock(return_value=(b"", b"ssh: connect to host 10.0.0.1 port 443: Connection refused"))

        with patch(
            "tech_dev_agents.ops_console.services.presence_service.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=mock_proc),
        ):
            result = await self.svc._probe(self.agent)

        assert result.state == PresenceState.OFFLINE
        assert "Connection refused" in (result.detail or "")

    @pytest.mark.asyncio
    async def test_probe_happy_path_returns_parsed_state(self):
        """T426-12: Successful SSH → parses stdout and returns derived state."""
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"active\n\nrunning\n", b""))

        with patch(
            "tech_dev_agents.ops_console.services.presence_service.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=mock_proc),
        ):
            result = await self.svc._probe(self.agent)

        assert result.name == "dan"
        assert result.state == PresenceState.WORKING

    @pytest.mark.asyncio
    async def test_probe_idle_agent_returns_idle(self):
        """T426-12b: Successful SSH with no SDK process → idle."""
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"active\n\n\n", b""))

        with patch(
            "tech_dev_agents.ops_console.services.presence_service.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=mock_proc),
        ):
            result = await self.svc._probe(self.agent)

        assert result.state == PresenceState.IDLE


# ---------------------------------------------------------------------------
# T426-13 through T426-16: get_all_presence() — fleet fan-out + caching
# ---------------------------------------------------------------------------


class TestGetAllPresence:
    """T426-13–16: Fleet fan-out, cache hit/miss, exception coercion, disabled agents."""

    @pytest.mark.asyncio
    async def test_cache_hit_returns_cached_result_without_probing(self):
        """T426-13: If cache is warm, no SSH probes are fired."""
        svc = _make_service()
        cached_response = AgentPresenceListResponse(
            agents=[
                AgentPresence(name="dan", state=PresenceState.IDLE, checked_at=_NOW),
            ],
            cached=True,
            checked_at=_NOW,
        )
        # Pre-warm the cache
        svc._cache.set("all_presence", cached_response)

        with patch.object(svc, "_probe", new=AsyncMock()) as mock_probe:
            result = await svc.get_all_presence()

        mock_probe.assert_not_called()
        assert result.cached is True
        assert len(result.agents) == 1

    @pytest.mark.asyncio
    async def test_cache_miss_probes_all_enabled_agents(self):
        """T426-14: Cold cache → _probe called once per enabled agent."""
        svc = _make_service(agents=[
            _make_agent("dan", "10.0.0.1", enabled=True),
            _make_agent("derrick", "10.0.0.2", enabled=True),
        ])

        async def fake_probe(agent):
            return AgentPresence(name=agent.name, state=PresenceState.IDLE, checked_at=_NOW)

        with patch.object(svc, "_probe", side_effect=fake_probe) as mock_probe:
            result = await svc.get_all_presence()

        assert mock_probe.call_count == 2
        assert result.cached is False
        names = {a.name for a in result.agents}
        assert names == {"dan", "derrick"}

    @pytest.mark.asyncio
    async def test_probe_exception_coerced_to_offline(self):
        """T426-15: If _probe raises, gather coerces to offline AgentPresence."""
        svc = _make_service(agents=[_make_agent("dan", "10.0.0.1")])

        with patch.object(
            svc, "_probe", side_effect=RuntimeError("unexpected error")
        ):
            result = await svc.get_all_presence()

        assert len(result.agents) == 1
        assert result.agents[0].state == PresenceState.OFFLINE
        assert result.agents[0].name == "dan"

    @pytest.mark.asyncio
    async def test_disabled_agents_are_excluded(self):
        """T426-16: Agents with enabled=False are not probed and not in the response."""
        svc = _make_service(agents=[
            _make_agent("dan", "10.0.0.1", enabled=True),
            _make_agent("retired-bot", "10.0.0.9", enabled=False),
        ])

        async def fake_probe(agent):
            return AgentPresence(name=agent.name, state=PresenceState.IDLE, checked_at=_NOW)

        with patch.object(svc, "_probe", side_effect=fake_probe):
            result = await svc.get_all_presence()

        names = {a.name for a in result.agents}
        assert "retired-bot" not in names
        assert "dan" in names

    @pytest.mark.asyncio
    async def test_result_is_cached_after_fresh_probe(self):
        """T426-13b: Response is stored in cache after successful fan-out."""
        svc = _make_service(agents=[_make_agent("dan")])

        async def fake_probe(agent):
            return AgentPresence(name=agent.name, state=PresenceState.IDLE, checked_at=_NOW)

        with patch.object(svc, "_probe", side_effect=fake_probe):
            await svc.get_all_presence()

        assert svc._cache.get("all_presence") is not None

    @pytest.mark.asyncio
    async def test_mixed_results_some_offline(self):
        """T426-15b: Mixed results — one working, one offline from exception."""
        svc = _make_service(agents=[
            _make_agent("dan", "10.0.0.1"),
            _make_agent("derrick", "10.0.0.2"),
        ])

        async def probe_side_effect(agent):
            if agent.name == "dan":
                return AgentPresence(name="dan", state=PresenceState.WORKING, checked_at=_NOW)
            raise OSError("SSH refused")

        with patch.object(svc, "_probe", side_effect=probe_side_effect):
            result = await svc.get_all_presence()

        states = {a.name: a.state for a in result.agents}
        assert states["dan"] == PresenceState.WORKING
        assert states["derrick"] == PresenceState.OFFLINE


# ---------------------------------------------------------------------------
# T426-17: _build_ssh_command() — SSH command structure
# ---------------------------------------------------------------------------


class TestBuildSshCommand:
    """T426-17: SSH command includes correct flags and configurable parameters."""

    def setup_method(self):
        self.svc = _make_service()

    def test_ssh_command_includes_required_flags(self):
        """T426-17: SSH command includes BatchMode=yes, StrictHostKeyChecking=no, LogLevel=ERROR."""
        agent = _make_agent("dan", "10.0.0.1")
        cmd = self.svc._build_ssh_command(agent)

        cmd_str = " ".join(cmd)
        assert "BatchMode=yes" in cmd_str
        assert "StrictHostKeyChecking=no" in cmd_str
        assert "LogLevel=ERROR" in cmd_str
        assert "ConnectTimeout=5" in cmd_str

    def test_ssh_command_uses_agent_host_and_ssh_port(self):
        """T426-17b: Host and ssh_port are correctly set in the SSH command."""
        agent = _make_agent("dan", "10.0.0.1")
        agent.ssh_port = 443
        cmd = self.svc._build_ssh_command(agent)

        assert "10.0.0.1" in " ".join(cmd)
        assert "443" in cmd  # passed as -p argument

    def test_ssh_command_remote_combines_three_checks(self):
        """T426-17c: Remote command contains systemctl, cat pause flag, and pgrep."""
        agent = _make_agent("dan", "10.0.0.1")
        cmd = self.svc._build_ssh_command(agent)
        remote_cmd = cmd[-1]  # Last element is the remote shell command string

        assert "systemctl" in remote_cmd
        assert "dispatch-poller" in remote_cmd
        assert "dispatch-poller-paused-until" in remote_cmd
        assert "claude_sdk" in remote_cmd

    def test_ssh_command_respects_custom_poller_service_name(self):
        """T426-17d: Custom poller_service setting is used in the remote command."""
        agent_service = MagicMock()
        agent_service.get_registry.return_value = []
        svc = PresenceService(
            agent_service=agent_service,
            poller_service="my-custom-poller",
        )
        agent = _make_agent("dan", "10.0.0.1")
        cmd = svc._build_ssh_command(agent)
        remote_cmd = cmd[-1]

        assert "my-custom-poller" in remote_cmd

    def test_ssh_command_uses_default_port_443_when_ssh_port_absent(self):
        """T426-17e: AgentRecord without ssh_port attribute falls back to port 443."""
        agent = MagicMock(spec=["name", "host", "port", "enabled"])
        agent.name = "dan"
        agent.host = "10.0.0.1"
        agent.port = 8080
        agent.enabled = True
        # NOTE: no ssh_port attribute — getattr fallback to 443

        svc = _make_service()
        cmd = svc._build_ssh_command(agent)
        assert "443" in cmd


# ---------------------------------------------------------------------------
# T426-R01: SSH key-only auth flags (AC1 — PR #55 remediation)
# ---------------------------------------------------------------------------


class TestRemediationSSHKeyAuth:
    """T426-R01: SSH command must enforce key-only auth to prevent credential exposure."""

    def test_ssh_command_includes_password_auth_no(self):
        """T426-R01a: SSH command includes PasswordAuthentication=no (AC1)."""
        svc = _make_service()
        agent = _make_agent("dan", "10.0.0.1")
        cmd = svc._build_ssh_command(agent)
        cmd_str = " ".join(cmd)
        assert "PasswordAuthentication=no" in cmd_str, (
            "SSH command must include -o PasswordAuthentication=no to prevent "
            "credentials from appearing in /proc/{pid}/cmdline"
        )

    def test_ssh_command_includes_preferred_authentications_publickey(self):
        """T426-R01b: SSH command includes PreferredAuthentications=publickey (AC1)."""
        svc = _make_service()
        agent = _make_agent("dan", "10.0.0.1")
        cmd = svc._build_ssh_command(agent)
        cmd_str = " ".join(cmd)
        assert "PreferredAuthentications=publickey" in cmd_str, (
            "SSH command must include -o PreferredAuthentications=publickey "
            "to enforce key-only authentication"
        )


# ---------------------------------------------------------------------------
# T426-R02/R03: Command injection validation (AC2 — PR #55 remediation)
# ---------------------------------------------------------------------------


class TestRemediationInputValidation:
    """T426-R02/R03: Host values must be validated before SSH interpolation."""

    def test_validate_ssh_target_accepts_valid_ip(self):
        """T426-R02a: Valid IPv4 address passes _validate_ssh_target without error."""
        svc = _make_service()
        # Must not raise
        svc._validate_ssh_target("10.0.0.1")

    def test_validate_ssh_target_accepts_valid_fqdn(self):
        """T426-R02b: Valid FQDN passes _validate_ssh_target without error."""
        svc = _make_service()
        svc._validate_ssh_target("agent-01.internal.example.com")

    @pytest.mark.parametrize("malicious_value", [
        "10.0.0.1; rm -rf /",
        "host|cat /etc/passwd",
        "host&bad-cmd",
        "host`id`",
        "host$(whoami)",
        "host(subshell)",
        "host\ninjected-command",
        "host with spaces",
    ])
    def test_validate_ssh_target_rejects_shell_metacharacters(self, malicious_value):
        """T426-R02c: Values containing shell metacharacters raise ValueError (AC2)."""
        svc = _make_service()
        with pytest.raises(ValueError):
            svc._validate_ssh_target(malicious_value)

    @pytest.mark.asyncio
    async def test_probe_returns_offline_for_invalid_host(self):
        """T426-R03: _probe with malicious host returns offline without spawning SSH (AC2)."""
        svc = _make_service()
        agent = _make_agent("dan", "10.0.0.1; bad-command")

        with patch(
            "tech_dev_agents.ops_console.services.presence_service.asyncio.create_subprocess_exec",
        ) as mock_exec:
            result = await svc._probe(agent)

        mock_exec.assert_not_called()
        assert result.state == PresenceState.OFFLINE
        assert result.detail is not None


# ---------------------------------------------------------------------------
# T426-R04: Cache aliasing prevention (AC8 — PR #55 remediation)
# ---------------------------------------------------------------------------


class TestRemediationCacheAliasing:
    """T426-R04: Cached response must not be mutated in-place (model_copy required)."""

    @pytest.mark.asyncio
    async def test_cached_response_returned_with_cached_true_without_mutating_store(self):
        """T426-R04: Second call returns new object with cached=True; stored stays cached=False."""
        svc = _make_service(agents=[_make_agent("dan")])

        async def fake_probe(agent):
            return AgentPresence(name=agent.name, state=PresenceState.IDLE, checked_at=_NOW)

        with patch.object(svc, "_probe", side_effect=fake_probe):
            await svc.get_all_presence()

        # Grab the stored reference BEFORE the cache-hit call
        stored = svc._cache.get("all_presence")
        assert stored is not None
        assert stored.cached is False, "Stored cache entry should have cached=False"

        # Second call hits the cache
        second = await svc.get_all_presence()

        assert second.cached is True, "Returned response should have cached=True"
        # The stored object must NOT have been mutated
        assert stored.cached is False, (
            "Stored cache entry was mutated in-place. Use model_copy(update={'cached': True}) "
            "instead of cached.cached = True"
        )
        assert second is not stored, "Returned object must be a new copy, not the stored object"


# ---------------------------------------------------------------------------
# T426-R05: Zombie SSH process prevention (AC7 — PR #55 remediation)
# ---------------------------------------------------------------------------


class TestRemediationZombieProcessPrevention:
    """T426-R05: SSH subprocess must be killed and reaped on communicate() timeout."""

    @pytest.mark.asyncio
    async def test_timeout_kills_and_waits_for_ssh_subprocess(self):
        """T426-R05: communicate() timeout triggers proc.kill() then proc.wait() (AC7)."""
        svc = _make_service()
        agent = _make_agent("dan", "10.0.0.1")

        mock_proc = MagicMock()
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock(return_value=None)
        # communicate() raises TimeoutError, simulating a hung SSH connection
        mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError("hung SSH"))

        with patch(
            "tech_dev_agents.ops_console.services.presence_service.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=mock_proc),
        ):
            result = await svc._probe(agent)

        mock_proc.kill.assert_called_once(), (
            "proc.kill() must be called to terminate orphaned SSH subprocess"
        )
        mock_proc.wait.assert_called_once(), (
            "proc.wait() must be called after kill() to reap the zombie process"
        )
        assert result.state == PresenceState.OFFLINE


# ---------------------------------------------------------------------------
# T426-R06: Bounded cache growth (AC3 — PR #55 remediation)
# ---------------------------------------------------------------------------


class TestRemediationBoundedCache:
    """T426-R06: PresenceService cache must have a bounded maxsize (AC3)."""

    def test_cache_has_maxsize_attribute(self):
        """T426-R06: svc._cache exposes a maxsize attribute set to at least 1."""
        svc = _make_service()
        assert hasattr(svc._cache, "maxsize"), (
            "Cache must have a maxsize attribute to prevent unbounded growth. "
            "Pass maxsize= to TTLCache or switch to cachetools.TTLCache(maxsize=1, ttl=N)."
        )
        assert svc._cache.maxsize >= 1, "Cache maxsize must be >= 1"
