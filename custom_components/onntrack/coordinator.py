from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import OnntrackApi, OnntrackApiError, OnntrackAuthError, _is_active_alert_value
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN, EVENT_ALERT, EVENT_STATUS_CHANGED

_LOGGER = logging.getLogger(__name__)


class OnntrackCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(
        self,
        hass: HomeAssistant,
        api: OnntrackApi,
        scan_interval: int = DEFAULT_SCAN_INTERVAL,
    ) -> None:
        self.api = api
        super().__init__(
            hass,
            logger=_LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            data = await self.api.async_fetch_data()
        except OnntrackAuthError:
            try:
                await self.api.async_login()
                data = await self.api.async_fetch_data()
            except OnntrackAuthError as error:
                # A fresh login was refused too, so the stored password is no
                # longer valid. Asking Home Assistant for new credentials beats
                # retrying a wrong password every minute forever.
                raise ConfigEntryAuthFailed(str(error)) from error
            except OnntrackApiError as error:
                raise UpdateFailed(str(error)) from error
        except OnntrackApiError as error:
            raise UpdateFailed(str(error)) from error

        self._fire_change_events(data)
        return data

    def _fire_change_events(self, data: dict[str, Any]) -> None:
        if not self.data:
            return
        previous_devices = self.data.get("devices", {})
        current_devices = data.get("devices", {})
        for imei in previous_devices.keys() - current_devices.keys():
            previous = previous_devices[imei]
            self.hass.bus.async_fire(
                EVENT_ALERT,
                {
                    "imei": imei,
                    "device_name": previous.get("device", {}).get("deviceName") or imei,
                    "field": "device_removed",
                    "value": True,
                    "previous_value": False,
                    "address": previous.get("address"),
                    "latitude": previous.get("latitude"),
                    "longitude": previous.get("longitude"),
                    "positioning_time": previous.get("positioning_time"),
                },
            )

        for imei, record in current_devices.items():
            previous = previous_devices.get(imei)
            if not previous:
                continue
            device_name = record.get("device", {}).get("deviceName") or imei
            old_status = previous.get("status")
            new_status = record.get("status")
            if new_status != old_status:
                self.hass.bus.async_fire(
                    EVENT_STATUS_CHANGED,
                    {
                        "imei": imei,
                        "device_name": device_name,
                        "previous_status": old_status,
                        "status": new_status,
                    },
                )

            old_alerts = previous.get("alert_properties", {})
            for field, value in record.get("alert_properties", {}).items():
                old_value = old_alerts.get(field)
                if value != old_value and _is_active_alert_value(value):
                    self.hass.bus.async_fire(
                        EVENT_ALERT,
                        {
                            "imei": imei,
                            "device_name": device_name,
                            "field": field,
                            "value": value,
                            "previous_value": old_value,
                            "address": record.get("address"),
                            "latitude": record.get("latitude"),
                            "longitude": record.get("longitude"),
                            "positioning_time": record.get("positioning_time"),
                        },
                    )
