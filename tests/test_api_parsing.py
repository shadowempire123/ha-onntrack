"""The layer that guesses the portal's field names.

This is the most fragile part of the integration: the API is undocumented, the
field names differ between devices, and when one is renamed a sensor quietly
goes empty without anything appearing in the log. Hence the detail here.
"""

from __future__ import annotations

import pytest
from onntrack.api import (
    _alert_properties,
    _all_key_values,
    _coordinates,
    _extract_devices,
    _extract_route_points,
    _first_value,
    _is_active_alert_value,
    _lookup,
    _nested_data,
    _numeric,
    _response_message,
    _response_ok,
    _scalar_properties,
    parse_monitor_data,
)


def kv(*pairs):
    """Build the key/value structure the portal wraps its values in."""
    return {"monitorBaseVOS": [{"key": key, "value": value} for key, value in pairs]}


class TestNumeric:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (42, 42.0),
            ("42", 42.0),
            ("30%", 30.0),
            ("12.5 km/h", 12.5),
            ("-3.5", -3.5),
            ("speed: 88", 88.0),
            (7.5, 7.5),
        ],
    )
    def test_numbers_are_dug_out_of_text(self, value, expected):
        assert _numeric(value) == expected

    @pytest.mark.parametrize("value", [None, "", "N/A", "unknown", True, False])
    def test_non_numbers_give_none(self, value):
        # Booleans especially: True would otherwise arrive as 1.0 and turn into
        # a battery level.
        assert _numeric(value) is None


class TestKeyValues:
    def test_pairs_are_collected_and_folded(self):
        assert _all_key_values(kv(("Battery", "30%"), ("Speed", "0"))) == {
            "battery": "30%",
            "speed": "0",
        }

    def test_nesting_does_not_hide_pairs(self):
        payload = {"a": {"b": [kv(("Status", "STATIC"))]}}
        assert _all_key_values(payload) == {"status": "STATIC"}

    def test_keys_are_stripped(self):
        assert _all_key_values(kv((" Battery ", 30))) == {"battery": 30}

    def test_nothing_to_collect(self):
        assert _all_key_values({"plain": "value"}) == {}


class TestLookupOrder:
    def test_the_first_name_present_wins(self):
        values = {"gpsspeed": "80", "speed": "12"}
        assert _lookup(values, ("speed", "gpsspeed")) == "12"
        assert _lookup(values, ("gpsspeed", "speed")) == "80"

    def test_order_is_a_sequence_not_a_set(self):
        # A set would make the winner depend on string hashing, which Python
        # randomises per process: the same device would report a different
        # value after a restart.
        values = {"a": 1, "b": 2}
        for _ in range(50):
            assert _lookup(values, ("b", "a")) == 2

    def test_empty_values_are_skipped(self):
        assert _lookup({"speed": "", "gpsspeed": "80"}, ("speed", "gpsspeed")) == "80"
        assert _lookup({"speed": None, "gpsspeed": "80"}, ("speed", "gpsspeed")) == "80"

    def test_nothing_matches(self):
        assert _lookup({"speed": "12"}, ("battery",)) is None


class TestCoordinates:
    def test_separate_fields(self):
        assert _coordinates({}, {"latitude": "48.2", "longitude": "16.37"}) == (48.2, 16.37)

    def test_alternative_names(self):
        assert _coordinates({}, {"lastlat": 48.2, "lastlng": 16.37}) == (48.2, 16.37)

    def test_a_combined_pair(self):
        assert _coordinates({}, {"lastlatlng": "48.2,16.37"}) == (48.2, 16.37)

    def test_separate_fields_beat_the_combined_one(self):
        found = _coordinates({}, {"latitude": "48.9", "lastlatlng": "48.2,16.37"})
        assert found == (48.9, 16.37)

    def test_nothing_usable(self):
        assert _coordinates({}, {}) == (None, None)
        assert _coordinates({}, {"lastlatlng": "not,coordinates"}) == (None, None)


class TestAlerts:
    def test_only_alert_shaped_keys_are_picked(self):
        properties = {
            "sosAlarm": "1",
            "vibrationAlert": "0",
            "tamperState": True,
            "battery": "30",
            "speed": "0",
        }
        assert set(_alert_properties(properties)) == {"sosAlarm", "vibrationAlert", "tamperState"}

    @pytest.mark.parametrize("value", [None, "", False, 0, "0", "OK", "normal", "none", " Off "])
    def test_quiet_values_are_not_alerts(self, value):
        assert _is_active_alert_value(value) is False

    @pytest.mark.parametrize("value", ["1", 1, True, "triggered", "ALARM"])
    def test_anything_else_counts(self, value):
        assert _is_active_alert_value(value) is True


class TestResponseShape:
    def test_data_is_unwrapped(self):
        assert _nested_data({"data": {"x": 1}}) == {"x": 1}
        assert _nested_data({"x": 1}) == {"x": 1}

    @pytest.mark.parametrize(
        "payload", [{"code": 0}, {"code": "200"}, {"ok": True}, {"code": 10000}, "plain"]
    )
    def test_accepted(self, payload):
        assert _response_ok(payload) is True

    @pytest.mark.parametrize("payload", [{"code": 500}, {"code": "1"}, {"ok": False}])
    def test_rejected(self, payload):
        assert _response_ok(payload) is False

    def test_the_message_is_found_either_way(self):
        assert _response_message({"msg": "nope"}) == "nope"
        assert _response_message({"message": "nope"}) == "nope"
        assert _response_message({}) == ""

    def test_devices_are_found_under_any_of_the_usual_keys(self):
        for key in ("result", "list", "rows", "records"):
            assert _extract_devices({"data": {key: [{"imei": "1"}]}}) == [{"imei": "1"}]
        assert _extract_devices({"data": [{"imei": "1"}]}) == [{"imei": "1"}]
        assert _extract_devices({"data": {}}) == []

    def test_route_points_likewise(self):
        assert _extract_route_points({"data": {"gpsPointStrList": ["a"]}}) == ["a"]
        assert _extract_route_points({"data": ["a"]}) == ["a"]
        assert _extract_route_points({"data": {}}) == []

    def test_scalar_properties_flatten_with_a_path(self):
        assert _scalar_properties({"a": {"b": 1}, "c": [2, 3]}) == {"a.b": 1, "c.0": 2, "c.1": 3}

    def test_first_value_searches_depth_first(self):
        assert _first_value({"outer": {"id": 7}}, {"id"}) == 7
        assert _first_value({"outer": {"nope": 1}}, {"id"}) is None


class TestParseMonitorData:
    def monitor(self, *pairs):
        return {"data": kv(*pairs)}

    def test_a_parked_vehicle_reports_zero_speed(self):
        record = parse_monitor_data(self.monitor(("Status", "STATIC"), ("Speed", "7")), {})
        assert record["parked"] is True
        assert record["speed"] == 0.0
        # The raw value is kept, so nobody has to wonder where it went.
        assert record["reported_speed"] == 7.0

    @pytest.mark.parametrize("status", ["STATIC", "Parked", "ACC: OFF", "stopped"])
    def test_the_words_that_mean_parked(self, status):
        assert parse_monitor_data(self.monitor(("Status", status)), {})["parked"] is True

    def test_a_moving_vehicle_keeps_its_speed(self):
        record = parse_monitor_data(self.monitor(("Status", "MOVING"), ("Speed", "63")), {})
        assert record["parked"] is False
        assert record["speed"] == 63.0

    def test_the_device_record_fills_the_gaps(self):
        record = parse_monitor_data(
            self.monitor(("Status", "MOVING")),
            {"lastLat": "48.2", "lastLng": "16.37", "totalMileage": "1000"},
        )
        assert (record["latitude"], record["longitude"]) == (48.2, 16.37)
        assert record["mileage"] == 1000.0

    def test_the_monitor_beats_the_device_record(self):
        record = parse_monitor_data(
            self.monitor(("Latitude", "49.0"), ("Longitude", "17.0")),
            {"lastLat": "48.2", "lastLng": "16.37"},
        )
        assert (record["latitude"], record["longitude"]) == (49.0, 17.0)

    def test_the_fields_the_sensors_read(self):
        record = parse_monitor_data(
            self.monitor(
                ("Battery", "30%"),
                ("Status", "STATIC"),
                ("TotalMileage", "7708.62"),
                ("TodayMileage", "0"),
                ("Address", "Beispielgasse 1"),
                ("PositioningTime", "2026-09-16 08:00:00"),
                ("LastOnlineTime", "2026-09-16 08:01:00"),
                ("GNSS", "GPS"),
                ("VisibleSatellites", "9"),
                ("CSQ", "24"),
            ),
            {},
        )
        assert record["battery"] == 30.0
        assert record["mileage"] == 7708.62
        assert record["today_mileage"] == 0.0
        assert record["address"] == "Beispielgasse 1"
        assert record["last_fix"] == "2026-09-16 08:00:00"
        assert record["last_online"] == "2026-09-16 08:01:00"
        assert record["gnss"] == "GPS"
        assert record["visible_satellites"] == 9.0
        assert record["cellular_signal"] == "24"

    def test_alerts_are_counted_not_just_listed(self):
        record = parse_monitor_data(
            self.monitor(("Status", "STATIC"), ("sosAlarm", "1"), ("vibrationAlarm", "0")),
            {},
        )
        assert record["alert_count"] == 1
        assert set(record["alert_properties"]) >= {"values.sosalarm", "values.vibrationalarm"}

    def test_an_empty_answer_does_not_raise(self):
        record = parse_monitor_data({}, {})
        assert record["battery"] is None
        assert record["latitude"] is None
        assert record["alert_count"] == 0
        assert record["parked"] is False
