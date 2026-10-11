"""The privileged helper (packaging/common/usb-mode-helper), exercised on a fake sysfs.

The fake kernel reacts to "bind": it binds usb-storage if the quirks list forces
Bulk-Only for the device ("vid:pid:u"), uas otherwise, and gives the disk a new node.
"""
import json
import os

import pytest


class FakeKernel:
    """sleep() replacement that applies a pending re-attach, like the kernel would."""

    def __init__(self, fs, nodes=("sdb", "sdc", "sdd")):
        self.fs = fs
        self.nodes = list(nodes)
        self.seen = ""
        self.reattaches = 0
        self.t = 0.0

    def sleep(self, seconds):
        self.t += seconds
        bind = self.fs.bound_requests()
        if bind != self.seen:
            self.seen = bind
            self.reattaches += 1
            forced = f"{self.fs_vid_pid}:u" in self.fs.quirks().lower().split(",")
            self.fs.set_driver("usb-storage" if forced else "uas")
            self.fs.set_block(self.nodes.pop(0))
            # make the next bind distinguishable
            open(os.path.join(self.fs.drivers, "usb", "bind"), "w").close()
            self.seen = ""

    fs_vid_pid = "0bc2:a020"

    def clock(self):
        return self.t


def run(helper, fs, state_dir, cmd, kernel, timeout=60):
    fn = helper.set_bot if cmd == "set-bot" else helper.restore
    return fn(fs.root, str(state_dir), fs.bus_id, "0bc2", "a020",
              timeout=timeout, sleep=kernel.sleep, clock=kernel.clock)


# ---------------------------------------------------------------- arguments

@pytest.mark.parametrize("argv", [
    [], ["set-bot"], ["reboot", "2-1.2", "0bc2", "a020"],
    ["set-bot", "2-1.2;rm -rf /", "0bc2", "a020"],
    ["set-bot", "../2-1", "0bc2", "a020"],
    ["set-bot", "2-1.2", "0BC2", "a020"],
    ["set-bot", "2-1.2", "0bc2", "a02"],
    ["set-bot", "2-1.2", "0bc2", "a020:u,1234"],
    ["restore", "2-1.2", "0bc2", "a020", "extra"],
    ["set-bot", "usb2", "0bc2", "a020"],
])
def test_rejects_anything_but_strict_arguments(helper, argv):
    with pytest.raises(helper.HelperError) as e:
        helper.validate_args(argv)
    assert e.value.code == helper.EXIT_USAGE


def test_accepts_valid_arguments(helper):
    assert helper.validate_args(["set-bot", "2-1.2", "0bc2", "a020"]) == ("set-bot", "2-1.2", "0bc2", "a020")
    assert helper.validate_args(["restore", "10-3.1.4", "abcd", "0001"])[0] == "restore"


def test_main_returns_usage_code_without_touching_anything(helper, capsys):
    assert helper.main(["set-bot", "2-1.2", "ZZZZ", "a020"]) == helper.EXIT_USAGE
    assert "invalid" in capsys.readouterr().err


# ---------------------------------------------------------------- quirks list editing

def test_parse_and_format_quirks(helper):
    assert helper.parse_quirks("") == []
    assert helper.parse_quirks("\n") == []
    assert helper.parse_quirks("152d:0578:u, 174c:55aa:u,\n") == ["152d:0578:u", "174c:55aa:u"]
    assert helper.format_quirks(["a:b:u", "c:d:x"]) == "a:b:u,c:d:x"


def test_add_entry_keeps_existing_entries(helper):
    entries, added = helper.add_entry(["152d:0578:u"], "0bc2", "a020")
    assert added and entries == ["152d:0578:u", "0bc2:a020:u"]


def test_add_entry_is_noop_when_user_already_forces_bot(helper):
    entries, added = helper.add_entry(["0BC2:A020:U"], "0bc2", "a020")
    assert not added and entries == ["0BC2:A020:U"]


def test_add_entry_refuses_to_shadow_a_different_user_quirk(helper):
    with pytest.raises(helper.HelperError):
        helper.add_entry(["0bc2:a020:k"], "0bc2", "a020")


def test_remove_entry_removes_only_ours(helper):
    assert helper.remove_entry(["152d:0578:u", "0bc2:a020:u", "0bc2:a021:u"], "0bc2", "a020") == \
        ["152d:0578:u", "0bc2:a021:u"]
    assert helper.remove_entry(["152d:0578:u"], "0bc2", "a020") == ["152d:0578:u"]


# ---------------------------------------------------------------- device checks

def test_refuses_mismatching_vid_pid(helper, fake_sysfs, tmp_path):
    fs = fake_sysfs(vid="1234")
    with pytest.raises(helper.HelperError) as e:
        run(helper, fs, tmp_path / "run", "set-bot", FakeKernel(fs))
    assert e.value.code == helper.EXIT_INVALID_DEVICE
    assert fs.quirks() == ""


def test_refuses_unknown_bus_id(helper, fake_sysfs, tmp_path):
    fs = fake_sysfs()
    with pytest.raises(helper.HelperError):
        helper.set_bot(fs.root, str(tmp_path / "run"), "3-9", "0bc2", "a020")


def test_refuses_non_storage_device(helper, fake_sysfs, tmp_path):
    fs = fake_sysfs()
    with open(os.path.join(fs.intf, "bInterfaceClass"), "w") as f:
        f.write("03\n")
    with pytest.raises(helper.HelperError):
        run(helper, fs, tmp_path / "run", "set-bot", FakeKernel(fs))


# ---------------------------------------------------------------- set-bot / restore

def test_set_bot_then_restore_round_trip(helper, fake_sysfs, tmp_path):
    fs = fake_sysfs(quirks="152d:0578:u")
    kernel = FakeKernel(fs)
    state_dir = tmp_path / "run"

    run(helper, fs, state_dir, "set-bot", kernel)
    assert fs.quirks() == "152d:0578:u,0bc2:a020:u"
    assert fs.driver() == "usb-storage"
    assert fs.block == "sdb"
    state = json.loads((state_dir / "usbmode-2-1.2.json").read_text())
    assert state["previous_quirks"] == "152d:0578:u"
    assert state["added"] is True and state["previous_driver"] == "uas"
    assert oct((state_dir / "usbmode-2-1.2.json").stat().st_mode & 0o777) == "0o600"

    run(helper, fs, state_dir, "restore", kernel)
    assert fs.quirks() == "152d:0578:u"
    assert fs.driver() == "uas"
    assert not (state_dir / "usbmode-2-1.2.json").exists()
    assert kernel.reattaches == 2


def test_set_bot_is_idempotent(helper, fake_sysfs, tmp_path):
    fs = fake_sysfs()
    kernel = FakeKernel(fs)
    run(helper, fs, tmp_path / "run", "set-bot", kernel)
    run(helper, fs, tmp_path / "run", "set-bot", kernel)
    assert fs.quirks() == "0bc2:a020:u"
    assert kernel.reattaches == 1  # already on usb-storage: no second unplug


def test_restore_is_idempotent(helper, fake_sysfs, tmp_path):
    fs = fake_sysfs()
    kernel = FakeKernel(fs)
    run(helper, fs, tmp_path / "run", "set-bot", kernel)
    run(helper, fs, tmp_path / "run", "restore", kernel)
    assert run(helper, fs, tmp_path / "run", "restore", kernel) == "nothing to restore"
    assert fs.quirks() == "" and fs.driver() == "uas"
    assert kernel.reattaches == 2


def test_restore_without_set_bot_changes_nothing(helper, fake_sysfs, tmp_path):
    fs = fake_sysfs(quirks="0bc2:a020:u", driver="usb-storage")
    kernel = FakeKernel(fs)
    assert run(helper, fs, tmp_path / "run", "restore", kernel) == "nothing to restore"
    assert fs.quirks() == "0bc2:a020:u" and kernel.reattaches == 0


def test_user_quirk_is_never_removed(helper, fake_sysfs, tmp_path):
    """The user already forces Bulk-Only for this device: set-bot adds nothing, so
    restore must not take the user's own entry away."""
    fs = fake_sysfs(quirks="0bc2:a020:u", driver="uas")
    kernel = FakeKernel(fs)
    run(helper, fs, tmp_path / "run", "set-bot", kernel)
    run(helper, fs, tmp_path / "run", "restore", kernel)
    assert fs.quirks() == "0bc2:a020:u"


def test_set_bot_timeout_keeps_state_for_restore(helper, fake_sysfs, tmp_path):
    fs = fake_sysfs()

    class DeadKernel(FakeKernel):
        def sleep(self, seconds):  # device never comes back
            self.t += seconds

    kernel = DeadKernel(fs)
    with pytest.raises(helper.HelperError) as e:
        run(helper, fs, tmp_path / "run", "set-bot", kernel, timeout=60)
    assert e.value.code == helper.EXIT_TIMEOUT
    assert kernel.t >= 60
    assert (tmp_path / "run" / "usbmode-2-1.2.json").exists()

    run(helper, fs, tmp_path / "run", "restore", FakeKernel(fs))
    assert fs.quirks() == "" and fs.driver() == "uas"


# ---------------------------------------------------------------- never re-attach under a mount

def _add_partition_nodes(fs, disk="sdb", part="sdb1", disk_num="8:16", part_num="8:17"):
    scsi = os.path.join(fs.intf, "host0", "target0:0:0", "0:0:0:0")
    bdir = os.path.join(scsi, "block", disk)
    os.makedirs(os.path.join(bdir, part), exist_ok=True)
    with open(os.path.join(bdir, "dev"), "w") as f:
        f.write(disk_num + "\n")
    with open(os.path.join(bdir, part, "dev"), "w") as f:
        f.write(part_num + "\n")


def test_set_bot_refuses_while_a_partition_is_mounted(helper, fake_sysfs, tmp_path, _isolated_mountinfo):
    fs = fake_sysfs(block="sdb")
    _add_partition_nodes(fs)
    # btrfs shows an anonymous device number (0:45): only the source path identifies it
    _isolated_mountinfo.write_text(
        "36 25 0:45 / /run/media/u/USB_BTRFS rw,relatime shared:1 - btrfs /dev/sdb1 rw,space_cache=v2\n"
        "30 25 259:2 / /home rw - btrfs /dev/mapper/root rw\n")
    kernel = FakeKernel(fs)
    with pytest.raises(helper.HelperError) as e:
        run(helper, fs, tmp_path / "state", "set-bot", kernel)
    assert e.value.code == helper.EXIT_BUSY
    assert "/run/media/u/USB_BTRFS" in str(e.value)
    assert kernel.reattaches == 0
    assert fs.quirks().strip() == ""  # nothing was changed


def test_set_bot_refuses_when_device_number_matches(helper, fake_sysfs, tmp_path, _isolated_mountinfo):
    fs = fake_sysfs(block="sdb")
    _add_partition_nodes(fs)
    _isolated_mountinfo.write_text("40 25 8:17 / /mnt/data rw - ext4 /dev/disk/by-uuid/x rw\n")
    with pytest.raises(helper.HelperError) as e:
        run(helper, fs, tmp_path / "state", "set-bot", FakeKernel(fs))
    assert e.value.code == helper.EXIT_BUSY


def test_set_bot_ignores_unrelated_mounts(helper, fake_sysfs, tmp_path, _isolated_mountinfo):
    fs = fake_sysfs(block="sdb")
    _add_partition_nodes(fs)
    _isolated_mountinfo.write_text(
        "30 25 259:2 / /home rw - btrfs /dev/mapper/root rw\n"
        "31 25 259:1 / /boot rw - vfat /dev/nvme0n1p1 rw\n"
        "32 25 8:1 / /mnt/other rw - ext4 /dev/sda1 rw\n")
    assert "bulk-only" in run(helper, fs, tmp_path / "state", "set-bot", FakeKernel(fs))


def test_restore_refuses_while_a_volume_was_remounted(helper, fake_sysfs, tmp_path, _isolated_mountinfo):
    fs = fake_sysfs(block="sdb")
    kernel = FakeKernel(fs)
    run(helper, fs, tmp_path / "state", "set-bot", kernel)
    node = fs.block
    _add_partition_nodes(fs, disk=node, part=node + "1", disk_num="8:32", part_num="8:33")
    # the desktop auto-mounted the volume again after the switch
    _isolated_mountinfo.write_text(f"36 25 0:45 / /run/media/u/USB rw - btrfs /dev/{node}1 rw\n")
    reattaches = kernel.reattaches
    with pytest.raises(helper.HelperError) as e:
        run(helper, fs, tmp_path / "state", "restore", kernel)
    assert e.value.code == helper.EXIT_BUSY
    assert kernel.reattaches == reattaches
    assert os.path.exists(helper.state_file(str(tmp_path / "state"), fs.bus_id))  # state kept for a retry
    # once unmounted, restore works
    _isolated_mountinfo.write_text("")
    assert "restored" in run(helper, fs, tmp_path / "state", "restore", kernel)



# ---------------------------------------------------------------- empty list must really be written

def test_write_quirks_always_issues_a_write_even_for_an_empty_list(helper, fake_sysfs, monkeypatch):
    """Regression: write("") does nothing on a sysfs attribute, so the quirk stayed forced."""
    fs = fake_sysfs()
    helper.write_quirks(fs.root, ["0bc2:a020:u"])
    raw_writes = []
    real_open = open

    class Spy:
        def __init__(self, f):
            self.f = f

        def write(self, data):
            raw_writes.append(data)
            return self.f.write(data)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.f.close()

        def __getattr__(self, name):
            return getattr(self.f, name)

    def spying_open(path, mode="r", *a, **k):
        f = real_open(path, mode, *a, **k)
        return Spy(f) if "w" in mode and str(path).endswith("parameters/quirks") else f

    monkeypatch.setattr("builtins.open", spying_open)
    helper.write_quirks(fs.root, [])
    assert raw_writes and all(len(w) > 0 for w in raw_writes)
    assert helper.read_quirks(fs.root) == []


def test_write_quirks_fails_loudly_when_the_kernel_ignores_the_change(helper, fake_sysfs, monkeypatch):
    fs = fake_sysfs()
    helper.write_quirks(fs.root, ["0bc2:a020:u"])
    real_parse = helper.parse_quirks
    # simulate a parameter that keeps its old value
    monkeypatch.setattr(helper, "parse_quirks", lambda text: ["0bc2:a020:u"])
    with pytest.raises(helper.HelperError):
        helper.write_quirks(fs.root, [])
    monkeypatch.setattr(helper, "parse_quirks", real_parse)
