"""Diagnostics for issue reports.

Downloaded diagnostics end up pasted into public issues, so the guiding rule
here is that nothing identifying the account, the device or where it has been
may appear -- an IMEI is enough to bind a device on portals of this kind, and
the coordinates are somebody's home address.

The portal's raw payload cannot be cleaned by listing forbidden key names. It
carries a display structure in which the interesting values sit in generic
``value`` fields (``monitorBaseVOS[].value``, ``values.latlng``), so the same
IMEI turns up under half a dozen different keys. Instead of guessing at names,
the raw payloads are reduced to their *shape* -- which fields the portal sent
and of what type -- which is what a bug report actually needs. A value-based
scrub runs over the result as a second line of defence.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_PASSWORD, CONF_ROUTE_TOKEN, CONF_USERNAME, DOMAIN

REDACTED = "**REDACTED**"

TO_REDACT = {
    CONF_PASSWORD,
    CONF_ROUTE_TOKEN,
    CONF_USERNAME,
    "address",
    "imei",
    "latitude",
    "longitude",
}

# Values the integration derives itself. These are the numbers a report is
# usually about, and none of them identifies anybody.
SAFE_FIELDS = (
    "battery",
    "status",
    "speed",
    "reported_speed",
    "parked",
    "mileage",
    "today_mileage",
    "gnss",
    "visible_satellites",
    "cellular_signal",
    "last_fix",
    "last_online",
    "positioning_time",
    "alert_count",
)

MAX_SHAPE_DEPTH = 4


def describe_shape(value: Any, depth: int = 0) -> Any:
    """Return the structure of a payload without any of its values."""
    if isinstance(value, dict):
        if depth >= MAX_SHAPE_DEPTH:
            return f"dict[{len(value)}]"
        return {key: describe_shape(item, depth + 1) for key, item in sorted(value.items())}
    if isinstance(value, list):
        if not value:
            return []
        if depth >= MAX_SHAPE_DEPTH:
            return f"list[{len(value)}]"
        return [describe_shape(value[0], depth + 1), f"... {len(value)} items"]
    return type(value).__name__


def sensitive_values(entry_data: dict[str, Any], data: dict[str, Any]) -> set[str]:
    """Collect the strings that must not appear anywhere in the output."""
    values: set[str] = set()
    for key in (CONF_USERNAME, CONF_PASSWORD, CONF_ROUTE_TOKEN):
        if entry_data.get(key):
            values.add(str(entry_data[key]))
    for imei, record in (data.get("devices") or {}).items():
        values.add(str(imei))
        for key in ("address",):
            if record.get(key):
                values.add(str(record[key]))
        for key in ("latitude", "longitude"):
            if record.get(key) is not None:
                # Four decimals is roughly eleven metres -- enough to match the
                # same position however the portal happened to format it.
                values.add(f"{float(record[key]):.4f}")
        device = record.get("device") or {}
        for key in ("deviceName", "vehicleNumber", "carNumber", "name"):
            if device.get(key):
                values.add(str(device[key]))
    return {value for value in values if len(value) >= 4}


def scrub(value: Any, secrets: set[str]) -> Any:
    """Replace anything that still carries one of the sensitive values."""
    if isinstance(value, dict):
        return {key: scrub(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [scrub(item, secrets) for item in value]
    if isinstance(value, bool) or value is None:
        return value
    text = str(value)
    if any(secret in text for secret in secrets):
        return REDACTED
    return value


def _describe_device(record: dict[str, Any]) -> dict[str, Any]:
    described: dict[str, Any] = {field: record.get(field) for field in SAFE_FIELDS}
    described["has_address"] = bool(record.get("address"))
    described["has_position"] = record.get("latitude") is not None
    described["alert_properties"] = {
        key: bool(value) for key, value in (record.get("alert_properties") or {}).items()
    }
    for key in ("device", "device_properties", "monitor_properties"):
        described[f"{key}_shape"] = describe_shape(record.get(key) or {})
    return described


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    data = (coordinator.data if coordinator else None) or {}
    secrets = sensitive_values(dict(entry.data), data)

    report = {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "coordinator": {
            "last_update_success": bool(coordinator and coordinator.last_update_success),
            "update_interval_seconds": (
                coordinator.update_interval.total_seconds()
                if coordinator and coordinator.update_interval
                else None
            ),
            "reverse_geocode": bool(coordinator and coordinator.api.reverse_geocode),
        },
        "device_count": len(data.get("devices") or {}),
        # Keyed by IMEI, so the keys are dropped rather than redacted.
        "devices": [
            _describe_device(record)
            for _imei, record in sorted((data.get("devices") or {}).items())
        ],
    }
    return scrub(report, secrets)
