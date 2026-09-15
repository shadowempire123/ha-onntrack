"""Parsing, stop detection and page rendering."""

from __future__ import annotations

import json

import pytest

from onntrack.route_map import (
    RoutePoint,
    build_route_geojson,
    build_route_page,
    detect_stops,
    parse_route_point,
    parse_route_points,
    route_geometry,
)


def pipe(timestamp: str, latitude: str, longitude: str) -> str:
    """Build a portal record in the pipe-separated form the API returns."""
    return "|".join(["x"] * 7 + [timestamp, latitude, longitude])


class TestParsing:
    def test_pipe_record(self):
        point = parse_route_point(pipe("2026-09-01 10:00:00", "48.2", "16.37"))
        assert point == RoutePoint("2026-09-01 10:00:00", 48.2, 16.37)

    @pytest.mark.parametrize(
        "payload",
        [
            {"positioningTime": "2026-09-01 10:00:00", "latitude": 48.2, "longitude": 16.37},
            {"gpsTime": "2026-09-01 10:00:00", "lat": 48.2, "lng": 16.37},
            {"time": "2026-09-01 10:00:00", "lat": "48.2", "lon": "16.37"},
        ],
    )
    def test_dict_records(self, payload):
        point = parse_route_point(payload)
        assert point is not None
        assert (point.latitude, point.longitude) == (48.2, 16.37)

    @pytest.mark.parametrize(
        "payload",
        [
            "too|few|fields",
            pipe("2026-09-01 10:00:00", "not-a-number", "16.37"),
            pipe("2026-09-01 10:00:00", "91.0", "16.37"),
            pipe("2026-09-01 10:00:00", "48.2", "181.0"),
            None,
            42,
        ],
    )
    def test_unusable_records_are_dropped(self, payload):
        assert parse_route_point(payload) is None

    def test_parse_route_points_skips_the_unusable(self):
        points = parse_route_points(
            [pipe("2026-09-01 10:00:00", "48.2", "16.37"), "broken", None]
        )
        assert len(points) == 1


class TestGeometry:
    def test_repeated_positions_collapse(self):
        points = [
            RoutePoint("2026-09-01 10:00:00", 48.2, 16.37),
            RoutePoint("2026-09-01 10:01:00", 48.2, 16.37),
            RoutePoint("2026-09-01 10:02:00", 48.3, 16.40),
        ]
        assert len(route_geometry(points)) == 2

    def test_a_moving_vehicle_keeps_every_position(self):
        points = [
            RoutePoint(f"2026-09-01 10:0{i}:00", 48.2 + i / 100, 16.37) for i in range(5)
        ]
        assert len(route_geometry(points)) == 5


class TestStopDetection:
    def test_standing_still_long_enough_is_a_stop(self):
        points = [
            RoutePoint("2026-09-01 10:00:00", 48.2, 16.37),
            RoutePoint("2026-09-01 10:15:00", 48.2, 16.37),
            RoutePoint("2026-09-01 10:30:00", 48.2, 16.37),
        ]
        stops = detect_stops(points)
        assert len(stops) == 1
        assert stops[0].duration_minutes == 30
        assert stops[0].duration_seconds == 1800

    def test_a_short_halt_is_not_a_stop(self):
        points = [
            RoutePoint("2026-09-01 10:00:00", 48.2, 16.37),
            RoutePoint("2026-09-01 10:10:00", 48.2, 16.37),
        ]
        assert detect_stops(points) == []

    def test_driving_through_is_not_a_stop(self):
        points = [
            RoutePoint(f"2026-09-01 1{i}:00:00", 48.2 + i / 10, 16.37) for i in range(5)
        ]
        assert detect_stops(points) == []

    def test_unparsable_timestamps_do_not_raise(self):
        points = [
            RoutePoint("nonsense", 48.2, 16.37),
            RoutePoint("2026-09-01 10:30:00", 48.2, 16.37),
        ]
        assert detect_stops(points) == []


class TestGeoJson:
    def test_structure(self):
        points = [
            RoutePoint("2026-09-01 10:00:00", 48.2, 16.37),
            RoutePoint("2026-09-01 10:30:00", 48.2, 16.37),
            RoutePoint("2026-09-01 11:00:00", 48.5, 16.60),
        ]
        result = build_route_geojson("123", "Camper", "2026-09-01", "2026-09-02", points)
        assert result["type"] == "FeatureCollection"
        line = result["features"][0]
        assert line["geometry"]["type"] == "LineString"
        # GeoJSON is longitude first -- swapping this silently puts every route
        # in the wrong hemisphere.
        assert line["geometry"]["coordinates"][0] == [16.37, 48.2]
        assert line["properties"]["imei"] == "123"
        assert all(f["geometry"]["type"] == "Point" for f in result["features"][1:])

    def test_it_serialises(self):
        points = [RoutePoint("2026-09-01 10:00:00", 48.2, 16.37)]
        json.dumps(build_route_geojson("123", "Camper", "a", "b", points))


class TestPage:
    def render(self, **kwargs):
        defaults = {
            "map_id": "abc123",
            "token": "s3cret",
            "device_name": "Camper",
            "start": "2026-09-01T00:00:00",
            "end": "2026-09-15T00:00:00",
        }
        return build_route_page(**{**defaults, **kwargs})

    def test_no_coordinates_are_baked_into_the_page(self):
        # The whole point of serving the page from a view: it must not become a
        # standalone copy of the vehicle's track.
        page = self.render()
        assert "const initialPoints = [];" in page
        assert "const initialStops = [];" in page

    def test_the_endpoint_carries_the_token(self):
        page = self.render()
        assert '/api/onntrack/route/abc123?token=s3cret' in page

    def test_token_is_url_encoded(self):
        page = self.render(token="a+b/c=d")
        assert "token=a%2Bb%2Fc%3Dd" in page
        assert "token=a+b/c=d" not in page

    def test_map_id_is_sanitised(self):
        page = self.render(map_id="../../etc/passwd")
        assert "/api/onntrack/route/______etc_passwd?" in page
        assert ".." not in page.split("routeEndpoint")[1][:120]

    def test_device_name_is_escaped(self):
        page = self.render(device_name='<script>alert("x")</script>')
        assert "<script>alert" not in page
        assert "&lt;script&gt;" in page

    def test_initial_inputs_come_from_the_period(self):
        page = self.render()
        assert 'id="month" type="month" value="2026-09"' in page
        assert 'id="start" type="date" value="2026-09-01"' in page
        assert 'id="end" type="date" value="2026-09-15"' in page

    def test_zoom_control_avoids_the_waypoint_panel(self):
        page = self.render()
        assert 'L.map("map", {zoomControl: false})' in page
        assert 'L.control.zoom({position: "bottomright"})' in page
