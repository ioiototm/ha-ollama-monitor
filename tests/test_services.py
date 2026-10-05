"""load_model / unload_model actions and the unload button."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.ollama_monitor.const import DOMAIN

from .conftest import URL, URL2, make_entry, mock_host, setup_entry


def posts(aioclient_mock: AiohttpClientMocker) -> list[tuple[str, dict]]:
    """(path, json body) for every POST made."""
    return [(str(c[1].path), c[2]) for c in aioclient_mock.mock_calls if c[0] == "POST"]


@pytest.mark.parametrize(
    ("keep_alive", "expected"),
    [("30m", "30m"), ("forever", -1), (-1, -1), ("3600", 3600), ("2 hours", 7200), (None, None)],
)
async def test_load_model(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, keep_alive, expected
) -> None:
    """keep_alive is normalised before it goes to /api/generate."""
    mock_host(aioclient_mock)
    aioclient_mock.post(f"{URL}/api/generate", json={"done": True, "done_reason": "load"})
    await setup_entry(hass, make_entry())
    data = {"model": "gemma4:31b"}
    if keep_alive is not None:
        data["keep_alive"] = keep_alive
    await hass.services.async_call(DOMAIN, "load_model", data, blocking=True)
    body = posts(aioclient_mock)[0][1]
    assert body["model"] == "gemma4:31b"
    assert body["stream"] is False
    if expected is None:
        assert "keep_alive" not in body
    else:
        assert body["keep_alive"] == expected


async def test_load_bad_keep_alive(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Garbage and zero are rejected before calling Ollama."""
    mock_host(aioclient_mock)
    await setup_entry(hass, make_entry())
    with pytest.raises(ServiceValidationError, match="keep_alive"):
        await hass.services.async_call(
            DOMAIN, "load_model", {"model": "x", "keep_alive": "a while"}, blocking=True
        )
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "load_model", {"model": "x", "keep_alive": "0"}, blocking=True)
    assert posts(aioclient_mock) == []


async def test_load_embedding_model_falls_back(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Embedding-only models get warmed via /api/embed."""
    mock_host(aioclient_mock)
    aioclient_mock.post(
        f"{URL}/api/generate", status=400, json={"error": '"nomic-embed-text" does not support generate'}
    )
    aioclient_mock.post(f"{URL}/api/embed", json={"embeddings": [[0.1]]})
    await setup_entry(hass, make_entry())
    await hass.services.async_call(
        DOMAIN, "load_model", {"model": "nomic-embed-text", "keep_alive": "1h"}, blocking=True
    )
    calls = posts(aioclient_mock)
    assert [c[0] for c in calls] == ["/api/generate", "/api/embed"]
    assert calls[1][1] == {"model": "nomic-embed-text", "input": ".", "keep_alive": "1h"}


async def test_load_error_surfaces(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Ollama's error text reaches the user."""
    mock_host(aioclient_mock)
    aioclient_mock.post(f"{URL}/api/generate", status=404, json={"error": "model 'nope' not found"})
    await setup_entry(hass, make_entry())
    with pytest.raises(ServiceValidationError, match="model 'nope' not found"):
        await hass.services.async_call(DOMAIN, "load_model", {"model": "nope"}, blocking=True)


async def test_server_error_surfaces(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """A 500 (e.g. out of memory) is a HomeAssistantError with Ollama's text."""
    mock_host(aioclient_mock)
    aioclient_mock.post(
        f"{URL}/api/generate", status=500, json={"error": "model requires more system memory"}
    )
    await setup_entry(hass, make_entry())
    with pytest.raises(HomeAssistantError, match="requires more system memory") as exc:
        await hass.services.async_call(DOMAIN, "load_model", {"model": "qwen3:235b"}, blocking=True)
    assert not isinstance(exc.value, ServiceValidationError)


async def test_unload_one_and_all(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Named model → one call. No model → every loaded model."""
    mock_host(aioclient_mock)
    aioclient_mock.post(f"{URL}/api/generate", json={"done": True, "done_reason": "unload"})
    await setup_entry(hass, make_entry())

    await hass.services.async_call(DOMAIN, "unload_model", {"model": "qwen3:14b"}, blocking=True)
    assert posts(aioclient_mock) == [
        ("/api/generate", {"model": "qwen3:14b", "keep_alive": 0, "stream": False})
    ]

    aioclient_mock.mock_calls.clear()
    await hass.services.async_call(DOMAIN, "unload_model", {}, blocking=True)
    assert [b["model"] for _, b in posts(aioclient_mock)] == ["qwen3:14b", "nomic-embed-text:latest"]


async def test_unload_button(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """The button unloads everything."""
    mock_host(aioclient_mock)
    aioclient_mock.post(f"{URL}/api/generate", json={"done": True})
    await setup_entry(hass, make_entry())
    await hass.services.async_call(
        "button", "press", {"entity_id": "button.desktop_unload_all_models"}, blocking=True
    )
    assert len(posts(aioclient_mock)) == 2


async def test_targeting_with_two_hosts(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """With two hosts you must say which; device_id and config_entry_id both work."""
    mock_host(aioclient_mock)
    mock_host(aioclient_mock, URL2)
    aioclient_mock.post(f"{URL}/api/generate", json={"done": True})
    aioclient_mock.post(f"{URL2}/api/generate", json={"done": True})
    desktop = await setup_entry(hass, make_entry())
    pi = await setup_entry(hass, make_entry(URL2, "Pi"))

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "load_model", {"model": "qwen3:14b"}, blocking=True)

    (pi_device,) = dr.async_entries_for_config_entry(dr.async_get(hass), pi.entry_id)
    await hass.services.async_call(
        DOMAIN, "load_model", {"model": "qwen3:0.6b", "device_id": pi_device.id}, blocking=True
    )
    hosts = [c[1].host for c in aioclient_mock.mock_calls if c[0] == "POST"]
    assert hosts == ["pi.lan"]

    aioclient_mock.mock_calls.clear()
    await hass.services.async_call(
        DOMAIN, "unload_model", {"config_entry_id": [desktop.entry_id, pi.entry_id]}, blocking=True
    )
    hosts = {c[1].host for c in aioclient_mock.mock_calls if c[0] == "POST"}
    assert hosts == {"desktop.lan", "pi.lan"}

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "unload_model", {"device_id": "not-a-device"}, blocking=True)


async def test_diagnostics_redacts_key(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """The API key never lands in a diagnostics dump."""
    from custom_components.ollama_monitor.diagnostics import async_get_config_entry_diagnostics

    mock_host(aioclient_mock)
    entry = await setup_entry(hass, make_entry(api_key="hunter2"))
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["entry"]["data"]["api_key"] == "**REDACTED**"
    assert diag["status"]["online"] is True
    assert len(diag["status"]["loaded"]) == 2
