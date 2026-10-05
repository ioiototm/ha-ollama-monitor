"""Fixtures: a fake Ollama host served through HA's aiohttp mock."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.const import CONF_URL, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.ollama_monitor.const import CONF_API_KEY, DOMAIN

URL = "http://desktop.lan:11434"
URL2 = "http://pi.lan:11434"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Let HA load custom_components/ from this repo."""


def ps_payload(*, pinned: bool = False, empty: bool = False) -> dict[str, Any]:
    """Two models loaded on a 4090-ish box."""
    if empty:
        return {"models": []}
    now = dt_util.utcnow()
    soon = (now + timedelta(minutes=4)).isoformat()
    later = (now + timedelta(minutes=25)).isoformat().replace("+00:00", "Z")
    forever = "2318-03-12T12:00:00.123456789Z"
    return {
        "models": [
            {
                "name": "qwen3:14b",
                "model": "qwen3:14b",
                "size": 10_500_000_000,
                "size_vram": 10_500_000_000,
                "digest": "abc",
                "details": {"family": "qwen3", "parameter_size": "14.8B", "quantization_level": "Q4_K_M"},
                "expires_at": forever if pinned else later,
                "context_length": 8192,
            },
            {
                "name": "nomic-embed-text:latest",
                "model": "nomic-embed-text:latest",
                "size": 600_000_000,
                "size_vram": 300_000_000,
                "digest": "def",
                "details": {"family": "nomic-bert", "parameter_size": "137M", "quantization_level": "F16"},
                "expires_at": soon,
                "context_length": 2048,
            },
        ]
    }


TAGS = {
    "models": [
        {
            "name": "qwen3:14b",
            "model": "qwen3:14b",
            "modified_at": "2026-09-01T10:00:00Z",
            "size": 9_300_000_000,
            "digest": "abc",
            "details": {"family": "qwen3", "parameter_size": "14.8B", "quantization_level": "Q4_K_M"},
        },
        {
            "name": "nomic-embed-text:latest",
            "model": "nomic-embed-text:latest",
            "modified_at": "2026-08-01T10:00:00Z",
            "size": 274_000_000,
            "digest": "def",
            "details": {"family": "nomic-bert", "parameter_size": "137M", "quantization_level": "F16"},
        },
        {
            "name": "gemma4:31b",
            "model": "gemma4:31b",
            "modified_at": "2026-09-20T10:00:00Z",
            "size": 19_000_000_000,
            "digest": "ghi",
            "details": {"family": "gemma4", "parameter_size": "31B", "quantization_level": "Q4_K_M"},
        },
    ]
}


def mock_host(
    aioclient_mock: AiohttpClientMocker,
    url: str = URL,
    *,
    ps: dict[str, Any] | None = None,
    version: str = "0.36.1",
) -> None:
    """Register the three read endpoints."""
    aioclient_mock.get(f"{url}/api/version", json={"version": version})
    aioclient_mock.get(f"{url}/api/ps", json=ps if ps is not None else ps_payload())
    aioclient_mock.get(f"{url}/api/tags", json=TAGS)


def make_entry(url: str = URL, title: str = "Desktop", **extra: Any) -> MockConfigEntry:
    """A config entry for a host."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=title,
        unique_id=url.lower(),
        data={CONF_URL: url, CONF_API_KEY: None, CONF_VERIFY_SSL: True, **extra},
    )


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> MockConfigEntry:
    """Add and set up an entry."""
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry
