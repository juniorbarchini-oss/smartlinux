"""Diagnostic bundle: fixed read-only list, collection with fake hosts replaying the real Dell R630
captures (tests/fixtures/dell_r630_perc_h730 + dell_r630_perccli), zip / manifest, size cap."""
import hashlib
import json
import os
import zipfile
from datetime import datetime, timezone

import pytest

from smartlinux.core import diagnostics as dg
from smartlinux.core import sanitizer as san
from smartlinux.core.models import DiskInfo, ServerConfig
from diag_fakes import FakeDiagHost, r630_reply
from test_megaraid import FakeR630

NOW = datetime(2026, 10, 11, 20, 0, 0, tzinfo=timezone.utc)


def r630_disks():
    return FakeR630().discover_remote_drives()


def build(host=None, disks=None, known=(), model="Dell PowerEdge R630", **kw):
    host = host or FakeDiagHost()
    disks = r630_disks() if disks is None else disks
    results = dg.Collector(host).collect(disks, **kw)
    view = dg.build_app_view("9.9.9", host.engine, disks)
    known = list(known) + dg.known_identifiers(["pve-r630", "10.20.30.40"], ["root"], disks)
    return dg.build_bundle(results, view, "9.9.9", host.engine, model, known, now=NOW), host, results


# ---------------------------------------------------------------- the fixed list

@pytest.mark.parametrize("argv", [c.argv for c in dg.BASE_COMMANDS] + [
    ("cat", "/sys/class/scsi_host/host12/proc_name"),
    ("smartctl", "-j", "-a", "/dev/sda"), ("smartctl", "-j", "-a", "-d", "megaraid,5", "/dev/bus/0"),
    ("smartctl", "-j", "-a", "-d", "sat+megaraid,5", "/dev/bus/0"), ("smartctl", "-a", "-d", "sat", "/dev/sda"),
    ("smartctl", "-i", "/dev/sdb"), ("smartctl", "-j", "-a", "-d", "sat,12", "/dev/sdc"),
    ("smartctl", "-j", "-a", "/dev/nvme0n1"),
])
def test_whitelist_accepts_the_fixed_reads(argv):
    assert dg.validate_command(list(argv)) == tuple(argv)


@pytest.mark.parametrize("argv", [
    ["rm", "-rf", "/"], ["sh", "-c", "id"], ["cat", "/etc/shadow"], ["cat", "/etc/hostname"],
    ["cat", "/sys/class/dmi/id/product_serial"], ["cat", "/sys/class/scsi_host/../../../etc/passwd"],
    ["cat", "/proc/meminfo"], ["smartctl", "-t", "short", "/dev/sda"], ["smartctl", "-s", "on", "/dev/sda"],
    ["smartctl", "-a", "/dev/sda;id"], ["smartctl", "-j", "-a", "-d", "megaraid,0 ", "/dev/bus/0"],
    ["smartctl", "-j", "-a", "-d", "$(id)", "/dev/sda"], ["smartctl", "-j", "-a", "/etc/passwd"],
    ["smartctl", "-j", "-a", "/dev/../etc/passwd"], ["smartctl", "--scan-open"], ["smartctl", "-x", "/dev/sda"],
    ["lsblk", "-J"], ["lsblk", "-J", "-d", "-o", "NAME,LABEL,MOUNTPOINT"], ["dmidecode"],
    ["dmidecode", "-t", "memory"], ["modinfo", "zfs"], ["lspci", "-vvv"], ["uname", "-a"], ["hostname"],
    ["ip", "addr"], ["perccli64", "/c0", "show", "J"], ["ls", "/home"], ["grep", "-r", ".", "/etc"], [],
    "smartctl --scan",
])
def test_whitelist_rejects_everything_else(argv):
    with pytest.raises(dg.CommandNotAllowed):
        dg.validate_command(argv)


def test_collector_rejects_before_running():
    host = FakeDiagHost()
    with pytest.raises(dg.CommandNotAllowed):
        dg.Collector(host).run(dg.DiagCommand(("smartctl", "-t", "long", "/dev/sda"), "x.txt"))
    assert host.executed == []


def test_disk_commands_follow_the_app():
    disks = r630_disks()
    cmds = [c.display for c in dg.disk_commands(disks)]
    assert "smartctl -i /dev/sda" in cmds                                   # virtual: identity only
    assert "smartctl -j -a -d megaraid,2 /dev/bus/0" in cmds                 # physical behind the PERC
    assert not any(c.startswith("smartctl -j -a /dev/sda") for c in cmds)
    usb = DiskInfo(device_path="/dev/sdc", name="sdc", is_usb=True)
    assert [c.display for c in dg.disk_commands([usb], {"/dev/sdc": "sat,12"})] == \
        ["smartctl -j -a -d sat,12 /dev/sdc"]
    legacy = DiskInfo(device_path="/dev/sda", name="sda")
    assert [c.display for c in dg.disk_commands([legacy], {"/dev/sda": "sat"}, text_mode=True)] == \
        ["smartctl -a -d sat /dev/sda"]
    evil = DiskInfo(device_path="/dev/sda;reboot", name="x")
    assert dg.disk_commands([evil]) == []


# ---------------------------------------------------------------- collection on the R630

def test_collection_runs_only_whitelisted_commands_and_survives_failures():
    bundle, host, results = build()
    for argv in host.executed:
        if argv[0] == "perccli64":
            assert dg.vendor_tools.validate_args(argv[1:])
        else:
            dg.validate_command(argv)
    by_cmd = {r.command: r for r in results}
    assert by_cmd["modinfo hpsa"].exit_code == 1 and by_cmd["modinfo hpsa"].error   # missing module: recorded
    assert by_cmd["smartctl -j -a -d megaraid,5 /dev/bus/0"].exit_code == 0
    assert "perccli64 /c0/eall/sall show all J" in by_cmd
    assert len(bundle.files) > 40


def test_zip_structure_and_manifest(tmp_path):
    bundle, host, results = build()
    assert bundle.zip_name == "smartlinux-diag-2026-10-11-dell-poweredge-r630.zip"
    path = tmp_path / bundle.zip_name
    dg.write_zip(bundle, str(path))
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        assert names[:3] == ["README.txt", "MANIFEST.json", "app_view.json"]
        manifest = json.loads(zf.read("MANIFEST.json"))
        readme = zf.read("README.txt").decode()
        view = json.loads(zf.read("app_view.json"))
        for entry in manifest["commands"]:
            assert set(entry) >= {"command", "exit_code", "duration_s", "file", "sha256", "truncated"}
            if entry["file"]:
                assert hashlib.sha256(zf.read(entry["file"])).hexdigest() == entry["sha256"]
        blob = b"".join(zf.read(n) for n in names).decode().lower()
    assert manifest["smartlinux_version"] == "9.9.9" and manifest["connection"] == "paramiko"
    assert manifest["sanitized_replacements"][san.SERIAL] > 0
    assert len(manifest["commands"]) == len(results)
    for needle in ("9.9.9", "2026-10-11", "does NOT contain: passwords", "did not send it anywhere"):
        assert needle in readme
    assert {d["classification"] for d in view["disks"]} == {"virtual (RAID volume)", "physical behind RAID controller"}
    assert view["ssh_engine"] == "paramiko"
    # sanitized: fixture serials, logical unit ids, SAS addresses, VD names, NAA ids, hostname
    for original in ("s402xfake03", "0x5000c50098f0f45f", "0x5000c50098fd8251", "vmdata1",
                     "61866da0824bef0031414c331f55b793", "pve-r630", "10.20.30.40"):
        assert original not in blob, original
    # kept: models, firmware, versions, -d types, counters
    for kept in ("st1200mm0088", "tt31", "4.260.00-7126", "megaraid,2", "271985884", "perc h730 mini",
                 "07.734.00.00-rc1", "1200243695616"):
        assert kept in blob, kept
    assert not os.path.exists(str(path) + ".part")


def test_modinfo_keeps_only_version_and_description():
    bundle, _, _ = build()
    text = bundle.files["storage/modinfo_megaraid_sas.txt"]
    assert "version:        07.734.00.00-rc1" in text and "description:" in text
    assert "signer" not in text and "filename" not in text


def test_dmidecode_only_as_root_and_service_tag_replaced():
    tag = "7XK9Q42"

    def reply(argv):
        code, out, err = r630_reply(argv)
        return code, out.replace("Serial Number: XXXXXXX", f"Serial Number: {tag}"), err

    bundle, host, results = build(host=FakeDiagHost(reply=reply))
    assert tag.lower() not in "\n".join(bundle.files.values()).lower()
    assert "TAG0001" in bundle.files["system/dmidecode.txt"]
    bundle, host, results = build(host=FakeDiagHost(is_root=False, vendor=False))
    assert ["dmidecode", "-t", "system", "-t", "bios"] not in host.executed
    entry = next(r for r in results if r.command.startswith("dmidecode"))
    assert entry.skipped == "needs root (not run)"


def test_vendor_tool_absent_means_no_vendor_commands():
    bundle, host, _ = build(host=FakeDiagHost(vendor=False))
    assert not any(a[0].startswith("perccli") for a in host.executed)
    assert "tool: not installed" in bundle.files["vendor/detection.txt"]


def test_size_cap_marks_truncated():
    big_line = "x" * 1000 + "\n"

    def reply(argv):
        if argv == ["lscpu"]:
            return 0, big_line * 3000, ""          # ~3 MB
        return r630_reply(argv)

    bundle, _, results = build(host=FakeDiagHost(reply=reply))
    entry = next(r for r in results if r.command == "lscpu")
    assert entry.truncated
    text = bundle.files["system/lscpu.txt"]
    assert len(text.encode()) <= dg.MAX_OUTPUT_BYTES + 100
    assert text.endswith("[truncated by SmartLinux: output larger than 2 MB]\n")
    assert all(len(l) in (0, 1000) for l in text.splitlines()[:-1])     # never cut inside a line


def test_timeout_and_exceptions_are_recorded():
    seen = []

    class Host(FakeDiagHost):
        def execute(self, argv, timeout, sudo=False):
            seen.append(timeout)
            if list(argv) == ["lspci", "-vmm", "-nn"]:
                raise OSError("connection reset")
            return super().execute(argv, timeout, sudo)

    _, _, results = build(host=Host())
    assert set(seen) == {30}
    entry = next(r for r in results if r.command == "lspci -vmm -nn")
    assert entry.error == "connection reset"


def test_cancel_stops_between_commands():
    calls = []
    host = FakeDiagHost()
    collector = dg.Collector(host, cancelled=lambda: len(host.executed) >= 3, progress=calls.append)
    with pytest.raises(dg.Cancelled):
        collector.collect(r630_disks())
    assert len(host.executed) == 3


def test_defense_in_depth_fails_without_writing(monkeypatch, tmp_path):
    original = san.Sanitizer.apply

    def leaky(self, text):
        return text if '"serial_number"' in text else original(self, text)   # forced residue

    monkeypatch.setattr(san.Sanitizer, "apply", leaky)
    with pytest.raises(san.SanitizationError) as err:
        build()
    assert "Nothing was saved" in str(err.value)
    assert list(tmp_path.iterdir()) == []


def test_remote_host_uses_the_ssh_engine():
    class Engine:
        config = ServerConfig(id="x", name="nas", host="nas.lan", username="admin", password="pw")

        def __init__(self):
            self.raw, self.sudo = [], []

        def _raw_exec(self, cmd, timeout=15):
            self.raw.append((cmd, timeout))
            return 0, "ok\n", ""

        def _exec_with_sudo(self, cmd, timeout=15):
            self.sudo.append(cmd)
            return 0, "ok\n", ""

    engine = Engine()
    host = dg.RemoteDiagHost(engine, "plink")
    assert not host.is_root and host.engine == "plink"
    dg.Collector(host).run(dg.BASE_COMMANDS[5])
    assert engine.raw == [("smartctl --scan -j", 30)]
    assert host.vendor_host().privilege == "sudo"


def test_planned_commands_list_everything_before_running():
    lines = dg.planned_commands(r630_disks())
    assert lines[0] == "cat /etc/os-release"
    assert "dmidecode -t system -t bios   (only if already root)" in lines
    assert "smartctl -j -a -d megaraid,5 /dev/bus/0" in lines
    assert "    /cN/eall/sall show all J" in lines
