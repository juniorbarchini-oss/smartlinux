"""R630 / PERC H730 in the UI (offscreen Qt, no hardware): controller grouping in the sidebar,
unique keys for disks sharing /dev/bus/0, -d megaraid,N scans, virtual drives that are never
read, and the Server panel."""
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

QtWidgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)  # no Qt libs: skip
from smartlinux.core import system_info  # noqa: E402
from smartlinux.core.models import HealthStatus  # noqa: E402
from smartlinux.core.smart_parser import SmartParser  # noqa: E402
from smartlinux.ui.detail_panel import DetailPanel  # noqa: E402
from smartlinux.ui.sidebar import SidebarWidget  # noqa: E402
from test_megaraid import FakeR630, fixture_sections, megaraid_json  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def r630_local_disks():
    disks = FakeR630().discover_remote_drives()
    for d in disks:
        d.is_remote, d.server_id, d.server_name = False, None, None
    return disks


def labels(item):
    return [item.child(i).text(0) for i in range(item.childCount())]


def test_sidebar_groups_physical_under_controller_and_virtual_apart(app):
    sb = SidebarWidget()
    sb.set_local_disks(r630_local_disks())
    top = labels(sb.local_root)
    assert top == ["🎛️  PERC H730 Mini (MegaRAID) — 5 physical, 4 virtual",
                   "🧩  Virtual drives (RAID volumes) — 4"]
    controller, virtual = sb.local_root.child(0), sb.local_root.child(1)
    assert [t.split("•")[0].strip() for t in labels(controller)] == \
        [f"⚪  slot {n}" for n in (0, 1, 2, 3, 5)]
    assert all("Virtual drive (RAID volume) (1.2 TB)" in t for t in labels(virtual))
    assert len(sb._local_drives) == 9


def test_sidebar_updates_the_right_disk_behind_the_controller(app):
    sb = SidebarWidget()
    sb.set_local_disks(r630_local_disks())
    scanned = SmartParser.parse_smart_json(json.loads(megaraid_json(2)), "/dev/bus/0")
    scanned.name = "slot 2"
    sb.update_disk_info(scanned)
    controller = sb.local_root.child(0)
    assert labels(controller)[2].startswith("🟡  slot 2 • SEAGATE ST1200MM0088")
    assert labels(controller)[1].startswith("⚪  slot 1")  # siblings on the same /dev/bus/0 untouched


def test_sidebar_remote_server_is_grouped_too(app):
    from smartlinux.core.models import ServerConfig
    sb = SidebarWidget()
    sb.set_servers([ServerConfig(id="r630", name="pve-r630", host="10.0.0.5")])
    sb.set_server_drives("r630", FakeR630().discover_remote_drives())
    srv = sb.remote_root.child(0)
    assert labels(srv)[0] == "🎛️  PERC H730 Mini (MegaRAID) — 5 physical, 4 virtual"
    seen = []
    sb.host_selected.connect(seen.append)
    sb.tree.setCurrentItem(srv)
    assert seen == ["r630"]


@pytest.fixture
def window(app, monkeypatch):
    from smartlinux.ui import main_window as mw
    reads = []

    def fake_read(cls, path, dev_type=None):
        reads.append((path, dev_type))
        slot = int(dev_type.split(",")[1])
        disk = SmartParser.parse_smart_json(json.loads(megaraid_json(slot)), path)
        disk.smart_dev_type, disk.slot, disk.name = dev_type, slot, f"slot {slot}"
        return disk

    def sync_worker(self, fn, on_result=None, on_error=None, *args, **kwargs):
        res = fn(*args, **kwargs)
        if on_result:
            on_result(res)

    monkeypatch.setattr(mw.LocalDiskDetector, "check_smartctl_available", classmethod(lambda cls: (True, "")))
    monkeypatch.setattr(mw.LocalDiskDetector, "discover_drives", classmethod(lambda cls: r630_local_disks()))
    monkeypatch.setattr(mw.LocalDiskDetector, "read_drive_smart", classmethod(fake_read))
    monkeypatch.setattr(mw.system_info, "collect_local",
                        lambda: system_info.build_system_info(fixture_sections()))
    monkeypatch.setattr(mw.HistoryManager, "append_snapshot", classmethod(lambda cls, d: None))
    monkeypatch.setattr(mw.ConfigManager, "load_servers", classmethod(lambda cls: []))
    monkeypatch.setattr(mw.MainWindow, "_start_hotplug_watcher", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_start_worker", sync_worker)
    win = mw.MainWindow()
    yield mw, win, reads


def test_main_window_keeps_one_entry_per_physical_disk(window):
    mw, win, reads = window
    keys = sorted(win._all_known_disks)
    assert len(keys) == 9
    assert "local_/dev/bus/0#megaraid,5" in keys and "local_/dev/sda" in keys
    assert reads == []  # listing never scans
    assert "(RAID: 5 physical, 4 virtual)" in win.status_bar.currentMessage()


def test_scanning_a_physical_disk_uses_its_megaraid_type(window):
    mw, win, reads = window
    disk = win._all_known_disks["local_/dev/bus/0#megaraid,2"]
    win.sidebar.disk_selected.emit(disk)
    win.detail_panel.scan_requested.emit(disk)
    assert reads == [("/dev/bus/0", "megaraid,2")]
    scanned = win._all_known_disks["local_/dev/bus/0#megaraid,2"]
    assert scanned.health_status == HealthStatus.WARNING
    assert scanned.controller == "PERC H730 Mini (MegaRAID)"
    assert win._all_known_disks["local_/dev/bus/0#megaraid,1"].health_status == HealthStatus.UNKNOWN
    assert "megaraid,2" in win.detail_panel.info_lbl.text()
    assert "9 GROWN DEFECTS" in win.detail_panel.health_badge.text()


def test_virtual_drive_is_never_scanned(window):
    mw, win, reads = window
    disk = win._all_known_disks["local_/dev/sda"]
    win.sidebar.disk_selected.emit(disk)
    assert not win.detail_panel.scan_btn.isEnabled() and not win.detail_panel.self_test_btn.isEnabled()
    assert "VIRTUAL DRIVE (RAID VOLUME)" in win.detail_panel.health_badge.text()
    win.detail_panel.scan_requested.emit(disk)
    win.sidebar.scan_requested.emit(disk)
    assert reads == []
    ok, msg = win._do_run_self_test(disk, "short")
    assert not ok and "Virtual" in msg


def test_server_panel_shows_identity_and_controllers(window):
    mw, win, reads = window
    win.sidebar.tree.setCurrentItem(win.sidebar.local_root)
    assert win.detail_stack.currentWidget() is win.server_panel
    panel = win.server_panel
    assert panel.identity_lbl.text().startswith("Dell PowerEdge R630 · 2 × Intel Xeon E5-2660 v3 · 503 GiB RAM")
    rows = [[panel.table.item(r, c).text() for c in range(panel.table.columnCount())]
            for r in range(panel.table.rowCount())]
    assert ["PERC H730 Mini (MegaRAID)", "RAID", "02:00.0", "megaraid_sas 07.734.00.00-rc1", "5", "4", "—"] in rows
    # selecting a disk goes back to the diagnostics panel
    win.sidebar.disk_selected.emit(win._all_known_disks["local_/dev/sdb"])
    assert win.detail_stack.currentWidget() is win.detail_panel


def test_hotplug_relisting_does_not_read_system_info(window, monkeypatch):
    mw, win, reads = window
    calls = []
    monkeypatch.setattr(mw.system_info, "collect_local", lambda: calls.append(1))
    win._discover_local_drives()  # what the hotplug timer calls
    assert calls == []
    win.sidebar.local_refresh_requested.emit()  # the user's Refresh button
    assert calls == [1]


def test_server_panel_before_connecting(app):
    from smartlinux.ui.server_panel import ServerPanel
    panel = ServerPanel()
    panel.display("srv", "NAS", None)
    assert panel.identity_lbl.text() == "Not read yet."
    assert "connect or refresh" in panel.notice_lbl.text()


def test_detail_panel_virtual_drive(app):
    disk = r630_local_disks()[0]
    panel = DetailPanel()
    panel.display_disk(disk)
    assert disk.is_virtual
    assert panel.temp_card.val_lbl.text() == "—"
    assert "no S.M.A.R.T. data of its own" in panel.info_lbl.text().replace("\n", " ")
    assert panel.table.rowCount() == 0
