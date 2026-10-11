"""Shared wording for the manual scan action (detail panel button + sidebar menu)."""
import time
from datetime import datetime
from typing import Optional

from ..core.models import DiskInfo, HealthStatus

SCAN_NOW_TEXT = "🔍 Scan Now"
REFRESH_TEXT = "↻ Refresh"
NOT_SCANNED_TEXT = "Not scanned yet — click 'Scan Now'"


def has_scan_data(disk: Optional[DiskInfo]) -> bool:
    """True once this drive has been read in this session (even with a poor result)."""
    if disk is None:
        return False
    return bool(disk.attributes) or disk.health_status != HealthStatus.UNKNOWN or disk.last_scanned is not None


def scan_action_text(disk: Optional[DiskInfo]) -> str:
    return REFRESH_TEXT if has_scan_data(disk) else SCAN_NOW_TEXT


def format_last_scanned(ts: Optional[float], now: Optional[float] = None) -> str:
    """'Last scanned: 14:07' today, 'Last scanned: Oct 11 14:07' otherwise."""
    if ts is None:
        return NOT_SCANNED_TEXT
    when = datetime.fromtimestamp(ts)
    today = datetime.fromtimestamp(time.time() if now is None else now).date()
    stamp = when.strftime("%H:%M") if when.date() == today else f"{when.strftime('%b')} {when.day} {when.strftime('%H:%M')}"
    return f"Last scanned: {stamp}"
