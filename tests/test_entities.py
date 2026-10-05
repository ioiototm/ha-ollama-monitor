"""Sensors, binary sensors and offline behaviour."""

from __future__ import annotations

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from .conftest import URL, make_entry, mock_host, ps_payload, setup_entry


async def test_states(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Everything reads sensibly with two models loaded."""
    mock_host(aioclient_mock)
    entry = await setup_entry(hass, make_entry())

    loaded = hass.states.get("sensor.desktop_loaded_model")
    assert loaded.state == "qwen3:14b"  # most recently used: expires last
    models = loaded.attributes["models"]
    assert [m["name"] for m in models] == ["qwen3:14b", "nomic-embed-text:latest"]
    assert models[0]["processor"] == "100% GPU"
    assert models[1]["processor"] == "50%/50% CPU/GPU"
    assert models[1]["gpu_percent"] == 50
    assert models[0]["pinned"] is False
    assert models[0]["context_length"] == 8192

    assert hass.states.get("sensor.desktop_loaded_models").state == "2"
    vram = hass.states.get("sensor.desktop_vram_used")
    assert vram.attributes["unit_of_measurement"] == "GB"
    assert float(vram.state) == 10.8
    assert float(hass.states.get("sensor.desktop_model_memory_used").state) == 11.1

    next_unload = dt_util.parse_datetime(hass.states.get("sensor.desktop_next_unload").state)
    assert timedelta(minutes=3) < next_unload - dt_util.utcnow() < timedelta(minutes=5)

    installed = hass.states.get("sensor.desktop_installed_models")
    assert installed.state == "3"
    assert [m["name"] for m in installed.attributes["models"]] == [
        "gemma4:31b",
        "nomic-embed-text:latest",
        "qwen3:14b",
    ]
    assert round(float(hass.states.get("sensor.desktop_model_storage").state), 1) == 28.6
    assert hass.states.get("sensor.desktop_ollama_version").state == "0.36.1"
    assert hass.states.get("binary_sensor.desktop_online").state == STATE_ON
    assert hass.states.get("binary_sensor.desktop_online").attributes["url"] == URL
    assert hass.states.get("binary_sensor.desktop_model_loaded").state == STATE_ON

    (device,) = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert device.name == "Desktop"
    assert device.sw_version == "0.36.1"
    ent_reg = er.async_get(hass)
    assert len(er.async_entries_for_config_entry(ent_reg, entry.entry_id)) == 11


async def test_pinned_and_idle(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer: FrozenDateTimeFactory
) -> None:
    """keep_alive=-1 shows as pinned; nothing loaded shows idle."""
    mock_host(aioclient_mock, ps=ps_payload(pinned=True))
    await setup_entry(hass, make_entry())
    models = hass.states.get("sensor.desktop_loaded_model").attributes["models"]
    assert models[0]["pinned"] is True
    assert models[0]["expires_at"] is None
    # a pinned model doesn't shadow the one that was actually used last
    assert hass.states.get("sensor.desktop_loaded_model").state == "nomic-embed-text:latest"
    # next_unload ignores the pinned one and reports the embedder
    assert hass.states.get("sensor.desktop_next_unload").state != STATE_UNKNOWN

    aioclient_mock.clear_requests()
    mock_host(aioclient_mock, ps=ps_payload(empty=True))
    freezer.tick(timedelta(seconds=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get("sensor.desktop_loaded_model").state == "idle"
    assert hass.states.get("sensor.desktop_loaded_models").state == "0"
    assert hass.states.get("sensor.desktop_vram_used").state == "0.0"
    assert hass.states.get("sensor.desktop_next_unload").state == STATE_UNKNOWN
    assert hass.states.get("binary_sensor.desktop_model_loaded").state == STATE_OFF


async def test_offline_then_back(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer: FrozenDateTimeFactory
) -> None:
    """Host down → online off, rest unavailable. Back → all good again."""
    mock_host(aioclient_mock)
    entry = await setup_entry(hass, make_entry())

    aioclient_mock.clear_requests()
    for path in ("version", "ps", "tags"):
        aioclient_mock.get(f"{URL}/api/{path}", exc=TimeoutError())
    freezer.tick(timedelta(seconds=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get("binary_sensor.desktop_online").state == STATE_OFF
    assert hass.states.get("sensor.desktop_loaded_model").state == STATE_UNAVAILABLE
    assert hass.states.get("button.desktop_unload_all_models").state == STATE_UNAVAILABLE

    aioclient_mock.clear_requests()
    mock_host(aioclient_mock, version="0.37.0")
    freezer.tick(timedelta(seconds=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get("binary_sensor.desktop_online").state == STATE_ON
    assert hass.states.get("sensor.desktop_loaded_model").state == "qwen3:14b"
    # came back after an upgrade: version is re-read immediately and the device follows
    assert hass.states.get("sensor.desktop_ollama_version").state == "0.37.0"
    (device,) = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert device.sw_version == "0.37.0"


async def test_offline_at_startup(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """A host that's off when HA boots still sets up, showing offline."""
    for path in ("version", "ps", "tags"):
        aioclient_mock.get(f"{URL}/api/{path}", exc=TimeoutError())
    await setup_entry(hass, make_entry())
    assert hass.states.get("binary_sensor.desktop_online").state == STATE_OFF
    assert hass.states.get("sensor.desktop_loaded_model").state == STATE_UNAVAILABLE


async def test_slow_endpoints_polled_less(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer: FrozenDateTimeFactory
) -> None:
    """/api/tags and /api/version aren't hammered every 10 s."""
    mock_host(aioclient_mock)
    await setup_entry(hass, make_entry())
    for _ in range(3):
        freezer.tick(timedelta(seconds=11))
        async_fire_time_changed(hass)
        await hass.async_block_till_done(wait_background_tasks=True)
    paths = [str(call[1].path) for call in aioclient_mock.mock_calls]
    assert paths.count("/api/ps") == 4
    assert paths.count("/api/tags") == 1
    assert paths.count("/api/version") == 1


async def test_unload_entry(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Unloading works."""
    mock_host(aioclient_mock)
    entry = await setup_entry(hass, make_entry())
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.desktop_loaded_model").state == STATE_UNAVAILABLE
