"""Polling coordinator: one per Ollama host."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api import (
    InstalledModel,
    OllamaAuthError,
    OllamaClient,
    OllamaError,
    OllamaStatus,
)
from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DOMAIN, SLOW_REFRESH_SECONDS

_LOGGER = logging.getLogger(__name__)

type OllamaMonitorConfigEntry = ConfigEntry[OllamaMonitorCoordinator]


class OllamaMonitorCoordinator(DataUpdateCoordinator[OllamaStatus]):
    """Polls /api/ps often, /api/tags and /api/version less often.

    An unreachable host is *data*, not a failure: we return
    ``OllamaStatus(online=False)`` so the connectivity sensor can say "off"
    while the rest of the entities go unavailable.
    """

    config_entry: OllamaMonitorConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: OllamaMonitorConfigEntry,
        client: OllamaClient,
    ) -> None:
        """Set up the coordinator."""
        interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.title}",
            update_interval=timedelta(seconds=interval),
        )
        self.client = client
        self._version: str | None = None
        self._installed: list[InstalledModel] = []
        self._slow_fetched_at: float = 0.0
        self._was_online: bool | None = None

    def force_slow_refresh(self) -> None:
        """Make the next poll re-fetch installed models and version."""
        self._slow_fetched_at = 0.0

    async def _async_update_data(self) -> OllamaStatus:
        slow_due = time.monotonic() - self._slow_fetched_at > SLOW_REFRESH_SECONDS
        try:
            if slow_due:
                version, loaded, installed = await asyncio.gather(
                    self.client.version(), self.client.ps(), self.client.tags()
                )
                self._version, self._installed = version, installed
                self._slow_fetched_at = time.monotonic()
            else:
                loaded = await self.client.ps()
        except OllamaAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except OllamaError as err:
            if self._was_online is not False:
                _LOGGER.warning("Ollama at %s is unreachable: %s", self.client.url, err)
            self._was_online = False
            # Re-check version/tags straight away once it's back.
            self._slow_fetched_at = 0.0
            return OllamaStatus(online=False, version=self._version)

        if self._was_online is False:
            _LOGGER.info("Ollama at %s is back online", self.client.url)
        self._was_online = True
        return OllamaStatus(
            online=True,
            version=self._version,
            loaded=loaded,
            installed=self._installed,
        )
