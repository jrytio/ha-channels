"""Tests for diagnostics."""

from homeassistant.config_entries import ConfigEntryState

from custom_components.channels.diagnostics import async_get_config_entry_diagnostics
from custom_components.channels.lib import ChannelsConnectionError

from .conftest import make_app_client, setup_integration

REDACTED = "**REDACTED**"


async def test_app_diagnostics_include_the_last_status(hass, office_entry):
    await setup_integration(hass)
    status = office_entry.runtime_data.data

    info = await async_get_config_entry_diagnostics(hass, office_entry)

    assert info["data"]["kind"] == "app"
    assert info["data"]["host"] == REDACTED
    assert info["status"]["recording_id"] == "15017"
    assert info["status"]["position"] == status.position
    assert info["status"]["position"] is not None
    for key in ("title", "episode_title", "summary", "image_url"):
        assert info["status"][key] == REDACTED
    assert "10.0.0." not in str(info)


async def test_app_diagnostics_when_the_app_is_unreachable(
    hass, office_entry, app_clients
):
    client = make_app_client()
    client.status.side_effect = ChannelsConnectionError("down")
    app_clients["10.0.0.2"] = client
    await setup_integration(hass)

    info = await async_get_config_entry_diagnostics(hass, office_entry)

    assert info["status"] is None
    assert "10.0.0." not in str(info)


async def test_dvr_diagnostics_return_only_the_allowed_keys(
    hass, dvr_entry, dvr_client
):
    dvr_client.status.return_value = {
        "version": "2026.08.07.0346",
        "os": "linux",
        "arch": "amd64",
        "name": "nas6",
        "username": "me",
        "email": "me@example.com",
    }
    await setup_integration(hass)

    info = await async_get_config_entry_diagnostics(hass, dvr_entry)

    assert info["data"]["host"] == REDACTED
    assert info["status"] == {
        "version": "2026.08.07.0346",
        "os": "linux",
        "arch": "amd64",
        "name": "nas6",
    }
    assert "10.0.0." not in str(info)


async def test_dvr_diagnostics_when_the_server_is_unreachable(
    hass, dvr_entry, dvr_client
):
    await setup_integration(hass)
    dvr_client.status.side_effect = ChannelsConnectionError(
        "Channels DVR at 10.0.0.9 did not answer: boom"
    )

    info = await async_get_config_entry_diagnostics(hass, dvr_entry)

    assert info["status"] == "unreachable"
    assert "10.0.0.9" not in str(info)
    assert "boom" not in str(info)


async def test_diagnostics_for_a_dvr_server_in_setup_retry(hass, dvr_entry, dvr_client):
    dvr_client.status.side_effect = ChannelsConnectionError("down")
    await setup_integration(hass)
    assert dvr_entry.state is ConfigEntryState.SETUP_RETRY

    info = await async_get_config_entry_diagnostics(hass, dvr_entry)

    assert info["status"] == "not loaded"
    assert info["data"]["host"] == REDACTED


async def test_diagnostics_for_an_app_entry_that_is_not_loaded(hass, office_entry):
    await setup_integration(hass)
    assert await hass.config_entries.async_unload(office_entry.entry_id)

    info = await async_get_config_entry_diagnostics(hass, office_entry)

    assert info["status"] == "not loaded"
