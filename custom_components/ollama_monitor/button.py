"""Buttons: free up the GPU in one press."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import OllamaMonitorConfigEntry, OllamaMonitorCoordinator
from .entity import OllamaMonitorEntity
from .services import async_unload

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OllamaMonitorConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add buttons for this host."""
    async_add_entities([OllamaUnloadAllButton(entry.runtime_data)])


class OllamaUnloadAllButton(OllamaMonitorEntity, ButtonEntity):
    """Evict every loaded model."""

    _attr_translation_key = "unload_all"

    def __init__(self, coordinator: OllamaMonitorCoordinator) -> None:
        """Set up."""
        super().__init__(coordinator, "unload_all")

    async def async_press(self) -> None:
        """Unload everything that's loaded right now."""
        await async_unload(self.coordinator, None)
