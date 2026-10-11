"""Export diagnostic bundle dialog (offscreen Qt, fake host replaying the R630 captures)."""
import os
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

QtWidgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)  # no Qt libs: skip

from smartlinux.core import diagnostics as dg  # noqa: E402
from smartlinux.core import sanitizer as san  # noqa: E402
from smartlinux.ui import diagnostic_dialog as dd  # noqa: E402
from diag_fakes import FakeDiagHost  # noqa: E402
from test_megaraid import FakeR630  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def r630_job(host):
    disks = FakeR630().discover_remote_drives()

    def job(progress, cancelled):
        results = dg.Collector(host, progress, cancelled).collect(disks)
        view = dg.build_app_view("9.9.9", host.engine, disks)
        known = dg.known_identifiers(["pve-r630"], ["root"], disks)
        return dg.build_bundle(results, view, "9.9.9", host.engine, "Dell PowerEdge R630", known)
    return disks, job


def test_dialog_lists_commands_and_runs_nothing_until_asked(app):
    host = FakeDiagHost()
    disks, job = r630_job(host)
    dialog = dd.DiagnosticBundleDialog("pve-r630 (10.0.0.5)", dg.planned_commands(disks), job)
    commands = dialog.commands_view.toPlainText().splitlines()
    assert "smartctl --scan -j" in commands and "smartctl -j -a -d megaraid,2 /dev/bus/0" in commands
    assert "lsblk -J -d -o NAME,PATH,MODEL,SIZE,TYPE,TRAN,ROTA,SERIAL,VENDOR,WWN" in commands
    assert host.executed == [] and not dialog.save_btn.isEnabled()


def test_preview_is_sanitized_before_saving(app, tmp_path, monkeypatch):
    host = FakeDiagHost()
    disks, job = r630_job(host)
    dialog = dd.DiagnosticBundleDialog("pve-r630", dg.planned_commands(disks), job)
    dialog.start(sync=True)
    assert dialog.save_btn.isEnabled() and host.executed
    files = [dialog.file_list.item(i).text() for i in range(dialog.file_list.count())]
    assert files[:3] == ["README.txt", "MANIFEST.json", "app_view.json"]
    assert "smart/dev_bus_0__megaraid_2.json" in files
    dialog.file_list.setCurrentRow(files.index("smart/dev_bus_0__megaraid_2.json"))
    preview = dialog.preview.toPlainText()
    assert "ST1200MM0088" in preview and "S402XFAKE03" not in preview and "0x5000c50098f0f45f" not in preview
    assert list(tmp_path.iterdir()) == []                      # previewing writes nothing

    target = tmp_path / "bundle.zip"
    monkeypatch.setattr(dd.QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(target), "")))
    dialog.save_btn.click()
    assert dialog.saved_path == str(target)
    with zipfile.ZipFile(target) as zf:
        assert "README.txt" in zf.namelist() and "MANIFEST.json" in zf.namelist()
    assert "did not send it anywhere" in dialog.status_lbl.text()


def test_sanitization_failure_saves_nothing(app, tmp_path, monkeypatch):
    host = FakeDiagHost()
    disks, job = r630_job(host)
    original = san.Sanitizer.apply
    monkeypatch.setattr(san.Sanitizer, "apply",
                        lambda self, text: text if '"serial_number"' in text else original(self, text))
    dialog = dd.DiagnosticBundleDialog("pve-r630", [], job)
    dialog.start(sync=True)
    assert not dialog.save_btn.isEnabled() and dialog.bundle is None
    assert "Nothing was saved" in dialog.status_lbl.text()
    assert dialog.file_list.count() == 0


def test_cancel(app):
    host = FakeDiagHost()
    disks, job = r630_job(host)
    dialog = dd.DiagnosticBundleDialog("pve-r630", [], job)
    original_job = dialog.job

    def cancelling_job(progress, cancelled):
        dialog.worker.cancel()
        return original_job(progress, cancelled)

    dialog.job = cancelling_job
    dialog.start(sync=True)
    assert dialog.status_lbl.text() == "Cancelled. Nothing was saved."
    assert host.executed == []


def test_server_panel_button_opens_dialog(app, monkeypatch):
    from smartlinux.ui import main_window as mw
    from test_megaraid_ui import r630_local_disks
    from test_megaraid import fixture_sections
    from smartlinux.core import system_info
    opened = []

    def sync_worker(self, fn, on_result=None, on_error=None, *args, **kwargs):
        res = fn(*args, **kwargs)
        if on_result:
            on_result(res)

    monkeypatch.setattr(mw.LocalDiskDetector, "check_smartctl_available", classmethod(lambda cls: (True, "")))
    monkeypatch.setattr(mw.LocalDiskDetector, "discover_drives", classmethod(lambda cls: r630_local_disks()))
    monkeypatch.setattr(mw.system_info, "collect_local", lambda: system_info.build_system_info(fixture_sections()))
    monkeypatch.setattr(mw.ConfigManager, "load_servers", classmethod(lambda cls: []))
    monkeypatch.setattr(mw.MainWindow, "_start_hotplug_watcher", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_start_worker", sync_worker)
    monkeypatch.setattr(mw.DiagnosticBundleDialog, "exec", lambda self: opened.append(self) or 0)
    win = mw.MainWindow()
    win.sidebar.tree.setCurrentItem(win.sidebar.local_root)
    win.server_panel.diag_btn.click()
    assert len(opened) == 1
    dialog = opened[0]
    assert "this computer" in dialog.findChildren(QtWidgets.QLabel)[0].text()
    assert "smartctl -j -a -d megaraid,5 /dev/bus/0" in dialog.commands_view.toPlainText()
