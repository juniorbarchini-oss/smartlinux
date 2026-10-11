"""SMART scans are 100 % manual: listing drives never reads them (offscreen Qt, no hardware)."""
import os
from datetime import datetime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

QtWidgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)  # no Qt libs: skip
from PySide6 import QtCore  # noqa: E402

from smartlinux.core.models import DiskInfo  # noqa: E402
from smartlinux.ui import sidebar as sidebar_mod  # noqa: E402
from smartlinux.ui.detail_panel import DetailPanel  # noqa: E402
from smartlinux.ui.scan_state import format_last_scanned, scan_action_text  # noqa: E402
from smartlinux.ui.sidebar import SidebarWidget  # noqa: E402
from test_usb_compat import uas_disk, bot_disk  # noqa: E402


def listed(path):
    """A drive as discovery lists it: identity only, no SMART read."""
    return DiskInfo(device_path=path, name=path.rsplit("/", 1)[-1], model="Test Drive", size_human="1.0 TB")


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def window(app, monkeypatch):
    """MainWindow whose workers run synchronously and whose SMART reads are recorded."""
    from smartlinux.ui import main_window as mw
    drives = [listed("/dev/sda"), listed("/dev/sdb")]
    reads = []

    def fake_read(cls, path):
        reads.append(path)
        return bot_disk(path)

    def sync_worker(self, fn, on_result=None, on_error=None, *args, **kwargs):
        try:
            res = fn(*args, **kwargs)
        except Exception as e:  # pragma: no cover - surfaced through on_error like the real worker
            if on_error:
                on_error(str(e))
            return None
        if on_result:
            on_result(res)
        return None

    monkeypatch.setattr(mw.LocalDiskDetector, "check_smartctl_available", classmethod(lambda cls: (True, "")))
    monkeypatch.setattr(mw.LocalDiskDetector, "discover_drives",
                        classmethod(lambda cls: [listed(d.device_path) for d in drives]))
    monkeypatch.setattr(mw.LocalDiskDetector, "read_drive_smart", classmethod(fake_read))
    monkeypatch.setattr(mw.usb_compat, "detect_uas_smart_block", lambda d: None)
    monkeypatch.setattr(mw.HistoryManager, "append_snapshot", classmethod(lambda cls, d: None))
    monkeypatch.setattr(mw.ConfigManager, "load_servers", classmethod(lambda cls: []))
    monkeypatch.setattr(mw.MainWindow, "_start_hotplug_watcher", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_start_worker", sync_worker)
    win = mw.MainWindow()
    yield mw, win, reads
    win._hotplug_watcher = None


# ---------------------------------------------------------------- no automatic scans

def test_opening_the_window_does_not_scan(window):
    mw, win, reads = window
    assert reads == []
    assert win.detail_panel.current_disk.device_path == "/dev/sda"  # shown, not read
    assert win.detail_panel.scan_btn.text() == "🔍 Scan Now"
    assert "Select a drive and click 'Scan Now'" in win.status_bar.currentMessage()


def test_selecting_a_drive_does_not_scan(window):
    mw, win, reads = window
    win.sidebar.disk_selected.emit(win._all_known_disks["local_/dev/sdb"])
    assert reads == []
    assert win.detail_panel.current_disk.device_path == "/dev/sdb"


def test_hotplug_redetection_does_not_scan(window):
    mw, win, reads = window
    win._discover_local_drives()
    win._discover_local_drives()
    assert reads == []


def test_remote_server_listing_does_not_scan(window, monkeypatch):
    mw, win, reads = window
    disk = listed("/dev/sdx")
    disk.is_remote, disk.server_id = True, "srv1"
    win._on_remote_drives_discovered("srv1", [disk])
    win.sidebar.disk_selected.emit(disk)
    assert reads == []


# ---------------------------------------------------------------- the button

def test_button_text_follows_scan_state(window):
    mw, win, reads = window
    panel = win.detail_panel
    assert panel.scan_btn.text() == "🔍 Scan Now"
    panel.scan_btn.click()          # -> scan_requested -> _scan_single_disk
    assert reads == ["/dev/sda"]
    assert panel.scan_btn.text() == "↻ Refresh"
    assert panel.scan_btn.isEnabled()


def test_scan_requested_signal_triggers_scan(window):
    mw, win, reads = window
    win.detail_panel.scan_requested.emit(win._all_known_disks["local_/dev/sdb"])
    assert reads == ["/dev/sdb"]


def test_loading_state_is_kept_while_scanning(app):
    panel = DetailPanel()
    panel.display_disk(listed("/dev/sda"))
    panel.set_loading(True, "Reading...")
    assert panel.scan_btn.text() == "⏳ Scanning..." and not panel.scan_btn.isEnabled()
    panel.set_loading(False, "Error: boom")   # failed scan of an unscanned drive
    assert panel.scan_btn.text() == "🔍 Scan Now" and panel.scan_btn.isEnabled()


def test_scan_action_text():
    assert scan_action_text(None) == "🔍 Scan Now"
    assert scan_action_text(listed("/dev/sda")) == "🔍 Scan Now"
    assert scan_action_text(uas_disk()) == "↻ Refresh"          # health known, few attributes
    failed = listed("/dev/sda")
    failed.last_scanned = 1.0                                    # scanned, nothing usable came back
    assert scan_action_text(failed) == "↻ Refresh"


# ---------------------------------------------------------------- last scanned

def test_last_scanned_is_set_and_survives_redetection(window, monkeypatch):
    mw, win, reads = window
    monkeypatch.setattr(mw.time, "time", lambda: 1_791_734_820.0)
    win.detail_panel.scan_btn.click()
    assert win._all_known_disks["local_/dev/sda"].last_scanned == 1_791_734_820.0

    win._discover_local_drives()  # hotplug / refresh: fresh DiskInfo objects from lsblk
    merged = win._all_known_disks["local_/dev/sda"]
    assert merged.last_scanned == 1_791_734_820.0
    assert merged.power_on_hours == 196 and len(merged.attributes) == 29
    assert win.sidebar._local_drives["/dev/sda"] is merged
    assert reads == ["/dev/sda"]

    win.sidebar.disk_selected.emit(merged)
    assert win.detail_panel.scan_btn.text() == "↻ Refresh"
    assert win.detail_panel.last_scanned_lbl.text().startswith("Last scanned: ")


def test_remote_reconnect_keeps_last_scanned(window):
    mw, win, reads = window
    scanned = bot_disk("/dev/sdx")
    scanned.is_remote, scanned.server_id, scanned.last_scanned = True, "srv1", 42.0
    scanned.compat_read_note = None
    win._all_known_disks["srv1_/dev/sdx"] = scanned
    again = listed("/dev/sdx")
    again.is_remote, again.server_id = True, "srv1"
    win._on_remote_drives_discovered("srv1", [again])
    merged = win._all_known_disks["srv1_/dev/sdx"]
    assert merged is again and merged.last_scanned == 42.0 and len(merged.attributes) == 29


def test_compat_reading_keeps_note_and_gets_last_scanned(window, monkeypatch):
    mw, win, reads = window
    monkeypatch.setattr(mw.time, "time", lambda: 1000.0)
    disk = bot_disk("/dev/sda")
    disk.compat_read_note = "Read in compatibility mode on X; the drive is back in normal (UAS) mode."
    win._on_compat_done("local_/dev/sda", mw.usb_compat.CompatReadResult(disk=disk, restored=True))
    assert disk.last_scanned == 1000.0
    win._discover_local_drives()
    merged = win._all_known_disks["local_/dev/sda"]
    assert merged.compat_read_note == disk.compat_read_note and merged.last_scanned == 1000.0
    assert merged.model == "IND-S325S001TX"


def test_last_scanned_format():
    now = datetime(2026, 10, 11, 18, 0).timestamp()
    assert format_last_scanned(None) == "Not scanned yet — click 'Scan Now'"
    assert format_last_scanned(datetime(2026, 10, 11, 14, 7).timestamp(), now) == "Last scanned: 14:07"
    assert format_last_scanned(datetime(2026, 10, 9, 9, 5).timestamp(), now) == "Last scanned: Oct 9 09:05"


def test_panel_shows_not_scanned_yet(app):
    panel = DetailPanel()
    panel.display_disk(listed("/dev/sda"))
    assert panel.last_scanned_lbl.text() == "Not scanned yet — click 'Scan Now'"
    assert "then 'Refresh'" in panel.self_test_status_lbl.text()


# ---------------------------------------------------------------- sidebar context menu

def _menu_texts(app, monkeypatch, disk, pick_scan=True):
    sb = SidebarWidget()
    sb.set_local_disks([disk])
    item = sb.local_root.child(0)
    shown, emitted = [], []
    sb.scan_requested.connect(emitted.append)

    class FakeMenu(QtWidgets.QMenu):
        def exec(self, *a, **k):
            acts = self.actions()
            shown.extend(x.text() for x in acts)
            return acts[0] if pick_scan else None

    monkeypatch.setattr(sidebar_mod, "QMenu", FakeMenu)
    monkeypatch.setattr(sb.tree, "itemAt", lambda pos: item)
    sb._show_context_menu(QtCore.QPoint(1, 1))
    return shown, emitted


def test_context_menu_uses_button_text(app, monkeypatch):
    fresh = listed("/dev/sda")
    shown, emitted = _menu_texts(app, monkeypatch, fresh)
    assert shown[0] == "🔍 Scan Now" and emitted == [fresh]

    scanned = bot_disk("/dev/sda")
    shown, _ = _menu_texts(app, monkeypatch, scanned)
    assert shown[0] == "↻ Refresh"

    panel = DetailPanel()
    for disk in (fresh, scanned):
        panel.display_disk(disk)
        assert panel.scan_btn.text() == scan_action_text(disk)


def test_context_menu_scan_goes_through_main_window(window, monkeypatch):
    mw, win, reads = window
    class FakeMenu(QtWidgets.QMenu):
        def exec(self, *a, **k):
            return self.actions()[0]

    monkeypatch.setattr(sidebar_mod, "QMenu", FakeMenu)
    item = win.sidebar.local_root.child(1)
    monkeypatch.setattr(win.sidebar.tree, "itemAt", lambda pos: item)
    win.sidebar._show_context_menu(QtCore.QPoint(1, 1))
    assert reads == ["/dev/sdb"]
    assert win.detail_panel.current_disk.device_path == "/dev/sdb"
