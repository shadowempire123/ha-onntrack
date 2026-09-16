from __future__ import annotations

import asyncio
import hashlib
import re
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

import aiohttp

from .const import VERSION

API_PREFIX = "/v3/new"
REFERER_PATH = "/resource/dev/index.html"

NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
# Nominatim requires a User-Agent that identifies the application and
# carries a contact address.
NOMINATIM_USER_AGENT = (
    f"HomeAssistant-Onntrack/{VERSION} (+https://github.com/shadowempire123/ha-onntrack)"
)
# At most one request per second for the whole application, as required by
# the Nominatim usage policy.
NOMINATIM_MIN_INTERVAL = 1.1
# Spacing between two lookups the cache cannot answer. Without it every trip
# produces one request per minute, because each poll reports fresh
# coordinates -- exactly the sustained load the policy forbids.
NOMINATIM_LOOKUP_INTERVAL = 120.0
# 403/429 is Nominatim asking the client to back off. Wait an hour.
NOMINATIM_BLOCK_INTERVAL = 3600.0
NOMINATIM_CACHE_LIMIT = 500

_NOMINATIM_LOCK = asyncio.Lock()
_NOMINATIM_LAST_REQUEST = 0.0


async def _nominatim_slot() -> None:
    """Keep every Nominatim request of this process a second apart."""
    global _NOMINATIM_LAST_REQUEST
    async with _NOMINATIM_LOCK:
        delay = NOMINATIM_MIN_INTERVAL - (time.monotonic() - _NOMINATIM_LAST_REQUEST)
        if delay > 0:
            await asyncio.sleep(delay)
        _NOMINATIM_LAST_REQUEST = time.monotonic()


class OnntrackApiError(RuntimeError):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class OnntrackAuthError(OnntrackApiError):
    pass


def _nested_data(response: Any) -> Any:
    if isinstance(response, dict) and "data" in response:
        return response["data"]
    return response


def _response_ok(response: Any) -> bool:
    if not isinstance(response, dict):
        return True
    if response.get("ok") in (True, 1, "true"):
        return True
    return response.get("code") in (0, "0", 200, "200", 10000, "10000")


def _response_message(response: Any) -> str:
    if not isinstance(response, dict):
        return ""
    return str(response.get("msg") or response.get("message") or "")


def _first_value(value: Any, names: set[str]) -> Any:
    if isinstance(value, dict):
        for name, candidate in value.items():
            if name.lower() in names and candidate not in (None, ""):
                return candidate
        for candidate in value.values():
            found = _first_value(candidate, names)
            if found not in (None, ""):
                return found
    elif isinstance(value, list):
        for candidate in value:
            found = _first_value(candidate, names)
            if found not in (None, ""):
                return found
    return None


def _all_key_values(value: Any, result: dict[str, Any] | None = None) -> dict[str, Any]:
    result = {} if result is None else result
    if isinstance(value, dict):
        if "key" in value and "value" in value:
            result[str(value["key"]).strip().lower()] = value["value"]
        for candidate in value.values():
            _all_key_values(candidate, result)
    elif isinstance(value, list):
        for candidate in value:
            _all_key_values(candidate, result)
    return result


def _lookup(key_values: dict[str, Any], names: set[str]) -> Any:
    for name in names:
        candidate = key_values.get(name)
        if candidate not in (None, ""):
            return candidate
    return None


def _numeric(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    match = re.search(r"[-+]?\d+(?:\.\d+)?", str(value))
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _coordinates(value: Any, key_values: dict[str, Any]) -> tuple[float | None, float | None]:
    latitude = _numeric(_lookup(key_values, {"latitude", "lat", "lastlat"}))
    longitude = _numeric(_lookup(key_values, {"longitude", "lng", "lon", "lastlng"}))
    combined = _lookup(key_values, {"source_latlng", "sourcelatlng", "lastlatlng"})
    if isinstance(combined, str):
        parts = [part.strip() for part in combined.split(",", 1)]
        if len(parts) == 2:
            latitude = latitude if latitude is not None else _numeric(parts[0])
            longitude = longitude if longitude is not None else _numeric(parts[1])
    return latitude, longitude


def _extract_devices(response: Any) -> list[dict[str, Any]]:
    data = _nested_data(response)
    if isinstance(data, list):
        candidates = data
    elif isinstance(data, dict):
        candidates = data.get("result") or data.get("list") or data.get("rows") or data.get("records") or []
    else:
        candidates = []
    return [item for item in candidates if isinstance(item, dict)]


def _extract_route_points(response: Any) -> list[Any]:
    data = _nested_data(response)
    if isinstance(data, dict):
        for key in ("gpsPointStrList", "pointList", "points", "result", "list"):
            candidate = data.get(key)
            if isinstance(candidate, list):
                return candidate
    return data if isinstance(data, list) else []


def _scalar_properties(value: Any, prefix: str = "", result: dict[str, Any] | None = None) -> dict[str, Any]:
    result = {} if result is None else result
    if isinstance(value, dict):
        for key, candidate in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            _scalar_properties(candidate, path, result)
    elif isinstance(value, list):
        for index, candidate in enumerate(value):
            _scalar_properties(candidate, f"{prefix}.{index}", result)
    elif value is None or isinstance(value, (str, int, float, bool)):
        result[prefix] = value
    return result


def _alert_properties(properties: dict[str, Any]) -> dict[str, Any]:
    terms = ("alarm", "alert", "fall", "vibrat", "remove", "tamper", "sos", "shock")
    return {
        key: value
        for key, value in properties.items()
        if any(term in key.casefold() for term in terms)
    }


def _is_active_alert_value(value: Any) -> bool:
    if value in (None, "", False, 0, "0"):
        return False
    if isinstance(value, str):
        return value.strip().casefold() not in {
            "clear", "cleared", "false", "inactive", "no", "none", "normal", "off", "ok"
        }
    return True


def parse_monitor_data(monitor_response: Any, device: dict[str, Any]) -> dict[str, Any]:
    monitor_data = _nested_data(monitor_response)
    key_values = _all_key_values(monitor_data)
    monitor_properties = _scalar_properties(monitor_data)
    monitor_properties.update({f"values.{key}": value for key, value in key_values.items()})
    latitude, longitude = _coordinates(monitor_data, key_values)
    alert_properties = _alert_properties(monitor_properties)
    status = _lookup(key_values, {"status", "devicestatus", "onlinestatus", "state"}) or device.get("status")
    reported_speed = _numeric(_lookup(key_values, {"speed", "gpsspeed", "vehiclespeed"}))
    parked = any(
        term in str(status or "").casefold()
        for term in ("static", "parked", "acc: off", "acc off", "stopped")
    )
    return {
        "latitude": latitude if latitude is not None else _numeric(device.get("lastLat")),
        "longitude": longitude if longitude is not None else _numeric(device.get("lastLng")),
        "battery": _numeric(_lookup(key_values, {"battery", "batterylevel", "batterypercent", "electricity", "power"})),
        "status": status,
        "speed": 0.0 if parked else reported_speed,
        "reported_speed": reported_speed,
        "parked": parked,
        "mileage": _numeric(_lookup(key_values, {"totalmileage", "mileage", "mileagevalue"})) or _numeric(device.get("totalMileage")),
        "today_mileage": _numeric(_lookup(key_values, {"todaymileage", "dailymileage", "todaydistance", "daymileage"})),
        "address": _lookup(key_values, {"address", "locationaddress"}),
        "last_fix": _lookup(key_values, {"positioningtime", "gpstime", "fixtime", "lastfixtime"}),
        "last_online": _lookup(key_values, {"lastonlinetime", "lastonline", "onlinetime", "lastcontacttime", "lastupdatetime"}),
        "gnss": _lookup(key_values, {"gnss", "positioningtype", "positiontype", "loctype", "gpsstatus"}),
        "visible_satellites": _numeric(_lookup(key_values, {"visiblesatellites", "satellitecount", "satellites", "satnum", "gpsnum"})),
        "cellular_signal": _lookup(key_values, {"cellularsignalstrength", "cellsignal", "cellstrength", "gsm_signal", "signalstrength", "csq", "rsrp"}),
        "positioning_time": _lookup(key_values, {"positioningtime", "gpstime", "fixtime", "lastfixtime"}),
        "device_properties": _scalar_properties(device),
        "monitor_properties": monitor_properties,
        "alert_properties": alert_properties,
        "alert_count": sum(_is_active_alert_value(value) for value in alert_properties.values()),
    }


class OnntrackApi:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        username: str,
        password: str,
        *,
        address_cache: dict[str, str] | None = None,
        cache_changed: Callable[[], None] | None = None,
        reverse_geocode: bool = True,
    ) -> None:
        self._session = session
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self._token: str | None = None
        # Filled and persisted from the outside so that resolved addresses
        # survive a restart.
        self._address_cache: dict[str, str] = {} if address_cache is None else address_cache
        self._cache_changed = cache_changed
        # Sending coordinates to Nominatim is optional; some installations do
        # not want them leaving the network at all.
        self.reverse_geocode = reverse_geocode
        self._last_address: dict[str, str] = {}
        self._next_lookup = 0.0
        self._blocked_until = 0.0

    async def _async_reverse_geocode(self, latitude: float, longitude: float) -> str | None:
        if not self.reverse_geocode:
            return None
        cache_key = f"{latitude:.4f},{longitude:.4f}"
        if cache_key in self._address_cache:
            return self._address_cache[cache_key]
        now = time.monotonic()
        if now < self._blocked_until or now < self._next_lookup:
            return None
        self._next_lookup = now + NOMINATIM_LOOKUP_INTERVAL
        await _nominatim_slot()
        try:
            async with self._session.get(
                NOMINATIM_URL,
                params={"format": "jsonv2", "lat": latitude, "lon": longitude, "zoom": 18},
                headers={"User-Agent": NOMINATIM_USER_AGENT},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status in (403, 429):
                    self._blocked_until = time.monotonic() + NOMINATIM_BLOCK_INTERVAL
                    return None
                data = await response.json(content_type=None) if response.status == 200 else {}
        except (TimeoutError, aiohttp.ClientError, TypeError, ValueError):
            return None
        address = data.get("display_name") if isinstance(data, dict) else None
        if not address:
            return None
        if len(self._address_cache) >= NOMINATIM_CACHE_LIMIT:
            self._address_cache.pop(next(iter(self._address_cache)))
        self._address_cache[cache_key] = str(address)
        if self._cache_changed is not None:
            self._cache_changed()
        return str(address)

    async def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        url = self.base_url + API_PREFIX + path
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": self.base_url,
            "Referer": self.base_url + REFERER_PATH,
            "User-Agent": f"home-assistant-onntrack/{VERSION}",
        }
        if self._token:
            headers["Authorization"] = self._token
        try:
            async with self._session.request(method, url, json=payload, headers=headers) as response:
                try:
                    data = await response.json(content_type=None)
                except (TypeError, ValueError, aiohttp.ContentTypeError) as error:
                    text = await response.text()
                    raise OnntrackApiError(
                        f"No JSON response from {method} {path}: HTTP {response.status} {text[:200]}",
                        response.status,
                    ) from error
        except OnntrackApiError:
            raise
        except (TimeoutError, aiohttp.ClientError) as error:
            raise OnntrackApiError(f"Network error on {method} {path}: {error}") from error

        if response.status == 401:
            self._token = None
            raise OnntrackAuthError(f"Onntrack requires a new login for {method} {path}", response.status)
        if response.status >= 400:
            raise OnntrackApiError(f"Onntrack answered with HTTP {response.status} on {method} {path}", response.status)
        if not _response_ok(data):
            message = _response_message(data) or "unknown error"
            raise OnntrackApiError(f"Onntrack rejected {method} {path}: {message}", response.status)
        return data

    async def async_login(self) -> None:
        password_hash = hashlib.md5(self.password.encode("utf-8"), usedforsecurity=False).hexdigest()
        try:
            response = await self._request(
                "POST",
                "/homepage/login",
                {
                    "account": self.username,
                    "language": "en",
                    "nodeId": "",
                    "password": password_hash,
                    "validCode": "",
                },
            )
        except OnntrackAuthError:
            raise
        except OnntrackApiError as error:
            raise OnntrackAuthError("Onntrack login failed") from error
        data = _nested_data(response)
        token = data.get("token") if isinstance(data, dict) else None
        if not token:
            raise OnntrackAuthError("The Onntrack login returned no token")
        self._token = str(token)

    async def _async_ensure_login(self) -> None:
        if not self._token:
            await self.async_login()

    async def async_get_devices(self) -> tuple[Any, list[dict[str, Any]]]:
        await self._async_ensure_login()
        timestamp = int(time.time() * 1000)
        account_response = await self._request("GET", f"/account/current?timestamp={timestamp}")
        account_data = _nested_data(account_response)
        user_id = account_data.get("id") if isinstance(account_data, dict) else None
        if user_id in (None, ""):
            user_id = _first_value(account_data, {"id"})
        if user_id in (None, ""):
            raise OnntrackApiError("Onntrack returned no user id")

        device_response = await self._request(
            "POST",
            "/newDevice/list",
            {
                "imeis": "",
                "dateType": "activation",
                "startDate": "",
                "endDate": "",
                "lowerFlag": 0,
                "page": 1,
                "pageSize": 10,
                "activationFlag": "",
                "bindFlag": "",
                "dayNum": "",
                "expirationType": "",
                "equipment": {
                    "userId": user_id,
                    "deviceName": "",
                    "mcType": "",
                    "sim": "",
                    "isClose": "",
                    "orgId": "",
                    "equipmentDetail": {
                        "sn": "",
                        "vehicleNumber": "",
                        "carFrame": "",
                        "equipType": "",
                    },
                },
                "expirationDate": "",
                "totalSize": 1,
            },
        )
        return user_id, _extract_devices(device_response)

    async def async_fetch_data(self) -> dict[str, Any]:
        user_id, devices = await self.async_get_devices()
        records: dict[str, Any] = {}
        for device in devices:
            imei = str(device.get("imei") or "").strip()
            if not imei:
                continue
            monitor_user_id = device.get("userId") or user_id
            monitor_response = await self._request(
                "POST",
                "/newMonitor/getMonitorInfo",
                {"imei": imei, "isAllFlag": 1, "userId": monitor_user_id},
            )
            record = {
                "imei": imei,
                "device": device,
                **parse_monitor_data(monitor_response, device),
            }
            if not record["address"] and record["latitude"] is not None and record["longitude"] is not None:
                # Keep the last resolved address while a lookup is throttled,
                # otherwise the sensor drops to empty in between.
                address = await self._async_reverse_geocode(record["latitude"], record["longitude"])
                if address:
                    self._last_address[imei] = address
                record["address"] = address or self._last_address.get(imei)
            records[imei] = record
        return {"user_id": user_id, "devices": records}

    async def async_get_route(self, imei: str, start: datetime, end: datetime) -> list[Any]:
        await self._async_ensure_login()
        response = await self._request(
            "POST",
            "/newTrackInfo/getPointList",
            {
                "confidenceLevel": "",
                "endTime": end.strftime("%Y-%m-%d %H:%M:%S"),
                "imei": imei,
                "selectMap": "googleMap",
                "selectType": "all",
                "startTime": start.strftime("%Y-%m-%d %H:%M:%S"),
            },
        )
        return _extract_route_points(response)
