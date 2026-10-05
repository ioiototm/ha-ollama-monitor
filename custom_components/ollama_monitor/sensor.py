"""Sensors: what's loaded, how much memory it eats, what's installed."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfInformation
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import OllamaStatus
from .coordinator import OllamaMonitorConfigEntry
from .entity import OllamaMonitorEntity

PARALLEL_UPDATES = 0

IDLE = "idle"


@dataclass(frozen=True, kw_only=True)
class OllamaSensorDescription(SensorEntityDescription):
    """Describes an Ollama Monitor sensor."""

    value_fn: Callable[[OllamaStatus], str | int | datetime | None]
    attrs_fn: Callable[[OllamaStatus], dict[str, Any]] | None = None


def _loaded_attrs(status: OllamaStatus) -> dict[str, Any]:
    return {
        "models": [m.as_attr() for m in status.loaded],
        "model_count": len(status.loaded),
    }


def _installed_attrs(status: OllamaStatus) -> dict[str, Any]:
    return {"models": [m.as_attr() for m in status.installed]}


SENSORS: tuple[OllamaSensorDescription, ...] = (
    OllamaSensorDescription(
        key="loaded_model",
        translation_key="loaded_model",
        value_fn=lambda s: s.primary_model.name if s.primary_model else IDLE,
        attrs_fn=_loaded_attrs,
    ),
    OllamaSensorDescription(
        key="loaded_models",
        translation_key="loaded_models",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: len(s.loaded),
    ),
    OllamaSensorDescription(
        key="vram_used",
        translation_key="vram_used",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.GIGABYTES,
        suggested_display_precision=1,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: s.vram_used,
    ),
    OllamaSensorDescription(
        key="memory_used",
        translation_key="memory_used",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.GIGABYTES,
        suggested_display_precision=1,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: s.memory_used,
    ),
    OllamaSensorDescription(
        key="next_unload",
        translation_key="next_unload",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda s: s.next_unload,
    ),
    OllamaSensorDescription(
        key="installed_models",
        translation_key="installed_models",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: len(s.installed),
        attrs_fn=_installed_attrs,
    ),
    OllamaSensorDescription(
        key="disk_used",
        translation_key="disk_used",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.GIGABYTES,
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.disk_used,
    ),
    OllamaSensorDescription(
        key="version",
        translation_key="version",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.version,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: OllamaMonitorConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add sensors for this host."""
    coordinator = entry.runtime_data
    async_add_entities(OllamaSensor(coordinator, desc) for desc in SENSORS)


class OllamaSensor(OllamaMonitorEntity, SensorEntity):
    """A single Ollama Monitor sensor."""

    entity_description: OllamaSensorDescription
    # The model lists churn on every request and can get long; keep them out of
    # the recorder database. The live state still has them for the card.
    _unrecorded_attributes = frozenset({"models", "model_count"})

    def __init__(self, coordinator, description: OllamaSensorDescription) -> None:
        """Bind the description."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> str | int | datetime | None:
        """Current value."""
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Model lists for the card and templates."""
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data)
