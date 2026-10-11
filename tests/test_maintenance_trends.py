"""Controller view Overview sentence, Maintenance cards and Trends (offscreen Qt, no hardware).
Real perccli64 captures (tests/fixtures/dell_r630_perccli) + SYNTHETIC variants derived from them."""
import json
import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from smartlinux.core import controller as ctl
from smartlinux.core import history_manager as hm
from smartlinux.core import maintenance as mt
from smartlinux.core import trends
from smartlinux.core import vendor_tools as vt
from smartlinux.core.models import DiskInfo, HealthStatus
from smartlinux.core.smart_parser import SmartParser
from test_megaraid import megaraid_json
from test_megaraid_ui import r630_local_disks
from test_vendor_tools import REPLIES, read_fixture_controller, synthetic_vd_state, _synthetic

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)


def cards(overrides=None):
    return {c.key: c for c in mt.build_cards(read_fixture_controller(overrides))}


def synthetic_show_all(mutate):
    return _synthetic(("/c0", "show", "all", "J"), mutate)


# ---------------------------------------------------------------- whitelist untouched

def test_whitelist_did_not_change():
    assert vt.ALLOWED_TEMPLATES == (
        ("-v",), ("show", "ctrlcount", "J"), (vt._CTRL, "show", "J"), (vt._CTRL, "show", "all", "J"),
        (vt._CTRL + "/vall", "show", "all", "J"), (vt._CTRL + "/eall/sall", "show", "all", "J"),
        (vt._CTRL + "/bbu", "show", "all", "J"), (vt._CTRL + "/dall", "show", "J"),
    )


def test_cards_only_use_replies_already_read():
    c = read_fixture_controller()
    assert set(c.raw) <= {"show", "show_all", "volumes", "drives", "battery", "groups"}
    assert len(mt.build_cards(c)) == 4


# ---------------------------------------------------------------- cards from the real captures

def test_schedule_card_with_na_consistency_check():
    card = cards()["schedule"]
    assert card.status == HealthStatus.HEALTHY                          # NA is neutral, not an alarm
    assert card.line == "Next patrol read 10/17/2026, 04:00:00 (every 7 days) · No consistency check is scheduled"
    rows = {label: (value, tech) for label, value, tech in card.details}
    assert rows["Next consistency check"] == ("Not scheduled", "Next Consistency check launch")
    assert rows["Battery learn cycle"][0] == "every 90 days"


def test_battery_card():
    card = cards()["battery"]
    assert card.status == HealthStatus.HEALTHY
    assert card.line == "Optimal · 98 % · protects the cache for ~32 min"
    rows = {label: value for label, value, _ in card.details}
    assert rows["Capacity"] == "404 mAh / 416 mAh" and rows["Charge cycles"] == "0"
    assert rows["Learn cycle"] == "OK (not running)" and rows["Replacement required"] == "No"


def test_controller_and_operations_cards():
    c = cards()
    assert c["controller"].status == HealthStatus.HEALTHY and c["controller"].line == "Optimal · no memory errors"
    assert {l: v for l, v, _ in c["controller"].details}["ECC errors"] == "0"
    assert c["operations"].line == "No operation in progress"


# ---------------------------------------------------------------- SYNTHETIC variants

def test_synthetic_ecc_and_uncorrectable_memory_errors_warn():
    def mutate(rd):
        rd["Status"]["ECC Bucket Count"] = 3
    card = cards(synthetic_show_all(mutate))["controller"]
    assert card.status == HealthStatus.WARNING and "ECC bucket count 3" in card.line

    def mutate2(rd):
        rd["Status"]["Memory Uncorrectable Errors"] = 2
    card = cards(synthetic_show_all(mutate2))["controller"]
    assert card.status == HealthStatus.WARNING and "2 uncorrectable memory errors" in card.line


def test_synthetic_rebuild_at_40_percent():
    def mutate(rd):
        rd["VD0 Properties"]["Active Operations"] = "Rebuild (Progress 40%, Estimated Time Left 2 Hours 10 Minutes)"
    card = cards(_synthetic(("/c0/vall", "show", "all", "J"), mutate))["operations"]
    assert card.status == HealthStatus.WARNING
    assert card.line == "Rebuild on SO: 40 % · about 2 Hours 10 Minutes left"


def test_synthetic_operation_without_progress_is_not_invented():
    def mutate(rd):
        rd["VD1 Properties"]["Active Operations"] = "Consistency Check"
    card = cards(_synthetic(("/c0/vall", "show", "all", "J"), mutate))["operations"]
    assert card.line == "Consistency Check on VMDATA1" and card.status == HealthStatus.HEALTHY


def test_synthetic_battery_replacement_required():
    def mutate(rd):
        for row in rd["BBU_Firmware_Status"]:
            if row["Property"] == "Replacement required":
                row["Value"] = "Yes"
    card = cards(_synthetic(("/c0/bbu", "show", "all", "J"), mutate))["battery"]
    assert card.status == HealthStatus.WARNING and card.line.endswith("· replacement required")


def test_synthetic_scheduled_consistency_check_and_missing_battery():
    def mutate(rd):
        rd["Scheduled Tasks"]["Next Consistency check launch"] = "10/18/2026, 03:00:00"
    assert "Next consistency check 10/18/2026, 03:00:00" in cards(synthetic_show_all(mutate))["schedule"].line
    no_bbu = json.dumps({"Controllers": [{"Command Status": {"Status": "Failure", "Detailed Status": [
        {"ErrMsg": "BBU doesn't exist"}]}}]})
    card = cards({("/c0/bbu", "show", "all", "J"): no_bbu})["battery"]
    assert card.status == HealthStatus.UNKNOWN and "No battery" in card.line


# ---------------------------------------------------------------- Overview sentence

def scanned_r630():
    disks = r630_local_disks()
    out = []
    for d in disks:
        if d.is_virtual:
            out.append(d)
            continue
        s = SmartParser.parse_smart_json(json.loads(megaraid_json(d.slot)), d.device_path)
        s.smart_dev_type, s.slot, s.controller, s.last_scanned = d.smart_dev_type, d.slot, d.controller, 1.0
        out.append(s)
    return [d for d in out if not d.is_virtual], [d for d in out if d.is_virtual]


def test_sentence_lists_only_the_exceptions():
    physical, virtual = scanned_r630()
    assert ctl.status_sentence(physical, virtual, read_fixture_controller()) == \
        "2 disks have reallocated sectors."
    report = read_fixture_controller(synthetic_vd_state("Dgrd"))                # SYNTHETIC
    assert "1 volume has lost redundancy" in ctl.status_sentence(physical, virtual, report)


def test_sentence_when_everything_is_fine_and_before_scanning():
    healthy = DiskInfo(device_path="/dev/sda", name="sda", health_status=HealthStatus.HEALTHY,
                       health_summary="Healthy", last_scanned=1.0)
    assert ctl.status_sentence([healthy], []) == "Everything is in order."
    assert ctl.status_sentence([DiskInfo(device_path="/dev/sdb", name="sdb")], []) == ctl.NOT_SCANNED_TEXT


# ---------------------------------------------------------------- Trends

def entry(days_ago, **values):
    return {"timestamp": (NOW - timedelta(days=days_ago)).isoformat(timespec="seconds"), "serial": "X", **values}


def by_key(trend):
    return {m.metric.key: m for m in trend.metrics}


def test_trend_getting_worse():
    history = [entry(20, grown_defects=2, uncorrected_errors=0, temperature_c=30),
               entry(5, grown_defects=5, uncorrected_errors=0, temperature_c=31),
               entry(0, grown_defects=9, uncorrected_errors=1, temperature_c=29)]
    t = by_key(trends.disk_trend(history, now=NOW))
    assert t["defects"].current_text == "9"
    assert [c.text for c in t["defects"].changes] == ["▲ +4", "▲ +7"]
    assert [c.text for c in t["uncorrected"].changes] == ["▲ +1", "▲ +1"]
    assert [c.text for c in t["temperature"].changes] == ["▼ -2", "▼ -1"]
    assert t["temperature"].current_text == "29 °C"


def test_trend_stable_and_window_without_readings():
    history = [entry(10, grown_defects=0, temperature_c=30), entry(0, grown_defects=0, temperature_c=30)]
    t = by_key(trends.disk_trend(history, now=NOW))
    assert [c.text for c in t["defects"].changes] == ["—", "= no change"]   # no reading inside 7 days


def test_trend_with_too_little_history():
    assert trends.disk_trend([entry(0, grown_defects=3)], now=NOW).enough is False
    assert trends.disk_trend([], now=NOW).enough is False
    assert trends.NOT_ENOUGH == "Not enough history yet — scan again later"


def test_old_history_lines_still_work(tmp_path, monkeypatch):
    path = tmp_path / "history.jsonl"
    monkeypatch.setattr(hm, "HISTORY_DIR", str(tmp_path))
    monkeypatch.setattr(hm, "HISTORY_FILE", str(path))
    old = {"timestamp": (NOW - timedelta(days=3)).isoformat(timespec="seconds"), "serial": "S402XFAKE03",
           "model": "SEAGATE ST1200MM0088", "device_path": "/dev/bus/0", "health_status": "healthy",
           "temperature_c": 31, "power_on_hours": 25700, "reallocated_sector_ct": None, "pending_sector": None,
           "reallocated_event_count": None, "nvme_wear_pct": None}
    path.write_text(json.dumps(old) + "\n")
    disk = SmartParser.parse_smart_json(json.loads(megaraid_json(2)), "/dev/bus/0")
    disk.vendor_media_errors = 0
    hm.HistoryManager.append_snapshot(disk)
    history = hm.HistoryManager.get_history(disk)
    assert len(history) == 2 and "grown_defects" not in history[0]
    assert (history[1]["grown_defects"], history[1]["uncorrected_errors"], history[1]["media_errors"]) == (9, 0, 0)
    t = by_key(trends.disk_trend(history))
    assert t["defects"].current_text == "9" and t["temperature"].changes[0].text == "▼ -2"
    assert [c.text for c in t["uncorrected"].changes] == ["—", "—"]          # the old line has no value


# ---------------------------------------------------------------- the view (offscreen Qt)

REAL_APPEND = hm.HistoryManager.__dict__["append_snapshot"]   # before any fixture patches it
QtWidgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
from test_controller_view import app, env, open_controller  # noqa: E402,F401  (fixture reuse)


@pytest.fixture
def history_file(tmp_path, monkeypatch, env):
    monkeypatch.setattr(hm, "HISTORY_DIR", str(tmp_path))
    monkeypatch.setattr(hm, "HISTORY_FILE", str(tmp_path / "history.jsonl"))
    monkeypatch.setattr(env.mw.HistoryManager, "append_snapshot", REAL_APPEND)
    return tmp_path / "history.jsonl"


def test_three_tabs_with_clear_empty_states(env, history_file):
    win = env.mw.MainWindow()
    view = open_controller(win)
    assert [view.tabs.tabText(i) for i in range(view.tabs.count())] == ["Overview", "Maintenance", "Trends"]
    assert view.summary_lbl.text() == ctl.NOT_SCANNED_TEXT
    assert view.maint_empty_lbl.text() == ctl.NOT_SCANNED_TEXT and view.cards == {}
    assert not view.trends_empty_lbl.isHidden() and view.trends_empty_lbl.text() == ctl.NOT_SCANNED_TEXT
    assert env.log == []


def test_after_a_scan_overview_maintenance_and_trends(env, history_file):
    win = env.mw.MainWindow()
    view = open_controller(win)
    view.scan_btn.click()
    assert view.summary_lbl.text() == "2 disks have reallocated sectors."
    assert list(view.cards) == ["schedule", "battery", "controller", "operations"]
    battery = view.cards["battery"]
    assert battery.line_lbl.text() == "Optimal · 98 % · protects the cache for ~32 min"
    assert battery.title_lbl.text().startswith(HealthStatus.HEALTHY.icon)
    assert battery.details.isHidden()
    battery.details_btn.setChecked(True)
    assert not battery.details.isHidden() and battery.details_btn.text() == "Hide details"
    tooltips = {w.toolTip() for w in battery.details.findChildren(QtWidgets.QLabel)}
    assert "Average time to empty" in tooltips                       # technical name as a tooltip
    # one scan: every disk has a single reading
    t = view.trends_table
    assert t.rowCount() == 5 and t.item(0, 1).text() == trends.NOT_ENOUGH
    # a second scan gives a comparison (same values: "= no change")
    view.scan_btn.click()
    assert t.rowCount() == 15
    rows = [[t.item(r, c).text() if t.item(r, c) else "" for c in range(5)] for r in range(t.rowCount())]
    assert rows[6] == ["slot 2", "Defects", "9", "= no change", "= no change"]
    assert rows[7][1:3] == ["Uncorrected errors", "0"]
    for table in (view.trends_table, view.volumes_table):
        assert table.columnCount() <= 7
    assert view.trends_table.columnCount() <= 6
    lines = [json.loads(l) for l in history_file.read_text().splitlines()]
    assert {l.get("grown_defects") for l in lines} == {0, 9, 2}
    assert all("media_errors" in l for l in lines)                 # from the vendor tool


def test_maintenance_without_vendor_tool(env, history_file):
    env.opts["binary"] = None
    win = env.mw.MainWindow()
    view = open_controller(win)
    view.scan_btn.click()
    assert view.cards == {}
    assert "vendor tool" in view.maint_empty_lbl.text() and not view.maint_empty_lbl.isHidden()
    assert view.summary_lbl.text() == "2 disks have reallocated sectors."


# ---- polish: single-drive volumes are information, not a problem --------------------------

def test_single_drive_volumes_are_a_neutral_note_not_part_of_the_problem_sentence():
    report = read_fixture_controller()
    assert "redundancy" not in ctl.status_sentence([], [], report)
    assert ctl.redundancy_note(report) == "3 volumes are single drives (RAID0, no controller redundancy)."
    assert ctl.redundancy_note(None) == ""


def test_lost_redundancy_is_still_a_problem():
    physical, virtual = scanned_r630()
    report = read_fixture_controller(synthetic_vd_state("Dgrd"))                # SYNTHETIC
    assert "lost redundancy" in ctl.status_sentence(physical, virtual, report)


def test_operations_card_reads_no_operation_instead_of_none():
    report = read_fixture_controller()
    card = mt.operations_card(report.volumes)
    assert all(value == "No operation" for _label, value, _tip in card.details)
