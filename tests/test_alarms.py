"""The alarm log: parsing the portal report, merging polls, and the events.

The rows below are shaped exactly like what searchAlarmInfo returned for a
Portable Pro+ on 2026-09-25, trimmed to the columns that carry anything.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta, timezone

from onntrack.alarms import (
    ALARM_BACKFILL_START,
    ALARM_OVERLAP,
    alarm_counts,
    alarm_window_start,
    merge_alarms,
    parse_alarm,
    portal_timezone,
)
from onntrack.api import OnntrackApiError
from onntrack.const import EVENT_ALERT
from onntrack.coordinator import OnntrackCoordinator

IMEI = "111222333444555"
GMT2 = timezone(timedelta(hours=2))


def row(alarm_id="a1", push_time="2026-09-25 11:50:42", name="Vibration alert", status="3"):
    return {
        "id": alarm_id,
        "imei": IMEI,
        "pushTime": push_time,
        "createTime": push_time,
        "statusName": name,
        "status": status,
        "lat": 48.33553695678711,
        "gglat": 48.33553695678711,
        "bdlat": 48.34155419055313,
        "lng": 16.454740524291992,
        "gglng": 16.454740524291992,
        "bdlng": 16.461130429252183,
        "speed": "N/A",
        "addr": "",
    }


def alarm(alarm_id, time, kind="Vibration alert"):
    return {"id": alarm_id, "time": time, "type": kind, "code": "3", "latitude": 1.0, "longitude": 2.0}


class TestPortalTimezone:
    def test_fixed_offset(self):
        assert portal_timezone("GMT+02:00") == GMT2

    def test_negative_offset_with_minutes(self):
        assert portal_timezone("GMT-03:30") == timezone(-timedelta(hours=3, minutes=30))

    def test_garbage_falls_back_to_utc(self):
        assert portal_timezone(None) is UTC
        assert portal_timezone("Europe/Vienna") is UTC


class TestParseAlarm:
    def test_real_row(self):
        parsed = parse_alarm(row(), GMT2)
        assert parsed == {
            "id": "a1",
            "time": "2026-09-25T09:50:42+00:00",
            "type": "Vibration alert",
            "code": "3",
            "latitude": 48.33553695678711,
            "longitude": 16.454740524291992,
        }

    def test_winter_time_is_not_shifted(self):
        # The portal stays on GMT+2 in January; local Vienna time is GMT+1 then.
        parsed = parse_alarm(row(push_time="2027-01-10 08:00:00"), GMT2)
        assert parsed["time"] == "2027-01-10T06:00:00+00:00"

    def test_google_coordinates_win_over_baidu(self):
        parsed = parse_alarm(row(), GMT2)
        assert parsed["latitude"] != 48.34155419055313

    def test_zero_coordinates_mean_none(self):
        item = row()
        item.update(lat=0, gglat=0, lng=0, gglng=0)
        parsed = parse_alarm(item, GMT2)
        assert parsed["latitude"] is None and parsed["longitude"] is None

    def test_unusable_rows_are_dropped(self):
        assert parse_alarm(None, GMT2) is None
        assert parse_alarm(row(alarm_id=""), GMT2) is None
        assert parse_alarm(row(push_time="yesterday"), GMT2) is None


class TestMerge:
    def test_overlap_does_not_duplicate(self):
        known = [alarm("b", "2026-09-03T12:00:00+00:00"), alarm("a", "2026-09-01T12:00:00+00:00")]
        fresh = [alarm("c", "2026-09-25T09:50:42+00:00"), alarm("b", "2026-09-03T12:00:00+00:00")]
        merged, added = merge_alarms(known, fresh)
        assert [item["id"] for item in merged] == ["c", "b", "a"]
        assert [item["id"] for item in added] == ["c"]

    def test_counts_are_sorted_by_frequency(self):
        alarms = [
            alarm("1", "t1", "Install alert"),
            alarm("2", "t2"),
            alarm("3", "t3"),
        ]
        assert list(alarm_counts(alarms).items()) == [("Vibration alert", 2), ("Install alert", 1)]


class TestWindow:
    def test_first_sync_is_the_backfill(self):
        assert alarm_window_start([], None) == ALARM_BACKFILL_START

    def test_starts_before_the_newest_alarm(self):
        start = alarm_window_start([alarm("a", "2026-09-25T09:50:42+00:00")], None)
        assert start == datetime(2026, 9, 25, 9, 50, 42, tzinfo=UTC) - ALARM_OVERLAP

    def test_a_quiet_device_is_not_asked_for_the_same_weeks_again(self):
        start = alarm_window_start(
            [alarm("a", "2026-08-01T00:00:00+00:00")], "2026-09-25T10:00:00+00:00"
        )
        assert start == datetime(2026, 9, 25, 10, tzinfo=UTC) - ALARM_OVERLAP


class FakeBus:
    def __init__(self) -> None:
        self.fired = []

    def async_fire(self, event_type, payload):
        self.fired.append((event_type, payload))


class FakeHass:
    def __init__(self) -> None:
        self.bus = FakeBus()


class FakeApi:
    def __init__(self, alarms=None, error=None) -> None:
        self.alarms = alarms or []
        self.error = error
        self.windows = []

    async def async_get_alarms(self, imei, start, end):
        self.windows.append((start, end))
        if self.error:
            raise self.error
        return self.alarms


class FakeStore:
    def __init__(self) -> None:
        self.saves = 0

    def async_delay_save(self, data_func, delay):
        self.saves += 1
        self.saved = data_func()


def update(api, log=None):
    store = FakeStore()
    coordinator = OnntrackCoordinator(FakeHass(), api, alarm_store=store)
    coordinator.alarm_log = log or {}
    data = {"devices": {IMEI: {"device": {"deviceName": "Camper"}, "address": "somewhere"}}}
    asyncio.run(coordinator._async_update_alarms(data))
    return coordinator, data, store


class TestCoordinatorAlarms:
    def test_backfill_is_stored_but_not_announced(self):
        api = FakeApi([alarm("a", "2026-09-25T09:50:42+00:00")])
        coordinator, data, store = update(api)
        assert api.windows[0][0] == ALARM_BACKFILL_START
        assert data["devices"][IMEI]["alarms"][0]["id"] == "a"
        assert coordinator.hass.bus.fired == []
        assert store.saves == 1

    def test_a_new_alarm_fires_one_event(self):
        known = alarm("a", "2026-09-03T12:00:00+00:00")
        api = FakeApi([alarm("b", "2026-09-25T09:50:42+00:00"), known])
        coordinator, _data, store = update(api, {IMEI: {"alarms": [known], "checked_until": None}})
        assert len(coordinator.hass.bus.fired) == 1
        event, payload = coordinator.hass.bus.fired[0]
        assert event == EVENT_ALERT
        assert payload["field"] == "portal_alarm"
        assert payload["value"] == "Vibration alert"
        assert payload["alarm_id"] == "b"
        assert payload["device_name"] == "Camper"
        assert store.saves == 1

    def test_nothing_new_writes_nothing(self):
        known = alarm("a", "2026-09-03T12:00:00+00:00")
        coordinator, _data, store = update(
            FakeApi([known]), {IMEI: {"alarms": [known], "checked_until": None}}
        )
        assert coordinator.hass.bus.fired == []
        assert store.saves == 0

    def test_a_portal_error_keeps_the_log(self):
        known = alarm("a", "2026-09-03T12:00:00+00:00")
        coordinator, data, _store = update(
            FakeApi(error=OnntrackApiError("down")),
            {IMEI: {"alarms": [known], "checked_until": None}},
        )
        assert data["devices"][IMEI]["alarms"] == [known]
        assert coordinator.alarm_log[IMEI]["alarms"] == [known]
