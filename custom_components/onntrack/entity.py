from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN


def record_for(coordinator: Any, imei: str) -> dict[str, Any]:
    data = coordinator.data or {}
    return data.get("devices", {}).get(imei, {})


def device_info_for(record: dict[str, Any], imei: str) -> DeviceInfo:
    device = record.get("device", {})
    name = str(device.get("deviceName") or f"Onntrack {imei}")
    model = str(device.get("mcTypeAlias") or device.get("mcType") or "Portable Pro+")
    return DeviceInfo(
        identifiers={(DOMAIN, imei)},
        name=name,
        manufacturer="Onntrack",
        model=model,
        serial_number=imei,
    )
