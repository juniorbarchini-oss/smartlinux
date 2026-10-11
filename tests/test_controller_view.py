"""RAID controller node + controller view + serial scans (offscreen Qt, no hardware). Replays the
Dell R630 captures (tests/fixtures/dell_r630_perc_h730 + dell_r630_perccli)."""
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

QtWidgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)  # no Qt libs: skip

from smartlinux.core import controller as ctl  # noqa: E402
from smartlinux.core import system_info, vendor_tools as vt  # noqa: E402
from smartlinux.core.models import DiskInfo  # noqa: E402
from smartlinux.core.smart_parser import SmartParser  # noqa: E402
from smartlinux.ui.server_panel import ServerPanel  # noqa: E402
from test_megaraid import fixture_sections, megaraid_json  # noqa: E402
from test_megaraid_ui import r630_local_disks  # noqa: E402
from test_vendor_tools import FakeHost, synthetic_drive_state  # noqa: E402
from test_usb_compat import bot_disk  # noqa: E402

LABEL = "PERC H730 Mini (MegaRAID)"


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class Env:
    """Records every vendor tool argv and every SMART read, in order."""

    def __init__(self):
        self.log = []                  # ("vendor", argv) / ("smart", path, dev_type)
        self.hosts = []
        self.settings = {}
        self.opts = {"binary": "perccli64", "privilege": "root", "overrides": None, "fail": set(),
                     "on_read": None, "disks": r630_local_disks}

    def vendor_argv(self):
        return [e[1] for e in self.log if e[0] == "vendor"]

    def smart_reads(self):
        return [e[2] or e[1] for e in self.log if e[0] == "smart"]


@pytest.fixture
def env(app, monkeypatch):
    from smartlinux.ui import main_window as mw
    e = Env()

    class LoggingHost(FakeHost):
        def _execute(self, argv):
            e.log.append(("vendor", list(argv)))
            return super()._execute(argv)

    def make_host():
        host = LoggingHost(binary=e.opts["binary"], privilege=e.opts["privilege"], overrides=e.opts["overrides"])
        e.hosts.append(host)
        return host

    def fake_read(cls, path, dev_type=None):
        e.log.append(("smart", path, dev_type))
        if e.opts["on_read"]:
            e.opts["on_read"](path, dev_type)
        if dev_type in e.opts["fail"]:
            raise RuntimeError("Smartctl open device failed: INQUIRY failed")
        if dev_type is None:
            return bot_disk(path)              # a real (healthy) reading of a plain disk
        slot = int(dev_type.split(",")[1])
        disk = SmartParser.parse_smart_json(json.loads(megaraid_json(slot)), path)
        disk.smart_dev_type, disk.slot, disk.name = dev_type, slot, f"slot {slot}"
        return disk

    def sync_worker(self, fn, on_result=None, on_error=None, *args, **kwargs):
        res = fn(*args, **kwargs)
        if on_result:
            on_result(res)

    monkeypatch.setattr(mw.AppSettings, "get", classmethod(lambda cls, k, d=None: e.settings.get(k, d)))
    monkeypatch.setattr(mw.AppSettings, "set", classmethod(lambda cls, **v: e.settings.update(v) or True))
    monkeypatch.setattr(mw.vendor_tools, "LocalHost", make_host)
    monkeypatch.setattr(mw.LocalDiskDetector, "check_smartctl_available", classmethod(lambda cls: (True, "")))
    monkeypatch.setattr(mw.LocalDiskDetector, "discover_drives", classmethod(lambda cls: e.opts["disks"]()))
    monkeypatch.setattr(mw.LocalDiskDetector, "read_drive_smart", classmethod(fake_read))
    monkeypatch.setattr(mw.usb_compat, "detect_uas_smart_block", lambda d: None)
    monkeypatch.setattr(mw.system_info, "collect_local", lambda: system_info.build_system_info(fixture_sections()))
    monkeypatch.setattr(mw.HistoryManager, "append_snapshot", classmethod(lambda cls, d: None))
    monkeypatch.setattr(mw.ConfigManager, "load_servers", classmethod(lambda cls: []))
    monkeypatch.setattr(mw.MainWindow, "_start_hotplug_watcher", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_start_worker", sync_worker)
    monkeypatch.setattr(mw.MainWindow, "_launch_scan", lambda self, worker: worker.run())  # inline, same order
    e.mw = mw
    return e


def controller_item(win):
    return win.sidebar.local_root.child(0)


def open_controller(win):
    win.sidebar.tree.setCurrentItem(controller_item(win))
    return win.controller_view


# ---------------------------------------------------------------- the selector is gone

def test_selector_is_gone_and_old_setting_is_harmless(env):
    env.settings[vt.SETTINGS_KEY] = {"local|02:00.0": "vendor", "junk": 5}
    win = env.mw.MainWindow()
    win.sidebar.local_refresh_requested.emit()
    for attr in ("vendor_sections", "vendor_mode_changed", "disk_link_requested"):
        assert not hasattr(win.server_panel, attr)
    assert not hasattr(ServerPanel, "vendor_mode_changed")
    win.sidebar.tree.setCurrentItem(win.sidebar.local_root)
    assert not win.server_panel.findChildren(QtWidgets.QRadioButton)
    assert env.log == [] and env.hosts == []                  # refresh never reads the controller
    assert env.settings[vt.SETTINGS_KEY] == {"local|02:00.0": "vendor", "junk": 5}   # not deleted
    env.settings[vt.SETTINGS_KEY] = "not a dict"
    env.mw.MainWindow().sidebar.local_refresh_requested.emit()


# ---------------------------------------------------------------- controller node / view

def test_clicking_the_controller_node_opens_the_view_and_reads_nothing(env):
    win = env.mw.MainWindow()
    assert controller_item(win).text(0) == f"⚪  {LABEL} — 5 physical, 4 virtual"
    view = open_controller(win)
    assert win.detail_stack.currentWidget() is view
    assert env.log == [] and env.hosts == []
    assert view.title_lbl.text() == LABEL
    assert view.not_scanned_lbl.isVisible() or not view.not_scanned_lbl.isHidden()
    assert view.not_scanned_lbl.text() == ctl.NOT_SCANNED_TEXT
    assert view.disks_table.rowCount() == 5
    assert [view.disks_table.item(r, 3).text() for r in range(5)] == ["—"] * 5
    assert view.volumes_table.rowCount() == 4                 # the 4 virtual drives, RAID state not read yet
    assert {view.volumes_table.item(r, 2).text() for r in range(4)} == {"Not read yet"}
    assert "checked when you click" in view.tool_lbl.text()


def test_scan_controller_vendor_once_then_each_disk_in_series(env, monkeypatch):
    win = env.mw.MainWindow()
    view = open_controller(win)
    progress = []
    monkeypatch.setattr(view, "set_progress", lambda text, done, total: progress.append((text, done, total)))
    view.scan_btn.click()

    assert len(env.hosts) == 1                                         # one vendor read
    kinds = [e[0] for e in env.log]
    first_smart = kinds.index("smart")
    assert set(kinds[:first_smart]) == {"vendor"} and set(kinds[first_smart:]) == {"smart"}
    assert env.smart_reads() == [f"megaraid,{n}" for n in (0, 1, 2, 3, 5)]
    for argv in env.vendor_argv():
        assert argv[0] in vt.CANDIDATES and vt.validate_args(argv[1:])
    assert ("Disk 3 of 5", 3, 6) in progress
    assert progress[0][0].startswith("Reading the RAID state")
    assert win._scan_worker is None and not view.cancel_btn.isVisibleTo(view)
    assert view.last_scan_lbl.text().startswith("Last scan: ")


def test_unified_table_and_worst_status(env):
    win = env.mw.MainWindow()
    view = open_controller(win)
    view.scan_btn.click()
    row = [view.disks_table.item(2, c).text() for c in range(1, 9)]
    assert row[0] == "32:2 / DID 2"
    assert row[1] == "SEAGATE ST1200MM0088"
    assert "9 grown defects" in row[2]
    assert row[3] == "29 °C" and row[4] == "25,784" and row[5] == "9"
    assert row[6] == "Online" and row[7] == "VMDATA1"
    assert not view.not_scanned_lbl.isVisibleTo(view)
    vols = [[view.volumes_table.item(r, c).text() for c in range(7)] for r in range(view.volumes_table.rowCount())]
    assert vols[0][:5] == ["SO", "RAID1", "Optimal", "1.090 TB", "/dev/sda"] and vols[0][6] == ""
    assert vols[1][6] == ctl.SINGLE_DRIVE_NOTE and "32:2 (DID 2)" in vols[1][5]
    # worst of disks (grown defects = WARNING) and volumes (all Optimal)
    assert view.status_lbl.text().startswith("🟡")
    assert controller_item(win).text(0).startswith("🟡")
    assert "perccli64 007.1910 ✓" in view.tool_lbl.text()
    assert "Optimal, 98%" in view.facts_lbl.text() and "4.260.00-7126" in view.facts_lbl.text()


def test_synthetic_failed_drive_makes_the_controller_red(env):
    env.opts["overrides"] = synthetic_drive_state("State", "Failed", slot=1)   # SYNTHETIC variant
    win = env.mw.MainWindow()
    view = open_controller(win)
    view.scan_btn.click()
    assert view.disks_table.item(1, 7).text() == "Failed"
    assert controller_item(win).text(0).startswith("🔴")
    assert view.status_lbl.text().startswith("🔴")


def test_one_failing_disk_does_not_abort(env):
    env.opts["fail"] = {"megaraid,3"}
    win = env.mw.MainWindow()
    view = open_controller(win)
    view.scan_btn.click()
    assert env.smart_reads() == [f"megaraid,{n}" for n in (0, 1, 2, 3, 5)]
    assert view.disks_table.item(3, 3).text().startswith("⚪ Error: Smartctl open device failed")
    assert "SEAGATE" in view.disks_table.item(4, 2).text()
    assert win._all_known_disks["local_/dev/bus/0#megaraid,3"].error_message


def test_cancel_stops_cleanly(env):
    win = env.mw.MainWindow()
    view = open_controller(win)

    def cancel_on_second(path, dev_type):
        if dev_type == "megaraid,1":
            view.cancel_btn.click()

    env.opts["on_read"] = cancel_on_second
    view.scan_btn.click()
    assert env.smart_reads() == ["megaraid,0", "megaraid,1"]     # the running read finishes, no more
    assert win.status_bar.currentMessage() == "Scan cancelled."
    assert win._scan_worker is None and view.scan_btn.isEnabled()
    assert view.disks_table.item(2, 3).text() == "—"


def test_without_the_tool_smart_is_still_read(env):
    env.opts["binary"] = None
    win = env.mw.MainWindow()
    view = open_controller(win)
    view.scan_btn.click()
    assert env.vendor_argv() == []
    assert len(env.smart_reads()) == 5
    text = view.tool_lbl.text()
    for needle in ("Vendor tool not installed", "support.dell.com", "sudo dpkg -i perccli_*.deb",
                   "SmartLinux never installs it"):
        assert needle in text
    assert view.volumes_table.rowCount() == 4                 # no RAID report: the 4 listed virtual drives


def test_without_permissions_smart_is_still_read(env):
    env.opts["privilege"] = "none"
    win = env.mw.MainWindow()
    view = open_controller(win)
    view.scan_btn.click()
    assert env.vendor_argv() == [["perccli64", "-v"]]
    assert len(env.smart_reads()) == 5
    assert "administrator rights" in view.tool_lbl.text()


def test_row_refresh_scans_only_that_disk(env):
    win = env.mw.MainWindow()
    view = open_controller(win)
    view.row_scan_buttons[1].click()
    assert env.smart_reads() == ["megaraid,1"] and env.hosts == []
    assert "SEAGATE" in view.disks_table.item(1, 2).text()
    assert view.disks_table.item(0, 3).text() == "—"


def test_row_opens_the_disk_and_back_link_returns(env):
    win = env.mw.MainWindow()
    view = open_controller(win)
    view.disks_table.cellDoubleClicked.emit(2, 2)
    assert win.detail_stack.currentWidget() is win.detail_panel
    assert win.detail_panel.current_disk.uid == "/dev/bus/0#megaraid,2"
    assert win.detail_panel.back_btn.isVisibleTo(win.detail_panel)
    win.detail_panel.back_btn.click()
    assert win.detail_stack.currentWidget() is win.controller_view
    assert env.log == []


# ---------------------------------------------------------------- server panel

def test_server_panel_discovery_banner_and_controller_table(env):
    win = env.mw.MainWindow()
    win.sidebar.tree.setCurrentItem(win.sidebar.local_root)
    panel = win.server_panel
    assert panel.discovery_lbl.isVisibleTo(panel) and panel.discovery_lbl.text() == ctl.DISCOVERY_TEXT
    assert [panel.raid_table.item(0, c).text() for c in range(4)] == [LABEL, "⚪  Not scanned", "5", "4"]
    panel.raid_table.cellDoubleClicked.emit(0, 0)
    assert win.detail_stack.currentWidget() is win.controller_view
    win.controller_view.scan_btn.click()
    win.sidebar.tree.setCurrentItem(win.sidebar.local_root)
    assert not panel.discovery_lbl.isVisibleTo(panel)
    assert panel.raid_table.item(0, 1).text() == "🟡  Attention"
    assert panel.diag_btn.isEnabled()                                 # diagnostic bundle kept


def test_scan_all_disks_without_a_raid_controller(env):
    env.opts["disks"] = lambda: [DiskInfo(device_path=p, name=p[5:], model="Test Drive", size_human="1 TB")
                                 for p in ("/dev/sda", "/dev/sdb")]
    win = env.mw.MainWindow()
    win.sidebar.tree.setCurrentItem(win.sidebar.local_root)
    panel = win.server_panel
    assert not panel.raid_table.isVisibleTo(panel) and not panel.discovery_lbl.isVisibleTo(panel)
    panel.scan_all_btn.click()
    assert env.smart_reads() == ["/dev/sda", "/dev/sdb"] and env.hosts == []
    assert win._all_known_disks["local_/dev/sda"].last_scanned is not None
    assert win.sidebar.local_root.child(0).text(0).startswith("🟢")


def test_scan_all_disks_on_the_r630_reads_physical_disks_only(env):
    win = env.mw.MainWindow()
    win.sidebar.tree.setCurrentItem(win.sidebar.local_root)
    win.server_panel.scan_all_btn.click()
    assert env.smart_reads() == [f"megaraid,{n}" for n in (0, 1, 2, 3, 5)]   # virtual drives never read
    assert env.hosts == []


def test_non_raid_disk_view_has_no_back_link(env):
    env.opts["disks"] = lambda: [DiskInfo(device_path="/dev/sda", name="sda", model="Laptop SSD")]
    win = env.mw.MainWindow()
    win.sidebar.disk_selected.emit(win._all_known_disks["local_/dev/sda"])
    assert not win.detail_panel.back_btn.isVisibleTo(win.detail_panel)


# ---------------------------------------------------------------- a virtual drive opens its controller

def virtual_disk(win, device_path):
    return next(d for d in win._all_known_disks.values() if d.is_virtual and d.device_path == device_path)


def test_clicking_a_virtual_drive_opens_the_controller_view_with_its_row_selected(env):
    win = env.mw.MainWindow()
    win._on_disk_selected_in_sidebar(virtual_disk(win, "/dev/sdb"))
    view = win.controller_view
    assert win.detail_stack.currentWidget() is view
    assert env.log == []                                       # display only, nothing is read
    assert view.tabs.currentIndex() == 0
    rows = view.volumes_table.selectionModel().selectedRows()
    assert [view.volumes_table.item(r.row(), 4).text() for r in rows] == ["/dev/sdb"]


def test_virtual_drive_row_is_found_by_volume_name_after_the_controller_scan(env):
    win = env.mw.MainWindow()
    view = open_controller(win)
    win.controller_view.scan_requested.emit("", LABEL)
    win._on_disk_selected_in_sidebar(virtual_disk(win, "/dev/sdc"))
    rows = view.volumes_table.selectionModel().selectedRows()
    assert [view.volumes_table.item(r.row(), 0).text() for r in rows] == ["VMDATA2"]
    assert view.volumes_table.item(rows[0].row(), 1).text() == "RAID0"
    assert win.detail_stack.currentWidget() is view


def test_a_physical_disk_still_opens_its_own_view(env):
    win = env.mw.MainWindow()
    phys = next(d for d in win._all_known_disks.values() if not d.is_virtual and d.slot == 2)
    win._on_disk_selected_in_sidebar(phys)
    assert win.detail_stack.currentWidget() is win.detail_panel


# ---------------------------------------------------------------- export report from the controller

def test_controller_export_opens_the_export_dialog_with_only_its_physical_disks_checked(env, monkeypatch):
    from PySide6.QtCore import Qt
    from smartlinux.ui import export_dialog
    seen = {}

    class FakeDialog:
        exported_files = []

        def __init__(self, **kwargs):
            seen.update(kwargs)

        def exec(self):
            return 0

    monkeypatch.setattr(env.mw, "ExportDialog", FakeDialog)
    win = env.mw.MainWindow()
    view = open_controller(win)
    assert view.export_btn.isEnabled()
    view.export_btn.click()
    wanted = {d.uid for d in view.physical}
    assert wanted and {d.uid for d in seen["preselected"]} == wanted
    assert all(not d.is_virtual for d in seen["preselected"])
    # the real dialog checks exactly those rows
    dialog = export_dialog.ExportDialog(available_disks=seen["available_disks"], parent=win,
                                        preselected=seen["preselected"])
    checked = {dialog.disk_list_widget.item(i).data(Qt.UserRole).uid for i in range(dialog.disk_list_widget.count())
               if dialog.disk_list_widget.item(i).checkState() == Qt.Checked}
    assert checked == wanted


def test_controller_export_is_disabled_while_a_scan_runs(env):
    win = env.mw.MainWindow()
    view = open_controller(win)
    view.set_busy(True)
    assert not view.export_btn.isEnabled()
    view.set_busy(False)
    assert view.export_btn.isEnabled()
