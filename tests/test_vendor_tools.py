"""Vendor tool (perccli/storcli) mode, replayed from the real perccli64 007.1910 captures in
tests/fixtures/dell_r630_perccli (no hardware): read-only whitelist, detection, parsing,
health rules (incl. SYNTHETIC variants derived from the real fixture) and disk mapping."""
import copy
import json
import os

import pytest

from smartlinux.core import vendor_tools as vt
from smartlinux.core.models import HealthStatus, ServerConfig
from test_megaraid import FakeR630

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "dell_r630_perccli")

REPLIES = {
    ("-v",): "version.txt",
    ("show", "ctrlcount", "J"): "show_ctrlcount.json",
    ("/c0", "show", "J"): "c0_show.json",
    ("/c0", "show", "all", "J"): "c0_show_all.json",
    ("/c0/vall", "show", "all", "J"): "c0_vall_show_all.json",
    ("/c0/eall/sall", "show", "all", "J"): "c0_eall_sall_show_all.json",
    ("/c0/bbu", "show", "all", "J"): "c0_bbu_show_all.json",
    ("/c0/dall", "show", "J"): "c0_dall_show.json",
}


def text(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


class FakeHost(vt.ToolHost):
    """Replays the captures; records every argv that would have been executed."""

    def __init__(self, binary="perccli64", privilege="root", overrides=None):
        self.binary = binary
        self.privilege = privilege
        self.overrides = overrides or {}
        self.executed = []

    def find(self):
        return self.binary

    def _execute(self, argv):
        self.executed.append(argv)
        key = tuple(argv[1:])
        if key in self.overrides:
            return 0, self.overrides[key], ""
        return 0, text(REPLIES[key]), ""


def read_fixture_controller(overrides=None):
    host = FakeHost(overrides=overrides)
    return vt.read_controller(host, vt.detect(host), 0)


class Ctrl:
    def __init__(self, pci_slot="02:00.0", driver="megaraid_sas"):
        self.pci_slot, self.driver = pci_slot, driver


# ---------------------------------------------------------------- whitelist (rule b, c)

ALLOWED = [
    ["-v"], ["show", "ctrlcount", "J"], ["/c0", "show", "J"], ["/c0", "show", "all", "J"],
    ["/c0/vall", "show", "all", "J"], ["/c0/eall/sall", "show", "all", "J"],
    ["/c0/bbu", "show", "all", "J"], ["/c0/dall", "show", "J"], ["/c12", "show", "J"],
    ["/c99/bbu", "show", "all", "J"],
]

REJECTED = [
    ["/c0", "set", "alarm=off"], ["/c0/e32/s4", "set", "good"], ["/c0/e32/s2", "start", "rebuild"],
    ["/c0/v0", "start", "cc"], ["/c0/v0", "stop", "cc"], ["/c0", "add", "vd", "r0", "drives=32:4"],
    ["/c0/v1", "del"], ["/c0/v1", "delete", "force"], ["/c0/fall", "import"], ["/c0/fall", "del"],
    ["/c0", "clear", "events"], ["/c0", "flash", "file=fw.rom"], ["/c0/e32/s2", "secure", "erase"],
    ["/c0/fall", "show", "foreign"], ["/c0", "show", "patrolread"], ["/c0", "start", "patrolread"],
    ["/c0/cv", "show", "all", "J"],                      # read-only but not whitelisted
    ["/c0", "show"], ["/c0/vall", "show", "J"], ["/c0", "show", "J", "extra"],
    ["/c100", "show", "J"], ["/c-1", "show", "J"], ["/cx", "show", "J"], ["/c٣", "show", "J"],
    ["/c0;reboot", "show", "J"], ["/c0", "show", "J;", "rm", "-rf", "/"], ["/c0", "show", "J", "&&", "id"],
    ["/c0", "show", "J|sh"], ["$(id)"], ["`id`"], ["/c0", "show", "J\n"], ["/c0 show J"],
    ["/c0", "show", "J>/tmp/x"], ["/c0", "show", "'J'"], ["/c0", "show", "$HOME"], [],
]


@pytest.mark.parametrize("args", ALLOWED, ids=lambda a: " ".join(a))
def test_whitelist_allows_only_read_templates(args):
    host = FakeHost(overrides={tuple(args): "{}"})
    host.run("perccli64", args)
    assert host.executed == [["perccli64", *args]]


@pytest.mark.parametrize("args", REJECTED, ids=lambda a: repr(" ".join(a)))
def test_whitelist_rejects_before_running(args):
    host = FakeHost()
    with pytest.raises(vt.CommandNotAllowed):
        host.run("perccli64", args)
    assert host.executed == []


@pytest.mark.parametrize("bad", ["/c0 show J", ("/c0", "show", 1), None, 5])
def test_whitelist_rejects_non_token_lists(bad):
    with pytest.raises(vt.CommandNotAllowed):
        vt.validate_args(bad)


@pytest.mark.parametrize("binary", ["/tmp/perccli64", "perccli64; id", "sh", "./storcli64", "", "PERCCLI64"])
def test_executable_only_from_fixed_candidates(binary):
    host = FakeHost()
    with pytest.raises(vt.CommandNotAllowed):
        host.run(binary, ["-v"])
    assert host.executed == []


@pytest.mark.parametrize("controller", ["0; reboot", "100", "-1", "1 ", "", True, "٣", "0x1"])
def test_controller_number_is_validated(controller):
    with pytest.raises(vt.CommandNotAllowed):
        vt.controller_args(controller, "show")


def test_controller_args_cover_every_read():
    for kind in ("show", "show_all", "volumes", "drives", "battery", "groups"):
        assert vt.controller_args(7, kind)[0].startswith("/c7")
    with pytest.raises(vt.CommandNotAllowed):
        vt.controller_args(0, "set")


def test_candidates_are_the_fixed_list():
    assert vt.CANDIDATES == ("perccli64", "perccli", "storcli64", "storcli",
                             "/opt/MegaRAID/perccli/perccli64", "/opt/MegaRAID/storcli/storcli64")


def test_module_never_installs_anything():
    with open(vt.__file__, encoding="utf-8") as f:
        source = f.read()
    code = source.split("MISSING_TOOL_HELP")[0]  # the help text tells the USER how to install
    import re
    for word in ("dpkg", "rpm", "apt", "apt-get", "yum", "dnf", "pacman", "wget", "curl", "install"):
        assert not re.search(rf"\b{word}\b", code), word
    assert "shell=True" not in code


# ---------------------------------------------------------------- detection (rule a)

def test_detect_installed_tool_and_version():
    status = vt.detect(FakeHost())
    assert status.installed and status.usable
    assert status.found_text == "perccli64 007.1910 found"


def test_detect_missing_tool_runs_nothing():
    host = FakeHost(binary=None)
    status = vt.detect(host)
    assert not status.installed and not status.usable and host.executed == []
    assert status.found_text == "not installed"


def test_detect_without_root_is_not_usable():
    status = vt.detect(FakeHost(privilege="none"))
    assert status.installed and not status.usable
    host = FakeHost(privilege="none")
    states = vt.read_host(host, [Ctrl()], {"local|02:00.0": vt.MODE_VENDOR}, "")
    assert states["02:00.0"].report is None
    assert host.executed == [["perccli64", "-v"]]  # nothing else without root


class FakeClient:
    def __init__(self, username="root", password=None, found="perccli64"):
        self.config = ServerConfig(id="r630", name="pve", host="10.0.0.5", username=username, password=password)
        self.found = found
        self.raw, self.sudo = [], []

    def _raw_exec(self, cmd, timeout=15):
        self.raw.append(cmd)
        if cmd.startswith("for p in"):
            return 0, f"@@found {self.found}\n@@found /tmp/evil\n" if self.found else "@@found /tmp/evil\n", ""
        return 0, text("version.txt"), ""

    def _exec_with_sudo(self, cmd, timeout=15):
        self.sudo.append(cmd)
        return 0, text("version.txt"), ""


def test_remote_detection_uses_only_candidates():
    client = FakeClient(found="/opt/MegaRAID/perccli/perccli64")
    host = vt.RemoteHost(client)
    assert host.find() == "/opt/MegaRAID/perccli/perccli64"
    assert all(c in client.raw[0] for c in vt.CANDIDATES)
    assert vt.RemoteHost(FakeClient(found=None)).find() is None  # "/tmp/evil" is never used


@pytest.mark.parametrize("username,password,privilege,channel", [
    ("root", None, "root", "raw"), ("admin", "pw", "sudo", "sudo"), ("admin", None, "none", "raw"),
])
def test_remote_privilege(username, password, privilege, channel):
    client = FakeClient(username=username, password=password)
    host = vt.RemoteHost(client)
    assert host.privilege == privilege
    status = vt.detect(host)
    assert status.usable is (privilege != "none")
    assert getattr(client, channel)[-1] == "perccli64 -v"


# ---------------------------------------------------------------- parsing the real captures

def test_fixture_controller_identity():
    c = read_fixture_controller()
    assert (c.product, c.fw_version, c.fw_package) == ("PERC H730 Mini", "4.260.00-7126", "25.4.1.0004")
    assert c.bios_version == "6.29.00.0_4.16.07.00_0x06120100"
    assert (c.driver_name, c.driver_version, c.personality) == ("megaraid_sas", "07.734.00.00-rc1", "RAID-Mode")
    assert c.status == "Optimal" and c.pci_slot == "02:00.0"
    assert c.level == vt.OK and c.health == HealthStatus.HEALTHY and c.errors == []


def test_fixture_controller_count():
    host = FakeHost()
    assert vt.controller_count(host, vt.detect(host)) == 1


def test_fixture_volumes():
    c = read_fixture_controller()
    assert [(v.dg_vd, v.name, v.raid_type, v.state, v.os_drive) for v in c.volumes] == [
        ("0/0", "SO", "RAID1", "Optl", "/dev/sda"), ("1/1", "VMDATA1", "RAID0", "Optl", "/dev/sdb"),
        ("2/2", "VMDATA2", "RAID0", "Optl", "/dev/sdc"), ("3/3", "VMDATA3", "RAID0", "Optl", "/dev/sdd")]
    assert [[m["DID"] for m in v.members] for v in c.volumes] == [[0, 1], [2], [3], [5]]
    assert c.volumes[0].cache == "RWBD" and c.volumes[0].size == "1.090 TB"
    assert all(v.active_operations is None for v in c.volumes)
    assert c.volumes[0].notes == [] and c.volumes[1].notes == ["no redundancy (RAID0 of a single disk)"]
    assert c.volumes[1].summary() == "VMDATA1 · RAID0 · 1 disk · no redundancy"
    assert c.volumes[0].summary() == "SO · RAID1 · 2 disks"


def test_fixture_drives():
    c = read_fixture_controller()
    assert [(d.eid_slot, d.did, d.state, d.smartctl_type) for d in c.drives] == [
        ("32:0", 0, "Onln", "megaraid,0"), ("32:1", 1, "Onln", "megaraid,1"), ("32:2", 2, "Onln", "megaraid,2"),
        ("32:3", 3, "Onln", "megaraid,3"), ("32:5", 5, "Onln", "megaraid,5")]
    d = c.drives[2]
    assert (d.media_errors, d.other_errors, d.predictive_failures, d.smart_alert) == (0, 0, 0, False)
    assert d.temperature == "29 °C" and d.link_speed == "12.0Gb/s" and d.volume.name == "VMDATA1"


def test_fixture_battery_and_no_cachevault():
    c = read_fixture_controller()
    b = c.battery
    assert b.present and b.kind == "BBU" and b.state == "Optimal" and b.charge == "98%"
    assert (b.replacement_required, b.pack_missing, b.temperature) == (False, False, "47 °C")
    cv = vt.parse_reply(text("c0_cv_show_all.json"))
    assert not cv.ok and cv.missing and "Cachevault doesn't exist" in cv.message
    absent = vt.parse_battery(cv)
    assert not absent.present and absent.level == vt.OK  # "doesn't exist" = component not there


# ---------------------------------------------------------------- health rules (SYNTHETIC variants)

def _synthetic(name, mutate):
    """A SYNTHETIC variant of a real capture: same JSON with one field changed."""
    data = json.loads(text(REPLIES[name]))
    mutate(data["Controllers"][0]["Response Data"])
    return {name: json.dumps(data)}


def synthetic_vd_state(state):
    def mutate(rd):
        rd["/c0/v0"][0]["State"] = state
    return _synthetic(("/c0/vall", "show", "all", "J"), mutate)


def synthetic_drive_state(field, value, slot=2):
    def mutate(rd):
        detail = rd[f"Drive /c0/e32/s{slot} - Detailed Information"][f"Drive /c0/e32/s{slot} State"]
        if field == "State":
            rd[f"Drive /c0/e32/s{slot}"][0]["State"] = value
        else:
            detail[field] = value
    return _synthetic(("/c0/eall/sall", "show", "all", "J"), mutate)


def synthetic_battery(prop, value, section="BBU_Info"):
    def mutate(rd):
        for row in rd[section]:
            if row["Property"] == prop:
                row["Value"] = value
    return _synthetic(("/c0/bbu", "show", "all", "J"), mutate)


@pytest.mark.parametrize("state,level,reason", [
    ("Dgrd", vt.WARNING, "SO: degraded, redundancy lost"),
    ("Pdgd", vt.WARNING, "SO: partially degraded, redundancy lost"),
    ("OfLn", vt.FAILED, "SO: offline"),
])
def test_synthetic_volume_states(state, level, reason):
    c = read_fixture_controller(synthetic_vd_state(state))
    assert c.volumes[0].level == level and c.level == level
    assert reason in c.reasons


@pytest.mark.parametrize("field,value,level", [
    ("State", "Failed", vt.FAILED), ("State", "Offln", vt.FAILED), ("State", "UBad", vt.FAILED),
    ("Predictive Failure Count", 1, vt.WARNING), ("S.M.A.R.T alert flagged by drive", "Yes", vt.WARNING),
    ("Media Error Count", 3, vt.ATTENTION), ("Other Error Count", 42, vt.OK),
])
def test_synthetic_drive_states(field, value, level):
    c = read_fixture_controller(synthetic_drive_state(field, value))
    assert c.drives[2].level == level and c.level == level
    if level == vt.ATTENTION:
        assert vt.LEVEL_TEXT[c.drives[2].level] == "ATTENTION"


def test_synthetic_battery_failed_and_replacement():
    c = read_fixture_controller(synthetic_battery("Battery State", "Failed"))
    assert c.battery.level == vt.WARNING and "battery state Failed" in c.reasons
    c = read_fixture_controller(synthetic_battery("Replacement required", "Yes", "BBU_Firmware_Status"))
    assert c.battery.level == vt.WARNING and "battery replacement required" in c.reasons


def test_synthetic_controller_takes_the_worst_part():
    overrides = {}
    overrides.update(synthetic_vd_state("Dgrd"))
    overrides.update(synthetic_drive_state("State", "Failed", slot=1))
    c = read_fixture_controller(overrides)
    assert c.level == vt.FAILED and c.health == HealthStatus.FAILED
    assert c.summary.startswith("FAILED: ")


def test_synthetic_active_operation_is_shown():
    def mutate(rd):
        rd["VD0 Properties"]["Active Operations"] = "Rebuild 45% (Elapsed 01:12:00)"
    c = read_fixture_controller(_synthetic(("/c0/vall", "show", "all", "J"), mutate))
    assert c.volumes[0].active_operations == "Rebuild 45% (Elapsed 01:12:00)"


# ---------------------------------------------------------------- read_host: modes, mapping

def test_standard_mode_only_detects():
    host = FakeHost()
    states = vt.read_host(host, [Ctrl()], {}, "")
    assert states["02:00.0"].mode == vt.MODE_STANDARD and states["02:00.0"].report is None
    assert host.executed == [["perccli64", "-v"]]


def test_vendor_mode_reads_the_controller():
    host = FakeHost()
    states = vt.read_host(host, [Ctrl()], {"local|02:00.0": vt.MODE_VENDOR}, "")
    assert states["02:00.0"].report.product == "PERC H730 Mini"
    assert all(vt.validate_args(argv[1:]) for argv in host.executed)


def test_only_megaraid_controllers_are_offered():
    host = FakeHost()
    assert vt.read_host(host, [Ctrl("00:1f.2", "ahci")], {"local|00:1f.2": vt.MODE_VENDOR}, "") == {}
    assert host.executed == []


def test_modes_are_per_server_and_controller():
    modes = {"r630|02:00.0": vt.MODE_VENDOR}
    host = FakeHost()
    assert vt.read_host(host, [Ctrl()], modes, "")["02:00.0"].report is None     # other host
    assert vt.read_host(FakeHost(), [Ctrl()], modes, "r630")["02:00.0"].report is not None
    assert vt.load_modes(lambda k, d: {"a|b": "vendor", "c|d": "rm -rf"}) == {"a|b": "vendor"}


def test_two_controllers_are_matched_by_pci():
    second = copy.deepcopy(json.loads(text("c0_show.json")))
    second["Controllers"][0]["Response Data"]["Bus Number"] = 3
    count = json.loads(text("show_ctrlcount.json"))
    count["Controllers"][0]["Response Data"]["Controller Count"] = 2
    host = FakeHost(overrides={("show", "ctrlcount", "J"): json.dumps(count),
                               ("/c1", "show", "J"): json.dumps(second)})
    mapping = vt.map_controllers(host, vt.detect(host), [Ctrl("02:00.0"), Ctrl("03:00.0")])
    assert mapping == {"02:00.0": 0, "03:00.0": 1}


# ---------------------------------------------------------------- disk mapping (VD -> sdX, DID -> megaraid,N)

def r630_states(mode=vt.MODE_VENDOR):
    return vt.read_host(FakeHost(), [Ctrl()], {"r630|02:00.0": mode}, "r630")


def test_annotations_on_virtual_and_physical_disks():
    disks = FakeR630().discover_remote_drives()
    vt.annotate_disks(r630_states(), disks)
    by_uid = {d.uid: d for d in disks}
    assert by_uid["/dev/sdb"].raid_volume == "VMDATA1 · RAID0 · 1 disk · no redundancy"
    assert by_uid["/dev/sda"].raid_volume == "SO · RAID1 · 2 disks"
    assert "32:0 (DID 0, Online)" in by_uid["/dev/sda"].raid_detail
    assert by_uid["/dev/bus/0#megaraid,2"].raid_volume == "VMDATA1"
    assert by_uid["/dev/bus/0#megaraid,0"].raid_volume == "SO"
    assert by_uid["/dev/bus/0#megaraid,5"].raid_health == HealthStatus.HEALTHY


def test_standard_mode_leaves_disks_untouched():
    disks = FakeR630().discover_remote_drives()
    vt.annotate_disks(r630_states(vt.MODE_STANDARD), disks)
    assert all(d.raid_volume is None and d.raid_detail is None for d in disks)
