"""Ollama Monitor: see what your Ollama hosts are up to, from Home Assistant."""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.const import CONF_URL, CONF_VERIFY_SSL, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import OllamaClient
from .const import CARD_FILENAME, CARD_URL_BASE, CONF_API_KEY, DOMAIN, VERSION
from .coordinator import OllamaMonitorConfigEntry, OllamaMonitorCoordinator
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

_CARD_REGISTERED = f"{DOMAIN}_card_registered"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register actions and serve the dashboard card."""
    async_setup_services(hass)
    await _async_register_card(hass)
    return True


async def _async_register_card(hass: HomeAssistant) -> None:
    """Serve the bundled Lovelace card and auto-load it on every dashboard."""
    if hass.data.get(_CARD_REGISTERED) or hass.http is None:
        return
    from homeassistant.components.http import StaticPathConfig

    www = Path(__file__).parent / "www"
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL_BASE, str(www), cache_headers=False)]
    )
    if "frontend" in hass.config.components:
        from homeassistant.components.frontend import add_extra_js_url

        add_extra_js_url(hass, f"{CARD_URL_BASE}/{CARD_FILENAME}?v={VERSION}")
    hass.data[_CARD_REGISTERED] = True


async def async_setup_entry(hass: HomeAssistant, entry: OllamaMonitorConfigEntry) -> bool:
    """Set up one Ollama host."""
    session = async_get_clientsession(hass, verify_ssl=entry.data.get(CONF_VERIFY_SSL, True))
    client = OllamaClient(session, entry.data[CONF_URL], entry.data.get(CONF_API_KEY))
    coordinator = OllamaMonitorCoordinator(hass, entry, client)
    # Offline hosts don't fail setup: they show up with online=off and recover on their own.
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    @callback
    def _sync_sw_version() -> None:
        """Keep the device's firmware field in step with Ollama upgrades."""
        version = coordinator.data.version if coordinator.data else None
        if not version:
            return
        dev_reg = dr.async_get(hass)
        for device in dr.async_entries_for_config_entry(dev_reg, entry.entry_id):
            if device.sw_version != version:
                dev_reg.async_update_device(device.id, sw_version=version)

    entry.async_on_unload(coordinator.async_add_listener(_sync_sw_version))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: OllamaMonitorConfigEntry) -> bool:
    """Tear down one host."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
