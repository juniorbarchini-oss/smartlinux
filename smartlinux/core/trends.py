"""Trends of the controller view: how a disk's key counters changed in the last 7 and 30 days,
from the local SMART history (core/history_manager.py). Only differences between real readings:
no extrapolation, no "time to failure" (no such number exists).
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

WINDOWS = (7, 30)
NOT_ENOUGH = "Not enough history yet — scan again later"
WORSE, BETTER, SAME = "▲", "▼", "="


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    fields: tuple          # snapshot fields, first one present wins
    unit: str = ""
    tooltip: str = ""


METRICS = (
    Metric("defects", "Defects", ("grown_defects", "reallocated_sector_ct"),
           tooltip="SAS: Grown Defect List · ATA: Reallocated_Sector_Ct"),
    Metric("uncorrected", "Uncorrected errors", ("uncorrected_errors",),
           tooltip="SAS: read + write + verify uncorrected errors · ATA: Reported_Uncorrect"),
    Metric("temperature", "Temperature", ("temperature_c",), unit=" °C", tooltip="temperature_c"),
)


@dataclass
class Change:
    days: int
    delta: Optional[float]          # None: no reading old enough inside the window

    @property
    def text(self) -> str:
        if self.delta is None:
            return "—"
        arrow = WORSE if self.delta > 0 else (BETTER if self.delta < 0 else SAME)
        if self.delta == 0:
            return f"{arrow} no change"
        value = int(self.delta) if float(self.delta).is_integer() else round(self.delta, 1)
        return f"{arrow} {'+' if value > 0 else ''}{value}"


@dataclass
class MetricTrend:
    metric: Metric
    current: Optional[float]
    changes: List[Change]

    @property
    def current_text(self) -> str:
        if self.current is None:
            return "—"
        value = int(self.current) if float(self.current).is_integer() else self.current
        return f"{value}{self.metric.unit}"


@dataclass
class DiskTrend:
    enough: bool
    metrics: List[MetricTrend]
    readings: int


def _when(entry: Dict[str, Any]) -> Optional[datetime]:
    try:
        ts = datetime.fromisoformat(str(entry.get("timestamp")))
    except (TypeError, ValueError):
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _value(entry: Dict[str, Any], metric: Metric) -> Optional[float]:
    for name in metric.fields:
        value = entry.get(name)               # older lines may not have the newer fields
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value
    return None


def disk_trend(history: List[Dict[str, Any]], now: Optional[datetime] = None) -> DiskTrend:
    """Change of each metric between the latest reading and the oldest reading inside each
    window (7 / 30 days). Needs at least two dated readings."""
    now = now or datetime.now(timezone.utc)
    dated = sorted(((t, e) for e in history if (t := _when(e)) is not None), key=lambda x: x[0])
    if len(dated) < 2:
        return DiskTrend(False, [], len(dated))
    latest_time, latest = dated[-1]
    metrics: List[MetricTrend] = []
    for metric in METRICS:
        current = _value(latest, metric)
        changes = []
        for days in WINDOWS:
            start = now - timedelta(days=days)
            window = [(t, e) for t, e in dated[:-1] if t >= start and _value(e, metric) is not None]
            if current is None or not window:
                changes.append(Change(days, None))
            else:
                changes.append(Change(days, current - _value(window[0][1], metric)))
        metrics.append(MetricTrend(metric, current, changes))
    enough = any(c.delta is not None for m in metrics for c in m.changes)
    return DiskTrend(enough, metrics, len(dated))
