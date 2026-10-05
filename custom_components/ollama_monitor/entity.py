"""Shared entity bits."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import OllamaMonitorCoordinator


class OllamaMonitorEntity(CoordinatorEntity[OllamaMonitorCoordinator]):
    """One device per Ollama host; entities hang off it."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: OllamaMonitorCoordinator, key: str) -> None:
        """Set up ids and device info."""
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Ollama",
            model="Ollama server",
            sw_version=coordinator.data.version if coordinator.data else None,
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=coordinator.client.url,
        )

    @property
    def available(self) -> bool:
        """Unavailable while the host is unreachable."""
        return super().available and self.coordinator.data is not None and self.coordinator.data.online
