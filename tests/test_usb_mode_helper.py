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
