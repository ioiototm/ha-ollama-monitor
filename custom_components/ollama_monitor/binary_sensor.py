"""Binary sensors: is the host up, is anything loaded."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import OllamaMonitorConfigEntry, OllamaMonitorCoordinator
from .entity import OllamaMonitorEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OllamaMonitorConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add binary sensors for this host."""
    coordinator = entry.runtime_data
    async_add_entities([OllamaOnlineSensor(coordinator), OllamaModelLoadedSensor(coordinator)])


class OllamaOnlineSensor(OllamaMonitorEntity, BinarySensorEntity):
    """Connectivity. Stays available when the host is down so it can say 'off'."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_translation_key = "online"

    def __init__(self, coordinator: OllamaMonitorCoordinator) -> None:
        """Set up."""
        super().__init__(coordinator, "online")

    @property
    def available(self) -> bool:
        """Available as long as the coordinator has run at all."""
        return self.coordinator.data is not None

    @property
    def is_on(self) -> bool:
        """True when Ollama answered the last poll."""
        return bool(self.coordinator.data and self.coordinator.data.online)

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Expose the URL so the card can show where it's pointed."""
        return {"url": self.coordinator.client.url}


class OllamaModelLoadedSensor(OllamaMonitorEntity, BinarySensorEntity):
    """On while at least one model sits in memory."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_translation_key = "model_loaded"

    def __init__(self, coordinator: OllamaMonitorCoordinator) -> None:
        """Set up."""
        super().__init__(coordinator, "model_loaded")

    @property
    def is_on(self) -> bool:
        """Anything loaded?"""
        return bool(self.coordinator.data.loaded)
