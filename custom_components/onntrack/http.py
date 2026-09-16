from __future__ import annotations

import hmac
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .api import OnntrackAuthError
from .route_map import (
    build_route_geojson,
    build_route_page,
    detect_stops,
    parse_route_points,
    route_geometry,
)

try:
    from homeassistant.components.http.const import KEY_AUTHENTICATED
except ImportError:  # pragma: no cover - older cores
    KEY_AUTHENTICATED = "ha_authenticated"


def _authorized(request: web.Request, route: dict[str, Any]) -> bool:
    """Accept a normal Home Assistant login or the route's own token.

    The map runs inside a dashboard iframe, which cannot send an
    Authorization header, so the per-entry token in the URL is what makes the
    page usable at all. Both views therefore keep requires_auth = False and
    decide here instead of leaving the route data open to anyone.
    """
    if request.get(KEY_AUTHENTICATED):
        return True
    token = str(route.get("token") or "")
    return bool(token) and hmac.compare_digest(token, request.query.get("token", ""))


def _next_month(value: datetime) -> datetime:
    if value.month == 12:
        return value.replace(year=value.year + 1, month=1, day=1)
    return value.replace(month=value.month + 1, day=1)


def _route_period(query: Any, time_zone: str) -> tuple[datetime, datetime, str]:
    zone = ZoneInfo(time_zone)
    start_value = query.get("start")
    end_value = query.get("end")
    if start_value or end_value:
        if not start_value or not end_value:
            raise web.HTTPBadRequest(text="start and end must both use YYYY-MM-DD")
        try:
            start = datetime.strptime(start_value, "%Y-%m-%d").replace(tzinfo=zone)
            end = datetime.strptime(end_value, "%Y-%m-%d").replace(tzinfo=zone) + timedelta(days=1)
        except ValueError as error:
            raise web.HTTPBadRequest(text="start and end must use YYYY-MM-DD") from error
        if end <= start or end - start > timedelta(days=366):
            raise web.HTTPBadRequest(text="route range must be between 1 day and 1 year")
        return start, end, f"{start_value} to {end_value}"

    month = query.get("month", "")
    try:
        start = datetime.strptime(month, "%Y-%m").replace(tzinfo=zone)
    except ValueError as error:
        raise web.HTTPBadRequest(text="month must use YYYY-MM") from error
    if start.year < 2000 or start.year > 2100:
        raise web.HTTPBadRequest(text="month is outside the supported range")
    return start, _next_month(start), month


def _month_chunks(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    chunks: list[tuple[datetime, datetime]] = []
    cursor = start
    while cursor < end:
        chunk_end = min(_next_month(cursor), end)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end
    return chunks


class OnntrackRouteView(HomeAssistantView):
    url = "/api/onntrack/route/{map_id}"
    name = "api:onntrack:route"
    requires_auth = False

    def __init__(self, hass: HomeAssistant, route_maps: dict[str, dict[str, Any]]) -> None:
        self._hass = hass
        self._route_maps = route_maps

    async def get(self, request: web.Request, map_id: str) -> web.Response:
        route = self._route_maps.get(map_id)
        if route is None:
            raise web.HTTPNotFound()
        if not _authorized(request, route):
            raise web.HTTPUnauthorized()

        period_start, period_end, period_label = _route_period(
            request.query, self._hass.config.time_zone
        )

        coordinator = route["coordinator"]
        raw_points: list[Any] = []
        for chunk_start, chunk_end in _month_chunks(period_start, period_end):
            try:
                raw_points.extend(
                    await coordinator.api.async_get_route(route["imei"], chunk_start, chunk_end)
                )
            except OnntrackAuthError:
                await coordinator.api.async_login()
                raw_points.extend(
                    await coordinator.api.async_get_route(route["imei"], chunk_start, chunk_end)
                )
        points = parse_route_points(raw_points)
        if request.query.get("format") == "geojson":
            return self.json(
                build_route_geojson(
                    route["imei"],
                    route["device_name"],
                    period_start.isoformat(),
                    period_end.isoformat(),
                    points,
                )
            )
        geometry = route_geometry(points)
        stops = detect_stops(points)
        return self.json(
            {
                "period": period_label,
                "device_name": route["device_name"],
                "raw_point_count": len(raw_points),
                "point_count": len(geometry),
                "points": [
                    [point.latitude, point.longitude, point.timestamp]
                    for point in geometry
                ],
                "stops": [
                    [
                        stop.latitude,
                        stop.longitude,
                        stop.start,
                        stop.end,
                        stop.duration_minutes,
                        stop.duration_seconds,
                    ]
                    for stop in stops
                ],
            }
        )

class OnntrackRouteMapView(HomeAssistantView):
    """Serve the Leaflet page itself, instead of writing it into /config/www.

    Anything below www is reachable as /local without any authentication, so a
    route page stored there hands out the vehicle's track to whoever opens the
    URL.
    """

    url = "/api/onntrack/map/{map_id}"
    name = "api:onntrack:map"
    requires_auth = False

    def __init__(self, hass: HomeAssistant, route_maps: dict[str, dict[str, Any]]) -> None:
        self._hass = hass
        self._route_maps = route_maps

    async def get(self, request: web.Request, map_id: str) -> web.Response:
        route = self._route_maps.get(map_id)
        if route is None:
            raise web.HTTPNotFound()
        if not _authorized(request, route):
            raise web.HTTPUnauthorized()

        today = datetime.now(ZoneInfo(self._hass.config.time_zone))
        start = str(route.get("start") or today.replace(day=1).strftime("%Y-%m-%d"))
        end = str(route.get("end") or today.strftime("%Y-%m-%d"))
        return web.Response(
            text=build_route_page(map_id, str(route.get("token") or ""), route["device_name"], start, end),
            content_type="text/html",
            headers={"Cache-Control": "no-store"},
        )
