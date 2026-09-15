"""Reverse geocoding: caching, throttling and backing off.

Nominatim's usage policy is the reason this code exists in the shape it does.
An integration that ignores it gets the whole integration blocked, not the one
user who caused it, so these rules are worth pinning down.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from onntrack import api as api_module
from onntrack.api import OnntrackApi


def run(coroutine):
    return asyncio.run(coroutine)


class FakeResponse:
    def __init__(self, status: int, payload: dict):
        self.status = status
        self._payload = payload

    async def json(self, content_type=None):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


class FakeSession:
    """Records the requests the geocoder makes, answers from a script."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params, "headers": headers})
        if not self.responses:
            raise AssertionError("unexpected extra request to " + url)
        return self.responses.pop(0)


class Boom(FakeResponse):
    """A response object whose use raises the way aiohttp would."""

    async def __aenter__(self):
        raise api_module.aiohttp.ClientError("connection reset")


@pytest.fixture(autouse=True)
def _no_rate_limit_wait():
    """Start every test with the process-wide slot free."""
    api_module._NOMINATIM_LAST_REQUEST = time.monotonic() - 60
    yield


def build(session, cache=None):
    saves = []
    client = OnntrackApi(
        session,
        "https://portal.example",
        "user",
        "password",
        address_cache={} if cache is None else cache,
        cache_changed=lambda: saves.append(1),
    )
    return client, saves


class TestLookup:
    def test_a_hit_is_returned_cached_and_persisted(self):
        session = FakeSession(FakeResponse(200, {"display_name": "Gewerbestrasse 1"}))
        client, saves = build(session)

        assert run(client._async_reverse_geocode(48.3355, 16.4547)) == "Gewerbestrasse 1"
        assert len(session.calls) == 1
        assert client._address_cache == {"48.3355,16.4547": "Gewerbestrasse 1"}
        assert saves == [1], "the caller must be told to write the cache to disk"

    def test_the_user_agent_identifies_the_integration(self):
        session = FakeSession(FakeResponse(200, {"display_name": "somewhere"}))
        client, _ = build(session)
        run(client._async_reverse_geocode(48.0, 16.0))

        agent = session.calls[0]["headers"]["User-Agent"]
        assert api_module.VERSION in agent
        assert "github.com" in agent, "the policy asks for a contact address"

    def test_nearby_coordinates_share_a_cache_entry(self):
        session = FakeSession(FakeResponse(200, {"display_name": "Gewerbestrasse 1"}))
        client, _ = build(session)
        run(client._async_reverse_geocode(48.3355, 16.4547))

        # Roughly eleven metres away: the same address, and no second request.
        assert run(client._async_reverse_geocode(48.33551, 16.45474)) == "Gewerbestrasse 1"
        assert len(session.calls) == 1

    def test_a_cache_loaded_from_storage_is_used(self):
        session = FakeSession()
        client, _ = build(session, {"48.2000,16.3700": "Wien, Stephansplatz"})

        assert run(client._async_reverse_geocode(48.2, 16.37)) == "Wien, Stephansplatz"
        assert session.calls == []

    def test_an_empty_answer_is_not_cached(self):
        session = FakeSession(FakeResponse(200, {}))
        client, saves = build(session)

        assert run(client._async_reverse_geocode(48.0, 16.0)) is None
        assert client._address_cache == {}
        assert saves == []

    def test_a_network_error_is_swallowed(self):
        session = FakeSession(Boom(0, {}))
        client, _ = build(session)

        assert run(client._async_reverse_geocode(48.0, 16.0)) is None


class TestThrottling:
    def test_a_second_uncached_lookup_is_held_back(self):
        session = FakeSession(FakeResponse(200, {"display_name": "first"}))
        client, _ = build(session)
        run(client._async_reverse_geocode(48.0, 16.0))

        # A driving vehicle reports a new position every minute. Without this
        # the integration would poll Nominatim for the whole trip.
        assert run(client._async_reverse_geocode(49.0, 17.0)) is None
        assert len(session.calls) == 1

    def test_the_interval_is_two_minutes(self):
        assert api_module.NOMINATIM_LOOKUP_INTERVAL == 120.0

    def test_requests_are_a_second_apart(self):
        started = time.monotonic()
        run(self._two_slots())
        assert time.monotonic() - started >= 1.0

    async def _two_slots(self):
        await api_module._nominatim_slot()
        await api_module._nominatim_slot()


class TestBackOff:
    @pytest.mark.parametrize("status", [403, 429])
    def test_a_rejection_stops_the_geocoder_for_an_hour(self, status):
        session = FakeSession(FakeResponse(status, {}))
        client, saves = build(session)

        assert run(client._async_reverse_geocode(48.0, 16.0)) is None
        assert saves == []
        remaining = client._blocked_until - time.monotonic()
        assert 3500 < remaining < 3700

    def test_nothing_is_tried_while_blocked(self):
        session = FakeSession(FakeResponse(429, {}))
        client, _ = build(session)
        run(client._async_reverse_geocode(48.0, 16.0))

        client._next_lookup = 0  # only the hour-long block should hold now
        assert run(client._async_reverse_geocode(48.5, 16.5)) is None
        assert len(session.calls) == 1


class TestCacheSize:
    def test_the_oldest_entry_makes_room(self):
        limit = api_module.NOMINATIM_CACHE_LIMIT
        cache = {f"{index}.0000,0.0000": f"Address {index}" for index in range(limit)}
        session = FakeSession(FakeResponse(200, {"display_name": "new"}))
        client, _ = build(session, cache)

        run(client._async_reverse_geocode(9.9, 9.9))

        assert len(cache) == limit
        assert "0.0000,0.0000" not in cache
        assert cache["9.9000,9.9000"] == "new"
