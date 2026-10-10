from smartlinux.core.smart_parser import SMART_DEVICE_TYPES, reading_quality


def test_sat_is_tried_before_generic_scsi():
    assert SMART_DEVICE_TYPES.index("sat") < SMART_DEVICE_TYPES.index("scsi")
    assert SMART_DEVICE_TYPES[-1] == "scsi"


def test_bridge_identity_only_scores_low():
    bridge = {"model_name": "JMicron SATA", "smart_status": {"passed": True}}
    assert reading_quality(bridge) == 1


def test_ata_attribute_table_scores_full():
    assert reading_quality({"ata_smart_attributes": {"table": [{"id": 5}]}}) == 2


def test_nvme_and_scsi_telemetry_score_full():
    assert reading_quality({"nvme_smart_health_information_log": {}}) == 2
    assert reading_quality({"scsi_error_counter_log": {}}) == 2


def test_empty_reply_scores_zero():
    assert reading_quality({"smartctl": {"exit_status": 1}}) == 0
