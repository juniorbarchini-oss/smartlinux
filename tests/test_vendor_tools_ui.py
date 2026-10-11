"""Vendor tool mode in the UI (offscreen Qt, no hardware, no real perccli): it only runs on a
user Refresh, the choice is saved per host + controller, Standard read stays as before."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

QtWidgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)  # no Qt libs: skip

from smartlinux.core import system_info, vendor_tools as vt  # noqa: E402
from test_megaraid import fixture_sections  # noqa: E402
from test_megaraid_ui import r630_local_disks  # noqa: E402
from test_vendor_tools import FakeHost  # noqa: E402

KEY = "local|02:00.0"


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def window(app, monkeypatch):
    from smartlinux.ui import main_window as mw
    settings = {}
    hosts = []
    opts = {"binary": "perccli64", "privilege": "root"}

    def make_host():
        host = FakeHost(binary=opts["binary"], privilege=opts["privilege"])
        hosts.append(host)
        return host

    def sync_worker(self, fn, on_result=None, on_error=None, *args, **kwargs):
        res = fn(*args, **kwargs)
        if on_result:
            on_result(res)

    monkeypatch.setattr(mw.AppSettings, "get", classmethod(lambda cls, k, d=None: settings.get(k, d)))
    monkeypatch.setattr(mw.AppSettings, "set", classmethod(lambda cls, **v: settings.update(v) or True))
    monkeypatch.setattr(mw.vendor_tools, "LocalHost", make_host)
    monkeypatch.setattr(mw.LocalDiskDetector, "check_smartctl_available", classmethod(lambda cls: (True, "")))
    monkeypatch.setattr(mw.LocalDiskDetector, "discover_drives", classmethod(lambda cls: r630_local_disks()))
    monkeypatch.setattr(mw.system_info, "collect_local", lambda: system_info.build_system_info(fixture_sections()))
    monkeypatch.setattr(mw.HistoryManager, "append_snapshot", classmethod(lambda cls, d: None))
    monkeypatch.setattr(mw.ConfigManager, "load_servers", classmethod(lambda cls: []))
    monkeypatch.setattr(mw.MainWindow, "_start_hotplug_watcher", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_start_worker", sync_worker)

    def build(saved_mode=None, **options):
        opts.update(options)
        if saved_mode:
            settings[vt.SETTINGS_KEY] = {KEY: saved_mode}
        return mw.MainWindow()

    return build, settings, hosts


def executed(hosts):
    return [argv for h in hosts for argv in h.executed]


def open_server_panel(win):
    win.sidebar.tree.setCurrentItem(win.sidebar.local_root)
    return win.server_panel


def test_vendor_tool_never_runs_by_itself(window):
    build, settings, hosts = window
    win = build(saved_mode=vt.MODE_VENDOR)          # startup, even with "Vendor tool" saved
    win._discover_local_drives()                    # hotplug re-listing
    win._refresh_local_host()                       # startup-style listing
    assert hosts == [] and executed(hosts) == []
    section = open_server_panel(win).vendor_sections["02:00.0"]
    assert "not checked yet" in section.info_lbl.text()


def test_refresh_reads_controller_in_vendor_mode(window):
    build, settings, hosts = window
    win = build(saved_mode=vt.MODE_VENDOR)
    win.sidebar.local_refresh_requested.emit()      # the user's Refresh
    assert ["perccli64", "/c0/vall", "show", "all", "J"] in executed(hosts)
    assert all(vt.validate_args(a[1:]) for a in executed(hosts))
    section = open_server_panel(win).vendor_sections["02:00.0"]
    assert section.vendor_btn.isChecked() and "perccli64 007.1910 found" in section.vendor_btn.text()
    assert section.volumes_table.rowCount() == 4 and section.drives_table.rowCount() == 5
    assert section.volumes_table.item(1, 1).text() == "VMDATA1"
    assert "no redundancy" in section.volumes_table.item(1, 9).text()
    assert "Optimal" in section.status_lbl.text()
    assert "charge 98%" in section.battery_lbl.text()
    labels = [win.sidebar.local_root.child(1).child(i).text(0) for i in range(4)]
    assert any("sdb · VMDATA1 · RAID0 · 1 disk · no redundancy" in t for t in labels)
    controller = win.sidebar.local_root.child(0)
    assert controller.child(2).text(0).endswith("· VMDATA1")


def test_standard_mode_only_checks_for_the_tool(window):
    build, settings, hosts = window
    win = build()
    win.sidebar.local_refresh_requested.emit()
    assert executed(hosts) == [["perccli64", "-v"]]
    section = open_server_panel(win).vendor_sections["02:00.0"]
    assert section.standard_btn.isChecked() and section.vendor_btn.isEnabled()
    assert section.volumes_table is None
    assert all(d.raid_volume is None for d in win._host_drives[""])
    assert "Virtual drive (RAID volume)" in win.sidebar.local_root.child(1).child(0).text(0)


def test_choosing_vendor_tool_saves_and_does_not_run(window):
    build, settings, hosts = window
    win = build()
    win.sidebar.local_refresh_requested.emit()
    before = len(executed(hosts))
    open_server_panel(win).vendor_sections["02:00.0"].vendor_btn.setChecked(True)
    assert settings[vt.SETTINGS_KEY] == {KEY: vt.MODE_VENDOR}
    assert len(executed(hosts)) == before
    assert "click Refresh" in win.status_bar.currentMessage()
    win.server_panel.vendor_sections["02:00.0"].standard_btn.setChecked(True)
    assert settings[vt.SETTINGS_KEY] == {KEY: vt.MODE_STANDARD}


def test_missing_tool_explains_and_disables(window):
    build, settings, hosts = window
    win = build(saved_mode=vt.MODE_VENDOR, binary=None)
    win.sidebar.local_refresh_requested.emit()
    section = open_server_panel(win).vendor_sections["02:00.0"]
    assert not section.vendor_btn.isEnabled() and section.standard_btn.isChecked()
    text = section.info_lbl.text()
    for needle in ("support.dell.com", "PERCCLI", "SAS RAID", "StorCLI", "sudo dpkg -i perccli_*.deb",
                   "sudo rpm -ivh perccli-*.rpm", "/opt/MegaRAID", "never installs"):
        assert needle in text
    assert "http" not in text
    assert executed(hosts) == []


def test_tool_without_root_explains(window):
    build, settings, hosts = window
    win = build(saved_mode=vt.MODE_VENDOR, privilege="none")
    win.sidebar.local_refresh_requested.emit()
    section = open_server_panel(win).vendor_sections["02:00.0"]
    assert not section.vendor_btn.isEnabled()
    assert "administrator rights" in section.info_lbl.text()
    assert executed(hosts) == [["perccli64", "-v"]]


def test_drive_row_opens_the_sidebar_disk(window):
    build, settings, hosts = window
    win = build(saved_mode=vt.MODE_VENDOR)
    win.sidebar.local_refresh_requested.emit()
    section = open_server_panel(win).vendor_sections["02:00.0"]
    section.drives_table.cellDoubleClicked.emit(2, 0)
    assert win.detail_stack.currentWidget() is win.detail_panel
    assert win.detail_panel.current_disk.uid == "/dev/bus/0#megaraid,2"
    assert "VMDATA1" in win.detail_panel.info_lbl.text()
    open_server_panel(win)
    win.server_panel.vendor_sections["02:00.0"].volumes_table.cellDoubleClicked.emit(1, 0)
    assert win.detail_panel.current_disk.device_path == "/dev/sdb"
    assert "RAID VOLUME: VMDATA1" in win.detail_panel.health_badge.text()
