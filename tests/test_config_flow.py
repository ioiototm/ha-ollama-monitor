"""Config, options, reconfigure and reauth flows."""

from __future__ import annotations

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_NAME, CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.ollama_monitor.const import CONF_API_KEY, CONF_SCAN_INTERVAL, DOMAIN

from .conftest import URL, make_entry, mock_host, setup_entry


async def test_user_flow_bare_host(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """A bare hostname gets http:// and :11434, and a name is derived."""
    mock_host(aioclient_mock)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_URL: "desktop.lan"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Desktop"
    assert result["data"][CONF_URL] == URL
    assert result["data"][CONF_API_KEY] is None
    assert result["result"].unique_id == URL


async def test_user_flow_custom_name_and_key(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Name and API key are kept; the key is sent as a Bearer token."""
    mock_host(aioclient_mock, "https://ollama.example.com")
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_URL: "https://ollama.example.com/", CONF_NAME: "Desktop", CONF_API_KEY: " s3cret "},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Desktop"
    assert result["data"][CONF_API_KEY] == "s3cret"
    assert aioclient_mock.mock_calls[0][3]["Authorization"] == "Bearer s3cret"


async def test_user_flow_errors(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Unreachable, 401, and not-Ollama all map to friendly errors."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})

    aioclient_mock.get(f"{URL}/api/version", exc=TimeoutError())
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_URL: URL})
    assert result["errors"] == {"base": "cannot_connect"}

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{URL}/api/version", status=401, text="nope")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_URL: URL})
    assert result["errors"] == {"base": "invalid_auth"}

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{URL}/api/version", text="<html>nginx welcome</html>")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_URL: URL})
    assert result["errors"] == {"base": "not_ollama"}

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_URL: "ftp://what"})
    assert result["errors"] == {CONF_URL: "invalid_url"}

    aioclient_mock.clear_requests()
    mock_host(aioclient_mock)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_URL: URL})
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_duplicate_aborts(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Same URL twice is refused."""
    mock_host(aioclient_mock)
    await setup_entry(hass, make_entry())
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: "http://DESKTOP.lan:11434/"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_options_flow(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Scan interval is stored and applied after reload."""
    mock_host(aioclient_mock)
    entry = await setup_entry(hass, make_entry())
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_SCAN_INTERVAL: 30})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_SCAN_INTERVAL: 30}
    assert entry.runtime_data.update_interval.total_seconds() == 30


async def test_reconfigure(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Moving a host to a new URL keeps the entry."""
    mock_host(aioclient_mock)
    mock_host(aioclient_mock, "https://ollama.desktop.example.com")
    entry = await setup_entry(hass, make_entry())
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: "https://ollama.desktop.example.com"}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_URL] == "https://ollama.desktop.example.com"
    assert entry.unique_id == "https://ollama.desktop.example.com"


async def test_reconfigure_onto_other_host(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Can't reconfigure one host onto another configured host's URL."""
    mock_host(aioclient_mock)
    mock_host(aioclient_mock, "http://pi.lan:11434")
    entry = await setup_entry(hass, make_entry())
    await setup_entry(hass, make_entry("http://pi.lan:11434", "Pi"))
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: "http://pi.lan:11434"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_URL] == URL


async def test_reauth(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """401 while polling triggers reauth; a new key fixes it."""
    aioclient_mock.get(f"{URL}/api/version", status=401)
    aioclient_mock.get(f"{URL}/api/ps", status=401)
    aioclient_mock.get(f"{URL}/api/tags", status=401)
    entry = make_entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress()
    assert flows and flows[0]["context"]["source"] == "reauth"

    aioclient_mock.clear_requests()
    mock_host(aioclient_mock)
    result = await hass.config_entries.flow.async_configure(flows[0]["flow_id"], {CONF_API_KEY: "newkey"})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_API_KEY] == "newkey"
