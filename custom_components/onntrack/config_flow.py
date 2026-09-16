from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import OnntrackApi, OnntrackApiError, OnntrackAuthError
from .const import (
    CONF_BASE_URL,
    CONF_PASSWORD,
    CONF_REVERSE_GEOCODE,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
    DEFAULT_BASE_URL,
    DEFAULT_REVERSE_GEOCODE,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)

PASSWORD_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


def _normalize_base_url(value: str) -> str:
    base_url = value.strip().rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("invalid_url")
    return base_url


async def _async_validate(hass, base_url: str, username: str, password: str) -> None:
    """Sign in once, so bad credentials surface here instead of at setup."""
    if not username or not password:
        raise OnntrackAuthError("Username and password are required")
    api = OnntrackApi(async_get_clientsession(hass), base_url, username, password)
    await api.async_get_devices()


class OnntrackConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                base_url = _normalize_base_url(user_input[CONF_BASE_URL])
                username = user_input[CONF_USERNAME].strip()
                await _async_validate(self.hass, base_url, username, user_input[CONF_PASSWORD])
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
                    vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR,
                }
            ),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]):
        """Entry point when the portal stops accepting the stored password.

        Without this the integration simply stays broken until someone deletes
        and re-adds it.
        """
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict | None = None):
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await _async_validate(
                    self.hass,
                    entry.data[CONF_BASE_URL],
                    entry.data[CONF_USERNAME],
                    user_input[CONF_PASSWORD],
                )
            except OnntrackAuthError:
                errors["base"] = "invalid_auth"
            except OnntrackApiError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry, data={**entry.data, CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            description_placeholders={"username": entry.data[CONF_USERNAME]},
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return OnntrackOptionsFlowHandler()


class OnntrackOptionsFlowHandler(config_entries.OptionsFlow):
    async def async_step_init(self, user_input: dict | None = None):
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                    CONF_REVERSE_GEOCODE: user_input[CONF_REVERSE_GEOCODE],
                }
            )

        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_SCAN_INTERVAL,
                            max=MAX_SCAN_INTERVAL,
                            step=10,
                            unit_of_measurement="s",
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required(
                        CONF_REVERSE_GEOCODE,
                        default=options.get(CONF_REVERSE_GEOCODE, DEFAULT_REVERSE_GEOCODE),
                    ): BooleanSelector(),
                }
            ),
        )
