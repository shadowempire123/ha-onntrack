from __future__ import annotations

from urllib.parse import urlparse

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import OnntrackApi, OnntrackApiError, OnntrackAuthError
from .const import CONF_BASE_URL, CONF_PASSWORD, CONF_USERNAME, DEFAULT_BASE_URL, DOMAIN


def _normalize_base_url(value: str) -> str:
    base_url = value.strip().rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("invalid_url")
    return base_url


class OnntrackConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                base_url = _normalize_base_url(user_input[CONF_BASE_URL])
                username = user_input[CONF_USERNAME].strip()
                if not username or not user_input[CONF_PASSWORD]:
                    raise OnntrackAuthError("Username and password are required")
                session = async_get_clientsession(self.hass)
                api = OnntrackApi(session, base_url, username, user_input[CONF_PASSWORD])
                await api.async_get_devices()
            except ValueError:
                errors["base"] = "invalid_url"
            except OnntrackAuthError:
                errors["base"] = "invalid_auth"
            except OnntrackApiError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(f"{base_url.casefold()}::{username.casefold()}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=username,
                    data={
                        CONF_BASE_URL: base_url,
                        CONF_USERNAME: username,
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_BASE_URL, default=DEFAULT_BASE_URL): str,
                    vol.Required(CONF_USERNAME): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return OnntrackOptionsFlowHandler()


class OnntrackOptionsFlowHandler(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        return self.async_create_entry(title="", data={})
