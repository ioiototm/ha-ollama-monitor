"""load_model / unload_model actions."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr
import voluptuous as vol

from .api import OllamaConnectionError, OllamaError, OllamaResponseError, parse_keep_alive
from .const import (
    ATTR_CONFIG_ENTRY_ID,
    ATTR_DEVICE_ID,
    CONF_KEEP_ALIVE,
    CONF_LINKED_DEVICE,
    CONF_MODEL,
    DOMAIN,
    SERVICE_LOAD_MODEL,
    SERVICE_UNLOAD_MODEL,
)
from .coordinator import OllamaMonitorConfigEntry, OllamaMonitorCoordinator

_TARGET = {
    vol.Optional(ATTR_DEVICE_ID): vol.All(cv.ensure_list, [cv.string]),
    vol.Optional(ATTR_CONFIG_ENTRY_ID): vol.All(cv.ensure_list, [cv.string]),
}

LOAD_SCHEMA = vol.Schema(
    {
        **_TARGET,
        vol.Required(CONF_MODEL): vol.All(cv.string, vol.Length(min=1)),
        vol.Optional(CONF_KEEP_ALIVE): vol.Any(None, cv.string, vol.Coerce(float)),
    }
)

UNLOAD_SCHEMA = vol.Schema(
    {
        **_TARGET,
        vol.Optional(CONF_MODEL): vol.Any(None, cv.string),
    }
)


def _loaded_entries(hass: HomeAssistant) -> list[OllamaMonitorConfigEntry]:
    return [e for e in hass.config_entries.async_entries(DOMAIN) if e.state is ConfigEntryState.LOADED]


@callback
def _resolve_coordinators(hass: HomeAssistant, call: ServiceCall) -> list[OllamaMonitorCoordinator]:
    """Turn device_id / config_entry_id (or nothing, if there's one host) into coordinators."""
    entry_ids: list[str] = list(call.data.get(ATTR_CONFIG_ENTRY_ID, []))
    device_ids: list[str] = call.data.get(ATTR_DEVICE_ID, [])

    if device_ids:
        dev_reg = dr.async_get(hass)
        for device_id in device_ids:
            device = dev_reg.async_get(device_id)
            ours = [
                eid
                for eid in (device.config_entries if device else ())
                if (e := hass.config_entries.async_get_entry(eid)) and e.domain == DOMAIN
            ]
            # Hosts shown under someone else's device (e.g. the desktop's own device).
            ours += [
                e.entry_id
                for e in hass.config_entries.async_entries(DOMAIN)
                if e.options.get(CONF_LINKED_DEVICE) == device_id
            ]
            if not ours:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="device_not_found",
                    translation_placeholders={"device_id": device_id},
                )
            entry_ids.extend(ours)

    if not entry_ids:
        loaded = _loaded_entries(hass)
        if len(loaded) == 1:
            return [loaded[0].runtime_data]
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_target")

    coordinators: list[OllamaMonitorCoordinator] = []
    for entry_id in dict.fromkeys(entry_ids):
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN or entry.state is not ConfigEntryState.LOADED:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="entry_not_loaded",
                translation_placeholders={"entry_id": entry_id},
            )
        coordinators.append(entry.runtime_data)
    return coordinators


def _raise_for(coordinator: OllamaMonitorCoordinator, action: str, err: OllamaError) -> None:
    key = "host_offline" if isinstance(err, OllamaConnectionError) else "request_failed"
    # 404 means Ollama doesn't have that model: the caller's mistake, not a failure.
    error_cls = (
        ServiceValidationError
        if isinstance(err, OllamaResponseError) and err.status == 404
        else HomeAssistantError
    )
    raise error_cls(
        translation_domain=DOMAIN,
        translation_key=key,
        translation_placeholders={
            "host": coordinator.config_entry.title,
            "action": action,
            "error": str(err),
        },
    ) from err


async def async_load(coordinator: OllamaMonitorCoordinator, model: str, keep_alive: int | str | None) -> None:
    """Load a model on one host and refresh its state."""
    try:
        await coordinator.client.load(model, keep_alive)
    except OllamaError as err:
        _raise_for(coordinator, f"load {model}", err)
    finally:
        await coordinator.async_refresh()


async def async_unload(coordinator: OllamaMonitorCoordinator, model: str | None) -> None:
    """Unload one model (or, with None, everything) on one host."""
    try:
        names = [model] if model else [m.name for m in await coordinator.client.ps()]
        for name in names:
            await coordinator.client.unload(name)
    except OllamaError as err:
        _raise_for(coordinator, f"unload {model or 'all models'}", err)
    finally:
        await coordinator.async_refresh()


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration's actions (once, not per host)."""

    async def handle_load(call: ServiceCall) -> None:
        try:
            keep_alive = parse_keep_alive(call.data.get(CONF_KEEP_ALIVE))
        except ValueError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_keep_alive",
                translation_placeholders={"value": str(call.data.get(CONF_KEEP_ALIVE))},
            ) from err
        if keep_alive == 0:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="keep_alive_zero",
            )
        for coordinator in _resolve_coordinators(hass, call):
            await async_load(coordinator, call.data[CONF_MODEL], keep_alive)

    async def handle_unload(call: ServiceCall) -> None:
        for coordinator in _resolve_coordinators(hass, call):
            await async_unload(coordinator, call.data.get(CONF_MODEL) or None)

    hass.services.async_register(DOMAIN, SERVICE_LOAD_MODEL, handle_load, schema=LOAD_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_UNLOAD_MODEL, handle_unload, schema=UNLOAD_SCHEMA)
