"""Seagate drives pack several counters into one SMART raw value (errors in the upper 16
bits, operations in the lower 32 bits). Decoding them stops healthy drives from being
flagged and the raw number from being shown as if it were a count."""
import copy
import json
import os

from smartlinux.core.models import HealthStatus
from smartlinux.core.smart_parser import (
    SmartParser, VENDOR_RAW_DECODERS, detect_raw_vendor,
)

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _load(*parts):
    with open(os.path.join(FIXTURES, *parts), encoding="utf-8") as f:
        return json.load(f)


def _attr(disk, attr_id):
    return next(a for a in disk.attributes if a.id == attr_id)


def test_real_seagate_mobile_drive_is_decoded():
    data = _load("seagate_st2000lm007", "smartctl_j_a.json")
    assert detect_raw_vendor(data) == "seagate"
    disk = SmartParser.parse_smart_json(data, "/dev/sdb")

    assert _attr(disk, 1).raw.startswith("0 errors / 126,256,062 reads")
    assert _attr(disk, 7).raw.startswith("46 errors / ")
    assert _attr(disk, 188).raw == "7"
    # the genuine problems are still reported
    assert _attr(disk, 5).status_type == HealthStatus.WARNING
    assert _attr(disk, 187).status_type == HealthStatus.WARNING or _attr(disk, 187).status == "ATTENTION"
    assert disk.health_status == HealthStatus.WARNING
    assert "Reallocated_Sector_Ct" in disk.health_summary


def test_packed_upper_fields_alone_do_not_trigger_command_timeout_attention():
    data = copy.deepcopy(_load("seagate_st2000lm007", "smartctl_j_a.json"))
    for item in data["ata_smart_attributes"]["table"]:
        if item["id"] == 188:
            item["raw"]["value"] = 3 << 32      # counter field 0, other packed fields set
            item["raw"]["string"] = str(3 << 32)
    disk = SmartParser.parse_smart_json(data, "/dev/sdb")
    attr = _attr(disk, 188)
    assert attr.raw == "0"
    assert attr.status == "OK"


def test_non_seagate_drive_keeps_the_raw_value_untouched():
    data = _load("usb_dji_fly_drive_bot", "smartctl_j_a_sat.json")   # an ATA SSD, not Seagate
    assert detect_raw_vendor(data) is None
    before = {a["id"]: a["raw"]["string"] for a in data["ata_smart_attributes"]["table"]}
    disk = SmartParser.parse_smart_json(data, "/dev/sdb")
    for attr in disk.attributes:
        assert attr.raw == before[attr.id]


def test_decoder_table_only_covers_verified_attributes():
    assert set(VENDOR_RAW_DECODERS["seagate"]) == {1, 7, 188}


def test_vendor_detection_from_model_name_prefix():
    assert detect_raw_vendor({"model_name": "ST4000DM004-2CV104"}) == "seagate"
    assert detect_raw_vendor({"model_name": "WDC WD30EFRX-68EUZN0"}) is None
    assert detect_raw_vendor({"model_family": "Seagate Barracuda 7200.14"}) == "seagate"
