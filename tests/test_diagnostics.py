"""Diagnostics must not hand out anything that identifies device or account.

The fixture below mirrors the real shape of the portal's payload, including the
display structure where the interesting values sit in generic ``value`` fields.
An earlier version of this test used a flat, tidy record and passed while the
real thing leaked the IMEI six times over -- hence the awkward nesting here.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, ClassVar

from onntrack.diagnostics import (
    async_get_config_entry_diagnostics,
    describe_shape,
    scrub,
    sensitive_values,
)

IMEI = "111222333444555"
TOKEN = "s3cret-route-token-that-must-not-leak"
PASSWORD = "hunter2-not-in-the-report"
ACCOUNT = "owner@example.com"
VEHICLE = "X-99999Z"
DEVICE_NAME = f"Camper {VEHICLE}"
ADDRESS = "Beispielgasse 1, Musterdorf, Austria"
LAT, LON = 48.33549880981445, 16.454675674438477

DEVICES = {
    IMEI: {
        "imei": IMEI,
        "latitude": LAT,
        "longitude": LON,
        "address": ADDRESS,
        "battery": 30.0,
        "status": "parked",
        "speed": 0.0,
        "mileage": 1234.5,
        "alert_count": 0,
        "alert_properties": {"lowBattery": False, "tow": 0},
        "device": {
            "deviceName": DEVICE_NAME,
            "bindUserAccount": ACCOUNT,
            "lastLat": LAT,
            "lastLng": LON,
            "equipmentDetail": {"imei": IMEI, "vehicleNumber": VEHICLE},
        },
        "device_properties": {
            "bindUserAccount": ACCOUNT,
            "lastLat": LAT,
            "equipmentDetail": {"imei": IMEI, "vehicleNumber": VEHICLE},
        },
        "monitor_properties": {
            "1": {"monitorBaseVOS": [{"value": DEVICE_NAME}, {"value": IMEI}]},
            "3": {"monitorBaseVOS": [{"value": ADDRESS}, {"value": [{"value": f"{LAT},{LON}"}]}]},
            "values": {"imei": IMEI, "address": ADDRESS, "latlng": [{"value": f"{LAT},{LON}"}]},
        },
    }
}

SECRETS = (IMEI, TOKEN, PASSWORD, ACCOUNT, VEHICLE, DEVICE_NAME, ADDRESS, "48.3354", "16.4546")


class FakeEntry:
    entry_id = "01ENTRY"
    data: ClassVar[dict[str, Any]] = {
        "base_url": "https://platform.onntrack.nl",
        "username": ACCOUNT,
        "password": PASSWORD,
        "route_token": TOKEN,
    }
    options: ClassVar[dict[str, Any]] = {"scan_interval": 120, "reverse_geocode": False}


class FakeApi:
    reverse_geocode = False


class FakeCoordinator:
    last_update_success = True
    update_interval = None
    api = FakeApi()
    data: ClassVar[dict[str, Any]] = {"user_id": 4711, "devices": DEVICES}


class FakeHass:
    data: ClassVar[dict[str, Any]] = {"onntrack": {FakeEntry.entry_id: FakeCoordinator()}}


def collect() -> dict[str, Any]:
    return asyncio.run(async_get_config_entry_diagnostics(FakeHass(), FakeEntry()))


class TestNothingLeaks:
    def test_no_secret_survives_anywhere(self):
        dumped = json.dumps(collect())
        for secret in SECRETS:
            assert secret not in dumped, f"{secret!r} leaked into the diagnostics"

    def test_the_imei_key_is_dropped_not_redacted(self):
        # The device map is keyed by IMEI and redaction helpers only touch
        # values, so the keys have to go entirely.
        assert IMEI not in json.dumps(collect()["devices"])

    def test_raw_payloads_are_reduced_to_their_shape(self):
        device = collect()["devices"][0]
        shape = device["device_shape"]
        # Field names survive -- that is what tells a maintainer what the portal
        # actually sent -- but the values do not.
        assert "bindUserAccount" in shape
        assert shape["bindUserAccount"] == "str"
        assert shape["equipmentDetail"]["vehicleNumber"] == "str"


class TestWhatRemainsIsUseful:
    def test_the_numbers_a_report_is_about(self):
        device = collect()["devices"][0]
        assert device["battery"] == 30.0
        assert device["status"] == "parked"
        assert device["mileage"] == 1234.5
        assert device["alert_properties"] == {"lowBattery": False, "tow": False}

    def test_presence_without_the_value(self):
        device = collect()["devices"][0]
        assert device["has_address"] is True
        assert device["has_position"] is True

    def test_entry_and_coordinator_state(self):
        report = collect()
        assert report["device_count"] == 1
        assert report["entry"]["data"]["base_url"] == "https://platform.onntrack.nl"
        assert report["entry"]["options"] == {"scan_interval": 120, "reverse_geocode": False}
        assert report["coordinator"]["last_update_success"] is True
        assert report["coordinator"]["reverse_geocode"] is False


class TestHelpers:
    def test_shape_keeps_keys_and_types_only(self):
        assert describe_shape({"a": 1, "b": "x"}) == {"a": "int", "b": "str"}
        assert describe_shape([{"v": 1}, {"v": 2}]) == [{"v": "int"}, "... 2 items"]
        assert describe_shape({}) == {}

    def test_shape_stops_descending(self):
        deep = {"a": {"b": {"c": {"d": {"e": {"f": 1}}}}}}
        assert "dict[" in json.dumps(describe_shape(deep))

    def test_sensitive_values_finds_the_obvious(self):
        found = sensitive_values(dict(FakeEntry.data), {"devices": DEVICES})
        assert IMEI in found
        assert ACCOUNT in found
        assert DEVICE_NAME in found
        assert ADDRESS in found
        assert any(value.startswith("48.3355") for value in found)

    def test_short_values_are_not_used_as_secrets(self):
        # A three-character value would redact half the report.
        found = sensitive_values({"username": "abc"}, {"devices": {}})
        assert "abc" not in found

    def test_scrub_leaves_booleans_and_none_alone(self):
        assert scrub({"a": True, "b": None, "c": "x"}, {"zzzz"}) == {"a": True, "b": None, "c": "x"}
