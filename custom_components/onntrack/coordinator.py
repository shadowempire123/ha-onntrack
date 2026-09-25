from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .alarms import alarm_window_start, merge_alarms
from .api import OnntrackApi, OnntrackApiError, OnntrackAuthError, _is_active_alert_value
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN, EVENT_ALERT, EVENT_STATUS_CHANGED

_LOGGER = logging.getLogger(__name__)


class OnntrackCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(
        self,
        hass: HomeAssistant,
        api: OnntrackApi,
        scan_interval: int = DEFAULT_SCAN_INTERVAL,
        alarm_store: Any = None,
    ) -> None:
        self.api = api
        # Per IMEI: {"alarms": [...newest first], "checked_until": iso}. Kept
        # in a Store so the log outlives restarts and the backfill runs once.
        self.alarm_log: dict[str, dict[str, Any]] = {}
        self._alarm_store = alarm_store
        self._alarm_error_logged = False
        super().__init__(
            hass,
            logger=_LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )

    async def async_load_alarm_log(self) -> None:
        if self._alarm_store is None:
            return
        stored = await self._alarm_store.async_load() or {}
        self.alarm_log = {
            imei: entry
            for imei, entry in stored.items()
            if isinstance(entry, dict) and isinstance(entry.get("alarms"), list)
        }

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

        await self._async_update_alarms(data)
        self._fire_change_events(data)
        return data

    async def _async_update_alarms(self, data: dict[str, Any]) -> None:
        """Pull alarms the portal reported since the last poll into the log.

        A failure here must not take the position sensors down with it, so it
        is logged and the next poll simply tries the same window again.
        """
        changed = False
        for imei, record in data.get("devices", {}).items():
            entry = self.alarm_log.get(imei)
            now = datetime.now(UTC)
            try:
                fresh = await self.api.async_get_alarms(
                    imei,
                    alarm_window_start(
                        (entry or {}).get("alarms", []), (entry or {}).get("checked_until")
                    ),
                    now + timedelta(minutes=5),
                )
            except OnntrackApiError as error:
                if not self._alarm_error_logged:
                    _LOGGER.warning("Could not fetch Onntrack alarms for %s: %s", imei, error)
                    self._alarm_error_logged = True
                record["alarms"] = (entry or {}).get("alarms", [])
                continue
            self._alarm_error_logged = False
            merged, added = merge_alarms((entry or {}).get("alarms", []), fresh)
            self.alarm_log[imei] = {"alarms": merged, "checked_until": now.isoformat()}
            record["alarms"] = merged
            # checked_until alone is not worth a disk write every minute; after
            # a restart the window then starts at the newest alarm instead.
            changed = changed or entry is None or bool(added)
            # The first sync after installing is the backfill; announcing a
            # few hundred old alarms at once would help nobody.
            if entry is None:
                continue
            device_name = record.get("device", {}).get("deviceName") or imei
            for alarm in reversed(added):
                self.hass.bus.async_fire(
                    EVENT_ALERT,
                    {
                        "imei": imei,
                        "device_name": device_name,
                        "field": "portal_alarm",
                        "value": alarm["type"],
                        "previous_value": None,
                        "alarm_id": alarm["id"],
                        "alarm_code": alarm["code"],
                        "alarm_time": alarm["time"],
                        "address": record.get("address"),
                        "latitude": alarm["latitude"],
                        "longitude": alarm["longitude"],
                        "positioning_time": record.get("positioning_time"),
                    },
                )
        if changed and self._alarm_store is not None:
            self._alarm_store.async_delay_save(lambda: self.alarm_log, 10)

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
