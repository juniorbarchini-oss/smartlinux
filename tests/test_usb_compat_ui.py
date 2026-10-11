"""Detail panel + confirmation dialog for USB compatibility mode (offscreen Qt)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

QtWidgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)  # no Qt libs: skip
from PySide6 import QtCore  # noqa: E402

from smartlinux.core.usb_compat import UasCandidate, Volume  # noqa: E402
from smartlinux.ui.compat_dialog import plan_lines, CompatModeDialog  # noqa: E402
from smartlinux.ui.detail_panel import DetailPanel  # noqa: E402
from test_usb_compat import uas_disk, bot_disk  # noqa: E402

CAND = UasCandidate(bus_id="2-1.2", vid="0bc2", pid="a020", name="Seagate DJI Fly Drive",
                    device_path="/dev/sda", interface="2-1.2:1.0")
VOLS = [
    Volume(path="/dev/sda", type="disk"),
    Volume(path="/dev/sda1", type="part", fstype="btrfs", uuid="u1", label="USB_BTRFS",
           mountpoints=["/run/media/me/USB_BTRFS"]),
    Volume(path="/dev/sda2", type="part", fstype="ext4", uuid="u2", label="<b>x</b>",
           mountpoints=["/run/media/me/USB_DATA"]),
]


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_panel_shows_notice_instead_of_table(app):
    panel = DetailPanel()
    disk = uas_disk()
    disk.uas_candidate = CAND
    panel.display_disk(disk)
    assert not panel.compat_notice.isHidden()
    assert panel.table.isHidden()
    assert "Nothing is wrong with the drive" in panel.compat_notice_lbl.text()
    assert "UAS" in panel.compat_notice_lbl.text()
    assert panel.compat_btn.text() == "Read SMART in compatibility mode…"
    assert panel.temp_card.sub_lbl.text() == "Hidden by the USB adapter (UAS mode)"

    seen = []
    panel.compat_mode_requested.connect(seen.append)
    panel.compat_btn.click()
    assert seen == [disk]


def test_panel_shows_compat_reading_note(app):
    panel = DetailPanel()
    disk = bot_disk("/dev/sdc")
    disk.compat_read_note = "Read in compatibility mode on 2026-10-11 13:09; the drive is back in normal (UAS) mode."
    panel.display_disk(disk)
    assert panel.compat_notice.isHidden()
    assert not panel.table.isHidden() and panel.table.rowCount() == 29
    assert not panel.compat_note_banner.isHidden()
    assert "back in normal (UAS) mode" in panel.compat_note_lbl.text()


def test_regular_disk_has_no_notice(app):
    panel = DetailPanel()
    panel.display_disk(uas_disk())
    assert panel.compat_notice.isHidden() and panel.compat_note_banner.isHidden()


def test_busy_panel_locks_buttons(app):
    panel = DetailPanel()
    disk = uas_disk()
    disk.uas_candidate = CAND
    panel.display_disk(disk)
    panel.set_compat_busy(True, "Restoring normal (UAS) mode...")
    assert not panel.compat_btn.isEnabled() and not panel.scan_btn.isEnabled()
    panel.set_compat_busy(False)
    assert panel.compat_btn.isEnabled() and panel.scan_btn.isEnabled()


def test_confirmation_lists_every_step(app):
    lines = plan_lines(CAND, VOLS)
    html = "\n".join(lines)
    for needle in ("/dev/sda1", "USB_BTRFS", "/run/media/me/USB_DATA", "pkexec",
                   "20-30 seconds", "may change", "read-only", "no self-tests",
                   "Normal (UAS) mode is restored", "reduced speed", "reboot",
                   "serial number"):
        assert needle in html, needle
    assert [l[:2] for l in lines if not l.startswith(" ")] == ["a.", "b.", "c.", "d.", "e.", "f."]


def test_dialog_never_renders_volume_labels_as_html(app):
    dlg = CompatModeDialog(CAND, VOLS)
    labels = [l for l in dlg.findChildren(QtWidgets.QLabel) if "<b>x</b>" in l.text()]
    assert labels and all(l.textFormat() == QtCore.Qt.PlainText for l in labels)


def test_dialog_buttons_and_cancel_is_default(app):
    dlg = CompatModeDialog(CAND, VOLS)
    buttons = {b.text(): b for b in dlg.findChildren(QtWidgets.QPushButton)}
    assert set(buttons) == {"Continue", "Cancel"}
    assert buttons["Cancel"].isDefault()
    buttons["Cancel"].click()
    assert dlg.result() == QtWidgets.QDialog.Rejected


# ---------------------------------------------------------------- main window wiring

@pytest.fixture
def window(app, monkeypatch):
    from smartlinux.ui import main_window as mw
    monkeypatch.setattr(mw.LocalDiskDetector, "check_smartctl_available", classmethod(lambda cls: (True, "")))
    monkeypatch.setattr(mw.LocalDiskDetector, "discover_drives", classmethod(lambda cls: []))
    monkeypatch.setattr(mw.ConfigManager, "load_servers", classmethod(lambda cls: []))
    monkeypatch.setattr(mw.MainWindow, "_start_hotplug_watcher", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_discover_local_drives", lambda self: None)
    win = mw.MainWindow()
    yield mw, win
    win._hotplug_watcher = None


def test_refusal_is_shown_and_no_dialog_opens(window, monkeypatch):
    mw, win = window
    warnings, dialogs = [], []
    monkeypatch.setattr(mw.QMessageBox, "warning", lambda *a, **k: warnings.append(a[2]))
    monkeypatch.setattr(mw.CompatModeDialog, "exec", lambda self: dialogs.append(1))
    win._on_compat_preflight(uas_disk(), CAND, (VOLS, ["/run/media/me/USB_DATA is in use by: vlc (PID 4)."]))
    assert dialogs == [] and "vlc (PID 4)" in warnings[0]
    assert win._compat_worker is None


def test_cancel_does_nothing(window, monkeypatch):
    mw, win = window
    monkeypatch.setattr(mw.CompatModeDialog, "exec", lambda self: mw.CompatModeDialog.Rejected)
    started = []
    monkeypatch.setattr(mw.CompatModeWorker, "start", lambda self: started.append(1))
    win._on_compat_preflight(uas_disk(), CAND, (VOLS, []))
    assert started == [] and win._compat_worker is None


def test_continue_starts_worker_and_result_is_saved(window, monkeypatch):
    mw, win = window
    monkeypatch.setattr(mw.CompatModeDialog, "exec", lambda self: mw.CompatModeDialog.Accepted)
    started = []
    monkeypatch.setattr(mw.CompatModeWorker, "start", lambda self: started.append(self.candidate))
    win._on_compat_preflight(uas_disk(), CAND, (VOLS, []))
    assert started == [CAND] and not win.detail_panel.compat_btn.isEnabled()

    saved = []
    monkeypatch.setattr(mw.HistoryManager, "append_snapshot", classmethod(lambda cls, d: saved.append(d)))
    disk = bot_disk("/dev/sdc")
    disk.compat_read_note = "Read in compatibility mode on X; the drive is back in normal (UAS) mode."
    win._all_known_disks["local_/dev/sda"] = uas_disk()
    win._on_compat_done("local_/dev/sda", mw.usb_compat.CompatReadResult(disk=disk, restored=True))
    assert saved == [disk]
    assert "local_/dev/sda" not in win._all_known_disks and win._all_known_disks["local_/dev/sdc"] is disk
    assert win.detail_panel.current_disk is disk and win.detail_panel.compat_btn.isEnabled()


def test_local_scan_attaches_candidate(window, monkeypatch):
    mw, win = window
    monkeypatch.setattr(mw.LocalDiskDetector, "read_drive_smart", classmethod(lambda cls, p: uas_disk(p)))
    monkeypatch.setattr(mw.usb_compat, "detect_uas_smart_block", lambda d: CAND)
    assert win._do_read_single_disk(uas_disk()).uas_candidate is CAND
