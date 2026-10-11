"""Fake hosts that replay the Dell R630 captures for the diagnostic bundle tests (no hardware)."""
import os

from smartlinux.core import diagnostics, vendor_tools
from smartlinux.core.models import ServerConfig

ROOT = os.path.join(os.path.dirname(__file__), "fixtures")
H730 = os.path.join(ROOT, "dell_r630_perc_h730")
PERCCLI = os.path.join(ROOT, "dell_r630_perccli")


def fixture(directory, name):
    with open(os.path.join(directory, name), encoding="utf-8") as f:
        return f.read()


def _dmi(field):
    for line in fixture(H730, "dmi_id.txt").splitlines():
        k, _, v = line.partition("=")
        if k == field:
            return v + "\n"
    return None


PERCCLI_REPLIES = {
    "-v": "version.txt",
    "show ctrlcount J": "show_ctrlcount.json",
    "/c0 show J": "c0_show.json",
    "/c0 show all J": "c0_show_all.json",
    "/c0/vall show all J": "c0_vall_show_all.json",
    "/c0/eall/sall show all J": "c0_eall_sall_show_all.json",
    "/c0/bbu show all J": "c0_bbu_show_all.json",
    "/c0/dall show J": "c0_dall_show.json",
}


def r630_reply(argv):
    """(code, stdout, stderr) the R630 gave (or would give) for one whitelisted argv."""
    cmd = " ".join(argv)
    tool = os.path.basename(argv[0])
    if tool in ("perccli64", "perccli", "storcli64", "storcli"):
        name = PERCCLI_REPLIES.get(" ".join(argv[1:]))
        return (0, fixture(PERCCLI, name), "") if name else (1, "", "unexpected")
    if cmd.startswith("cat /sys/class/dmi/id/"):
        value = _dmi(argv[1].rsplit("/", 1)[-1])
        return (0, value, "") if value is not None else (1, "", "No such file or directory")
    if cmd == "dmidecode -t system -t bios":
        return 0, fixture(H730, "dmidecode_system_bios.txt"), ""
    if cmd == "lsblk -J -d -o " + diagnostics.LSBLK_COLUMNS:
        return 0, fixture(H730, "lsblk_virtual_drives.json"), ""
    if cmd == "lspci -vmm -nn":
        return 0, fixture(H730, "lspci_storage_controllers.txt"), ""
    if cmd == "smartctl --scan -j":
        return 0, fixture(H730, "smartctl_scan_j.json"), ""
    if cmd == "smartctl --scan":
        return 0, fixture(H730, "smartctl_scan.txt"), ""
    if cmd == "smartctl --version":
        return 0, "smartctl 7.5 2025-04-30 r5714 [x86_64-linux-7.0.14-20-pve] (local build)\n", ""
    if cmd == "lscpu":
        return 0, "\n".join(fixture(H730, "host_summary.txt").splitlines()[:9]) + "\n", ""
    if cmd == "grep MemTotal /proc/meminfo":
        return 0, "MemTotal:       528248040 kB\n", ""
    if cmd == "cat /etc/os-release":
        return 0, 'PRETTY_NAME="Debian GNU/Linux 13 (trixie)"\nNAME="Debian GNU/Linux"\n', ""
    if cmd == "uname -srm":
        return 0, "Linux 7.0.14-20-pve x86_64\n", ""
    if cmd == "ls /sys/class/scsi_host":
        return 0, "host0\nhost1\nhost10\n", ""
    if cmd.startswith("cat /sys/class/scsi_host/"):
        return 0, ("megaraid_sas\n" if "/host0/" in cmd else "ahci\n"), ""
    if cmd.startswith("modinfo "):
        if argv[1] == "megaraid_sas":
            return 0, ("filename:       /lib/modules/7.0.14-20-pve/kernel/drivers/scsi/megaraid/megaraid_sas.ko\n"
                       "description:    Broadcom MegaRAID SAS Driver\nversion:        07.734.00.00-rc1\n"
                       "signer:         Proxmox\nsig_key:        AA:BB\n"), ""
        return 1, "", f"modinfo: ERROR: Module {argv[1]} not found."
    if cmd.startswith("smartctl -j -a -d megaraid,") and cmd.endswith(" /dev/bus/0"):
        slot = argv[4].split(",")[1]
        name = "smartctl_j_a_megaraid_4_empty_slot.json" if slot == "4" else f"smartctl_j_a_megaraid_{slot}.json"
        return 0, fixture(H730, name), ""
    if cmd.startswith("smartctl -i /dev/sd"):
        return 2, fixture(H730, "smartctl_i_virtual_sda.txt"), ""
    return 127, "", f"sh: {argv[0]}: not found"


class FakeDiagHost(diagnostics.DiagHost):
    def __init__(self, is_root=True, engine="paramiko", vendor=True, reply=r630_reply):
        self.is_root = is_root
        self.engine = engine
        self.vendor = vendor
        self.reply = reply
        self.executed = []

    def execute(self, argv, timeout, sudo=False):
        self.executed.append(list(argv))
        return self.reply(list(argv))

    def vendor_host(self):
        host = self

        class VH(vendor_tools.ToolHost):
            privilege = "root" if host.is_root else "none"

            def find(self):
                return "perccli64" if host.vendor else None

            def _execute(self, argv):  # pragma: no cover - diagnostics runs vendor argv itself
                return host.execute(argv, 30)
        return VH()


def r630_server():
    return ServerConfig(id="r630", name="pve-r630", host="10.20.30.40", username="root")
