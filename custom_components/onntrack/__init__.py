from __future__ import annotations

import hashlib
import secrets
import shutil
import time
from pathlib import Path
from urllib.parse import quote

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util

from .api import OnntrackApi, OnntrackAuthError
from .const import (
    CONF_BASE_URL,
    CONF_PASSWORD,
    CONF_REVERSE_GEOCODE,
    CONF_ROUTE_TOKEN,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
    DEFAULT_REVERSE_GEOCODE,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    PLATFORMS,
    SERVICE_GET_ROUTE,
    SERVICE_REGENERATE_ROUTE_TOKEN,
)
from .coordinator import OnntrackCoordinator
from .http import OnntrackRouteMapView, OnntrackRouteView
from .route_map import parse_route_points, route_geometry

DATA_ROUTE_MAPS = f"{DOMAIN}_route_maps"
STORAGE_VERSION = 1
# Lets reverse-geocoded addresses survive a restart. Without it the cache
# starts empty every time and Nominatim is asked for the same coordinates
# over and over.
GEOCODE_SAVE_DELAY = 300

GET_ROUTE_SCHEMA = vol.Schema(
    {
        vol.Required("imei"): cv.string,
        vol.Required("start"): cv.datetime,
        vol.Required("end"): cv.datetime,
        vol.Optional("config_entry_id"): cv.string,
    }
)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    route_maps = hass.data.setdefault(DATA_ROUTE_MAPS, {})
    hass.http.register_view(OnntrackRouteView(hass, route_maps))
    hass.http.register_view(OnntrackRouteMapView(hass, route_maps))

    async def async_get_route(call: ServiceCall) -> ServiceResponse:
        imei = call.data["imei"].strip()
        start = dt_util.as_local(call.data["start"])
        end = dt_util.as_local(call.data["end"])
        if start >= end:
            raise HomeAssistantError("Route start must be before route end")

        coordinators = hass.data.get(DOMAIN, {})
        config_entry_id = call.data.get("config_entry_id")
        if config_entry_id:
            coordinator = coordinators.get(config_entry_id)
        else:
            match = next(
                (
                    (entry_id, candidate)
                    for entry_id, candidate in coordinators.items()
                    if imei in (candidate.data or {}).get("devices", {})
                ),
                None,
            )
            config_entry_id, coordinator = match if match else (None, None)
        if coordinator is None:
            raise HomeAssistantError(f"No configured Onntrack device found for IMEI {imei}")

        try:
            points = await coordinator.api.async_get_route(imei, start, end)
        except OnntrackAuthError:
            await coordinator.api.async_login()
            points = await coordinator.api.async_get_route(imei, start, end)
        route_points = parse_route_points(points)
        if not route_points:
            raise HomeAssistantError("Onntrack returned no valid coordinates for this period")
        device_name = (
            coordinator.data["devices"][imei].get("device", {}).get("deviceName") or imei
        )
        map_id = hashlib.sha256(f"{config_entry_id}:{imei}".encode()).hexdigest()[:24]
        entry = hass.config_entries.async_get_entry(config_entry_id) if config_entry_id else None
        token = str((entry.data.get(CONF_ROUTE_TOKEN) if entry else "") or "")
        route_maps[map_id] = {
            "coordinator": coordinator,
            "imei": imei,
            "device_name": device_name,
            "token": token,
            "start": start.isoformat(),
            "end": end.isoformat(),
        }
        generated_at = int(time.time())
        credentials = f"token={quote(token, safe='')}&v={generated_at}"
        period = f"start={start:%Y-%m-%d}&end={end:%Y-%m-%d}"
        return {
            "imei": imei,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "point_count": len(points),
            "map_point_count": len(route_geometry(route_points)),
            "map_url": f"/api/onntrack/map/{map_id}?{credentials}",
            "geojson_url": f"/api/onntrack/route/{map_id}?{credentials}&format=geojson&{period}",
        }

    async def async_regenerate_route_token(call: ServiceCall) -> ServiceResponse:
        """Issue a new route token, invalidating every map link handed out so far.

        The token is what protects the route endpoints, so there has to be a way
        to replace it without deleting and re-adding the integration.
        """
        config_entry_id = call.data.get("config_entry_id")
        entries = hass.config_entries.async_entries(DOMAIN)
        if config_entry_id:
            entry = hass.config_entries.async_get_entry(config_entry_id)
            if entry is None or entry.domain != DOMAIN:
                raise HomeAssistantError(f"No Onntrack config entry with ID {config_entry_id}")
        elif len(entries) == 1:
            entry = entries[0]
        else:
            raise HomeAssistantError(
                "Several Onntrack accounts are configured; pass config_entry_id"
            )

        token = secrets.token_urlsafe(32)
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_ROUTE_TOKEN: token}
        )
        generated_at = int(time.time())
        maps = []
        for map_id, route in route_maps.items():
            if route.get("coordinator") is not hass.data.get(DOMAIN, {}).get(entry.entry_id):
                continue
            route["token"] = token
            maps.append(
                {
                    "imei": route["imei"],
                    "device_name": route["device_name"],
                    "map_url": (
                        f"/api/onntrack/map/{map_id}"
                        f"?token={quote(token, safe='')}&v={generated_at}"
                    ),
                }
            )
        return {"maps": maps}

    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_ROUTE,
        async_get_route,
        schema=GET_ROUTE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_REGENERATE_ROUTE_TOKEN,
        async_regenerate_route_token,
        schema=vol.Schema({vol.Optional("config_entry_id"): cv.string}),
        supports_response=SupportsResponse.ONLY,
    )
    return True


def _remove_legacy_route_files(config_dir: str) -> None:
    """Delete route pages that earlier versions wrote into /config/www.

    Everything below www is served as /local without any authentication, so
    those files handed the vehicle's track to anyone who knew the URL. The
    pages are regenerated on demand, nothing is lost by removing them.
    """
    shutil.rmtree(Path(config_dir) / "www" / "onntrack" / "routes", ignore_errors=True)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    await hass.async_add_executor_job(_remove_legacy_route_files, hass.config.config_dir)

    if not entry.data.get(CONF_ROUTE_TOKEN):
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, CONF_ROUTE_TOKEN: secrets.token_urlsafe(32)},
        )
    route_token = str(entry.data[CONF_ROUTE_TOKEN])

    store: Store[dict[str, str]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.geocode")
    stored = await store.async_load() or {}
    address_cache = {key: value for key, value in stored.items() if isinstance(value, str)}

    def _address_cache_changed() -> None:
        store.async_delay_save(lambda: address_cache, GEOCODE_SAVE_DELAY)

    session = async_get_clientsession(hass)
    api = OnntrackApi(
        session,
        entry.data[CONF_BASE_URL],
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        address_cache=address_cache,
        cache_changed=_address_cache_changed,
        reverse_geocode=entry.options.get(CONF_REVERSE_GEOCODE, DEFAULT_REVERSE_GEOCODE),
    )
    coordinator = OnntrackCoordinator(
        hass, api, entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    )
    try:
        await coordinator.async_config_entry_first_refresh()
    except UpdateFailed as error:
        raise ConfigEntryNotReady(str(error)) from error

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    route_maps = hass.data.setdefault(DATA_ROUTE_MAPS, {})
    for imei, record in coordinator.data.get("devices", {}).items():
        map_id = hashlib.sha256(f"{entry.entry_id}:{imei}".encode()).hexdigest()[:24]
        route_maps[map_id] = {
            "coordinator": coordinator,
            "imei": imei,
            "device_name": record.get("device", {}).get("deviceName") or imei,
            "token": route_token,
        }
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        coordinator = hass.data[DOMAIN].pop(entry.entry_id, None)
        route_maps = hass.data.get(DATA_ROUTE_MAPS, {})
        for map_id in [
            map_id
            for map_id, route in route_maps.items()
            if route.get("coordinator") is coordinator
        ]:
            route_maps.pop(map_id)
    return unloaded


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
