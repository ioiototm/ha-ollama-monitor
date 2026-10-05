"""Diagnostics download for an Ollama host."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_API_KEY
from .coordinator import OllamaMonitorConfigEntry

TO_REDACT = {CONF_API_KEY}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: OllamaMonitorConfigEntry
) -> dict[str, Any]:
    """Entry config plus the last poll."""
    data = entry.runtime_data.data
    return {
        "entry": {
            "title": entry.title,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "status": {
            "online": data.online,
            "version": data.version,
            "loaded": [m.as_attr() for m in data.loaded],
            "installed": [m.as_attr() for m in data.installed],
        }
        if data
        else None,
    }
