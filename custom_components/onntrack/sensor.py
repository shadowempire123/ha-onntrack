from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfLength
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .entity import device_info_for, record_for

SENSOR_DESCRIPTIONS = (
    SensorEntityDescription(
        key="battery",
        translation_key="battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery",
    ),
    SensorEntityDescription(
        key="status",
        translation_key="status",
        icon="mdi:car-connected",
    ),
    SensorEntityDescription(
        key="speed",
        translation_key="speed",
        native_unit_of_measurement="km/h",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:speedometer",
    ),
    SensorEntityDescription(
        key="mileage",
        translation_key="mileage",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:counter",
    ),
    SensorEntityDescription(
        key="today_mileage",
        translation_key="today_mileage",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        state_class=SensorStateClass.TOTAL,
        icon="mdi:map-marker-distance",
    ),
    SensorEntityDescription(
        key="address",
        translation_key="address",
        icon="mdi:map-marker",
    ),
    SensorEntityDescription(
        key="gnss",
        translation_key="gnss",
        icon="mdi:crosshairs-gps",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="visible_satellites",
        translation_key="visible_satellites",
        icon="mdi:satellite-variant",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="cellular_signal",
        translation_key="cellular_signal",
        icon="mdi:signal-cellular-3",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="last_online",
        translation_key="last_online",
        icon="mdi:cloud-clock",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="last_fix",
        translation_key="last_fix",
        icon="mdi:map-clock",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="imei",
        translation_key="imei",
        icon="mdi:identifier",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="alert_count",
        translation_key="alert_count",
        icon="mdi:alert",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    known: set[str] = set()

    @callback
    def _add_new_devices() -> None:
        """Pick up trackers added to the account after setup.

        Entities used to be built once, so a newly bought device stayed
        invisible until Home Assistant was restarted.
        """
        devices = (coordinator.data or {}).get("devices", {})
        added = [imei for imei in devices if imei not in known]
        if not added:
            return
        known.update(added)
        async_add_entities(
            OnntrackSensor(coordinator, imei, description)
            for imei in added
            for description in SENSOR_DESCRIPTIONS
        )

    _add_new_devices()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_devices))


class OnntrackSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: Any, imei: str, description: SensorEntityDescription) -> None:
        super().__init__(coordinator)
        self.coordinator = coordinator
        self.imei = imei
        self.entity_description = description
        self._attr_unique_id = f"{DOMAIN}_{imei}_{description.key}"

    @property
    def device_info(self):
        return device_info_for(record_for(self.coordinator, self.imei), self.imei)

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success and bool(record_for(self.coordinator, self.imei))

    @property
    def native_value(self) -> Any:
        return record_for(self.coordinator, self.imei).get(self.entity_description.key)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        record = record_for(self.coordinator, self.imei)
        # Deliberately no latitude/longitude here. Thirteen sensors carrying
        # the same coordinates wrote the vehicle's position into the recorder
        # thirteen times per poll; the device_tracker is where the position
        # belongs.
        attributes = {
            "positioning_time": record.get("positioning_time"),
        }
        if self.entity_description.key == "imei":
            attributes.update(
                {
                    "device_properties": record.get("device_properties", {}),
                    "monitor_properties": record.get("monitor_properties", {}),
                    "alert_properties": record.get("alert_properties", {}),
                }
            )
        elif self.entity_description.key == "alert_count":
            attributes["alert_properties"] = record.get("alert_properties", {})
        elif self.entity_description.key == "speed":
            attributes["reported_speed"] = record.get("reported_speed")
            attributes["parked"] = record.get("parked")
        return attributes

