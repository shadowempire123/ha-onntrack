from __future__ import annotations

import html
import math
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import quote


@dataclass(frozen=True)
class RoutePoint:
    timestamp: str
    latitude: float
    longitude: float


@dataclass(frozen=True)
class RouteStop:
    start: str
    end: str
    duration_minutes: int
    duration_seconds: int
    latitude: float
    longitude: float


def parse_route_point(value: Any) -> RoutePoint | None:
    if isinstance(value, str):
        parts = value.split("|")
        if len(parts) < 10:
            return None
        timestamp, latitude_value, longitude_value = parts[7:10]
    elif isinstance(value, dict):
        timestamp = str(
            value.get("positioningTime")
            or value.get("gpsTime")
            or value.get("time")
            or value.get("timestamp")
            or ""
        )
        latitude_value = value.get("latitude", value.get("lat"))
        longitude_value = value.get("longitude", value.get("lng", value.get("lon")))
    else:
        return None

    try:
        latitude = float(latitude_value)
        longitude = float(longitude_value)
    except (TypeError, ValueError):
        return None
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        return None
    return RoutePoint(timestamp.strip(), latitude, longitude)


def parse_route_points(values: list[Any]) -> list[RoutePoint]:
    return [point for value in values if (point := parse_route_point(value)) is not None]


def route_geometry(points: list[RoutePoint]) -> list[RoutePoint]:
    geometry: list[RoutePoint] = []
    for point in points:
        if not geometry or (point.latitude, point.longitude) != (
            geometry[-1].latitude,
            geometry[-1].longitude,
        ):
            geometry.append(point)
    return geometry


def _parse_timestamp(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _distance_meters(first: RoutePoint, second: RoutePoint) -> float:
    earth_radius = 6_371_000
    latitude_1 = math.radians(first.latitude)
    latitude_2 = math.radians(second.latitude)
    latitude_delta = latitude_2 - latitude_1
    longitude_delta = math.radians(second.longitude - first.longitude)
    haversine = (
        math.sin(latitude_delta / 2) ** 2
        + math.cos(latitude_1) * math.cos(latitude_2) * math.sin(longitude_delta / 2) ** 2
    )
    return 2 * earth_radius * math.asin(math.sqrt(haversine))


def detect_stops(
    points: list[RoutePoint],
    minimum_minutes: int = 20,
    radius_meters: int = 100,
) -> list[RouteStop]:
    stops: list[RouteStop] = []
    index = 0
    while index < len(points) - 1:
        started_at = _parse_timestamp(points[index].timestamp)
        if started_at is None:
            index += 1
            continue
        end_index = index + 1
        while end_index < len(points) and _distance_meters(points[index], points[end_index]) <= radius_meters:
            end_index += 1
        last_index = end_index - 1
        ended_at = _parse_timestamp(points[last_index].timestamp)
        if ended_at is not None:
            duration_seconds = int((ended_at - started_at).total_seconds())
            duration_minutes = duration_seconds // 60
            if duration_seconds >= minimum_minutes * 60:
                cluster = points[index:end_index]
                stops.append(
                    RouteStop(
                        start=points[index].timestamp,
                        end=points[last_index].timestamp,
                        duration_minutes=duration_minutes,
                        duration_seconds=duration_seconds,
                        latitude=sum(point.latitude for point in cluster) / len(cluster),
                        longitude=sum(point.longitude for point in cluster) / len(cluster),
                    )
                )
        index = max(end_index, index + 1)
    return stops


def build_route_geojson(
    imei: str,
    device_name: str,
    start: str,
    end: str,
    points: list[RoutePoint],
) -> dict[str, Any]:
    geometry_points = route_geometry(points)
    stops = detect_stops(points)
    coordinates = [[point.longitude, point.latitude] for point in geometry_points]
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "imei": imei,
                    "device_name": device_name,
                    "start": start,
                    "end": end,
                    "point_count": len(points),
                },
                "geometry": {"type": "LineString", "coordinates": coordinates},
            },
            *[
                {
                    "type": "Feature",
                    "properties": {
                        "kind": "stop",
                        "start": stop.start,
                        "end": stop.end,
                        "duration_minutes": stop.duration_minutes,
                        "duration_seconds": stop.duration_seconds,
                    },
                    "geometry": {"type": "Point", "coordinates": [stop.longitude, stop.latitude]},
                }
                for stop in stops
            ],
            ],
    }


def build_route_page(
    map_id: str,
    token: str,
    device_name: str,
    start: str,
    end: str,
) -> str:
    """Render the route page.

    The page carries no coordinates of its own. It is served from a view that
    checks the route token and pulls every position from the route endpoint,
    so no GPS data is written to the unauthenticated /local path.
    """
    safe_map_id = re.sub(r"[^a-zA-Z0-9_-]", "_", map_id)
    route_endpoint = f"/api/onntrack/route/{safe_map_id}?token={quote(token, safe='')}"
    title = html.escape(device_name)
    period = html.escape(f"{start} - {end}")
    initial_month = html.escape(start[:7])
    initial_start = html.escape(start[:10])
    initial_end = html.escape(end[:10])
    page = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="referrer" content="strict-origin-when-cross-origin">
    <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
    <meta http-equiv="Pragma" content="no-cache">
    <meta http-equiv="Expires" content="0">
  <title>Onntrack Route - {title}</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
  <style>
        html, body, #map {{ height: 100%; margin: 0; }}
        body {{ font-family: "Helvetica Neue", sans-serif; color: #202124; }}
        .controls {{ position: absolute; z-index: 1000; top: 12px; right: 12px; max-width: 380px;
      padding: 10px 12px; background: rgba(255,255,255,.94); border-radius: 6px;
      box-shadow: 0 1px 5px rgba(0,0,0,.35); }}
        .controls strong, .controls span {{ display: block; }}
        .controls span {{ margin-top: 5px; font-size: 12px; color: #444; }}
        .picker {{ display: flex; gap: 6px; margin-top: 8px; }}
        .picker button, .picker input {{ min-height: 34px; border: 1px solid #aaa; border-radius: 4px; background: white; }}
        .picker button {{ width: 38px; cursor: pointer; font-size: 18px; }}
        .picker input {{ min-width: 0; padding: 0 6px; }}
        .range {{ display: grid; grid-template-columns: 1fr 1fr auto; gap: 6px; margin-top: 7px; }}
        .range input, .range button {{ min-width: 0; min-height: 32px; border: 1px solid #aaa; border-radius: 4px; background: white; }}
        .range button {{ padding: 0 10px; cursor: pointer; }}
                .filters {{ display: flex; align-items: center; justify-content: flex-end; gap: 12px; margin-top: 8px; font-size: 12px; }}
                .filters label {{ display: flex; align-items: center; gap: 6px; white-space: nowrap; }}
        #loading {{ color: #1565c0; }}
                .waypoints {{ position: absolute; z-index: 999; top: 12px; left: 12px; width: min(310px, calc(100% - 24px));
                    max-height: calc(100% - 24px); display: flex; flex-direction: column; overflow: hidden;
                    background: rgba(255,255,255,.96); border-radius: 6px; box-shadow: 0 1px 5px rgba(0,0,0,.35); }}
                .waypoints header {{ padding: 11px 12px 9px; border-bottom: 1px solid #ddd; }}
                .waypoints header strong {{ display: block; }}
                .waypoints header span {{ font-size: 12px; color: #5f6368; }}
                .waypoint-list {{ list-style: none; margin: 0; padding: 0; overflow-y: auto; }}
                .waypoint {{ width: 100%; display: grid; grid-template-columns: 30px 1fr; gap: 8px; padding: 10px 12px;
                    text-align: left; border: 0; border-bottom: 1px solid #eee; background: transparent; cursor: pointer; }}
                .waypoint:hover, .waypoint:focus-visible {{ background: #eef5fb; outline: none; }}
                .waypoint-index {{ width: 25px; height: 25px; display: grid; place-items: center; border-radius: 50%;
                    color: white; background: #1565c0; font-size: 12px; font-weight: 700; }}
                .waypoint[data-kind="start"] .waypoint-index {{ background: #188038; }}
                .waypoint[data-kind="stop"] .waypoint-index {{ background: #e8710a; }}
                .waypoint[data-kind="end"] .waypoint-index {{ background: #c5221f; }}
                .waypoint-title {{ display: block; font-weight: 600; font-size: 13px; }}
                .waypoint-meta {{ display: block; margin-top: 3px; color: #5f6368; font-size: 11px; line-height: 1.35; white-space: pre-line; }}
                .empty {{ padding: 14px 12px; color: #5f6368; font-size: 12px; }}
                .pause-popup {{ min-width: 250px; color: #718096; line-height: 1.45; white-space: pre; }}
                .track-popup {{ min-width: 210px; line-height: 1.45; white-space: nowrap; }}
                .leaflet-popup-content {{ width: auto !important; max-width: min(320px, calc(100vw - 80px)); }}
                .leaflet-popup-content-wrapper {{ max-width: calc(100vw - 40px); }}
                @media (max-width: 760px) {{
                    .controls {{ top: 8px; right: 8px; left: 8px; max-width: none; }}
                    .waypoints {{ top: auto; right: 8px; bottom: 8px; left: 8px; width: auto; max-height: 36%; }}
                    .leaflet-control-zoom {{ display: none; }}
                    .range {{ grid-template-columns: 1fr 1fr; }}
                    .range button {{ grid-column: 1 / -1; }}
                }}
  </style>
</head>
<body>
  <div id="map"></div>
    <div class="controls">
        <strong>{title}</strong>
        <span id="period">{period}</span>
        <span id="count"></span>
        <div class="picker">
            <button id="previous" title="Previous month" aria-label="Previous month">&#8249;</button>
            <input id="month" type="month" value="{initial_month}" aria-label="Month">
            <button id="next" title="Next month" aria-label="Next month">&#8250;</button>
        </div>
        <div class="range">
            <input id="start" type="date" value="{initial_start}" aria-label="Start date">
            <input id="end" type="date" value="{initial_end}" aria-label="End date">
            <button id="show-range">Show</button>
        </div>
                <div class="filters">
                    <label><input id="show-points" type="checkbox"> Track points</label>
                </div>
        <span id="loading"></span>
    </div>
        <aside class="waypoints" aria-label="Waypoints and stops">
            <header>
                <strong>Waypoints &amp; stops</strong>
                <span id="waypoint-summary"></span>
            </header>
            <ol id="waypoint-list" class="waypoint-list"></ol>
        </aside>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
        const routeEndpoint = "{route_endpoint}";
        const initialPoints = [];
        const initialStops = [];
        const map = L.map("map", {{zoomControl: false}});
        const streetLayer = L.tileLayer("https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png", {{
          maxZoom: 19,
          referrerPolicy: "strict-origin-when-cross-origin",
          attribution: "&copy; OpenStreetMap contributors"
        }}).addTo(map);
        // Aerial imagery without an API key, switchable next to the street map.
        const satelliteLayer = L.tileLayer(
          "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}", {{
          maxZoom: 19,
          referrerPolicy: "strict-origin-when-cross-origin",
          attribution: "Imagery &copy; Esri, Maxar, Earthstar Geographics"
        }});
        L.control.layers({{"Map": streetLayer, "Satellite": satelliteLayer}}, null, {{position: "bottomright"}}).addTo(map);
        L.control.zoom({{position: "bottomright"}}).addTo(map);
        const routeLayer = L.layerGroup().addTo(map);
        const trackPointLayer = L.layerGroup().addTo(map);
        const monthInput = document.getElementById("month");
        const startInput = document.getElementById("start");
        const endInput = document.getElementById("end");
        const showPoints = document.getElementById("show-points");
        const waypointList = document.getElementById("waypoint-list");
        const waypointSummary = document.getElementById("waypoint-summary");
        let currentPoints = initialPoints;

        function durationLabel(seconds) {{
            const days = Math.floor(seconds / 86400);
            const hours = Math.floor((seconds % 86400) / 3600);
            const minutes = Math.floor((seconds % 3600) / 60);
            const rest = seconds % 60;
            return [days && days + "d", hours && hours + "h", minutes && minutes + "m", rest && rest + "s"]
                .filter(Boolean).join(" ") || "0s";
        }}
        function timeLabel(value) {{
            const parsed = new Date(value);
            if (Number.isNaN(parsed.getTime())) return value || "Unknown";
            return new Intl.DateTimeFormat(undefined, {{dateStyle: "medium", timeStyle: "short"}}).format(parsed);
        }}
        function popupContent(title, lines) {{
            const content = document.createElement("div");
            const heading = document.createElement("strong");
            heading.textContent = title;
            content.appendChild(heading);
            for (const line of lines) {{
                content.appendChild(document.createElement("br"));
                content.appendChild(document.createTextNode(line));
            }}
            return content;
        }}
        function pausePopup(stop) {{
            const content = document.createElement("div");
            content.className = "pause-popup";
            content.textContent = "Start Time: " + String(stop[2] || "").replace("T", " ")
                + "\\nEnd Time: " + String(stop[3] || "").replace("T", " ")
                + "\\nDuration: " + durationLabel(stop[5]);
            return content;
        }}
        function trackPopup(point, index) {{
            const content = document.createElement("div");
            content.className = "track-popup";
            const heading = document.createElement("strong");
            heading.textContent = "Trackpunkt " + (index + 1);
            const timestamp = document.createElement("div");
            timestamp.textContent = timeLabel(point[2]);
            content.append(heading, timestamp);
            return content;
        }}
        function renderWaypoints(points, stops) {{
            waypointList.replaceChildren();
            if (!points.length) {{
                const empty = document.createElement("li");
                empty.className = "empty";
                empty.textContent = "No waypoints in the selected period";
                waypointList.appendChild(empty);
                waypointSummary.textContent = "0 waypoints";
                return;
            }}
            const entries = [
                {{kind: "start", title: "Start", latitude: points[0][0], longitude: points[0][1], start: points[0][2]}},
                ...stops.map(stop => ({{kind: "stop", title: "Stop", latitude: stop[0], longitude: stop[1], start: stop[2], end: stop[3], duration: stop[5]}})),
                {{kind: "end", title: "End", latitude: points.at(-1)[0], longitude: points.at(-1)[1], start: points.at(-1)[2]}}
            ];
            entries.forEach((entry, index) => {{
                const item = document.createElement("li");
                const button = document.createElement("button");
                button.type = "button";
                button.className = "waypoint";
                button.dataset.kind = entry.kind;
                const badge = document.createElement("span");
                badge.className = "waypoint-index";
                badge.textContent = String(index + 1);
                const text = document.createElement("span");
                const title = document.createElement("span");
                title.className = "waypoint-title";
                title.textContent = entry.kind === "stop"
                    ? entry.title + " · " + durationLabel(entry.duration)
                    : entry.title;
                const meta = document.createElement("span");
                meta.className = "waypoint-meta";
                meta.textContent = timeLabel(entry.start)
                    + (entry.end ? " to " + timeLabel(entry.end) : "")
                    + "\\n" + entry.latitude.toFixed(5) + ", " + entry.longitude.toFixed(5);
                text.append(title, meta);
                button.append(badge, text);
                button.addEventListener("click", () => map.flyTo(
                    [entry.latitude, entry.longitude], Math.max(map.getZoom(), 14), {{duration: 0.5}}
                ));
                item.appendChild(button);
                waypointList.appendChild(item);
            }});
            waypointSummary.textContent = stops.length + " stops · " + points.length + " track points";
        }}
        function renderTrackPoints() {{
            trackPointLayer.clearLayers();
            if (!showPoints.checked) return;
            currentPoints.forEach((point, index) => {{
                L.circleMarker([point[0], point[1]], {{radius: 3, color: "#0b57d0", weight: 1, fillOpacity: 0.8}})
                    .addTo(trackPointLayer)
                    .bindPopup(trackPopup(point, index), {{minWidth: 210, maxWidth: 280}});
            }});
        }}
        function showRoute(points, stops) {{
            routeLayer.clearLayers();
            currentPoints = points;
            renderWaypoints(points, stops);
            renderTrackPoints();
            if (!points.length) return;
            const coordinates = points.map(point => [point[0], point[1]]);
            const route = L.polyline(coordinates, {{color: "#1565c0", weight: 4, opacity: 0.85}}).addTo(routeLayer);
            L.circleMarker(coordinates[0], {{radius: 8, color: "#0d652d", fillColor: "#188038", fillOpacity: 1}})
                .addTo(routeLayer).bindPopup(popupContent("Start", [timeLabel(points[0][2])]));
            L.circleMarker(coordinates.at(-1), {{radius: 8, color: "#a50e0e", fillColor: "#c5221f", fillOpacity: 1}})
                .addTo(routeLayer).bindPopup(popupContent("End", [timeLabel(points.at(-1)[2])]));
            for (const stop of stops) {{
                L.circleMarker([stop[0], stop[1]], {{radius: 7, color: "#a63d00", fillColor: "#ff8f00", fillOpacity: 0.85}})
                    .addTo(routeLayer)
                    .bindPopup(pausePopup(stop), {{minWidth: 250, maxWidth: 320}});
            }}
            if (coordinates.length === 1) map.setView(coordinates[0], 15);
            else map.fitBounds(route.getBounds(), {{padding: [48, 48]}});
        }}
        showRoute(initialPoints, initialStops);
        map.setView([0, 0], 2);
        loadRange();

        async function loadMonth() {{
            const month = monthInput.value;
            if (!month) return;
            document.getElementById("loading").textContent = "Loading route...";
            try {{
                const query = new URLSearchParams({{month}});
                const response = await fetch(routeEndpoint + "&" + query);
                if (!response.ok) throw new Error(await response.text() || "HTTP " + response.status);
                const data = await response.json();
                showRoute(data.points, data.stops);
                document.getElementById("period").textContent = month;
                document.getElementById("count").textContent = data.point_count + " map points, " + data.stops.length + " stops";
                const [year, monthNumber] = month.split("-").map(Number);
                startInput.value = month + "-01";
                endInput.value = month + "-" + String(new Date(year, monthNumber, 0).getDate()).padStart(2, "0");
                document.getElementById("loading").textContent = data.point_count ? "" : "No route in this month";
            }} catch (error) {{
                document.getElementById("loading").textContent = "Error: " + error.message;
            }}
        }}
        async function loadRange() {{
            const start = startInput.value;
            const end = endInput.value;
            if (!start || !end || start > end) {{
                document.getElementById("loading").textContent = "Select a valid period";
                return;
            }}
            document.getElementById("loading").textContent = "Loading period...";
            try {{
                const query = new URLSearchParams({{start, end}});
                const response = await fetch(routeEndpoint + "&" + query);
                if (!response.ok) throw new Error(await response.text() || "HTTP " + response.status);
                const data = await response.json();
                showRoute(data.points, data.stops);
                document.getElementById("period").textContent = start + " bis " + end;
                document.getElementById("count").textContent = data.point_count + " map points, " + data.stops.length + " stops";
                document.getElementById("loading").textContent = data.point_count ? "" : "No route in this period";
            }} catch (error) {{
                document.getElementById("loading").textContent = "Error: " + error.message;
            }}
        }}
        function changeMonth(offset) {{
            const [year, month] = monthInput.value.split("-").map(Number);
            const date = new Date(year, month - 1 + offset, 1);
            monthInput.value = date.getFullYear() + "-" + String(date.getMonth() + 1).padStart(2, "0");
            loadMonth();
        }}
        monthInput.addEventListener("change", loadMonth);
        document.getElementById("previous").addEventListener("click", () => changeMonth(-1));
        document.getElementById("next").addEventListener("click", () => changeMonth(1));
        document.getElementById("show-range").addEventListener("click", loadRange);
          showPoints.addEventListener("change", renderTrackPoints);
  </script>
</body>
</html>
"""
    return page
