"""Config flow: one entry per Ollama host."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_NAME, CONF_URL, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import voluptuous as vol
from yarl import URL

from .api import OllamaAuthError, OllamaClient, OllamaConnectionError, OllamaError, normalize_url
from .const import (
    CONF_API_KEY,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


def _host_schema(defaults: dict[str, Any], *, with_name: bool) -> vol.Schema:
    fields: dict[Any, Any] = {
        vol.Required(CONF_URL, default=defaults.get(CONF_URL, "")): TextSelector(
            TextSelectorConfig(type=TextSelectorType.URL)
        ),
    }
    if with_name:
        fields[vol.Optional(CONF_NAME, description={"suggested_value": defaults.get(CONF_NAME)})] = (
            TextSelector()
        )
    fields[vol.Optional(CONF_API_KEY, description={"suggested_value": defaults.get(CONF_API_KEY)})] = (
        TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
    )
    fields[vol.Optional(CONF_VERIFY_SSL, default=defaults.get(CONF_VERIFY_SSL, True))] = bool
    return vol.Schema(fields)


async def _validate(hass: HomeAssistant, data: dict[str, Any]) -> tuple[str, str]:
    """Return (normalised url, version) or raise."""
    url = normalize_url(data[CONF_URL])
    session = async_get_clientsession(hass, verify_ssl=data.get(CONF_VERIFY_SSL, True))
    client = OllamaClient(session, url, data.get(CONF_API_KEY))
    version = await client.version()
    return url, version


def _default_name(url: str) -> str:
    host = URL(url).host or url
    # "ollama.desktop.home.example.com" → "Desktop"; "192.168.1.20" stays as is.
    parts = host.split(".")
    if not host.replace(".", "").isdigit() and len(parts) > 1:
        candidates = [p for p in parts if p not in ("ollama", "www", "local", "lan", "lab", "home")]
        if candidates:
            return candidates[0].capitalize()
    return host


class OllamaMonitorConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add an Ollama host."""

    VERSION = 1

    async def _try(self, user_input: dict[str, Any], errors: dict[str, str]) -> tuple[str, str] | None:
        try:
            return await _validate(self.hass, user_input)
        except ValueError:
            errors[CONF_URL] = "invalid_url"
        except OllamaAuthError:
            errors["base"] = "invalid_auth"
        except OllamaConnectionError:
            errors["base"] = "cannot_connect"
        except OllamaError:
            errors["base"] = "not_ollama"
        except Exception:
            _LOGGER.exception("Unexpected error validating Ollama host")
            errors["base"] = "unknown"
        return None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the URL (and optional name / key)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            result = await self._try(user_input, errors)
            if result:
                url, _version = result
                await self.async_set_unique_id(url.lower())
                self._abort_if_unique_id_configured()
                name = (user_input.get(CONF_NAME) or "").strip() or _default_name(url)
                return self.async_create_entry(
                    title=name,
                    data={
                        CONF_URL: url,
                        CONF_API_KEY: (user_input.get(CONF_API_KEY) or "").strip() or None,
                        CONF_VERIFY_SSL: user_input.get(CONF_VERIFY_SSL, True),
                    },
                )
        return self.async_show_form(
            step_id="user",
            data_schema=_host_schema(user_input or {}, with_name=True),
            errors=errors,
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Change URL / key without losing entities."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            result = await self._try(user_input, errors)
            if result:
                url, _version = result
                if any(
                    other.entry_id != entry.entry_id and other.unique_id == url.lower()
                    for other in self._async_current_entries(include_ignore=False)
                ):
                    return self.async_abort(reason="already_configured")
                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=url.lower(),
                    data_updates={
                        CONF_URL: url,
                        CONF_API_KEY: (user_input.get(CONF_API_KEY) or "").strip() or None,
                        CONF_VERIFY_SSL: user_input.get(CONF_VERIFY_SSL, True),
                    },
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_host_schema(user_input or dict(entry.data), with_name=False),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """The proxy started rejecting us."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for a new API key."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**entry.data, CONF_API_KEY: user_input.get(CONF_API_KEY)}
            if await self._try(data, errors):
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_API_KEY: (user_input.get(CONF_API_KEY) or "").strip() or None}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {vol.Optional(CONF_API_KEY): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))}
            ),
            description_placeholders={"name": entry.title},
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OllamaMonitorOptionsFlow:
        """Options: polling interval."""
        return OllamaMonitorOptionsFlow()


class OllamaMonitorOptionsFlow(OptionsFlowWithReload):
    """Polling interval."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show options."""
        if user_input is not None:
            return self.async_create_entry(data={CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL])})
        current = self.config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SCAN_INTERVAL, default=current): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_SCAN_INTERVAL,
                            max=MAX_SCAN_INTERVAL,
                            step=1,
                            unit_of_measurement="s",
                            mode=NumberSelectorMode.BOX,
                        )
                    )
                }
            ),
        )
