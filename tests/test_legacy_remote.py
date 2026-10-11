"""End-to-end behaviour against a simulated WD My Cloud EX4 (smartctl 5.38, BusyBox),
replaying the real captures in tests/fixtures/legacy_smartctl_5_38."""
import os
import re

from smartlinux.core.models import HealthStatus, ServerConfig
from smartlinux.core.remote_common import BaseRemoteClient, parse_scan_text, smartctl_reply_usable

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "legacy_smartctl_5_38")


def fixture(name: str) -> str:
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read()


class FakeEX4(BaseRemoteClient):
    """Replays fixtures for the exact commands SmartLinux sends; records every command."""

    def __init__(self, username="root", password=None):
        super().__init__(ServerConfig(id="nas", name="EX4", host="192.168.100.14",
                                      username=username, password=password))
        self.commands = []
        self.accepted_types = {"sat"}

    def connect(self, timeout=8):
        return True, "ok"

    def disconnect(self):
        pass

    def is_connected(self):
        return True

    def _exec_with_sudo(self, cmd, timeout=15):
        return 127, "", "sh: sudo: not found"

    def _raw_exec(self, cmd, timeout=15):
        self.commands.append(cmd)
        if cmd.startswith("uname"):
            return 0, "Linux\n", ""
        if cmd.startswith("smartctl"):
            args = cmd.split()
            if any(a in ("-j", "--scan") for a in args):
                bad = "json" if "-j" in args else "scan"
                return 1, fixture(f"smartctl_{'json' if bad == 'json' else 'scan'}_unsupported.txt"), ""
            if "-t" in args:
                dtype = args[args.index("-d") + 1] if "-d" in args else None
                if dtype in self.accepted_types:
                    return 0, "smartctl version 5.38\n\nSelf-test has begun\n", ""
                return 1, "Smartctl: Device Read Identity Failed\n", ""
            dtype = args[args.index("-d") + 1] if "-d" in args else None
            if dtype in self.accepted_types and "-a" in args:
                return 0, fixture("smartctl_a.txt"), ""
            return 1, "smartctl version 5.38\n\nSmartctl: Device Read Identity Failed (not an ATA/ATAPI device)\n", ""
        if cmd.startswith("cat /proc/partitions"):
            return 0, fixture("proc_partitions.txt"), ""
        if cmd.startswith("for n in"):
            model = fixture("sys_block_sda_and_uname.txt").splitlines()[0]
            return 0, f"sda|{model}\n", ""
        return 127, "", f"sh: {cmd.split()[0]}: not found"


def test_discovery_without_scan_or_lsblk():
    nas = FakeEX4()
    drives = nas.discover_remote_drives()
    assert [d.device_path for d in drives] == ["/dev/sda"]
    d = drives[0]
    assert d.model == "WDC WD30EFRX-68E"
    assert d.size_bytes == 3_000_592_982_016 and d.size_human == "3.0 TB"
    assert d.is_remote and d.server_id == "nas"
    assert nas.uses_text_mode


def test_remote_commands_are_busybox_compatible():
    nas = FakeEX4()
    nas.discover_remote_drives()
    nas.read_remote_drive_smart("/dev/sda")
    for cmd in nas.commands:
        assert "$((" not in cmd
        assert not re.search(r"\bwhich\b", cmd)
        assert "lsblk" not in cmd
        assert "python" not in cmd and "udevadm" not in cmd


def test_read_falls_back_to_text_and_sat():
    nas = FakeEX4()
    disk = nas.read_remote_drive_smart("/dev/sda")
    assert disk.error_message is None
    assert disk.model == "WDC WD30EFRX-68EUZN0"
    assert disk.power_on_hours == 21883 and len(disk.attributes) == 17
    assert disk.health_status == HealthStatus.WARNING
    assert disk.server_name == "EX4" and disk.is_remote
    smartctl_cmds = [c for c in nas.commands if c.startswith("smartctl")]
    assert smartctl_cmds[0] == "smartctl -j -a /dev/sda"      # probe
    assert any("-d sat -a /dev/sda" in c and "-j" not in c for c in smartctl_cmds)


def test_text_mode_is_remembered_and_probe_not_repeated():
    nas = FakeEX4()
    nas.read_remote_drive_smart("/dev/sda")
    nas.commands.clear()
    nas.read_remote_drive_smart("/dev/sda")
    assert not any(" -j " in c for c in nas.commands if c.startswith("smartctl"))


def test_root_with_password_does_not_get_masked_by_missing_sudo():
    nas = FakeEX4(username="root", password="secret")
    disk = nas.read_remote_drive_smart("/dev/sda")
    assert disk.error_message is None and disk.model == "WDC WD30EFRX-68EUZN0"


def test_unreadable_drive_reports_error():
    nas = FakeEX4()
    nas.accepted_types = set()
    disk = nas.read_remote_drive_smart("/dev/sda")
    assert disk.health_status == HealthStatus.FAILED
    assert disk.error_message


def test_self_test_uses_device_type_that_worked():
    nas = FakeEX4()
    nas.read_remote_drive_smart("/dev/sda")
    ok, msg = nas.run_self_test("/dev/sda", "short")
    assert ok and "has begun" in msg
    assert nas.commands[-1] == "smartctl -d sat -t short /dev/sda"


def test_smartctl_reply_usable():
    assert smartctl_reply_usable(fixture("smartctl_a.txt"))
    assert smartctl_reply_usable(fixture("smartctl_json_unsupported.txt"))
    assert not smartctl_reply_usable("sh: smartctl: not found")
    assert not smartctl_reply_usable("smartctl 7.3 2022\nSmartctl open device: /dev/sda failed: Permission denied")


def test_parse_scan_text():
    out = "/dev/sda -d ata # /dev/sda, ATA device\n/dev/sdb -d scsi # /dev/sdb, SCSI device\n"
    assert parse_scan_text(out) == ["/dev/sda", "/dev/sdb"]
    assert parse_scan_text(fixture("smartctl_scan_unsupported.txt")) == []


def test_search_path_includes_synology_wd_module_dir():
    from smartlinux.core.remote_common import UNIVERSAL_PATH
    assert "/usr/local/modules/bin" in UNIVERSAL_PATH
