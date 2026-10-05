"""Shared entity bits."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_LINKED_DEVICE, DOMAIN
from .coordinator import OllamaMonitorConfigEntry, OllamaMonitorCoordinator

# Home Assistant 2026.x made a device belong to exactly one config entry. Since then,
# entities join someone else's device by setting `device_entry`; before, they did it
# by reusing that device's identifiers in `device_info`. This method arrived with the
# change, so it tells us which way this HA instance wants it.
_LINK_VIA_DEVICE_ENTRY = hasattr(dr.DeviceRegistry, "async_get_device_by_identifier")


def linked_device(hass: HomeAssistant, entry: OllamaMonitorConfigEntry) -> dr.DeviceEntry | None:
    """The existing device this host's entities should appear under, if any."""
    device_id = entry.options.get(CONF_LINKED_DEVICE)
    if not device_id:
        return None
    return dr.async_get(hass).async_get(device_id)


def own_device_info(coordinator: OllamaMonitorCoordinator) -> DeviceInfo:
    """The integration's own "Ollama server" device."""
    entry = coordinator.config_entry
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer="Ollama",
        model="Ollama server",
        sw_version=coordinator.data.version if coordinator.data else None,
        entry_type=DeviceEntryType.SERVICE,
        configuration_url=coordinator.client.url,
    )


class OllamaMonitorEntity(CoordinatorEntity[OllamaMonitorCoordinator]):
    """Entities hang off the host's own device, or a device the user linked."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: OllamaMonitorCoordinator, key: str) -> None:
        """Set up ids and device info."""
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        device = linked_device(coordinator.hass, entry)
        if device is None:
            self._attr_device_info = own_device_info(coordinator)
        elif _LINK_VIA_DEVICE_ENTRY:
            self.device_entry = device
        else:
            self._attr_device_info = DeviceInfo(
                identifiers=device.identifiers, connections=device.connections
            )

    @property
    def available(self) -> bool:
        """Unavailable while the host is unreachable."""
        return super().available and self.coordinator.data is not None and self.coordinator.data.online
