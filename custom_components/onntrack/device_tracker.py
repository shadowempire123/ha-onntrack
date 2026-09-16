from __future__ import annotations

from typing import Any

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .entity import device_info_for, record_for


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    known: set[str] = set()

    @callback
    def _add_new_devices() -> None:
        """Pick up trackers added to the account after setup."""
        devices = (coordinator.data or {}).get("devices", {})
        added = [imei for imei in devices if imei not in known]
        if not added:
            return
        known.update(added)
        async_add_entities(OnntrackTracker(coordinator, imei) for imei in added)

    _add_new_devices()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_devices))


class OnntrackTracker(CoordinatorEntity, TrackerEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "location"
    _attr_icon = "mdi:map-marker"
    _attr_source_type = SourceType.GPS

    def __init__(self, coordinator: Any, imei: str) -> None:
        super().__init__(coordinator)
        self.coordinator = coordinator
        self.imei = imei
        self._attr_unique_id = f"{DOMAIN}_{imei}_location"

    @property
    def device_info(self):
        return device_info_for(record_for(self.coordinator, self.imei), self.imei)

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success and bool(record_for(self.coordinator, self.imei))

    @property
    def latitude(self) -> float | None:
        value = record_for(self.coordinator, self.imei).get("latitude")
        return float(value) if value is not None else None

    @property
    def longitude(self) -> float | None:
        value = record_for(self.coordinator, self.imei).get("longitude")
        return float(value) if value is not None else None

    @property
    def battery_level(self) -> int | None:
        value = record_for(self.coordinator, self.imei).get("battery")
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        record = record_for(self.coordinator, self.imei)
        return {
            "imei": self.imei,
            "status": record.get("status"),
            "battery": record.get("battery"),
            "speed": record.get("speed"),
            "reported_speed": record.get("reported_speed"),
            "parked": record.get("parked"),
            "mileage": record.get("mileage"),
            "address": record.get("address"),
            "positioning_time": record.get("positioning_time"),
            "alert_count": record.get("alert_count"),
        }

