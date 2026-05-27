"""STORY-730: contract tests for hermes-log-sync RuntimeDirectory fix."""
from pathlib import Path
import pytest

REPO = Path(__file__).parent.parent.parent
SERVICE = REPO / "deployment/vm/hermes-log-sync.service"
SYNC_SH_VM = REPO / "deployment/vm/hermes-log-sync.sh"
SYNC_SH_PROMTAIL = REPO / "deployment/promtail/hermes-log-sync.sh"
PROMTAIL_CONFIGS = [
    REPO / "deployment/vm/promtail-config.yaml",
    REPO / "deployment/vm/promtail-config-derrick.yaml",
    REPO / "deployment/promtail/promtail-config-dan.yaml",
    REPO / "deployment/promtail/promtail-config-derrick.yaml",
]
RUNTIME_DIR = "/run/hermes-log-sync"
RUNTIME_LOG = f"{RUNTIME_DIR}/combined.log"
LEGACY_PATH = "/tmp/hermes-combined.log"


@pytest.fixture(scope="module")
def service_text():
    return SERVICE.read_text()


class TestT1RuntimeDirectoryDirective:
    def test_t1_has_runtime_directory(self, service_text):
        assert "RuntimeDirectory=hermes-log-sync" in service_text

    def test_t1_has_runtime_directory_mode(self, service_text):
        assert "RuntimeDirectoryMode=0755" in service_text

    def test_t1_no_old_install_workaround(self, service_text):
        assert "install -m 0664 -o hermes" not in service_text


class TestT2SimplerExecStartPre:
    def test_t2_execstartpre_uses_ln(self, service_text):
        assert "/bin/ln -sf" in service_text

    def test_t2_symlink_targets_runtime_log(self, service_text):
        assert RUNTIME_LOG in service_text

    def test_t2_symlink_creates_legacy_path(self, service_text):
        assert LEGACY_PATH in service_text


class TestT3LogSyncScriptPath:
    def test_t3_vm_script_writes_to_runtime(self):
        text = SYNC_SH_VM.read_text()
        assert RUNTIME_LOG in text

    def test_t3_promtail_script_writes_to_runtime(self):
        text = SYNC_SH_PROMTAIL.read_text()
        assert RUNTIME_LOG in text

    def test_t3_vm_script_no_tmp_write(self):
        text = SYNC_SH_VM.read_text()
        assert f">> {LEGACY_PATH}" not in text

    def test_t3_promtail_script_no_tmp_write(self):
        text = SYNC_SH_PROMTAIL.read_text()
        assert f">> {LEGACY_PATH}" not in text


class TestT4PromtailConfigsUpdated:
    @pytest.mark.parametrize("config", PROMTAIL_CONFIGS, ids=lambda p: p.name)
    def test_t4_promtail_config_watches_runtime(self, config):
        text = config.read_text()
        assert RUNTIME_LOG in text, f"{config.name} still references old /tmp path"


class TestT5BackwardCompatSymlink:
    def test_t5_execstartpre_creates_tmp_symlink(self, service_text):
        """The symlink means /tmp/hermes-combined.log still works for other writers."""
        assert f"-sf {RUNTIME_LOG} {LEGACY_PATH}" in service_text


class TestT6Story728SafetyBeltPreserved:
    def test_t6_start_limit_burst(self, service_text):
        assert "StartLimitBurst=5" in service_text

    def test_t6_start_limit_interval(self, service_text):
        assert "StartLimitIntervalSec=300" in service_text

    def test_t6_restart_on_failure_not_always(self, service_text):
        assert "Restart=on-failure" in service_text
        assert "Restart=always" not in service_text
