"""USB compatibility mode (UAS -> Bulk-Only) without hardware: fake sysfs + fake runner.

Fixtures: tests/fixtures/usb_dji_fly_drive/ (UAS: SCSI identity + health only) and
tests/fixtures/usb_dji_fly_drive_bot/ (Bulk-Only: full ATA table, see its README).
"""
import json
import os
import subprocess
from datetime import datetime

import pytest

from smartlinux.core import usb_compat
from smartlinux.core.models import DiskInfo
from smartlinux.core.smart_parser import SmartParser
from smartlinux.core.usb_compat import (
    CmdResult, CompatModeError, UasCandidate, Volume,
    check_guards, detect_uas_smart_block, list_volumes, preflight, run_compat_read,
)

FIX = os.path.join(os.path.dirname(__file__), "fixtures")
UAS_DIR = os.path.join(FIX, "usb_dji_fly_drive")
BOT_DIR = os.path.join(FIX, "usb_dji_fly_drive_bot")
UUID1 = "09e3f9bd-ba6b-4d72-8ac4-55dd09dd2da9"
UUID2 = "eea6314d-5429-4310-82bb-c9bce122f5af"


def _json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def uas_disk(path="/dev/sda"):
    return SmartParser.parse_smart_json(_json(os.path.join(UAS_DIR, "smartctl_j_a_scsi.json")), path)


def bot_disk(path):
    return SmartParser.parse_smart_json(_json(os.path.join(BOT_DIR, "smartctl_j_a_none.json")), path)


def lsblk_tree(**changes):
    """The Bulk-Only lsblk capture (two mounted partitions), optionally modified."""
    data = _json(os.path.join(BOT_DIR, "lsblk_bot_mode.json"))
    for i, child in enumerate(data["blockdevices"][0]["children"]):
        child.update(changes.get(f"child{i}", {}))
    data["blockdevices"][0]["children"].extend(changes.get("extra", []))
    return json.dumps(data)


class FakeRunner:
    """Records every command; answers from handlers keyed by the first words."""

    def __init__(self, fs=None, tree=None, tran="usb"):
        self.calls = []
        self.fs = fs
        self.tree = tree or lsblk_tree()
        self.tran = tran
        self.mounted = set()      # UUIDs mounted again (findmnt)
        self.unmounted = set()    # device paths unmounted (lsblk then shows no mountpoint)
        self.handlers = {}

    def on(self, *prefix, fn):
        self.handlers[prefix] = fn
        return self

    def __call__(self, cmd, timeout=10):
        self.calls.append(list(cmd))
        for prefix in sorted(self.handlers, key=len, reverse=True):
            if tuple(cmd[:len(prefix)]) == prefix:
                return self.handlers[prefix](cmd)
        return self.default(cmd)

    def default(self, cmd):
        name = cmd[0]
        if name == "lsblk" and "TRAN" in cmd:
            return CmdResult(0, self.tran + "\n")
        if name == "lsblk":
            data = json.loads(self.tree)
            for child in data["blockdevices"][0].get("children", []):
                if child.get("path") in self.unmounted:
                    child["mountpoint"] = None
            return CmdResult(0, json.dumps(data))
        if name == "lsof":
            return CmdResult(1, "")
        if name == "udisksctl":
            if cmd[1] == "mount":
                self.mounted.add(cmd[-1].rsplit("/", 1)[-1])
            else:
                self.unmounted.add(cmd[-1])
            return CmdResult(0, "")
        if name == "findmnt":
            uuid = cmd[-1].split("=", 1)[1]
            return CmdResult(0, "/run/media/x\n") if uuid in self.mounted else CmdResult(1, "")
        if name == "pkexec":
            action = cmd[2]
            if self.fs:
                if action == "set-bot":
                    self.fs.set_driver("usb-storage")
                    self.fs.set_block("sdb")
                else:
                    self.fs.set_driver("uas")
                    self.fs.set_block("sdc")
            return CmdResult(0, "ok\n")
        return CmdResult(127, "", "unexpected command")

    def names(self):
        out = []
        for c in self.calls:
            if c[0] == "udisksctl":
                out.append(f"{c[1]} {c[-1]}")
            elif c[0] == "pkexec":
                out.append(c[2])
        return out


class Clock:
    def __init__(self):
        self.t = 0.0

    def sleep(self, s):
        self.t += s

    def __call__(self):
        return self.t


def candidate(**over):
    base = dict(bus_id="2-1.2", vid="0bc2", pid="a020", name="Seagate DJI Fly Drive",
                device_path="/dev/sda", interface="2-1.2:1.0")
    base.update(over)
    return UasCandidate(**base)


def flow(fs, runner, read_smart=None, **kw):
    clock = Clock()
    reads = []

    def default_read(path):
        reads.append(path)
        return bot_disk(path)

    result = run_compat_read(
        candidate(), read_smart or default_read, runner=runner, sysfs_root=fs.root,
        helper_path="/usr/lib/smartlinux/usb-mode-helper", sleep=clock.sleep, clock=clock,
        now=lambda: datetime(2026, 10, 11, 13, 9), **kw)
    return result, reads


# ================================================================== detection

def test_detects_uas_adapter_hiding_smart(fake_sysfs):
    fs = fake_sysfs()
    cand = detect_uas_smart_block(uas_disk(), sysfs_root=fs.root, runner=FakeRunner())
    assert cand == UasCandidate(bus_id="2-1.2", vid="0bc2", pid="a020", name="Seagate DJI Fly Drive",
                                device_path="/dev/sda", interface="2-1.2:1.0")


def test_detection_reads_nothing_but_lsblk(fake_sysfs):
    fs = fake_sysfs()
    runner = FakeRunner()
    detect_uas_smart_block(uas_disk(), sysfs_root=fs.root, runner=runner)
    assert [c[0] for c in runner.calls] == ["lsblk"]
    assert fs.quirks() == "" and fs.bound_requests() == ""


def test_not_detected_when_already_bulk_only(fake_sysfs):
    fs = fake_sysfs(driver="usb-storage")
    assert detect_uas_smart_block(uas_disk(), sysfs_root=fs.root, runner=FakeRunner()) is None


def test_not_detected_when_reading_has_full_table(fake_sysfs):
    fs = fake_sysfs()
    assert detect_uas_smart_block(bot_disk("/dev/sda"), sysfs_root=fs.root, runner=FakeRunner()) is None


def test_not_detected_for_remote_disk(fake_sysfs):
    fs = fake_sysfs()
    disk = uas_disk()
    disk.is_remote = True
    assert detect_uas_smart_block(disk, sysfs_root=fs.root, runner=FakeRunner()) is None


def test_not_detected_without_usb_transport(fake_sysfs):
    fs = fake_sysfs()
    assert detect_uas_smart_block(uas_disk(), sysfs_root=fs.root, runner=FakeRunner(tran="sata")) is None


def test_not_detected_for_non_usb_block(fake_sysfs, tmp_path):
    fs = fake_sysfs()
    sata = tmp_path / "sys" / "devices" / "pci0000:00" / "ata1" / "host1" / "target1:0:0" / "1:0:0:0"
    (sata / "block" / "sdz").mkdir(parents=True)
    os.symlink(sata, sata / "block" / "sdz" / "device")
    os.symlink(sata / "block" / "sdz", os.path.join(fs.root, "block", "sdz"))
    assert detect_uas_smart_block(uas_disk("/dev/sdz"), sysfs_root=fs.root, runner=FakeRunner()) is None


@pytest.mark.parametrize("vid,pid", [("0BCZ", "a020"), ("0bc2", "a02"), ("0bc2:a020:u,", "1")])
def test_not_detected_when_sysfs_ids_are_malformed(fake_sysfs, vid, pid):
    fs = fake_sysfs(vid=vid, pid=pid)
    assert detect_uas_smart_block(uas_disk(), sysfs_root=fs.root, runner=FakeRunner()) is None


@pytest.mark.parametrize("path", ["/dev/../sda", "/dev/sda;reboot", "/dev/disk/by-id/usb-x"])
def test_not_detected_for_odd_device_paths(fake_sysfs, path):
    fs = fake_sysfs()
    assert detect_uas_smart_block(uas_disk(path), sysfs_root=fs.root, runner=FakeRunner()) is None


# ================================================================== volumes & guards

def test_list_volumes_from_lsblk_capture():
    vols = list_volumes("/dev/sda", FakeRunner())
    assert [v.path for v in vols] == ["/dev/sda", "/dev/sda1", "/dev/sda2"]
    assert vols[1].label == "USB_BTRFS" and vols[1].uuid == UUID1
    assert vols[2].mountpoints == ["/run/media/hbarchini/USB_DATA"]
    assert [v.path for v in vols if v.is_mounted] == ["/dev/sda1", "/dev/sda2"]


def test_list_volumes_falls_back_to_old_lsblk_columns():
    runner = FakeRunner()
    runner.on("lsblk", "-J", "-p", "-o", "NAME,PATH,TYPE,FSTYPE,UUID,LABEL,MOUNTPOINTS",
              fn=lambda cmd: CmdResult(1, "", "lsblk: unknown column: MOUNTPOINTS"))
    assert len(list_volumes("/dev/sda", runner)) == 3


def test_guards_pass_for_plain_data_partitions():
    assert check_guards("/dev/sda", list_volumes("/dev/sda", FakeRunner()), FakeRunner()) == []


def test_guard_refuses_non_usb_disk():
    problems = check_guards("/dev/sda", [], FakeRunner(tran="sata"))
    assert problems and "not connected over USB" in problems[0]


@pytest.mark.parametrize("mp", ["/", "/boot", "/boot/efi", "/home"])
def test_guard_refuses_system_mounts(mp):
    runner = FakeRunner(tree=lsblk_tree(child0={"mountpoint": mp}))
    problems = check_guards("/dev/sda", list_volumes("/dev/sda", runner), runner)
    assert any(mp in p and "system needs" in p for p in problems)


def test_guard_refuses_swap():
    runner = FakeRunner(tree=lsblk_tree(child1={"fstype": "swap", "mountpoint": "[SWAP]"}))
    problems = check_guards("/dev/sda", list_volumes("/dev/sda", runner), runner)
    assert any("swap" in p for p in problems)


def test_guard_refuses_luks_and_mapper():
    luks = {"fstype": "crypto_LUKS", "mountpoint": None,
            "children": [{"name": "/dev/mapper/luks-1", "path": "/dev/mapper/luks-1", "type": "crypt",
                          "fstype": "ext4", "mountpoint": "/mnt/secret"}]}
    runner = FakeRunner(tree=lsblk_tree(child1=luks))
    problems = check_guards("/dev/sda", list_volumes("/dev/sda", runner), runner)
    assert any("/dev/sda2" in p and "LUKS" in p for p in problems)
    assert any("/dev/mapper/luks-1" in p for p in problems)


def test_guard_lists_processes_using_a_volume_lsof():
    runner = FakeRunner()
    runner.on("lsof", fn=lambda cmd: CmdResult(0, "p4242\ncvlc\nf3\np77\ncbash\n")
              if cmd[-1].endswith("USB_DATA") else CmdResult(1, ""))
    problems = check_guards("/dev/sda", list_volumes("/dev/sda", runner), runner)
    assert len(problems) == 1
    assert "USB_DATA" in problems[0] and "vlc (PID 4242)" in problems[0] and "bash (PID 77)" in problems[0]


def test_guard_lists_processes_with_fuser_when_lsof_missing():
    runner = FakeRunner()
    runner.on("lsof", fn=lambda cmd: CmdResult(127, "", "not found"))
    runner.on("fuser", fn=lambda cmd: CmdResult(0, " 1234c 99m", "/mnt:")
              if cmd[-1].endswith("USB_BTRFS") else CmdResult(1, ""))
    runner.on("ps", fn=lambda cmd: CmdResult(0, "   99 rsync\n 1234 nautilus\n"))
    problems = check_guards("/dev/sda", list_volumes("/dev/sda", runner), runner)
    assert len(problems) == 1
    assert "rsync (PID 99)" in problems[0] and "nautilus (PID 1234)" in problems[0]


def test_preflight_reports_missing_helper(fake_sysfs, tmp_path):
    fs = fake_sysfs()
    vols, problems = preflight(candidate(), FakeRunner(), helper_path=str(tmp_path / "nope"), sysfs_root=fs.root)
    assert len(vols) == 3
    assert any("helper" in p and "not installed" in p for p in problems)


@pytest.mark.parametrize("over", [{"bus_id": "2-1.2 ; reboot"}, {"bus_id": "../../x"},
                                  {"vid": "0BC2"}, {"pid": "a020:u"},
                                  {"device_path": "/dev/sda --foo"}])
def test_tampered_candidate_is_rejected_before_any_command(fake_sysfs, over):
    fs = fake_sysfs()
    runner = FakeRunner(fs)
    with pytest.raises(CompatModeError):
        run_compat_read(candidate(**over), bot_disk, runner=runner, sysfs_root=fs.root)
    with pytest.raises(CompatModeError):
        preflight(candidate(**over), runner, sysfs_root=fs.root)
    assert runner.calls == []


@pytest.mark.parametrize("change", ["replugged_elsewhere", "now_bulk_only", "other_device"])
def test_stale_candidate_is_rejected(fake_sysfs, change):
    if change == "replugged_elsewhere":
        fs = fake_sysfs(block="sdb")   # /dev/sda is gone, the drive is /dev/sdb now
    elif change == "now_bulk_only":
        fs = fake_sysfs(driver="usb-storage")
    else:
        fs = fake_sysfs(pid="a021")
    runner = FakeRunner(fs)
    with pytest.raises(CompatModeError, match="Scan"):
        run_compat_read(candidate(), bot_disk, runner=runner, sysfs_root=fs.root)
    assert runner.calls == []


# ================================================================== flow

def test_flow_order_and_result(fake_sysfs):
    fs = fake_sysfs()
    runner = FakeRunner(fs)
    result, reads = flow(fs, runner)

    assert runner.names() == [
        "unmount /dev/sda1", "unmount /dev/sda2",
        "set-bot",
        "restore",
        f"mount /dev/disk/by-uuid/{UUID1}", f"mount /dev/disk/by-uuid/{UUID2}",
    ]
    assert reads == ["/dev/sdb"]  # the node found again by VID/PID after re-enumeration
    pk = [c for c in runner.calls if c[0] == "pkexec"]
    assert pk[0] == ["pkexec", "/usr/lib/smartlinux/usb-mode-helper", "set-bot", "2-1.2", "0bc2", "a020"]
    assert pk[1][2:] == ["restore", "2-1.2", "0bc2", "a020"]

    disk = result.disk
    assert result.restored and not result.restore_error and not result.remount_warnings
    assert disk.device_path == "/dev/sdc" and disk.name == "sdc"
    assert disk.model == "IND-S325S001TX" and disk.power_on_hours == 196 and len(disk.attributes) == 29
    assert disk.compat_read_note == ("Read in compatibility mode on 2026-10-11 13:09; "
                                     "the drive is back in normal (UAS) mode.")
    assert fs.driver() == "uas"


def test_no_selftest_or_write_commands_are_run(fake_sysfs):
    fs = fake_sysfs()
    runner = FakeRunner(fs)
    flow(fs, runner)
    flat = [" ".join(c) for c in runner.calls]
    assert not any(" -t " in c or "smartctl" in c for c in flat)


def test_restore_runs_even_if_reading_fails(fake_sysfs):
    fs = fake_sysfs()
    runner = FakeRunner(fs)

    def broken_read(path):
        raise RuntimeError("smartctl crashed")

    with pytest.raises(CompatModeError, match="smartctl crashed"):
        flow(fs, runner, read_smart=broken_read)
    assert runner.names()[2:4] == ["set-bot", "restore"]
    assert runner.names()[-2:] == [f"mount /dev/disk/by-uuid/{UUID1}", f"mount /dev/disk/by-uuid/{UUID2}"]
    assert fs.driver() == "uas"


def test_restore_runs_if_set_bot_fails(fake_sysfs):
    fs = fake_sysfs()
    runner = FakeRunner(fs)
    runner.on("pkexec", "/usr/lib/smartlinux/usb-mode-helper", "set-bot",
              fn=lambda cmd: CmdResult(4, "", "usb-mode-helper: timed out after 60 s"))
    with pytest.raises(CompatModeError, match="timed out"):
        flow(fs, runner)
    assert "restore" in runner.names()


def test_restore_runs_if_set_bot_call_times_out(fake_sysfs):
    fs = fake_sysfs()
    runner = FakeRunner(fs)

    def hang(cmd):
        raise subprocess.TimeoutExpired(cmd, 300)

    runner.on("pkexec", "/usr/lib/smartlinux/usb-mode-helper", "set-bot", fn=hang)
    with pytest.raises(CompatModeError):
        flow(fs, runner)
    assert "restore" in runner.names()


def test_restore_runs_if_device_never_reappears(fake_sysfs):
    fs = fake_sysfs()
    runner = FakeRunner(fs)

    def set_bot_lost(cmd):
        fs.set_block(None)
        return CmdResult(0)

    runner.on("pkexec", "/usr/lib/smartlinux/usb-mode-helper", "set-bot", fn=set_bot_lost)
    with pytest.raises(CompatModeError, match="did not reappear"):
        flow(fs, runner)
    assert "restore" in runner.names()


@pytest.mark.parametrize("rc", [126, 127])
def test_denied_authorization_changes_nothing(fake_sysfs, rc):
    fs = fake_sysfs()
    runner = FakeRunner(fs)
    runner.on("pkexec", "/usr/lib/smartlinux/usb-mode-helper", "set-bot", fn=lambda cmd: CmdResult(rc))
    with pytest.raises(CompatModeError, match="not granted"):
        flow(fs, runner)
    assert "restore" not in runner.names()
    assert runner.names()[-2:] == [f"mount /dev/disk/by-uuid/{UUID1}", f"mount /dev/disk/by-uuid/{UUID2}"]


def test_failed_restore_is_reported(fake_sysfs):
    fs = fake_sysfs()
    runner = FakeRunner(fs)

    def restore_fails(cmd):
        return CmdResult(1, "", "usb-mode-helper: could not write usb-storage quirks")

    runner.on("pkexec", "/usr/lib/smartlinux/usb-mode-helper", "restore", fn=restore_fails)
    result, _ = flow(fs, runner)
    assert not result.restored and "quirks" in result.restore_error
    assert "could NOT be switched back" in result.disk.compat_read_note


def test_read_and_restore_failure_both_reported(fake_sysfs):
    fs = fake_sysfs()
    runner = FakeRunner(fs)
    runner.on("pkexec", "/usr/lib/smartlinux/usb-mode-helper", "restore",
              fn=lambda cmd: CmdResult(1, "", "boom"))

    def broken_read(path):
        raise RuntimeError("read failed")

    with pytest.raises(CompatModeError) as e:
        flow(fs, runner, read_smart=broken_read)
    assert "read failed" in str(e.value) and "Unplug and replug" in str(e.value)


def test_guard_refusal_stops_before_unmounting(fake_sysfs):
    fs = fake_sysfs()
    runner = FakeRunner(fs)
    runner.on("lsof", fn=lambda cmd: CmdResult(0, "p1\ncfirefox\n"))
    with pytest.raises(CompatModeError, match="firefox"):
        flow(fs, runner)
    assert runner.names() == []


def test_unmount_failure_remounts_and_never_calls_helper(fake_sysfs):
    fs = fake_sysfs()
    runner = FakeRunner(fs)
    runner.on("udisksctl", "unmount", "-b", "/dev/sda2",
              fn=lambda cmd: CmdResult(1, "", "target is busy"))
    with pytest.raises(CompatModeError, match="target is busy"):
        flow(fs, runner)
    assert runner.names() == ["unmount /dev/sda1", "unmount /dev/sda2", f"mount /dev/disk/by-uuid/{UUID1}"]


def test_automount_in_bulk_only_mode_is_unmounted_before_restore(fake_sysfs):
    fs = fake_sysfs()
    runner = FakeRunner(fs)

    def set_bot_and_desktop_automounts(cmd):
        fs.set_driver("usb-storage")
        fs.set_block("sdb")
        runner.unmounted.clear()  # the desktop mounted the partitions again
        return CmdResult(0)

    runner.on("pkexec", "/usr/lib/smartlinux/usb-mode-helper", "set-bot", fn=set_bot_and_desktop_automounts)
    flow(fs, runner)
    names = runner.names()
    assert names[:3] == ["unmount /dev/sda1", "unmount /dev/sda2", "set-bot"]
    assert names[3:6] == ["unmount /dev/sda1", "unmount /dev/sda2", "restore"]


def test_automount_check_never_blocks_restore(fake_sysfs):
    fs = fake_sysfs()
    runner = FakeRunner(fs)
    state = {"n": 0}

    def flaky_lsblk(cmd):
        state["n"] += 1
        return CmdResult(0, runner.tree) if state["n"] == 1 else CmdResult(0, "not json")

    runner.on("lsblk", "-J", fn=flaky_lsblk)
    result, _ = flow(fs, runner)
    assert "restore" in runner.names() and result.restored
    assert any("automatic mounts" in w for w in result.remount_warnings)


def test_desktop_automount_is_respected(fake_sysfs):
    fs = fake_sysfs()
    runner = FakeRunner(fs)
    runner.mounted = {UUID1, UUID2}  # the desktop already mounted them again
    result, _ = flow(fs, runner)
    assert not any(n.startswith("mount ") for n in runner.names())
    assert result.remount_warnings == []


def test_remount_failure_becomes_a_warning(fake_sysfs):
    fs = fake_sysfs()
    runner = FakeRunner(fs)
    runner.on("udisksctl", "mount", fn=lambda cmd: CmdResult(1, "", "Object is not a mountable filesystem"))
    result, _ = flow(fs, runner)
    assert result.restored
    assert len(result.remount_warnings) == 2
    assert "USB_BTRFS" in result.remount_warnings[0]


def test_volume_without_uuid_is_reported():
    runner = FakeRunner()
    clock = Clock()
    warnings = usb_compat._remount([Volume(path="/dev/sda1", mountpoints=["/mnt"])], runner,
                                   clock.sleep, clock, 5, 0)
    assert warnings and "no UUID" in warnings[0]
