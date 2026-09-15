"""Access control and period parsing for the route endpoints.

The authorisation check is the only thing between a public URL and the
vehicle's movement history, so it gets the most attention here.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from aiohttp import web

from onntrack.http import _authorized, _month_chunks, _next_month, _route_period

TOKEN = "s3cret-token"


class FakeRequest:
    """Enough of aiohttp's request for the authorisation check."""

    def __init__(self, query=None, authenticated=False):
        self.query = query or {}
        self._state = {"ha_authenticated": True} if authenticated else {}

    def get(self, key, default=None):
        return self._state.get(key, default)


class TestAuthorisation:
    def test_a_logged_in_user_gets_through_without_a_token(self):
        assert _authorized(FakeRequest(authenticated=True), {"token": TOKEN}) is True

    def test_the_right_token_gets_through(self):
        assert _authorized(FakeRequest({"token": TOKEN}), {"token": TOKEN}) is True

    @pytest.mark.parametrize(
        "supplied",
        ["", "wrong", TOKEN + "x", TOKEN[:-1], TOKEN.upper(), " " + TOKEN],
    )
    def test_anything_else_is_refused(self, supplied):
        assert _authorized(FakeRequest({"token": supplied}), {"token": TOKEN}) is False

    def test_a_missing_token_parameter_is_refused(self):
        assert _authorized(FakeRequest(), {"token": TOKEN}) is False

    @pytest.mark.parametrize("stored", [None, "", {}])
    def test_a_route_without_a_token_cannot_be_opened(self, stored):
        # An empty stored token must never be satisfiable by an empty query
        # parameter -- that would reopen the hole this check exists to close.
        route = {} if stored == {} else {"token": stored}
        assert _authorized(FakeRequest({"token": ""}), route) is False
        assert _authorized(FakeRequest(), route) is False


class TestPeriod:
    def test_a_month(self):
        start, end, label = _route_period({"month": "2026-09"}, "Europe/Vienna")
        assert (start.year, start.month, start.day) == (2026, 9, 1)
        assert (end.year, end.month, end.day) == (2026, 10, 1)
        assert label == "2026-09"

    def test_december_rolls_into_january(self):
        start, end, _ = _route_period({"month": "2026-12"}, "Europe/Vienna")
        assert (end.year, end.month) == (2027, 1)

    def test_an_explicit_range_includes_the_final_day(self):
        start, end, label = _route_period(
            {"start": "2026-09-01", "end": "2026-09-14"}, "Europe/Vienna"
        )
        assert end - start == timedelta(days=14)
        assert label == "2026-09-01 to 2026-09-14"

    def test_the_period_is_in_the_configured_zone(self):
        start, _, _ = _route_period({"month": "2026-09"}, "Europe/Vienna")
        assert start.utcoffset() == timedelta(hours=2)

    @pytest.mark.parametrize(
        "query",
        [
            {},
            {"month": ""},
            {"month": "2026-13"},
            {"month": "September 2026"},
            {"month": "1999-01"},
            {"month": "2200-01"},
            {"start": "2026-09-01"},
            {"end": "2026-09-01"},
            {"start": "2026-09-01", "end": "not-a-date"},
            {"start": "2026-09-14", "end": "2026-09-01"},
            {"start": "2020-01-01", "end": "2026-01-01"},
        ],
    )
    def test_unusable_periods_are_rejected(self, query):
        with pytest.raises(web.HTTPBadRequest):
            _route_period(query, "Europe/Vienna")

    def test_the_same_day_twice_means_that_one_day(self):
        start, end, _ = _route_period(
            {"start": "2026-09-01", "end": "2026-09-01"}, "Europe/Vienna"
        )
        assert end - start == timedelta(days=1)


class TestChunking:
    def test_a_long_range_is_split_per_month(self):
        zone = _route_period({"start": "2026-01-15", "end": "2026-03-20"}, "UTC")
        chunks = _month_chunks(zone[0], zone[1])
        assert len(chunks) == 3
        assert chunks[0][1] == chunks[1][0], "chunks must not leave a gap"
        assert chunks[-1][1] == zone[1]

    def test_a_short_range_stays_in_one_piece(self):
        start, end, _ = _route_period({"start": "2026-09-01", "end": "2026-09-05"}, "UTC")
        assert len(_month_chunks(start, end)) == 1

    def test_next_month_crosses_the_year(self):
        assert _next_month(datetime(2026, 12, 9)).year == 2027
        assert _next_month(datetime(2026, 12, 9)).month == 1
