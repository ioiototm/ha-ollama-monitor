"""Showing a host's entities under an existing device (e.g. the PC's own device)."""

from __future__ import annotations

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.ollama_monitor.const import CONF_LINKED_DEVICE, CONF_SCAN_INTERVAL, DOMAIN

from .conftest import URL, make_entry, mock_host


def _pc_device(hass: HomeAssistant) -> dr.DeviceEntry:
    """A device some other integration made for the desktop."""
    other = MockConfigEntry(domain="system_monitor_ish", title="PC")
    other.add_to_hass(hass)
    return dr.async_get(hass).async_get_or_create(
        config_entry_id=other.entry_id,
        identifiers={("system_monitor_ish", "desktop")},
        name="Desktop PC",
        manufacturer="Me",
        sw_version="Pop!_OS 24.04",
    )


def _own_devices(hass: HomeAssistant, entry_id: str) -> list[dr.DeviceEntry]:
    dev_reg = dr.async_get(hass)
    return [
        d for d in dr.async_entries_for_config_entry(dev_reg, entry_id) if (DOMAIN, entry_id) in d.identifiers
    ]


def _device_ids(hass: HomeAssistant, entry_id: str) -> set[str | None]:
    ent_reg = er.async_get(hass)
    return {e.device_id for e in er.async_entries_for_config_entry(ent_reg, entry_id)}


async def test_link_from_config_flow(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Picking a device in setup puts every entity on it and creates no Ollama device."""
    mock_host(aioclient_mock)
    aioclient_mock.post(f"{URL}/api/generate", json={"done": True})
    pc = _pc_device(hass)

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: URL, CONF_LINKED_DEVICE: pc.id}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    assert entry.options == {CONF_LINKED_DEVICE: pc.id}

    assert _device_ids(hass, entry.entry_id) == {pc.id}
    dev_reg = dr.async_get(hass)
    assert not _own_devices(hass, entry.entry_id)
    # the PC's own details are left alone
    assert dev_reg.async_get(pc.id).sw_version == "Pop!_OS 24.04"
    assert hass.states.get("sensor.desktop_pc_loaded_model").state == "qwen3:14b"

    # actions accept the PC's device id
    await hass.services.async_call(
        DOMAIN, "unload_model", {"device_id": pc.id, "model": "qwen3:14b"}, blocking=True
    )
    assert any(c[0] == "POST" for c in aioclient_mock.mock_calls)


async def test_link_and_unlink_via_options(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Linking later moves entities over; clearing it brings the Ollama device back."""
    mock_host(aioclient_mock)
    pc = _pc_device(hass)
    entry = make_entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    dev_reg = dr.async_get(hass)
    (own,) = _own_devices(hass, entry.entry_id)
    assert _device_ids(hass, entry.entry_id) == {own.id}

    result = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 10, CONF_LINKED_DEVICE: pc.id}
    )
    await hass.async_block_till_done()
    assert _device_ids(hass, entry.entry_id) == {pc.id}
    assert dev_reg.async_get(own.id) is None

    result = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(result["flow_id"], {CONF_SCAN_INTERVAL: 10})
    await hass.async_block_till_done()
    assert entry.options == {CONF_SCAN_INTERVAL: 10}
    (own_again,) = _own_devices(hass, entry.entry_id)
    assert _device_ids(hass, entry.entry_id) == {own_again.id}
    assert dev_reg.async_get(pc.id) is not None


async def test_linked_device_deleted(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """If the linked device disappears, the host falls back to its own device."""
    mock_host(aioclient_mock)
    entry = make_entry()
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, options={CONF_LINKED_DEVICE: "gone"})
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.desktop_loaded_model").state == "qwen3:14b"
