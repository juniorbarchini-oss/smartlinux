import os

import pytest

from smartlinux.core.models import HealthStatus
from smartlinux.core.smart_text_parser import (
    is_unrecognized_option, looks_like_smartctl_text, parse_proc_partitions,
    parse_smart_text, smart_text_to_dict, text_reading_quality,
)

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "legacy_smartctl_5_38")


def fixture(name: str) -> str:
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read()


def test_unrecognized_option_detected():
    assert is_unrecognized_option(fixture("smartctl_json_unsupported.txt"))
    assert is_unrecognized_option(fixture("smartctl_scan_unsupported.txt"))
    assert not is_unrecognized_option(fixture("smartctl_a.txt"))
    assert text_reading_quality(fixture("smartctl_json_unsupported.txt")) == 0


def test_looks_like_smartctl_text_rejects_shell_errors():
    assert looks_like_smartctl_text(fixture("smartctl_H.txt"))
    assert not looks_like_smartctl_text("smartctl: command not found")
    assert not looks_like_smartctl_text("")


def test_full_report_identity():
    disk = parse_smart_text(fixture("smartctl_a.txt"), "/dev/sda")
    assert disk.device_path == "/dev/sda" and disk.name == "sda"
    assert disk.model == "WDC WD30EFRX-68EUZN0"
    assert disk.serial == "WD-REDACTED"
    assert disk.firmware == "82.00A82"
    assert disk.size_bytes == 3_000_592_982_016
    assert disk.size_human == "3.0 TB"


def test_full_report_telemetry():
    disk = parse_smart_text(fixture("smartctl_a.txt"), "/dev/sda")
    assert disk.power_on_hours == 21883
    assert disk.temperature_c == 30
    assert disk.power_cycles == 253


def test_full_report_attributes():
    disk = parse_smart_text(fixture("smartctl_a.txt"), "/dev/sda")
    assert len(disk.attributes) == 17
    by_id = {a.id: a for a in disk.attributes}
    a5 = by_id[5]
    assert (a5.name, a5.current, a5.worst, a5.threshold, a5.raw) == ("Reallocated_Sector_Ct", "195", "195", "140", "155")
    assert by_id[194].raw == "30"
    assert by_id[193].raw == "18342"
    # reallocated sectors > 0 is a warning, like in the JSON path
    assert a5.status_type == HealthStatus.WARNING
    assert disk.health_status == HealthStatus.WARNING
    assert "Reallocated_Sector_Ct" in disk.health_summary


def test_raw_attribute_table_fields():
    data = smart_text_to_dict(fixture("smartctl_a.txt"))
    row = next(r for r in data["ata_smart_attributes"]["table"] if r["id"] == 198)
    assert row["when_failed"] == ""
    assert row["flags"]["value"] == 0x30
    assert (row["value"], row["worst"], row["thresh"]) == (100, 253, 0)


def test_self_test_log_parsed():
    data = smart_text_to_dict(fixture("smartctl_l_selftest.txt"))
    table = data["ata_smart_self_test_log"]["standard"]["table"]
    assert len(table) == 20
    assert table[0]["type"]["string"] == "Short offline"
    assert table[0]["status"]["string"] == "Completed without error"
    assert table[0]["lifetime_hours"] == 21848
    assert table[5]["status"]["string"] == "Aborted by host"
    assert table[19]["lifetime_hours"] == 0


def test_self_test_status_summary_uses_hours_ago():
    disk = parse_smart_text(fixture("smartctl_a.txt"), "/dev/sda")
    # drive is at 21883h, latest test at 21848h -> 35h -> "1 day ago"
    assert disk.self_test_status == "Short offline: Completed without error (1 day ago)"


def test_health_only_output():
    data = smart_text_to_dict(fixture("smartctl_H.txt"))
    assert data["smart_status"]["passed"] is True
    assert text_reading_quality(fixture("smartctl_H.txt")) == 1
    disk = parse_smart_text(fixture("smartctl_H.txt"), "/dev/sda")
    assert disk.health_status == HealthStatus.HEALTHY


def test_info_only_output_has_identity_quality_one():
    assert text_reading_quality(fixture("smartctl_i.txt")) == 1
    disk = parse_smart_text(fixture("smartctl_i.txt"), "/dev/sda")
    assert disk.model == "WDC WD30EFRX-68EUZN0" and disk.size_bytes > 0


def test_full_report_scores_two():
    assert text_reading_quality(fixture("smartctl_a.txt")) == 2


def test_error_log_has_no_attributes():
    data = smart_text_to_dict(fixture("smartctl_l_error.txt"))
    assert "ata_smart_attributes" not in data
    assert text_reading_quality(fixture("smartctl_l_error.txt")) == 0


def test_failed_health_marks_disk_failed():
    text = fixture("smartctl_a.txt").replace("test result: PASSED", "test result: FAILED!")
    disk = parse_smart_text(text, "/dev/sda")
    assert disk.health_status == HealthStatus.FAILED


def test_failing_now_attribute_marks_disk_failed():
    text = fixture("smartctl_a.txt").replace(
        "Always       -       4200", "Always   FAILING_NOW 4200")
    disk = parse_smart_text(text, "/dev/sda")
    spin = next(a for a in disk.attributes if a.id == 3)
    assert spin.status_type == HealthStatus.FAILED
    assert disk.health_status == HealthStatus.FAILED


def test_self_test_in_progress():
    text = fixture("smartctl_a.txt").replace(
        "Self-test execution status:      (   0)	The previous self-test routine completed\n"
        "					without error or no self-test has ever \n"
        "					been run.",
        "Self-test execution status:      ( 249)	Self-test routine in progress...\n"
        "					90% of test remaining.")
    disk = parse_smart_text(text, "/dev/sda")
    assert "in progress" in disk.self_test_status and "90%" in disk.self_test_status


# ---- tolerance to smartctl 6.x / 7.x variants -------------------------------------------

SMARTCTL_7 = """smartctl 7.3 2022-02-28 r5338 [x86_64-linux-6.1.0] (local build)
Copyright (C) 2002-22, Bruce Allen, Christian Franke, www.smartmontools.org

=== START OF INFORMATION SECTION ===
Model Family:     Seagate BarraCuda 3.5
Device Model:     ST2000DM008-2FR102
Serial Number:    ZFL1ABCD
LU WWN Device Id: 5 000c50 0e1234567
Firmware Version: 0001
User Capacity:    2,000,398,934,016 bytes [2.00 TB]
Sector Sizes:     512 bytes logical, 4096 bytes physical
Rotation Rate:    7200 rpm
Form Factor:      3.5 inches
ATA Version is:   ACS-3 T13/2161-D revision 5
SMART support is: Available - device has SMART capability.
SMART support is: Enabled

=== START OF READ SMART DATA SECTION ===
SMART overall-health self-assessment test result: PASSED

SMART Attributes Data Structure revision number: 10
Vendor Specific SMART Attributes with Thresholds:
ID# ATTRIBUTE_NAME          FLAG     VALUE WORST THRESH TYPE      UPDATED  WHEN_FAILED RAW_VALUE
  1 Raw_Read_Error_Rate     0x000f   110   099   006    Pre-fail  Always       -       25214056
  5 Reallocated_Sector_Ct   0x0033   100   100   010    Pre-fail  Always       -       0
  9 Power_On_Hours          0x0032   091   091   000    Old_age   Always       -       8200h+12m+34.567s
 12 Power_Cycle_Count       0x0032   100   100   020    Old_age   Always       -       410
190 Airflow_Temperature_Cel 0x0022   065   052   045    Old_age   Always       -       35 (Min/Max 27/41)
194 Temperature_Celsius     0x0022   035   048   000    Old_age   Always       -       35 (0 20 0 0 0)
197 Current_Pending_Sector  0x0012   100   100   000    Old_age   Always       -       0

SMART Self-test log structure revision number 1
Num  Test_Description    Status                  Remaining  LifeTime(hours)  LBA_of_first_error
# 1  Extended offline    Completed without error       00%      8190         -
# 2  Short offline       Completed: read failure       90%      8100         123456

"""


def test_smartctl7_report():
    disk = parse_smart_text(SMARTCTL_7, "/dev/sdb")
    assert disk.model == "ST2000DM008-2FR102"
    assert disk.size_bytes == 2_000_398_934_016
    assert disk.power_on_hours == 8200
    assert disk.power_cycles == 410
    assert disk.temperature_c == 35
    assert disk.health_status == HealthStatus.HEALTHY
    assert disk.rotation_rate == "7200 RPM (HDD)"
    assert disk.self_test_status.startswith("Extended offline: Completed without error")


def test_unknown_attribute_columns_are_tolerated():
    text = SMARTCTL_7.replace("0x0033   100   100   010    Pre-fail  Always       -       0",
                              "0x0033   ---   ---   ---    Pre-fail  Always       -       0")
    assert len(parse_smart_text(text, "/dev/sdb").attributes) == 7


def test_ssd_rotation_rate():
    text = SMARTCTL_7.replace("7200 rpm", "Solid State Device")
    assert parse_smart_text(text, "/dev/sdb").rotation_rate.startswith("SSD")


def test_nvme_text_report():
    text = """smartctl 7.3 2022-02-28 r5338 [x86_64-linux-6.1.0] (local build)

=== START OF INFORMATION SECTION ===
Model Number:                       Samsung SSD 970 EVO 1TB
Serial Number:                      S1234
Firmware Version:                   2B2QEXE7
Total NVM Capacity:                 1,000,204,886,016 [1.00 TB]

=== START OF SMART DATA SECTION ===
SMART overall-health self-assessment test result: PASSED

SMART/Health Information (NVMe Log 0x02)
Critical Warning:                   0x00
Temperature:                        41 Celsius
Available Spare:                    100%
Available Spare Threshold:          10%
Percentage Used:                    3%
Power Cycles:                       120
Power On Hours:                     1,234
Unsafe Shutdowns:                   5
Media and Data Integrity Errors:    0
"""
    disk = parse_smart_text(text, "/dev/nvme0n1")
    assert disk.model == "Samsung SSD 970 EVO 1TB"
    assert disk.protocol == "NVMe"
    assert disk.temperature_c == 41 and disk.power_on_hours == 1234 and disk.power_cycles == 120
    assert disk.health_status == HealthStatus.HEALTHY


def test_scsi_sas_report():
    text = """smartctl 6.6 2016-05-31 r4324 [x86_64-linux] (local build)

=== START OF INFORMATION SECTION ===
Vendor:               SEAGATE
Product:              ST300MM0006
Revision:             0003
User Capacity:        300,000,000,000 bytes [300 GB]
Serial number:        S0K1ABCD

=== START OF READ SMART DATA SECTION ===
SMART Health Status: OK

Current Drive Temperature:     33 C
"""
    disk = parse_smart_text(text, "/dev/sdc")
    assert disk.model == "SEAGATE ST300MM0006"
    assert disk.serial == "S0K1ABCD"
    assert disk.temperature_c == 33
    assert disk.health_status == HealthStatus.HEALTHY


# ---- discovery ---------------------------------------------------------------------------

def test_proc_partitions_returns_only_whole_disks():
    assert parse_proc_partitions(fixture("proc_partitions.txt")) == [("sda", 2930266584)]


def test_proc_partitions_multiple_and_nvme():
    text = (
        "major minor  #blocks  name\n\n"
        "   8        0  976762584 sda\n   8        1  976760832 sda1\n"
        "   8       16  488386584 sdb\n 259        0  500107608 nvme0n1\n"
        " 259        1     524288 nvme0n1p1\n 179        0   31116288 mmcblk0\n"
        " 253        0   20971520 dm-0\n   9      127  976629760 md127\n"
    )
    assert [n for n, _ in parse_proc_partitions(text)] == ["sda", "sdb", "nvme0n1"]


def test_proc_partitions_garbage():
    assert parse_proc_partitions("") == []
    assert parse_proc_partitions("cat: can't open '/proc/partitions'") == []


# ---- attributes smartctl cannot name (generic SSD, not in its database) -----------------------

def test_unnamed_attributes_are_shown_as_vendor_specific_with_their_id():
    import os
    path = os.path.join(os.path.dirname(__file__), "fixtures", "generic_ssd_vendor_attributes", "smartctl_a.txt")
    disk = parse_smart_text(open(path).read(), "/dev/sda")
    by_id = {a.id: a for a in disk.attributes}
    assert len(disk.attributes) == 12                       # nothing is dropped
    for attr_id in (160, 161, 164, 245):
        attr = by_id[attr_id]
        assert attr.name == f"Vendor-specific (ID {attr_id})"
        assert "does not document" in attr.description      # the tooltip says why it has no name
    assert by_id[5].name == "Reallocated_Sector_Ct"         # named attributes keep smartctl's name
    assert by_id[164].raw == "179802"                       # the reading itself is untouched
    assert len({a.name for a in disk.attributes}) == 12     # unique names (history keys by name)
