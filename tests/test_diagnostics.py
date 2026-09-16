"""Diagnostics must not hand out anything that identifies device or account.

Downloaded diagnostics get pasted into public issue reports. An IMEI in there
is enough to bind the device on portals of this kind, and the coordinates are
someone's home address.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, ClassVar

from onntrack.diagnostics import (
    TO_REDACT,
    _redact_devices,
    async_get_config_entry_diagnostics,
)

IMEI = "865282041088616"
SECRET = "QFDjAlfqR3NJ3cSw0x4sUC67masZyNLGqpkZQnq40TM"

DEVICES = {
    IMEI: {
        "imei": IMEI,
        "device": {"deviceName": "Camper W-45437S", "mcType": "Portable Pro+"},
        "latitude": 48.33552,
        "longitude": 16.45471,
        "address": "Gewerbestrasse, Hagenbrunn, Austria",
        "battery": 30.0,
        "status": "parked",
    }
}


class FakeEntry:
    entry_id = "01M1P5WKX18WCNY42216Y1D56P"
    data: ClassVar[dict[str, Any]] = {
        "base_url": "https://platform.onntrack.nl",
        "username": "someone@example.com",
        "password": "hunter2",
        "route_token": SECRET,
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


def collect():
    return asyncio.run(async_get_config_entry_diagnostics(FakeHass(), FakeEntry()))


class TestRedaction:
    def test_nothing_identifying_survives(self):
        dumped = json.dumps(collect())
        for secret in (IMEI, SECRET, "hunter2", "someone@example.com", "Camper W-45437S"):
            assert secret not in dumped, f"{secret!r} leaked into the diagnostics"

    def test_coordinates_are_gone(self):
        dumped = json.dumps(collect())
        assert "48.33552" not in dumped
        assert "16.45471" not in dumped
        assert "Gewerbestrasse" not in dumped

    def test_the_imei_key_itself_is_dropped(self):
        # The device map is keyed by IMEI, and redaction helpers only touch
        # values -- so the keys have to be thrown away, not redacted.
        assert IMEI not in json.dumps(_redact_devices(DEVICES))

    def test_what_is_useful_stays(self):
        report = collect()
        assert report["device_count"] == 1
        assert report["entry"]["options"] == {"scan_interval": 120, "reverse_geocode": False}
        assert report["entry"]["data"]["base_url"] == "https://platform.onntrack.nl"
        assert report["coordinator"]["last_update_success"] is True
        assert report["devices"][0]["battery"] == 30.0
        assert report["devices"][0]["status"] == "parked"

    def test_the_redaction_list_covers_the_obvious(self):
        for key in ("password", "route_token", "username", "imei", "latitude", "longitude"):
            assert key in TO_REDACT
