"""Run the integration's modules without a Home Assistant installation.

The modules under test need only a handful of names from ``homeassistant`` and
``aiohttp``, so both are stubbed here. That keeps the suite runnable with
nothing but pytest: no Home Assistant checkout, no network, no event loop
plugin.

The package is assembled by hand rather than imported normally, because
``custom_components/onntrack/__init__.py`` pulls in the full Home Assistant
config-entry machinery. Giving a synthetic ``onntrack`` module a ``__path__``
lets the submodules be imported on their own, with their relative imports
intact.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

INTEGRATION = Path(__file__).resolve().parent.parent / "custom_components" / "onntrack"


def _stub_homeassistant() -> None:
    homeassistant = types.ModuleType("homeassistant")
    const = types.ModuleType("homeassistant.const")

    class Platform:
        DEVICE_TRACKER = "device_tracker"
        SENSOR = "sensor"

    const.Platform = Platform

    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = type("HomeAssistant", (), {})

    components = types.ModuleType("homeassistant.components")
    http = types.ModuleType("homeassistant.components.http")

    class HomeAssistantView:
        requires_auth = True

        def json(self, data):
            return data

    http.HomeAssistantView = HomeAssistantView

    http_const = types.ModuleType("homeassistant.components.http.const")
    http_const.KEY_AUTHENTICATED = "ha_authenticated"

    # A faithful stand-in for Home Assistant's redaction helper: it replaces
    # the values of matching keys and recurses. Testing the diagnostics module
    # against a fake that did less would prove nothing.
    diagnostics = types.ModuleType("homeassistant.components.diagnostics")
    redacted = "**REDACTED**"

    def async_redact_data(data, to_redact):
        if isinstance(data, dict):
            return {
                key: (redacted if key in to_redact else async_redact_data(value, to_redact))
                for key, value in data.items()
            }
        if isinstance(data, list):
            return [async_redact_data(item, to_redact) for item in data]
        return data

    diagnostics.async_redact_data = async_redact_data
    diagnostics.REDACTED = redacted

    config_entries = types.ModuleType("homeassistant.config_entries")
    config_entries.ConfigEntry = type("ConfigEntry", (), {})

    sys.modules.update(
        {
            "homeassistant": homeassistant,
            "homeassistant.const": const,
            "homeassistant.core": core,
            "homeassistant.components": components,
            "homeassistant.components.http": http,
            "homeassistant.components.http.const": http_const,
            "homeassistant.components.diagnostics": diagnostics,
            "homeassistant.config_entries": config_entries,
        }
    )


def _stub_aiohttp() -> None:
    aiohttp = types.ModuleType("aiohttp")

    class ClientError(Exception):
        pass

    class ClientTimeout:
        def __init__(self, total=None):
            self.total = total

    aiohttp.ClientError = ClientError
    aiohttp.ClientTimeout = ClientTimeout
    aiohttp.ContentTypeError = type("ContentTypeError", (ClientError,), {})
    aiohttp.ClientSession = type("ClientSession", (), {})

    web = types.ModuleType("aiohttp.web")

    class HTTPException(Exception):
        status = 0

        def __init__(self, text=None):
            super().__init__(text)
            self.text = text

    web.Request = type("Request", (), {})
    web.Response = type("Response", (), {})
    web.HTTPException = HTTPException
    web.HTTPBadRequest = type("HTTPBadRequest", (HTTPException,), {"status": 400})
    web.HTTPUnauthorized = type("HTTPUnauthorized", (HTTPException,), {"status": 401})
    web.HTTPNotFound = type("HTTPNotFound", (HTTPException,), {"status": 404})
    aiohttp.web = web

    sys.modules["aiohttp"] = aiohttp
    sys.modules["aiohttp.web"] = web


def _install_package() -> None:
    package = types.ModuleType("onntrack")
    package.__path__ = [str(INTEGRATION)]
    sys.modules["onntrack"] = package


_stub_homeassistant()
_stub_aiohttp()
_install_package()
