"""Sanitizer: SYNTHETIC inputs with known sensitive values (none of them is real)."""
import json

import pytest

from smartlinux.core import sanitizer as san

# ---- synthetic sensitive values -------------------------------------------------------------
SERIAL = "ZA1B2C3D"
SERIAL_2 = "WD-WCC4N7XK2PL9"
WWN_HEX = "0x5000c500a1b2c3d4"
SAS_ADDR = "0x5000c500a1b2c3d5"
WWN_ID = 2712847316
UUID = "3f2504e0-4f89-11d3-9a0c-0305e82c3301"
SERVICE_TAG = "7XK9Q42"
SKU = "SKU-ACME-001"
ASSET = "ACME-ASSET-7731"
MAC = "a4:bf:01:23:45:67"
IPV4 = "10.11.12.13"
IPV6 = "fe80::a4bf:1ff:fe23:4567"
HOST = "acme-db01"
LABEL = "ACME_BACKUP"
USER = "jdoe"
VD_NAME = "CLIENTDATA"

SENSITIVE = [SERIAL, SERIAL_2, WWN_HEX, SAS_ADDR, str(WWN_ID), UUID, SERVICE_TAG, SKU, ASSET, MAC,
             IPV4, IPV6, HOST, LABEL, USER, VD_NAME]

SMARTCTL_JSON = json.dumps({
    "device": {"name": "/dev/bus/0", "type": "sat+megaraid,2", "protocol": "ATA"},
    "model_name": "Samsung SSD 860 EVO 500GB", "serial_number": SERIAL, "firmware_version": "RVT04B6Q",
    "wwn": {"naa": 5, "oui": 9528, "id": WWN_ID},
    "user_capacity": {"bytes": 500107862016},
    "ata_smart_attributes": {"table": [
        {"id": 5, "name": "Reallocated_Sector_Ct", "raw": {"value": 0, "string": "0"}},
        {"id": 241, "name": "Total_LBAs_Written", "raw": {"value": 271985884, "string": "271985884"}},
    ]},
}, indent=2)

SMARTCTL_TEXT = f"""smartctl 7.5 2025-04-30 r5714 [x86_64-linux-6.8.12-4-pve] (local build)
Model Family:     Western Digital Red
Device Model:     WDC WD40EFRX-68N32N0
Serial Number:    {SERIAL_2}
LU WWN Device Id: 5 0014ee 2b6f1c0d9
Firmware Version: 82.00A82
User Capacity:    4,000,787,030,016 bytes [4.00 TB]
  5 Reallocated_Sector_Ct   0x0033   200   200   140    Pre-fail  Always       -       0
"""

LSBLK = json.dumps({"blockdevices": [
    {"name": "sda", "path": "/dev/sda", "model": "ST1200MM0088", "size": "1.1T", "serial": SERIAL,
     "wwn": WWN_HEX, "vendor": "SEAGATE ", "tran": "sas", "type": "disk", "rota": True},
]}, indent=2)

DMIDECODE = f"""# dmidecode 3.6
Getting SMBIOS data from sysfs.
SMBIOS 3.0 present.
Handle 0x0001, DMI type 1, 27 bytes
System Information
\tManufacturer: Dell Inc.
\tProduct Name: PowerEdge R640
\tVersion: Not Specified
\tSerial Number: {SERVICE_TAG}
\tUUID: {UUID}
\tSKU Number: {SKU}
\tFamily: Not Specified

Handle 0x0003, DMI type 3, 22 bytes
Chassis Information
\tAsset Tag: {ASSET}
\tSerial Number: Not Specified
"""

PERCCLI = json.dumps({"Controllers": [{"Response Data": {
    "VD LIST": [{"DG/VD": "0/0", "TYPE": "RAID1", "State": "Optl", "Name": VD_NAME}],
    "Drive /c0/e32/s0 Device attributes": {"SN": SERIAL, "Firmware Revision": "TT31",
                                           "Model Number": "ST1200MM0088"},
    "Port Information": [{"Port": 0, "SAS address": SAS_ADDR}],
    "Inquiry Data": " ".join(f"{b:02x}" for b in ("SEAGATE ST1200MM0088    TT31" + SERIAL).encode()),
    "BIOS Version": "6.29.00.0_4.16.07.00_0x06120100", "Driver Version": "07.734.00.00-rc1",
}}]}, indent=2)

MISC = f"""link/ether {MAC} brd ff:ff:ff:ff:ff:ff
inet {IPV4}/24 brd 10.11.12.255 scope global vmbr0
inet6 {IPV6}/64 scope link
{HOST}.acme.local is up, short name {HOST.upper()}
/dev/sdb1: LABEL="{LABEL}" TYPE="ext4"
mounted on /run/media/{USER}/{LABEL}
config in /home/{USER}/.config/smartlinux/settings.json
version 1.2.3 kernel 6.8.12-4-pve, controller PCI 00:02:00:00, time 16:30:38
"""

FILES = {
    "smart/sda.json": SMARTCTL_JSON, "smart/sdb.txt": SMARTCTL_TEXT, "storage/lsblk.json": LSBLK,
    "system/dmidecode.txt": DMIDECODE, "vendor/perccli.json": PERCCLI, "system/misc.txt": MISC,
}
KNOWN = [(san.HOSTNAME, HOST), (san.USERNAME, USER), (san.USERNAME, "root")]


@pytest.fixture
def result():
    return san.sanitize_files(FILES, KNOWN)


def test_no_sensitive_value_survives(result):
    out, _ = result
    blob = "\n".join(out.values()).lower()
    for value in SENSITIVE:
        assert value.lower() not in blob, value
    # the WWN in smartctl's text form and the serial hidden in SCSI inquiry hex
    assert "0014ee 2b6f1c0d9" not in blob
    assert " ".join(f"{b:02x}" for b in SERIAL.encode()) not in blob


def test_what_development_needs_is_kept(result):
    out, _ = result
    blob = "\n".join(out.values())
    for value in ("Samsung SSD 860 EVO 500GB", "RVT04B6Q", "WDC WD40EFRX-68N32N0", "82.00A82", "ST1200MM0088",
                  "TT31", "SEAGATE", "PowerEdge R640", "Dell Inc.", "sat+megaraid,2", "500107862016",
                  "271985884", "Reallocated_Sector_Ct", "6.29.00.0_4.16.07.00_0x06120100",
                  "07.734.00.00-rc1", "smartctl 7.5 2025-04-30 r5714", "6.8.12-4-pve", "00:02:00:00",
                  "16:30:38", "1.2.3", '"oui": 9528', '"naa": 5', "RAID1"):
        assert value in blob, value


def test_mapping_is_consistent_across_files(result):
    out, s = result
    _, fake_serial = s.mapping[SERIAL.lower()]
    assert json.loads(out["smart/sda.json"])["serial_number"] == fake_serial
    assert json.loads(out["storage/lsblk.json"])["blockdevices"][0]["serial"] == fake_serial
    assert fake_serial in out["vendor/perccli.json"]
    assert len(fake_serial) == len(SERIAL)
    _, fake_wwn = s.mapping[WWN_HEX.lower()]
    assert fake_wwn.startswith("0x") and len(fake_wwn) == len(WWN_HEX)
    _, fake_host = s.mapping[HOST.lower()]
    assert out["system/misc.txt"].count(fake_host) == 2           # lower and UPPER case occurrence


def test_json_stays_valid_and_categories_are_counted(result):
    out, s = result
    for name in ("smart/sda.json", "storage/lsblk.json", "vendor/perccli.json"):
        json.loads(out[name])
    summary = s.summary()
    for category in (san.SERIAL, san.WWN, san.UUID, san.SERVICE_TAG, san.ASSET_TAG, san.MAC, san.IPV4,
                     san.IPV6, san.HOSTNAME, san.LABEL, san.USERNAME):
        assert summary.get(category, 0) >= 1, category
    assert all(isinstance(v, int) for v in summary.values())
    assert not any(v.lower() in json.dumps(summary).lower() for v in SENSITIVE)


def test_dmidecode_system_block_is_service_tag(result):
    _, s = result
    assert s.mapping[SERVICE_TAG.lower()][0] == san.SERVICE_TAG
    assert s.mapping[SKU.lower()][0] == san.SERVICE_TAG
    assert s.mapping[ASSET.lower()][0] == san.ASSET_TAG


def test_deterministic():
    out_a, _ = san.sanitize_files(FILES, KNOWN)
    out_b, _ = san.sanitize_files(FILES, KNOWN)
    assert out_a == out_b


def test_system_users_and_placeholders_are_not_replaced(result):
    out, s = result
    assert "root" not in s.mapping
    for placeholder in ("not specified", "xxxxxxx", "0000000000000000", "to be filled by o.e.m."):
        s.add(san.SERIAL, placeholder)
        assert placeholder not in s.mapping
    assert "Not Specified" in out["system/dmidecode.txt"]


def test_inquiry_data_is_removed(result):
    out, _ = result
    assert "<removed: SCSI inquiry data embeds the serial number>" in out["vendor/perccli.json"]


def test_defense_in_depth_catches_a_residue():
    s = san.Sanitizer()
    s.add(san.SERIAL, SERIAL)
    with pytest.raises(san.SanitizationError) as err:
        s.verify({"clean.txt": "nothing here", "leak.txt": f"serial {SERIAL.lower()} left behind"})
    assert err.value.filename == "leak.txt" and err.value.category == san.SERIAL
    assert SERIAL not in str(err.value)                    # the message never shows the original
    with pytest.raises(san.SanitizationError):
        s.verify({"hex.txt": " ".join(f"{b:02x}" for b in SERIAL.encode())})


def test_defense_in_depth_when_replacement_misses(monkeypatch):
    original = san.Sanitizer.apply

    def leaky(self, text):
        return text if "Serial Number" in text else original(self, text)   # forced residue

    monkeypatch.setattr(san.Sanitizer, "apply", leaky)
    with pytest.raises(san.SanitizationError):
        san.sanitize_files(FILES, KNOWN)


def test_versions_are_not_mistaken_for_ip_addresses():
    s = san.Sanitizer()
    s.collect("BIOS 6.29.00.0_4.16.07.00 FW 4.260.00-7126 smartctl 7.5 kernel 7.0.14-20-pve 0.0.0.0 127.0.0.1")
    assert not any(c == san.IPV4 for c, _ in s.mapping.values())
