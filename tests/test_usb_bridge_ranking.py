"""Regression: a DJI Fly Drive (Seagate USB bridge, SCSI-only SMART) was shown as
"Unsupported device bridge" because the empty reply to the sntrealtek probe
(quality 2, size 0) outranked the real sat,auto / scsi readings (quality 1, with size)."""
import json
import os
import subprocess
from types import SimpleNamespace

import pytest

from smartlinux.core import detector
from smartlinux.core.models import HealthStatus
from smartlinux.core.smart_parser import reading_rank

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "usb_dji_fly_drive")

BY_TYPE = {
    None: "none", "sat": "sat", "sat,auto": "sat_auto", "scsi": "scsi", "sntrealtek": "sntrealtek",
}


def _fixture(name):
    with open(os.path.join(FIXTURES, f"smartctl_j_a_{name}.json"), encoding="utf-8") as f:
        return f.read()


def test_identified_reading_outranks_empty_richer_one():
    assert reading_rank(1, 1_000_000) > reading_rank(2, 0)
    assert reading_rank(2, 1_000_000) > reading_rank(1, 1_000_000)
    assert reading_rank(0, 0) < reading_rank(1, 0)


def test_local_read_picks_scsi_reading_over_empty_nvme_probe(monkeypatch):
    def fake_run(cmd, **kwargs):
        dev_type = cmd[cmd.index("-d") + 1] if "-d" in cmd else None
        name = BY_TYPE.get(dev_type)
        out = _fixture(name) if name else "{}"
        return SimpleNamespace(stdout=out, stderr="", returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(detector.LocalDiskDetector, "get_smartctl_bin", classmethod(lambda cls: "smartctl"))
    monkeypatch.setattr(detector.LocalDiskDetector, "check_is_usb", classmethod(lambda cls, p: True))

    disk = detector.LocalDiskDetector.read_drive_smart("/dev/sda")

    assert disk.size_bytes == 1024209542656
    assert "DJI Fly Drive" in disk.model
    assert disk.health_status == HealthStatus.HEALTHY
    assert disk.is_usb is True
    assert not disk.error_message
