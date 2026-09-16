"""The events the coordinator fires when something changes.

Automations hang off these, so a missed or duplicated event is not cosmetic:
onntrack_alert is what would tell you the tracker was pulled out of the vehicle.
"""

from __future__ import annotations

from typing import Any

from onntrack.const import EVENT_ALERT, EVENT_STATUS_CHANGED
from onntrack.coordinator import OnntrackCoordinator

IMEI = "111222333444555"


class FakeBus:
    def __init__(self) -> None:
        self.fired: list[tuple[str, dict[str, Any]]] = []

    def async_fire(self, event_type: str, payload: dict[str, Any]) -> None:
        self.fired.append((event_type, payload))


class FakeHass:
    def __init__(self) -> None:
        self.bus = FakeBus()


def record(status="STATIC", alerts=None, name="Camper"):
    return {
        "imei": IMEI,
        "device": {"deviceName": name},
        "status": status,
        "address": "somewhere",
        "latitude": 48.2,
        "longitude": 16.37,
        "positioning_time": "2026-09-16 08:00:00",
        "alert_properties": alerts or {},
    }


def coordinator(previous):
    hass = FakeHass()
    instance = OnntrackCoordinator(hass, api=None)
    instance.data = previous
    return instance


def fire(previous, current):
    instance = coordinator(previous)
    instance._fire_change_events(current)
    return instance.hass.bus.fired


class TestFirstRun:
    def test_nothing_fires_without_a_previous_state(self):
        # Otherwise every restart would announce every alert again.
        assert fire(None, {"devices": {IMEI: record(alerts={"sosAlarm": "1"})}}) == []

    def test_a_device_seen_for_the_first_time_is_quiet(self):
        assert fire({"devices": {}}, {"devices": {IMEI: record()}}) == []


class TestStatus:
    def test_a_change_is_announced(self):
        fired = fire(
            {"devices": {IMEI: record(status="STATIC")}},
            {"devices": {IMEI: record(status="MOVING")}},
        )
        assert len(fired) == 1
        event, payload = fired[0]
        assert event == EVENT_STATUS_CHANGED
        assert payload["previous_status"] == "STATIC"
        assert payload["status"] == "MOVING"
        assert payload["device_name"] == "Camper"

    def test_an_unchanged_status_stays_quiet(self):
        assert fire(
            {"devices": {IMEI: record(status="STATIC")}},
            {"devices": {IMEI: record(status="STATIC")}},
        ) == []


class TestAlerts:
    def test_an_alert_going_active_fires(self):
        fired = fire(
            {"devices": {IMEI: record(alerts={"sosAlarm": "0"})}},
            {"devices": {IMEI: record(alerts={"sosAlarm": "1"})}},
        )
        assert [event for event, _ in fired] == [EVENT_ALERT]
        payload = fired[0][1]
        assert payload["field"] == "sosAlarm"
        assert payload["value"] == "1"
        assert payload["previous_value"] == "0"
        assert payload["latitude"] == 48.2

    def test_an_alert_clearing_does_not_fire(self):
        # Only the transition into an alert is worth waking somebody for.
        assert fire(
            {"devices": {IMEI: record(alerts={"sosAlarm": "1"})}},
            {"devices": {IMEI: record(alerts={"sosAlarm": "0"})}},
        ) == []

    def test_a_standing_alert_does_not_repeat(self):
        assert fire(
            {"devices": {IMEI: record(alerts={"sosAlarm": "1"})}},
            {"devices": {IMEI: record(alerts={"sosAlarm": "1"})}},
        ) == []

    def test_each_alert_is_reported_separately(self):
        fired = fire(
            {"devices": {IMEI: record(alerts={"sosAlarm": "0", "tamperAlarm": "0"})}},
            {"devices": {IMEI: record(alerts={"sosAlarm": "1", "tamperAlarm": "1"})}},
        )
        assert len(fired) == 2
        assert {payload["field"] for _, payload in fired} == {"sosAlarm", "tamperAlarm"}


class TestDisappearance:
    def test_a_vanished_device_raises_an_alert(self):
        fired = fire({"devices": {IMEI: record()}}, {"devices": {}})
        assert len(fired) == 1
        event, payload = fired[0]
        assert event == EVENT_ALERT
        assert payload["field"] == "device_removed"
        assert payload["value"] is True
        assert payload["imei"] == IMEI
        # The last known position travels with it -- that is the whole point
        # when a tracker stops answering.
        assert payload["latitude"] == 48.2
        assert payload["address"] == "somewhere"

    def test_the_device_name_falls_back_to_the_imei(self):
        gone = record()
        gone["device"] = {}
        fired = fire({"devices": {IMEI: gone}}, {"devices": {}})
        assert fired[0][1]["device_name"] == IMEI
