"""The alarm log the Onntrack app pushes to the phone.

Vibration, install and removal alerts never show up in getMonitorInfo, which is
all the coordinator used to read, so the integration missed every one of them.
The portal keeps them in a separate report (newReportAlarm/searchAlarmInfo),
reaching back to the day the tracker was activated. This module turns that
report into a log that survives restarts and only ever grows.

Nothing here touches Home Assistant, so it can be tested on its own.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from typing import Any

# The portal has no notion of a device's first day, so the first sync simply
# asks for everything since a date before any of these trackers were sold.
ALARM_BACKFILL_START = datetime(2020, 1, 1, tzinfo=UTC)
# An alarm can reach the report a little after it happened, so each poll
# looks back this far past the newest alarm already known.
ALARM_OVERLAP = timedelta(hours=1)
# Upper bound for the stored log per device. A tracker on a vehicle raises a
# few hundred alarms a quarter; this is years of them.
ALARM_LOG_LIMIT = 5000
# How many alarms the sensor carries as an attribute. The full log is
# available through the get_alarms action.
RECENT_ALARM_COUNT = 50


def portal_timezone(value: Any) -> tzinfo:
    """Turn the account's ``timeZones`` value ("GMT+02:00") into a tzinfo.

    The portal stores a fixed offset, not a region: it does not switch to
    winter time. Reading its timestamps as Home Assistant local time would put
    every alarm from October to March an hour off.
    """
    match = re.fullmatch(r"\s*(?:GMT|UTC)?\s*([+-])(\d{1,2}):?(\d{2})?\s*", str(value or ""))
    if not match:
        return UTC
    sign = -1 if match.group(1) == "-" else 1
    offset = timedelta(hours=int(match.group(2)), minutes=int(match.group(3) or 0))
    return timezone(sign * offset)


def _coordinate(item: dict[str, Any], *names: str) -> float | None:
    for name in names:
        try:
            value = float(item.get(name))
        except (TypeError, ValueError):
            continue
        if value:
            return value
    return None


def parse_alarm(item: Any, tz: tzinfo) -> dict[str, Any] | None:
    """Reduce one report row to what is worth keeping.

    Most columns are empty for this device class (speed "N/A", no address, no
    driver), and bdlat/bdlng are Baidu's shifted coordinates. What is left is
    the id, the time, the kind of alarm and where it happened.
    """
    if not isinstance(item, dict):
        return None
    alarm_id = str(item.get("id") or "").strip()
    raw_time = str(item.get("pushTime") or item.get("createTime") or "").strip()
    if not alarm_id or not raw_time:
        return None
    try:
        local = datetime.strptime(raw_time[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return {
        "id": alarm_id,
        "time": local.replace(tzinfo=tz).astimezone(UTC).isoformat(),
        "type": str(item.get("statusName") or item.get("typeName") or "Unknown alarm"),
        "code": str(item.get("status") or ""),
        "latitude": _coordinate(item, "gglat", "lat"),
        "longitude": _coordinate(item, "gglng", "lng"),
    }


def merge_alarms(
    known: list[dict[str, Any]], fresh: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Add ``fresh`` to ``known`` and return the merged log and what was new.

    The poll window overlaps the previous one on purpose, so most of ``fresh``
    is usually known already; the id decides. Both lists are newest first.
    """
    seen = {alarm["id"] for alarm in known}
    added = []
    for alarm in fresh:
        if alarm["id"] not in seen:
            seen.add(alarm["id"])
            added.append(alarm)
    added.sort(key=lambda alarm: alarm["time"], reverse=True)
    merged = sorted([*added, *known], key=lambda alarm: alarm["time"], reverse=True)
    return merged[:ALARM_LOG_LIMIT], added


def alarm_window_start(alarms: list[dict[str, Any]], checked_until: str | None) -> datetime:
    """Where the next poll has to start so that nothing slips through.

    Anchored on the last successful query rather than only on the newest
    alarm: a device that raises no alarm for weeks would otherwise be asked
    for those same weeks on every poll.
    """
    anchors = []
    for value in (checked_until, alarms[0]["time"] if alarms else None):
        if value:
            try:
                anchors.append(datetime.fromisoformat(value))
            except ValueError:
                continue
    if not anchors:
        return ALARM_BACKFILL_START
    return max(anchors) - ALARM_OVERLAP


def alarm_counts(alarms: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for alarm in alarms:
        counts[alarm["type"]] = counts.get(alarm["type"], 0) + 1
    return dict(sorted(counts.items(), key=lambda item: item[1], reverse=True))
