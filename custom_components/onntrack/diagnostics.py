"""Diagnostics for issue reports.

Everything that identifies the account, the device or where it has been is
removed. What is left is the shape of the data, which is what a bug report
actually needs -- an IMEI in a public GitHub issue is enough to bind the device
on portals of this kind.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_PASSWORD, CONF_ROUTE_TOKEN, CONF_USERNAME, DOMAIN

TO_REDACT = {
    CONF_PASSWORD,
    CONF_ROUTE_TOKEN,
    CONF_USERNAME,
    "address",
    "deviceName",
    "imei",
    "latitude",
    "longitude",
    "sim",
    "simNo",
    "userId",
    "user_id",
}


def _redact_devices(devices: dict[str, Any]) -> list[dict[str, Any]]:
    """Redact the device map, whose keys are IMEIs.

    ``async_redact_data`` only touches values, so a plain call would leave every
    IMEI sitting in the keys.
    """
    return [
        async_redact_data(record, TO_REDACT) for _imei, record in sorted(devices.items())
    ]


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    data = (coordinator.data if coordinator else None) or {}
    return {
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
        "device_count": len(data.get("devices", {})),
        "devices": _redact_devices(data.get("devices", {})),
    }
