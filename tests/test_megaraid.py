"""Dell PowerEdge R630 + PERC H730 Mini (LSI MegaRAID), replayed from the real captures in
tests/fixtures/dell_r630_perc_h730 (no hardware): discovery, -d megaraid,N reads, virtual
drives, SAS health rules and server / controller identity."""
import json
import os
import subprocess

import pytest

from smartlinux.core import detector, raid, system_info
from smartlinux.core.models import DiskInfo, HealthStatus, ServerConfig
from smartlinux.core.remote_common import BaseRemoteClient
from smartlinux.core.smart_parser import SmartParser

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "dell_r630_perc_h730")
PHYSICAL_SLOTS = [0, 1, 2, 3, 5]


def text(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


def js(name):
    return json.loads(text(name))


def megaraid_json(slot):
    name = "smartctl_j_a_megaraid_4_empty_slot.json" if slot == 4 else f"smartctl_j_a_megaraid_{slot}.json"
    return text(name)


SYSFS_REPLY = "".join(f"@@{n}|DELL    |PERC H730 Mini  |2341994496\n" for n in ("sda", "sdb", "sdc", "sdd"))


# ---------------------------------------------------------------- scan parsing

def test_scan_keeps_device_type_and_has_no_duplicates():
    entries = raid.scan_entries(js("smartctl_scan_j.json"))
    assert len(entries) == 9
    assert [e["type"] for e in entries if raid.is_passthrough_type(e["type"])] == \
        [f"megaraid,{n}" for n in PHYSICAL_SLOTS]
    doubled = js("smartctl_scan_j.json")
    doubled["devices"] += doubled["devices"]
    assert len(raid.scan_entries(doubled)) == 9


def test_scan_entries_become_unique_disks():
    make = lambda p, **k: DiskInfo(device_path=p, name=p.split("/")[-1], **k)  # noqa: E731
    disks = [raid.disk_from_scan_entry(e, make) for e in raid.scan_entries(js("smartctl_scan_j.json"))]
    physical = [d for d in disks if raid.is_passthrough_type(d.smart_dev_type)]
    assert {d.device_path for d in physical} == {"/dev/bus/0"}
    assert [d.slot for d in physical] == PHYSICAL_SLOTS
    assert [d.name for d in physical] == [f"slot {n}" for n in PHYSICAL_SLOTS]
    assert len({d.uid for d in disks}) == 9
    assert len({d.host_key for d in disks}) == 9
    plain = [d for d in disks if d not in physical]
    assert [d.uid for d in plain] == ["/dev/sda", "/dev/sdb", "/dev/sdc", "/dev/sdd"]  # unchanged for normal disks


def test_device_type_helpers():
    assert raid.read_types_for("megaraid,3") == ["megaraid,3", "sat+megaraid,3"]
    assert raid.read_types_for("sat+megaraid,3") == ["sat+megaraid,3"]
    assert raid.slot_from_type("megaraid,12") == 12
    assert not raid.is_passthrough_type("scsi") and not raid.is_passthrough_type(None)
    assert not raid.is_passthrough_type("sat,12")


# ---------------------------------------------------------------- virtual drives

@pytest.mark.parametrize("model,vendor,expected", [
    ("PERC H730 Mini", "DELL", True),
    ("PERC H330 Adp", "DELL", True),
    ("LOGICAL VOLUME", "HP", True),
    ("MR9361-8i", "AVAGO", True),
    ("MegaRAID 9271", "LSI", True),
    ("ST1200MM0088", "SEAGATE", False),
    ("Samsung SSD 860 EVO 500GB", "ATA", False),
    ("WDC WD40EFRX-68N32N0", "ATA", False),
    ("", "DELL", False),
])
def test_virtual_drive_classification(model, vendor, expected):
    assert raid.is_virtual_raid_model(model, vendor) is expected


def test_virtual_drive_has_no_invented_health():
    disk = raid.mark_virtual(DiskInfo(device_path="/dev/sda", name="sda", model="PERC H730 Mini", size_human="1.1T"))
    assert disk.is_virtual and disk.health_status == HealthStatus.UNKNOWN
    assert disk.health_summary == "Virtual drive (RAID volume)" and disk.attributes == []
    assert disk.size_human == "1.1T"


# ---------------------------------------------------------------- local discovery + read

class FakeRun:
    def __init__(self, lsblk=None, scan=None, reads=None):
        self.lsblk = lsblk
        self.scan = scan
        self.reads = reads or {}
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        if cmd[0] == "lsblk":
            return subprocess.CompletedProcess(cmd, 0 if self.lsblk else 1, self.lsblk or "", "")
        if "--scan" in cmd:
            return subprocess.CompletedProcess(cmd, 0, self.scan or "", "")
        dev_type = cmd[cmd.index("-d") + 1] if "-d" in cmd else None
        out = self.reads.get(dev_type, "")
        return subprocess.CompletedProcess(cmd, 0 if out else 2, out, "")


def test_local_discovery_finds_5_physical_and_4_virtual(monkeypatch):
    run = FakeRun(lsblk=text("lsblk_virtual_drives.json"), scan=text("smartctl_scan_j.json"))
    monkeypatch.setattr(detector.subprocess, "run", run)
    monkeypatch.setattr(detector.platform, "system", lambda: "Linux")
    drives = detector.LocalDiskDetector.discover_drives()

    virtual = [d for d in drives if d.is_virtual]
    physical = [d for d in drives if raid.is_passthrough_type(d.smart_dev_type)]
    assert [d.device_path for d in virtual] == ["/dev/sda", "/dev/sdb", "/dev/sdc", "/dev/sdd"]
    assert [d.smart_dev_type for d in physical] == [f"megaraid,{n}" for n in PHYSICAL_SLOTS]
    assert len(drives) == 9 and len({d.uid for d in drives}) == 9
    assert {d.controller for d in drives} == {"PERC H730 Mini (MegaRAID)"}
    assert all(d.size_human == "1.1T" for d in virtual)
    # listing never reads SMART
    assert not any("-a" in c for c in run.calls)


def test_local_non_raid_machine_is_unchanged(monkeypatch):
    lsblk = json.dumps({"blockdevices": [{"name": "sda", "path": "/dev/sda", "model": "Samsung SSD 860",
                                          "size": "500G", "tran": "sata", "type": "disk"}]})
    scan = json.dumps({"devices": [{"name": "/dev/sda", "type": "sat", "protocol": "ATA"}]})
    monkeypatch.setattr(detector.subprocess, "run", FakeRun(lsblk=lsblk, scan=scan))
    monkeypatch.setattr(detector.platform, "system", lambda: "Linux")
    drives = detector.LocalDiskDetector.discover_drives()
    assert [(d.device_path, d.uid, d.is_virtual, d.controller) for d in drives] == \
        [("/dev/sda", "/dev/sda", False, None)]


def test_local_read_uses_megaraid_type(monkeypatch):
    run = FakeRun(reads={"megaraid,2": megaraid_json(2)})
    monkeypatch.setattr(detector.subprocess, "run", run)
    disk = detector.LocalDiskDetector.read_drive_smart("/dev/bus/0", dev_type="megaraid,2")
    assert run.calls[0][-4:] == ["-d", "megaraid,2", "-a", "/dev/bus/0"]
    assert disk.uid == "/dev/bus/0#megaraid,2" and disk.name == "slot 2"
    assert disk.health_status == HealthStatus.WARNING
    assert disk.health_summary == "Warning: 9 grown defects"


def test_local_read_falls_back_to_sat_megaraid(monkeypatch):
    run = FakeRun(reads={"megaraid,1": megaraid_json(4), "sat+megaraid,1": megaraid_json(1)})
    monkeypatch.setattr(detector.subprocess, "run", run)
    disk = detector.LocalDiskDetector.read_drive_smart("/dev/bus/0", dev_type="megaraid,1")
    tried = [c[c.index("-d") + 1] for c in run.calls]
    assert tried == ["megaraid,1", "sat+megaraid,1"]
    assert disk.model == "SEAGATE ST1200MM0088" and disk.smart_dev_type == "megaraid,1"


# ---------------------------------------------------------------- remote discovery + read

class FakeR630(BaseRemoteClient):
    """Replays the R630 captures for the commands SmartLinux sends over SSH."""

    def __init__(self, broken_megaraid_types=()):
        super().__init__(ServerConfig(id="r630", name="pve-r630", host="10.0.0.5"))
        self.commands = []
        self.broken = set(broken_megaraid_types)

    def connect(self, timeout=8):
        return True, "ok"

    def disconnect(self):
        pass

    def is_connected(self):
        return True

    def _exec_with_sudo(self, cmd, timeout=15):
        return 1, "", "not used"

    def _raw_exec(self, cmd, timeout=15):
        self.commands.append(cmd)
        if cmd.startswith("smartctl --scan -j"):
            return 0, text("smartctl_scan_j.json"), ""
        if cmd.startswith("for d in"):
            return 0, SYSFS_REPLY, ""
        if cmd.startswith("smartctl -j"):
            args = cmd.split()
            dev_type = args[args.index("-d") + 1] if "-d" in args else None
            if dev_type and dev_type.startswith("sat+megaraid,"):
                slot = int(dev_type.split(",")[1])
                return 0, megaraid_json(slot), ""
            if dev_type and dev_type.startswith("megaraid,") and dev_type not in self.broken:
                return 0, megaraid_json(int(dev_type.split(",")[1])), ""
            if dev_type in self.broken:
                return 2, megaraid_json(4), ""
            return 2, text("smartctl_j_a_virtual_sda.json"), ""
        return 127, "", "not found"


def test_remote_discovery_matches_local():
    client = FakeR630()
    drives = client.discover_remote_drives()
    assert len(drives) == 9 and len({d.uid for d in drives}) == 9
    assert sum(d.is_virtual for d in drives) == 4
    assert all(d.is_remote and d.server_id == "r630" for d in drives)
    virtual = [d for d in drives if d.is_virtual]
    assert all(d.model == "PERC H730 Mini" and d.size_human == "1.2 TB" for d in virtual)
    assert {d.controller for d in drives} == {"PERC H730 Mini (MegaRAID)"}
    assert not any(" -a " in c for c in client.commands)


@pytest.mark.parametrize("slot", PHYSICAL_SLOTS)
def test_remote_read_each_physical_disk(slot):
    client = FakeR630()
    disk = client.read_remote_drive_smart("/dev/bus/0", dev_type=f"megaraid,{slot}")
    assert client.commands[0] == f"smartctl -j -d megaraid,{slot} -a /dev/bus/0"
    assert disk.model == "SEAGATE ST1200MM0088" and disk.is_remote
    assert disk.uid == f"/dev/bus/0#megaraid,{slot}"
    expected = HealthStatus.WARNING if slot in (2, 5) else HealthStatus.HEALTHY
    assert disk.health_status == expected


def test_remote_read_falls_back_to_sat_megaraid_and_self_test_uses_it():
    client = FakeR630(broken_megaraid_types={"megaraid,3"})
    disk = client.read_remote_drive_smart("/dev/bus/0", dev_type="megaraid,3")
    assert client.commands == ["smartctl -j -d megaraid,3 -a /dev/bus/0",
                               "smartctl -j -d sat+megaraid,3 -a /dev/bus/0"]
    assert disk.health_status == HealthStatus.HEALTHY and disk.smart_dev_type == "megaraid,3"
    client.run_self_test("/dev/bus/0", "short", dev_type="megaraid,3")
    assert client.commands[-1] == "smartctl -d sat+megaraid,3 -t short /dev/bus/0"


# ---------------------------------------------------------------- SAS data and health

@pytest.mark.parametrize("slot,status,summary", [
    (0, HealthStatus.HEALTHY, None),
    (1, HealthStatus.HEALTHY, None),
    (2, HealthStatus.WARNING, "Warning: 9 grown defects"),
    (3, HealthStatus.HEALTHY, None),
    (5, HealthStatus.WARNING, "Warning: 2 grown defects"),
])
def test_sas_health_rules_on_fixtures(slot, status, summary):
    disk = SmartParser.parse_smart_json(json.loads(megaraid_json(slot)), "/dev/bus/0")
    assert disk.health_status == status
    if summary:
        assert disk.health_summary == summary


def test_sas_identity_and_rows():
    disk = SmartParser.parse_smart_json(json.loads(megaraid_json(2)), "/dev/bus/0")
    assert (disk.vendor, disk.model, disk.serial, disk.firmware) == \
        ("SEAGATE", "SEAGATE ST1200MM0088", "S402XFAKE03", "TT31")
    assert disk.protocol == "SAS" and disk.transport == "SAS (SPL-4)"
    assert disk.rotation_rate == "10000 RPM (HDD)" and disk.form_factor == "2.5 inches"
    assert disk.temperature_c == 29 and disk.power_on_hours == 25784 and disk.power_cycles == 145
    assert disk.size_human == "1.2 TB"
    assert disk.self_test_status == "Background short: Completed (10 days ago)"
    rows = {a.name: a for a in disk.attributes}
    assert rows["SMART Health Status"].current == "OK"
    assert rows["Grown Defect List"].current == "9" and rows["Grown Defect List"].status == "WARNING"
    assert rows["Read Errors Corrected"].status == "OK"  # ECC at work, not a problem
    for op in ("Read", "Write", "Verify"):
        assert rows[f"{op} Errors Uncorrected"].current == "0"
    assert rows["Start/Stop Cycles"].current == "145"
    assert rows["Current Temperature"].threshold == "60 °C"
    assert all(a.description for a in disk.attributes)


def _sas(**changes):
    data = json.loads(megaraid_json(0))
    data.update(changes)
    return SmartParser.parse_smart_json(data, "/dev/bus/0")


def test_sas_failed_health_status_is_failed():
    disk = _sas(smart_status={"passed": False, "scsi": {"ie_string": "FIRMWARE IMPENDING FAILURE"}})
    assert disk.health_status == HealthStatus.FAILED
    assert "FIRMWARE IMPENDING FAILURE" in disk.health_summary


def test_sas_uncorrected_errors_warn():
    data = json.loads(megaraid_json(0))
    data["scsi_error_counter_log"]["read"]["total_uncorrected_errors"] = 3
    disk = SmartParser.parse_smart_json(data, "/dev/bus/0")
    assert disk.health_status == HealthStatus.WARNING
    assert disk.health_summary == "Warning: 3 uncorrected read errors"


def test_sas_non_medium_errors_alone_are_not_a_problem():
    disk = _sas(scsi_nonmedium_error={"count": 4711})
    assert disk.health_status == HealthStatus.HEALTHY
    row = next(a for a in disk.attributes if a.name == "Non-Medium Error Count")
    assert row.current == "4,711" and row.status == "OK"


def test_empty_slot_reply_is_not_usable():
    from smartlinux.core.smart_parser import reading_quality
    assert reading_quality(json.loads(megaraid_json(4))) == 0


# ---------------------------------------------------------------- server / controller info

def fixture_sections():
    summary = text("host_summary.txt")
    return {
        "dmi": text("dmi_id.txt"), "dmidecode": text("dmidecode_system_bios.txt"),
        "lscpu": summary, "meminfo": summary, "os": summary, "pve": summary, "uptime": summary,
        "kernel": "7.0.14-20-pve", "hostname": "pve-r630",
        "lspci": text("lspci_storage_controllers.txt"),
        "scsi_host": text("scsi_hosts.txt"), "modinfo": text("scsi_hosts.txt"),
    }


def test_system_info_identity_from_fixture():
    info = system_info.build_system_info(fixture_sections())
    assert info.model_line == "Dell PowerEdge R630"
    assert info.cpu_line == "2 × Intel Xeon E5-2660 v3"
    assert (info.cpu_sockets, info.cpu_cores_per_socket, info.cpu_threads) == (2, 10, 40)
    assert info.ram_line == "503 GiB RAM"
    assert info.os_name == "Debian GNU/Linux 13 (trixie)" and info.proxmox_version == "9.2.21"
    assert info.service_tag == "XXXXXXX" and info.bios_version == "2.2.5" and info.board == "02C2CP"
    assert info.identity_line() == ("Dell PowerEdge R630 · 2 × Intel Xeon E5-2660 v3 · 503 GiB RAM · "
                                    "Proxmox VE 9.2.21 · kernel 7.0.14-20-pve · "
                                    "up 1 week, 3 days, 2 hours, 20 minutes")


def test_controllers_from_fixture():
    info = system_info.build_system_info(fixture_sections())
    assert [(c.kind, c.pci_slot) for c in info.controllers] == \
        [("SATA", "00:11.4"), ("SATA", "00:1f.2"), ("RAID", "02:00.0")]
    perc = info.controllers[2]
    assert perc.label == "PERC H730 Mini (MegaRAID)"
    assert perc.device == "MegaRAID SAS-3 3108 [Invader]"
    assert perc.driver == "megaraid_sas" and perc.driver_version == "07.734.00.00-rc1"
    assert perc.scsi_hosts == [0]
    assert perc.firmware is None  # not reported by this system: never invented
    assert info.controllers[0].driver == "ahci"

    disks = FakeR630().discover_remote_drives()
    system_info.assign_controllers(info, disks)
    assert (perc.physical_disks, perc.virtual_disks, perc.counts_known) == (5, 4, True)
    assert {d.controller for d in disks} == {"PERC H730 Mini (MegaRAID)"}


def test_controller_counts_use_sysfs_paths_when_available():
    sections = fixture_sections()
    sections["block"] = ("sda=/sys/devices/pci0000:00/0000:00:02.2/0000:02:00.0/host0/target0:2:0/0:2:0:0/block/sda\n"
                         "sde=/sys/devices/pci0000:00/0000:00:1f.2/ata1/host1/target1:0:0/1:0:0:0/block/sde\n")
    info = system_info.build_system_info(sections)
    disks = [raid.mark_virtual(DiskInfo(device_path="/dev/sda", name="sda", model="PERC H730 Mini")),
             DiskInfo(device_path="/dev/sde", name="sde", model="Samsung SSD")]
    system_info.assign_controllers(info, disks)
    by_slot = {c.pci_slot: c for c in info.controllers}
    assert (by_slot["02:00.0"].virtual_disks, by_slot["00:1f.2"].physical_disks) == (1, 1)
    assert disks[1].controller is None  # plain disks are not regrouped in the sidebar


def test_collect_parses_sections_and_survives_busybox():
    script_seen = []

    def run(cmd):
        script_seen.append(cmd)
        out = "@@@dmi\nsys_vendor=Dell Inc.\nproduct_name=PowerEdge R630\n@@@uptime\n871234.50 1234.00\n" \
              "@@@meminfo\nMemTotal:       528248040 kB\n@@@end\n"
        return 0, out, ""

    info = system_info.collect(run)
    assert "lspci -vmm -nn" in script_seen[0] and "dmidecode -t system" in script_seen[0]
    assert info.identity_line() == "Dell PowerEdge R630 · 503 GiB RAM · up 10 days, 2 h"
    assert info.controllers == [] and info.error is None

    busybox = system_info.collect(lambda cmd: (127, "", "sh: lscpu: not found"))
    assert busybox.identity_line() == "" and busybox.error

    boom = system_info.collect(lambda cmd: (_ for _ in ()).throw(OSError("ssh dropped")))
    assert boom.error == "ssh dropped"


def test_script_is_read_only():
    script = system_info.SYSTEM_INFO_SCRIPT
    for forbidden in ("storcli", "perccli", "megacli", "smartctl", "rm ", ">/sys", "> /sys"):
        assert forbidden not in script


def test_cpuinfo_fallback_and_free_output():
    cpuinfo = "processor\t: 0\nmodel name\t: ARMv7 Processor rev 1 (v7l)\nprocessor\t: 1\n"
    assert system_info.parse_cpu("", cpuinfo) == ("ARMv7 Processor rev 1 (v7l)", None, None, 2)
    assert system_info.parse_mem_total(text("host_summary.txt")) == 540925992960


def test_usb_bridge_types_do_not_change_the_disk_key():
    data = {"device": {"name": "/dev/sdb", "type": "sat,12", "protocol": "ATA"},
            "smart_status": {"passed": True}, "user_capacity": {"bytes": 500107862016}}
    disk = SmartParser.parse_smart_json(data, "/dev/sdb")
    assert disk.uid == "/dev/sdb" and disk.host_key == "local_/dev/sdb"
    assert DiskInfo(device_path="/dev/sdb", name="sdb", smart_dev_type="sat,12").uid == "/dev/sdb"
